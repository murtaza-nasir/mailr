"""Trusted addresses (user guide site/docs/user-guide/forwarded-notes.md).

A user can name addresses of their own that mailr is not connected to (a work account, say). Mail
they forward from such an address, with a note on top ("add this to my calendar"), lets Ambrose act
on the note through the built-in standing order "Follow my forwarded notes" (assistant/forwards.py),
and the user's own standing orders can match it (AssistantOrder.from_trusted, AssistantOrder.from_me).
This module is the identity part only: the addresses, their codes and the trust rule.

An address is trusted only after the user typed the one-time code mailr mailed to it (through the
normal send path; the code works for CODE_MINUTES minutes; every step is in the audit log).
Suggestions ("these look like yours") come from the user's mail history and are never trusted
until confirmed.

The trust rule (`check`). A message speaks for the user only when all of these hold:

1. From is one of the user's confirmed trusted addresses.
2. Authentication verifies it for that address's domain: a DMARC pass with header.from = the
   domain, or a passing DKIM signature d= the domain (or a parent of it), and no DMARC fail
   (core/authres.verified_for on Message.auth_results, which ingest fills only from the
   top-most Authentication-Results header of a trusted authserv-id). Absent results never count.
3. Nobody else sent, re-sent or relayed it: the From header is one plain address (core/fromline:
   no defects, no second address, no other address in the display name), no Sender header that
   is not exactly that address (a malformed one refuses too), no Resent-* header, no List-Id,
   List-Post or List-Unsubscribe (mailing lists), never a draft.
4. It is addressed to the user alone: at least one To or Cc, and every To and Cc address is one of
   the user's own addresses (account address, sending identities) or trusted addresses. Mail on
   which the user only copied themselves ("Hi Bob, …" with Bcc to mailr) is ordinary mail.

Anything else is ordinary mail. Only the note is an instruction: the text above the forwarded part
(agent/own_links.own_note: the Outlook "From: … Sent: …" block, "---- Forwarded message ----",
"-----Original Message-----", Gmail and Apple markers, attached message/rfc822 parts). What sits
below it is content, untrusted like any mail.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.models import Message, TrustedAddress, User

ACTION = "agent.forwarded_instructions"
CODE_MINUTES = 30
CODES_PER_HOUR = 3  # code mails to one address
CODES_PER_DAY = 10  # code mails of one user, to any addresses
MAX_ATTEMPTS = 5
MAX_ADDRESSES = 10
MAX_NOTE = 4000
# The subject of the code mail starts with this (any language), so codes per hour can be counted.
SUBJECT_TAG = "[Mailr]"
_EMAIL = re.compile(r"[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]+")
# The old switch's modes (clients before 0.32; core/trusted.mode).
MODES = {"allow": "allow", "ask": "ask", "off": "deny"}


def normalize(addr: str) -> str:
    return (addr or "").strip().lower()


def _hash(code: str) -> str:
    return hashlib.sha256(code.strip().encode()).hexdigest()


async def confirmed(db: AsyncSession, user_id: int) -> set[str]:
    rows = (await db.execute(select(TrustedAddress.email).where(
        TrustedAddress.user_id == user_id, TrustedAddress.confirmed_at.is_not(None)))).scalars()
    return {normalize(e) for e in rows}


async def own_addresses(db: AsyncSession, user: User) -> set[str]:
    from mailr.core.ingest import my_addresses

    return await my_addresses(db, user)


# ---------------------------------------------------------------- the trust rule


@dataclass
class Verdict:
    ok: bool
    # why not: a short code (not_trusted, not_verified, list, from, sender, resent, recipients, draft)
    reason: str = ""
    address: str = ""


def _addrs(v: Any) -> list[str]:
    return [normalize(str(a.get("email", ""))) for a in v or [] if isinstance(a, dict) and a.get("email")]


def check_message(m: Message, trusted: set[str], mine: set[str]) -> Verdict:
    """Rules 1, 2, 4 and the stored part of 3, on the stored message (no raw)."""
    from mailr.core import authres

    addr = normalize(m.from_email)
    if m.draft or m.deleted:
        return Verdict(False, "draft", addr)
    if addr not in trusted:
        return Verdict(False, "not_trusted", addr)
    if not authres.verified_for(m.auth_results, addr.rsplit("@", 1)[-1]):
        return Verdict(False, "not_verified", addr)
    if m.list_id or m.list_unsubscribe:
        return Verdict(False, "list", addr)
    rcpts = _addrs(m.to) + _addrs(m.cc)
    if not rcpts or any(r not in mine and r not in trusted for r in rcpts):
        return Verdict(False, "recipients", addr)
    return Verdict(True, "", addr)


def check_headers(raw: bytes, from_addr: str) -> str:
    """Rule 3 on the raw headers: "" when nobody else sent, re-sent or relayed the message. From and
    Sender are read by core/fromline (the one strict reading): a From that is not one plain address
    equal to `from_addr`, or a Sender that does not read cleanly as that address, refuses the
    message (fail closed)."""
    import email.parser
    import email.policy

    from mailr.core import fromline

    msg = email.parser.BytesHeaderParser(policy=email.policy.compat32).parsebytes(raw)
    for h in ("List-Id", "List-Post", "List-Unsubscribe"):
        if msg.get(h):
            return "list"
    if any(k.lower().startswith("resent-") for k in msg.keys()):
        return "resent"
    frm = fromline.of(msg)
    if frm.problem == "several":
        return "sender"  # several From addresses or headers: not one person's message
    if frm.problem or not frm.email or frm.email != normalize(from_addr):
        return "from"  # a From header that does not read as this one plain address
    if not fromline.sender_ok(fromline.raw_values(msg, "Sender"), frm.email):
        return "sender"
    return ""


async def check(db: AsyncSession, user: User, m: Message, raw: bytes | None = None) -> Verdict:
    """The whole trust rule (module docstring). Reads the raw message for rule 3 when `raw` is not given."""
    trusted = await confirmed(db, user.id)
    if normalize(m.from_email) not in trusted:
        return Verdict(False, "not_trusted", normalize(m.from_email))
    v = check_message(m, trusted, await own_addresses(db, user))
    if not v.ok:
        return v
    if raw is None:
        from mailr.core.store import user_store

        raw = await (await user_store(db, user)).get_raw(m)
    why = check_headers(raw, m.from_email)
    return Verdict(False, why, v.address) if why else v


async def from_user(db: AsyncSession, user: User, m: Message, raw: bytes | None = None) -> Verdict:
    """The condition "only from me or my trusted addresses" (AssistantOrder.from_me): From is one of
    the user's own addresses (account, sending identities) or a confirmed trusted address, and the
    rule's checks 2 and 3 hold (its domain's authentication verifies it; nobody else sent, re-sent
    or relayed it; no mailing list). Rule 4 does not apply: mail to an alias of the user's (say
    memory@) counts wherever else it went."""
    from mailr.core import authres

    addr = normalize(m.from_email)
    if m.draft or m.deleted:
        return Verdict(False, "draft", addr)
    if addr not in (await confirmed(db, user.id)) | (await own_addresses(db, user)):
        return Verdict(False, "not_trusted", addr)
    if not authres.verified_for(m.auth_results, addr.rsplit("@", 1)[-1]):
        return Verdict(False, "not_verified", addr)
    if m.list_id or m.list_unsubscribe:
        return Verdict(False, "list", addr)
    if raw is None:
        from mailr.core.store import user_store

        raw = await (await user_store(db, user)).get_raw(m)
    why = check_headers(raw, m.from_email)
    return Verdict(False, why, addr) if why else Verdict(True, "", addr)


# ---------------------------------------------------------------- addresses another assistant handles

# Settings → Ambrose → Trusted addresses → "Addresses for other assistants": the
# user's own addresses (an alias such as memory@) that another program reads and acts on. Mail
# addressed only to them is never acted on by a standing order, the built-in one included, unless
# the order's search names the address.
ELSEWHERE_KEY = "handled_elsewhere"
MAX_ELSEWHERE = 20


def clean_elsewhere(value: Any) -> list[str]:
    """The setting's value: valid addresses, lower-cased, without repeats (raises ValueError)."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("handled_elsewhere must be a list of email addresses")
    out: list[str] = []
    for v in value:
        a = normalize(str(v or ""))
        if not a:
            continue
        if not _EMAIL.fullmatch(a):
            raise ValueError(f"not an email address: {str(v)[:80]}")
        if a not in out:
            out.append(a)
    if len(out) > MAX_ELSEWHERE:
        raise ValueError(f"at most {MAX_ELSEWHERE} addresses")
    return out


