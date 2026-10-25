"""Speakr: speakr_recordings (metadata mirror) and recording_links

Revision ID: e5f1a7c3b9d2
Revises: d7e2b9c4a1f6
Create Date: 2026-10-01 23:59:00

"""
from typing import Sequence, Union

from alembic import op

revision: str = 'e5f1a7c3b9d2'
down_revision: Union[str, Sequence[str], None] = 'd7e2b9c4a1f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = """
CREATE TABLE speakr_recordings (
    id bigserial PRIMARY KEY,
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    connector_id integer NOT NULL REFERENCES connectors(id) ON DELETE CASCADE,
    recording_id integer NOT NULL,
    title text NOT NULL DEFAULT '',
    meeting_date timestamptz,
    created_upstream timestamptz,
    completed_upstream timestamptz,
    duration_s double precision,
    participants text[] NOT NULL DEFAULT '{}',
    tags jsonb NOT NULL DEFAULT '[]',
    folder jsonb,
    status varchar(20) NOT NULL DEFAULT '',
    has_transcript boolean NOT NULL DEFAULT false,
    has_summary boolean NOT NULL DEFAULT false,
    audio_available boolean NOT NULL DEFAULT true,
    is_archived boolean NOT NULL DEFAULT false,
    content_hash varchar(64) NOT NULL DEFAULT '',
    synced_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz,
    CONSTRAINT uq_speakr_recordings_connector_rec UNIQUE (connector_id, recording_id)
);
CREATE INDEX ix_speakr_recordings_user_date ON speakr_recordings (user_id, meeting_date);

CREATE TABLE recording_links (
    id bigserial PRIMARY KEY,
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    connector_id integer NOT NULL REFERENCES connectors(id) ON DELETE CASCADE,
    recording_id integer NOT NULL,
    kind varchar(12) NOT NULL,
    target varchar(300) NOT NULL,
    occurrence varchar(40) NOT NULL DEFAULT '',
    state varchar(12) NOT NULL DEFAULT 'suggested',
    score double precision,
    source varchar(12) NOT NULL DEFAULT 'auto',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_recording_links_target UNIQUE (connector_id, recording_id, kind, target, occurrence)
);
CREATE INDEX ix_recording_links_user_target ON recording_links (user_id, kind, target);
"""


def upgrade() -> None:
    for stmt in SCHEMA.split(";"):
        if stmt.strip():
            op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS recording_links")
    op.execute("DROP TABLE IF EXISTS speakr_recordings")
