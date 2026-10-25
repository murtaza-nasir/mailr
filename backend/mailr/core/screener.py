"""The sender screener (site/docs/user-guide/screener.md).

Opt-in per user (settings.screener, off by default) and per account (settings.screener_accounts,
{connector id: bool}; an account not listed follows `default_for`: personal accounts yes;
university, government and company accounts no (Workspace or 365 on an own domain, or a domain
at least COLLEAGUES other people write from); local-only accounts no).

At ingest (core/ingest.py), after the filters, the blocked senders and the spam checks, mail on its
way to the Inbox from a first-time sender on a screened account is held: it is stored without the
Inbox label (in Archive, like muted mail), Message.screened_key names the sender, and the sender
waits in Mail → Screener as a ScreenerSender row with status "pending". Mail a filter or a spam
check already moved out of the Inbox is never held. Held mail sends no notification (it is not in
the Inbox) and is not in the Inbox count.

First-time sender: nothing marks them as known. Known is any of:
- the address has a People row with mail either way, a contact detail or a group (People and
  contacts), or the address is in a contact card;
- for a corporate-domain key, you wrote to someone at the domain, or a contact is there;
- a VIP (settings.vip);
- an address on one of your own domains;
- mail in a conversation you wrote in (a reply to a thread you started or joined);
- a calendar invitation from a domain you have mail with (not a shared provider).

Keys (`sender_key`): the address at a shared mail provider (intel/spam.SHARED_PROVIDERS, as the spam
learner does) or at a university, government or military domain; the domain otherwise. A decision
for "example.com" covers every address there.

Decisions (`decide`), on the held mail and on later mail from the key:
- allow: held mail goes to the Inbox; later mail arrives normally (filters still apply);
- block: held mail goes to Spam (the existing block list: the sender's addresses are blocked, the
  spam marks train the spam learner) or, with block_to "trash", to Trash; later mail follows;
- label: held mail gets the label and stays out of the Inbox; later mail too ("Feed" is a label).
Decisions stay in force when the screener is turned off (they are the user's own choices, like
filters); only holding new senders stops, and turning it off releases the held mail to the Inbox.
Mail you send to a pending sender allows them (`allow_recipients`). `forget` undoes a decision:
the sender is screened again.

One definition of "held" (`held_conditions`) serves the Screener list, its sidebar number
(core/sidebar.py: senders waiting, unread = senders with unread held mail) and the conversation
view "screener" (api/mail_read.conversation_conditions).
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, func, not_, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core.jobs import enqueue, handler
from mailr.models import (
    Connector,
    Conversation,
    Identity,
    Label,
    Message,
    ScreenerSender,
    User,
    light_message,
)

log = logging.getLogger("mailr.screener")

SETTING = "screener"
ACCOUNTS = "screener_accounts"
DECISIONS = ("allow", "block", "label")
STATUS_FOR = {"allow": "allowed", "block": "blocked", "label": "label"}
FEED = "Feed"
# University, government and military domains: many unrelated people share one, so decisions there
# are by address, as at shared mail providers.
_INSTITUTION = re.compile(r"(^|\.)(edu|gov|mil)(\.[a-z]{2})?$|(^|\.)ac\.[a-z]{2}$")
# Connector kinds that carry no mail.
_NOT_MAIL = {"caldav", "speakr", "file", "webcal"}
# People who write from an account's own domain before it counts as a work account.
COLLEAGUES = 3


def enabled(user: User | None) -> bool:
    return bool(user is not None and (user.settings or {}).get(SETTING))


def _domain(email: str | None) -> str:
    e = (email or "").strip().lower()
    return e.rsplit("@", 1)[1] if "@" in e else ""


def institutional(domain: str) -> bool:
    return bool(_INSTITUTION.search(domain or ""))


def shared_domain(domain: str) -> bool:
    """A domain many unrelated people share: a mail provider or an institution."""
    from mailr.intel.spam import SHARED_PROVIDERS

    return domain in SHARED_PROVIDERS or institutional(domain)


def sender_key(email: str | None) -> str | None:
    """What a decision is remembered by: the address at a shared provider or an institution, the
    domain otherwise."""
    e = (email or "").strip().lower()
    dom = _domain(e)
    if not dom:
        return None
    return e if shared_domain(dom) else dom


def is_domain_key(key: str) -> bool:
    return "@" not in key


# ---------------------------------------------------------------- accounts


def is_mail_account(c: Connector) -> bool:
    return c.kind not in _NOT_MAIL


async def account_address(db: AsyncSession, c: Connector) -> str:
    row = (await db.execute(select(Identity.email).where(Identity.connector_id == c.id)
                            .order_by(Identity.is_default.desc(), Identity.id).limit(1))).scalar()
    if row:
        return row.lower()
    cfg = c.config or {}
    return str(cfg.get("email") or (cfg.get("imap") or {}).get("username") or "").lower()


def default_screened(c: Connector, address: str) -> tuple[bool, str]:
    """Whether an account is screened when the user has not chosen, and why (shown in Settings)."""
    if c.data_class == "local_only":
        return False, "local-only account"
    dom = _domain(address)
    if dom and institutional(dom):
        return False, "university or government account"
    from mailr.intel.spam import SHARED_PROVIDERS

    if dom and dom not in SHARED_PROVIDERS and c.kind in ("gmail", "m365"):
        # Google Workspace or Microsoft 365 on the organisation's own domain.
        return False, "work account"
    return True, "personal account"


async def _colleagues(db: AsyncSession, uid: int, address: str) -> bool:
    """Other people write from the account's own (non-shared) domain: an organisation's domain."""
    dom = _domain(address)
    if not dom or shared_domain(dom):
        return False
    n = (await db.execute(text(
        "SELECT count(*) FROM people WHERE user_id = :u AND split_part(email, '@', 2) = :d AND email <> :a "
        "AND received_count > 0"), {"u": uid, "d": dom, "a": address})).scalar() or 0
    return n >= COLLEAGUES


async def default_for(db: AsyncSession, uid: int, c: Connector, address: str) -> tuple[bool, str]:
    """default_screened, plus: an account on a domain colleagues write from is a work account."""
    on, why = default_screened(c, address)
    if on and await _colleagues(db, uid, address):
        return False, "work account: colleagues write from its domain"
    return on, why


async def accounts(db: AsyncSession, user: User) -> list[dict[str, Any]]:
    """Every mail account with whether the screener covers it."""
    chosen = (user.settings or {}).get(ACCOUNTS) or {}
    out = []
    for c in (await db.execute(select(Connector).where(Connector.user_id == user.id).order_by(Connector.id))).scalars():
        if not is_mail_account(c):
            continue
        addr = await account_address(db, c)
        default, why = await default_for(db, user.id, c, addr)
        mine = chosen.get(str(c.id))
        out.append({"id": c.id, "name": c.name, "address": addr, "local_only": c.data_class == "local_only",
                    "screened": bool(mine) if isinstance(mine, bool) else default,
                    "default": default, "reason": why, "chosen": isinstance(mine, bool)})
    return out


async def account_screened(db: AsyncSession, user: User, c: Connector | None) -> bool:
    if c is None:
        return False  # mail with no account (an import, a phone's own copy): never held
    mine = ((user.settings or {}).get(ACCOUNTS) or {}).get(str(c.id))
    if isinstance(mine, bool):
        return mine
    return (await default_for(db, user.id, c, await account_address(db, c)))[0]


# ---------------------------------------------------------------- who is known


def _has_invite(m: Message) -> bool:
    return any(a.get("type") in ("text/calendar", "application/ics") or (a.get("filename") or "").lower().endswith(".ics")
               for a in m.attachments or [])


async def _address_known(db: AsyncSession, uid: int, email: str) -> bool:
    return bool((await db.execute(text(
        "SELECT 1 FROM people p WHERE p.user_id = :u AND p.email = :e AND NOT p.blocked AND "
        "(p.sent_count > 0 OR p.received_count > 0 OR p.provenance <> '{}'::jsonb) "
        "UNION ALL SELECT 1 FROM person_group_members gm JOIN person_groups g ON g.id = gm.group_id "
        "  WHERE g.user_id = :u AND g.state = 'active' AND lower(gm.email) = :e "
        "UNION ALL SELECT 1 FROM contact_cards c WHERE c.user_id = :u "
        "  AND c.data->'emails' @> jsonb_build_array(jsonb_build_object('email', CAST(:e AS text))) "
        "LIMIT 1"), {"u": uid, "e": email})).first())


async def _domain_known(db: AsyncSession, uid: int, domain: str, *, any_mail: bool = False) -> bool:
    """You wrote to someone at the domain or have a contact there (with any_mail: or have any mail
    with someone there)."""
    mail = "p.sent_count > 0 OR p.received_count > 0" if any_mail else "p.sent_count > 0"
    return bool((await db.execute(text(
        f"SELECT 1 FROM people p WHERE p.user_id = :u AND split_part(p.email, '@', 2) = :d AND NOT p.blocked "
        f"AND ({mail} OR p.provenance <> '{{}}'::jsonb) LIMIT 1"), {"u": uid, "d": domain})).first())


async def _in_my_conversation(db: AsyncSession, m: Message, conv: Conversation | None) -> bool:
    refs = [r for r in [m.in_reply_to, *(m.references or [])] if r]
    conds = []
    if conv is not None:
        conds.append(Message.conversation_id == conv.id)
    if refs:
        conds.append(Message.message_id_hdr.in_(refs[:50]))
    if not conds:
        return False
    return bool((await db.execute(select(Message.id).where(
        Message.user_id == m.user_id, Message.id != m.id, Message.sent_by_me.is_(True), Message.draft.is_(False),
        or_(*conds)).limit(1))).first())


async def exemption(db: AsyncSession, user: User, m: Message, conv: Conversation | None) -> str | None:
    """Why this sender is not a first-time sender, or None when they are."""
    from mailr.core.ingest import my_addresses

    email = (m.from_email or "").strip().lower()
    dom = _domain(email)
    if not dom:
        return "no sender address"
    if email in {a.lower() for a in (user.settings or {}).get("vip") or []}:
        return "VIP"
    mine = await my_addresses(db, user)
    if email in mine or dom in {_domain(a) for a in mine if not shared_domain(_domain(a))}:
        return "your own domain"
    if await _in_my_conversation(db, m, conv):
        return "reply in a conversation you wrote in"
    if await _address_known(db, user.id, email):
        return "known sender"
    key = sender_key(email)
    if key and is_domain_key(key) and await _domain_known(db, user.id, dom):
        return "known domain"
    if _has_invite(m) and not shared_domain(dom) and await _domain_known(db, user.id, dom, any_mail=True):
        return "invitation from a known domain"
    return None


# ---------------------------------------------------------------- ingest


async def entry_for(db: AsyncSession, uid: int, key: str) -> ScreenerSender | None:
    return (await db.execute(select(ScreenerSender).where(ScreenerSender.user_id == uid,
                                                          ScreenerSender.key == key))).scalar_one_or_none()


async def route(db: AsyncSession, user: User, m: Message, conv: Conversation | None, connector: Connector | None,
                label_ids: set[int], by_role: dict[str, Label]) -> tuple[set[int], str]:
    """Where the screener sends incoming mail on its way to the Inbox: the new label set and what
    happened ("" nothing, "held", "blocked", "label"). Decisions apply on every account; holding a
    new sender only while the screener is on and the account is screened."""
    inbox = by_role.get("inbox")
    if inbox is None or inbox.id not in label_ids or m.sent_by_me:
        return label_ids, ""
    key = sender_key(m.from_email)
    if not key:
        return label_ids, ""
    entry = await entry_for(db, user.id, key)
    if entry is not None and entry.status == "allowed":
        return label_ids, ""
    if entry is not None and entry.status == "blocked":
        target = by_role.get("trash" if entry.block_to == "trash" else "junk")
        if target is None:
            return label_ids, ""
        from mailr.models import LabelDecision

        if entry.block_to != "trash":
            db.add(LabelDecision(user_id=user.id, message_id=m.id, kind="spam", target="spam", value=True,
                                 source="rule", source_ref="screener", confidence=1.0,
                                 reason="blocked in the Screener"))
        return {target.id}, "blocked"
    if entry is not None and entry.status == "label":
        out = set(label_ids) - {inbox.id}
        if entry.label_id and (await db.get(Label, entry.label_id)) is not None:
            out.add(entry.label_id)
        return out, "label"
    if not enabled(user) or not await account_screened(db, user, connector):
        return label_ids, ""
    if entry is None:
        if await exemption(db, user, m, conv) is not None:
            return label_ids, ""
        await db.execute(text(
            "INSERT INTO screener_senders (user_id, key, email, name, status) VALUES (:u, :k, :e, :n, 'pending') "
            "ON CONFLICT (user_id, key) DO NOTHING"),
            {"u": user.id, "k": key, "e": (m.from_email or "").lower(), "n": (m.from_name or "")[:300]})
        entry = await entry_for(db, user.id, key)
        if entry is None or entry.status != "pending":
            return label_ids, ""
    else:  # pending: the latest name and address show on the card
        entry.email, entry.name = (m.from_email or "").lower(), (m.from_name or entry.name)[:300]
    m.screened_key = key
    m.local_change_at = datetime.now(UTC)  # a mirror account's Inbox state must not bring it back
    return set(label_ids) - {inbox.id}, "held"


async def allow_recipients(db: AsyncSession, user: User, m: Message) -> None:
    """Mail the user sent: pending senders among its recipients are allowed (a job, after commit)."""
    if not m.sent_by_me or m.draft:
        return
    keys = {k for k in (sender_key(a.get("email")) for a in (m.to or []) + (m.cc or []) + (m.bcc or [])) if k}
    if not keys:
        return
    hit = (await db.execute(select(ScreenerSender.id).where(
        ScreenerSender.user_id == user.id, ScreenerSender.key.in_(sorted(keys)),
        ScreenerSender.status == "pending"))).scalars().all()
    for sid in hit:
        await enqueue(db, "screener.decide", {"sender_id": sid, "decision": "allow", "by": "reply"},
                      user_id=user.id, dedupe_key=f"screener:{sid}:allow")


# ---------------------------------------------------------------- held mail


async def _role_ids(db: AsyncSession, uid: int) -> dict[str, int]:
    return dict((await db.execute(select(Label.role, Label.id).where(
        Label.user_id == uid, Label.role.in_(("inbox", "junk", "trash"))))).all())


def held_conditions(uid: int, roles: dict[str, int], p=None) -> list[Any]:
    """The one definition of held mail: marked by the screener, still there, and not since moved to
    the Inbox, Spam or Trash by hand. With a principal, only mail it may see."""
    conds: list[Any] = [Message.user_id == uid, Message.screened_key.is_not(None), Message.deleted.is_(False),
                        Message.draft.is_(False)]
    out = [v for k, v in roles.items() if k in ("inbox", "junk", "trash")]
    if out:
        conds.append(not_(Message.label_ids.overlap(out)))
    if p is not None:
        from mailr.core.visibility import visible_message_filter

        conds.append(visible_message_filter(p))
    return conds


async def held_conversation_ids(db: AsyncSession, uid: int, p=None):
    """Subquery: conversations with held mail (the conversation view "screener")."""
    return select(Message.conversation_id).where(*held_conditions(uid, await _role_ids(db, uid), p))


async def counts(db: AsyncSession, p) -> dict[str, int]:
    """The sidebar number: senders waiting, and senders with unread held mail."""
    conds = held_conditions(p.user_id, await _role_ids(db, p.user_id), p)
    row = (await db.execute(select(
        func.count(func.distinct(Message.screened_key)),
        func.count(func.distinct(Message.screened_key)).filter(Message.seen.is_(False)),
    ).where(*conds))).one()
    return {"total": int(row[0] or 0), "unread": int(row[1] or 0)}


def auth_hint(auth: dict[str, Any] | None, email: str) -> dict[str, str]:
    """What the card says about the sender's authentication (RFC 8601 results, core/authres.py)."""
    from mailr.core import authres

    dom = _domain(email)
    if authres.failed(auth):
        return {"level": "failed", "text": f"Failed authentication: this may not really be from {dom}"}
    if authres.verified_for(auth, dom):
        return {"level": "verified", "text": f"Verified: sent by {dom}"}
    return {"level": "unknown", "text": "Not verified: no authentication result for this domain"}