def elsewhere_of(user: User) -> list[str]:
    try:
        return clean_elsewhere((user.settings or {}).get(ELSEWHERE_KEY))
    except ValueError:
        return []


def handled_elsewhere(m: Message, user: User, mine: set[str]) -> list[str]:
    """The addresses another assistant handles that this message went to, when it went to no other
    address of the user's ([] otherwise: mailr acts on it as usual). `mine`: the user's own
    addresses (core/ingest.my_addresses). Other people on To or Cc do not matter."""
    listed = set(elsewhere_of(user))
    if not listed:
        return []
    rcpts = _addrs(m.to) + _addrs(m.cc)
    hit = [r for r in dict.fromkeys(rcpts) if r in listed]
    if not hit or any(r in mine and r not in listed for r in rcpts):
        return []
    return hit


_QUERY_ADDR = re.compile(r"[^\s\"'()<>,;:\[\]]+@[^\s\"'()<>,;:\[\]]+")


def names_address(query: str, addresses: list[str]) -> bool:
    """The order's search names one of the addresses (to:memory@example.com, or the address
    alone). Whole addresses only: to:memory@example.com does not name y@example.com."""
    named = {normalize(a).rstrip(".") for a in _QUERY_ADDR.findall(query or "")}
    return any(normalize(a) in named for a in addresses)


