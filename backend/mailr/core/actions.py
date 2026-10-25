"""User actions on conversations and messages.

Each action updates Stalwart (labels = mailboxes, flags = keywords) and the index together,
records training signals (user corrections become LabelDecision rows with source='user'),
queues write-back for mirror-mode connectors, and returns an `undo` descriptor.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import threading
from mailr.core.jobs import enqueue
from mailr.core.store import UserStore
from mailr.models import Connector, Conversation, Label, LabelDecision, Message, light_message

REVERSIBLE = {
    "archive": "inbox", "inbox": "archive", "read": "unread", "unread": "read", "star": "unstar",
    "unstar": "star", "important": "not_important", "not_important": "important",
    "mute": "unmute", "unmute": "mute", "unsnooze": None, "follow_up": "cancel_follow_up",
}
SCOPE_FOR = {"delete_forever": "delete"}
# Permission class of each action.
_CLASS = {"trash": "mail.trash", "untrash": "mail.trash", "spam": "mail.spam", "not_spam": "mail.spam",
          "delete_forever": "mail.delete"}
_VERB = {"archive": "Archive", "inbox": "Move to Inbox", "trash": "Move to Trash", "untrash": "Restore from Trash",
         "spam": "Report spam", "not_spam": "Not spam", "delete_forever": "Delete forever", "read": "Mark read",
         "unread": "Mark unread", "star": "Star", "unstar": "Unstar", "important": "Mark important",
         "not_important": "Mark not important", "add_labels": "Add labels", "remove_labels": "Remove labels",
         "move": "Move", "snooze": "Snooze", "unsnooze": "Unsnooze", "mute": "Mute", "unmute": "Unmute",
         "category": "Change category", "restore_labels": "Undo", "follow_up": "Remind me if no reply",
         "cancel_follow_up": "Cancel the follow-up reminder", "restore_importance": "Undo",
         "restore_category": "Undo", "show_in_tab": "Show in tab", "restore_tab": "Undo"}


def action_class(action: str) -> str:
    """The class by the action's name alone. Agents are classified with classify(), which also
    looks at the labels an action would add or restore."""
    return _CLASS.get(action, "mail.label")


def _label_args(args: dict[str, Any]) -> list[int]:
    return [int(x) for x in (args.get("label_ids") or ([args["label_id"]] if args.get("label_id") else []))]


async def classify(db: AsyncSession, uid: int, msgs: list[Message], action: str, args: dict[str, Any]) -> str:
    """Permission class by effect: moving a conversation into Trash or Junk (move, add_labels,
    an Undo that restores labels, unmute with labels) is mail.trash / mail.spam, whatever the
    action is called."""
    if action in _CLASS:
        return _CLASS[action]
    roles = dict((await db.execute(select(Label.id, Label.role).where(
        Label.user_id == uid, Label.role.in_(("trash", "junk"))))).all())
    touched: set[int] = set()
    if action in ("add_labels", "move"):
        touched = set(_label_args(args))
    elif action == "restore_labels" or (action == "unmute" and args.get("labels")):
        by_id = {int(k): {int(x) for x in v or []} for k, v in (args.get("labels") or {}).items()}
        for m in msgs:
            if m.id in by_id:
                touched |= by_id[m.id] ^ set(m.label_ids or [])
    hit = {roles[i] for i in touched if i in roles}
    if "junk" in hit:
        return "mail.spam"
    if "trash" in hit:
        return "mail.trash"
    return "mail.label"


async def check_agent_labels(db: AsyncSession, uid: int, action: str, args: dict[str, Any]) -> None:
    """Agents name only the user's own labels, and add_labels never adds a system (role) label:
    Trash, Spam, Inbox and the rest have their own actions and permission classes."""
    ids = set(_label_args(args))
    if action in ("restore_labels", "unmute"):
        for v in (args.get("labels") or {}).values():
            ids |= {int(x) for x in v or []}
    if not ids:
        return
    rows = dict((await db.execute(select(Label.id, Label.role).where(Label.user_id == uid, Label.id.in_(ids)))).all())
    if set(rows) != ids:
        raise HTTPException(400, "unknown label")
    if action == "add_labels" and any(rows.values()):
        raise HTTPException(400, "system labels cannot be added with add_labels; use the matching action "
                                 "(archive, inbox, trash, spam, star, important)")


async def preview(db: AsyncSession, msgs: list[Message], action: str, args: dict[str, Any]) -> dict[str, Any]:
    """Human-readable summary for an approval card."""
    lines = []
    for m in msgs[:5]:
        who = m.from_name or m.from_email
        lines.append(f"{who}: {m.subject or '(no subject)'}")
    if len(msgs) > 5:
        lines.append(f"…and {len(msgs) - 5} more")
    ids = list(args.get("label_ids") or ([args["label_id"]] if args.get("label_id") else []))
    if ids and msgs:
        names = (await db.execute(select(Label.name).where(Label.id.in_(ids),
                                                           Label.user_id == msgs[0].user_id))).scalars().all()
        lines.insert(0, "Labels: " + ", ".join(names))
    if args.get("until"):
        lines.insert(0, f"Until {args['until']}")
    return {"title": f"{_VERB.get(action, action)}: {len(msgs)} message(s)", "lines": lines,
            "message_ids": [m.id for m in msgs[:200]]}


class ActionContext:
    def __init__(self, db: AsyncSession, store: UserStore, principal):
        self.db = db
        self.store = store
        self.p = principal
        self.uid = principal.user_id
        self._roles: dict[str, Label] | None = None
        # One local-change time per action, so the messages are written once, not once per mark.
        self.now = datetime.now(UTC)

    async def role(self, role: str) -> Label:
        if self._roles is None:
            rows = (await self.db.execute(select(Label).where(Label.user_id == self.uid, Label.role.is_not(None)))).scalars()
            self._roles = {lb.role: lb for lb in rows}
        if role not in self._roles:
            self._roles[role] = await self.store.role_label(self.db, role)
        return self._roles[role]


async def messages_for(db: AsyncSession, p, conv_ids: list[int] | None = None,
                       msg_ids: list[int] | None = None) -> list[Message]:
    from mailr.core.visibility import visible_message_filter

    # Actions read neither the embedding nor the body text.
    stmt = select(Message).options(*light_message()).where(
        Message.user_id == p.user_id, Message.deleted.is_(False), visible_message_filter(p))
    if conv_ids is not None:
        stmt = stmt.where(Message.conversation_id.in_(conv_ids))
    if msg_ids is not None:
        stmt = stmt.where(Message.id.in_(msg_ids))
    return list((await db.execute(stmt)).scalars())


async def _train(ctx: ActionContext, msgs: list[Message], kind: str, target: str,
                 value: bool) -> tuple[list[LabelDecision], list[list[Any]]]:
    """Record the user's choice as "user" decisions. Returns the decisions added and, as
    [id, status] pairs, the model decisions it rejected (an undo needs both)."""
    made = []
    for m in msgs[:200]:
        d = LabelDecision(user_id=ctx.uid, message_id=m.id, kind=kind, target=target, value=value,
                          source="user", source_ref=ctx.p.actor, confidence=1.0,
                          reason="user action", status="accepted")
        ctx.db.add(d)
        made.append(d)
    rejected: list[list[Any]] = []
    # Reject pending model suggestions that the user contradicted.
    if msgs:
        rows = (await ctx.db.execute(select(LabelDecision).where(
            LabelDecision.user_id == ctx.uid, LabelDecision.message_id.in_([m.id for m in msgs]),
            LabelDecision.kind == kind, LabelDecision.target == target,
            LabelDecision.source.in_(("classifier", "llm", "decision_model")), LabelDecision.value != value))).scalars()
        for d in rows:
            rejected.append([d.id, d.status])
            d.status = "rejected"
    await _retrain_soon(ctx, kind, target)
    return made, rejected


async def _retrain_soon(ctx: ActionContext, kind: str, target: str) -> None:
    await enqueue(ctx.db, "intel.retrain", {"kind": kind, "target": target}, user_id=ctx.uid,
                  dedupe_key=f"intel.retrain:{ctx.uid}:{kind}:{target}",
                  run_at=datetime.now(UTC))


# How long after a toggle an undo without its record (an app that drops the `importance` field)
# may still remove the user's latest importance decision on a message.
UNDO_FALLBACK_WINDOW = timedelta(hours=1)


async def restore_importance(ctx: ActionContext, msgs: list[Message], rec: dict[str, Any] | None) -> None:
    """Undo of Mark important / Mark not important: the decisions the toggle added are deleted,
    the model decisions it rejected get their status back, and each message gets its earlier
    flag and score. The decision record is then as it was before the toggle, and the model does
    not learn the opposite choice from an undo.

    `rec` is the undo record the toggle returned: message_ids with their earlier `important`
    and `scores`, the `decisions` it added and the `rejected` [id, status] pairs. Without it, the
    user's latest importance decision on each message is deleted when it is younger than
    UNDO_FALLBACK_WINDOW, and the flag follows the latest decision left."""
    db, mine = ctx.db, {m.id: m for m in msgs}
    if not mine:
        return
    base = (LabelDecision.user_id == ctx.uid, LabelDecision.kind == "importance",
            LabelDecision.message_id.in_(list(mine)))
    before: dict[int, tuple[bool, float | None]] = {}
    if rec:
        made = [int(x) for x in rec.get("decisions") or []]
        if made:
            await db.execute(delete(LabelDecision).where(*base, LabelDecision.source == "user",
                                                         LabelDecision.id.in_(made)))
        for did, status in rec.get("rejected") or []:
            await db.execute(update(LabelDecision).where(*base, LabelDecision.id == int(did),
                                                         LabelDecision.status == "rejected").values(status=str(status)))
        scores = list(rec.get("scores") or [])
        for i, (mid, imp) in enumerate(zip(rec.get("message_ids") or [], rec.get("important") or [], strict=False)):
            if int(mid) in mine:
                before[int(mid)] = (bool(imp), scores[i] if i < len(scores) else None)
    else:
        cutoff = datetime.now(UTC) - UNDO_FALLBACK_WINDOW
        rows = (await db.execute(select(LabelDecision).where(*base).order_by(LabelDecision.id))).scalars().all()
        latest_user: dict[int, LabelDecision] = {}
        for d in rows:
            if d.source == "user":
                latest_user[d.message_id] = d
        gone = {d.id for d in latest_user.values() if d.created_at and d.created_at >= cutoff}
        if gone:
            await db.execute(delete(LabelDecision).where(*base, LabelDecision.id.in_(gone)))
        for mid, m in mine.items():
            left = [d for d in rows if d.message_id == mid and d.id not in gone and d.status != "rejected"]
            if left:
                d = left[-1]
                score = d.confidence if d.source in ("classifier", "heuristic") else (1.0 if d.value else 0.0)
                before[mid] = (d.value, score)
            elif any(d.message_id == mid for d in latest_user.values() if d.id in gone):
                before[mid] = (not m.important, None)
    for val in (True, False):
        group = [mine[mid] for mid, (imp, _s) in before.items() if imp is val]
        if group:
            await ctx.store.set_keywords(group, important=val)
    for mid, (_imp, score) in before.items():
        mine[mid].importance_score = score
    await _retrain_soon(ctx, "importance", "important")


async def restore_category(ctx: ActionContext, msgs: list[Message], rec: dict[str, Any] | None) -> None:
    """Undo of a category change (More → Category, or the category part of Show in tab): the "user" decisions the change added are deleted,
    the model decisions it rejected get their status back, and each message gets its earlier
    category. `rec` is the undo record the change returned."""
    if not rec:
        raise HTTPException(400, "nothing to undo")
    db, mine = ctx.db, {m.id: m for m in msgs}
    base = (LabelDecision.user_id == ctx.uid, LabelDecision.kind == "category",
            LabelDecision.message_id.in_(list(mine) or [0]))
    made = [int(x) for x in rec.get("decisions") or []]
    targets: set[str] = set()
    if made:
        rows = (await db.execute(select(LabelDecision).where(*base, LabelDecision.source == "user",
                                                             LabelDecision.id.in_(made)))).scalars().all()
        targets |= {d.target for d in rows}
        await db.execute(delete(LabelDecision).where(*base, LabelDecision.source == "user", LabelDecision.id.in_(made)))
    for did, status in rec.get("rejected") or []:
        await db.execute(update(LabelDecision).where(*base, LabelDecision.id == int(did),
                                                     LabelDecision.status == "rejected").values(status=str(status)))
    for mid, cat in zip(rec.get("message_ids") or [], rec.get("categories") or [], strict=False):
        if int(mid) in mine:
            mine[int(mid)].category = cat
            targets.add(cat or "primary")
    # The conversation's category follows its messages again (threading.refresh keeps an old value
    # when no message has one, so it is cleared first).
    for cid in {m.conversation_id for m in msgs if m.conversation_id}:
        conv = await db.get(Conversation, cid)
        if conv is not None:
            conv.category = None
    for t in targets:
        await _retrain_soon(ctx, "category", t)


async def store_targets(db: AsyncSession, msgs: list[Message]) -> dict[int, list[int]]:
    """Store-mode connectors that may hold a copy of each message at their provider, as
    {connector id: message ids} (the copy is deleted after Delete forever, connectors/tasks.py
    `delete_forever_upstream`)."""
    by_conn: dict[int, list[int]] = {}
    from mailr.connectors.imap import all_refs

    for m in msgs:
        cids = {int(r["connector"]) for r in all_refs(m.upstream) if str(r.get("connector") or "").isdigit()}
        for cid in cids:
            by_conn.setdefault(cid, []).append(m.id)
    if not by_conn:
        return {}
    able = set((await db.execute(select(Connector.id).where(
        Connector.id.in_(list(by_conn)), Connector.mode == "store", Connector.enabled.is_(True)))).scalars())
    return {cid: ids for cid, ids in by_conn.items() if cid in able}


async def mirror_targets(db: AsyncSession, msgs: list[Message]) -> dict[int, list[int]]:
    """Mirror-mode connectors that hold a copy of each message, as {connector id: message ids}.

    A message can sit in several accounts: one provider stores it and also forwards it to a Gmail
    account that is connected as a mirror. Every mirror copy gets the change, whichever
    connector delivered the message first."""
    from mailr.connectors.base import REGISTRY
    from mailr.connectors.imap import all_refs
    from mailr.connectors.loader import _load_all

    _load_all()
    by_conn: dict[int, list[int]] = {}
    for m in msgs:
        cids = {m.connector_id} if m.connector_id else set()
        for r in all_refs(m.upstream):
            if str(r.get("connector") or "").isdigit():
                cids.add(int(r["connector"]))
        for cid in cids:
            by_conn.setdefault(cid, []).append(m.id)
    if not by_conn:
        return {}
    rows = (await db.execute(select(Connector.id, Connector.kind).where(
        Connector.id.in_(list(by_conn)), Connector.mode == "mirror", Connector.enabled.is_(True)))).all()
    able = {cid for cid, kind in rows if kind in REGISTRY and REGISTRY[kind].capabilities.write_back}
    return {cid: ids for cid, ids in by_conn.items() if cid in able}


def mark_local_change(msgs: list[Message], when: datetime | None = None) -> None:
    """Record that mailr changed these messages (a user, rule or agent action). A mirror
    account's older state is not applied over this change."""
    now = when or datetime.now(UTC)
    for m in msgs:
        m.local_change_at = now


