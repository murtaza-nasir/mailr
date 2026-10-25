"""Activity: what you, Ambrose and your agents changed recently, across mail,
calendar, tasks, notes and tables, with an Undo where the change can be taken back, and the
administrator's changes that affected you.

There is no table of its own. An activity entry is an audit_log row (core/audit.py) whose result
carries an `activity` object:

    {"app": "mail" | "calendar" | "tasks" | "notes" | "tables" | "settings" | "account",
     "text": "Archived “Lunch on Friday”",       # one plain phrase, without the person (English)
     "msg": {"key": "activity.mail.archive", "params": {"what": {...}}},  # the same, as a key
     "target": {"kind", "id", "title"} | None,    # what it changed (kind: conversation, event, task, note, table)
     "key": "event:abc",                          # the thing it changed, for "a later change exists"
     "undo": {"method", "path", "body"} | {"mail": {...}} | None,
     "undone_at": iso | absent, "count": n}       # count: coalesced edits

An administrator's change that affects a person (core/audit.notice, action admin_notice.*) is an
entry too: its text is the notice's sentence (inputs.description, and inputs.msg as a key).

The phrase is kept as a catalogue key with parameters (`msg`, mailr/i18n `activity.*`) and rendered
when the feed is read, in the reader's language. `text` keeps the English phrase beside it: entries
written before the keys existed have only `text`, and it is shown as it is.

Who made a change is the row's actor: "user" (you), "agent" (Ambrose, the built-in assistant),
"token:<name>" (an agent with an API token), "admin" (an administrator's notice) or "system".

The Undo is a request the signed-in client sends as it is (the same routes the apps use, with the
session's own rights), then marks the entry undone (POST /activity/{id}/undone). An entry offers
Undo only while nothing later changed the same thing, for UNDO_DAYS days, and only once. Mail undos
are stored with internal ids and are turned into a request with public ids when they are read.

Frequent small writes (a note's text, a checklist's items, a table's cells) are coalesced: a write
within COALESCE_MINUTES of the newest entry for the same thing by the same actor bumps that entry
(its time and count) and adds no row. Such entries carry no Undo; the note's or table's own history
holds the steps.
"""

from __future__ import annotations

from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from mailr import i18n
from mailr.models import AuditLog

KEY = "activity"
APPS = ("mail", "calendar", "tasks", "notes", "tables", "settings", "account")
UNDO_DAYS = 7
COALESCE_MINUTES = 10
# The partial index ix_audit_activity (migration d7a3f9c1e2b4) has exactly this predicate.
WHERE = "(audit_log.result ? 'activity' OR audit_log.action LIKE 'admin_notice.%')"


def short(s: Any, n: int = 60) -> str:
    v = " ".join(str(s or "").split())
    return v if len(v) <= n else v[: n - 1] + "…"


def quoted(s: Any, empty: str = "untitled", n: int = 60) -> dict[str, Any]:
    """A title in quotation marks (the language's own), or `empty` (a key under activity.what)."""
    v = short(s, n)
    return i18n.msg("activity.what.quoted", title=v) if v else i18n.msg(f"activity.what.{empty}")


# The forward Ambrose is acting on (assistant/forwards.py): while it is set, every entry carries
# `via` {"kind": "conversation", "id", "title"}, so the feed links each step to the forwarded note.
VIA: ContextVar[dict[str, Any] | None] = ContextVar("activity_via", default=None)


def entry(app: str, m: dict[str, Any], *, undo: dict[str, Any] | None = None, kind: str | None = None,
          ref: Any = None, title: str | None = None, key: str | None = None) -> dict[str, Any]:
    """An activity object; `m` is the phrase as an i18n.msg (its English is kept as `text`)."""
    out: dict[str, Any] = {"app": app, "text": short(i18n.render(m), 300), "msg": m}
    if (via := VIA.get()) is not None:
        out["via"] = dict(via)
    if kind and ref is not None:
        out["target"] = {"kind": kind, "id": str(ref), "title": short(title, 200)}
    k = key or (f"{kind}:{ref}" if kind and ref is not None else None)
    if k:
        out["key"] = k[:200]
    if undo:
        out["undo"] = undo
    return out


def http(method: str, path: str, body: Any = None) -> dict[str, Any]:
    out: dict[str, Any] = {"method": method, "path": path}
    if body is not None:
        out["body"] = body
    return out