def note_of(raw: bytes) -> tuple[str, bool]:
    """(the note, forwarded): the user's own text above the forwarded part, capped."""
    from mailr.agent.own_links import own_note

    text, forwarded = own_note(raw)
    return text[:MAX_NOTE].strip(), forwarded


async def forwarded_note(db: AsyncSession, user: User, m: Message) -> tuple[Verdict, str, bool, bytes | None]:
    """(verdict, note, forwarded, raw) for a message: the note only when the verdict is ok."""
    if normalize(m.from_email) not in await confirmed(db, user.id):
        return Verdict(False, "not_trusted", normalize(m.from_email)), "", False, None
    from mailr.core.store import user_store

    raw = await (await user_store(db, user)).get_raw(m)
    v = await check(db, user, m, raw)
    if not v.ok:
        return v, "", False, raw
    note, fwd = await asyncio.to_thread(note_of, raw)
    return v, note, fwd, raw


# ---------------------------------------------------------------- the old switch (clients before 0.32)


async def mode(db: AsyncSession, user: User) -> tuple[str, str]:
    """(mode, source) for clients before 0.32, which show Allow, Ask me or Off here: Off when the
    built-in order "Follow my forwarded notes" is off, else its permission."""
    from mailr.assistant import orders
    from mailr.models import AssistantOrder

    o = (await db.execute(select(AssistantOrder).where(
        AssistantOrder.user_id == user.id, AssistantOrder.builtin == orders.BUILTIN_FORWARDS))).scalar_one_or_none()
    if o is not None and not o.enabled:
        return "off", "rule"
    effect, source = await orders.permission(db, user)
    return ("allow" if effect == "allow" else "ask"), source


async def set_mode(db: AsyncSession, user: User, value: str) -> None:
    """The old switch, for clients before 0.32: Off turns the built-in order off; Ask me and Allow
    turn it on with that permission."""
    from mailr.assistant import orders

    if value not in MODES:
        raise ValueError("mode must be allow, ask or off")
    o = await orders.ensure_builtin(db, user)
    o.enabled = value != "off"
    if value != "off":
        await orders.set_permission(db, user, value)
    await db.flush()


# ---------------------------------------------------------------- addresses


def state(t: TrustedAddress, now: datetime | None = None) -> str:
    """confirmed | pending (a code is out) | expired (the code ran out; send a new one)."""
    if t.confirmed_at is not None:
        return "confirmed"
    now = now or datetime.now(UTC)
    if t.code_expires_at is None or t.code_expires_at <= now:
        return "expired"
    return "pending"


def out(t: TrustedAddress, last: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"id": t.id, "email": t.email, "state": state(t),
            "confirmed_at": t.confirmed_at.isoformat() if t.confirmed_at else None,
            "code_expires_at": t.code_expires_at.isoformat() if t.code_expires_at and not t.confirmed_at else None,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            # The newest mail from the address and whether its domain's authentication verified it.
            "last_message": last}


