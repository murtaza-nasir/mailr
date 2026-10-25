"""Inbox layouts (core/layouts.py): list presets and custom layouts, save custom layouts, preview
a section while editing, the counts shown on the tabs, and the sidebar's Inbox count."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import BigInteger, Select, String, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.api import contract as C
from mailr.api.mail_read import (
    TOTAL_CAP,
    _label_counts,
    _settings,
    excluding,
    memo_labels,
    section_conditions,
    tab_conditions,
)
from mailr.config import get_settings
from mailr.core import layouts
from mailr.core.auth import Principal, current, scope
from mailr.core.publicid import ConvId
from mailr.core.spamtrash import live_sql
from mailr.core.visibility import is_restricted
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.models import Conversation, Label

router = APIRouter(tags=["layouts"])


async def _label_names(db: AsyncSession, uid: int) -> dict[int, str]:
    rows = (await db.execute(select(Label.id, Label.name).where(Label.user_id == uid))).all()
    return {i: n for i, n in rows}


def _decorate(lays: list[dict[str, Any]], names: dict[int, str]) -> list[dict[str, Any]]:
    for lay in lays:
        for tab in lay["tabs"]:
            for s in tab["sections"]:
                s["view_all_q"] = layouts.view_all_query(s, names)
    return lays


async def _out(db: AsyncSession, p: Principal, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = settings or await _settings(db, p)
    names = await _label_names(db, p.user_id)
    lays = _decorate(layouts.all_layouts(settings), names)
    active = layouts.active_id(settings)
    if not any(x["id"] == active for x in lays):
        active = "default"
    return {"active": active, "layouts": lays,
            "options": {"bases": list(layouts.BASES), "filters": list(layouts.FILTERS), "icons": list(layouts.ICONS),
                        "limits": {"tabs": layouts.MAX_TABS, "sections": layouts.MAX_SECTIONS,
                                   "layouts": layouts.MAX_LAYOUTS}}}


@router.get("/layouts", response_model=C.mail.LayoutsResponse, **C.TYPED)
async def get_layouts(p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    return await _out(db, p)


@router.get("/layouts/show-in-tab")
async def show_in_tab_choices(conversation_ids: str = "", p: Principal = Depends(scope("read")),
                              db: AsyncSession = Depends(get_db)):
    # ACT mail.show_in_tab @api
    """What the "Show in tab" menu offers for these conversations: the active
    layout's tabs in tab-bar order with their kind (first, label, category, inbox, or disabled with
    a reason), and the tab each conversation shows in now. `current` is that tab when every
    conversation shows in the same one, else null: the menu marks one tab at most."""
    from mailr.core import tabplace

    try:
        ids = [int(x) for x in conversation_ids.split(",") if x.strip()][:500]
    except ValueError:
        raise HTTPException(400, "conversation_ids must be numbers") from None  # i18n: internal
    settings = await _settings(db, p)
    lay = tabplace.active_layout(settings)
    where = await tabplace.current_tabs(db, p, lay, ids)
    found = set(where.values())
    return {"layout": lay["id"] if lay else None, "tabs": tabplace.targets(lay),
            "current": next(iter(found)) if ids and len(found) == 1 else None,
            "placements": [{"conversation_id": ConvId(cid), "tab": tid} for cid, tid in where.items()]}


class LayoutsIn(BaseModel):
    layouts: list[dict[str, Any]]
    # Make this layout the inbox (a preset id or a custom layout id).
    active: str | None = None


@router.put("/layouts")
async def put_layouts(body: LayoutsIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """Replace the user's custom layouts (presets are not stored)."""
    if p.is_token:
        raise ApiError(403, "layouts.user_only")
    names = await _label_names(db, p.user_id)
    try:
        custom = layouts.validate(body.layouts, set(names))
    except layouts.LayoutError as e:
        raise HTTPException(400, str(e)) from None
    from mailr.core import usersettings

    ids = {x["id"] for x in custom}
    active = body.active if body.active is not None else layouts.active_id(await usersettings.current(db, p.user_id))
    if active in layouts.PRESET_IDS:
        inbox_type = active
    elif active in ids:
        inbox_type = f"layout:{active}"
    else:
        # The active custom layout was deleted: fall back to the default inbox.
        inbox_type = "default"
    await usersettings.patch(db, p.user_id, {"inbox_layouts": custom, "inbox_type": inbox_type})
    await db.commit()
    return await _out(db, p)


