"""Suggested groups from the mail the user sends.

The recipients of each sent message (To, Cc and Bcc, without the user's own addresses) form a set.
Sets that recur are clustered: a set joins a cluster when it overlaps the cluster's core by a
Jaccard index of at least 0.6, and a cluster's core is everyone on at least half of its messages.
A cluster of three or more messages with a core of two or more people becomes a suggestion. A
class roster, a committee or a family shows up this way.

Suggestions only: nothing is created until the user accepts one and names it. A suggestion that
matches an existing group, or one the user dismissed (Jaccard 0.8 or more), is never made again.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.models import Identity, Person, PersonGroup, PersonGroupMember, User

LOOKBACK = timedelta(days=730)
MIN_MESSAGES = 3
MIN_SIZE, MAX_SIZE = 2, 60
JOIN = 0.6
SAME = 0.8
KEEP = 12

_FREE = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com", "me.com", "live.com",
         "aol.com", "proton.me", "protonmail.com", "gmx.com", "fastmail.com", "msn.com", "mac.com"}
_STOP = set("re fw fwd the a an and or of for to in on at with from your my our this that is are be by about "
            "meeting update question quick hi hello thanks thank you new next".split())
_COURSE = re.compile(r"\b([A-Z]{2,5})[ -]?(\d{3,4}[A-Z]?)\b")


def jaccard(a: set[str] | frozenset[str], b: set[str] | frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


@dataclass
class Cluster:
    counts: Counter = field(default_factory=Counter)
    n: int = 0
    subjects: list[str] = field(default_factory=list)
    last: datetime | None = None

    def core(self) -> frozenset[str]:
        return frozenset(e for e, c in self.counts.items() if c * 2 >= self.n)


def cluster(sends: list[tuple[frozenset[str], str, datetime | None]]) -> list[Cluster]:
    """Cluster recipient sets. `sends` holds one (recipients, subject, date) per sent message."""
    by_set: dict[frozenset[str], list[tuple[str, datetime | None]]] = {}
    for rc, subj, when in sends:
        if MIN_SIZE <= len(rc) <= MAX_SIZE:
            by_set.setdefault(rc, []).append((subj, when))
    order = sorted(by_set.items(), key=lambda kv: (-len(kv[1]), -len(kv[0]), sorted(kv[0])))
    clusters: list[Cluster] = []
    for rc, msgs in order:
        best, score = None, 0.0
        for c in clusters:
            j = jaccard(rc, c.core())
            if j >= JOIN and j > score:
                best, score = c, j
        if best is None:
            best = Cluster()
            clusters.append(best)
        for e in rc:
            best.counts[e] += len(msgs)
        best.n += len(msgs)
        best.subjects += [s for s, _ in msgs]
        for _, w in msgs:
            if w and (best.last is None or w > best.last):
                best.last = w
    out = [c for c in clusters if c.n >= MIN_MESSAGES and len(c.core()) >= MIN_SIZE]
    out.sort(key=lambda c: (-c.n, -len(c.core())))
    kept: list[Cluster] = []
    for c in out:
        if not any(jaccard(c.core(), k.core()) >= SAME for k in kept):
            kept.append(c)
    return kept


def _norm_subject(s: str) -> str:
    s = re.sub(r"^\s*((re|fwd?|aw|wg)\s*:\s*)+", "", s or "", flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", s).strip()


def name_hint(members: frozenset[str], subjects: list[str], names: dict[str, str]) -> str:
    """A starting name for the Accept field: a course code shared by the subjects ("CHEM 101"), a
    family name shared by the members, the members' common organization, or a recurring phrase."""
    subs = [_norm_subject(s) for s in subjects if s]
    codes = Counter(f"{m.group(1)} {m.group(2)}" for s in subs for m in [_COURSE.search(s)] if m)
    if codes:
        code, n = codes.most_common(1)[0]
        if n * 5 >= 2 * len(subs):
            return code
    last = Counter((names.get(e) or "").split()[-1] for e in members if len((names.get(e) or "").split()) >= 2)
    if last:
        fam, n = last.most_common(1)[0]
        if n >= 2 and n * 2 >= len(members) and fam[:1].isupper():
            return f"{fam} family"
    doms = {e.rsplit("@", 1)[-1] for e in members}
    if len(doms) == 1:
        d = doms.pop()
        if d not in _FREE:
            parts = d.split(".")
            org = parts[-3] if len(parts) >= 3 and parts[-2] in ("co", "ac", "edu", "gov", "com", "org") else parts[-2] if len(parts) >= 2 else d
            return org.capitalize() if len(org) > 4 else org.upper()
    words = Counter()
    for s in subs:
        toks = [t for t in re.findall(r"[A-Za-z][A-Za-z0-9'-]+", s) if t.lower() not in _STOP]
        words.update({" ".join(toks[i:i + 2]) for i in range(len(toks) - 1)})
    if words:
        phrase, n = words.most_common(1)[0]
        if n * 2 >= len(subs) and n >= 2:
            return phrase
    return ""


