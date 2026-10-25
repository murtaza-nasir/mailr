"""Live editing: versioned items, the live change signal and merges.

The items people and agents edit at the same time are drafts, calendar events, tasks, the
assistant's memory notes and filter rules. Each has a version that counts up with every write, by
anyone: the user in a browser tab, the Android app, the built-in assistant, an agent with an API
token. Every write

1. takes the item's lock (`resolve` / `commit` run in the writer's transaction),
2. merges, when the writer worked from an older version (`base_version`), its copy with what is
   stored three ways against the stored copy of that version (core/merge3.py); changes to
   different fields or different paragraphs merge, overlapping ones raise LiveConflict (HTTP 409
   with the base, the stored copy and the writer's copy), never a silent overwrite,
3. records the new version (`commit`) and sends the live event
   {"type": "item", "kind", "id", "version", "by", "origin"} to the user's open apps.

An item stored before this module, or changed by a path that records no version (a sync from
another calendar app), gets its version when it is next read or written (`current`). That "sync"
version is recorded only from a copy read while the item's lock is held: a reader or writer that
loaded the item before another write committed would otherwise record its older copy as the
newest version, and a later merge against it brings old text back (the trace of 13 Oct 2026).
So `current` takes a loader, re-reads the item under the lock when what it loaded first differs
from the newest version, and `lock` re-reads the ORM objects a writer loaded before it.

`by`: "user" (the web app), "device" (the Android app), "assistant", "agent:<token name>".
`origin`: the X-Mailr-Client header of the request, an id per browser tab or device, so a client
can tell its own writes from others.

Assistant and agent writes also send {"type": "editing", "kind", "id", "by", "state": "start" |
"end"} around the write, so an open editor can say "Assistant is editing this draft…" (sent at
once, outside the writer's transaction). The editor may report typing (`typing`); a write asked to
wait for the user (`wait_idle`) holds until they have paused.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import merge3
from mailr.models import ItemVersion

log = logging.getLogger("mailr.live")

KINDS = ("draft", "event", "task", "memory", "rule", "note", "checkitem", "table", "row")
# The text fields of each kind that merge by units; every other field merges as a whole.
TEXT_FIELDS: dict[str, dict[str, str]] = {
    "draft": {"html": "html", "text": "lines"},
    "event": {"description": "lines"},
    "task": {"notes": "lines"},
    "memory": {"text": "lines"},
    "rule": {},
    "note": {"body": "lines"},  # notes/live.py merges notes and items field by field
    "checkitem": {},
    "table": {},  # tables/actions.py merges the schema per column and a row per cell
    "row": {},
}
NOUNS = {"draft": "draft", "event": "event", "task": "task", "memory": "memory", "rule": "filter", "note": "note",
         "checkitem": "item", "table": "table", "row": "row"}

# History kept per item: the newest KEEP versions, and every version younger than KEEP_HOURS,
# but never more than KEEP_MAX.
KEEP = 20
KEEP_HOURS = 24
KEEP_MAX = 100

# Who writes and from which client (set per request by main.py's middleware, per tool call by
# agent/tools.run_tool).
by_var: ContextVar[str | None] = ContextVar("live_by", default=None)
origin_var: ContextVar[str | None] = ContextVar("live_origin", default=None)
# The person behind the write ({"id", "name"}), set per request by core/auth.py. Events carry it
# as `author`, so the other people on a shared note see "Dana is editing".
author_var: ContextVar[dict[str, Any] | None] = ContextVar("live_author", default=None)


# The kinds whose events carry `author` (items people share).
SHARED_KINDS = frozenset({"note", "checkitem", "table", "row"})


def author() -> dict[str, Any] | None:
    return author_var.get()


def _author_of(kind: str) -> dict[str, Any]:
    a = author_var.get()
    return {"author": a} if a and kind in SHARED_KINDS else {}


def who(p: Any = None, *, assistant: bool = False) -> str:
    """The `by` of a write made by principal `p`."""
    tok = getattr(p, "token", None) if p is not None else None
    if tok is not None:
        return f"agent:{(getattr(tok, 'name', '') or str(getattr(tok, 'id', '')))[:60]}"
    if assistant:
        return "assistant"
    return by_var.get() or "user"


def public_id(kind: str, item: str | int) -> Any:
    """The id an app knows the item by (drafts: the public message id)."""
    if kind == "draft":
        from mailr.core import publicid

        return publicid.encode("message", int(item))
    if kind in ("memory", "rule"):
        try:
            return int(item)
        except (TypeError, ValueError):
            return item
    return str(item)


def ref(kind: str, item: str | int, version: int | None) -> dict[str, Any]:
    """What a tool or a route returns about an item it created or changed."""
    from mailr.core.publicid import MsgId

    iid: Any = MsgId(int(item)) if kind == "draft" else (int(item) if kind in ("memory", "rule") else str(item))
    return {"kind": kind, "id": iid, "version": version}


def _canon(v: Any) -> str:
    return json.dumps(v, sort_keys=True, separators=(",", ":"), default=str)


class LiveConflict(Exception):
    """Both writers changed the same field or paragraph since `base`: HTTP 409 with all three."""

    def __init__(self, kind: str, item: str, base: dict[str, Any] | None, base_version: int | None,
                 theirs: dict[str, Any], theirs_version: int, mine: dict[str, Any], fields: list[str],
                 texts: dict[str, list[merge3.Conflict]] | None = None, by: str | None = None):
        super().__init__(f"the {NOUNS.get(kind, kind)} changed since version {base_version}")
        self.kind, self.item = kind, item
        self.base, self.base_version = base, base_version
        self.theirs, self.theirs_version = theirs, theirs_version
        self.mine, self.fields, self.texts, self.by = mine, fields, texts or {}, by

    def body(self) -> dict[str, Any]:
        noun = NOUNS.get(self.kind, self.kind)
        who_ = {"assistant": "the assistant", "device": "another device"}.get(self.by or "", "someone else")
        if (self.by or "").startswith("agent:"):
            who_ = f"the agent {self.by[6:]}"
        return {
            "error": "conflict", "conflict": True, "kind": self.kind,
            "id": public_id(self.kind, self.item),
            "detail": f"This {noun} was changed by {who_} while you edited it; both changed "
                      f"{', '.join(self.fields)}.",
            "fields": self.fields,
            "base": {"version": self.base_version, "content": self.base},
            "theirs": {"version": self.theirs_version, "content": self.theirs, "by": self.by},
            "mine": {"content": self.mine},
            "texts": {k: [c.out() for c in v] for k, v in self.texts.items()},
        }


# ---------------------------------------------------------------- the version store


# What `current` reads the item's editable content from: the content itself (only when the caller
# read it while holding the item's lock), or a loader that reads it from the database again.
Content = dict[str, Any] | Callable[[], Awaitable[dict[str, Any]]]


def _held(db: AsyncSession) -> set[tuple[int, str, str]]:
    """The items whose lock this session's current transaction holds."""
    txn = db.sync_session.get_transaction()
    got = db.info.get("live_locks")
    if got is None or got[0] is not txn:
        got = (txn, set())
        db.info["live_locks"] = got
    return got[1]