# A write-back keeps trying through a provider outage of a few days: the job queue's backoff
# doubles from 30 s to its cap of one hour, so 72 attempts are about two and a half days. Until the
# provider confirms, the local state stands (connectors/runner.py `_pending_write_backs`) and the
# account says "N changes not synced to the provider yet" (core/accounthealth.py). It gave up after
# five attempts (about 7.5 minutes) before 13 Oct 2026, and the next pass reverted the change.
WRITE_BACK_ATTEMPTS = 72


async def _writeback(ctx: ActionContext, msgs: list[Message], **change: Any) -> None:
    mark_local_change(msgs, getattr(ctx, "now", None))
    for cid, ids in (await mirror_targets(ctx.db, msgs)).items():
        await enqueue(ctx.db, "connector.write_back", {"connector_id": cid, "message_ids": ids, "change": change},
                      user_id=ctx.uid, max_attempts=WRITE_BACK_ATTEMPTS)


async def restore_label_sets(ctx: ActionContext, msgs: list[Message], by_id: dict[int, list[int]],
                             writeback: bool = True) -> None:
    """Give each message in `by_id` the labels it had before (an Undo). Messages that go back to
    the same label set share one store update, chunked by the store, instead of one call per
    message; mirror accounts get the reverse change, grouped the same way."""
    groups: dict[tuple[int, ...], list[Message]] = {}
    for m in msgs:
        if m.id in by_id:
            groups.setdefault(tuple(sorted({int(x) for x in by_id[m.id] or []})), []).append(m)
    if not groups:
        return
    old = {m.id: set(m.label_ids or []) for group in groups.values() for m in group}
    names = {lb.id: (lb.role or lb.name) for lb in (await ctx.db.execute(
        select(Label).where(Label.user_id == ctx.uid))).scalars()} if writeback else {}
    for target, group in groups.items():
        await ctx.store.replace_labels(group, list(target), ctx.db)
    if not writeback:
        return
    changes: dict[tuple[tuple[str, ...], tuple[str, ...]], list[Message]] = {}
    for group in groups.values():
        for m in group:
            new = set(m.label_ids or [])
            key = (tuple(names[i] for i in sorted(new - old[m.id]) if i in names),
                   tuple(names[i] for i in sorted(old[m.id] - new) if i in names))
            changes.setdefault(key, []).append(m)
    for (add, rem), group in changes.items():
        await _writeback(ctx, group, add_labels=list(add), remove_labels=list(rem))