async def pending(db: AsyncSession, p, *, include_local: bool = True, limit: int = 200) -> list[dict[str, Any]]:
    """The senders waiting in the Screener, newest first, with their held mail summarised."""
    uid = p.user_id
    conds = held_conditions(uid, await _role_ids(db, uid), p)
    if not include_local:
        conds.append(Message.local_only.is_(False))
    rows = (await db.execute(select(
        Message.id, Message.screened_key, Message.conversation_id, Message.subject, Message.snippet, Message.date,
        Message.received_at, Message.from_email, Message.from_name, Message.auth_results, Message.seen,
        Message.connector_id, Message.local_only, Message.list_id,
    ).where(*conds).order_by(Message.date.desc().nulls_last(), Message.id.desc()).limit(5000))).all()
    groups: dict[str, list[Any]] = {}
    for r in rows:
        groups.setdefault(r.screened_key, []).append(r)
    if not groups:
        return []
    entries = {e.key: e for e in (await db.execute(select(ScreenerSender).where(
        ScreenerSender.user_id == uid, ScreenerSender.key.in_(list(groups)),
        ScreenerSender.status == "pending"))).scalars()}
    names = dict((await db.execute(select(Connector.id, Connector.name).where(Connector.user_id == uid))).all())
    out = []
    for key, msgs in groups.items():
        e = entries.get(key)
        if e is None:
            continue
        latest, first = msgs[0], msgs[-1]
        out.append({
            "id": e.id, "key": key, "kind": "domain" if is_domain_key(key) else "address",
            "email": latest.from_email.lower(), "name": latest.from_name or e.name or "",
            "addresses": sorted({m.from_email.lower() for m in msgs})[:20],
            "messages": len(msgs), "conversations": len({m.conversation_id for m in msgs}),
            "unread": sum(1 for m in msgs if not m.seen),
            # When the sender first wrote (their oldest held message), not when the row was made.
            "first_seen": (first.date or first.received_at or e.created_at).isoformat(),
            "latest": {"message_id": latest.id, "conversation_id": latest.conversation_id,
                       "subject": latest.subject or "", "snippet": latest.snippet or "",
                       "date": (latest.date or latest.received_at).isoformat() if (latest.date or latest.received_at) else None},
            "auth": auth_hint(latest.auth_results, latest.from_email),
            "newsletter": any(m.list_id for m in msgs),
            "account": names.get(latest.connector_id) or "",
            "local_only": any(m.local_only for m in msgs),
        })
    out.sort(key=lambda s: s["latest"]["date"] or "", reverse=True)
    return out[:limit]


