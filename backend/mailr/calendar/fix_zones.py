"""Repair stored calendar objects whose TZIDs are not IANA keys (`mailr calendar-fix-zones`).

An event added from an Outlook invite before 9 Oct 2026 kept Outlook's Windows zone name
("Central Standard Time") on the CalDAV server and in the mirror. The migration e6b8d4f2a9c1
maps the mirror's columns; this rewrites the objects themselves on the server through the
writer (If-Match, per-UID lock): each Windows name or alias becomes its IANA key with a matching
VTIMEZONE (ical.serialize → ical.normalize_zones), and a costly VTIMEZONE becomes its fixed offset
(zones.clean). UID, SEQUENCE and every other property stay,
and no iTIP mail goes out (no organizer write hook is set).

Counts only: never a title, an address or any other text of an object.
"""

from __future__ import annotations

from typing import Any

from icalendar import Calendar
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.calendar import tz as tzmod
from mailr.models import CalCollection, CalObject, Connector


def needs_fix(ics: str) -> bool:
    """True when the object uses a TZID that is not an IANA key but maps to one, or carries a
    VTIMEZONE that is costly to use (calendar/zones.py; the rewrite turns it into its fixed
    offset)."""
    from mailr.calendar import zones

    try:
        cal = Calendar.from_ical(ics)
        used = cal.get_used_tzids()
    except Exception:  # noqa: BLE001  unreadable: the sync reports it, not this
        return False
    return any(not tzmod.is_iana(t) and tzmod.iana(t) for t in used) or bool(zones.problems(cal))


async def fix_user(db: AsyncSession, user_id: int, *, dry_run: bool = False) -> dict[str, int]:
    """Rewrite the user's affected objects; the caller's session is committed per object."""
    from mailr.calendar import writer

    st = {"scanned": 0, "affected": 0, "fixed": 0, "read_only": 0, "failed": 0}
    rows = (await db.execute(
        select(CalObject.id, CalObject.ics, CalCollection.read_only, Connector.kind)
        .join(CalCollection, CalCollection.id == CalObject.collection_id)
        .join(Connector, Connector.id == CalObject.connector_id)
        .where(CalObject.user_id == user_id, CalObject.deleted_at.is_(None), CalObject.ics.ilike("%TZID%"))
        .order_by(CalObject.id))).all()
    for oid, ics, read_only, kind in rows:
        st["scanned"] += 1
        if not needs_fix(ics or ""):
            continue
        st["affected"] += 1
        if read_only or kind == "webcal":
            st["read_only"] += 1  # a subscription or a read-only share: mailr cannot write it
            continue
        if dry_run:
            continue
        try:
            obj = await db.get(CalObject, oid)
            await writer.rmw(db, user_id, None, obj, lambda cal: None)
            await db.commit()
            st["fixed"] += 1
        except Exception:  # noqa: BLE001  counted; the object stays as it was
            await db.rollback()
            st["failed"] += 1
    return st


def line(email: str, st: dict[str, Any], dry_run: bool) -> str:
    verb = "would fix" if dry_run else "fixed"
    n = st["affected"] - st["read_only"] if dry_run else st["fixed"]
    return (f"{email}: {st['scanned']} with a TZID, {st['affected']} with a non-IANA zone, {n} {verb}, "
            f"{st['read_only']} read-only (left), {st['failed']} failed")
