"""Time zones for the calendar mirror and writer.

The user's zone is `settings.timezone` (Settings → You → Region), then a zone an older server left
in the assistant profile, then `settings.device_timezone` (the zone of the user's browser, which the
web sends as X-Mailr-Zone while Region is Automatic: note_device_zone), then UTC.
Floating times (no TZID, no Z) are read in that zone. A TZID is resolved with zoneinfo, then
the Windows names Outlook writes, then the VTIMEZONE embedded in the object, then the user's zone.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, tzinfo
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession


@lru_cache(maxsize=512)
def zone(name: str | None) -> ZoneInfo | None:
    """A ZoneInfo for an IANA or Windows zone name, None when it is unknown."""
    name = (name or "").strip().strip('"')
    if not name:
        return None
    if name.upper() in ("UTC", "Z", "GMT", "ETC/UTC"):
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        pass
    try:
        from icalendar.timezone.windows_to_olson import WINDOWS_TO_OLSON

        olson = WINDOWS_TO_OLSON.get(name)
        if olson:
            return ZoneInfo(olson)
    except (ImportError, ZoneInfoNotFoundError, ValueError):
        pass
    # "/mozilla.org/20050126_1/Europe/London" and similar prefixed ids.
    parts = name.split("/")
    for i in range(1, len(parts) - 1):
        try:
            return ZoneInfo("/".join(parts[i:]))
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return None


def iana(name: str | None) -> str | None:
    """The IANA key for a zone name (IANA, Windows, an alias or a prefixed id); None when the
    name is unknown. The only zone names mailr stores and sends out (9 Oct 2026: Outlook's
    "Central Standard Time" reached the browser, whose formatter threw)."""
    z = zone(name)
    if z is None:
        return None
    return "UTC" if z.key in ("Etc/UTC", "UTC") else z.key


def is_iana(name: str | None) -> bool:
    """True when `name` is already the IANA key itself (no mapping needed)."""
    return bool(name) and iana(name) == name


def zone_name(tz: tzinfo | None) -> str | None:
    """The IANA key of a tzinfo, or None. A VTIMEZONE-built zone (dateutil) is named by its
    TZID mapped to IANA; a name with no IANA equivalent gives None."""
    if tz is None:
        return None
    key = getattr(tz, "key", None) or getattr(tz, "zone", None) or getattr(tz, "_tzid", None)
    if key and (name := iana(str(key))):
        return name
    if tz is UTC:
        return "UTC"
    off = datetime(2026, 1, 1, tzinfo=tz).utcoffset()
    return "UTC" if off is not None and not off else None


DEVICE_KEY = "device_timezone"
ZONE_HEADER = "x-mailr-zone"


def user_zone_from(user: Any, profile_data: dict[str, Any] | None = None) -> ZoneInfo:
    # One zone: the account setting; a profile copy from an older server only fills a gap. With
    # Region on Automatic (no setting), the browser's zone the web last sent, then UTC.
    settings = getattr(user, "settings", None) or {}
    name = settings.get("timezone") or (profile_data or {}).get("timezone")
    return zone(name) or zone(settings.get(DEVICE_KEY)) or ZoneInfo("UTC")


def user_zone_name(user: Any, profile_data: dict[str, Any] | None = None) -> str:
    """The IANA name of user_zone_from."""
    return iana(user_zone_from(user, profile_data).key) or "UTC"


async def note_device_zone(db: AsyncSession, user: Any, sent: str | None) -> bool:
    """Keep the zone the user's browser sent (header X-Mailr-Zone) as `settings.device_timezone`:
    the user's zone while Region is Automatic. An unknown name is ignored. When it changes and no
    Region is chosen, date-only reminders are planned again (as for a Region change). Flushes, the
    caller commits. True when it changed."""
    name = iana((sent or "").strip()[:64]) if sent else None
    if not name or user is None or (getattr(user, "settings", None) or {}).get(DEVICE_KEY) == name:
        return False
    from mailr.models import User

    u = await db.get(User, user.id, with_for_update=True, populate_existing=True)
    if u is None or (u.settings or {}).get(DEVICE_KEY) == name:
        return False
    u.settings = {**(u.settings or {}), DEVICE_KEY: name}
    await db.flush()
    if u is not user:
        user.settings = u.settings
    if not u.settings.get("timezone"):
        from mailr.core.jobs import enqueue

        # Date-only reminders are planned again a little later, in a job: a laptop and a phone in
        # different zones that take turns here cost one replan, and no request waits for it.
        await enqueue(db, "calendar.replan_zone", {"user_id": u.id}, user_id=u.id,
                      run_at=datetime.now(UTC) + timedelta(seconds=ZONE_SETTLE_S),
                      dedupe_key=f"calendar.replan_zone:{u.id}")
        await zone_changed(db, u.id)
    return True


ZONE_SETTLE_S = 60


async def zone_changed(db: AsyncSession, user_id: int) -> None:
    """The user's zone changed: all-day and floating events are expanded again in the new zone
    (calendar.rematerialize), so range queries and free/busy cover the right days."""
    from mailr.core.jobs import enqueue

    await enqueue(db, "calendar.rematerialize", {"user_id": user_id}, user_id=user_id,
                  dedupe_key=f"calendar.rematerialize:{user_id}:zone")


CLOCK_KEY = "device_clock"
CLOCK_HEADER = "x-mailr-clock"
CLOCKS = ("12h", "24h")


def user_clock(user: Any, default: str = "24h") -> str:
    """The user's clock, "12h" or "24h": the time_format setting (Settings → You → Region) when it
    names one; on Automatic, the clock of the device the user last used (header X-Mailr-Clock:
    note_device_clock); else `default`."""
    settings = (getattr(user, "settings", None) or {}) if user is not None else {}
    for v in (settings.get("time_format"), settings.get(CLOCK_KEY)):
        if v in CLOCKS:
            return v
    return default


async def note_device_clock(db: AsyncSession, user: Any, sent: str | None) -> bool:
    """Keep the clock the user's device sent (header X-Mailr-Clock, "12h" or "24h") as
    `settings.device_clock`: the user's clock while Time format is Automatic, which only the device
    can resolve (its locale). Anything else is ignored. Flushes, the caller commits. True when it
    changed."""
    v = (sent or "").strip().lower()
    if v not in CLOCKS or user is None or (getattr(user, "settings", None) or {}).get(CLOCK_KEY) == v:
        return False
    from mailr.models import User

    u = await db.get(User, user.id, with_for_update=True, populate_existing=True)
    if u is None or (u.settings or {}).get(CLOCK_KEY) == v:
        return False
    u.settings = {**(u.settings or {}), CLOCK_KEY: v}
    await db.flush()
    if u is not user:
        user.settings = u.settings
    return True


async def user_zone(db: AsyncSession, user_or_id: Any) -> ZoneInfo:
    """The user's home zone (the account setting, then UTC)."""
    from mailr.models import AssistantProfile, User

    user = user_or_id if not isinstance(user_or_id, int) else await db.get(User, user_or_id)
    if user is None:
        return ZoneInfo("UTC")
    prof = await db.get(AssistantProfile, user.id)
    return user_zone_from(user, prof.data if prof is not None else None)