async def _sends(db: AsyncSession, user_id: int, mine: set[str]) -> list[tuple[frozenset[str], str, datetime | None]]:
    since = datetime.now(UTC) - LOOKBACK
    rows = await db.execute(text(
        "SELECT \"to\", cc, bcc, subject, coalesce(date, received_at) AS t FROM messages "
        "WHERE user_id=:u AND sent_by_me AND NOT draft AND coalesce(date, received_at) >= :s"),
        {"u": user_id, "s": since})
    out = []
    for to, cc, bcc, subj, when in rows:
        rc = frozenset(e for e in ((a.get("email") or "").strip().lower() for a in (to or []) + (cc or []) + (bcc or []))
                       if "@" in e and e not in mine)
        out.append((rc, subj or "", when))
    return out


async def _mine(db: AsyncSession, user_id: int) -> set[str]:
    u = await db.get(User, user_id)
    ids = set((await db.execute(select(Identity.email).where(Identity.user_id == user_id))).scalars())
    return {e.lower() for e in ids | {u.email if u else ""} if e}


async def refresh(db: AsyncSession, user_id: int) -> dict[str, int]:
    # ACT group.suggest @api
    """ACT group.suggest: find recurring recipient sets and store them as suggestions.
    Returns counts: new, updated, skipped (matching a group or a dismissed suggestion)."""
    mine = await _mine(db, user_id)
    clusters = cluster(await _sends(db, user_id, mine))
    groups = (await db.execute(select(PersonGroup).where(PersonGroup.user_id == user_id))).scalars().all()
    mem: dict[int, set[str]] = {g.id: set() for g in groups}
    for gid, e in (await db.execute(select(PersonGroupMember.group_id, PersonGroupMember.email).where(
            PersonGroupMember.group_id.in_([g.id for g in groups])))).all():
        mem[gid].add(e)
    emails = list({e for c in clusters for e in c.core()})
    names = dict((await db.execute(select(Person.email, Person.name).where(
        Person.user_id == user_id, Person.email.in_(emails)))).all()) if emails else {}
    res = {"new": 0, "updated": 0, "skipped": 0}
    open_ = [g for g in groups if g.state == "suggested"]
    for c in clusters:
        core = c.core()
        if any(jaccard(core, mem[g.id]) >= SAME for g in groups if g.state != "suggested"):
            res["skipped"] += 1
            continue
        evidence: dict[str, Any] = {"messages": c.n, "last": c.last.isoformat() if c.last else None,
                                    "subjects": list(dict.fromkeys(_norm_subject(s) for s in c.subjects if s))[:3]}
        score = c.n * math.log2(1 + len(core))
        same = next((g for g in open_ if jaccard(core, mem[g.id]) >= SAME), None)
        if same is not None:
            same.score, same.evidence = score, evidence
            same.name_hint = name_hint(core, c.subjects, names)[:200]
            same.updated_at = datetime.now(UTC)
            if mem[same.id] != set(core):
                await db.execute(text("DELETE FROM person_group_members WHERE group_id=:g"), {"g": same.id})
                for e in sorted(core):
                    db.add(PersonGroupMember(group_id=same.id, email=e, name=names.get(e, "")[:300]))
            res["updated"] += 1
            continue
        if len(open_) >= KEEP:
            continue
        g = PersonGroup(user_id=user_id, name="", source="suggested", state="suggested", score=score, evidence=evidence,
                        name_hint=name_hint(core, c.subjects, names)[:200])
        db.add(g)
        await db.flush()
        for e in sorted(core):
            db.add(PersonGroupMember(group_id=g.id, email=e, name=names.get(e, "")[:300]))
        open_.append(g)
        mem[g.id] = set(core)
        res["new"] += 1
    await db.flush()
    return res