async def decided(db: AsyncSession, uid: int, limit: int = 500) -> list[dict[str, Any]]:
    """Decisions the user made, newest first (Settings → Mail → Spam & Screener, to change or forget one)."""
    rows = (await db.execute(select(ScreenerSender, Label.name).outerjoin(Label, Label.id == ScreenerSender.label_id)
                             .where(ScreenerSender.user_id == uid, ScreenerSender.status != "pending")
                             .order_by(ScreenerSender.decided_at.desc().nulls_last(), ScreenerSender.id.desc())
                             .limit(limit))).all()
    return [{"id": e.id, "key": e.key, "kind": "domain" if is_domain_key(e.key) else "address", "email": e.email,
             "name": e.name, "status": e.status, "label_id": e.label_id, "label": lname or "",
             "block_to": e.block_to, "decided_by": e.decided_by,
             "decided_at": e.decided_at.isoformat() if e.decided_at else None} for e, lname in rows]


async def _held_messages(db: AsyncSession, p, key: str) -> list[Message]:
    conds = held_conditions(p.user_id, await _role_ids(db, p.user_id), p)
    return list((await db.execute(select(Message).options(*light_message()).where(
        *conds, Message.screened_key == key))).scalars())


async def _feed_label(db: AsyncSession, store, uid: int) -> Label:
    found = (await db.execute(select(Label).where(Label.user_id == uid, func.lower(Label.name) == FEED.lower(),
                                                  Label.role.is_(None)))).scalar_one_or_none()
    return found or await store.create_label(db, FEED)