async def aware(db: AsyncSession, user_or_id: Any, v: datetime) -> datetime:
    """A time a person or the assistant gave: one without an offset is in the user's zone (as the
    approval card shows it), one with an offset is taken as written."""
    return v if v.tzinfo is not None else v.replace(tzinfo=await user_zone(db, user_or_id))


def resolve_tz(tzid: str | None, cal: Any = None, fallback: tzinfo | None = None) -> tzinfo:
    """The zone a TZID names: zoneinfo, the Windows mapping, the object's VTIMEZONE (only when it
    is cheap, else its fixed offset: calendar/zones.py), else `fallback` (the user's zone)."""
    z = zone(tzid)
    if z is not None:
        return z
    if tzid and cal is not None:
        from mailr.calendar import zones

        for vtz in cal.walk("VTIMEZONE"):
            if str(vtz.get("tzid", "")) == tzid:
                try:
                    return zones.build(vtz)
                except Exception:  # noqa: BLE001  a broken VTIMEZONE: fall through
                    break
    return fallback or ZoneInfo("UTC")


def localize(v: datetime, tzid: str | None, cal: Any = None, fallback: tzinfo | None = None) -> datetime:
    """An aware datetime for a parsed iCalendar value: aware values stay, a naive value with a
    TZID is placed in that zone, a floating value in `fallback`."""
    if v.tzinfo is not None:
        return v
    return v.replace(tzinfo=resolve_tz(tzid, cal, fallback))


def to_zone(v: datetime | date, tz: tzinfo | None) -> datetime | date:
    """A value as it is written: timed values in `tz` (UTC values stay UTC when tz is UTC or
    None), dates unchanged."""
    if not isinstance(v, datetime):
        return v
    if v.tzinfo is None:
        v = v.replace(tzinfo=tz or UTC)
    if tz is None or zone_name(tz) == "UTC":
        return v.astimezone(UTC)
    return v.astimezone(tz)
