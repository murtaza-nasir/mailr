"""Speakers ↔ people (speaker.map).

A Speakr speaker (a name, no email) is mapped to a mailr person by email address in
`speakr_speaker_people`, which mailr keeps itself (People groups and CardDAV contacts have no field
for it). People is the one table of persons; an address is the key of a person, and a speaker maps
to one address.

Keys. Speakr's speaker names are given by the user and stay the same across recordings, so a named
speaker is one speaker on the whole connector: "name:<lower-case name>". A voice profile with an id
and no name is "id:<speaker id>". An unnamed label such as SPEAKER_02 means nothing outside its
own recording: "label:<recording>:<label>". A confirmed mapping on a name or id key therefore
applies to every recording with that speaker. One recording can differ: a row keyed
"rec:<recording>:<base key>" is a per-recording override, confirmed (someone else in this
recording) or dismissed ("Not them in this recording"). Override rows keep `speaker_name` empty,
so the connector-wide readers of speaker names (matching.py, the Recordings list) count only the
global mappings.

Me. A speaker whose name is the user's own (the profile name, a sending identity's name, or the
name on one of the user's own addresses in People) is the user, shown as "You" with nothing to
confirm. A first name alone counts when it is the user's first name and nobody from the linked
meeting or the mail around it shares it. The user's own addresses are never offered as candidates.

Candidates, best first, never applied on their own: a name match is needed (the full name, the
first name, a nickname such as Mike for Michael, another word of the name, or for longer names one
letter off), and the score adds: a guest of the linked calendar event; a participant of a mail
thread from three days before to three days after the meeting whose subject shares words with the
recording's title; a member of one of the user's groups; and the correspondence strength
(two-way first). Rows of People with the same full name and at most one organization
are one person with several addresses. Unnamed labels are offered the linked meeting's guests that
no other speaker has. The top three are shown; the user confirms one or picks someone else. A
dismissed person is not offered again for that speaker.

Speaker ids and addresses in Speakr. A newer Speakr names the saved speaker behind each
diarisation label (`speaker_id`, no longer null) and keeps an address per speaker (`email`). A
confirmed mapping of a named speaker with an id is also stored under "id:<speaker id>", so it
survives a rename in Speakr: when the name key finds nothing and the id key does, the person
follows the speaker to the new name. An address Speakr holds for the speaker is offered first
("address saved in Speakr"). With write-back on, a confirmed person is written to the speaker in
Speakr (PUT /speakers/{id} {"email"}); otherwise nothing is written to Speakr.

`person_recordings` answers "recordings with this person" for get_person and the person card:
recordings whose participants name the person (their globally mapped speaker names or their own
full name), plus recordings where a per-recording mapping names them, minus recordings where they
were marked "Not them in this recording".
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core.people import TWO_WAY, org_from_email, rank_expr
from mailr.integrations.speakr import store
from mailr.models import (
    Connector,
    Identity,
    Message,
    Person,
    PersonGroup,
    PersonGroupMember,
    SpeakrRecording,
    SpeakrSpeakerPerson,
    User,
)

LABEL_RX = re.compile(r"^speaker[ _-]?\d+$")
SHOWN = 3
THREAD_DAYS = 3

# Common nicknames and spellings; each line is one name. A speaker called by one of them matches the others.
_NICK_SETS = [
    "michael mike mick mikey", "robert rob bob bobby robbie", "william will bill billy liam", "richard rick dick rich",
    "james jim jimmy jamie", "joseph joe joey", "thomas tom tommy", "christopher chris kit", "daniel dan danny",
    "matthew matt", "anthony tony", "andrew andy drew", "edward ed eddie ted", "jonathan jon jonny",
    "john jack johnny", "nicholas nick nicky", "benjamin ben benny", "samuel sam sammy", "alexander alex al sasha",
    "steven stephen steve", "timothy tim timmy", "gregory greg", "patrick pat paddy", "charles charlie chuck",
    "david dave davey", "peter pete", "kenneth ken kenny", "ronald ron ronnie", "donald don donnie",
    "lawrence larry", "raymond ray", "gerald gerry jerry", "jeffrey jeff", "douglas doug", "frederick fred freddie",
    "elizabeth liz beth betty lizzie eliza", "katherine catherine kate katie kathy cathy kat",
    "margaret maggie meg peggy", "jennifer jen jenny", "jessica jess jessie", "rebecca becky becca", "susan sue suzy",
    "deborah debbie deb", "patricia pat patty trish", "victoria vicky tori", "christina christine chris tina",
    "kimberly kim", "abigail abby", "alexandra alex sandra", "samantha sam", "barbara barb babs", "dorothy dot dottie",
    "pamela pam", "cynthia cindy", "theresa teresa terry tess", "allison alison ali", "melissa missy mel",
    "muhammad mohammad mohammed mohamed muhammed mohd", "ahmad ahmed", "mustafa mustapha", "yusuf yousef youssef",
    "ibrahim ebrahim", "hussain hussein husain", "hassan hasan", "mehmet mehmed",
]
NICKNAMES: dict[str, set[str]] = {}
for _line in _NICK_SETS:
    _names = set(_line.split())
    for _n in _names:
        NICKNAMES.setdefault(_n, set()).update(_names - {_n})

_STOP = {"about", "after", "again", "also", "from", "have", "into", "meeting", "call", "chat", "with", "this", "that",
         "their", "there", "these", "those", "were", "what", "when", "will", "your", "the", "and", "for", "notes",
         "discussion", "sync", "update", "weekly", "recording", "session", "zoom", "teams"}

_WHY = {"full": "same name", "first": "same first name", "nickname": "a nickname of this name",
        "word": "a word of the name", "close": "a similar name"}
_THREAD_HINT = {"from": "emailed you about this", "to": "you emailed them about this", "cc": "on an email about this"}


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _person_name(name: str) -> str:
    """A People name in "First Last" order ("Last, First" is turned around; quotes and
    parenthesised parts are dropped)."""
    n = re.sub(r"\(.*?\)|[\"']", " ", name or "")
    if n.count(",") == 1:
        last, first = n.split(",")
        n = f"{first} {last}"
    return _norm(n)


def _is_label(name: str) -> bool:
    return bool(LABEL_RX.match(_norm(name)))


def speaker_key(sp: dict[str, Any]) -> str:
    """The key: "name:<name>" for a named speaker (the same on every recording of the connector),
    "id:<speaker id>" for a voice profile without a name, and "label:<recording>:<label>" for an
    unnamed label such as SPEAKER_00, which means nothing outside its own recording."""
    name = _norm(sp.get("name") or "")
    if name and not _is_label(name):
        return f"name:{name}"[:200]
    sid = sp.get("speaker_id")
    if sid not in (None, ""):
        return f"id:{sid}"
    label = _norm(sp.get("label") or sp.get("name") or "")
    if sp.get("recording_id") is not None:
        return f"label:{int(sp['recording_id'])}:{label}"[:200]
    return f"name:{label}"[:200]


def override_key(rid: int, base: str) -> str:
    """The per-recording override of a connector-wide key."""
    return f"rec:{int(rid)}:{base}"[:200]


def _first(name: str) -> str:
    return _norm(name).split(" ", 1)[0] if name else ""


def _lev1(a: str, b: str) -> bool:
    """True when a and b differ by at most one edit."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:] if len(a) < len(b) else a[i + 1:] == b[i + 1:]