async def _last_message(db: AsyncSession, user_id: int, addr: str) -> dict[str, Any] | None:
    from mailr.core import authres
    from mailr.core.publicid import MsgId

    m = (await db.execute(select(Message).where(
        Message.user_id == user_id, func.lower(Message.from_email) == addr, Message.draft.is_(False),
        Message.deleted.is_(False)).order_by(Message.date.desc().nulls_last(), Message.id.desc()).limit(1))).scalar_one_or_none()
    if m is None:
        return None
    return {"id": MsgId(m.id), "subject": (m.subject or "")[:200], "at": m.date.isoformat() if m.date else None,
            "verified": authres.verified_for(m.auth_results, addr.rsplit("@", 1)[-1])}


async def listing(db: AsyncSession, user: User) -> dict[str, Any]:
    rows = (await db.execute(select(TrustedAddress).where(TrustedAddress.user_id == user.id)
                             .order_by(TrustedAddress.email))).scalars().all()
    items = [out(t, await _last_message(db, user.id, t.email) if t.confirmed_at else None) for t in rows]
    m, source = await mode(db, user)
    return {"addresses": items, "suggestions": await suggestions(db, user, {t.email for t in rows}),
            # The addresses another assistant handles (the user setting handled_elsewhere).
            "handled_elsewhere": elsewhere_of(user),
            # For clients before 0.32 only: the built-in order's state (core/trusted.mode).
            "mode": m, "mode_source": "default" if source == "default" else "rule",
            "code_minutes": CODE_MINUTES}


async def suggestions(db: AsyncSession, user: User, skip: set[str] | None = None, limit: int = 5) -> list[dict[str, Any]]:
    """Addresses in the user's mail history that look like theirs: the user's name in the display
    name, verified by the address's domain (DKIM or DMARC), not one of their own addresses, not a
    list. Shown as "these look like yours"; never trusted until confirmed."""
    from mailr.core import authres

    words = [w.lower() for w in re.findall(r"[^\W\d_]{2,}", user.name or "", re.UNICODE)]
    if not words:
        return []
    mine = await own_addresses(db, user)
    skip = {normalize(s) for s in skip or set()} | mine
    since = datetime.now(UTC) - timedelta(days=400)
    conds = [Message.user_id == user.id, Message.draft.is_(False), Message.deleted.is_(False),
             Message.sent_by_me.is_(False), Message.list_id.is_(None), Message.date >= since]
    conds += [func.lower(Message.from_name).contains(w) for w in words]
    rows = (await db.execute(
        select(func.lower(Message.from_email), func.count(), func.max(Message.date), func.max(Message.id))
        .where(*conds).group_by(func.lower(Message.from_email))
        .order_by(func.count().desc()).limit(40))).all()
    found: list[dict[str, Any]] = []
    for addr, n, last, mid in rows:
        if not addr or addr in skip or not _EMAIL.fullmatch(addr):
            continue
        m = await db.get(Message, mid)
        if m is None or not authres.verified_for(m.auth_results, addr.rsplit("@", 1)[-1]):
            continue
        found.append({"email": addr, "name": m.from_name or "", "messages": int(n),
                      "last_at": last.isoformat() if last else None})
        if len(found) >= limit:
            break
    return found


class TrustedError(ValueError):
    """A refusal with an i18n code under errors.trusted (and the HTTP status)."""

    def __init__(self, status: int, code: str, **params: Any):
        super().__init__(code)
        self.status, self.code, self.params = status, code, params


async def _send_code(db: AsyncSession, p: Any, t: TrustedAddress) -> None:
    from mailr import i18n
    from mailr.core.sendgate import SendGate
    from mailr.knowledge import profile
    from mailr.models import Outbox

    now = datetime.now(UTC)
    codes = (Outbox.user_id == p.user.id, Outbox.payload["subject"].astext.ilike(f"{SUBJECT_TAG}%"))
    recent = (await db.execute(select(func.count()).select_from(Outbox).where(
        *codes, Outbox.recipients.contains([t.email]), Outbox.created_at > now - timedelta(hours=1)))).scalar_one()
    if recent >= CODES_PER_HOUR:
        raise TrustedError(429, "trusted.too_many_codes")
    # Across addresses too, counted from the outbox: removing an address and adding another does
    # not reset it.
    today = (await db.execute(select(func.count()).select_from(Outbox).where(
        *codes, Outbox.created_at > now - timedelta(days=1)))).scalar_one()
    if today >= CODES_PER_DAY:
        raise TrustedError(429, "trusted.too_many_codes_today", max=CODES_PER_DAY)
    code = f"{secrets.randbelow(10**6):06d}"
    t.code_hash = _hash(code)
    t.code_expires_at = datetime.now(UTC) + timedelta(minutes=CODE_MINUTES)
    await db.flush()
    user = p.user
    lang = i18n.user_language(user)
    prof = await profile.get(db, user.id)
    name = profile.assistant_name(prof)
    await SendGate(db, p).submit({
        "to": [{"email": t.email, "name": ""}],
        "subject": f"{SUBJECT_TAG} " + i18n.t("trusted.mail.subject", lang, code=code),
        # The account's address, not the free-text name: the name could carry any text.
        "text": i18n.t("trusted.mail.body", lang, who=user.email, address=t.email, assistant=name,
                       code=code, minutes=CODE_MINUTES)})