async def _label_names(ctx: ActionContext, ids: set[int]) -> dict[int, str]:
    """The user's labels' names by id (the names a write-back to a mirror account uses)."""
    if not ids:
        return {}
    return dict((await ctx.db.execute(select(Label.id, Label.name).where(
        Label.user_id == ctx.uid, Label.id.in_(sorted(ids)), Label.role.is_(None)))).all())


# Actions that can put mail back in Spam by restoring an earlier label set (the Undo of "Not spam").
_SPAM_ENTRY = ("restore_labels", "restore_tab", "unmute", "unsnooze")


async def _spam_kept_labels(ctx: ActionContext, msgs: list[Message], action: str,
                            before: dict[int, list[int]]) -> None:
    """The labels Report spam kept aside (Message.spam_kept_labels) follow the mail in and out of
    Spam whatever the action: mail that left Spam gets them back ("Not spam", Move to Inbox, the
    Undo of Report spam), except into Trash or when deleted (Trash keeps no labels); mail that an
    Undo put back in Spam keeps aside the own labels it lost."""
    from mailr.intel.classify import restore_kept_labels

    junk = (await ctx.role("junk")).id
    left = [m for m in msgs if m.spam_kept_labels and junk not in (m.label_ids or [])]
    if action in ("trash", "delete_forever"):
        for m in left:
            m.spam_kept_labels = None
    elif left:
        back = await restore_kept_labels(ctx.db, ctx.store, left)
        names = await _label_names(ctx, {i for ids in back.values() for i in ids})
        groups: dict[tuple[str, ...], list[Message]] = {}
        for m in left:
            if back.get(m.id):
                groups.setdefault(tuple(sorted(names[i] for i in back[m.id] if i in names)), []).append(m)
        for add, group in groups.items():
            if add:
                await _writeback(ctx, group, add_labels=list(add))
    if action in _SPAM_ENTRY:
        entered = [m for m in msgs if junk in (m.label_ids or []) and junk not in set(before.get(m.id) or [])]
        if entered:
            own = set((await ctx.db.execute(select(Label.id).where(
                Label.user_id == ctx.uid, Label.role.is_(None)))).scalars())
            for m in entered:
                lost = (set(before.get(m.id) or []) & own) - set(m.label_ids or [])
                if lost:
                    m.spam_kept_labels = sorted(lost | set(m.spam_kept_labels or []))