async def decide(db: AsyncSession, p, entry: ScreenerSender, decision: str, *, label_id: int | None = None,
                 feed: bool = False, block_to: str = "spam", by: str = "user") -> dict[str, Any]:
    """Apply a decision to a sender: their held mail moves now (as the user's own action, so mirror
    accounts follow), and later mail follows the decision at ingest."""
    from mailr.core import actions as act
    from mailr.core.store import user_store

    if decision not in DECISIONS:
        raise ValueError("decision must be allow, block or label")
    if block_to not in ("spam", "trash"):
        raise ValueError("block_to must be spam or trash")
    user = await db.get(User, entry.user_id)
    store = await user_store(db, user)
    ctx = act.ActionContext(db, store, p)
    msgs = await _held_messages(db, p, entry.key)
    target_label: Label | None = None
    if decision == "label":
        if feed:
            target_label = await _feed_label(db, store, entry.user_id)
        elif label_id is not None:
            target_label = await db.get(Label, int(label_id))
        if target_label is None or target_label.user_id != entry.user_id or target_label.role:
            raise ValueError("choose one of your labels")
    if decision == "allow":
        await act.apply(ctx, msgs, "inbox", {})
    elif decision == "block" and block_to == "spam":
        # The existing block list: every address of the sender's held mail (and the card's).
        await act.apply(ctx, msgs, "spam", {"block_sender": True})
        from mailr.api.org_routes import set_blocked

        for email in {entry.email, *(m.from_email.lower() for m in msgs)} - {""}:
            await set_blocked(db, entry.user_id, email, True)
    elif decision == "block":
        await act.apply(ctx, msgs, "trash", {})
    else:
        await act.apply(ctx, msgs, "add_labels", {"label_ids": [target_label.id]})
    ids = [m.id for m in msgs]
    if ids:
        await db.execute(update(Message).where(Message.id.in_(ids)).values(screened_key=None))
    entry.status = STATUS_FOR[decision]
    entry.label_id = target_label.id if target_label is not None else None
    entry.block_to = block_to if decision == "block" else "spam"
    entry.decided_by = by[:60]
    entry.decided_at = datetime.now(UTC)
    await db.flush()
    return {"id": entry.id, "key": entry.key, "status": entry.status, "affected": len(ids),
            "label_id": entry.label_id, "label": target_label.name if target_label is not None else None}