def holds(db: AsyncSession, user_id: int, kind: str, item: str | int) -> bool:
    return (int(user_id), kind, str(item)) in _held(db)


async def lock(db: AsyncSession, user_id: int, kind: str, item: str | int, *fresh: Any) -> None:
    """Serialise the writes of one item until the end of the transaction. `fresh`: ORM objects
    the caller loaded before; they are read again once the lock is held, so the caller merges
    with (and stores over) the copy every earlier write committed, never an older one."""
    item = str(item)
    if not holds(db, user_id, kind, item):
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                         {"k": f"live:{user_id}:{kind}:{item}"})
        _held(db).add((int(user_id), kind, item))
    await reread(db, *fresh)


async def reread(db: AsyncSession, *objs: Any) -> None:
    """Read ORM objects from the database again (this session's own changes are flushed first,
    so they are kept). Only the column attributes already loaded are read: a deferred column stays
    as it was (an async session cannot load one on access)."""
    if not objs:
        return
    from sqlalchemy import inspect

    await db.flush()
    for o in objs:
        st = inspect(o)
        names = [a.key for a in st.mapper.column_attrs if a.key not in st.unloaded]
        if names:
            await db.refresh(o, attribute_names=names)


def loader(db: AsyncSession, obj: Any, content: Callable[[Any], Any]) -> Callable[[], Awaitable[dict[str, Any]]]:
    """A loader for `current`: the first call gives `content(obj)` as loaded, every later call
    reads `obj` from the database again first. `content` may be async."""
    calls = {"n": 0}

    async def load() -> dict[str, Any]:
        if calls["n"]:
            await reread(db, obj)
        calls["n"] += 1
        got = content(obj)
        return await got if asyncio.iscoroutine(got) else got

    return load


async def latest(db: AsyncSession, user_id: int, kind: str, item: str) -> ItemVersion | None:
    return (await db.execute(select(ItemVersion).where(
        ItemVersion.user_id == user_id, ItemVersion.kind == kind, ItemVersion.item_id == str(item))
        .order_by(ItemVersion.version.desc()).limit(1))).scalar_one_or_none()