def actor_of(by: str | None) -> str:
    """The audit actor for a live `by` (core/live.who): user, assistant or agent:<name>."""
    if not by or by == "user":
        return "user"
    if by == "assistant":
        return "agent"
    if by.startswith("agent:"):
        return f"token:{by[6:]}"
    return by[:50]


async def add(db: AsyncSession, user_id: int, actor: str, action: str, target: str, act: dict[str, Any], *,
              inputs: dict[str, Any] | None = None, token_id: int | None = None, coalesce: bool = False) -> None:
    """Write one activity entry (or bump the newest one for the same thing, with `coalesce`)."""
    if coalesce and act.get("key"):
        since = datetime.now(UTC) - timedelta(minutes=COALESCE_MINUTES)
        row = (await db.execute(
            select(AuditLog).where(AuditLog.user_id == user_id, AuditLog.actor == actor[:50],
                                   AuditLog.action == action[:100], AuditLog.created_at >= since,
                                   text("audit_log.result ? 'activity'"))
            .order_by(AuditLog.id.desc()).limit(1))).scalar_one_or_none()
        if row is not None and ((row.result or {}).get(KEY) or {}).get("key") == act["key"]:
            prev = dict(row.result[KEY])
            prev["count"] = int(prev.get("count") or 1) + 1
            prev.pop("undo", None)
            prev["text"] = act["text"]  # the name as it is now
            if act.get("msg"):
                prev["msg"] = act["msg"]
            if act.get("target"):
                prev["target"] = act["target"]
            row.result = {**row.result, KEY: prev}
            row.created_at = datetime.now(UTC)
            return
    db.add(AuditLog(user_id=user_id, token_id=token_id, actor=actor[:50], action=action[:100],
                    target=str(target)[:200], inputs=inputs or {}, result={KEY: act}, ok=True))


# ---------------------------------------------------------------- mail

# The mail actions that make an entry; each one's phrase is activity.mail.<action>
# ("Archived {{what}}"), and any other action reads activity.mail.other.
MAIL_PAST = frozenset({
    "archive", "inbox", "trash", "untrash", "spam", "not_spam", "delete_forever", "star", "unstar",
    "important", "not_important", "add_labels", "remove_labels", "move", "snooze", "unsnooze", "mute",
    "unmute", "category", "follow_up", "cancel_follow_up", "restore_labels", "restore_importance",
    "restore_category", "show_in_tab", "restore_tab",
})
# Reading mail is not a change a person looks for here (opening a conversation marks it read).
MAIL_QUIET = frozenset({"read", "unread"})


def mail(action: str, msgs: list[Any], ids: list[int], by: str, undo: dict[str, Any] | None) -> dict[str, Any] | None:
    """The entry of a mail action on `ids` (conversations or messages, as `by` says)."""
    if action in MAIL_QUIET or not msgs:
        return None
    convs = sorted({m.conversation_id for m in msgs if m.conversation_id})
    what = quoted(msgs[0].subject, "a_conversation") if len(convs) <= 1 else \
        i18n.msg("activity.what.conversations", count=len(convs))
    stored = {"mail": {**undo, "ids": [int(i) for i in ids][:500], "by": by}} if undo else None
    first = convs[0] if convs else None
    phrase = i18n.msg(f"activity.mail.{action if action in MAIL_PAST else 'other'}", what=what)
    return entry("mail", phrase, undo=stored,
                 kind="conversation" if len(convs) == 1 else None, ref=first if len(convs) == 1 else None,
                 title=msgs[0].subject if len(convs) == 1 else None,
                 key="mail:" + ",".join(map(str, convs[:30])))


def _mail_request(u: dict[str, Any]) -> dict[str, Any] | None:
    """A stored mail undo as the request the client sends (public ids)."""
    from mailr.core import publicid
    from mailr.core.publicid import ConvId, MsgId

    by = u.get("by") or "conversation"
    body = {k: v for k, v in u.items() if k not in ("by", "ids")}
    wrap = ConvId if by == "conversation" else MsgId
    body["ids"] = [wrap(int(i)) for i in u.get("ids") or []]
    if isinstance(body.get("labels"), dict) and publicid.enabled():
        body["labels"] = {str(publicid.encode("message", int(k))): v for k, v in body["labels"].items()}
    if not body.get("action"):
        return None
    return http("POST", "/conversations/actions" if by == "conversation" else "/messages/actions", body)