class _Nothing(Exception):
    """A handler found nothing to change (archive of mail already out of the Inbox): `affected` 0."""


class _Run:
    """One call of apply(): what every action handler reads. `msgs` may be narrowed by the handler
    (archive keeps only the mail it changes); the shared steps after it use the narrowed list."""

    def __init__(self, ctx: ActionContext, msgs: list[Message], action: str, args: dict[str, Any]):
        self.ctx, self.msgs, self.action, self.args = ctx, msgs, action, args
        self.store, self.db, self.uid = ctx.store, ctx.db, ctx.uid
        self.conv_ids = sorted({m.conversation_id for m in msgs if m.conversation_id})
        # Each message's labels before the action: the usual undo, and the Spam bookkeeping.
        self.before = {m.id: list(m.label_ids or []) for m in msgs}
        self._loaded: dict[int, Conversation] | None = None

    async def conv_of(self, cid: int) -> Conversation | None:
        """The action's conversations, loaded with one query when first needed (not one per id)."""
        if self._loaded is None:
            self._loaded = {c.id: c for c in (await self.db.execute(
                select(Conversation).where(Conversation.id.in_(self.conv_ids)))).scalars()}
        return self._loaded.get(cid)

    async def convs(self) -> list[Conversation]:
        """The action's conversations that exist, in id order."""
        return [c for cid in self.conv_ids if (c := await self.conv_of(cid)) is not None]

    def restore_before(self) -> dict[str, Any]:
        return {"action": "restore_labels", "labels": self.before}


Undo = dict[str, Any] | None


async def _archive(r: _Run) -> Undo:
    # What Archive removes is a user setting (Settings → Mail → Inbox → Archive):
    #   always the Inbox;
    #   `archive_removes_current_label` (default on): also the label of the list the user
    #     archived from (from_label_id), e.g. archiving in a "Later" tab clears Later;
    #   `archive_also_removes`: label ids cleared on every archive.
    # Role labels (Sent, Spam, ...) are never removed this way.
    from mailr.api.auth_routes import DEFAULT_SETTINGS
    from mailr.models import User

    u = await r.db.get(User, r.uid)
    prefs = {**DEFAULT_SETTINGS, **((u.settings if u else None) or {})}
    inbox = await r.ctx.role("inbox")
    wanted: list[int] = []
    if prefs.get("archive_removes_current_label") and r.args.get("from_label_id"):
        wanted.append(int(r.args["from_label_id"]))
    wanted += [int(x) for x in (prefs.get("archive_also_removes") or []) if str(x).isdigit()]
    remove, names = [inbox.id], ["inbox"]
    if wanted:
        for lb in (await r.db.execute(select(Label).where(Label.user_id == r.uid, Label.id.in_(wanted),
                                                          Label.role.is_(None)))).scalars():
            if lb.id not in remove:
                remove.append(lb.id)
                names.append(lb.name)
    # Only messages that carry one of those labels change. A conversation already archived,
    # or in Trash or Spam, is not archived again: nothing changes and `affected` says 0
    # (3 Oct 2026: an archive sent for a conversation in Trash answered "affected 1").
    r.msgs = [m for m in r.msgs if set(m.label_ids or []) & set(remove)]
    if not r.msgs:
        raise _Nothing
    await r.store.set_labels(r.msgs, add=[], remove=remove, db=r.db)
    await _writeback(r.ctx, r.msgs, remove_labels=names)
    return {"action": "restore_labels", "labels": {m.id: r.before[m.id] for m in r.msgs}}


async def _inbox(r: _Run) -> Undo:
    inbox, archive = await r.ctx.role("inbox"), await r.ctx.role("archive")
    await r.store.set_labels(r.msgs, add=[inbox.id], remove=[archive.id], db=r.db)
    await _writeback(r.ctx, r.msgs, add_labels=["inbox"])
    # Moving a snoozed conversation to the Inbox unsnoozes it (as in Gmail); otherwise it
    # would stay hidden from the Inbox until the snooze time.
    for conv in await r.convs():
        if conv.snoozed_until is not None:
            conv.snoozed_until = None
    return r.restore_before()