async def at(db: AsyncSession, user_id: int, kind: str, item: str, version: int) -> dict[str, Any] | None:
    row = (await db.execute(select(ItemVersion.content).where(
        ItemVersion.user_id == user_id, ItemVersion.kind == kind, ItemVersion.item_id == str(item),
        ItemVersion.version == int(version)))).scalar_one_or_none()
    return row


def unknown_base_conflicts(changes: dict[str, Any], now: dict[str, Any], lww: Any = ()) -> list[str]:
    """A write from a version that is no longer kept (pruned: older than KEEP and KEEP_HOURS) while
    the item moved on: with no base to merge against, every changed field whose value differs from
    the stored copy is a conflict (HTTP 409), except the fields where the later write wins (`lww`).
    The writer sees the stored copy and decides; nothing is overwritten silently. A field written
    with the value it already has is not a conflict."""
    return [f for f, v in changes.items() if f not in lww and not merge3.same(v, now.get(f))]


async def _insert(db: AsyncSession, user_id: int, kind: str, item: str, version: int, content: dict[str, Any],
                  by: str, origin: str | None) -> None:
    db.add(ItemVersion(user_id=user_id, kind=kind, item_id=str(item), version=version,
                       content=json.loads(_canon(content)), by=by[:80], origin=(origin or None) and origin[:64]))
    await db.flush()
    await db.execute(text(
        "DELETE FROM item_versions WHERE user_id = :u AND kind = :k AND item_id = :i AND version <= :old "
        "AND (created_at < now() - make_interval(hours => :h) OR version <= :cap)"),
        {"u": user_id, "k": kind, "i": str(item), "old": version - KEEP, "h": KEEP_HOURS, "cap": version - KEEP_MAX})


async def current(db: AsyncSession, user_id: int, kind: str, item: str | int, live: Content) -> int:
    """The version of the item as stored now. See `state`."""
    return (await state(db, user_id, kind, item, live))[0]


async def state(db: AsyncSession, user_id: int, kind: str, item: str | int, live: Content
                ) -> tuple[int, dict[str, Any]]:
    """(version, content) of the item as stored now, a pair that belongs together. An item without
    a version, or changed by a path that records none, gets one here ("seed", "sync").

    `live` is a loader (`loader(...)`): it is called once, and called again under the item's lock
    when what it gave differs from the newest version, since a write may have committed between
    the caller's read of the item and the read of its versions. Only that second copy, read while
    no recorded write can be in flight, is ever recorded. A caller that already holds the lock
    and read the item after taking it (`lock(..., obj)`) may pass the content itself."""
    item = str(item)
    held = holds(db, user_id, kind, item)
    if not held and not callable(live):
        raise TypeError("live.current: pass a loader, or hold the item's lock (live.lock with the objects)")
    content = await live() if callable(live) else live
    row = await latest(db, user_id, kind, item)
    if row is not None and _canon(row.content) == _canon(content):
        return row.version, content
    if callable(live):
        if not held:
            await lock(db, user_id, kind, item)
        # Read again: every write that recorded a version has committed (or waits behind us).
        row = await latest(db, user_id, kind, item)
        content = await live()
        if row is not None and _canon(row.content) == _canon(content):
            return row.version, content
    v = row.version + 1 if row is not None else 1
    await _insert(db, user_id, kind, item, v, content, "sync" if row is not None else "seed", None)
    return v, content


async def resolve(db: AsyncSession, user_id: int, kind: str, item: str | int, live: Content,
                  mine: dict[str, Any], base_version: int | None, *fresh: Any) -> tuple[dict[str, Any], bool]:
    """The copy to store for a write of `mine` made from `base_version`, and whether it is a
    merge: `mine` itself when nothing changed since then (or no base was given: the writer
    replaces), else the three-way merge with the stored copy `live`. Takes the item's lock and
    reads `fresh` (the ORM objects `live` is made from) again under it; `live` is evaluated after
    that. Raises LiveConflict."""
    item = str(item)
    await lock(db, user_id, kind, item, *fresh)
    head, live = await state(db, user_id, kind, item, live)
    if base_version is None or int(base_version) >= head:
        return mine, False
    base = await at(db, user_id, kind, item, int(base_version))
    r = merge3.merge_fields(base, mine, live, TEXT_FIELDS.get(kind))
    if not r.ok:
        row = await latest(db, user_id, kind, item)
        raise LiveConflict(kind, item, base, int(base_version) if base is not None else None, live, head, mine,
                           r.conflicts, r.texts, by=row.by if row is not None else None)
    return r.merged, True