# ---------------------------------------------------------------- calendar and tasks

SAFE_EVENT = ("summary", "location", "description", "start", "end", "all_day", "tz", "color")
SAFE_TASK = ("summary", "notes", "priority", "due", "start", "categories", "reminders", "reminders_at")


def _when(obj: Any, which: str) -> str | None:
    if obj.all_day:
        d = getattr(obj, f"{which}_date", None)
        return d.isoformat() if d else None
    v = getattr(obj, f"{which}_at", None)
    return v.isoformat() if v else None


def _task_due(obj: Any) -> str | None:
    if obj.due_date is not None:
        return obj.due_date.isoformat()
    return obj.due_at.isoformat() if obj.due_at else None


async def snapshot(db: AsyncSession, user_id: int, action: str, payload: dict[str, Any], p: Any) -> dict[str, Any] | None:
    """What an update or delete of an event or task needs for its Undo, read before it runs."""
    if action not in ("calendar.update", "calendar.delete", "tasks.update", "tasks.delete", "tasks.complete"):
        return None
    uid = payload.get("uid")
    if not uid:
        return None
    from mailr.calendar import mirror
    from mailr.calendar import tz as tzmod
    from mailr.models import CalCollection

    kind = "event" if action.startswith("calendar.") else "task"
    try:
        obj = await mirror.find_by_uid(db, user_id, str(uid), kind, p)
    except Exception:  # noqa: BLE001  an activity entry never fails the change
        return None
    if obj is None:
        return None
    coll = await db.get(CalCollection, obj.collection_id)
    snap: dict[str, Any] = {"summary": obj.summary or "", "recurring": bool(obj.recurring),
                            "guests": int(getattr(obj, "attendee_count", 0) or 0),
                            "calendar": coll.href if coll is not None else None,
                            "description_len": len(obj.description or "")}
    if kind == "event":
        snap.update({"location": obj.location or "", "description": (obj.description or "")[:3000],
                     "start": _when(obj, "start"), "end": _when(obj, "end"), "all_day": bool(obj.all_day),
                     "tz": tzmod.iana(obj.tzid), "color": obj.color})
    else:
        snap.update({"notes": (obj.description or "")[:3000], "priority": obj.priority or 0, "due": _task_due(obj),
                     "start": _when(obj, "start") if (obj.start_at or obj.start_date) else None,
                     "categories": list(obj.categories or []), "parent": obj.parent_uid})
        from mailr.calendar.todo import split_alarms

        snap["reminders"], snap["reminders_at"] = split_alarms(obj.ics or "", list(obj.alarms or []))
    return snap