async def _restore_labels(r: _Run) -> Undo:
    by_id = {int(k): v for k, v in (r.args.get("labels") or {}).items()}
    # Undo is an action like any other: mirror accounts get the reverse change too.
    await restore_label_sets(r.ctx, r.msgs, by_id)
    # Undoing "Report spam" takes its mark back from the spam sender domains (intel/spam.py).
    junk_ids = {(await r.ctx.role("junk")).id}
    left = [m for m in r.msgs if m.id in by_id and junk_ids & set(r.before.get(m.id) or [])
            and not junk_ids & set(m.label_ids or [])]
    if left:
        from mailr.intel import spam as spamintel

        await spamintel.forget_domains(r.db, r.uid, left, removed=False)
    return None


async def _trash(r: _Run) -> Undo:
    target = await r.ctx.role("trash")
    await r.store.replace_labels(r.msgs, [target.id], r.db)
    await _writeback(r.ctx, r.msgs, add_labels=["trash"], remove_labels=["inbox"])
    return r.restore_before()


async def _spam(r: _Run) -> Undo:
    target = await r.ctx.role("junk")
    # The message keeps only the Spam label; the user's own labels are kept aside for "Not
    # spam", and the ones the classifier applied are dropped (classify.move_to_spam). Mirror
    # accounts keep only the Spam copy: the label folders' copies go, so other mail apps never
    # show the spam there (Gmail keeps its labels on spam itself; connectors/gmail.py).
    from mailr.intel import spam as spamintel
    from mailr.intel.classify import move_to_spam

    kept = await move_to_spam(r.db, r.store, r.msgs, target.id)
    names = await _label_names(r.ctx, {i for ids in kept.values() for i in ids})
    await _writeback(r.ctx, r.msgs, add_labels=["junk"], remove_labels=["inbox", *sorted(set(names.values()))])
    await _train(r.ctx, r.msgs, "spam", "spam", True)
    for m in r.msgs:
        await enqueue(r.db, "spam.train", {"message_id": m.id, "spam": True}, user_id=r.uid)
    # Two reported conversations from one domain send its later mail to Spam (intel/spam.py).
    await spamintel.learn_domains(r.db, r.uid, r.msgs)
    if r.args.get("block_sender"):
        from sqlalchemy import text

        for email in {m.from_email for m in r.msgs if m.from_email}:
            await r.db.execute(text(
                "INSERT INTO people (user_id,email,name,organization,received_count,sent_count,notes,blocked) "
                "VALUES (:u,:e,'','',0,0,'',true) ON CONFLICT (user_id,email) DO UPDATE SET blocked=true"),
                {"u": r.uid, "e": email})
    return r.restore_before()


async def _out_of_trash_or_spam(r: _Run) -> Undo:
    """untrash and not_spam."""
    not_spam = r.action == "not_spam"
    src = await r.ctx.role("junk" if not_spam else "trash")
    inbox = await r.ctx.role("inbox")
    sent = await r.ctx.role("sent")
    # Not spam: the labels Report spam kept aside come back (classify.move_to_spam), upstream
    # in the same write-back as the Inbox, so the label folders get their copies again.
    back: dict[int, list[int]] = {}
    if not_spam:
        from mailr.intel.classify import restore_kept_labels

        back = await restore_kept_labels(r.db, r.store, r.msgs)
    # One store call per destination (Sent for my own mail, else the Inbox), not per message.
    for mine in (True, False):
        group = [m for m in r.msgs if bool(m.sent_by_me) is mine]
        if group:
            await r.store.set_labels(group, add=[sent.id] if mine else [inbox.id], remove=[src.id], db=r.db)
    names = await _label_names(r.ctx, {i for ids in back.values() for i in ids})
    by_add: dict[tuple[str, ...], list[Message]] = {}
    for m in r.msgs:
        by_add.setdefault(tuple(sorted(names[i] for i in back.get(m.id, ()) if i in names)), []).append(m)
    for add, group in by_add.items():
        await _writeback(r.ctx, group, add_labels=["inbox", *add], remove_labels=["junk" if not_spam else "trash"])
    if not_spam:
        await _not_spam_learning(r)
    return r.restore_before()


async def _not_spam_learning(r: _Run) -> None:
    """What "Not spam" teaches: the spam model, the spam sender domains, and the mail the models
    skipped or whose label suggestions went while it sat in Spam."""
    from mailr.intel import spam as spamintel

    await _train(r.ctx, r.msgs, "spam", "spam", False)
    for m in r.msgs:
        await enqueue(r.db, "spam.train", {"message_id": m.id, "spam": False}, user_id=r.uid)
    await spamintel.forget_domains(r.db, r.uid, r.msgs, removed=True)
    # Mail no model read because it looked like spam is triaged again, models allowed, and
    # the label classifier runs again on it and on mail whose label suggestions went when
    # it was moved to Spam (classify.withdraw_labels).
    ids = [m.id for m in r.msgs]
    skipped = set((await r.db.execute(select(LabelDecision.message_id).where(
        LabelDecision.message_id.in_(ids), LabelDecision.kind == "triage",
        LabelDecision.target == "model_skipped"))).scalars())
    withdrawn = set((await r.db.execute(select(LabelDecision.message_id).where(
        LabelDecision.message_id.in_(ids), LabelDecision.kind == "label",
        LabelDecision.source == "classifier", LabelDecision.status == "withdrawn"))).scalars())
    if skipped or withdrawn:
        await enqueue(r.db, "intel.retriage", {"user_id": r.uid, "message_ids": sorted(skipped | withdrawn),
                                               "labels": True, "labels_only": sorted(withdrawn - skipped)},
                      user_id=r.uid)


async def _delete_forever(r: _Run) -> Undo:
    from mailr.core import purge

    await r.store.destroy(r.msgs)
    for m in r.msgs:
        m.deleted = True
        m.jmap_id = None  # the store may reuse ids; a deleted row must never match one
    await _writeback(r.ctx, r.msgs, delete=True)
    # A store account's provider may still hold a copy (inside the retention window): it goes too.
    for cid, ids in (await store_targets(r.db, r.msgs)).items():
        await enqueue(r.db, "connector.delete_forever", {"connector_id": cid, "message_ids": ids},
                      user_id=r.uid, max_attempts=WRITE_BACK_ATTEMPTS)
    # The row's text, what was made from it and its export file go in the background (core/purge.py).
    await purge.queue(r.db, r.uid, [m.id for m in r.msgs])
    return None


