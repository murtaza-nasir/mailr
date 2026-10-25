"""Today: one answer with what today holds, for the Android Today widget and any
client that wants it in one request. The web's Today view (Calendar's first view) reads the same
parts from the routes each app already uses, and this route for the mail waiting on a reply.

GET /today?date=YYYY-MM-DD (default: today in the user's zone)
    events     today's events by start; cancelled ones and the ones I declined are left out
    tasks      open tasks due today or overdue (the server's "today" smart list, without the
               "started" ones that have no due), at most 50
    reminders  missed reminders (fired late, not handled)
    followups  mail waiting on a reply: conversations that came back with no reply (nudged), and
               follow-ups due by the end of the day
    approvals  requests waiting for the user's decision
    counts     the length of each list

Today never waits for a calendar server: a CalDAV account checked more than read_refresh_seconds()
ago is refreshed in the background, and the events and tasks come from the mirror as it is. Only an account's first sync is waited for.

Each part that fails (no calendar connected, a calendar server that is down) is an empty list with
an `errors` entry, so the widget still shows the rest. Session and agent tokens with `read` both
work; a token sees only what it may see (the calendar and task reads take the principal).
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core.auth import Principal, scope
from mailr.core.publicid import ApprovalId, ConvId
from mailr.db import get_db
from mailr.models import Conversation

log = logging.getLogger("mailr.today")

router = APIRouter(tags=["today"])

MAX_TASKS = 50
MAX_FOLLOWUPS = 30


def _day_bounds(day: date, zone) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=zone)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def _who(participants: list[dict[str, Any]] | None, me: set[str]) -> str:
    others = [x for x in participants or [] if (x.get("email") or "").lower() not in me]
    first = others[0] if others else {}
    return first.get("name") or first.get("email") or ""


async def followups(db: AsyncSession, p: Principal, end: datetime) -> list[dict[str, Any]]:
    """Conversations waiting on a reply: nudged ones (came back with no reply), and follow-ups
    that come due by `end`, newest first."""
    from mailr.calendar.mirror import my_addresses
    from mailr.core.visibility import visible_conversation_ids

    rows = (await db.execute(
        select(Conversation).where(
            Conversation.user_id == p.user_id,
            or_(Conversation.nudged_at.is_not(None),
                (Conversation.follow_up_at.is_not(None)) & (Conversation.follow_up_at < end)),
            Conversation.id.in_(visible_conversation_ids(p)))
        .order_by(Conversation.nudged_at.desc().nulls_last(), Conversation.follow_up_at)
        .limit(MAX_FOLLOWUPS))).scalars().all()
    me = await my_addresses(db, p.user_id)
    # Whom the reply is awaited from: the other senders, else the people my last message went to.
    sent_to: dict[int, list[dict[str, Any]]] = {}
    if rows:
        from mailr.models import Message

        for cid, to in (await db.execute(
                select(Message.conversation_id, Message.to).where(
                    Message.conversation_id.in_([c.id for c in rows]), Message.sent_by_me.is_(True),
                    Message.deleted.is_(False)).order_by(Message.date))).all():
            sent_to[cid] = list(to or [])
    return [{"conversation_id": ConvId(c.id), "subject": c.subject or "",
             "who": _who(c.participants, me) or _who(sent_to.get(c.id), me),
             "since": (c.follow_up_since or c.nudged_at).isoformat() if (c.follow_up_since or c.nudged_at) else None,
             "due_at": c.follow_up_at.isoformat() if c.follow_up_at else None, "nudged": c.nudged_at is not None}
            for c in rows]


# ACT calendar.today_view @api
@router.get("/today")
async def today(day: str | None = Query(None, alias="date"), p: Principal = Depends(scope("read")),
                db: AsyncSession = Depends(get_db)):
    from mailr.calendar import client as cal
    from mailr.calendar import mirror, planner, todo
    from mailr.calendar import tz as tzmod
    from mailr.calendar.routes import _my_addresses, _with_my_status

    zone = await tzmod.user_zone(db, p.user_id)
    now = datetime.now(UTC)
    try:
        d = date.fromisoformat(day) if day else now.astimezone(zone).date()
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD") from None  # i18n: internal
    start, end = _day_bounds(d, zone)
    errors: dict[str, str] = {}
    connected = await cal.has_any_calendar(db, p.user_id, p=p)
    if connected:
        # Once for both parts, and without waiting on the calendar servers: an account checked
        # more than read_refresh_seconds() ago is refreshed in the background (the calendar views
        # wait up to 3 s per account for it; Today waited twice, 6.4 s on 14 Oct 2026).
        await mirror.ensure_synced(db, p.user_id, p, wait=False)

    events: list[dict[str, Any]] = []
    if connected:
        try:
            evs = _with_my_status(await cal.agenda(db, p.user_id, start, end, p=p, synced=True),
                                  await _my_addresses(db, p))
            for e in evs:
                if (e.get("status") or "").upper() == "CANCELLED" or (e.get("my_status") or "").upper() == "DECLINED":
                    continue
                events.append({"uid": e["uid"], "occurrence": e.get("occurrence"), "summary": e.get("summary") or "",
                               "start": e.get("start"), "end": e.get("end"), "all_day": bool(e.get("all_day")),
                               "location": e.get("location") or "", "calendar": e.get("calendar"),
                               "colour": e.get("color") or e.get("calendar_color"), "my_status": e.get("my_status"),
                               "conference": planner.join_url(e.get("url"), e.get("location"), e.get("description"))})
            events.sort(key=lambda e: (not e["all_day"], str(e["start"] or "")))
        except Exception as e:  # noqa: BLE001
            log.info("today: events for user %s failed: %s", p.user_id, e)
            errors["events"] = "Couldn't read the calendar."

    tasks: list[dict[str, Any]] = []
    if connected:
        try:
            res = await todo.list_tasks(db, p.user, p, smart="today", status="open", limit=200, synced=True)
            for t in res["items"]:
                if not t.get("due"):
                    continue
                due = str(t["due"])
                due_day = date.fromisoformat(due[:10]) if len(due) == 10 else \
                    datetime.fromisoformat(due).astimezone(zone).date()
                if due_day > d:
                    continue
                # Overdue: due before this day, or a time today that has passed.
                overdue = due_day < d or (len(due) > 10 and datetime.fromisoformat(due) < now)
                tasks.append({"uid": t["uid"], "summary": t.get("summary") or "", "due": t["due"],
                              "due_all_day": bool(t.get("due_all_day")), "overdue": overdue,
                              "list": t.get("list"), "priority": t.get("priority") or 0, "etag": t.get("etag"),
                              "recurring": bool(t.get("recurring"))})
                if len(tasks) >= MAX_TASKS:
                    break
        except Exception as e:  # noqa: BLE001
            log.info("today: tasks for user %s failed: %s", p.user_id, e)
            errors["tasks"] = "Couldn't read the tasks."

    reminders = [{"key": r["key"], "title": r.get("title") or "", "kind": r.get("kind"), "at": r.get("fire_at"),
                  "uid": r.get("uid"), "occurrence": r.get("occurrence"), "missed": True}
                 for r in await planner.active(db, p.user_id, p) if r.get("missed")]

    fups = await followups(db, p, end)

    from mailr.models import PendingAction

    stmt = select(PendingAction).where(PendingAction.user_id == p.user_id, PendingAction.status == "pending")
    if p.token is not None:
        stmt = stmt.where(PendingAction.token_id == p.token.id)
    pend = (await db.execute(stmt.order_by(PendingAction.id.desc()).limit(50))).scalars().all()
    approvals = [{"id": ApprovalId(a.id), "title": (a.preview or {}).get("title") or a.action, "kind": a.action,
                  "created_at": a.created_at.isoformat() if a.created_at else None} for a in pend]

    await db.commit()
    out = {"date": d.isoformat(), "zone": tzmod.zone_name(zone), "connected": connected,
           "events": events, "tasks": tasks, "reminders": reminders, "followups": fups, "approvals": approvals,
           "counts": {"events": len(events), "tasks": len(tasks), "reminders": len(reminders),
                      "followups": len(fups), "approvals": len(approvals)}}
    if errors:
        out["errors"] = errors
    return out
