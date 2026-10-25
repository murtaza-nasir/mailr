"""The away message (vacation responder), as in Gmail: Settings → Mail → Writing → Away message.

User setting `vacation`:
    {"enabled": bool, "start": "YYYY-MM-DD" | "", "end": "YYYY-MM-DD" | "", "subject": str,
     "message": str, "contacts_only": bool, "resend_days": int}

After triage (intel/tasks.py) a fresh inbound message may get one reply per sender every
`resend_days` days (default 4). Never to: mailing lists, bulk or automated mail (Auto-Submitted,
Precedence, no-reply senders), the user's own mail, or mail older than LIVE_WINDOW. The reply
goes through the send gate as the user and carries `Auto-Submitted: auto-replied` (RFC 3834),
which other responders honour, so two away messages never ping-pong.

A reply goes only to a sender whose address is proven: a trusted DMARC pass or a DKIM pass for
the sender's domain, or an SPF pass for an envelope sender (Return-Path) equal to From. Mail in
Spam or Trash, from a blocked sender or scored as likely spam gets none, and the reply waits for
spam scoring. Its subject is the user's own (default "Away message"), never the sender's, and at
most MAX_PER_HOUR replies go out in an hour.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select

from mailr.core.jobs import Retry, enqueue, handler
from mailr.db import session_scope
from mailr.models import Message, Person, User

log = logging.getLogger("mailr.vacation")

DEFAULTS: dict[str, Any] = {"enabled": False, "start": "", "end": "", "subject": "", "message": "",
                            "contacts_only": False, "resend_days": 4}
LIVE_WINDOW = timedelta(days=2)
MAX_PER_HOUR = 30
DEFAULT_SUBJECT = "Away message"
_NOREPLY = re.compile(r"(^|[._+-])(no-?reply|do-?not-?reply|mailer-daemon|postmaster|bounce[s]?|notifications?)([._+-]|@|$)", re.I)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def clean(v: dict[str, Any]) -> dict[str, Any]:
    """Validate the setting (raises ValueError)."""
    out = {**DEFAULTS, **{k: v[k] for k in DEFAULTS if k in v}}
    out["enabled"] = bool(out["enabled"])
    out["contacts_only"] = bool(out["contacts_only"])
    for k in ("start", "end"):
        out[k] = str(out[k] or "").strip()
        if out[k] and not _DATE.match(out[k]):
            raise ValueError(f"away message {k} must be YYYY-MM-DD")
    if out["start"] and out["end"] and out["end"] < out["start"]:
        raise ValueError("the away message ends before it starts")
    out["subject"] = str(out["subject"] or "").strip()[:200]
    out["message"] = str(out["message"] or "").strip()[:5000]
    if out["enabled"] and not out["message"]:
        raise ValueError("write the away message first")
    try:
        out["resend_days"] = max(1, min(int(out["resend_days"]), 30))
    except (TypeError, ValueError):
        out["resend_days"] = 4
    return out


def _today(user: User) -> date:
    from mailr.calendar.tz import user_zone_from

    return datetime.now(user_zone_from(user)).date()


def active(user: User) -> dict[str, Any] | None:
    try:
        v = clean((user.settings or {}).get("vacation") or {})
    except ValueError:
        return None
    if not v["enabled"]:
        return None
    today = _today(user).isoformat()
    if v["start"] and today < v["start"]:
        return None
    if v["end"] and today > v["end"]:
        return None
    return v


def eligible(m: Message, bulk: bool, auto_submitted: bool) -> bool:
    if m.sent_by_me or m.draft or m.deleted or bulk or auto_submitted or m.list_id:
        return False
    if not m.from_email or _NOREPLY.search(m.from_email.split("@")[0]):
        return False
    return (m.date or m.received_at or datetime.now(UTC)) >= datetime.now(UTC) - LIVE_WINDOW


async def maybe_enqueue(db, user: User, m: Message, signals) -> None:
    """Called when triage finishes (intel/tasks.py)."""
    if active(user) is None:
        return
    from mailr.intel.features import is_bulk

    auto = bool(signals.values.get("auto_submitted")) if signals is not None else False
    if not eligible(m, is_bulk(signals) if signals is not None else False, auto):
        return
    await enqueue(db, "vacation.reply", {"message_id": m.id}, user_id=user.id, dedupe_key=f"vacation.reply:{m.id}")


def _sender_verified(auth: dict[str, Any], sender: str) -> bool:
    """DMARC pass for the sender's domain, or a passing DKIM signature for it (or a parent of
    it), from a trusted Authentication-Results header (core/authres.verified_for)."""
    from mailr.core import authres

    dom = sender.rsplit("@", 1)[-1].lower() if "@" in sender else ""
    return authres.verified_for(auth, dom)


def _return_path(raw: bytes | None) -> str:
    if not raw:
        return ""
    from email import message_from_bytes, policy
    from email.utils import parseaddr

    head = raw.split(b"\r\n\r\n", 1)[0].split(b"\n\n", 1)[0]
    try:
        rp = message_from_bytes(head + b"\r\n\r\n", policy=policy.compat32).get_all("Return-Path") or []
    except Exception:  # noqa: BLE001
        return ""
    # Only the top one is the receiving server's; more than one means a sender wrote one.
    return parseaddr(str(rp[0]))[1].lower() if len(rp) == 1 else ""


async def _refusal(db, user: User, m: Message, raw: bytes | None) -> str | None:
    """Why this message gets no away reply, or None."""
    from mailr.intel import spam
    from mailr.models import Job, Label

    trash = (await db.execute(select(Label.id).where(Label.user_id == m.user_id, Label.role == "trash"))).scalar()
    if trash is not None and trash in (m.label_ids or []):
        return "in Trash"
    if await spam.model_skip_reason(db, user, m, raw):
        return "likely spam"
    sender = (m.from_email or "").lower()
    if raw:
        from mailr.calendar.inbound import _senders

        if len(_senders(raw)) > 1:  # a forged From beside a second, authenticated one
            return "more than one sender"
    if not _sender_verified(m.auth_results or {}, sender):
        spf_ok = str((m.auth_results or {}).get("spf") or "").lower() == "pass"
        if not (spf_ok and _return_path(raw) == sender):
            return "unverified sender"
    pending = (await db.execute(select(Job.id).where(
        Job.dedupe_key == f"spam.classify:{m.id}", Job.status.in_(("queued", "running"))).limit(1))).first()
    if pending:
        return "wait"
    return None


@handler("vacation.reply")
async def vacation_reply(payload: dict[str, Any], ctx) -> None:
    from mailr.core.drafts import reply_payload
    from mailr.core.sendgate import SendGate, _SystemPrincipal
    from mailr.knowledge import profile

    async with session_scope() as db:
        m = await db.get(Message, int(payload["message_id"]))
        if m is None or m.deleted:
            return
        user = await db.get(User, m.user_id)
        v = active(user) if user else None
        if v is None:
            return
        sender = (m.from_email or "").lower()
        raw = None
        try:
            from mailr.core.store import user_store

            raw = await (await user_store(db, user)).get_raw(m)
        except Exception:  # noqa: BLE001 - without the raw message only stored facts count
            raw = None
        why = await _refusal(db, user, m, raw)
        if why == "wait":
            raise Retry(delay=60)  # spam scoring first
        if why:
            log.info("no away message for message %s: %s", m.id, why)
            return
        if v["contacts_only"]:
            known = (await db.execute(select(Person.id).where(
                Person.user_id == user.id, Person.email == sender, Person.sent_count > 0))).first()
            if not known:
                return
        prof = await profile.get(db, user.id)
        state = dict(prof.state or {})
        sent: dict[str, str] = dict(state.get("vacation_sent") or {})
        last = sent.get(sender)
        now = datetime.now(UTC)
        if last and datetime.fromisoformat(last) > now - timedelta(days=v["resend_days"]):
            return
        hour_ago = (now - timedelta(hours=1)).isoformat()
        if sum(1 for t in sent.values() if t > hour_ago) >= MAX_PER_HOUR:
            log.info("away message limit reached for user %s", user.id)
            return
        sp = _SystemPrincipal(user)
        body = await reply_payload(db, sp, m.id, "reply")
        body["to"] = [{"email": sender, "name": m.from_name or ""}]
        body["cc"], body["bcc"] = [], []
        # The user's subject, never the sender's: a reply must not carry a stranger's text.
        body["subject"] = v["subject"] or DEFAULT_SUBJECT
        body["text"] = v["message"]
        body.pop("html", None)
        body["attachments"] = []
        await SendGate(db, sp).submit(body, system={"auto_reply": True})
        sent[sender] = datetime.now(UTC).isoformat()
        if len(sent) > 2000:  # keep the most recent senders
            sent = dict(sorted(sent.items(), key=lambda kv: kv[1])[-2000:])
        state["vacation_sent"] = sent
        prof.state = state
        log.info("away message sent to %s for user %s", sender, user.id)
