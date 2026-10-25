"""Correspondent aggregates, updated on ingest, and the strength score.

Strength is recency-weighted correspondence. Each event adds `weight * exp((t - T0) / TAU)` to
`people.strength`: a received message 1, a sent message 3 shared by the square root of its
recipient count (a class roster of 40 adds little to each student), and a reply to that
person's message 2 more. On this fixed time base the stored numbers keep their order as time
passes, so ingest adds to them and nothing has to decay them. `strength_now` turns the stored
number into today's value (about "recent messages"). Ranking multiplies by 1.5 for two-way
correspondents (both sent and received), so people I write to and hear from come first.
`rescore` recomputes every score of a user from the mail (daily, and after the migration).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

from sqlalchemy import Float, case, cast, text
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.models import Message, Person

_FREE_MAIL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com",
              "me.com", "live.com", "aol.com", "proton.me", "protonmail.com", "gmx.com", "fastmail.com"}

T0 = datetime(2020, 1, 1, tzinfo=UTC)
TAU_DAYS = 180.0
W_RECEIVED, W_SENT, W_REPLY = 1.0, 3.0, 2.0
TWO_WAY = 1.5


def org_from_email(email: str) -> str:
    dom = email.rsplit("@", 1)[-1].lower()
    if dom in _FREE_MAIL:
        return ""
    parts = dom.split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "ac", "edu", "gov", "com", "org"):
        return parts[-3]
    return parts[-2] if len(parts) >= 2 else dom


def decay(when: datetime | None) -> float:
    """exp((t - T0) / TAU): the time factor of one event."""
    if when is None:
        return 0.0
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return math.exp((when - T0).total_seconds() / 86400.0 / TAU_DAYS)


def strength_now(stored: float | None, now: datetime | None = None) -> float:
    """The stored score as of now (rounded for display)."""
    if not stored:
        return 0.0
    return round(stored / decay(now or datetime.now(UTC)), 3)


def rank_expr():
    """ORDER BY this DESC: strength, raised for two-way correspondents. People with no score yet
    (address-book contacts, rows before the first rescore) sort after everyone scored."""
    two_way = case(((Person.sent_count > 0) & (Person.received_count > 0), TWO_WAY), else_=1.0)
    return cast(Person.strength, Float) * two_way


def fallback_expr():
    return Person.sent_count * 5 + Person.received_count


async def _upsert(db: AsyncSession, uid: int, email: str, name: str, when: datetime | None,
                  received: int, sent: int, score: float = 0.0) -> None:
    if not email or "@" not in email:
        return
    # The name from mail never replaces a name the user set in their address book or by hand.
    await db.execute(
        text(
            """
            INSERT INTO people (user_id, email, name, organization, received_count, sent_count,
                                first_seen, last_seen, last_sent, notes, blocked, strength)
            VALUES (:u, :e, :n, :o, :r, :s, CAST(:t AS timestamptz), CAST(:t AS timestamptz),
                    CASE WHEN CAST(:s AS int) > 0 THEN CAST(:t AS timestamptz) END, '', false, :w)
            ON CONFLICT (user_id, email) DO UPDATE SET
              name = CASE WHEN EXCLUDED.name <> ''
                          AND coalesce(people.provenance->'name'->>'source', 'mail') = 'mail'
                          THEN EXCLUDED.name ELSE people.name END,
              received_count = people.received_count + EXCLUDED.received_count,
              sent_count = people.sent_count + EXCLUDED.sent_count,
              first_seen = LEAST(people.first_seen, EXCLUDED.first_seen),
              last_seen = GREATEST(people.last_seen, EXCLUDED.last_seen),
              last_sent = GREATEST(people.last_sent, EXCLUDED.last_sent),
              strength = people.strength + EXCLUDED.strength
            """
        ),
        {"u": uid, "e": email.lower(), "n": name or "", "o": org_from_email(email), "r": received,
         "s": sent, "t": when, "w": score},
    )


async def record(db: AsyncSession, m: Message) -> None:
    when = m.date or m.received_at
    f = decay(when)
    if m.sent_by_me:
        rcpts = (m.to or []) + (m.cc or []) + (m.bcc or [])
        share = W_SENT / math.sqrt(max(1, len(rcpts)))
        replied_to = ""
        if m.in_reply_to and not m.draft:
            replied_to = (await db.execute(text(
                "SELECT lower(from_email) FROM messages WHERE user_id=:u AND message_id_hdr=:h AND NOT sent_by_me "
                "LIMIT 1"), {"u": m.user_id, "h": m.in_reply_to})).scalar() or ""
        for a in rcpts:
            email = (a.get("email") or "").lower()
            w = 0.0 if m.draft else share * f + (W_REPLY * f if email and email == replied_to else 0.0)
            await _upsert(db, m.user_id, email, a.get("name", ""), when, 0, 1, w)
    else:
        w = 0.0 if m.list_id else W_RECEIVED * f
        await _upsert(db, m.user_id, m.from_email, m.from_name, when, 1, 0, w)


_RESCORE = """
WITH ev AS (
  SELECT lower(m.from_email) AS e, CAST(:wr AS float8) AS w, coalesce(m.date, m.received_at) AS t
    FROM messages m
   WHERE m.user_id = :u AND NOT m.sent_by_me AND NOT m.draft AND m.list_id IS NULL AND m.from_email <> ''
  UNION ALL
  SELECT lower(x->>'email'),
         CAST(:ws AS float8) / sqrt(greatest(1, jsonb_array_length(coalesce(m."to", '[]'))
                                     + jsonb_array_length(coalesce(m.cc, '[]'))
                                     + jsonb_array_length(coalesce(m.bcc, '[]')))),
         coalesce(m.date, m.received_at)
    FROM messages m
    CROSS JOIN LATERAL jsonb_array_elements(coalesce(m."to", '[]') || coalesce(m.cc, '[]') || coalesce(m.bcc, '[]')) x
   WHERE m.user_id = :u AND m.sent_by_me AND NOT m.draft
  UNION ALL
  SELECT lower(p.from_email), CAST(:wp AS float8), coalesce(m.date, m.received_at)
    FROM messages m JOIN messages p ON p.user_id = m.user_id AND p.message_id_hdr = m.in_reply_to
   WHERE m.user_id = :u AND m.sent_by_me AND NOT m.draft AND m.in_reply_to IS NOT NULL AND NOT p.sent_by_me
), agg AS (
  SELECT e, sum(w * exp(extract(epoch FROM (t - CAST(:t0 AS timestamptz))) / 86400.0 / :tau)) AS s
    FROM ev WHERE t IS NOT NULL AND e <> '' GROUP BY e
)
UPDATE people p SET strength = coalesce(a.s, 0)
  FROM people p2 LEFT JOIN agg a ON a.e = p2.email
 WHERE p.id = p2.id AND p2.user_id = :u AND p.strength IS DISTINCT FROM coalesce(a.s, 0)
"""


async def rescore(db: AsyncSession, user_id: int) -> int:
    """Recompute the strength of every person of a user from their mail. Returns rows changed."""
    res = await db.execute(text(_RESCORE), {"u": user_id, "wr": W_RECEIVED, "ws": W_SENT, "wp": W_REPLY,
                                            "t0": T0, "tau": TAU_DAYS})
    return res.rowcount or 0