def calendar_task(action: str, payload: dict[str, Any], preview: dict[str, Any] | None, result: Any,
                  before: dict[str, Any] | None) -> dict[str, Any] | None:
    """The entry of a calendar or task change that ran (calendar/routes._gated, routes_tasks.gated,
    the assistant's agent/tools._execute)."""
    res = result if isinstance(result, dict) else {}
    uid = payload.get("uid") or res.get("uid")
    ch = dict(payload.get("changes") or {})
    b = before or {}
    if action == "calendar.create":
        name = payload.get("summary") or res.get("summary")
        undo = http("DELETE", f"/calendar/events/{uid}") if uid and not payload.get("attendees") else None
        return entry("calendar", i18n.msg("activity.calendar.created", what=quoted(name)), undo=undo, kind="event", ref=uid, title=name)
    if action == "calendar.update":
        name = ch.get("summary") or b.get("summary") or res.get("summary")
        if str(ch.get("status") or "").upper() == "CANCELLED":
            return entry("calendar", i18n.msg("activity.calendar.cancelled", what=quoted(name)), kind="event", ref=uid, title=name)
        moved = set(ch) <= {"start", "end", "all_day", "tz"}
        undo = None
        if before and (payload.get("scope") or "all") == "all" and not b.get("guests") and set(ch) <= set(SAFE_EVENT) \
                and b.get("description_len", 0) <= 3000:
            undo = http("PATCH", f"/calendar/events/{uid}", {k: b.get(k) for k in ch if k in b})
        return entry("calendar", i18n.msg(f"activity.calendar.{'moved' if moved else 'changed'}", what=quoted(name)), undo=undo,
                     kind="event", ref=uid, title=name)
    if action == "calendar.delete":
        name = b.get("summary")
        undo = None
        if before and (payload.get("scope") or "all") == "all" and not b.get("guests") and not b.get("recurring") \
                and b.get("start") and b.get("description_len", 0) <= 3000:
            undo = http("POST", "/calendar/events", {k: b[k] for k in ("summary", "start", "end", "all_day", "location",
                                                                       "description", "calendar", "tz", "color")
                                                     if b.get(k) not in (None, "")})
        return entry("calendar", i18n.msg("activity.calendar.deleted", what=quoted(name)), undo=undo, kind="event", ref=uid, title=name)
    if action == "tasks.create":
        name = payload.get("summary") or res.get("summary")
        uid = res.get("uid") or uid
        return entry("tasks", i18n.msg("activity.tasks.added", what=quoted(name)), undo=http("DELETE", f"/tasks/{uid}") if uid else None,
                     kind="task", ref=uid, title=name)
    if action == "tasks.complete":
        name = b.get("summary") or res.get("summary")
        if payload.get("op") == "skip":
            return entry("tasks", i18n.msg("activity.tasks.skipped", what=quoted(name)), undo=http("PATCH", f"/tasks/{uid}", {"completed": False}),
                         kind="task", ref=uid, title=name)
        done = ch.get("status") == "completed"
        return entry("tasks", i18n.msg(f"activity.tasks.{'completed' if done else 'reopened'}", what=quoted(name)),
                     undo=http("PATCH", f"/tasks/{uid}", {"completed": not done}), kind="task", ref=uid, title=name)
    if action == "tasks.update":
        name = ch.get("summary") or b.get("summary") or res.get("summary")
        if payload.get("op") == "move":
            return entry("tasks", i18n.msg("activity.tasks.moved", what=quoted(name)), kind="task", ref=uid, title=name)
        undo = None
        if before and set(ch) <= set(SAFE_TASK) and b.get("description_len", 0) <= 3000:
            back: dict[str, Any] = {}
            for k in ch:
                if k == "due" and b.get("due") is None:
                    back["clear_due"] = True
                elif k == "start" and b.get("start") is None:
                    back["clear_start"] = True
                else:
                    back[k] = b.get(k)
            undo = http("PATCH", f"/tasks/{uid}", back)
        return entry("tasks", i18n.msg("activity.tasks.changed", what=quoted(name)), undo=undo, kind="task", ref=uid, title=name)
    if action == "tasks.delete":
        name = b.get("summary")
        undo = None
        if before and not payload.get("cascade") and not b.get("recurring") and b.get("description_len", 0) <= 3000:
            body = {k: b[k] for k in ("summary", "notes", "priority", "due", "start", "categories", "parent")
                    if b.get(k) not in (None, "", [], 0)}
            if b.get("calendar"):
                body["list"] = b["calendar"]
            undo = http("POST", "/tasks", body)
        return entry("tasks", i18n.msg("activity.tasks.deleted", what=quoted(name)), undo=undo, kind="task", ref=uid, title=name)
    return None


# ---------------------------------------------------------------- notes and tables

# The notes operations (notes/ops.py) that make an entry: (phrase, coalesced, keep the undo). The
# phrase is activity.notes.<phrase> ("Created {{what}}").
NOTE_OPS: dict[str, tuple[str, bool, bool]] = {
    "note.create": ("created", False, True), "note.from_email": ("created", False, True),
    "note.from_recording": ("created", False, True), "note.from_template": ("created", False, True),
    "note.duplicate": ("copied", False, True), "note.trash": ("trashed", False, True),
    "note.restore": ("restored", False, True), "note.purge": ("purged", False, False),
    "note.convert": ("converted", False, True), "note.revert": ("reverted", False, False),
    "revision.restore": ("revision_restored", False, False),
    "note.edit": ("edited", True, False), "items.add": ("items_added", True, False),
    "item.patch": ("edited", True, False), "item.delete": ("item_deleted", False, True),
    "items.clear_checked": ("checked_cleared", False, True),
    "items.uncheck_all": ("unchecked_all", False, True), "section.delete": ("section_deleted", False, True),
    "item.to_task": ("item_to_task", False, True), "note.capture": ("captured", True, False),
}


