"""Reminder state shared by every device.

Android plans and fires its own reminders; the server plans and fires them for the web
(calendar/planner.py). What happens to a fired reminder is recorded here, so a reminder handled
on one device disappears on the others:

    POST /reminders/snooze   {key, until}                     ACT task.snooze, ACT event.snooze
    POST /reminders/handle   {keys}                           ACT reminder.dismiss, ACT reminder.dismiss_all
    POST /reminders/add      {kind:"e", uid, occurrence, minutes, handle?}   ACT event.remind_before
    GET  /reminders/state    ?since=<iso>                     ACT reminder.sync
    GET  /reminders/settings, PUT /reminders/settings          the defaults both planners use
    GET  /reminders/active                                    the web tray (fired, not handled)
    POST /reminders/act      {token, action, minutes?}        Web Push buttons (no session)
    GET  /push/webpush/key, POST /push/webpush, DELETE /push/webpush   ACT notification.subscribe

Keys in older forms (20261002T150000Z, offsets) are normalised to the canonical string in every
route (section 1). Every change records a `reminder` change (SSE, the
/changes feed, push), which devices apply, and updates the server's schedule.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.calendar import ical
from mailr.core.auth import Principal, current, scope
from mailr.core.events import record_change
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.models import PushTarget, ReminderState, User

router = APIRouter(tags=["reminders"])

KEY = re.compile(r"^(?P<kind>[et])\|(?P<uid>[^|]{1,300})\|(?P<occ>[^|]{0,40})\|(?P<min>-?\d{1,6})$")
# A note reminder (notes/reminders.py): n|<note pid>|abs|<instant>.
NOTE_KEY = re.compile(r"^n\|(?P<uid>n[0-9a-z]{1,20})\|abs\|(?P<at>[^|]{8,40})$")
KINDS = ("e", "t", "n")
KEEP_DAYS = 30


def _parse(key: str) -> dict[str, Any]:
    m = NOTE_KEY.match(key or "")
    if m:
        at = ical.normalize_occurrence(m["at"])
        if len(at) <= 10:  # a date alone is not an instant
            raise HTTPException(400, f"not a reminder key: {key[:60]!r}")  # i18n: internal
        return {"kind": "n", "uid": m["uid"], "occurrence": at, "minutes": 0}
    m = KEY.match(key or "")
    if not m:
        raise HTTPException(400, f"not a reminder key: {key[:60]!r}")  # i18n: internal
    return {"kind": m["kind"], "uid": m["uid"], "occurrence": ical.normalize_occurrence(m["occ"]),
            "minutes": int(m["min"])}


def _norm(key: str, tz: tzinfo | None = None) -> str:
    """The canonical key (a naive time in an old key is read in the user's zone)."""
    parts = _parse(key)
    if parts["kind"] == "n":
        return f"n|{parts['uid']}|abs|{parts['occurrence']}"
    return ical.normalize_key(key, tz)


async def _zone(db: AsyncSession, user_id: int) -> tzinfo:
    from mailr.calendar import tz as tzmod

    return await tzmod.user_zone(db, user_id)


async def _schedule(db: AsyncSession, user_id: int, rows: list[ReminderState]) -> None:
    """Keep the server's plan in step with the states (snooze and one-off rows)."""
    from mailr.calendar import planner

    await planner.sync_states(db, user_id, rows)


def _out(r: ReminderState) -> dict[str, Any]:
    return {"key": r.key, "kind": r.kind, "uid": r.uid, "occurrence": r.occurrence, "minutes": r.minutes,
            "state": r.state, "snoozed_until": r.snoozed_until.isoformat() if r.snoozed_until else None,
            "oneoff": r.oneoff, "device": r.device, "updated_at": r.updated_at.isoformat() if r.updated_at else None}


async def _upsert(db: AsyncSession, user_id: int, key: str, *, state: str, device: str,
                  snoozed_until: datetime | None = None, oneoff: bool | None = None) -> ReminderState:
    row = (await db.execute(select(ReminderState).where(ReminderState.user_id == user_id,
                                                        ReminderState.key == key))).scalar_one_or_none()
    if row is None:
        row = ReminderState(user_id=user_id, key=key, **_parse(key), state=state, oneoff=bool(oneoff))
        db.add(row)
    row.state, row.snoozed_until, row.device = state, snoozed_until, device[:60]
    if oneoff is not None:
        row.oneoff = oneoff
    row.updated_at = datetime.now(UTC)
    await db.flush()
    return row


def _device(p: Principal) -> str:
    return f"token:{p.token.id}" if p.token is not None else "session"


class SnoozeIn(BaseModel):
    key: str = Field(max_length=400)
    until: datetime


@router.post("/reminders/snooze")
async def snooze(body: SnoozeIn, p: Principal = Depends(scope("calendar")), db: AsyncSession = Depends(get_db)):
    # ACT task.snooze @api
    # ACT event.snooze @api
    """Snooze a reminder: it fires again at `until`; the record is unchanged.

    A snooze whose time passed more than a minute ago changes nothing and succeeds: it comes from
    a phone that queued it offline, and that phone has already reminded the user at `until`.
    Applying it late could undo a dismissal made since on another device. The answer is the
    current state with `"elapsed": true`, so a replay never fails and is safe to repeat."""
    # ACT task.snooze, ACT event.snooze
    zone = await _zone(db, p.user_id)
    until = body.until if body.until.tzinfo else body.until.replace(tzinfo=zone)
    if until > datetime.now(UTC) + timedelta(days=366):
        raise ApiError(400, "reminders.snooze_too_far")
    key = _norm(body.key, zone)
    if until <= datetime.now(UTC) - timedelta(minutes=1):
        row = (await db.execute(select(ReminderState).where(ReminderState.user_id == p.user_id,
                                                            ReminderState.key == key))).scalar_one_or_none()
        out = _out(row) if row is not None else {"key": key, **_parse(key), "state": "pending", "snoozed_until": None}
        return {**out, "elapsed": True}
    row = await _upsert(db, p.user_id, key, state="snoozed", snoozed_until=until, device=_device(p))
    await record_change(db, p.user_id, "reminder", row.id)
    await _schedule(db, p.user_id, [row])
    await db.commit()
    return _out(row)


class HandleIn(BaseModel):
    keys: list[str] = Field(min_length=1, max_length=500)  # Clear all sends the whole tray
    undo: bool = False  # restore: the reminder shows again (state removed)


@router.post("/reminders/handle")
async def handle(body: HandleIn, p: Principal = Depends(scope("calendar")), db: AsyncSession = Depends(get_db)):
    # ACT reminder.dismiss @api
    # ACT reminder.dismiss_all @api
    """Done with these reminders (dismissed, or their task or event acted on). The tray's
    Clear all sends every key it lists in one call.
    Keys are read under the caller's account only, and a repeat changes nothing new.
    undo=true forgets the state again, so the reminder is shown once more."""
    # ACT reminder.dismiss, ACT reminder.dismiss_all
    zone = await _zone(db, p.user_id)
    keys = list(dict.fromkeys(_norm(k, zone) for k in body.keys))
    return await handle_keys(db, p.user_id, keys, device=_device(p), undo=body.undo)


async def handle_keys(db: AsyncSession, user_id: int, keys: list[str], *, device: str, undo: bool = False
                      ) -> dict[str, Any]:
    ids: list[int] = []
    rows: list[ReminderState] = []
    if undo:
        rows = list((await db.execute(select(ReminderState).where(ReminderState.user_id == user_id,
                                                                  ReminderState.key.in_(keys)))).scalars().all())
        for r in rows:  # back to planned: it shows again when its time has come (one-offs stay one-offs)
            r.state, r.snoozed_until, r.updated_at = "pending", None, datetime.now(UTC)
            ids.append(r.id)
    else:
        for k in keys:
            row = await _upsert(db, user_id, k, state="handled", device=device)
            rows.append(row)
            ids.append(row.id)
    await record_change(db, user_id, "reminder", ids)
    await _schedule(db, user_id, rows)
    await db.commit()
    return {"ok": True, "count": len(ids)}


class AddIn(BaseModel):
    kind: str = Field(pattern="^e$")
    uid: str = Field(min_length=1, max_length=300)
    occurrence: str = Field("", max_length=40)
    minutes: int = Field(ge=0, le=60 * 24 * 14)
    handle: list[str] = Field(default_factory=list, max_length=20)  # the fired reminder(s) this replaces


@router.post("/reminders/add")
async def add(body: AddIn, p: Principal = Depends(scope("calendar")), db: AsyncSession = Depends(get_db)):
    # ACT event.remind_before @api
    """A one-off reminder for this occurrence, `minutes` before it starts.
    The reminders in `handle` (the one being acted on) become handled."""
    # ACT event.remind_before
    zone = await _zone(db, p.user_id)
    key = _norm(f"e|{body.uid}|{body.occurrence}|{body.minutes}", zone)
    row = await _upsert(db, p.user_id, key, state="pending", device=_device(p), oneoff=True)
    ids, rows = [row.id], [row]
    for k in dict.fromkeys(_norm(k, zone) for k in body.handle):
        if k != key:
            other = await _upsert(db, p.user_id, k, state="handled", device=_device(p))
            ids.append(other.id)
            rows.append(other)
    await record_change(db, p.user_id, "reminder", ids)
    await _schedule(db, p.user_id, rows)
    await db.commit()
    return _out(row)


@router.get("/reminders/state")
async def state(since: datetime | None = None, limit: int = Query(500, ge=1, le=2000),
                kinds: str = Query("e,t", max_length=10, pattern="^[etn](,[etn])*$"),
                p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    # ACT reminder.sync @api
    """Reminder states changed since `since` (all when omitted), oldest first.
    `next` is the cursor for the following call. `kinds` names the kinds of key the device plans:
    `e,t` by default (events and tasks); a phone that plans note reminders asks for `e,t,n`, so an
    older phone never meets an `n|` key it cannot read."""
    # ACT reminder.sync
    q = select(ReminderState).where(ReminderState.user_id == p.user_id,
                                    ReminderState.kind.in_([k for k in kinds.split(",") if k in KINDS]))
    if since is not None:
        q = q.where(ReminderState.updated_at > (since if since.tzinfo else since.replace(tzinfo=UTC)))
    rows = (await db.execute(q.order_by(ReminderState.updated_at, ReminderState.id).limit(limit))).scalars().all()
    return {"items": [_out(r) for r in rows],
            "next": rows[-1].updated_at.isoformat() if rows else (since.isoformat() if since else None)}


async def prune(db: AsyncSession) -> int:
    """Drop states whose reminders are long past (called by the periodic cleanup)."""
    res = await db.execute(delete(ReminderState).where(
        ReminderState.updated_at < datetime.now(UTC) - timedelta(days=KEEP_DAYS),
        ReminderState.state != "snoozed"))
    return res.rowcount or 0


# ---------------- settings, the web tray and Web Push ----------------


@router.get("/reminders/settings")
async def get_settings_(p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """The defaults the server planner and the phone both plan with; the keys match only when
    both use the same values."""
    from mailr.calendar import planner

    return planner.settings_of(await db.get(User, p.user_id))


class WebPrefsIn(BaseModel):
    desktop: bool | None = None
    push: bool | None = None


class RepeatIn(BaseModel):
    """Remind again: every `every_min` minutes, at most `max` times."""
    enabled: bool | None = None
    every_min: Literal[5, 10, 15, 30] | None = None
    max: int | None = Field(None, ge=1, le=3)


class ReminderSettingsIn(BaseModel):
    events: bool | None = None
    tasks: bool | None = None
    default_timed_event: int | None = Field(None, ge=-1440, le=60 * 24 * 28)
    default_all_day_event: int | None = Field(None, ge=-1440, le=60 * 24 * 28)
    default_task: bool | None = None
    morning_hour: int | None = Field(None, ge=0, le=23)
    web: WebPrefsIn | None = None
    missed: bool | None = None  # show a reminder that fires late ("Missed: …")
    sound: bool | None = None  # play a sound with a reminder (clients; the payload carries it)
    repeat: RepeatIn | None = None  # Remind again; the server repeats only to the web


@router.put("/reminders/settings")
async def put_settings(body: ReminderSettingsIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """Partial update. `default_timed_event` / `default_all_day_event` set to null mean "none"
    (send the key with null); a key left out keeps its value. Every item is planned again."""
    from mailr.calendar import planner

    if p.is_token:
        raise ApiError(403, "reminders.settings_user_only")
    # Locked until the commit, like PATCH /me/settings: a save beside another settings save must not
    # write back an older copy of the settings.
    user = await db.get(User, p.user_id, with_for_update=True, populate_existing=True)
    cur = planner.settings_of(user)
    sent = body.model_dump(exclude_unset=True)
    for k, v in sent.items():
        if k in ("web", "repeat"):
            cur[k] = {**cur[k], **{a: b for a, b in (v or {}).items() if b is not None}}
        elif v is not None or k in ("default_timed_event", "default_all_day_event"):
            cur[k] = v
    user.settings = {**(user.settings or {}), "reminders": cur}
    await db.flush()
    await planner.replan_user(db, p.user_id)
    await db.commit()
    return planner.settings_of(user)


@router.get("/reminders/active")
async def active(now: datetime | None = None, p: Principal = Depends(scope("read")),
                 db: AsyncSession = Depends(get_db)):
    """The web tray: reminders the server fired that are not handled, and snoozes that have
    come due (missed ones are flagged `missed`). `now` is the asking page's clock: which snoozes
    have come due and which events are over is judged by it, as the page shows them ("Starts in
    54 min"); the server's clock when absent."""
    from mailr.calendar import planner

    if now is not None and now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return {"items": await planner.active(db, p.user_id, p, now=now)}


# ---- tokens for the buttons of a Web Push notification ----

ACT_TTL = timedelta(days=7)


def _act_key() -> bytes:
    from mailr.core.crypto import _instance_key

    return hashlib.sha256(b"mailr-reminder-act" + _instance_key()).digest()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


ACT_ACTIONS = ("snooze", "done", "dismiss")


def act_token(user_id: int, key: str, action: str, ttl: timedelta = ACT_TTL) -> str:
    """An HMAC over user, key, the one action it allows and expiry: lets a service worker press
    one button of one reminder without the session's CSRF header. A token minted for "snooze"
    cannot complete the task."""
    if action not in ACT_ACTIONS:
        raise ValueError(f"unknown reminder action {action}")
    exp = int((datetime.now(UTC) + ttl).timestamp())
    body = f"{user_id}.{exp}.{_b64(key.encode())}.{action}"
    sig = _b64(hmac.new(_act_key(), body.encode(), hashlib.sha256).digest()[:24])
    return f"{body}.{sig}"


def act_tokens(user_id: int, key: str, ttl: timedelta = ACT_TTL) -> dict[str, str]:
    """One token per button (the Web Push payload's `tokens`)."""
    return {a: act_token(user_id, key, a, ttl) for a in ACT_ACTIONS}


def read_act_token(token: str, action: str) -> tuple[int, str]:
    try:
        uid, exp, k, bound, sig = token.split(".")
        body = f"{uid}.{exp}.{k}.{bound}"
        want = _b64(hmac.new(_act_key(), body.encode(), hashlib.sha256).digest()[:24])
        if not hmac.compare_digest(want, sig):
            raise ValueError("signature")
        if not hmac.compare_digest(bound, action):
            raise ValueError("this token is for another action")
        if int(exp) < datetime.now(UTC).timestamp():
            raise ApiError(410, "reminders.notification_expired")
        return int(uid), _unb64(k).decode()
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        raise ApiError(403, "reminders.invalid_token") from None


class ActIn(BaseModel):
    token: str = Field(min_length=10, max_length=1200)
    action: str = Field(pattern="^(snooze|done|dismiss)$")
    minutes: int | None = Field(None, ge=1, le=60 * 24 * 7)


@router.post("/reminders/act")
async def act(body: ActIn, db: AsyncSession = Depends(get_db)):
    """A button on a Web Push notification (no session: the signed token names the user and the
    reminder): snooze, dismiss or done ("done" on a task completes it; on an event it is a
    dismiss)."""
    # ACT task.snooze @api, ACT event.snooze @api, ACT reminder.dismiss @api, ACT task.complete @api
    user_id, key = read_act_token(body.token, body.action)
    user = await db.get(User, user_id)
    if user is None or user.disabled:
        raise ApiError(403, "reminders.invalid_token")
    parts = _parse(key)
    if body.action == "snooze":
        minutes = body.minutes or (5 if parts["kind"] == "e" else 60)
        row = await _upsert(db, user_id, key, state="snoozed",
                            snoozed_until=datetime.now(UTC) + timedelta(minutes=minutes), device="webpush")
        await record_change(db, user_id, "reminder", row.id)
        await _schedule(db, user_id, [row])
        await db.commit()
        return _out(row)
    if body.action == "done" and parts["kind"] == "t":
        from mailr.calendar import writer

        try:
            await writer.update_task(db, user_id, parts["uid"], {"status": "completed"})
        except writer.Gone:
            pass
        except writer.Conflict:
            raise ApiError(409, "reminders.task_changed") from None
    return await handle_keys(db, user_id, [key], device="webpush")


# ---- Web Push subscriptions ----


class WebPushKeys(BaseModel):
    p256dh: str = Field(min_length=20, max_length=200, pattern=r"^[A-Za-z0-9_\-=]+$")
    auth: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_\-=]+$")


class WebPushIn(BaseModel):
    endpoint: str = Field(min_length=8, max_length=2000)
    keys: WebPushKeys
    device: str = Field("", max_length=200)
    events: list[str] | None = None  # default: reminders only


def _push_owner(p: Principal) -> None:
    if p.is_token:
        raise ApiError(403, "reminders.push_user_only")


@router.get("/push/webpush/key")
async def webpush_key(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """The VAPID application server key for PushManager.subscribe (base64url)."""
    from mailr.core import webpush

    _push_owner(p)
    pub = await webpush.public_key(db)
    await db.commit()
    return {"public_key": pub}


# ACT notification.subscribe @api
@router.post("/push/webpush")
async def webpush_subscribe(body: WebPushIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """Store this browser's push subscription. It receives reminders (opted in on creation)."""
    from mailr.api.push_routes import target_out
    from mailr.core.netguard import UnsafeTarget, check_url, private_ok_for
    from mailr.core.push import EVENTS

    _push_owner(p)
    if not body.endpoint.startswith("https://"):
        raise HTTPException(400, "a push endpoint must be https")  # i18n: internal
    try:
        await check_url(body.endpoint, private_ok=private_ok_for(p.user))
    except UnsafeTarget as e:
        raise HTTPException(400, str(e)) from None
    events = ["reminder"] if body.events is None else list(dict.fromkeys(body.events))
    if set(events) - set(EVENTS):
        raise HTTPException(400, f"unknown events: {sorted(set(events) - set(EVENTS))}")  # i18n: internal
    t = (await db.execute(select(PushTarget).where(PushTarget.user_id == p.user_id,
                                                   PushTarget.endpoint == body.endpoint))).scalar_one_or_none()
    if t is None:
        t = PushTarget(user_id=p.user_id, endpoint=body.endpoint)
        db.add(t)
    t.kind, t.device, t.events = "webpush", body.device[:200], events
    t.p256dh, t.auth = body.keys.p256dh, body.keys.auth
    t.session_id = p.session.id if p.session else None  # it ends with this sign-in
    await db.commit()
    return target_out(t)


@router.delete("/push/webpush")
async def webpush_unsubscribe(endpoint: str = Query(min_length=8, max_length=2000), p: Principal = Depends(current),
                              db: AsyncSession = Depends(get_db)):
    """Turn off notifications in this browser: its push subscription is forgotten."""
    # ACT notification.subscribe (turning it off)
    _push_owner(p)
    res = await db.execute(delete(PushTarget).where(PushTarget.user_id == p.user_id, PushTarget.endpoint == endpoint,
                                                    PushTarget.kind == "webpush"))
    await db.commit()
    return {"ok": True, "removed": res.rowcount or 0}
