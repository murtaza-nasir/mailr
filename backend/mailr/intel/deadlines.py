"""Which deadlines are the user's own.

A deadline signal is meant for an obligation the reader has: a bill to pay, a form to return, a
submission, a registration they started, a reply a person asked for. Marketing mail has dates
too ("30% off through October 22", "your points expire"); those are never deadlines, whatever the
model's answer, unless the sender is someone the user writes to or the user has confirmed a
deadline from them before. The user can also turn a deadline down from its chip ("Not a
deadline"), for one conversation or for everything from a sender.

Routes: POST /conversations/{cid}/deadline/dismiss  (ACT mail.dismiss_deadline)
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core.audit import audit
from mailr.core.auth import Principal, scope
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.intel.features import Signals, is_bulk
from mailr.models import Conversation, LabelDecision, Message, User

router = APIRouter(tags=["intelligence"])

MUTED_KEY = "deadline_muted_senders"  # user.settings: senders whose mail never gets a deadline
MAX_MUTED = 500

# Wording of an obligation the reader has. Bills and account notices, calls for papers and
# submissions, forms and applications, a registration the reader started, a renewal.
OBLIGATION = re.compile(
    r"(?i)\b(?:payment (?:is |was )?due|amount due|minimum payment|balance due|past due|overdue|pay by|"
    r"due date|bill is (?:due|ready)|autopay|invoice (?:is )?due|"
    r"call for (?:papers|proposals|abstracts|submissions|chapters)|submissions? (?:are |is )?due|"
    r"submission deadline|(?:paper|abstract|proposal|application)s? (?:are |is )?due|application deadline|"
    r"(?:complete|finish) your (?:registration|application|enrollment|form)|your registration is incomplete|"
    r"(?:return|sign|submit|complete) (?:the|this|your) (?:form|paperwork|document)|"
    r"renew (?:your|by)|renewal (?:is )?due|expires? and must be renewed)\b")

# Marketing wording: sales, offers, points promotions, "ends", "through <date>", "expires".
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
MARKETING = re.compile(
    r"(?i)\b(?:\d{1,2}\s?% off|sale|discounts?|offers?|deals?|promo(?:tion)?s?|promo code|coupons?|"
    r"bonus (?:points|miles)|(?:buy|purchase|gift) (?:points|miles)|points? (?:expire|expiring|expiration)|"
    r"(?:points|miles|rewards|credits?) (?:will )?expire|limited[- ]time|last chance|ends? (?:soon|today|tonight|"
    r"this|on|at|sunday|monday|tuesday|wednesday|thursday|friday|saturday)|(?:now )?through " + _MONTH +
    r"|valid (?:through|until|thru)|expires?|shop now|book now|don'?t miss|sweepstakes|save (?:up to|\$|\d)|"
    r"early[- ]bird|exclusive)\b")


# A period phrase: "now-Dec. 31", "now through October 22", "from Sep 7 to Oct 22", "valid until
# Nov 30". Its end date describes how long an offer or a benefit lasts.
_DATE = rf"(?:{_MONTH}\s*\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s*\d{{4}})?|\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?)"
PERIOD = re.compile(
    rf"(?i)(?:\bnow\s*(?:[-–—]|through|thru|until|till|to)\s*|\b(?:through|thru|until|till)\s+|"
    rf"\bfrom\s+{_DATE}\s*(?:[-–—]|to|through|thru|until)\s*|{_DATE}\s*[-–—]\s*)(?P<end>{_DATE})")
# Something the reader would do by then. With one of these in the same sentence, the period
# end can be a real deadline ("submissions are accepted through November 1").
ACTION = re.compile(r"(?i)\b(?:pay(?:ment)?|activat(?:e|ion)|enrol(?:l|ment)?|submi(?:t|ssions?)|renew(?:al)?|"
                    r"regist(?:er|ration)|respond|reply|appl(?:y|ication)|sign up|return|complete|file|vote|"
                    r"rsvp|confirm)\b")
_SENTENCE = re.compile(r"\n+|(?<=[.!?])\s+(?=[A-Z])")


def period_reason(m: Message, deadline_date: str | None, zone: Any = None) -> str | None:
    """Why the deadline is no deadline: it ends an offer, reward or earning period with nothing for
    the reader to do ("You're earning 5% cash back, now-Dec. 31"). None otherwise."""
    if not deadline_date:
        return None
    from mailr.intel.classify import parse_deadline

    for sentence in _SENTENCE.split(_text(m)):
        for mt in PERIOD.finditer(sentence):
            if parse_deadline(mt.group("end"), m.date, zone) == deadline_date and not ACTION.search(sentence):
                return "An offer or earning period, with nothing to do by then"
    return None


def _text(m: Message) -> str:
    return f"{m.subject or ''}\n{(m.body_text or '')[:6000]}"


def marketing_reason(m: Message, s: Signals, category: str | None) -> str | None:
    """Why this mail is marketing and its dates are not the reader's deadlines, or None. Mail with
    obligation wording (a bill, a call for papers, a form) never counts."""
    text = _text(m)
    if OBLIGATION.search(text):
        return None
    if (category or "") == "promotions":
        return "Promotions mail"
    if is_bulk(s) and MARKETING.search(text):
        return "Marketing mail"
    return None


async def _confirmed_sender(db: AsyncSession, user_id: int, sender: str) -> bool:
    """The user has confirmed a deadline from this sender before (the Why panel's Apply)."""
    return bool((await db.execute(
        select(LabelDecision.id).join(Message, Message.id == LabelDecision.message_id).where(
            LabelDecision.user_id == user_id, LabelDecision.kind == "smart", LabelDecision.target == "deadline",
            LabelDecision.status == "accepted", func.lower(Message.from_email) == sender).limit(1))).first())


def muted(user: User | None, sender: str) -> bool:
    return bool(sender) and sender.lower() in {x.lower() for x in ((user.settings or {}) if user else {}).get(MUTED_KEY) or []}


async def blocked(db: AsyncSession, user: User | None, m: Message, s: Signals,
                  deadline_date: str | None = None) -> str | None:
    """Why a deadline found in this message is dropped, or None to keep it."""
    sender = (m.from_email or "").lower()
    if muted(user, sender):
        return "You said mail from this sender has no deadlines"
    if m.id and (await db.execute(select(LabelDecision.id).where(
            LabelDecision.message_id == m.id, LabelDecision.kind == "smart", LabelDecision.target == "deadline",
            LabelDecision.status == "rejected").limit(1))).first():
        return "You said this is not a deadline"
    # An offer or benefit period is no deadline from anyone: the test is whether the reader must
    # do something by then, not who sent it (a card issuer also sends real bills).
    from mailr.calendar.tz import user_zone

    period = period_reason(m, deadline_date, await user_zone(db, user) if user is not None else None)
    if period:
        return period
    why = marketing_reason(m, s, m.category)
    if why is None:
        return None
    # Someone the user writes to, or a sender whose deadline the user confirmed, keeps theirs.
    if s.values.get("i_wrote_to_sender") or (sender and await _confirmed_sender(db, m.user_id, sender)):
        return None
    return why


# ---------------------------------------------------------------- feedback from the chip


class DismissIn(BaseModel):
    sender: bool = False  # also every deadline from this conversation's senders, now and later


# ACT mail.dismiss_deadline @api
@router.post("/conversations/{cid}/deadline/dismiss")
async def dismiss(cid: int, body: DismissIn, p: Principal = Depends(scope("label")),
                  db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """"Not a deadline" on a Due chip: the conversation's deadline decisions become rejected. They
    leave has:deadline and the chip, and a re-run of triage does not add them again. With sender,
    the senders join the user's list of senders without deadlines, and their other deadlines are
    rejected too."""
    from mailr.core.events import record_change

    conv = await db.get(Conversation, cid)
    if conv is None or conv.user_id != p.user_id or p.is_token:
        raise ApiError(404, "mail.conversation_not_found")
    rows = (await db.execute(
        select(LabelDecision, Message.from_email, Message.conversation_id)
        .join(Message, Message.id == LabelDecision.message_id)
        .where(Message.conversation_id == cid, LabelDecision.kind == "smart", LabelDecision.target == "deadline",
               LabelDecision.value.is_(True), LabelDecision.status != "rejected"))).all()
    senders = sorted({(f or "").lower() for _, f, _ in rows if f})
    if not rows:
        senders = sorted({(f or "").lower() for f in (await db.execute(select(Message.from_email).where(
            Message.conversation_id == cid, Message.sent_by_me.is_(False), Message.deleted.is_(False)))).scalars() if f})
    decisions = [d for d, _, _ in rows]
    convs = {cid}
    if body.sender and senders:
        from mailr.core import usersettings

        st = await usersettings.current(db, p.user_id)
        lst = [x for x in st.get(MUTED_KEY) or [] if x.lower() not in senders] + senders
        await usersettings.patch(db, p.user_id, {MUTED_KEY: lst[-MAX_MUTED:]})
        more = (await db.execute(
            select(LabelDecision, Message.conversation_id).join(Message, Message.id == LabelDecision.message_id)
            .where(Message.user_id == p.user_id, func.lower(Message.from_email).in_(senders), LabelDecision.kind == "smart",
                   LabelDecision.target == "deadline", LabelDecision.value.is_(True),
                   LabelDecision.status != "rejected"))).all()
        for d, c in more:
            decisions.append(d)
            convs.add(c)
    for d in decisions:
        d.status = "rejected"
    await record_change(db, p.user_id, "conversation", sorted(c for c in convs if c))
    await audit(db, p, "deadline.dismiss", str(cid), {"sender": body.sender, "rejected": len(decisions)})
    await db.commit()
    return {"rejected": len({d.id for d in decisions}), "conversation_ids": sorted(c for c in convs if c),
            "senders": senders if body.sender else []}