def note(name: str, note_obj: Any, args: dict[str, Any], res: Any) -> tuple[dict[str, Any], bool] | None:
    spec = NOTE_OPS.get(name)
    if spec is None:
        return None
    phrase, coalesce, keep_undo = spec
    r = res if isinstance(res, dict) else {}
    made = r.get("note") if isinstance(r.get("note"), dict) else None
    pid = (note_obj.pid if note_obj is not None else None) or (made or {}).get("id")
    title = (getattr(note_obj, "title", None) if note_obj is not None else None) or (made or {}).get("title") \
        or args.get("title") or ""
    kind_word = "list" if ((made or {}).get("kind") or getattr(note_obj, "kind", None) or args.get("kind")) == "checklist" else "note"
    if name == "item.patch" and set(args) & {"checked"} and not set(args) & {"text", "extra"}:
        phrase = "items_checked" if args.get("checked") else "items_unchecked"
    what = quoted(title, f"a_{kind_word}")
    undo = r.get("undo") if keep_undo and isinstance(r.get("undo"), dict) and r["undo"].get("path") else None
    if undo:
        undo = {k: undo[k] for k in ("method", "path", "body") if k in undo}
    return entry("notes", i18n.msg(f"activity.notes.{phrase}", what=what), undo=undo, kind="note", ref=pid, title=title,
                 key=f"note:{pid}:{name}" if coalesce else None), coalesce


async def record_note(db: AsyncSession, p: Any, name: str, note_obj: Any, args: dict[str, Any], res: Any) -> None:
    from mailr.core import live

    got = note(name, note_obj, args, res)
    if got is None or p is None or getattr(p, "user_id", None) is None:
        return
    act, coalesce = got
    by = live.who(p)
    tok = getattr(p, "token", None)
    await add(db, p.user_id, actor_of(by), f"notes.{name}", act.get("target", {}).get("id", ""), act,
              token_id=tok.id if tok is not None else None, coalesce=coalesce)


# The table changes that make an entry: (phrase, coalesced). The phrase is activity.tables.<phrase>;
# a phrase about rows takes the count (rows_added_one/_other) or, without one, <phrase>_some.
TABLE_ACTS: dict[str, tuple[str, bool]] = {
    "create": ("created", False), "trash": ("trashed", False),
    "restore": ("restored", False), "duplicate": ("copied", False),
    "rows.add": ("rows_added", False), "rows.delete": ("rows_deleted", False),
    "rows.restore": ("rows_restored", False), "rows.edit": ("edited", True),
    "column.delete": ("column_deleted", False), "revert": ("reverted", False),
}


async def record_table(db: AsyncSession, user_id: int, by: str | None, what: str, t: Any, res: Any, *,
                       token_id: int | None = None, n: int | None = None) -> None:
    spec = TABLE_ACTS.get(what)
    if spec is None or t is None:
        return
    phrase, coalesce = spec
    r = res if isinstance(res, dict) else {}
    if what == "rows.edit" and not r.get("undo"):
        return  # nothing changed (the same value saved again)
    undo = r.get("undo") if not coalesce and isinstance(r.get("undo"), dict) and r["undo"].get("path") else None
    if undo:
        undo = {k: undo[k] for k in ("method", "path", "body") if k in undo}
    pid = getattr(t, "pid", None) or getattr(t, "id", None)
    what_ = quoted(getattr(t, "name", ""), "a_table")
    if phrase.startswith("rows_"):
        m = i18n.msg(f"activity.tables.{phrase}", what=what_, count=n) if n else \
            i18n.msg(f"activity.tables.{phrase}_some", what=what_)
    else:
        m = i18n.msg(f"activity.tables.{phrase}", what=what_)
    act = entry("tables", m, undo=undo, kind="table", ref=pid, title=getattr(t, "name", ""),
                key=f"table:{pid}:{what}" if coalesce else None)
    await add(db, user_id, actor_of(by), f"tables.{what}", str(pid or ""), act, token_id=token_id, coalesce=coalesce)


# ---------------------------------------------------------------- the feed

def who_of(actor: str) -> tuple[str, str | None]:
    if actor == "user":
        return "you", None
    if actor == "agent":
        return "ambrose", None
    if actor.startswith("token:"):
        return "agent", actor[6:] or None
    if actor == "admin":
        return "admin", None
    return "system", None


