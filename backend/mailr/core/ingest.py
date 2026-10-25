"""Single entry point for mail entering a user's store.

Connectors, imports, and sent mail all call `ingest_raw`. Order:
parse → dedupe → index row → thread → rules/blocked senders → mute/spam handling → sender
screener → Stalwart import with final labels → people → decisions → queue model work + notifications.
The index row and the Stalwart import commit together; if the import fails, nothing is kept.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import people, rules, threading
from mailr.core.jobs import enqueue
from mailr.core.mime import content_hash, norm_subject, parse, semantic_digest
from mailr.core.store import UserStore
from mailr.intel.features import header_map
from mailr.models import Connector, Conversation, Identity, Label, LabelDecision, Message, User

log = logging.getLogger("mailr.ingest")

# Where a message sits: a copy from another account never changes it (_merge_duplicate).
PLACEMENT = ("inbox", "archive", "trash", "junk")

_SPAM_HDR = re.compile(r"^\s*(yes|true)\b", re.IGNORECASE)
# X-Mailr-Origin values of mail another app put into mailr (the phone's "Send to self").
UNTRUSTED_ORIGINS = {"android-share"}


# ACT mail.send_to_self @api: a share from the phone is marked by its origin header.
def shared_origin(p) -> str | None:
    """The X-Mailr-Origin of mail another app shared into mailr, stored as Message.origin. It
    only lowers trust (Message.own_words), so a forged header from outside does no harm."""
    v = (p.headers.get("X-Mailr-Origin") or "").strip().lower()
    return v if v in UNTRUSTED_ORIGINS else None


# Mail the store holds that mailr indexes later (core/storesync.py) and that is older than this is
# triaged in batches, as an import is (queue_intel).
BACKFILL_AGE = timedelta(days=3)


@dataclass
class IngestOptions:
    connector: Connector | None = None
    # Roles ("inbox", "sent", "junk", "trash", "archive", "drafts") and/or label names.
    roles: list[str] = field(default_factory=lambda: ["inbox"])
    label_names: list[str] = field(default_factory=list)
    keywords: dict[str, bool] = field(default_factory=dict)  # seen/flagged/answered/draft/important
    received_at: datetime | None = None
    upstream: dict[str, Any] = field(default_factory=dict)
    source: str = "connector"  # connector | import | send | draft
    run_rules: bool = True
    run_intel: bool = True
    notify: bool = True
    # Mirror-mode connectors: a known message seen in another upstream folder gains that
    # folder's label (roles included), so upstream moves and copies show up in mailr.
    merge_labels: bool = False
    # The source records what the user did with the message (a Gmail history import): for a
    # message mailr already has, its inbox/archive/spam/trash state and read/star/important
    # flags replace mailr's, while its labels are added.
    state_authoritative: bool = False
    # With state_authoritative: the provider state also replaces mailr's for a message mailr
    # already has. Only the first import of an account sets it, and a message that mailr changed
    # (local_change_at) keeps its state.
    merge_provider_state: bool = False
    # Ids of the user's own labels the sender picked (a send's payload "label_ids", checked by
    # sendgate.own_label_ids). Added to a new message before the filters run, and to a copy
    # mailr already has, so a replay or a copy that arrived first ends the same way.
    label_ids: list[int] = field(default_factory=list)


@dataclass
class IngestResult:
    message: Message
    duplicate: bool = False


async def my_addresses(db: AsyncSession, user: User) -> set[str]:
    rows = (await db.execute(select(Identity.email, Identity.receives).where(Identity.user_id == user.id))).all()
    out = {user.email.lower()}
    for email, _receives in rows:
        out.add(email.lower())
    return out


async def _labels_by(db: AsyncSession, user_id: int) -> tuple[dict[str, Label], dict[str, Label]]:
    labels = list((await db.execute(select(Label).where(Label.user_id == user_id))).scalars())
    return ({lb.role: lb for lb in labels if lb.role}, {lb.name.lower(): lb for lb in labels})


def unsub_method_of(m: Message, p) -> str | None:
    """Message.unsub_method from the parsed headers (core/unsubscribe.method_of)."""
    from mailr.core.unsubscribe import method_of

    return method_of(m, p.list_unsubscribe, (p.headers or {}).get("List-Unsubscribe-Post"))


async def ingest_raw(db: AsyncSession, store: UserStore, raw: bytes, opts: IngestOptions) -> IngestResult:
    user = store.user
    uid = user.id
    # Off the event loop: HTML parsing of a hostile body must not stall sync, jobs and push.
    p = await asyncio.to_thread(parse, raw)
    msgid = p.message_id or f"<{hashlib.sha256(raw).hexdigest()[:40]}@mailr.synthetic>"
    chash = content_hash(raw)

    # ---- dedupe: the same message (same Message-ID AND same content) already stored ----
    # Serialise concurrent ingests of one Message-ID (two connectors receiving the same list
    # mail, overlapping workers) so they cannot both insert it.
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                     {"k": f"msg:{uid}:{msgid}"})
    existing, match = await _find_duplicate(db, store, uid, msgid, chash, p, raw, ref=opts.upstream)
    if existing is not None:
        await _merge_duplicate(db, store, existing, opts, chash, match)
        return IngestResult(existing, duplicate=True)

    mine = await my_addresses(db, user)
    sent_by_me = is_sent_by_me(p, mine, opts)
    by_role, by_name = await _labels_by(db, uid)
    if not by_role:
        await store.sync_labels(db)
        by_role, by_name = await _labels_by(db, uid)

    label_ids: set[int] = set()
    for r in opts.roles:
        if r in by_role:
            label_ids.add(by_role[r].id)
    for name in opts.label_names:
        lb = by_name.get(name.lower())
        if lb is None:
            lb = await store.create_label(db, name)
            by_name[name.lower()] = lb
        label_ids.add(lb.id)
    # Labels picked when sending ("Send to self"). The filters run after them, so a note to self
    # still gets its filter's labels (Later) as well; a filter that removes one has the last word.
    label_ids |= set(opts.label_ids)
    # Mail the user sent to one of their own addresses (a note to self) is also incoming mail:
    # it lands in the inbox and the user's filters apply to it (e.g. `from:me to:me`).
    to_me = {a["email"] for a in p.to + p.cc} & mine
    self_addressed = sent_by_me and bool(to_me)
    if sent_by_me and not opts.keywords.get("draft") and "sent" in by_role:
        label_ids.add(by_role["sent"].id)
        # Mail I sent stays out of my inbox unless I also addressed it to myself.
        if not to_me and "inbox" in by_role:
            label_ids.discard(by_role["inbox"].id)

    kw = {"seen": False, "flagged": False, "answered": False, "draft": False, "important": False}
    kw.update({k: bool(v) for k, v in opts.keywords.items() if k in kw})
    if sent_by_me:
        kw["seen"] = True

    upstream_spam = any(_SPAM_HDR.match(p.headers.get(h, "")) for h in ("X-Spam-Status",)) or \
        p.headers.get("X-Spam-Flag", "").lower().startswith("yes")

    m = Message(
        user_id=uid, connector_id=opts.connector.id if opts.connector else None,
        local_only=bool(opts.connector and opts.connector.data_class == "local_only"),
        content_hash=chash,
        upstream={**opts.upstream, "hash": chash} if opts.upstream else {}, message_id_hdr=msgid,
        in_reply_to=p.in_reply_to,
        references=p.references, subject=p.subject, norm_subject=norm_subject(p.subject),
        from_email=p.from_email, from_name=p.from_name, to=p.to, cc=p.cc, bcc=p.bcc,
        reply_to=p.reply_to, delivered_to=p.delivered_to, list_id=p.list_id,
        list_unsubscribe=p.list_unsubscribe, date=p.date or opts.received_at or datetime.now(UTC),
        received_at=opts.received_at or datetime.now(UTC), size=p.size, snippet=p.snippet,
        body_text=p.body_text, attached_text=p.attached_text or None, lang=p.lang, lang_confidence=p.lang_confidence,
        has_attachments=bool([a for a in p.attachments if not a.get("inline")]),
        attachments=p.attachments, auth_results=p.auth_results, sent_by_me=sent_by_me,
        origin=shared_origin(p), label_ids=sorted(label_ids),
        intel_state=("backfill" if opts.source == "import" else "pending") if opts.run_intel else "skipped", **kw,
    )
    m.unsub_method = unsub_method_of(m, p)
    db.add(m)
    await db.flush()

    conv_id = await threading.assign(db, m, refresh_now=False, headers=header_map(raw))
    conv = await db.get(Conversation, conv_id)

    plan: dict[str, Any] = {}
    if opts.run_rules and (not sent_by_me or self_addressed) and opts.source in ("connector", "import", "send"):
        plan = await rules.plan_for_message(db, m)
        label_ids |= set(plan.get("add_labels", ()))
        label_ids -= set(plan.get("remove_labels", ()))
        if plan.get("archive") and "inbox" in by_role:
            label_ids.discard(by_role["inbox"].id)
        if plan.get("mark_read"):
            kw["seen"] = True
        if plan.get("star"):
            kw["flagged"] = True
        if "important" in plan:
            kw["important"] = plan["important"]
        if plan.get("category"):
            m.category = plan["category"]
        if plan.get("trash") and "trash" in by_role:
            label_ids = {by_role["trash"].id}
        if plan.get("_rules"):
            m.local_change_at = datetime.now(UTC)

    inbound = not sent_by_me and opts.source == "connector"
    spam_reason = ""
    if inbound and not plan.get("never_spam") and "junk" in by_role and not plan.get("spam") and not upstream_spam:
        from mailr.core import spamrules
        from mailr.intel import spam as spamintel

        brand = spamrules.claimed_brand(p.from_name, p.from_email)
        score = spamrules.upstream_score(p.headers)
        limit = spamrules.upstream_unknown_threshold(user.settings or {})
        if brand and not await spamrules.known_sender(db, uid, p.from_email):
            spam_reason = f"claims to be {brand.title()} but was sent from {p.from_email.rsplit('@', 1)[-1]}"
        elif score is not None and limit is not None and score >= limit \
                and not await spamrules.known_sender(db, uid, p.from_email):
            spam_reason = f"provider spam score {score:.1f} (at least {limit:g} for new senders)"
        elif (learned := await spamintel.learned_reason(db, user, p.from_email)) \
                and not await spamrules.known_sender(db, uid, p.from_email):
            spam_reason = learned  # spam sender domains (intel/spam.py)
        elif conv is not None and m.in_reply_to:
            others = (await db.execute(select(Message.label_ids).where(
                Message.conversation_id == conv.id, Message.id != m.id, Message.deleted.is_(False)))).scalars().all()
            junk_id = by_role["junk"].id
            if others and all(junk_id in (x or []) for x in others):
                spam_reason = "reply to a conversation that is in Spam"
    if (plan.get("spam") or spam_reason or (upstream_spam and not plan.get("never_spam"))) and "junk" in by_role:
        label_ids = {by_role["junk"].id}
    if conv and conv.muted and inbound and "inbox" in by_role:
        label_ids.discard(by_role["inbox"].id)
    if conv and conv.snoozed_until and inbound and "inbox" in by_role and by_role["inbox"].id in label_ids:
        conv.snoozed_until = None  # new mail wakes a snoozed conversation
    if conv and (conv.follow_up_at or conv.nudged_at):
        # mail.follow_up: a reply after my message ends the reminder and the "No reply" mark;
        # my own follow-up ends the mark.
        when = p.date or datetime.now(UTC)
        if not sent_by_me and not kw.get("draft") and (conv.follow_up_since is None or when > conv.follow_up_since):
            conv.follow_up_at = conv.follow_up_since = conv.nudged_at = None
        elif sent_by_me and not kw.get("draft"):
            conv.nudged_at = None

    if inbound:
        # The sender screener (core/screener.py), after filters, blocked senders and spam: mail on
        # its way to the Inbox from a first-time sender waits in the Screener instead, and a
        # decision about the sender (block, a label) applies.
        from mailr.core import screener

        label_ids, _ = await screener.route(db, user, m, conv, opts.connector, label_ids, by_role)
    if not label_ids and "archive" in by_role:
        label_ids = {by_role["archive"].id}
    for k, v in kw.items():
        setattr(m, k, v)
    m.label_ids = sorted(label_ids)

    stored = await store.import_message(db, raw, sorted(label_ids),
                                        {KEY: True for KEY, on in (
                                            ("$seen", kw["seen"]), ("$flagged", kw["flagged"]),
                                            ("$answered", kw["answered"]), ("$draft", kw["draft"]),
                                            ("$important", kw["important"])) if on},
                                        m.received_at)
    m.jmap_id, m.jmap_thread_id, m.blob_id = stored["jmap_id"], stored["thread_id"], stored["blob_id"]

    if spam_reason:
        db.add(LabelDecision(user_id=uid, message_id=m.id, kind="spam", target="spam", value=True,
                             source="rule", source_ref="builtin", confidence=0.95, reason=spam_reason))
    if plan:
        for d in rules.decisions_for_plan(m, plan):
            db.add(d)
        # Forward only live mail (not history imports), never local-only mail; the handler
        # sends through the send gate to verified forwarding addresses only.
        # Never mail filed as Spam or Trash (provider flag, blocked sender, spam rules): the
        # user's domain would relay it. rules.forward checks again when it runs.
        binned = any(r in by_role and by_role[r].id in label_ids for r in ("junk", "trash"))
        if plan.get("forward_to") and opts.source == "connector" and not m.local_only and not binned:
            for addr in plan["forward_to"]:
                await enqueue(db, "rules.forward", {"message_id": m.id, "to": addr}, user_id=uid,
                              dedupe_key=f"fwd:{m.id}:{addr}")

    junk = "junk" in by_role and by_role["junk"].id in label_ids
    if inbound and not junk and any(a.get("type") in ("text/calendar", "application/ics")
                                    or (a.get("filename") or "").lower().endswith(".ics")
                                    # an invitation in an attached email: applied only for a forward
                                    # from a trusted address (calendar/forwarded.py)
                                    or (a.get("email") or {}).get("invite") for a in m.attachments or []):
        # An invitation, reply or cancellation: calendar/inbound.py applies it when it is safe.
        has_cal = (await db.execute(select(Connector.id).where(
            Connector.user_id == uid, Connector.kind == "caldav", Connector.enabled.is_(True)).limit(1))).first()
        if has_cal:
            await enqueue(db, "calendar.itip_incoming", {"message_id": m.id}, user_id=uid,
                          dedupe_key=f"itip:{m.id}")

    if inbound and not junk:
        # A Speakr notification links its conversation to the recording (integrations/speakr/mail.py).
        from mailr.integrations.speakr.mail import maybe_enqueue as speakr_mail

        await speakr_mail(db, m)

    await people.record(db, m)
    if sent_by_me and not kw["draft"] and opts.source in ("connector", "send"):
        from mailr.core import screener

        await screener.allow_recipients(db, user, m)  # writing to a pending sender allows them
    await threading.refresh(db, uid, [conv_id])
    if opts.run_intel and not m.draft:
        await queue_intel(db, m)
    if inbound and not junk and opts.source == "connector":
        # Remote images are fetched now, through the image proxy, so opening the message later
        # tells the sender nothing (core/imageproxy.py). History imports are fetched when opened.
        from mailr.core.imageproxy import maybe_enqueue_prefetch

        await maybe_enqueue_prefetch(db, user, m)
    in_inbox = "inbox" in by_role and by_role["inbox"].id in label_ids
    if opts.notify and inbound and in_inbox and not kw["seen"]:
        await enqueue(db, "push.new_mail", {"message_id": m.id}, user_id=uid,
                      run_at=datetime.now(UTC))
    return IngestResult(m)


async def queue_intel(db: AsyncSession, m: Message) -> None:
    """Triage for a new message: live mail gets a job of its own in the main lane, at once; a
    message marked "backfill" (a history import, old mail indexed from the store) waits for the
    user's intel.backfill job, which takes such mail 128 at a time with one embedding call, in a
    lane of its own. An import of 500,000 messages used to queue 500,000 jobs ahead of reminders,
    snooze wake-ups and sends."""
    if m.intel_state == "backfill":
        await enqueue(db, "intel.backfill", {"user_id": m.user_id}, user_id=m.user_id,
                      dedupe_key=f"intel.backfill:{m.user_id}")
    else:
        await enqueue(db, "intel.message", {"message_id": m.id}, user_id=m.user_id,
                      dedupe_key=f"intel.message:{m.id}")


def is_sent_by_me(p, mine: set[str], opts: IngestOptions) -> bool:
    """Mail counts as sent by the user only when mailr sent it, it came from a Sent folder,
    or it is From one of the user's addresses AND the receiving provider's (top-most)
    authentication results pass. A forged "From: me" is inbound mail.
    A share from the phone ("Send to self", X-Mailr-Origin) is filed as the user's mail like any
    note to self (Sent, `from:me`, filters); its text is not theirs, which Message.origin records
    and Message.own_words checks."""
    if opts.source == "send":
        return True
    if p.from_email not in mine:
        return False
    if "sent" in opts.roles:
        return True
    # DMARC pass for the From domain, or DKIM from it (or a parent): anyone can DKIM-sign with
    # their own domain while putting the user's address in From (core/authres.verified_for).
    from mailr.core import authres

    return authres.verified_for(p.auth_results, p.from_email.rsplit("@", 1)[-1])


def _norm_text(t: str) -> str:
    return " ".join((t or "").split()).lower()


def _emails(addrs: Any) -> list[str]:
    return sorted({str(a.get("email", "")).lower() for a in addrs or [] if isinstance(a, dict)})


def _same_people(e: Message, p) -> bool:
    """content_hash covers From but not Reply-To, To or Cc: a copy that differs in any of them
    (a lookalike that reuses the Message-ID with the attacker's Reply-To) is another message."""
    return all(_emails(getattr(e, f)) == _emails(getattr(p, f)) for f in ("reply_to", "to", "cc"))


async def _find_duplicate(db: AsyncSession, store: UserStore, uid: int, msgid: str, chash: str, p,
                          raw: bytes, ref: dict[str, Any] | None = None) -> tuple[Message | None, str]:
    """The stored message this one duplicates, and how it matched: "ref" (this very upstream copy,
    `ref`, is already recorded on it), "exact" (same content hash and the same people) or
    "semantic" (the same message re-encoded in transit, checked against the stored bytes). Same
    subject, sender and text are not enough (Oct 2026): a lookalike with another attachment or
    Reply-To would absorb the real message."""
    rows = (await db.execute(
        select(Message).where(Message.user_id == uid, Message.message_id_hdr == msgid,
                              Message.deleted.is_(False)).order_by(Message.id)
    )).scalars().all()
    if ref and ref.get("uid") is not None:
        # A copy mailr put at the provider itself (a sent message saved in the provider's Sent,
        # connectors/sentcopy.py) is linked when it is made; a sync that fetched it meanwhile
        # finds it here, whatever the provider did to its bytes.
        key = _ref_key(ref)
        for e in rows:
            up = e.upstream or {}
            if key in {_ref_key(r) for r in [up, *(up.get("copies") or [])] if r.get("uid") is not None}:
                return e, "ref"
    digest: str | None = None
    for e in rows:
        if not _same_people(e, p):
            continue
        if e.content_hash is None and e.blob_id:
            try:
                e.content_hash = content_hash(await store.get_raw(e))
            except Exception:  # noqa: BLE001, S110
                pass
        if e.content_hash == chash:
            return e, "exact"
        # Same text re-encoded in transit (e.g. quoted-printable vs 8bit): proven on the bytes.
        if (e.subject == p.subject and e.from_email == p.from_email
                and _norm_text(e.body_text) == _norm_text(p.body_text) and e.body_text and e.blob_id):
            if digest is None:
                digest = semantic_digest(raw) or ""
            if not digest:
                continue
            try:
                theirs = semantic_digest(await store.get_raw(e))
            except Exception:  # noqa: BLE001  cannot prove it: keep both
                continue
            if theirs == digest:
                return e, "semantic"
    return None, ""


def _ref_key(r: dict[str, Any]) -> tuple[str, str, str, str]:
    return tuple(str(r.get(k)) for k in ("connector", "folder", "uidvalidity", "uid"))  # type: ignore[return-value]


async def _merge_duplicate(db: AsyncSession, store: UserStore, m: Message, opts: IngestOptions,
                           chash: str | None = None, match: str = "exact") -> None:
    """The same message arrived again (another address, a folder copy, a Gmail import of a
    message we already have). Union the labels an import brings; record the upstream copy.

    mailr's placement of the message stands. A copy from another account never
    adds Inbox, Archive, Trash or Spam: a provider that also forwards to Gmail leaves a Gmail copy that
    must not bring a deleted, archived or filtered message back to the Inbox. A copy in another
    folder of the message's own mirror account does add its folder's role (an upstream move),
    except Inbox after mailr changed the message, and nothing while it is in Trash or Spam."""
    by_role, by_name = await _labels_by(db, m.user_id)
    current = set(m.label_ids or [])
    same = opts.connector is not None and m.connector_id == opts.connector.id
    binned = any(r in by_role and by_role[r].id in current for r in ("trash", "junk"))
    add: set[int] = set()
    if opts.source == "import" or opts.merge_labels:
        for name in opts.label_names:
            lb = by_name.get(name.lower()) or await store.create_label(db, name)
            add.add(lb.id)
        for r in opts.roles:
            if r not in by_role:
                continue
            if r in PLACEMENT and not (same and opts.merge_labels and not binned
                                       and not (r == "inbox" and m.local_change_at is not None)):
                continue
            add.add(by_role[r].id)
    # Labels picked when sending: the copy that arrived first (the inbox copy of a note to self)
    # gets them too. Adding a label it has already changes nothing.
    add |= {i for i in opts.label_ids if i in {lb.id for lb in by_name.values()}}
    if opts.state_authoritative and opts.merge_provider_state and m.local_change_at is None:
        await apply_provider_state(db, store, m, roles=opts.roles, label_names=opts.label_names,
                                   keywords=opts.keywords)
    else:
        new = add - current
        if new:
            await store.set_labels([m], add=sorted(new), remove=[], db=db)
    if opts.upstream and (m.upstream or {}).get("orphan") and opts.connector is not None \
            and str(m.upstream["orphan"].get("connector")) == str(opts.connector.id):
        # Found again in another folder of the same account: it was moved, not deleted.
        m.upstream = {k: v for k, v in m.upstream.items() if k != "orphan"}
    if opts.connector is not None and opts.connector.kind != "file":
        # A row the store sync indexed without its account (after a database restore) gets it from
        # the account that fetched the message again: the data class, and the link that store mode
        # retention and the account's own folders need. A row is never made less private.
        if opts.connector.data_class == "local_only" and not m.local_only:
            m.local_only = True
        if m.connector_id is None:
            m.connector_id = opts.connector.id
            if opts.upstream and not (m.upstream or {}).get("uid"):
                m.upstream = {**opts.upstream, "hash": chash or m.content_hash, "hv": 2,
                              **({"match": "semantic"} if match == "semantic" else {})}
    if opts.upstream:
        # The copy keeps the hash of its OWN bytes (hv 2), so retention deletes it upstream only
        # when it is this message: same hash, or proven the same re-encoded ("semantic"). Before
        # Oct 2026 a copy carried the stored message's hash whatever it held.
        ref = {**opts.upstream, "hash": chash or m.content_hash, "hv": 2}
        if match == "semantic":
            ref["match"] = "semantic"
        primary = m.upstream or {}
        if _ref_key(ref) != _ref_key(primary):
            copies = [c for c in primary.get("copies", []) if _ref_key(c) != _ref_key(ref)]
            copies.append(ref)
            m.upstream = {**primary, "copies": copies[-20:]}
    if opts.keywords.get("flagged") and not m.flagged and not opts.state_authoritative:
        await store.set_keywords([m], flagged=True)
    await _sent_copy_seen(db, store, m, opts, by_role)
    if (m.sent_by_me and m.seen and opts.upstream and not opts.keywords.get("seen")
            and opts.connector is not None and opts.connector.mode == "mirror" and opts.source == "connector"):
        # The delivered copy of mail the user sent to themselves arrives unread; mailr has it as
        # read (their own mail). The copy is marked read at the provider, and until that is done
        # the copy's flag is not applied over mailr's (runner._pending_write_backs). Before 23 Oct
        # 2026 a reply to oneself turned unread in mailr when its delivered copy was synced.
        from mailr.core.actions import WRITE_BACK_ATTEMPTS

        await enqueue(db, "connector.write_back", {"connector_id": opts.connector.id, "message_ids": [m.id],
                                                   "change": {"seen": True}},
                      user_id=m.user_id, max_attempts=WRITE_BACK_ATTEMPTS)
    if "junk" in by_role and by_role["junk"].id in (m.label_ids or []) and by_role["junk"].id not in current:
        # Moved to Spam upstream: labels never apply to spam (intel/classify.py withdraw_labels).
        from mailr.intel.classify import withdraw_labels

        await withdraw_labels(db, store, [m])
    if m.conversation_id:
        await threading.refresh(db, m.user_id, [m.conversation_id])


async def _sent_copy_seen(db: AsyncSession, store: UserStore, m: Message, opts: IngestOptions,
                          by_role: dict[str, Label]) -> None:
    """A copy of this message is in the user's own Sent folder: they sent it. The first copy may
    have arrived through the inbox without a passing authentication result (mail between the
    user's own addresses), so it was stored as inbound. Now it is theirs, and a note to self
    gets the user's filters (`from:me to:me`), as it would have on arrival. (30 Sep 2026: a
    self-sent screenshot stayed in the inbox instead of going to Later.) mailr's own sent copy
    (source "send") counts too: a "Send to self" whose inbox copy arrived first goes to Later."""
    if m.sent_by_me or "sent" not in opts.roles or opts.source not in ("connector", "import", "send"):
        return
    mine = await my_addresses(db, store.user)
    if (m.from_email or "").lower() not in mine:
        return
    m.sent_by_me = True
    rcpt = {str(a.get("email", "")).lower() for a in list(m.to or []) + list(m.cc or [])}
    if not (rcpt & mine) or not opts.run_rules:
        return
    await apply_rules_to_stored(db, store, m, by_role)


async def apply_rules_to_stored(db: AsyncSession, store: UserStore, m: Message,
                                by_role: dict[str, Label] | None = None) -> dict[str, Any]:
    """Run the user's filters on a message already in the store, as ingest would have on arrival:
    labels, archive, trash, read, star and importance, with the decisions recorded. Category and
    forwarding are left alone (a later pass must not forward old mail). Returns the plan."""
    if by_role is None:
        by_role, _ = await _labels_by(db, m.user_id)
    plan = await rules.plan_for_message(db, m)
    if plan.get("_rules"):
        # The labels a filter applies are recorded, so a mirror account's label state keeps them.
        for d in rules.decisions_for_plan(m, plan):
            db.add(d)
        m.local_change_at = datetime.now(UTC)
    add, remove = list(plan.get("add_labels", ())), list(plan.get("remove_labels", ()))
    if plan.get("archive") and "inbox" in by_role:
        remove.append(by_role["inbox"].id)
    if plan.get("trash") and "trash" in by_role:
        await store.replace_labels([m], [by_role["trash"].id], db)
    elif add or remove:
        await store.set_labels([m], add=add, remove=remove, db=db)
    flags: dict[str, bool] = {}
    if plan.get("mark_read"):
        flags["seen"] = True
    if plan.get("star"):
        flags["flagged"] = True
    if "important" in plan:
        flags["important"] = bool(plan["important"])
    if flags:
        await store.set_keywords([m], **flags)
    return plan


async def apply_provider_state(
    db: AsyncSession, store: UserStore, m: Message, *, roles: list[str], label_names: list[str],
    keywords: dict[str, bool], category: str | None = None, provider: str | None = None,
) -> bool:
    """Make a message match what the user did with it at a provider whose history is
    authoritative (a Gmail import): inbox/archive/spam/trash placement, read/star/important
    flags, the provider's category, plus its labels (added, never removed). With `provider`,
    its importance and category are also recorded as "provider" decisions, which mailr's
    models neither override nor ignore (they train on them). Returns True if anything changed."""
    by_role, by_name = await _labels_by(db, m.user_id)
    rset = set(roles)
    add: set[int] = set()
    remove: set[int] = set()
    for name in label_names:
        lb = by_name.get(name.lower()) or await store.create_label(db, name)
        by_name[name.lower()] = lb
        add.add(lb.id)
    if rset & {"trash", "junk"} and ("trash" if "trash" in rset else "junk") in by_role:
        target = by_role["trash" if "trash" in rset else "junk"].id
        add = {target}
        remove = set(m.label_ids or []) - {target}
    else:
        for r in ("trash", "junk"):
            if r in by_role:
                remove.add(by_role[r].id)
        if "inbox" in by_role:
            (add if "inbox" in rset else remove).add(by_role["inbox"].id)
        if "archive" in by_role and "inbox" in rset:
            remove.add(by_role["archive"].id)
        if "sent" in rset and "sent" in by_role:
            add.add(by_role["sent"].id)
    remove -= add
    current = set(m.label_ids or [])
    changed = False
    if (add - current) or (remove & current):
        await store.set_labels([m], add=sorted(add - current), remove=sorted(remove & current), db=db)
        changed = True
        if "junk" in by_role and by_role["junk"].id in (m.label_ids or []) and by_role["junk"].id not in current:
            from mailr.intel.classify import withdraw_labels

            await withdraw_labels(db, store, [m])  # labels never apply to spam
    flags = {k: bool(keywords.get(k, False)) for k in ("seen", "flagged", "important")}
    if m.sent_by_me:
        flags["seen"] = True
    if any(getattr(m, k) != v for k, v in flags.items()):
        await store.set_keywords([m], **flags)
        changed = True
    if category is not None:
        cat = None if category == "primary" else category
        if m.category != cat:
            m.category = cat
            changed = True
    if provider and not m.sent_by_me:
        await db.execute(delete(LabelDecision).where(LabelDecision.message_id == m.id,
                                                     LabelDecision.source == "provider"))
        db.add(LabelDecision(user_id=m.user_id, message_id=m.id, kind="importance", target="important",
                             value=bool(m.important), source="provider", source_ref=provider,
                             confidence=1.0, status="accepted",
                             reason=f"marked {'important' if m.important else 'not important'} in {provider}"))
        if category is not None:
            db.add(LabelDecision(user_id=m.user_id, message_id=m.id, kind="category", target=category,
                                 value=True, source="provider", source_ref=provider, confidence=1.0,
                                 status="accepted", reason=f"{category} in {provider}"))
    if changed and m.conversation_id:
        await db.flush()
        await threading.refresh(db, m.user_id, [m.conversation_id])
    return changed


async def _stored_twin(db: AsyncSession, store: UserStore, uid: int, msgid: str, chash: str, p,
                       received: datetime | None, jmap_id: str) -> Message | None:
    """The indexed message a store message duplicates: same Message-ID, content hash, people and
    arrival time, with a store copy of its own that still exists. Mailr never stores one message
    twice (ingest dedupes), so such a pair comes from an import that was repeated."""
    if received is None:
        return None
    rows = (await db.execute(select(Message).where(
        Message.user_id == uid, Message.message_id_hdr == msgid, Message.content_hash == chash,
        Message.deleted.is_(False), Message.draft.is_(False), Message.jmap_id.is_not(None),
        Message.jmap_id != jmap_id))).scalars().all()
    for e in rows:
        if e.received_at is None or abs((e.received_at - received).total_seconds()) > 1 or not _same_people(e, p):
            continue
        if e.jmap_id and await store.get_emails([e.jmap_id]):
            return e
    return None


async def _source_account(db: AsyncSession, uid: int, p, sent_by_me: bool) -> tuple[int | None, bool]:
    """(connector id, local-only) of the mail account a message in the store came through, read
    from its addresses: the sender for mail the user sent, else Delivered-To, To and Cc against each
    account's addresses (core/mailaccounts.py). The id only when exactly one account matches;
    local-only when any matching account is."""
    from mailr.core.mailaccounts import account_addresses

    accounts = await account_addresses(db, uid)
    if not accounts:
        return None, False
    if sent_by_me:
        mine = {(p.from_email or "").lower()}
    else:
        mine = {a.lower() for a in p.delivered_to or []} | {
            str(a.get("email", "")).lower() for a in list(p.to or []) + list(p.cc or []) if isinstance(a, dict)}
    hits = [cid for cid, addrs in accounts.items() if mine & set(addrs)]
    if not hits:
        return None, False
    local = bool((await db.execute(select(Connector.id).where(
        Connector.id.in_(hits), Connector.data_class == "local_only").limit(1))).first())
    return (hits[0] if len(hits) == 1 else None), local


async def index_stored(db: AsyncSession, store: UserStore, email: dict[str, Any], raw: bytes) -> Message | None:
    """Index a message that already exists in Stalwart (added by an IMAP client, e.g. a phone
    saving to Sent or moving mail in). No store import, no rules: the client already decided."""
    user = store.user
    if (await db.execute(select(Message.id).where(Message.user_id == user.id,
                                                   Message.jmap_id == email["id"]))).first():
        return None
    p = await asyncio.to_thread(parse, raw)  # off the event loop
    # The lock ingest_raw holds for this Message-ID until it commits: a message mailr is still
    # importing (its transaction open longer than storesync's grace period) is waited for and then
    # found, instead of indexed a second time under the same store id (the duplicate key on
    # messages_user_id_jmap_id_key in production, 10 Oct 2026).
    msgid_key = p.message_id or f"<{hashlib.sha256(raw).hexdigest()[:40]}@mailr.synthetic>"
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                     {"k": f"msg:{user.id}:{msgid_key}"})
    if (await db.execute(select(Message.id).where(Message.user_id == user.id,
                                                   Message.jmap_id == email["id"]))).first():
        return None
    jmap_to_label = {lb.jmap_id: lb.id for lb in (await db.execute(
        select(Label).where(Label.user_id == user.id))).scalars()}
    if any(j not in jmap_to_label for j in (email.get("mailboxIds") or {})):
        await store.sync_labels(db)
        jmap_to_label = {lb.jmap_id: lb.id for lb in (await db.execute(
            select(Label).where(Label.user_id == user.id))).scalars()}
    kws = email.get("keywords") or {}
    mine = await my_addresses(db, user)
    by_role, _ = await _labels_by(db, user.id)
    label_ids = sorted(jmap_to_label[j] for j in (email.get("mailboxIds") or {}) if j in jmap_to_label)
    in_sent = "sent" in by_role and by_role["sent"].id in label_ids
    sent_by_me = is_sent_by_me(p, mine, IngestOptions(roles=["sent"] if in_sent else [], source="store"))
    received = None
    if email.get("receivedAt"):
        received = datetime.fromisoformat(email["receivedAt"].replace("Z", "+00:00"))
    if not kws.get("$draft"):
        twin = await _stored_twin(db, store, user.id, msgid_key, content_hash(raw), p, received, email["id"])
        if twin is not None:
            # A second store copy of a message the index has: an import whose transaction rolled
            # back after the store took it (a database blip, a worker stopped mid-ingest), then
            # imported again. The store does not refuse such a copy. Its folders join the indexed
            # copy and the duplicate goes, so the mailbox shows the message once.
            add = [i for i in label_ids if i not in (twin.label_ids or [])]
            if add:
                await store.set_labels([twin], add=add, remove=[], db=db)
            await store.destroy([Message(jmap_id=email["id"])])
            log.info("user %s: store message %s duplicates message %s; removed", user.id, email["id"], twin.id)
            return None
    m = Message(
        user_id=user.id, jmap_id=email["id"], jmap_thread_id=email.get("threadId"), blob_id=email.get("blobId"),
        message_id_hdr=p.message_id or f"<{hashlib.sha256(raw).hexdigest()[:40]}@mailr.synthetic>",
        in_reply_to=p.in_reply_to, references=p.references, subject=p.subject,
        norm_subject=norm_subject(p.subject), from_email=p.from_email, from_name=p.from_name, to=p.to,
        cc=p.cc, bcc=p.bcc, reply_to=p.reply_to, delivered_to=p.delivered_to, list_id=p.list_id,
        list_unsubscribe=p.list_unsubscribe, date=p.date or received, received_at=received or datetime.now(UTC),
        size=p.size, snippet=p.snippet, body_text=p.body_text, attached_text=p.attached_text or None,
        lang=p.lang, lang_confidence=p.lang_confidence,
        has_attachments=bool([a for a in p.attachments if not a.get("inline")]), attachments=p.attachments,
        auth_results=p.auth_results, sent_by_me=sent_by_me, origin=shared_origin(p), content_hash=content_hash(raw),
        seen=bool(kws.get("$seen")), flagged=bool(kws.get("$flagged")), answered=bool(kws.get("$answered")),
        draft=bool(kws.get("$draft")), important=bool(kws.get("$important")),
        label_ids=label_ids, intel_state="pending",
    )
    if (m.received_at or m.date or datetime.now(UTC)) < datetime.now(UTC) - BACKFILL_AGE:
        m.intel_state = "backfill"  # old mail (a store rescan after a restore): batched, as an import
    m.unsub_method = unsub_method_of(m, p)
    try:
        async with db.begin_nested():  # ON CONFLICT (user_id, jmap_id) DO NOTHING, for writers without the lock
            db.add(m)
            await db.flush()
    except IntegrityError as e:
        if "jmap_id" not in str(e.orig):
            raise
        return None
    conv_id = await threading.assign(db, m, headers=header_map(raw))
    # A reply saved by a phone inherits the data class of its conversation, so quoting local-only
    # mail never makes that text visible to hosted agents or models. Mail that arrived through an
    # account and is indexed from the store (after a database restore, the store sync adds
    # everything since the dump) takes that account and its data class from its addresses.
    cid, account_local = await _source_account(db, user.id, p, sent_by_me)
    m.connector_id = cid
    m.local_only = account_local or bool((await db.execute(select(Message.id).where(
        Message.conversation_id == conv_id, Message.local_only.is_(True), Message.id != m.id).limit(1))).first())
    await people.record(db, m)
    if not m.draft:
        await queue_intel(db, m)
    return m
