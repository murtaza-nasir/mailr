"""Stored zone names are IANA keys only (9 Oct 2026)

Outlook writes Windows zone names ("Central Standard Time"). An event added from an Outlook
invite was stored with that name in cal_objects.tzid, and the browser's date formatter threw on
it. The mirror now stores only IANA keys; this migration maps the names already stored:
cal_objects.tzid and end_tzid, cal_collections.calendar_tz and users.settings.timezone. A
Windows name, an alias or a prefixed id becomes its IANA key; an unknown name becomes null (the
instant is kept in start_at/end_at, and the time shows in the user's zone).

The CalDAV copies keep their TZIDs until the next write; `mailr calendar-fix-zones` rewrites them.

Safe to run again: only names that are not IANA keys change.

Revision ID: e6b8d4f2a9c1
Revises: d5a7c3e9f1b2
Create Date: 2026-10-09 23:30:00

"""
from collections.abc import Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import sqlalchemy as sa
from alembic import op

revision: str = 'e6b8d4f2a9c1'
down_revision: str | Sequence[str] | None = 'd5a7c3e9f1b2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _iana(name: str) -> str | None:
    """The IANA key for a stored name (the rules of calendar/tz.py `iana`, frozen here)."""
    name = (name or "").strip().strip('"')
    if not name:
        return None
    if name.upper() in ("UTC", "Z", "GMT", "ETC/UTC"):
        return "UTC"
    try:
        return ZoneInfo(name).key
    except (ZoneInfoNotFoundError, ValueError):
        pass
    try:
        from icalendar.timezone.windows_to_olson import WINDOWS_TO_OLSON

        olson = WINDOWS_TO_OLSON.get(name)
        if olson:
            return ZoneInfo(olson).key
    except (ImportError, ZoneInfoNotFoundError, ValueError):
        pass
    parts = name.split("/")
    for i in range(1, len(parts) - 1):
        try:
            return ZoneInfo("/".join(parts[i:])).key
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return None


def _fix_column(conn, table: str, column: str) -> None:
    rows = conn.execute(sa.text(f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL")).scalars()
    for old in list(rows):
        new = _iana(old)
        if new == old:
            continue
        conn.execute(sa.text(f"UPDATE {table} SET {column} = :new WHERE {column} = :old"), {"new": new, "old": old})


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    tables = set(insp.get_table_names())
    if "cal_objects" in tables:
        _fix_column(conn, "cal_objects", "tzid")
        _fix_column(conn, "cal_objects", "end_tzid")
    if "cal_collections" in tables:
        _fix_column(conn, "cal_collections", "calendar_tz")
    if "users" in tables:
        rows = conn.execute(sa.text(
            "SELECT DISTINCT settings->>'timezone' FROM users WHERE settings ? 'timezone'")).scalars()
        for old in list(rows):
            new = _iana(old) if old else None
            if new == old:
                continue
            if new:
                conn.execute(sa.text("UPDATE users SET settings = jsonb_set(settings, '{timezone}', to_jsonb(CAST(:new AS text))) "
                                     "WHERE settings->>'timezone' = :old"), {"new": new, "old": old})
            else:
                conn.execute(sa.text("UPDATE users SET settings = settings - 'timezone' WHERE settings->>'timezone' = :old"),
                             {"old": old})


def downgrade() -> None:
    pass  # the old names are not kept; IANA keys work everywhere they did
