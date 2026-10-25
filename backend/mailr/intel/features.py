"""Cheap per-message signals shared by categories, importance, and label classifiers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from email.parser import BytesHeaderParser

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.models import Message, Person

NOREPLY = re.compile(r"^(no[-_.]?reply|do[-_.]?not[-_.]?reply|donotreply|notifications?|notify|alerts?|"
                     r"mailer[-_.]?daemon|bounce[s]?|news(letter)?|info|updates?|support|hello|team|marketing)"
                     r"([-_.+].*)?@", re.I)
ESP_HEADERS = ("x-mailchimp", "x-mc-user", "x-campaign", "x-campaignid", "x-sfmc", "x-sg-eid",
               "x-mailgun", "x-ses-outgoing", "x-mandrill", "x-sendinblue", "x-klaviyo", "x-hubspot",
               "x-marketo", "x-braze", "x-customerio", "x-cm-", "x-emarsys", "x-listmonk", "x-mailerlite",
               "feedback-id", "x-feedback-id", "x-csa-complaints")

# Fixed order: the learned models are trained on these columns.
FEATURES = [
    "known_sender", "i_wrote_to_sender", "log_received", "log_sent", "sent_by_me", "direct_to_me",
    "only_recipient", "cc_me", "is_list", "bulk", "unsubscribe", "noreply", "esp", "auto_submitted",
    "has_invite", "question", "i_participated", "thread_len", "auth_fail", "has_attachment",
    "reply_to_me", "many_recipients", "hour_business",
]


@dataclass
class Signals:
    values: dict[str, float]
    headers: dict[str, str] = field(default_factory=dict)
    sender: Person | None = None
    reasons: list[str] = field(default_factory=list)

    def vector(self) -> list[float]:
        return [float(self.values.get(k, 0.0)) for k in FEATURES]


def header_map(raw: bytes | None) -> dict[str, str]:
    if not raw:
        return {}
    try:
        msg = BytesHeaderParser().parsebytes(raw[:200_000])
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, str] = {}
    for k, v in msg.items():
        k = k.lower()
        if k not in out:
            out[k] = str(v)
    return out


async def my_addresses(db: AsyncSession, user_id: int) -> set[str]:
    rows = await db.execute(text(
        "SELECT lower(email) FROM identities WHERE user_id=:u UNION SELECT lower(email) FROM users WHERE id=:u"),
        {"u": user_id})
    return {r[0] for r in rows}


async def _conv_stats(db: AsyncSession, conv_ids: list[int]) -> dict[int, tuple[int, bool]]:
    if not conv_ids:
        return {}
    rows = await db.execute(text(
        "SELECT conversation_id, count(*) AS n, bool_or(sent_by_me) AS me FROM messages "
        "WHERE conversation_id = ANY(:c) AND NOT deleted GROUP BY conversation_id"), {"c": conv_ids})
    return {r.conversation_id: (r.n, bool(r.me)) for r in rows}


async def _people(db: AsyncSession, user_id: int, emails: list[str]) -> dict[str, Person]:
    if not emails:
        return {}
    rows = (await db.execute(select(Person).where(Person.user_id == user_id,
                                                  Person.email.in_(emails)))).scalars()
    return {p.email: p for p in rows}


def _signals(m: Message, sender: Person | None, stats: tuple[int, bool] | None, mine: set[str],
             hdr: dict[str, str]) -> Signals:
    import math

    to = [a["email"].lower() for a in (m.to or [])]
    cc = [a["email"].lower() for a in (m.cc or [])]
    rcpts = to + cc
    v: dict[str, float] = {}
    received = sender.received_count if sender else 0
    sent = sender.sent_count if sender else 0
    v["known_sender"] = 1.0 if (received > 1 or sent > 0) else 0.0
    v["i_wrote_to_sender"] = 1.0 if sent > 0 else 0.0
    v["log_received"] = math.log1p(received) / 5
    v["log_sent"] = math.log1p(sent) / 3
    v["sent_by_me"] = 1.0 if m.sent_by_me else 0.0
    v["direct_to_me"] = 1.0 if any(a in mine for a in to) or any(a in mine for a in (m.delivered_to or [])) else 0.0
    v["only_recipient"] = 1.0 if len(rcpts) == 1 and v["direct_to_me"] else 0.0
    v["cc_me"] = 1.0 if any(a in mine for a in cc) else 0.0
    v.update(bulk_values(m, hdr))
    v["has_invite"] = 1.0 if any(a.get("type") in ("text/calendar", "application/ics") for a in (m.attachments or [])) \
        or "text/calendar" in hdr.get("content-type", "") else 0.0
    body = (m.body_text or "")[:4000]
    v["question"] = 1.0 if "?" in body else 0.0
    tlen, part = stats if stats else (1, False)
    v["i_participated"] = 1.0 if part else 0.0
    v["thread_len"] = min(tlen, 20) / 20
    ar = m.auth_results or {}
    v["auth_fail"] = 1.0 if ar.get("dmarc") == "fail" or (ar.get("spf") == "fail" and ar.get("dkim") != "pass") else 0.0
    v["has_attachment"] = 1.0 if m.has_attachments else 0.0
    v["reply_to_me"] = 1.0 if part and m.in_reply_to else 0.0
    v["many_recipients"] = 1.0 if len(rcpts) > 8 else 0.0
    h = (m.date.hour if m.date else 12)
    v["hour_business"] = 1.0 if 7 <= h <= 19 else 0.0
    return Signals(values=v, headers=hdr, sender=sender)


# Learned models are trained and applied on DB-only signals (headers are not stored), so
# training and inference see the same inputs. Heuristics additionally use the headers.
LEARNED_EXCLUDE = ("bulk", "esp", "auto_submitted")


def learned_vector(s: Signals) -> list[float]:
    return [0.0 if k in LEARNED_EXCLUDE else float(s.values.get(k, 0.0)) for k in FEATURES]


async def compute(db: AsyncSession, m: Message, raw: bytes | None = None,
                  mine: set[str] | None = None) -> Signals:
    mine = mine if mine is not None else await my_addresses(db, m.user_id)
    people = await _people(db, m.user_id, [(m.from_email or "").lower()])
    stats = await _conv_stats(db, [m.conversation_id] if m.conversation_id else [])
    return _signals(m, people.get((m.from_email or "").lower()), stats.get(m.conversation_id), mine,
                    header_map(raw))


async def compute_batch(db: AsyncSession, user_id: int, msgs: list[Message]) -> list[Signals]:
    """Header-free signals for many messages at once (training)."""
    mine = await my_addresses(db, user_id)
    people = await _people(db, user_id, list({(m.from_email or "").lower() for m in msgs}))
    stats = await _conv_stats(db, list({m.conversation_id for m in msgs if m.conversation_id}))
    return [_signals(m, people.get((m.from_email or "").lower()), stats.get(m.conversation_id), mine, {})
            for m in msgs]


def bulk_values(m: Message, hdr: dict[str, str]) -> dict[str, float]:
    """The bulk-mail header signals. `hdr` has lower-case keys (header_map); without it only
    the fields stored on the message count (List-Id, List-Unsubscribe, the sender address)."""
    prec = hdr.get("precedence", "").lower()
    auto = hdr.get("auto-submitted", "").lower()
    return {
        "is_list": 1.0 if m.list_id else 0.0,
        "bulk": 1.0 if prec in ("bulk", "list", "junk") else 0.0,
        "unsubscribe": 1.0 if m.list_unsubscribe else 0.0,
        "noreply": 1.0 if NOREPLY.match(m.from_email or "") else 0.0,
        "esp": 1.0 if any(any(h.startswith(e) for e in ESP_HEADERS) for h in hdr) else 0.0,
        "auto_submitted": 1.0 if auto and auto != "no" else 0.0,
    }


def is_automated(m: Message, hdr: dict[str, str] | None = None) -> bool:
    """Bulk or machine-sent mail (the same rules as is_bulk). Mail the user sent never is."""
    if m.sent_by_me:
        return False
    return any(bulk_values(m, hdr or {}).values())


def is_bulk(s: Signals) -> bool:
    v = s.values
    return bool(v["is_list"] or v["bulk"] or v["esp"] or v["unsubscribe"] or v["auto_submitted"]
                or v["noreply"])