async def forget(db: AsyncSession, p, entry: ScreenerSender) -> dict[str, Any]:
    """Undo a decision: the sender is screened again, so their next message waits in the Screener.
    A Spam block made here is lifted (the address comes off the block list). Mail already moved
    stays where it is."""
    if entry.status == "blocked" and entry.block_to != "trash":
        from mailr.api.org_routes import set_blocked

        await set_blocked(db, entry.user_id, entry.email, False)
    entry.status, entry.label_id, entry.block_to = "pending", None, "spam"
    entry.decided_by, entry.decided_at = "", None
    await db.flush()
    return {"id": entry.id, "key": entry.key, "status": entry.status}


RELEASE_CHUNK = 500


async def release_all(db: AsyncSession, user: User) -> int:
    """The screener was turned off: held mail goes back to the Inbox; pending senders are forgotten
    (no decision is recorded). Returns how many messages moved."""
    from mailr.assistant.orders import principal_for
    from mailr.core import actions as act
    from mailr.core.store import user_store

    p = principal_for(user)
    conds = held_conditions(user.id, await _role_ids(db, user.id))
    ctx = act.ActionContext(db, await user_store(db, user), p)
    moved, after = 0, 0
    while True:  # RELEASE_CHUNK at a time, without the bodies and vectors
        msgs = list((await db.execute(select(Message).options(*light_message()).where(*conds, Message.id > after)
                                      .order_by(Message.id).limit(RELEASE_CHUNK))).scalars())
        if not msgs:
            break
        after = msgs[-1].id
        await act.apply(ctx, msgs, "inbox", {})
        moved += len(msgs)
    await db.execute(update(Message).where(Message.user_id == user.id, Message.screened_key.is_not(None))
                     .values(screened_key=None))
    await db.execute(ScreenerSender.__table__.delete().where(
        and_(ScreenerSender.user_id == user.id, ScreenerSender.status == "pending")))
    return moved