class PreviewIn(BaseModel):
    section: dict[str, Any]
    above: list[dict[str, Any]] = []


@router.post("/layouts/preview")
async def preview_section(body: PreviewIn, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """How many conversations a section being edited would show (total and unread)."""
    names = await _label_names(db, p.user_id)
    try:
        lay = layouts.validate([{"name": "x", "tabs": [{"name": "x", "sections": [*body.above, body.section]}]}],
                               set(names))[0]
    except layouts.LayoutError as e:
        raise HTTPException(400, str(e)) from None
    secs = lay["tabs"][0]["sections"]
    spec, above = secs[-1], secs[:-1]
    try:
        conds = await section_conditions(db, p, spec)
    except (ValueError, HTTPException) as e:
        raise HTTPException(400, f"invalid section: {getattr(e, 'detail', e)}") from None  # i18n: internal
    if conds is None:
        return {"total": 0, "unread": 0, "view_all_q": layouts.view_all_query(spec, names)}
    if spec.get("exclude_above"):
        for other in above:
            oc = await section_conditions(db, p, other)
            if oc:
                conds.append(excluding(oc))
    base = select(Conversation.id, Conversation.unread_count).where(*conds).limit(10_001).subquery()
    total, unread = (await db.execute(select(func.count(), func.count().filter(base.c.unread_count > 0))
                                      .select_from(base))).one()
    return {"total": total, "unread": unread, "capped": total > 10_000,
            "view_all_q": layouts.view_all_query(spec, names)}


UNREAD_CAP = 1000


async def _tab_where(db: AsyncSession, p: Principal, layout_id: str, tab: dict[str, Any]) -> list[Any] | None:
    """What a tab lists (api/mail_read.py tab_conditions, also the list of a whole tab)."""
    return await tab_conditions(db, p, layout_id, tab)


# The first tab's unread count by user: (change_log cursor, layout as saved, count). Like the
# label counts (api/mail_read.py), it holds until the mail or the layout changes.
_FIRST_TAB: dict[int, tuple[int, str, int]] = {}


async def first_tab_unread(db: AsyncSession, p: Principal, lay: dict[str, Any]) -> int:
    """Unread conversations the layout's first tab lists, with every exclusion the tab applies.
    The sidebar's Inbox count ("first_tab") and the first tab's own unread indicator both come
    from here, so they cannot disagree. Not capped (the label counts are not either)."""
    tab = lay["tabs"][0]
    key = json.dumps(lay, sort_keys=True, default=str)
    cursor = None
    if not is_restricted(p):
        cursor = (await db.execute(text("SELECT coalesce(max(id), 0) FROM change_log WHERE user_id=:u"),
                                   {"u": p.user_id})).scalar_one()
        hit = _FIRST_TAB.get(p.user_id)
        if hit and hit[0] == cursor and hit[1] == key:
            return hit[2]
    where = await _tab_where(db, p, lay["id"], tab)
    n = 0 if where is None else (await db.execute(
        select(func.count()).select_from(Conversation).where(*where, Conversation.unread_count > 0))).scalar_one()
    if cursor is not None:
        if len(_FIRST_TAB) > 1000:
            _FIRST_TAB.clear()
        _FIRST_TAB[p.user_id] = (cursor, key, n)
    return n


def inbox_count_mode(settings: dict[str, Any]) -> str:
    """The `inbox_count` setting: "all" or "first_tab" (the default, and anything unknown)."""
    return "all" if settings.get("inbox_count") == "all" else "first_tab"


@router.get("/inbox/count", response_model=C.mail.InboxCount, **C.TYPED)
async def inbox_count(p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    # ACT settings.inbox_count @api
    """The number by Inbox in the sidebar. With the `inbox_count`
    setting "first_tab" (default) it is the unread conversations the active layout's first tab
    lists: Primary in the Default layout, the whole inbox list in a layout with one tab, the
    first tab of a custom layout. Mail that other tabs claim is left out; those tabs show their
    own counts. With "all" it is every unread conversation with the Inbox label, as the Inbox
    label's own count in GET /labels."""
    # ACT settings.inbox_count
    settings = await _settings(db, p)
    mode = inbox_count_mode(settings)
    lay = layouts.find(settings, layouts.active_id(settings)) or layouts.find(settings, "default")
    if mode == "first_tab" and lay and lay["tabs"]:
        return {"count": await first_tab_unread(db, p, lay), "mode": mode, "layout": lay["id"],
                "tab": lay["tabs"][0]["id"]}
    inbox_id = (await db.execute(select(Label.id).where(Label.user_id == p.user_id, Label.role == "inbox"))).scalar()
    counts, _ = await _label_counts(db, p)
    return {"count": counts.get(inbox_id, (0, 0))[0] if inbox_id is not None else 0, "mode": "all",
            "layout": lay["id"] if lay else None, "tab": None}

# Conversations with a deadline: the deadline decision on the conversation's newest message (as
# the Due chip reads it), not rejected ("Not a deadline"), with a date from :since on. Mail in
# Spam or Trash counts for nothing here, newest or not (core/spamtrash.py).
_DUES_SQL = (
    "SELECT m.conversation_id AS cid, min(d.source_ref) AS due FROM label_decisions d "
    "JOIN messages m ON m.id = d.message_id "
    "WHERE d.user_id = :uid AND d.kind = 'smart' AND d.target = 'deadline' AND d.value "
    "AND d.status <> 'rejected' AND d.source_ref ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' AND d.source_ref >= :since "
    f"AND NOT m.deleted AND NOT m.draft AND {live_sql('m')} AND NOT EXISTS (SELECT 1 FROM messages n "
    f"WHERE n.conversation_id = m.conversation_id AND NOT n.deleted AND NOT n.draft AND n.id <> m.id AND {live_sql('n')} "
    "AND (n.date > m.date OR (n.date = m.date AND n.id > m.id) OR (m.date IS NULL AND n.date IS NOT NULL))) "
    "GROUP BY m.conversation_id"
)


@router.get("/layouts/{layout_id}/counts", response_model=C.mail.TabCounts, **C.TYPED)
async def tab_counts(layout_id: str, detail: bool = False, p: Principal = Depends(scope("read")),
                     db: AsyncSession = Depends(get_db)):
    """What each tab shows by its label, counted in one query for the whole layout.

    Without `detail` (older clients): {tab_id: n}, the count of the tab's `badge`. With
    `detail`: {"tabs": {tab_id: {...}}} with, for each of the tab's indicators, `unread`
    (counting stops at 1000, shown as "999+"), `all` (counting stops at 200,000, shown
    as "200,000+") and for "due" `due_count`, `earliest_due` (a date), `earliest_due_cid`,
    `earliest_due_from` and `earliest_due_subject`. "due" counts deadlines from MAILR_OVERDUE_DAYS
    ago (default 7) on. Every count covers the tab's sections
    together and leaves out what a claiming tab takes. The first tab's unread is not capped: it
    is the sidebar's Inbox count (GET /inbox/count, `first_tab_unread`)."""
    memo_labels(db)
    settings = await _settings(db, p)
    lay = layouts.find(settings, layout_id)
    if lay is None:
        raise ApiError(404, "layouts.layout_not_found")
    # A deadline up to MAILR_OVERDUE_DAYS past still counts (the client shows "Overdue", in red);
    # an older one drops, so a forgotten deadline does not keep the tab red.
    since = datetime.now(UTC).date() - timedelta(days=get_settings().overdue_days)
    if is_restricted(p):
        return await _tab_counts(db, p, layout_id, lay, detail, since)
    # The live stream refetches these after every change, and each open tab asks: between two
    # changes the answer is the same, so it is kept per user until the change cursor moves (or
    # the settings or the day do), at most TAB_COUNTS_TTL seconds, and computed once at a time.
    cursor = (await db.execute(text("SELECT coalesce(max(id), 0) FROM change_log WHERE user_id=:u"),
                               {"u": p.user_id})).scalar_one()
    key = (p.user_id, layout_id, detail, since, cursor,
           hashlib.sha256(json.dumps(settings, sort_keys=True, default=str).encode()).hexdigest())
    hit = _TAB_COUNTS.get(key)
    if hit and time.monotonic() - hit[0] < TAB_COUNTS_TTL:
        return hit[1]
    lock = _TAB_LOCKS.setdefault(key[:3], asyncio.Lock())
    async with lock:
        hit = _TAB_COUNTS.get(key)
        if hit and time.monotonic() - hit[0] < TAB_COUNTS_TTL:
            return hit[1]
        out = await _tab_counts(db, p, layout_id, lay, detail, since)
        _TAB_COUNTS[key] = (time.monotonic(), out)
        _TAB_COUNTS.move_to_end(key)
        while len(_TAB_COUNTS) > 1000:
            _TAB_COUNTS.popitem(last=False)
    if len(_TAB_LOCKS) > 1000:
        _TAB_LOCKS.clear()
    return out


TAB_COUNTS_TTL = 30.0
_TAB_COUNTS: OrderedDict[tuple, tuple[float, Any]] = OrderedDict()
_TAB_LOCKS: dict[tuple, asyncio.Lock] = {}


async def _tab_counts(db: AsyncSession, p: Principal, layout_id: str, lay: dict[str, Any], detail: bool,
                      since: Any) -> Any:
    dues = (text(_DUES_SQL).bindparams(uid=p.user_id, since=since.isoformat())
            .columns(cid=BigInteger, due=String).cte("dues"))
    cols: list[Any] = []
    wants: dict[str, list[str]] = {}
    slot: dict[str, int] = {}  # where a tab's columns are in the row (tabs without sections have none)
    first: dict[str, int] = {}
    for i, tab in enumerate(lay["tabs"]):
        want = list(tab["indicators"]) if detail else ([tab["badge"]] if tab["badge"] != "none" else [])
        if not want:
            continue
        wants[tab["id"]] = want
        where = await _tab_where(db, p, layout_id, tab)
        if where is None:
            continue
        k = slot[tab["id"]] = len(cols)
        if "unread" in want and i == 0:
            # The first tab's unread is the sidebar's Inbox count (GET /inbox/count): one computation.
            first[tab["id"]] = await first_tab_unread(db, p, lay)
        elif "unread" in want:
            cols.append(_count(select(Conversation.id).where(*where, Conversation.unread_count > 0).limit(UNREAD_CAP))
                        .label(f"unread_{k}"))
        if "all" in want:
            cols.append(_count(select(Conversation.id).where(*where).limit(TOTAL_CAP + 1)).label(f"all_{k}"))
        if "due" in want:
            ids = select(Conversation.id).where(*where)
            cols.append(_count(select(dues.c.cid).where(dues.c.cid.in_(ids))).label(f"due_{k}"))
            cols.append(select(dues.c.cid).where(dues.c.cid.in_(ids)).order_by(dues.c.due, dues.c.cid).limit(1)
                        .scalar_subquery().label(f"duecid_{k}"))
            cols.append(select(func.min(dues.c.due)).where(dues.c.cid.in_(ids)).scalar_subquery().label(f"duedate_{k}"))
    row = (await db.execute(select(*cols))).mappings().one() if cols else {}

    tabs: dict[str, dict[str, Any]] = {}
    for tid, want in wants.items():
        k = slot.get(tid)
        t: dict[str, Any] = {}
        if "unread" in want:
            t["unread"] = first[tid] if tid in first else row[f"unread_{k}"] if k is not None else 0
        if "all" in want:
            t["all"] = row[f"all_{k}"] if k is not None else 0
        if "due" in want:
            t["due_count"] = row[f"due_{k}"] if k is not None else 0
            t["earliest_due"] = row[f"duedate_{k}"] if k is not None else None
            t["earliest_due_cid"] = row[f"duecid_{k}"] if k is not None else None
        tabs[tid] = t
    if not detail:
        return {tid: next(iter(t.values()), 0) for tid, t in tabs.items()}

    firsts = {t["earliest_due_cid"] for t in tabs.values() if t.get("earliest_due_cid")}
    convs = {c.id: c for c in (await db.execute(select(Conversation).where(Conversation.id.in_(firsts)))).scalars()} \
        if firsts else {}
    for t in tabs.values():
        if "earliest_due_cid" not in t:
            continue
        c = convs.get(t["earliest_due_cid"])
        who = (c.participants or [{}])[0] if c else {}
        t["earliest_due_from"] = (who.get("name") or who.get("email") or "") if c else ""
        t["earliest_due_subject"] = c.subject if c else ""
        t["earliest_due_cid"] = ConvId(c.id) if c else None
    return {"tabs": tabs}


def _count(sel: Select[Any]) -> Any:
    return select(func.count()).select_from(sel.subquery()).scalar_subquery()
