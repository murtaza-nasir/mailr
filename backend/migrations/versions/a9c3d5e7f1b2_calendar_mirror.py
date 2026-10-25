"""calendar mirror: cal_collections, cal_objects, cal_occurrences, reminder_schedule; canonical reminder keys

Revision ID: a9c3d5e7f1b2
Revises: c4e8a1d2f3b5
Create Date: 2026-10-01 22:00:00

"""
import re
from datetime import UTC, date, datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a9c3d5e7f1b2'
down_revision: Union[str, Sequence[str], None] = 'c4e8a1d2f3b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = """
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE SEQUENCE IF NOT EXISTS cal_rev_seq;

CREATE TABLE cal_collections (
    id bigserial PRIMARY KEY,
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    connector_id integer NOT NULL REFERENCES connectors(id) ON DELETE CASCADE,
    href text NOT NULL,
    displayname text NOT NULL DEFAULT '',
    description text NOT NULL DEFAULT '',
    color varchar(9),
    components varchar[] NOT NULL DEFAULT '{}',
    server_order integer,
    read_only boolean NOT NULL DEFAULT false,
    calendar_tz varchar(64),
    ctag text,
    sync_token text,
    sync_supported boolean,
    sync_status varchar(12) NOT NULL DEFAULT 'idle',
    sync_error text NOT NULL DEFAULT '',
    fail_count integer NOT NULL DEFAULT 0,
    last_sync_at timestamptz,
    last_full_sync_at timestamptz,
    next_sync_at timestamptz,
    hidden boolean NOT NULL DEFAULT false,
    busy boolean NOT NULL DEFAULT true,
    notify boolean NOT NULL DEFAULT true,
    sort_order integer NOT NULL DEFAULT 0,
    rev bigint NOT NULL DEFAULT nextval('cal_rev_seq'),
    deleted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_cal_collections_connector_href UNIQUE (connector_id, href)
);
CREATE INDEX ix_cal_collections_user_id ON cal_collections (user_id);
CREATE INDEX ix_cal_collections_user_rev ON cal_collections (user_id, rev);

CREATE TABLE cal_objects (
    id bigserial PRIMARY KEY,
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    connector_id integer NOT NULL REFERENCES connectors(id) ON DELETE CASCADE,
    collection_id bigint NOT NULL REFERENCES cal_collections(id) ON DELETE CASCADE,
    href text NOT NULL,
    etag text,
    uid varchar(300) NOT NULL DEFAULT '',
    kind varchar(7) NOT NULL,
    ics text NOT NULL DEFAULT '',
    summary text NOT NULL DEFAULT '',
    description text NOT NULL DEFAULT '',
    location text NOT NULL DEFAULT '',
    status varchar(20) NOT NULL DEFAULT '',
    class varchar(12) NOT NULL DEFAULT '',
    transp varchar(12) NOT NULL DEFAULT '',
    color varchar(40),
    url text,
    tzid varchar(64),
    end_tzid varchar(64),
    floating boolean NOT NULL DEFAULT false,
    all_day boolean NOT NULL DEFAULT false,
    start_at timestamptz,
    end_at timestamptz,
    start_date date,
    end_date date,
    due_at timestamptz,
    due_date date,
    completed_at timestamptz,
    percent smallint,
    priority smallint,
    sort_order bigint,
    categories text[] NOT NULL DEFAULT '{}',
    parent_uid varchar(300),
    repeat_from varchar(10),
    rrule text,
    recurring boolean NOT NULL DEFAULT false,
    has_overrides boolean NOT NULL DEFAULT false,
    range_start timestamptz,
    range_end timestamptz,
    expanded_from timestamptz,
    expanded_until timestamptz,
    organizer varchar(320),
    organizer_name text NOT NULL DEFAULT '',
    is_organizer boolean NOT NULL DEFAULT false,
    my_partstat varchar(20),
    attendees jsonb NOT NULL DEFAULT '[]',
    attendee_count integer NOT NULL DEFAULT 0,
    sequence integer NOT NULL DEFAULT 0,
    dtstamp timestamptz,
    last_modified timestamptz,
    created timestamptz,
    alarms integer[] NOT NULL DEFAULT '{}',
    tags_text text NOT NULL DEFAULT '',
    tsv tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('simple', coalesce(summary, '')), 'A') ||
        setweight(to_tsvector('simple', coalesce(location, '')), 'B') ||
        setweight(to_tsvector('simple', coalesce(tags_text, '')), 'B') ||
        setweight(to_tsvector('simple', left(coalesce(description, ''), 20000)), 'C')) STORED,
    rev bigint NOT NULL DEFAULT nextval('cal_rev_seq'),
    deleted_at timestamptz,
    fetched_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_cal_objects_collection_href UNIQUE (collection_id, href)
);
CREATE INDEX ix_cal_objects_user_uid ON cal_objects (user_id, uid);
CREATE INDEX ix_cal_objects_user_rev ON cal_objects (user_id, rev);
CREATE INDEX ix_cal_objects_tsv ON cal_objects USING gin (tsv);
CREATE INDEX ix_cal_objects_categories ON cal_objects USING gin (categories);
CREATE INDEX ix_cal_objects_open_tasks ON cal_objects (user_id, due_at, due_date)
    WHERE kind = 'task' AND deleted_at IS NULL AND status <> 'COMPLETED';