def _out(row: AuditLog, later_keys: set[str], now: datetime, lang: str = i18n.DEFAULT) -> dict[str, Any]:
    who, by = who_of(row.actor or "")
    act = dict((row.result or {}).get(KEY) or {})
    if row.action.startswith("admin_notice."):
        ins = row.inputs or {}
        who, by = "admin", str(ins.get("by") or "") or None
        act = {"app": act.get("app") or "account", "msg": ins.get("msg"),
               "text": str(ins.get("description") or i18n.t("activity.admin.changed", lang))}
    # The phrase in the reader's language; an entry from before the keys keeps its stored English.
    shown = i18n.render(act["msg"], lang, fallback=act.get("text")) if i18n.is_msg(act.get("msg")) else act.get("text")
    undo = act.get("undo")
    request = None
    if isinstance(undo, dict):
        request = _mail_request(undo["mail"]) if isinstance(undo.get("mail"), dict) else (
            {k: undo[k] for k in ("method", "path", "body") if k in undo} if undo.get("path") else None)
    target = act.get("target")
    if isinstance(target, dict) and target.get("kind") == "conversation" and str(target.get("id", "")).isdigit():
        from mailr.core.publicid import ConvId

        target = {**target, "id": ConvId(int(target["id"]))}  # public form, as every conversation id
    via = act.get("via")
    if isinstance(via, dict) and via.get("kind") == "conversation" and str(via.get("id", "")).isdigit():
        from mailr.core.publicid import ConvId

        via = {"kind": "conversation", "id": ConvId(int(via["id"])), "title": short(via.get("title"), 200)}
    else:
        via = None
    key = act.get("key")
    undone = bool(act.get("undone_at"))
    fresh = now - row.created_at <= timedelta(days=UNDO_DAYS)
    superseded = bool(key and key in later_keys)
    offer = request if (request and not undone and fresh and not superseded and row.ok) else None
    return {"id": row.id, "at": row.created_at.isoformat(), "who": who, "by": by,
            "app": act.get("app") if act.get("app") in APPS else "account", "text": shown or row.action,
            "target": target, "count": int(act.get("count") or 1), "undo": offer, "undone": undone,
            "ok": bool(row.ok), **({"via": via} if via else {})}


async def feed(db: AsyncSession, user_id: int, *, cursor: int | None = None, limit: int = 50,
               who: str | None = None, app: str | None = None, lang: str = i18n.DEFAULT) -> dict[str, Any]:
    """The entries, newest first, with their phrases in `lang` (the reader's language)."""
    stmt = select(AuditLog).where(AuditLog.user_id == user_id, text(WHERE))
    if cursor:
        stmt = stmt.where(AuditLog.id < cursor)
    actor = {"you": ["user"], "ambrose": ["agent"], "admin": ["admin"]}.get(who or "")
    if actor:
        stmt = stmt.where(AuditLog.actor.in_(actor))
    elif who == "agents":
        stmt = stmt.where(AuditLog.actor.like("token:%"))
    if app in APPS:
        stmt = stmt.where(text("audit_log.result -> 'activity' ->> 'app' = :app")).params(app=app)
    rows = (await db.execute(stmt.order_by(AuditLog.id.desc()).limit(limit + 1))).scalars().all()
    more = len(rows) > limit
    rows = rows[:limit]
    # "A later change exists": the keys of every entry newer than each row (this page, and any
    # newer entries than the page's first, which the first page has by definition).
    later: set[str] = set()
    if cursor and rows:
        newer = (await db.execute(select(AuditLog.result).where(
            AuditLog.user_id == user_id, AuditLog.id >= cursor, text("audit_log.result ? 'activity'"),
            AuditLog.created_at >= datetime.now(UTC) - timedelta(days=UNDO_DAYS)))).scalars().all()
        later = {((r or {}).get(KEY) or {}).get("key") for r in newer} - {None}
    now = datetime.now(UTC)
    out = []
    for row in rows:
        out.append(_out(row, later, now, lang))
        k = ((row.result or {}).get(KEY) or {}).get("key")
        if k:
            later.add(k)
    return {"items": out, "next_cursor": str(rows[-1].id) if more and rows else None}


async def mark_undone(db: AsyncSession, user_id: int, entry_id: int) -> bool:
    row = await db.get(AuditLog, entry_id)
    if row is None or row.user_id != user_id or KEY not in (row.result or {}):
        return False
    act = dict(row.result[KEY])
    act["undone_at"] = datetime.now(UTC).isoformat()
    await db.execute(update(AuditLog).where(AuditLog.id == entry_id).values(result={**row.result, KEY: act}))
    return True