def name_match(speaker: str, person: str, email: str = "") -> tuple[str, int]:
    """How a speaker's name matches a person's name (or, with no name, the address's local part):
    (kind, points), kind one of full, first, nickname, word, close, or ("", 0)."""
    s = _norm(speaker)
    p = _person_name(person)
    if not s:
        return "", 0
    if not p and email:
        p = " ".join(t for t in re.split(r"[._\-+0-9]+", email.split("@", 1)[0].lower()) if t)
    if not p:
        return "", 0
    st, pt = s.split(), p.split()
    if s == p:
        return "full", 100
    if len(st) > 1:
        # "Mary Smith" for "Mary Ann Smith": every word, in order.
        it = iter(pt)
        if st[0] == pt[0] and all(w in it for w in st):
            return "full", 90
        if st[0] == pt[0]:
            return "first", 40
        return "", 0
    w = st[0]
    if pt[0] == w:
        return "first", 60
    if pt[0] in NICKNAMES.get(w, ()):
        return "nickname", 45
    if w in pt[1:]:
        return "word", 35
    if len(w) >= 4 and len(pt[0]) >= 4 and _lev1(w, pt[0]):
        return "close", 25
    return "", 0


def title_words(title: str) -> list[str]:
    return sorted({w for w in re.findall(r"[a-z][a-z0-9]+", (title or "").lower()) if len(w) >= 4 and w not in _STOP})


