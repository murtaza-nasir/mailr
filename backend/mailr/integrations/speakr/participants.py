"""Recording participants by person (web.recordings, participant.merge).

A recording's participants are the strings Speakr held when it was made. When speakers are merged
in Speakr, only the speaker profiles change, not older recordings, so one person can be "Alex" in old
recordings and "Alex Rivera" in new ones.
Here each participant string is resolved to one identity, in this order:

1. the per-recording override of the speaker mapping ("rec:<recording>:name:<name>", people.py);
2. the confirmed speaker mapping of the name on the connector ("name:<name>");
3. the user ("You"): one of the user's own names, or the user's first name alone when no other
   participant of the recording and no guest of its linked event has that first name, and the
   user has not said the name stands on its own (`separate`);
4. the one correspondent in People with that full name ("Last, First" counts);
5. an alias the user made (`speakr_participant_aliases`): the name is the same person as "me",
   an address, or another name, which is resolved again by steps 2 to 4 (so an alias of a mapped
   name gets that name's person);
6. the string itself.

An address marked "Not them in this recording" is skipped for that recording in steps 2 and 4.
An identity's key is "me", "email:<address>" or "name:<name>" ("First Last", lower case). The
Participants list, its filter, the row chips, the `participant` filter of the list route and the
counts are all by this key.

Suggested merges: a one-word name (a first name) that resolves to nothing is offered as the same
person as an identity with a full name of that first name (or a nickname of it), when the two
never appear in the same recording and their recordings share context (another participant, the
linked event or the folder). The reverse is offered too: a full name that resolves to nothing,
into a first name that has a person. Nothing is merged without the user, except the user's own
first name (step 3). A dismissed suggestion is kept so it is not offered again.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.integrations.speakr import store
from mailr.integrations.speakr.people import NICKNAMES, _norm, _person_name, me_of
from mailr.models import (
    CalObject,
    Connector,
    Person,
    RecordingLink,
    SpeakrParticipantAlias,
    SpeakrRecording,
    SpeakrSpeakerPerson,
)

ME = "me"
ALL_ROWS = 5000


def display(raw: str) -> str:
    """A participant as shown: "Lopez, Mara" becomes "Mara Lopez", spaces collapse, case stays."""
    n = re.sub(r"\s+", " ", (raw or "").strip())
    if n.count(",") == 1:
        last, first = (x.strip() for x in n.split(","))
        if last and first and len(first.split()) <= 3 and len(last.split()) <= 3:
            n = f"{first} {last}"
    return n


@dataclass
class Who:
    key: str  # me | email:<address> | name:<name>
    label: str
    email: str | None
    me: bool
    how: str  # override | mapped | me | me_first | people | alias | raw
    root: str  # the name the identity was found from

    def out(self, raw: str) -> dict[str, Any]:
        return {"name": raw, "email": self.email, "key": self.key, "label": self.label, "me": self.me,
                "how": self.how}


class Resolver:
    """Resolves participant strings of the given connectors (see the module's docstring)."""

    def __init__(self, me: dict[str, Any]):
        self.me = me
        self.mapped: dict[tuple[int, str], str] = {}
        self.this_rec: dict[tuple[int, int, str], str] = {}
        self.not_them: dict[tuple[int, int, str], set[str]] = {}
        self.by_name: dict[str, set[str]] = {}
        self.names_of: dict[str, str] = {}
        self.aliases: dict[tuple[int, str], tuple[str, str]] = {}
        self.separate: set[tuple[int, str]] = set()
        self.dismissed: set[tuple[int, str, str]] = set()

    def _me(self, n: str, how: str) -> Who:
        return Who(ME, "You", self.me.get("email") or None, True, how, n)

    def _email(self, email: str, raw: str, n: str, how: str) -> Who:
        email = email.lower()
        if email in self.me["addresses"]:
            return self._me(n, how)
        label = display(self.names_of.get(email) or "") or display(raw) or email
        return Who(f"email:{email}", label, email, False, how, n)

    def me_first(self, cid: int, n: str, others: set[str] | frozenset[str] = frozenset(),
                 guests: set[str] | frozenset[str] = frozenset()) -> bool:
        """True for the user's first name alone, with nobody else of that first name in the recording or its meeting."""
        if " " in n or n not in self.me["first"] or (cid, n) in self.separate:
            return False
        rivals = {o for o in others | guests if o != n and o not in self.me["names"] and o.split(" ", 1)[0] == n}
        return not rivals

    def resolve(self, cid: int, raw: str, *, rid: int | None = None, others: set[str] | frozenset[str] = frozenset(),
                guests: set[str] | frozenset[str] = frozenset(), depth: int = 0) -> Who:
        nk = _norm(raw)
        n = _person_name(raw)
        no = self.not_them.get((cid, rid, nk), set()) if rid is not None else set()
        if rid is not None:
            e = self.this_rec.get((cid, rid, nk))
            if e and e not in no:
                return self._email(e, raw, n, "override")
        e = self.mapped.get((cid, nk)) or self.mapped.get((cid, n))
        if e and e not in no:
            return self._email(e, raw, n, "mapped")
        if n in self.me["names"]:
            return self._me(n, "me")
        if self.me_first(cid, n, others, guests):
            return self._me(n, "me_first")
        found = self.by_name.get(n, set())
        if len(found) == 1 and not (found & no):
            return self._email(next(iter(found)), raw, n, "people")
        a = self.aliases.get((cid, n))
        if a and depth < 3:
            target, label = a
            if target == ME:
                return self._me(n, "alias")
            if target.startswith("email:") and target[6:] not in no:
                w = self._email(target[6:], label or raw, n, "alias")
                return Who(w.key, w.label, w.email, w.me, "alias", n)
            if target.startswith("name:"):
                inner = self.resolve(cid, target[5:], depth=depth + 1)
                if not (inner.email and inner.email in no):
                    shown = label if inner.how == "raw" and label else inner.label
                    return Who(inner.key, shown, inner.email, inner.me, "alias", inner.root)
        return Who(f"name:{n}", display(raw), None, False, "raw", n)

    def resolve_value(self, conns: list[Connector], v: str) -> tuple[set[str], bool]:
        """The identity keys a filter value stands for, and whether it is only a raw name (then a
        substring match of the participants applies too)."""
        v = (v or "").strip()
        low = v.lower()
        if low in (ME, "you"):
            return {ME}, False
        if low.startswith(("email:", "name:")):
            return {low}, False
        if "@" in low:
            return {self._email(low.removeprefix("mailto:"), "", "", "people").key}, False
        keys = {self.resolve(c.id, v).key for c in conns}
        return keys, all(k.startswith("name:") for k in keys)


async def load(db: AsyncSession, p: Any, conns: list[Connector], raws: set[str]) -> Resolver:
    """A resolver for these connectors and participant strings: five queries."""
    res = Resolver(await me_of(db, p.user_id))
    cids = [c.id for c in conns]
    if not cids:
        return res
    emails: set[str] = set()
    for m in (await db.execute(select(SpeakrSpeakerPerson).where(
            SpeakrSpeakerPerson.user_id == p.user_id, SpeakrSpeakerPerson.connector_id.in_(cids)))).scalars():
        key = m.speaker_key or ""
        rk = re.match(r"^rec:(\d+):name:(.*)$", key)
        email = (m.email or "").lower()
        if rk:
            k3 = (m.connector_id, int(rk.group(1)), _norm(rk.group(2)))
            if m.state == "confirmed":
                res.this_rec[k3] = email
                emails.add(email)
            else:
                res.not_them.setdefault(k3, set()).add(email)
        elif m.state == "confirmed" and (key.startswith("name:") or m.speaker_name):
            nm = _norm(m.speaker_name or key[5:])
            res.mapped[(m.connector_id, nm)] = email
            emails.add(email)
            if m.person_name:
                res.names_of.setdefault(email, m.person_name)
    names = {_person_name(x) for x in raws}
    for a in (await db.execute(select(SpeakrParticipantAlias).where(
            SpeakrParticipantAlias.user_id == p.user_id, SpeakrParticipantAlias.connector_id.in_(cids)))).scalars():
        if a.state == "alias":
            res.aliases[(a.connector_id, a.name)] = (a.target, a.target_label)
            if a.target.startswith("name:"):
                names.add(a.target[5:])
            elif a.target.startswith("email:"):
                emails.add(a.target[6:])
        elif a.state == "separate":
            res.separate.add((a.connector_id, a.name))
        elif a.state == "dismissed":
            res.dismissed.add((a.connector_id, a.name, a.target))
    full = sorted(n for n in names if len(n.split()) >= 2)
    if full:
        flipped = [f"{n.rsplit(' ', 1)[1]}, {n.rsplit(' ', 1)[0]}" for n in full]
        for name, email in (await db.execute(select(Person.name, Person.email).where(
                Person.user_id == p.user_id, Person.blocked.is_(False),
                func.lower(Person.name).in_(full + flipped)))).all():
            res.by_name.setdefault(_person_name(name), set()).add(email.lower())
    if emails:
        for name, email in (await db.execute(select(Person.name, Person.email).where(
                Person.user_id == p.user_id, func.lower(Person.email).in_(sorted(emails)), Person.name != ""))).all():
            res.names_of[email.lower()] = name
    return res


async def guests_of(db: AsyncSession, p: Any, links: dict[tuple[int, int], str], mine: set[str]) -> dict[str, set[str]]:
    """The names ("first last") of the guests of linked events, by event UID, without the user."""
    uids = sorted(set(links.values()))
    out: dict[str, set[str]] = {}
    if not uids:
        return out
    for uid, organizer, organizer_name, attendees in (await db.execute(select(
            CalObject.uid, CalObject.organizer, CalObject.organizer_name, CalObject.attendees).where(
            CalObject.user_id == p.user_id, CalObject.uid.in_(uids), CalObject.kind == "event"))).all():
        people = [{"email": organizer, "name": organizer_name}] + [a for a in attendees or [] if isinstance(a, dict)]
        for a in people:
            email = str(a.get("email") or "").lower().removeprefix("mailto:")
            name = _person_name(str(a.get("name") or ""))
            if name and email not in mine:
                out.setdefault(uid, set()).add(name)
    return out


async def event_links(db: AsyncSession, p: Any, keys: set[tuple[int, int]]) -> dict[tuple[int, int], str]:
    """Each recording's event: a confirmed link first, else the best suggestion."""
    best: dict[tuple[int, int], RecordingLink] = {}
    if not keys:
        return {}
    for lk in (await db.execute(select(RecordingLink).where(
            RecordingLink.user_id == p.user_id, RecordingLink.kind == "event", RecordingLink.state != "dismissed",
            RecordingLink.connector_id.in_({k[0] for k in keys}),
            RecordingLink.recording_id.in_({k[1] for k in keys})))).scalars():
        k = (lk.connector_id, lk.recording_id)
        if k not in keys:
            continue
        cur = best.get(k)
        if cur is None or (lk.state == "confirmed", lk.score or 0.0) > (cur.state == "confirmed", cur.score or 0.0):
            best[k] = lk
    return {k: lk.target for k, lk in best.items()}


def resolve_row(res: Resolver, r: SpeakrRecording, guests: set[str] | frozenset[str] = frozenset()) -> list[Who]:
    parts = list(r.participants or [])
    normed = [_person_name(x) for x in parts]
    out = []
    for i, x in enumerate(parts):
        others = {n for j, n in enumerate(normed) if j != i and n}
        out.append(res.resolve(r.connector_id, x, rid=r.recording_id, others=others, guests=guests))
    return out


async def resolve_rows(db: AsyncSession, p: Any, conns: list[Connector], rows: list[SpeakrRecording],
                       links: dict[tuple[int, int], str] | None = None) -> tuple[Resolver, list[list[Who]]]:
    """Every participant of these recordings, resolved."""
    res = await load(db, p, conns, {x for r in rows for x in r.participants or []})
    if links is None:
        links = await event_links(db, p, {(r.connector_id, r.recording_id) for r in rows})
    guests = await guests_of(db, p, links, res.me["addresses"])
    return res, [resolve_row(res, r, frozenset(guests.get(links.get((r.connector_id, r.recording_id), ""), ())))
                 for r in rows]


async def all_rows(db: AsyncSession, conns: list[Connector]) -> list[SpeakrRecording]:
    if not conns:
        return []
    return list((await db.execute(select(SpeakrRecording).where(
        SpeakrRecording.connector_id.in_([c.id for c in conns]), SpeakrRecording.deleted_at.is_(None))
        .order_by(SpeakrRecording.id.desc()).limit(ALL_ROWS))).scalars())


def filter_rows(res: Resolver, conns: list[Connector], rows: list[SpeakrRecording], whos: list[list[Who]],
                value: str) -> list[SpeakrRecording]:
    """The recordings with the person a `participant` value names (merged names count as one)."""
    keys, raw_only = res.resolve_value(conns, value)
    low = value.strip().lower()
    out = []
    for r, ws in zip(rows, whos, strict=True):
        if any(w.key in keys for w in ws) or (raw_only and any(low in (x or "").lower() for x in r.participants or [])):
            out.append(r)
    return out


# ---------------------------------------------------------------- the Participants list


def people_counts(rows: list[SpeakrRecording], whos: list[list[Who]]) -> list[dict[str, Any]]:
    """Identities by the number of recordings they are in (You first), with the strings each merges."""
    acc: dict[str, dict[str, Any]] = {}
    for r, ws in zip(rows, whos, strict=True):
        seen: set[str] = set()
        for raw, w in zip(r.participants or [], ws, strict=True):
            cur = acc.setdefault(w.key, {"key": w.key, "label": w.label, "email": w.email, "me": w.me, "count": 0,
                                         "names": Counter()})
            cur["names"][display(raw)] += 1
            if w.key not in seen:
                seen.add(w.key)
                cur["count"] += 1
    out = sorted(acc.values(), key=lambda x: (not x["me"], -x["count"], x["label"].lower()))
    for x in out:
        x["names"] = [n for n, _ in x["names"].most_common()]
    return out


def suggestions(res: Resolver, rows: list[SpeakrRecording], whos: list[list[Who]],
                links: dict[tuple[int, int], str]) -> list[dict[str, Any]]:
    """Suggested merges, most recordings first (see the module's docstring)."""
    recs: dict[tuple[int, str], set[int]] = {}
    ctx: dict[tuple[int, str], set[str]] = {}
    who_of: dict[tuple[int, str], Who] = {}
    fulls: dict[tuple[int, str], Counter[str]] = {}
    singles: dict[tuple[int, str], str] = {}
    for r, ws in zip(rows, whos, strict=True):
        cid = r.connector_id
        keys = {w.key for w in ws}
        context = {f"p:{k}" for k in keys if k != ME}
        if (cid, r.recording_id) in links:
            context.add(f"e:{links[(cid, r.recording_id)]}")
        if r.folder and (r.folder or {}).get("name"):
            context.add(f"f:{r.folder['name']}")
        for raw, w in zip(r.participants or [], ws, strict=True):
            k = (cid, w.key)
            who_of.setdefault(k, w)
            recs.setdefault(k, set()).add(r.recording_id)
            ctx.setdefault(k, set()).update(context - {f"p:{w.key}"})
            n = _person_name(raw)
            if len(n.split()) >= 2:
                fulls.setdefault(k, Counter())[display(raw)] += 1
            elif n and w.how in ("raw", "mapped", "people"):
                singles[k] = n
    out = []
    for (cid, key), n in singles.items():
        if key == ME:
            continue
        w = who_of[(cid, key)]
        firsts = {n} | NICKNAMES.get(n, set())
        best: list[tuple[int, tuple[int, str], str]] = []
        for (fcid, fkey), names in fulls.items():
            if fcid != cid or fkey in (key, ME):
                continue
            fw = who_of[(fcid, fkey)]
            full = names.most_common(1)[0][0]
            if _person_name(full).split(" ", 1)[0] not in firsts:
                continue
            if w.how != "raw" and fw.how != "raw":
                continue  # both have a person already
            if recs[(cid, key)] & recs[(fcid, fkey)]:
                continue  # in the same recording: two people
            score = len(ctx[(cid, key)] & ctx[(fcid, fkey)])
            if score:
                best.append((score, (fcid, fkey), full))
        if not best:
            continue
        best.sort(key=lambda x: -x[0])
        if len(best) > 1 and best[0][0] == best[1][0]:
            continue  # two people fit equally: no suggestion
        score, fk, full = best[0]
        fw = who_of[fk]
        if w.how == "raw":
            name, label, count = n, w.label, len(recs[(cid, key)])
            target, into_label, into_key = f"name:{_person_name(full)}", fw.label, fw.key
            target_label = full
        else:
            name, label, count = _person_name(full), full, len(recs[fk])
            target, into_label, into_key = _target_of(w, key), w.label, key
            target_label = w.label
        if (cid, name, target) in res.dismissed:
            continue
        out.append({"connector_id": cid, "name": name, "label": label, "count": count, "into": target,
                    "into_label": into_label, "into_key": into_key, "target_label": target_label, "score": score})
    out.sort(key=lambda x: (-x["count"], x["label"].lower()))
    return out


def _target_of(w: Who, key: str) -> str:
    """How an alias points at an identity: "me", the name a person was found from (so a later
    change of that name's mapping carries over), else the address or the name."""
    if key == ME:
        return ME
    if key.startswith("email:") and w.how in ("mapped", "people") and w.root:
        return f"name:{w.root}"
    return key


async def overview(db: AsyncSession, p: Any) -> dict[str, Any]:
    conns = await store.visible_connectors(db, p)
    rows = await all_rows(db, conns)
    links = await event_links(db, p, {(r.connector_id, r.recording_id) for r in rows})
    res, whos = await resolve_rows(db, p, conns, rows, links)
    aliases = [{"connector_id": cid, "name": n, "target": t, "target_label": lbl}
               for (cid, n), (t, lbl) in sorted(res.aliases.items())]
    return {"people": people_counts(rows, whos), "suggestions": suggestions(res, rows, whos, links),
            "aliases": aliases, "write_back": [c.id for c in conns if (c.config or {}).get("write_back")]}


# ---------------------------------------------------------------- merge, separate, dismiss


async def _rows_of(db: AsyncSession, cid: int, uid: int, name: str, state: str) -> list[SpeakrParticipantAlias]:
    return list((await db.execute(select(SpeakrParticipantAlias).where(
        SpeakrParticipantAlias.user_id == uid, SpeakrParticipantAlias.connector_id == cid,
        SpeakrParticipantAlias.name == name, SpeakrParticipantAlias.state == state))).scalars())


async def _set_alias(db: AsyncSession, uid: int, cid: int, name: str, target: str, label: str) -> None:
    now = datetime.now(UTC)
    await db.execute(delete(SpeakrParticipantAlias).where(
        SpeakrParticipantAlias.user_id == uid, SpeakrParticipantAlias.connector_id == cid,
        SpeakrParticipantAlias.name == name,
        (SpeakrParticipantAlias.state != "dismissed") | (SpeakrParticipantAlias.target == target)))
    if target.startswith("name:"):
        t = target[5:]
        # The other way round would be a loop: the target stops being an alias of this name.
        await db.execute(delete(SpeakrParticipantAlias).where(
            SpeakrParticipantAlias.user_id == uid, SpeakrParticipantAlias.connector_id == cid,
            SpeakrParticipantAlias.name == t, SpeakrParticipantAlias.state == "alias",
            SpeakrParticipantAlias.target == f"name:{name}"))
    # Names merged into this one follow it to the new target.
    for a in (await db.execute(select(SpeakrParticipantAlias).where(
            SpeakrParticipantAlias.user_id == uid, SpeakrParticipantAlias.connector_id == cid,
            SpeakrParticipantAlias.state == "alias", SpeakrParticipantAlias.target == f"name:{name}"))).scalars():
        if a.name != (target[5:] if target.startswith("name:") else ""):
            a.target, a.target_label, a.updated_at = target, label, now
    db.add(SpeakrParticipantAlias(user_id=uid, connector_id=cid, name=name, target=target, target_label=label[:300],
                                  state="alias"))
    await db.flush()


def _clean_target(into: str) -> str:
    into = (into or "").strip()
    low = into.lower()
    if low in (ME, "you"):
        return ME
    if low.startswith("email:") and "@" in low:
        return low
    if low.startswith("name:") and _person_name(into[5:]):
        return f"name:{_person_name(into[5:])}"
    raise ValueError("into must be me, email:<address> or name:<name>")


# ACT participant.merge @api
async def merge(db: AsyncSession, p: Any, *, into: str, key: str = "", name: str = "",
                connector_id: int | None = None, label: str = "") -> dict[str, Any]:
    """The same person: one name (`name`, on `connector_id` or every connector) or every name of an
    identity (`key`) becomes an alias of `into` (an identity key or an alias target). Names that a
    speaker mapping or People already give a person are not changed; they are named in `skipped`."""
    conns = await store.visible_connectors(db, p)
    if connector_id is not None:
        conns = [c for c in conns if c.id == int(connector_id)]
    if not conns:
        raise LookupError("Speakr account not found")
    if key == ME:
        raise ValueError("You stay You: use Separate for a name that is someone else")
    rows = await all_rows(db, conns)
    res, whos = await resolve_rows(db, p, conns, rows)
    into = (into or "").strip()
    if key and into.lower() == key:
        raise ValueError("a person cannot be merged into themselves")
    # An identity key: point at the name it was found from when there is one (_target_of).
    found = [w for ws in whos for w in ws if w.key == into.lower()]
    into_who = next((w for w in found if w.how in ("mapped", "people")), found[0] if found else None)
    target = _target_of(into_who, into_who.key) if into_who is not None else _clean_target(into)
    t_label = label or (into_who.label if into_who is not None else "") or (
        display(into[5:]) if into.lower().startswith("name:") else "")
    todo: dict[tuple[int, str], str] = {}
    skipped: dict[str, str] = {}
    if name:
        n = _person_name(name)
        for c in conns:
            todo[(c.id, n)] = display(name)
    else:
        for r, ws in zip(rows, whos, strict=True):
            for raw, w in zip(r.participants or [], ws, strict=True):
                if w.key != key:
                    continue
                if w.how in ("override", "mapped", "people", "me"):
                    skipped[display(raw)] = w.how
                else:
                    todo[(r.connector_id, _person_name(raw))] = display(raw)
    merged = []
    for (cid, n), shown in todo.items():
        if target == f"name:{n}" or (target == ME and n in res.me["names"]):
            continue
        await _set_alias(db, p.user_id, cid, n, target, t_label or shown)
        merged.append(shown)
    write_back = [c.id for c in conns if (c.config or {}).get("write_back")]
    return {"merged": sorted(set(merged)), "skipped": sorted(skipped), "into": target, "into_label": t_label,
            "write_back": write_back}


# ACT participant.merge
async def separate(db: AsyncSession, p: Any, *, name: str, connector_id: int | None = None) -> dict[str, Any]:
    """Undo a merge: the name's aliases go, and when the name is the user's first name it stands on
    its own from now on (`separate`)."""
    conns = await store.visible_connectors(db, p)
    if connector_id is not None:
        conns = [c for c in conns if c.id == int(connector_id)]
    if not conns:
        raise LookupError("Speakr account not found")
    n = _person_name(name)
    res = await load(db, p, conns, {name})
    removed = 0
    for c in conns:
        for a in await _rows_of(db, c.id, p.user_id, n, "alias"):
            await db.delete(a)
            removed += 1
        if res.me_first(c.id, n) and not await _rows_of(db, c.id, p.user_id, n, "separate"):
            db.add(SpeakrParticipantAlias(user_id=p.user_id, connector_id=c.id, name=n, target="", state="separate"))
            removed += 1
    await db.flush()
    return {"separated": removed, "name": display(name)}


# ACT participant.merge
async def dismiss(db: AsyncSession, p: Any, *, connector_id: int, name: str, into: str) -> dict[str, Any]:
    """A suggested merge the user turned down; it is not offered again."""
    conns = [c for c in await store.visible_connectors(db, p) if c.id == int(connector_id)]
    if not conns:
        raise LookupError("Speakr account not found")
    n, target = _person_name(name), _clean_target(into)
    if not any(a.target == target for a in await _rows_of(db, conns[0].id, p.user_id, n, "dismissed")):
        db.add(SpeakrParticipantAlias(user_id=p.user_id, connector_id=conns[0].id, name=n, target=target,
                                      state="dismissed"))
    await db.flush()
    return {"ok": True}


async def rename_in_speakr(db: AsyncSession, p: Any, *, connector_id: int, name: str, to: str) -> dict[str, Any]:
    """Also rename in Speakr (only with the connector's write-back on): each recording whose
    participants hold `name` gets `to` in its place, through writes.update_recording."""
    from mailr.integrations.speakr import writes

    conns = [c for c in await store.visible_connectors(db, p) if c.id == int(connector_id)]
    if not conns:
        raise LookupError("Speakr account not found")
    writes.require(conns[0], "write_back")
    n, to = _person_name(name), re.sub(r"\s+", " ", to or "").strip()
    if not to:
        raise ValueError("a new name is needed")
    changed = 0
    for r in await all_rows(db, conns):
        parts = list(r.participants or [])
        if not any(_person_name(x) == n for x in parts):
            continue
        new = list(dict.fromkeys(to if _person_name(x) == n else x for x in parts))
        await writes.update_recording(db, p, r.recording_id, conns[0].id, participants=new)
        changed += 1
    return {"renamed": changed}


async def alias_person(db: AsyncSession, conn: Connector, uid: int, name: str,
                       me: dict[str, Any]) -> tuple[str, str] | None:
    """For the recording's People tab (people.py): the person a speaker name gets through an alias,
    ("me", label) or (address, label), or None."""
    n = _person_name(name)
    a = (await db.execute(select(SpeakrParticipantAlias).where(
        SpeakrParticipantAlias.connector_id == conn.id, SpeakrParticipantAlias.user_id == uid,
        SpeakrParticipantAlias.name == n, SpeakrParticipantAlias.state == "alias").limit(1))).scalar_one_or_none()
    if a is None:
        return None
    if a.target == ME:
        return ME, "You"
    if a.target.startswith("email:"):
        return a.target[6:], a.target_label
    t = a.target[5:]
    if t in me["names"]:
        return ME, "You"
    m = (await db.execute(select(SpeakrSpeakerPerson).where(
        SpeakrSpeakerPerson.connector_id == conn.id, SpeakrSpeakerPerson.speaker_key == f"name:{t}",
        SpeakrSpeakerPerson.state == "confirmed").limit(1))).scalar_one_or_none()
    if m is not None:
        return m.email.lower(), a.target_label
    found = {e.lower() for (e,) in (await db.execute(select(Person.email).where(
        Person.user_id == uid, Person.blocked.is_(False), func.lower(Person.name) == _person_name(t)))).all()}
    return (next(iter(found)), a.target_label) if len(found) == 1 else None


async def is_separate(db: AsyncSession, conn: Connector, uid: int, name: str) -> bool:
    return bool(await _rows_of(db, conn.id, uid, _person_name(name), "separate"))
