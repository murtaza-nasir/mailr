"""People: strength, contact details and provenance; contact groups; signature suggestions; the
CardDAV address-book mirror.

Revision ID: a7c4e2b9d6f1
Revises: e5f1a7c3b9d2
Create Date: 2026-10-01 23:59:30

"""
from typing import Sequence, Union

from alembic import op

revision: str = 'a7c4e2b9d6f1'
down_revision: Union[str, Sequence[str], None] = 'e5f1a7c3b9d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = """
ALTER TABLE people
    ADD COLUMN strength double precision NOT NULL DEFAULT 0,
    ADD COLUMN title varchar(300) NOT NULL DEFAULT '',
    ADD COLUMN phones jsonb NOT NULL DEFAULT '[]',
    ADD COLUMN addresses jsonb NOT NULL DEFAULT '[]',
    ADD COLUMN birthday varchar(10) NOT NULL DEFAULT '',
    ADD COLUMN provenance jsonb NOT NULL DEFAULT '{}',
    ADD COLUMN signature_checked_id integer;
CREATE INDEX ix_people_user_strength ON people (user_id, strength DESC);

CREATE TABLE person_groups (
    id serial PRIMARY KEY,
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name varchar(200) NOT NULL DEFAULT '',
    colour varchar(9),
    source varchar(12) NOT NULL DEFAULT 'manual',
    state varchar(12) NOT NULL DEFAULT 'active',
    connector_id integer REFERENCES connectors(id) ON DELETE CASCADE,
    remote_key varchar(500) NOT NULL DEFAULT '',
    name_hint varchar(200) NOT NULL DEFAULT '',
    score double precision NOT NULL DEFAULT 0,
    evidence jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_person_groups_user_state ON person_groups (user_id, state);
CREATE UNIQUE INDEX uq_person_groups_remote ON person_groups (connector_id, remote_key) WHERE connector_id IS NOT NULL;

CREATE TABLE person_group_members (
    group_id integer NOT NULL REFERENCES person_groups(id) ON DELETE CASCADE,
    email varchar(320) NOT NULL,
    name varchar(300) NOT NULL DEFAULT '',
    PRIMARY KEY (group_id, email)
);

CREATE TABLE person_suggestions (
    id serial PRIMARY KEY,
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email varchar(320) NOT NULL,
    field varchar(20) NOT NULL,
    value text NOT NULL,
    norm text NOT NULL,
    source varchar(20) NOT NULL DEFAULT 'signature',
    method varchar(10) NOT NULL DEFAULT 'rules',
    message_id integer REFERENCES messages(id) ON DELETE SET NULL,
    local_only boolean NOT NULL DEFAULT false,
    state varchar(12) NOT NULL DEFAULT 'suggested',
    created_at timestamptz NOT NULL DEFAULT now(),
    decided_at timestamptz,
    CONSTRAINT uq_person_suggestions UNIQUE (user_id, email, field, norm)
);

CREATE TABLE address_books (
    id serial PRIMARY KEY,
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    connector_id integer NOT NULL REFERENCES connectors(id) ON DELETE CASCADE,
    href text NOT NULL,
    displayname text NOT NULL DEFAULT '',
    sync_token text,
    ctag text,
    synced_at timestamptz,
    error text NOT NULL DEFAULT '',
    CONSTRAINT uq_address_books UNIQUE (connector_id, href)
);

CREATE TABLE contact_cards (
    id bigserial PRIMARY KEY,
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    connector_id integer NOT NULL REFERENCES connectors(id) ON DELETE CASCADE,
    book_id integer NOT NULL REFERENCES address_books(id) ON DELETE CASCADE,
    href text NOT NULL,
    etag text,
    uid varchar(500) NOT NULL DEFAULT '',
    kind varchar(12) NOT NULL DEFAULT 'individual',
    data jsonb NOT NULL DEFAULT '{}',
    synced_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_contact_cards UNIQUE (connector_id, href)
);
CREATE INDEX ix_contact_cards_user ON contact_cards (user_id);
"""


def upgrade() -> None:
    for stmt in SCHEMA.split(";"):
        if stmt.strip():
            op.execute(stmt)


def downgrade() -> None:
    for t in ("contact_cards", "address_books", "person_suggestions", "person_group_members", "person_groups"):
        op.execute(f"DROP TABLE IF EXISTS {t}")
    op.execute("DROP INDEX IF EXISTS ix_people_user_strength")
    for c in ("strength", "title", "phones", "addresses", "birthday", "provenance", "signature_checked_id"):
        op.execute(f"ALTER TABLE people DROP COLUMN IF EXISTS {c}")
