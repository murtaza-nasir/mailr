"""Speakr writes: webhook deliveries, speaker ↔ person mapping, recording orders

Revision ID: a3c9e7f1d5b8
Revises: a7c4e2b9d6f1
Create Date: 2026-10-01 23:59:30

"""
from typing import Sequence, Union

from alembic import op

revision: str = 'a3c9e7f1d5b8'
down_revision: Union[str, Sequence[str], None] = 'a7c4e2b9d6f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = """
CREATE TABLE speakr_webhook_deliveries (
    id bigserial PRIMARY KEY,
    connector_id integer NOT NULL REFERENCES connectors(id) ON DELETE CASCADE,
    delivery_id varchar(100) NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_speakr_webhook_delivery UNIQUE (connector_id, delivery_id)
);

CREATE TABLE speakr_speaker_people (
    id bigserial PRIMARY KEY,
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    connector_id integer NOT NULL REFERENCES connectors(id) ON DELETE CASCADE,
    speaker_key varchar(200) NOT NULL,
    speaker_name varchar(200) NOT NULL DEFAULT '',
    email varchar(320) NOT NULL,
    person_name varchar(300) NOT NULL DEFAULT '',
    state varchar(12) NOT NULL DEFAULT 'confirmed',
    source varchar(12) NOT NULL DEFAULT 'user',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_speakr_speaker_people UNIQUE (connector_id, speaker_key, email)
);
CREATE INDEX ix_speakr_speaker_people_email ON speakr_speaker_people (user_id, email);

ALTER TABLE assistant_orders ADD COLUMN IF NOT EXISTS recordings jsonb NOT NULL DEFAULT '{}'
"""


def upgrade() -> None:
    for stmt in SCHEMA.split(";"):
        if stmt.strip():
            op.execute(stmt)


def downgrade() -> None:
    op.execute("ALTER TABLE assistant_orders DROP COLUMN IF EXISTS recordings")
    op.execute("DROP TABLE IF EXISTS speakr_speaker_people")
    op.execute("DROP TABLE IF EXISTS speakr_webhook_deliveries")