CREATE INDEX ix_cal_objects_parent ON cal_objects (user_id, parent_uid) WHERE parent_uid IS NOT NULL;
CREATE INDEX ix_cal_objects_user_kind ON cal_objects (user_id, kind) WHERE deleted_at IS NULL;

CREATE TABLE cal_occurrences (
    object_id bigint NOT NULL REFERENCES cal_objects(id) ON DELETE CASCADE,
    occurrence varchar(40) NOT NULL,
    user_id integer NOT NULL,
    collection_id bigint NOT NULL,
    start_at timestamptz NOT NULL,
    end_at timestamptz NOT NULL,
    all_day boolean NOT NULL DEFAULT false,
    is_override boolean NOT NULL DEFAULT false,
    cancelled boolean NOT NULL DEFAULT false,
    busy boolean NOT NULL DEFAULT true,
    summary text,
    location text,
    status varchar(20),
    PRIMARY KEY (object_id, occurrence)
);
CREATE INDEX ix_cal_occurrences_range ON cal_occurrences USING gist (user_id, tstzrange(start_at, end_at, '[]'));

CREATE TABLE reminder_schedule (
    id bigserial PRIMARY KEY,
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key varchar(400) NOT NULL,
    kind varchar(1) NOT NULL,
    object_id bigint REFERENCES cal_objects(id) ON DELETE CASCADE,
    uid varchar(300) NOT NULL DEFAULT '',
    occurrence varchar(40) NOT NULL DEFAULT '',
    minutes integer NOT NULL DEFAULT 0,
    anchor_at timestamptz,
    fire_at timestamptz NOT NULL,
    title text NOT NULL DEFAULT '',
    detail text NOT NULL DEFAULT '',
    source varchar(8) NOT NULL DEFAULT 'valarm',
    local_only boolean NOT NULL DEFAULT false,
    fired_at timestamptz,
    planned_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_reminder_schedule_user_key UNIQUE (user_id, key)
);
CREATE INDEX ix_reminder_schedule_due ON reminder_schedule (fire_at) WHERE fired_at IS NULL;

ALTER TABLE reminder_states ADD COLUMN anchor_at timestamptz;
ALTER TABLE reminder_states ADD COLUMN fired_at timestamptz;
"""

# ---- canonical reminder keys (a copy of mailr.calendar.ical.normalize_occurrence, frozen here) ----

_COMPACT = re.compile(r"^(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})(Z)?)?$")


def _canon(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    try:
        m = _COMPACT.match(s)
        if m:
            if m[4] is None:
                return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
            d = datetime(int(m[1]), int(m[2]), int(m[3]), int(m[4]), int(m[5]), int(m[6]), tzinfo=UTC)
        elif len(s) == 10 and s[4] == "-":
            return date.fromisoformat(s).isoformat()
        else:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=UTC)
        return d.astimezone(UTC).replace(microsecond=0).isoformat()
    except ValueError:
        return s


def _anchor(occ: str):
    try:
        if len(occ) == 10:
            d = date.fromisoformat(occ)
            return datetime(d.year, d.month, d.day, tzinfo=UTC)
        return datetime.fromisoformat(occ)
    except ValueError:
        return None


_RANK = {"handled": 3, "snoozed": 2, "pending": 1}


def normalise_reminder_keys(conn) -> None:
    """Rewrite each reminder_states key and occurrence to the canonical form. Rows that collide
    merge: handled beats snoozed beats pending, then the newest updated_at wins."""
    rows = conn.execute(sa.text(
        "SELECT id, user_id, key, kind, uid, occurrence, minutes, state, updated_at FROM reminder_states")).all()
    groups: dict[tuple[int, str], list] = {}
    for r in rows:
        occ = _canon(r.occurrence)
        key = f"{r.kind}|{r.uid}|{occ}|{r.minutes}"
        groups.setdefault((r.user_id, key), []).append((r, occ))
    for (_, key), members in groups.items():
        members.sort(key=lambda x: (_RANK.get(x[0].state, 0), x[0].updated_at), reverse=True)
        keep, occ = members[0]
        drop = [m[0].id for m in members[1:]]
        if drop:
            conn.execute(sa.text("DELETE FROM reminder_states WHERE id = ANY(:ids)"), {"ids": drop})
        if keep.key != key or keep.occurrence != occ or drop:
            conn.execute(sa.text("UPDATE reminder_states SET key=:k, occurrence=:o, anchor_at=:a WHERE id=:id"),
                         {"k": key, "o": occ, "a": _anchor(occ), "id": keep.id})
        else:
            conn.execute(sa.text("UPDATE reminder_states SET anchor_at=:a WHERE id=:id"),
                         {"a": _anchor(occ), "id": keep.id})


def upgrade() -> None:
    for stmt in [s.strip() for s in SCHEMA.split(";\n") if s.strip()]:
        op.execute(stmt)
    normalise_reminder_keys(op.get_bind())


def downgrade() -> None:
    op.execute("ALTER TABLE reminder_states DROP COLUMN fired_at")
    op.execute("ALTER TABLE reminder_states DROP COLUMN anchor_at")
    op.execute("DROP TABLE reminder_schedule")
    op.execute("DROP TABLE cal_occurrences")
    op.execute("DROP TABLE cal_objects")
    op.execute("DROP TABLE cal_collections")
    op.execute("DROP SEQUENCE cal_rev_seq")