def _latest_per_conversation(msgs: list[Message], *, skip_drafts: bool) -> list[Message]:
    """The newest message of each conversation (what Gmail marks unread or stars)."""
    oldest = datetime.min.replace(tzinfo=UTC)
    latest: dict[int | None, Message] = {}
    for m in msgs:
        cur = latest.get(m.conversation_id)
        if skip_drafts and m.draft:
            continue
        if cur is None or (m.date or oldest) >= (cur.date or oldest):
            latest[m.conversation_id] = m
    return list(latest.values())


async def _read_unread(r: _Run) -> Undo:
    targets = r.msgs
    if r.action == "unread" and r.args.get("scope") != "messages":
        # Gmail: marking a conversation unread marks its latest message unread.
        targets = _latest_per_conversation(r.msgs, skip_drafts=True) or r.msgs
    await r.store.set_keywords(targets, seen=(r.action == "read"))
    await _writeback(r.ctx, targets, seen=(r.action == "read"))
    return {"action": REVERSIBLE[r.action]}


async def _star(r: _Run) -> Undo:
    # Gmail stars the latest message of a conversation.
    targets = _latest_per_conversation(r.msgs, skip_drafts=False) if r.args.get("scope") != "messages" else r.msgs
    await r.store.set_keywords(targets, flagged=True)
    await _writeback(r.ctx, targets, flagged=True)
    return {"action": "unstar"}


async def _unstar(r: _Run) -> Undo:
    await r.store.set_keywords(r.msgs, flagged=False)
    await _writeback(r.ctx, r.msgs, flagged=False)
    return {"action": "star"}


async def _importance(r: _Run) -> Undo:
    """important and not_important."""
    val = r.action == "important"
    prior = [(m.id, m.important, m.importance_score) for m in r.msgs]
    await r.store.set_keywords(r.msgs, important=val)
    for m in r.msgs:
        m.importance_score = 1.0 if val else 0.0
    made, rejected = await _train(r.ctx, r.msgs, "importance", "important", val)
    await r.db.flush()
    # The undo restores the decision record (restore_importance) instead of adding the
    # opposite choice, so an undone toggle leaves no training example behind.
    return {"action": "restore_importance", "importance": {
        "message_ids": [i for i, _imp, _s in prior], "important": [imp for _i, imp, _s in prior],
        "scores": [s for _i, _imp, s in prior], "decisions": [d.id for d in made], "rejected": rejected}}


async def _restore_importance(r: _Run) -> Undo:
    await restore_importance(r.ctx, r.msgs, r.args.get("importance"))
    return None


async def _labels(r: _Run) -> Undo:
    """add_labels, remove_labels and move."""
    ids = _label_args(r.args)
    owned = {lb.id: lb for lb in (await r.db.execute(
        select(Label).where(Label.user_id == r.uid, Label.id.in_(ids)))).scalars()}
    if set(ids) - set(owned):
        raise HTTPException(400, "unknown label")
    names = [owned[i].role or owned[i].name for i in ids]
    if r.action == "add_labels":
        await r.store.set_labels(r.msgs, add=ids, remove=[], db=r.db)
        await _writeback(r.ctx, r.msgs, add_labels=names)
    elif r.action == "remove_labels":
        await r.store.set_labels(r.msgs, add=[], remove=ids, db=r.db)
        await _writeback(r.ctx, r.msgs, remove_labels=names)
    else:
        from_id = r.args.get("from_label_id") or (await r.ctx.role("inbox")).id
        await r.store.set_labels(r.msgs, add=ids, remove=[int(from_id)], db=r.db)
        await _writeback(r.ctx, r.msgs, add_labels=names, remove_labels=["inbox"])
    for i in ids:
        if not owned[i].role:
            await _train(r.ctx, r.msgs, "label", str(i), r.action != "remove_labels")
    return r.restore_before()


async def _category(r: _Run) -> Undo:
    # ACT mail.category: the user's correction, a "user" decision the category model learns.
    cat = r.args.get("category")
    prior = [(m.id, m.category) for m in r.msgs]
    for m in r.msgs:
        m.category = cat
    made, rejected = await _train(r.ctx, r.msgs, "category", cat or "primary", True)
    await r.db.flush()
    # As with importance, the undo restores the record instead of adding the opposite choice.
    return {"action": "restore_category", "categories": {
        "message_ids": [i for i, _c in prior], "categories": [c for _i, c in prior],
        "decisions": [d.id for d in made], "rejected": rejected}}


async def _restore_category(r: _Run) -> Undo:
    await restore_category(r.ctx, r.msgs, r.args.get("categories"))
    return None


async def _show_in_tab(r: _Run) -> Undo:
    return await show_in_tab(r.ctx, r.msgs, r.args, r.before)


async def _restore_tab(r: _Run) -> Undo:
    # The undo of Show in tab: the labels each message had, then the category record.
    by_id = {int(k): v for k, v in (r.args.get("labels") or {}).items()}
    if by_id:
        await restore_label_sets(r.ctx, r.msgs, by_id)
    if r.args.get("categories"):
        await restore_category(r.ctx, r.msgs, r.args["categories"])
    return None


async def _mute(r: _Run) -> Undo:
    """mute and unmute."""
    mute = r.action == "mute"
    for cid in r.conv_ids:
        conv = await r.conv_of(cid)
        conv.muted = mute  # type: ignore[union-attr]  # the messages' own conversations
    if mute:
        inbox = await r.ctx.role("inbox")
        await r.store.set_labels(r.msgs, add=[], remove=[inbox.id], db=r.db)
        await _writeback(r.ctx, r.msgs, remove_labels=["inbox"])
        # Undo = unmute AND put the conversation back where it was (Gmail's z after m).
        return {"action": "unmute", "labels": r.before}
    if r.args.get("labels"):
        await restore_label_sets(r.ctx, r.msgs, {int(k): v for k, v in r.args["labels"].items()})
    return {"action": "mute"}