async def commit(db: AsyncSession, user_id: int, kind: str, item: str | int, content: dict[str, Any], *,
                 by: str | None = None, origin: str | None = None, quiet: bool = False,
                 extra: dict[str, Any] | None = None, audience: list[int] | None = None) -> int:
    """Record the item's new content (after the write) and send the live event; returns its
    version. Writing the same content again keeps the version. `extra` adds fields to the event
    (a checklist item names its note); `audience` sends it to these users instead of the owner
    of the version store (a note's viewers)."""
    item = str(item)
    await lock(db, user_id, kind, item)
    row = await latest(db, user_id, kind, item)
    if row is not None and _canon(row.content) == _canon(content):
        return row.version
    v = row.version + 1 if row is not None else 1
    by = by or who()
    origin = origin if origin is not None else origin_var.get()
    await _insert(db, user_id, kind, item, v, content, by, origin)
    if not quiet:
        ev = {"type": "item", "kind": kind, "id": public_id(kind, item), "version": v, "by": by, "origin": origin,
              **_author_of(kind), **(extra or {})}
        for uid in audience if audience is not None else [user_id]:
            await _notify(db, uid, ev)
    return v


async def forget(db: AsyncSession, user_id: int, kind: str, item: str | int, *, by: str | None = None,
                 extra: dict[str, Any] | None = None, audience: list[int] | None = None) -> None:
    """The item is gone (deleted, sent): drop its history and tell the open apps."""
    item = str(item)
    await db.execute(delete(ItemVersion).where(ItemVersion.user_id == user_id, ItemVersion.kind == kind,
                                               ItemVersion.item_id == item))
    ev = {"type": "item", "kind": kind, "id": public_id(kind, item), "version": None, "deleted": True,
          "by": by or who(), "origin": origin_var.get(),
          **_author_of(kind), **(extra or {})}
    for uid in audience if audience is not None else [user_id]:
        await _notify(db, uid, ev)


async def _notify(db: AsyncSession, user_id: int, event: dict[str, Any]) -> None:
    from mailr.core.events import notify

    await notify(db, user_id, event)


# ---------------------------------------------------------------- "Assistant is editing"


async def editing(user_id: int, kind: str, item: str | int, by: str, state: str) -> None:
    """Tell the user's open apps that an agent starts (or ended) a write to the item. Sent at
    once, in a connection of its own (the write's transaction commits later)."""
    from mailr.core.events import notify
    from mailr.db import sessionmaker

    try:
        async with sessionmaker()() as db:
            await notify(db, user_id, {"type": "editing", "kind": kind, "id": public_id(kind, item), "by": by,
                                       "state": state})
            await db.commit()
    except Exception as e:  # noqa: BLE001  the banner is a courtesy; the write goes on
        log.warning("editing notice failed: %s", e)


# Typing reports from the editors: (user, kind, item id as the app knows it) -> monotonic time.
# Filled from the change stream (core/events.Hub), so every API process sees every report.
_typing: dict[tuple[int, str, str], float] = {}
IDLE_SECONDS = 2.0
IDLE_TIMEOUT = 20.0


def saw_typing(user_id: int, kind: str, item: Any) -> None:
    _typing[(int(user_id), str(kind), str(item))] = time.monotonic()
    if len(_typing) > 5000:  # forget the oldest reports
        for k, _ in sorted(_typing.items(), key=lambda kv: kv[1])[:1000]:
            _typing.pop(k, None)


async def wait_idle(user_id: int, kind: str, item: str | int, *, idle: float = IDLE_SECONDS,
                    timeout: float = IDLE_TIMEOUT) -> bool:
    """Hold until the user has not typed in the item's editor for `idle` seconds (at most
    `timeout`). True when they paused, False on the timeout."""
    k = (int(user_id), kind, str(public_id(kind, item)))
    end = time.monotonic() + timeout
    while True:
        last = _typing.get(k)
        quiet = time.monotonic() - last if last is not None else idle
        if quiet >= idle:
            return True
        if time.monotonic() >= end:
            return False
        await asyncio.sleep(min(idle - quiet, max(0.05, end - time.monotonic())) + 0.01)


_ORIGIN_OK = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_:.")


class LiveClientMiddleware:
    """Per request: the client id (X-Mailr-Client: one per browser tab or device) and whether the
    signed-in user writes from the Android app ("device")."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        origin = ua = ""
        for k, v in scope.get("headers") or []:
            if k == b"x-mailr-client":
                origin = v.decode("latin-1")[:64]
            elif k == b"user-agent":
                ua = v.decode("latin-1")
        t1 = origin_var.set(origin if origin and set(origin) <= _ORIGIN_OK else None)
        t2 = by_var.set("device" if ua.startswith("mailr-android") else None)
        try:
            return await self.app(scope, receive, send)
        finally:
            origin_var.reset(t1)
            by_var.reset(t2)