async def add(db: AsyncSession, p: Any, address: str) -> TrustedAddress:
    """Add an address (or send a new code to one not confirmed yet) and mail it a code."""
    addr = normalize(address)
    if not _EMAIL.fullmatch(addr):
        raise TrustedError(400, "common.invalid_email")
    if addr in await own_addresses(db, p.user):
        raise TrustedError(400, "trusted.already_yours")
    t = (await db.execute(select(TrustedAddress).where(TrustedAddress.user_id == p.user.id,
                                                       TrustedAddress.email == addr))).scalar_one_or_none()
    if t is not None and t.confirmed_at is not None:
        raise TrustedError(409, "trusted.already_confirmed")
    if t is None:
        n = (await db.execute(select(func.count()).select_from(TrustedAddress).where(
            TrustedAddress.user_id == p.user.id))).scalar_one()
        if n >= MAX_ADDRESSES:
            raise TrustedError(400, "trusted.address_limit", max=MAX_ADDRESSES)
        t = TrustedAddress(user_id=p.user.id, email=addr, attempts=0)
        db.add(t)
        await db.flush()
    await _send_code(db, p, t)
    return t


async def get(db: AsyncSession, user: User, tid: int) -> TrustedAddress:
    t = await db.get(TrustedAddress, tid)
    if t is None or t.user_id != user.id:
        raise TrustedError(404, "trusted.not_found")
    return t


async def confirm(db: AsyncSession, user: User, tid: int, code: str) -> TrustedAddress:
    """Check the code. Every guess counts (MAX_ATTEMPTS in all), taken with one conditional update
    before the code is compared, so guesses sent at once cannot pass the limit; an expired
    code never confirms."""
    t = await get(db, user, tid)
    if t.confirmed_at is not None:
        return t
    if t.attempts >= MAX_ATTEMPTS:
        raise TrustedError(429, "trusted.too_many_wrong")
    if state(t) == "expired" or not t.code_hash:
        raise TrustedError(410, "trusted.code_expired")
    took = (await db.execute(update(TrustedAddress).where(
        TrustedAddress.id == t.id, TrustedAddress.attempts < MAX_ATTEMPTS)
        .values(attempts=TrustedAddress.attempts + 1).returning(TrustedAddress.attempts)
        .execution_options(synchronize_session=False))).scalar_one_or_none()
    if took is None:
        raise TrustedError(429, "trusted.too_many_wrong")
    t.attempts = took
    if not hmac.compare_digest(t.code_hash, _hash(code or "")):
        raise TrustedError(400, "trusted.wrong_code")
    t.confirmed_at = datetime.now(UTC)
    t.code_hash, t.code_expires_at = "", None
    await db.flush()
    # The built-in order that acts on notes from this address (seeded for users made after 0.32's migration).
    from mailr.assistant.orders import ensure_builtin

    await ensure_builtin(db, user)
    return t


async def remove(db: AsyncSession, user: User, tid: int) -> str:
    t = await get(db, user, tid)
    addr = t.email
    await db.delete(t)
    await db.flush()
    return addr


def invite_preview(info: dict[str, Any], subject: str | None) -> dict[str, Any]:
    """The approval card of "add this invitation to my calendar" (no reply goes out)."""
    name = info.get("summary") or subject or ""
    ev = {k: info[k] for k in ("summary", "start", "end", "location", "tz") if info.get(k)}
    return {"title": f"Add “{name}” to your calendar"[:120], "event": ev,
            "lines": ["No reply goes to the organiser"]}