async def _snooze(r: _Run) -> Undo:
    when = await _future(r, "snooze")
    inbox = await r.ctx.role("inbox")
    await r.store.set_labels(r.msgs, add=[], remove=[inbox.id], db=r.db)
    await _writeback(r.ctx, r.msgs, remove_labels=["inbox"])
    for cid in r.conv_ids:
        conv = await r.conv_of(cid)
        conv.snoozed_until = when  # type: ignore[union-attr]  # the messages' own conversations
        await enqueue(r.db, "snooze.wake", {"conversation_id": cid, "until": when.isoformat()},
                      user_id=r.uid, run_at=when)
    return {"action": "unsnooze", "labels": r.before}


async def _unsnooze(r: _Run) -> Undo:
    inbox = await r.ctx.role("inbox")
    for cid in r.conv_ids:
        conv = await r.conv_of(cid)
        conv.snoozed_until = None  # type: ignore[union-attr]  # the messages' own conversations
    if r.args.get("labels"):
        await restore_label_sets(r.ctx, r.msgs, {int(k): v for k, v in r.args["labels"].items()}, writeback=False)
    else:
        await r.store.set_labels(r.msgs, add=[inbox.id], remove=[], db=r.db)
    return None


async def _follow_up(r: _Run) -> Undo:
    when = await _future(r, "follow-up")
    for cid in r.conv_ids:
        conv = await r.conv_of(cid)
        since = (await r.db.execute(select(func.max(Message.date)).where(
            Message.conversation_id == cid, Message.sent_by_me.is_(True), Message.draft.is_(False),
            Message.deleted.is_(False)))).scalar()
        if since is None:
            raise HTTPException(400, "a follow-up reminder needs a message you sent in the conversation")
        conv.follow_up_at, conv.follow_up_since, conv.nudged_at = when, since, None  # type: ignore[union-attr]
        await enqueue(r.db, "followup.check", {"conversation_id": cid, "at": when.isoformat()},
                      user_id=r.uid, run_at=when)
    return {"action": "cancel_follow_up"}


async def _cancel_follow_up(r: _Run) -> Undo:
    for cid in r.conv_ids:
        conv = await r.conv_of(cid)
        conv.follow_up_at = conv.follow_up_since = conv.nudged_at = None  # type: ignore[union-attr]
    return None


# One handler per action. Each changes the store and the index, queues the write-back, and returns
# the undo descriptor (None: no undo); apply() does the checks before and the bookkeeping after.
_HANDLERS: dict[str, Callable[[_Run], Awaitable[Undo]]] = {
    "archive": _archive, "inbox": _inbox, "restore_labels": _restore_labels, "trash": _trash,
    "spam": _spam, "untrash": _out_of_trash_or_spam, "not_spam": _out_of_trash_or_spam,
    "delete_forever": _delete_forever, "read": _read_unread, "unread": _read_unread, "star": _star,
    "unstar": _unstar, "important": _importance, "not_important": _importance,
    "restore_importance": _restore_importance, "add_labels": _labels, "remove_labels": _labels,
    "move": _labels, "category": _category, "restore_category": _restore_category,
    "show_in_tab": _show_in_tab, "restore_tab": _restore_tab, "mute": _mute, "unmute": _mute,
    "snooze": _snooze, "unsnooze": _unsnooze, "follow_up": _follow_up, "cancel_follow_up": _cancel_follow_up,
}


# ACT mail.archive @api, ACT mail.delete @api, ACT mail.spam @api, ACT mail.not_spam @api, ACT mail.read @api, ACT mail.unread @api, ACT mail.star @api,
# ACT mail.important @api (its undo: restore_importance), ACT mail.snooze @api, ACT mail.label @api, ACT mail.move @api, ACT mail.mute @api, ACT mail.follow_up @api,
# ACT mail.category @api (its undo: restore_category), ACT mail.show_in_tab @api (its undo: restore_tab)
async def apply(ctx: ActionContext, msgs: list[Message], action: str, args: dict[str, Any]) -> dict[str, Any]:
    if not msgs:
        return {"ok": True, "affected": 0, "undo": None}
    need = SCOPE_FOR.get(action, "label")
    ctx.p.require(need)
    if action in ("add_labels", "move") and (await ctx.role("junk")).id in set(_label_args(args)):
        # Moving mail into Spam (Move to Spam, a drop on Spam, add_labels or move through the API)
        # is Report spam: the same placement, kept labels, training and write-back. Its permission
        # class is mail.spam already (classify).
        action, args = "spam", {k: v for k, v in args.items() if k == "block_sender"}
    handler = _HANDLERS.get(action)
    if handler is None:
        raise HTTPException(400, f"unknown action {action}")
    r = _Run(ctx, msgs, action, args)
    try:
        undo = await handler(r)
    except _Nothing:
        return {"ok": True, "affected": 0, "undo": None}
    msgs = r.msgs
    if action != "spam" and (any(m.spam_kept_labels for m in msgs) or (action in _SPAM_ENTRY and msgs)):
        await _spam_kept_labels(ctx, msgs, action, r.before)
    if action not in ("follow_up", "cancel_follow_up"):
        mark_local_change(msgs, ctx.now)
    if action in ("archive", "trash", "spam", "delete_forever"):
        # Dealing with a nudged conversation ends its "No reply" mark.
        for conv in await r.convs():
            conv.nudged_at = None

    await ctx.db.flush()
    await threading.refresh(ctx.db, ctx.uid, r.conv_ids)
    return {"ok": True, "affected": len(msgs), "undo": undo}