@handler("screener.decide")
async def decide_job(payload: dict[str, Any], ctx) -> None:
    """A decision made off the request (mail you sent to a pending sender allows them)."""
    from mailr.assistant.orders import principal_for
    from mailr.db import session_scope

    async with session_scope() as db:
        e = await db.get(ScreenerSender, int(payload["sender_id"]))
        if e is None or e.status != "pending":
            return
        user = await db.get(User, e.user_id)
        await decide(db, principal_for(user), e, str(payload.get("decision") or "allow"), by=str(payload.get("by") or "user"))
        await db.commit()


@handler("screener.release_all")
async def release_job(payload: dict[str, Any], ctx) -> None:
    from mailr.db import session_scope

    async with session_scope() as db:
        user = await db.get(User, int(payload["user_id"]))
        if user is None or enabled(user):
            return
        n = await release_all(db, user)
        await db.commit()
        log.info("screener off for user %s: released %s messages", user.id, n)


# ---------------------------------------------------------------- the digest


async def digest_line(db: AsyncSession, user: User) -> str:
    """The routine digest's line: how many senders wait, with a link to review them."""
    if not enabled(user):
        return ""
    from mailr.assistant.orders import principal_for
    from mailr.config import get_settings

    n = (await counts(db, principal_for(user)))["total"]
    if not n:
        return ""
    from mailr import i18n

    base = get_settings().base_url.rstrip("/")
    return i18n.t("digest.screener", i18n.user_language(user), count=n, url=f"{base}/mail/screener")


# ---------------------------------------------------------------- agent approvals


def _register_executor() -> None:
    from mailr.core import permissions

    @permissions.executor("mail.screener")
    async def _execute(db: AsyncSession, user: User, payload: dict[str, Any]) -> dict[str, Any]:
        """A screener decision an agent asked for and the user approved."""
        e = await db.get(ScreenerSender, int(payload.get("sender_id") or 0))
        if e is None or e.user_id != user.id:
            raise RuntimeError("the sender is no longer in the Screener")
        sp = await permissions.requester(db, user, payload)
        res = await decide(db, sp, e, str(payload["decision"]), label_id=payload.get("label_id"),
                           feed=bool(payload.get("feed")), block_to=str(payload.get("block_to") or "spam"),
                           by=str(payload.get("by") or "agent"))
        await db.commit()
        return {**res, "title": f"Screener: {payload['decision']} {e.key}"}


_register_executor()