@dataclass
class Cand:
    email: str
    name: str = ""
    organization: str = ""
    rank: float = 0.0
    event: bool = False
    thread: str = ""  # from (emailed the user) | to (the user emailed them) | cc (on the thread)
    groups: list[str] = field(default_factory=list)

    def merge(self, o: Cand) -> None:
        self.name = self.name or o.name
        self.organization = self.organization or o.organization
        self.rank = max(self.rank, o.rank)
        self.event = self.event or o.event
        self.thread = self.thread or o.thread
        self.groups = list(dict.fromkeys(self.groups + o.groups))


def _hints(c: Cand) -> list[str]:
    out = []
    if c.event:
        out.append("in the linked meeting")
    if c.thread:
        out.append(_THREAD_HINT[c.thread])
    if c.groups:
        out.append(f"in your group {c.groups[0]}")
    return out


def rank_candidates(speaker: str, pool: list[Cand], *, exclude: set[str] | frozenset[str] = frozenset(),
                    unnamed: bool = False) -> list[dict[str, Any]]:
    """Candidates for one speaker, best first, each {email, name, organization, emails, hints, why,
    match, in_meeting, in_mail, score}. Named speakers need a name match; an unnamed label is
    offered the linked meeting's guests. People with the same full name and at most one
    organization become one candidate with several addresses."""
    top_rank = max((c.rank for c in pool), default=0.0)
    scored: list[tuple[float, Cand, str]] = []
    for c in pool:
        if c.email in exclude:
            continue
        if unnamed:
            if not c.event:
                continue
            kind, pts = "", 50
        else:
            kind, pts = name_match(speaker, c.name, c.email)
            if not pts:
                continue
        score = pts + (80 if c.event else 0) + (50 if c.thread else 0) + (15 if c.groups else 0)
        if c.rank > 0 and top_rank > 0:
            score += 20 * math.log1p(c.rank) / math.log1p(top_rank)
        scored.append((score, c, kind))
    scored.sort(key=lambda x: (-x[0], x[1].email))
    out: list[dict[str, Any]] = []
    by_name: dict[str, dict[str, Any]] = {}
    for score, c, kind in scored:
        full = _person_name(c.name)
        org = c.organization or org_from_email(c.email)
        twin = by_name.get(full) if len(full.split()) >= 2 else None
        if twin is not None and (not org or not twin["organization"] or org.lower() == twin["organization"].lower()):
            twin["emails"].append(c.email)
            twin["hints"] += [h for h in _hints(c) if h not in twin["hints"]]
            twin["in_meeting"] = twin["in_meeting"] or c.event
            twin["in_mail"] = twin["in_mail"] or bool(c.thread)
            twin["organization"] = twin["organization"] or org
            continue
        hints = _hints(c)
        item = {"email": c.email, "name": c.name, "organization": org, "emails": [c.email], "hints": hints,
                "why": ", ".join(hints) or _WHY.get(kind, ""), "match": kind, "in_meeting": c.event,
                "in_mail": bool(c.thread), "score": round(score, 1)}
        out.append(item)
        if len(full.split()) >= 2:
            by_name.setdefault(full, item)
    return out