async def show_in_tab(ctx: ActionContext, msgs: list[Message], args: dict[str, Any],
                      before: dict[int, list[int]]) -> dict[str, Any] | None:
    """ACT mail.show_in_tab: make each conversation show in the active layout's tab `tab`
    (core/tabplace.py plan): add the tab's label (or the Inbox), remove the labels of claiming tabs
    that would take it first, and set the category when the tab needs one. A category change is
    the user's correction, as with the category action. Undo (restore_tab) puts back the labels
    and the category record."""
    from mailr.api.mail_read import _settings
    from mailr.core import tabplace

    settings = await _settings(ctx.db, ctx.p)
    lay = tabplace.active_layout(settings)
    tab_id = str(args.get("tab") or "")
    if lay is None or not tab_id:
        raise HTTPException(400, "show_in_tab needs 'tab', a tab of the active layout")
    inbox = await ctx.role("inbox")
    archive_id = (await ctx.role("archive")).id
    by_conv: dict[int, list[Message]] = {}
    for m in msgs:
        by_conv.setdefault(m.conversation_id, []).append(m)
    convs = {c.id: c for c in (await ctx.db.execute(select(Conversation).where(
        Conversation.id.in_(list(by_conv))))).scalars()}
    owned = {lb.id: lb for lb in (await ctx.db.execute(select(Label).where(Label.user_id == ctx.uid))).scalars()}
    plans: dict[tuple, list[Message]] = {}
    cat_groups: dict[str, list[Message]] = {}
    for cid, group in by_conv.items():
        conv = convs.get(cid)
        try:
            pl = tabplace.plan(settings, lay, tab_id, conv.category if conv else None, inbox.id)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        add = [i for i in pl["add"] if i in owned]
        remove = [i for i in pl["remove"] if i in owned]
        if inbox.id in add:
            remove.append(archive_id)
        plans.setdefault((tuple(add), tuple(remove)), []).extend(group)
        if pl["category"]:
            cat_groups.setdefault(pl["category"], []).extend(group)
    label_changed: list[Message] = []
    for (add, remove), group in plans.items():
        changing = [m for m in group if set(add) - set(m.label_ids or []) or set(remove) & set(m.label_ids or [])]
        if not changing:
            continue
        await ctx.store.set_labels(changing, add=list(add), remove=list(remove), db=ctx.db)
        await _writeback(ctx, changing, add_labels=[owned[i].role or owned[i].name for i in add],
                         remove_labels=[owned[i].role or owned[i].name for i in remove if i in owned])
        for i in add:
            if not owned[i].role:
                await _train(ctx, changing, "label", str(i), True)
        label_changed += changing
    undo: dict[str, Any] = {"action": "restore_tab"}
    if label_changed:
        undo["labels"] = {m.id: before[m.id] for m in label_changed}
    if cat_groups:
        prior: list[tuple[int, str | None]] = []
        made: list[LabelDecision] = []
        rejected: list[list[Any]] = []
        for cat, group in cat_groups.items():
            prior += [(m.id, m.category) for m in group]
            for m in group:
                m.category = cat
            d, r = await _train(ctx, group, "category", cat, True)
            made += d
            rejected += r
        await ctx.db.flush()
        undo["categories"] = {"message_ids": [i for i, _c in prior], "categories": [c for _i, c in prior],
                              "decisions": [d.id for d in made], "rejected": rejected}
    return undo if len(undo) > 1 else None


async def _future(r: _Run, what: str) -> datetime:
    """`until`: an ISO time; one without an offset is in the user's zone (as the approval card shows it)."""
    from mailr.calendar.tz import aware

    until = r.args.get("until")
    if not until:
        raise HTTPException(400, f"{what} needs 'until'")
    try:
        when = datetime.fromisoformat(str(until).replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, f"{what} 'until' must be an ISO date-time") from None
    when = await aware(r.db, r.uid, when)
    if when <= datetime.now(UTC):
        raise HTTPException(400, f"{what} time is in the past")
    return when


async def replied_since(db: AsyncSession, conv_id: int, since: datetime) -> bool:
    """Someone else wrote in the conversation after `since` (my message)."""
    return (await db.execute(select(Message.id).where(
        Message.conversation_id == conv_id, Message.sent_by_me.is_(False), Message.draft.is_(False),
        Message.deleted.is_(False), Message.date > since).limit(1))).first() is not None


async def check_follow_up(db: AsyncSession, store: UserStore, conv_id: int, at_iso: str) -> bool:
    """mail.follow_up comes due: a reply since cancels it; otherwise the conversation returns to
    the inbox marked "No reply" (nudged_at). Returns whether it nudged."""
    conv = await db.get(Conversation, conv_id)
    if conv is None or conv.follow_up_at is None:
        return False
    if conv.follow_up_at.isoformat() != at_iso and conv.follow_up_at > datetime.now(UTC):
        return False  # moved to a later time; a newer job checks then
    since = conv.follow_up_since or conv.follow_up_at
    conv.follow_up_at = conv.follow_up_since = None
    if await replied_since(db, conv_id, since):
        await threading.refresh(db, conv.user_id, [conv_id])
        return False
    inbox = await store.role_label(db, "inbox")
    msgs = list((await db.execute(select(Message).where(Message.conversation_id == conv_id,
                                                          Message.deleted.is_(False)))).scalars())
    await store.set_labels(msgs, add=[inbox.id], remove=[], db=db)
    from types import SimpleNamespace

    await _writeback(SimpleNamespace(db=db, uid=conv.user_id), msgs, add_labels=["inbox"])
    conv.nudged_at = datetime.now(UTC)
    conv.snoozed_until = None
    await threading.refresh(db, conv.user_id, [conv_id])
    return True


async def wake_snoozed(db: AsyncSession, store: UserStore, conv_id: int, until_iso: str) -> None:
    conv = await db.get(Conversation, conv_id)
    if conv is None or conv.snoozed_until is None:
        return
    if conv.snoozed_until.isoformat() != until_iso and conv.snoozed_until > datetime.now(UTC):
        return  # re-snoozed to a later time; a newer job will wake it
    inbox = await store.role_label(db, "inbox")
    msgs = list((await db.execute(select(Message).where(Message.conversation_id == conv_id,
                                                          Message.deleted.is_(False)))).scalars())
    await store.set_labels(msgs, add=[inbox.id], remove=[], db=db)
    from types import SimpleNamespace

    await _writeback(SimpleNamespace(db=db, uid=conv.user_id), msgs, add_labels=["inbox"])
    # Gmail marks a woken conversation unread so it stands out.
    latest = max(msgs, key=lambda m: m.date or datetime.min.replace(tzinfo=UTC)) if msgs else None
    if latest is not None:
        await store.set_keywords([latest], seen=False)
    conv.snoozed_until = None
    await threading.refresh(db, conv.user_id, [conv_id])