def suggest(name: str, guests: list[dict[str, str]], contacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Suggested people for a speaker called `name` from a guest list and correspondents (with an
    optional `rank`), best first: the pure form of the candidates of `speakers_with_people`."""
    if not _norm(name) or _is_label(name):
        return []
    pool: dict[str, Cand] = {}
    for g in guests:
        pool.setdefault(g["email"], Cand(g["email"], g.get("name") or "")).event = True
    for c in contacts:
        pool.setdefault(c["email"], Cand(c["email"], c.get("name") or "", c.get("organization") or "",
                                         rank=float(c.get("rank") or 0)))
    return rank_candidates(name, list(pool.values()))[:SHOWN]


# ---------------------------------------------------------------- the database side


async def me_of(db: AsyncSession, user_id: int) -> dict[str, Any]:
    """The user's own addresses (identities and the account address, as calendar/mirror.py) and
    names: the profile name, the identities' names and the names People holds for those addresses."""
    from mailr.calendar import mirror as cal_mirror

    mine = await cal_mirror.my_addresses(db, user_id)
    user = await db.get(User, user_id)
    names = {_person_name(user.name)} if user and user.name else set()
    default = ""
    for email, nm, is_default in (await db.execute(select(Identity.email, Identity.name, Identity.is_default)
                                                   .where(Identity.user_id == user_id))).all():
        if nm:
            names.add(_person_name(nm))
        if is_default:
            default = email.lower()
    if mine:
        for (nm,) in (await db.execute(select(Person.name).where(
                Person.user_id == user_id, func.lower(Person.email).in_(list(mine)), Person.name != ""))).all():
            n = _person_name(nm)
            if len(n.split()) >= 2:
                names.add(n)
    names.discard("")
    full = {n for n in names if len(n.split()) >= 2}
    return {"addresses": mine, "names": names, "first": {_first(n) for n in full},
            "email": default or (user.email.lower() if user else ""), "name": (user.name if user else "") or ""}


async def _guests(db: AsyncSession, p: Any, conn: Connector, rid: int, mine: set[str]) -> list[dict[str, str]]:
    from mailr.integrations.speakr.followup import linked_event, recipients

    try:
        ev = await linked_event(db, p, conn, rid)
    except Exception:  # noqa: BLE001  no calendar
        return []
    return recipients(ev, mine)


async def _thread_people(db: AsyncSession, p: Any, row: SpeakrRecording) -> dict[str, tuple[str, str]]:
    """Participants of mail around the meeting whose subject shares words with the title (two words
    when the title has three or more): {email: (name, from | to | cc)}."""
    from mailr.core.visibility import visible_message_filter

    when = row.meeting_date or row.created_at
    words = title_words(row.title or "")
    if when is None or not words:
        return {}
    need = 2 if len(words) >= 3 else 1
    span = timedelta(days=THREAD_DAYS)
    rows = (await db.execute(select(Message.subject, Message.from_email, Message.from_name, Message.to, Message.cc,
                                     Message.sent_by_me).where(
        Message.user_id == p.user_id, Message.deleted.is_(False), Message.list_id.is_(None),
        Message.date >= when - span, Message.date <= when + span, visible_message_filter(p),
        or_(*[Message.subject.ilike(f"%{w}%") for w in words])).order_by(Message.date.desc()).limit(300))).all()
    out: dict[str, tuple[str, str]] = {}
    for subject, frm, frm_name, to, cc, by_me in rows:
        subj = (subject or "").lower()
        if sum(1 for w in words if w in subj) < need:
            continue
        if not by_me and frm:
            out[frm.lower()] = (frm_name or "", "from")
        for a in list(to or []) + list(cc or []):
            if isinstance(a, dict) and "@" in str(a.get("email") or ""):
                out.setdefault(str(a["email"]).lower(), (str(a.get("name") or ""), "to" if by_me else "cc"))
    return out


def _rank_of(r: Person) -> float:
    """rank_expr in Python: strength, raised for two-way correspondents."""
    two_way = TWO_WAY if (r.sent_count or 0) > 0 and (r.received_count or 0) > 0 else 1.0
    return float(r.strength or 0.0) * two_way


async def _pool(db: AsyncSession, p: Any, row: SpeakrRecording, names: list[str], guests: list[dict[str, str]],
                me: dict[str, Any]) -> list[Cand]:
    """Everyone who could be one of the speakers: the linked meeting's guests, the mail around the
    meeting, group members and People whose names match a speaker's (with nicknames and close
    spellings), minus the user."""
    uid = p.user_id
    pool: dict[str, Cand] = {}

    def add(c: Cand) -> None:
        n = _person_name(c.name)
        if c.email in me["addresses"] or (len(n.split()) >= 2 and n in me["names"]):
            return
        if c.email in pool:
            pool[c.email].merge(c)
        else:
            pool[c.email] = c

    for g in guests:
        add(Cand(g["email"], g.get("name") or "", event=True))
    for email, (nm, how) in (await _thread_people(db, p, row)).items():
        add(Cand(email, nm, thread=how))

    words: set[str] = set()
    close: set[str] = set()
    fulls: set[str] = set()
    for n in names:
        toks = _norm(n).split()
        if not toks:
            continue
        if len(toks) > 1:
            fulls.add(_norm(n))
        words.add(toks[0])
        words |= NICKNAMES.get(toks[0], set())
        if len(toks[0]) >= 4:
            close.add(toks[0][:2])
    conds = []
    if words:
        conds.append(func.lower(Person.name).op("~")("\\m(" + "|".join(re.escape(w) for w in sorted(words)) + ")\\M"))
    if fulls:
        conds.append(func.lower(Person.name).in_(sorted(fulls)))
    rows: list[Person] = []
    if conds:
        rows += list((await db.execute(select(Person).where(
            Person.user_id == uid, Person.blocked.is_(False), Person.name != "", or_(*conds))
            .order_by(rank_expr().desc()).limit(400))).scalars())
    if close:
        rows += list((await db.execute(select(Person).where(
            Person.user_id == uid, Person.blocked.is_(False),
            or_(*[func.lower(Person.name).like(f"{c}%") for c in sorted(close)]))
            .order_by(rank_expr().desc()).limit(200))).scalars())
    have = {r.email.lower() for r in rows}
    extra = [e for e in pool if e not in have]
    if extra:
        rows += list((await db.execute(select(Person).where(Person.user_id == uid,
                                                            func.lower(Person.email).in_(extra)))).scalars())
    for r in rows:
        if r.blocked and r.email.lower() not in pool:
            continue
        add(Cand(r.email.lower(), r.name or "", organization=r.organization or "", rank=_rank_of(r)))

    groups: dict[str, list[str]] = {}
    for gname, email, mname in (await db.execute(
            select(PersonGroup.name, PersonGroupMember.email, PersonGroupMember.name)
            .join(PersonGroupMember, PersonGroupMember.group_id == PersonGroup.id)
            .where(PersonGroup.user_id == uid, PersonGroup.state == "active"))).all():
        e = email.lower()
        groups.setdefault(e, []).append(gname)
        if e not in pool and mname and any(name_match(n, mname)[1] for n in names):
            add(Cand(e, mname))
    for e, c in pool.items():
        c.groups = groups.get(e, [])
    return list(pool.values())


async def _mappings(db: AsyncSession, conn: Connector, keys: list[str] | None = None) -> list[SpeakrSpeakerPerson]:
    q = select(SpeakrSpeakerPerson).where(SpeakrSpeakerPerson.connector_id == conn.id)
    if keys is not None:
        q = q.where(SpeakrSpeakerPerson.speaker_key.in_(keys))
    return list((await db.execute(q)).scalars())


async def _person_out(db: AsyncSession, uid: int, email: str, name: str, me: dict[str, Any]) -> dict[str, Any]:
    if email in me["addresses"]:
        return {"email": me["email"] or email, "name": me["name"] or name, "organization": "", "me": True}
    r = (await db.execute(select(Person).where(Person.user_id == uid, func.lower(Person.email) == email)
                          .limit(1))).scalar_one_or_none()
    return {"email": email, "name": name or (r.name if r else ""),
            "organization": (r.organization if r else "") or org_from_email(email), "me": False}


# ACT speaker.map @api
async def speakers_with_people(db: AsyncSession, p: Any, rid: int, cid: int | None = None) -> dict[str, Any]:
    """The recording's speakers, each with its person (confirmed, or the user: `me`), whether that
    applies to every recording with the speaker (`applies`: all | recording), the key a choice is
    stored under (`map_key`), the key of "Not them in this recording" (`override_key`), and up to
    three ranked candidates (`suggestions`; `more` counts the rest); plus the linked event's
    guests and the user."""
    conn, row = await store.find(db, p, rid, cid)
    speakers = await store.part(db, conn, row.recording_id, "speakers")
    me = await me_of(db, p.user_id)
    guests = await _guests(db, p, conn, row.recording_id, me["addresses"])
    keys = [speaker_key(s | {"recording_id": row.recording_id}) for s in speakers]
    ovs = ["" if k.startswith("label:") else override_key(row.recording_id, k) for k in keys]
    ids = [_id_key(s) for s in speakers]
    maps = await _mappings(db, conn, keys + [k for k in ovs if k] + [k for k in ids if k])
    await _follow_renames(db, p, conn, speakers, keys, ids, maps)

    resolved = []
    for s, key, ov in zip(speakers, keys, ovs, strict=True):
        base = [m for m in maps if m.speaker_key == key]
        over = [m for m in maps if ov and m.speaker_key == ov]
        g_conf = next((m for m in base if m.state == "confirmed"), None)
        o_conf = next((m for m in over if m.state == "confirmed"), None)
        o_dis = {m.email for m in over if m.state == "dismissed"}
        suppressed = g_conf is not None and g_conf.email in o_dis
        person, applies = None, "recording"
        if o_conf is not None:
            person = o_conf
        elif g_conf is not None and not suppressed:
            person, applies = g_conf, ("recording" if key.startswith("label:") else "all")
        map_key = ov if (suppressed or o_conf is not None) and ov else key
        if person is None:
            applies = "recording" if map_key != key or key.startswith("label:") else "all"
        name = s.get("name") or ""
        resolved.append({"s": s, "key": key, "ov": ov, "person": person, "applies": applies, "map_key": map_key,
                         "dismissed": {m.email for m in base if m.state == "dismissed"} | o_dis, "name": name,
                         "me": bool(person is None and not suppressed and name and _person_name(name) in me["names"])})

    taken = {r["person"].email for r in resolved if r["person"] is not None}
    open_names = [r["name"] for r in resolved if r["person"] is None and r["name"] and not _is_label(r["name"])]
    need_pool = any(r["person"] is None and not r["me"] for r in resolved)
    pool = await _pool(db, p, row, open_names, guests, me) if need_pool else []

    from mailr.integrations.speakr import participants

    out = []
    for r in resolved:
        s, name = r["s"], r["name"]
        cands: list[dict[str, Any]] = []
        is_me, person_out, via_alias = r["me"], None, None
        if r["person"] is not None:
            person_out = await _person_out(db, p.user_id, r["person"].email, r["person"].person_name, me)
            is_me = person_out["me"]
        elif not is_me:
            unnamed = not name or _is_label(name)
            cands = rank_candidates(name, pool, exclude=taken | r["dismissed"], unnamed=unnamed)
            saved = str(s.get("email") or "").strip().lower()
            if saved and "@" in saved and saved not in taken | r["dismissed"] | me["addresses"]:
                # The address the user gave this speaker in Speakr comes first.
                cands = [c for c in cands if c["email"] != saved]
                cands.insert(0, {"email": saved, "name": name if not unnamed else "", "organization": org_from_email(saved),
                                 "emails": [saved], "hints": ["address saved in Speakr"],
                                 "why": "address saved in Speakr", "match": "speakr", "in_meeting": False,
                                 "in_mail": False, "score": 1000.0})
            if (not unnamed and " " not in _norm(name) and _first(name) in me["first"]
                    and not any(c["in_meeting"] or c["in_mail"] for c in cands if c["match"] in ("first", "full"))
                    and not await participants.is_separate(db, conn, p.user_id, name)):
                is_me, cands = True, []  # "Alex" with no other Alex around the meeting
            elif not unnamed:
                # A name merged in the Participants list gets the person of the name it was merged into.
                got = await participants.alias_person(db, conn, p.user_id, name, me)
                if got is not None and got[0] == participants.ME:
                    is_me, cands = True, []
                elif got is not None and got[0] not in r["dismissed"]:
                    person_out = await _person_out(db, p.user_id, got[0], "", me)
                    is_me, cands, via_alias = person_out["me"], [], got[1] or None
                    r["applies"] = "all"
        if is_me and person_out is None:
            person_out = {"email": me["email"], "name": me["name"] or name, "organization": "", "me": True}
        out.append({"label": s.get("label") or "", "name": name, "speaker_id": s.get("speaker_id"), "key": r["key"],
                    "map_key": r["map_key"], "override_key": r["ov"] or None, "me": is_me, "person": person_out,
                    "applies": r["applies"], "suggestions": cands[:SHOWN], "more": max(0, len(cands) - SHOWN),
                    "via_alias": via_alias})
    return {"connector_id": conn.id, "recording_id": row.recording_id, "speakers": out, "guests": guests,
            "me": {"email": me["email"], "name": me["name"]},
            # Speakr keeps an address per speaker: a confirmed person can be written there.
            "speakr_email": any("email" in s for s in speakers)}


def _id_key(sp: dict[str, Any]) -> str:
    """"id:<speaker id>" for a named speaker that Speakr links to a saved speaker, else ""."""
    sid = sp.get("speaker_id")
    name = _norm(sp.get("name") or "")
    if isinstance(sid, int) and not isinstance(sid, bool) and name and not _is_label(name):
        return f"id:{sid}"
    return ""


async def _follow_renames(db: AsyncSession, p: Any, conn: Connector, speakers: list[dict[str, Any]], keys: list[str],
                          ids: list[str], maps: list[SpeakrSpeakerPerson]) -> None:
    """A speaker renamed in Speakr keeps its person: a confirmed "id:" mapping with no confirmed
    mapping under the new name becomes one (and `maps` gains it)."""
    for s, key, idk in zip(speakers, keys, ids, strict=True):
        if not idk or not key.startswith("name:"):
            continue
        if any(m.speaker_key == key and m.state == "confirmed" for m in maps):
            continue
        by_id = next((m for m in maps if m.speaker_key == idk and m.state == "confirmed"), None)
        if by_id is None or any(m.speaker_key == key and m.email == by_id.email for m in maps):
            continue  # nothing to follow, or the user said "Not them" under the new name
        maps.append(await map_speaker(db, p, conn.id, key=key, speaker_name=str(s.get("name") or ""),
                                      email=by_id.email, name=by_id.person_name, source="speakr"))


async def map_speaker(db: AsyncSession, p: Any, cid: int, *, key: str, speaker_name: str, email: str,
                      name: str = "", source: str = "user", speaker_id: int | None = None) -> SpeakrSpeakerPerson:
    """Confirm a person for a speaker key. A name or id key applies on every recording; a
    "rec:<recording>:" key only on that recording (its row keeps no speaker name). With
    `speaker_id` (Speakr's saved speaker), a name key is also stored under "id:<speaker id>"."""
    if speaker_id is not None and key.startswith("name:"):
        await map_speaker(db, p, cid, key=f"id:{int(speaker_id)}", speaker_name=speaker_name, email=email, name=name,
                          source=source)
    conns = [c for c in await store.visible_connectors(db, p) if c.id == int(cid)]
    if not conns:
        raise LookupError("Speakr account not found")
    conn = conns[0]
    email = (email or "").strip().lower().removeprefix("mailto:")
    if "@" not in email or not key:
        raise ValueError("a speaker and an email address are needed")
    key = key[:200]
    now = datetime.now(UTC)
    found = None
    for m in await _mappings(db, conn, [key]):
        if m.email == email:
            found = m
        elif m.state == "confirmed":
            m.state, m.updated_at = "dismissed", now  # one person per speaker
    if found is None:
        found = SpeakrSpeakerPerson(user_id=p.user_id, connector_id=conn.id, speaker_key=key, email=email)
        db.add(found)
    found.speaker_name = "" if key.startswith("rec:") else (speaker_name or "")[:200]
    found.person_name = (name or found.person_name or "")[:300]
    found.state, found.source, found.updated_at = "confirmed", source[:12], now
    await db.flush()
    return found


async def unmap_speaker(db: AsyncSession, p: Any, cid: int, *, key: str, email: str) -> int:
    """Not them: the mapping (or suggestion) of this key and address becomes dismissed. On a
    "rec:<recording>:" key this is "Not them in this recording"."""
    conns = [c for c in await store.visible_connectors(db, p) if c.id == int(cid)]
    if not conns:
        raise LookupError("Speakr account not found")
    email = (email or "").strip().lower()
    n = 0
    for m in await _mappings(db, conns[0], [key]):
        if m.email == email:
            m.state, m.updated_at = "dismissed", datetime.now(UTC)
            n += 1
    if key.startswith("name:"):
        # The same person under the speaker's id goes too, so a rename does not bring it back.
        for m in (await db.execute(select(SpeakrSpeakerPerson).where(
                SpeakrSpeakerPerson.connector_id == conns[0].id, SpeakrSpeakerPerson.speaker_key.like("id:%"),
                SpeakrSpeakerPerson.email == email, SpeakrSpeakerPerson.state == "confirmed"))).scalars():
            if _norm(m.speaker_name) == key[5:]:
                m.state, m.updated_at = "dismissed", datetime.now(UTC)
    if not n:
        db.add(SpeakrSpeakerPerson(user_id=p.user_id, connector_id=conns[0].id, speaker_key=key[:200], email=email,
                                   state="dismissed"))
    await db.flush()
    return n


def mapping_out(m: SpeakrSpeakerPerson) -> dict[str, Any]:
    return {"connector_id": m.connector_id, "key": m.speaker_key, "speaker_name": m.speaker_name, "email": m.email,
            "name": m.person_name, "state": m.state,
            "applies": "recording" if m.speaker_key.startswith(("rec:", "label:")) else "all"}


_REC_KEY = re.compile(r"^(rec|label):(\d+):")


async def person_recordings(db: AsyncSession, p: Any, email: str, *, conns: list[Connector] | None = None,
                            limit: int = 5) -> dict[str, Any]:
    """Recent recordings with a person: participants that name their globally mapped speakers or
    the person, recordings where a per-recording mapping names them, minus "Not them in this
    recording"."""
    conns = conns if conns is not None else await store.visible_connectors(db, p)
    email = (email or "").strip().lower()
    if not conns or "@" not in email:
        return {"count": 0, "items": []}
    by_id = {c.id: c for c in conns}
    maps = (await db.execute(select(SpeakrSpeakerPerson).where(
        SpeakrSpeakerPerson.user_id == p.user_id, SpeakrSpeakerPerson.email == email,
        SpeakrSpeakerPerson.connector_id.in_(list(by_id))))).scalars().all()
    names: set[tuple[int, str]] = set()
    yes: set[tuple[int, int]] = set()
    no: set[tuple[int, int]] = set()
    for m in maps:
        rk = _REC_KEY.match(m.speaker_key)
        if rk:
            k = (m.connector_id, int(rk.group(2)))
            if m.state == "confirmed":
                yes.add(k)
            elif rk.group(1) == "rec":
                no.add(k)
        elif m.state == "confirmed":
            nm = m.speaker_name or (m.speaker_key[5:] if m.speaker_key.startswith("name:") else "")
            if nm:
                names.add((m.connector_id, _norm(nm)))
    person = (await db.execute(select(Person).where(Person.user_id == p.user_id, Person.email == email)
                               .limit(1))).scalar_one_or_none()
    own = _norm(person.name) if person and len(_norm(person.name).split()) >= 2 else ""
    # Names merged in the Participants list into one of these names, or into the address.
    from mailr.models import SpeakrParticipantAlias

    targets = {f"name:{_person_name(n)}" for _, n in names} | ({f"name:{_person_name(own)}"} if own else set())
    for a in (await db.execute(select(SpeakrParticipantAlias).where(
            SpeakrParticipantAlias.user_id == p.user_id, SpeakrParticipantAlias.state == "alias",
            SpeakrParticipantAlias.connector_id.in_(list(by_id))))).scalars():
        if a.target in targets or a.target == f"email:{email}":
            names.add((a.connector_id, a.name))
    rows = (await db.execute(select(SpeakrRecording).where(
        SpeakrRecording.connector_id.in_(list(by_id)), SpeakrRecording.deleted_at.is_(None))
        .order_by(SpeakrRecording.meeting_date.desc().nulls_last(), SpeakrRecording.id.desc()).limit(500))).scalars().all()
    hits = []
    for r in rows:
        k = (r.connector_id, r.recording_id)
        ppl = {_norm(x) for x in r.participants or []} | {_person_name(x) for x in r.participants or []}
        named = any(c == r.connector_id and n in ppl for c, n in names) or (own and own in ppl)
        if k in yes or (k not in no and named):
            hits.append(r)
    return {"count": len(hits), "items": [store.recording_out(by_id[r.connector_id], r) for r in hits[:limit]],
            "local_only": any(by_id[r.connector_id].data_class == "local_only" for r in hits[:limit])}


async def write_back_on(db: AsyncSession, p: Any, cid: int) -> bool:
    """Write-back is on for this Speakr account (one the caller may see)."""
    conns = [c for c in await store.visible_connectors(db, p) if c.id == int(cid)]
    return bool(conns and (conns[0].config or {}).get("write_back"))


async def write_speaker_email(db: AsyncSession, p: Any, cid: int, rid: int | None, speaker_id: int | None,
                              email: str) -> bool | None:
    """Write a confirmed person's address to the speaker in Speakr: only with write-back on,
    a saved speaker id, and a Speakr whose speakers carry `email` (seen on this recording). True
    when written, False when Speakr refused, None when not applicable."""
    import logging

    from mailr.integrations.speakr import client as sc

    if speaker_id is None or rid is None:
        return None
    conns = [c for c in await store.visible_connectors(db, p) if c.id == int(cid)]
    if not conns or not (conns[0].config or {}).get("write_back"):
        return None
    conn = conns[0]
    try:
        _, row = await store.find(db, p, int(rid), conn.id)
        speakers = await store.part(db, conn, row.recording_id, "speakers")
    except (store.RecordingNotFound, sc.SpeakrError):
        return None
    if row.is_shared or not any(s.get("speaker_id") == speaker_id and "email" in s for s in speakers):
        return None
    try:
        await (await store.client_for(db, conn)).set_speaker_email(int(speaker_id), email)
    except sc.SpeakrError as e:
        logging.getLogger("mailr.speakr").warning("could not write the speaker's address to Speakr: %s", e)
        return False
    store.forget(conn.id, row.recording_id)
    return True
