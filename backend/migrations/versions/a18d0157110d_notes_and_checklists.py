"""Notes and checklists

Revision ID: a18d0157110d
Revises: c4e8a2f6d9b1
Create Date: 2026-10-05 22:00:00

Keep-style notes (text or checklist), sections and
items, the user's own note labels, per-viewer state (pins, archive, labels, grid order), revisions
for 30 days, links, the check log for learned order and usual items, learned ranks and dismissed
suggestions. The owner columns and the per-viewer tables are ready for sharing.

New tables only, so nothing locks an existing table. Re-runnable: each table and index is made
only when it is missing.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import HALFVEC
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID

revision: str = 'a18d0157110d'
down_revision: str | Sequence[str] | None = 'c4e8a2f6d9b1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("checkitem_usual_dismissed", "checkitem_ranks", "checkitem_checks", "note_links", "note_revisions",
          "note_labels", "note_items", "note_section_viewer", "note_sections", "note_viewer_state", "notes")


def _ts(name: str, nullable: bool = True, now: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable,
                     server_default=sa.func.now() if now else None)


def upgrade() -> None:
    have = set(sa.inspect(op.get_bind()).get_table_names())

    def create(name: str, *cols, indexes: Sequence[tuple[str, Sequence, dict]] = ()) -> None:
        if name not in have:
            op.create_table(name, *cols)
        for ix, columns, kw in indexes:
            op.create_index(ix, name, columns, if_not_exists=True, **kw)

    create(
        "notes",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("pid", sa.String(16), nullable=False, unique=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("owner_group", sa.String(200)),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("kind", sa.String(10), nullable=False, server_default="text"),
        sa.Column("title", sa.String(500), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("color", sa.String(20)),
        sa.Column("shopping", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("checked_at_bottom", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("suggest_usual", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("local_only", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("top_order_mode", sa.String(10), nullable=False, server_default="manual"),
        _ts("top_learned_through"),
        sa.Column("top_trips", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("import_key", sa.String(100)),
        _ts("created_at", False, True),
        _ts("updated_at", False, True),
        sa.Column("updated_by", JSONB(), nullable=False, server_default="{}"),
        _ts("deleted_at"),
        sa.Column("deleted_by", sa.Integer()),
        sa.Column("tsv", TSVECTOR(), sa.Computed(
            "to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(body, ''))", persisted=True)),
        sa.Column("embedding", HALFVEC(512)),
        sa.Column("embed_model", sa.String(200)),
        sa.Column("embed_hash", sa.String(64)),
        sa.CheckConstraint("(owner_user_id IS NULL) <> (owner_group IS NULL)", name="ck_notes_one_owner"),
        indexes=[("ix_notes_owner_user", ["owner_user_id", "deleted_at"], {}),
                 ("ix_notes_owner_group", ["owner_group"], {"postgresql_where": sa.text("owner_group IS NOT NULL")}),
                 ("ix_notes_tsv", ["tsv"], {"postgresql_using": "gin"}),
                 ("ix_notes_updated", ["updated_at"], {})],
    )
    create(
        "note_viewer_state",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("item_kind", sa.String(8), primary_key=True),
        sa.Column("item_id", sa.BigInteger(), primary_key=True),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("labels", ARRAY(sa.String(50)), nullable=False, server_default="{}"),
        sa.Column("order", sa.String(64)),
        sa.Column("seen_version", sa.Integer()),
        sa.Column("top_collapsed", sa.Boolean(), nullable=False, server_default=sa.false()),
        _ts("updated_at", False, True),
        indexes=[("ix_note_viewer_labels", ["labels"], {"postgresql_using": "gin"})],
    )
    create(
        "note_sections",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("note_id", sa.BigInteger(), sa.ForeignKey("notes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("order", sa.String(64), nullable=False),
        sa.Column("order_mode", sa.String(10), nullable=False, server_default="manual"),
        _ts("learned_through"),
        sa.Column("trips", sa.Integer(), nullable=False, server_default="0"),
        _ts("created_at", False, True),
        _ts("deleted_at"),
        indexes=[("ix_note_sections_note", ["note_id"], {})],
    )
    create(
        "note_section_viewer",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("section_id", UUID(as_uuid=False), sa.ForeignKey("note_sections.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("collapsed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    create(
        "note_items",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("note_id", sa.BigInteger(), sa.ForeignKey("notes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("section_id", UUID(as_uuid=False), sa.ForeignKey("note_sections.id", ondelete="SET NULL")),
        sa.Column("text", sa.String(1000), nullable=False),
        sa.Column("key", sa.String(1000), nullable=False),
        sa.Column("qty_amount", sa.Numeric(14, 3)),
        sa.Column("qty_unit", sa.String(10)),
        sa.Column("qty_text", sa.String(40)),
        sa.Column("extra", sa.String(1000)),
        sa.Column("checked", sa.Boolean(), nullable=False, server_default=sa.false()),
        _ts("checked_at"),
        _ts("checked_server_at"),
        sa.Column("checked_by", sa.Integer()),
        sa.Column("order", sa.String(64), nullable=False),
        sa.Column("task_uid", sa.String(300)),
        sa.Column("added_by", sa.Integer()),
        _ts("added_at", False, True),
        sa.Column("updated_by", JSONB(), nullable=False, server_default="{}"),
        _ts("updated_at", False, True),
        _ts("deleted_at"),
        sa.Column("tsv", TSVECTOR(), sa.Computed(
            "to_tsvector('simple', coalesce(text, '') || ' ' || coalesce(extra, ''))", persisted=True)),
        indexes=[("ix_note_items_note", ["note_id", "section_id"], {}),
                 ("ix_note_items_tsv", ["tsv"], {"postgresql_using": "gin"})],
    )
    create(
        "note_labels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("color", sa.String(20)),
        sa.Column("order", sa.String(64), nullable=False),
        _ts("created_at", False, True),
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_note_labels_name ON note_labels (user_id, lower(name))")
    create(
        "note_revisions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("note_id", sa.BigInteger(), sa.ForeignKey("notes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rev", sa.Integer(), nullable=False),
        _ts("opened_at", False, True),
        _ts("at", False, True),
        sa.Column("by", JSONB(), nullable=False, server_default="{}"),
        sa.Column("source", sa.String(12), nullable=False, server_default="edit"),
        sa.Column("closed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("content", JSONB(), nullable=False),
        sa.Column("summary", sa.String(300), nullable=False, server_default=""),
        sa.UniqueConstraint("note_id", "rev", name="uq_note_revisions"),
        indexes=[("ix_note_revisions_at", ["at"], {})],
    )
    create(
        "note_links",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("note_id", sa.BigInteger(), sa.ForeignKey("notes.id", ondelete="CASCADE")),
        sa.Column("table_id", sa.BigInteger()),
        sa.Column("item_id", UUID(as_uuid=False)),
        sa.Column("target_kind", sa.String(16), nullable=False),
        sa.Column("target_ref", JSONB(), nullable=False),
        sa.Column("target_key", sa.String(400), nullable=False),
        sa.Column("title", sa.String(300), nullable=False, server_default=""),
        sa.Column("created_by", sa.Integer()),
        _ts("created_at", False, True),
        indexes=[("ix_note_links_note", ["note_id"], {}), ("ix_note_links_target", ["target_key"], {})],
    )
    create(
        "checkitem_checks",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("note_id", sa.BigInteger(), sa.ForeignKey("notes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("section_id", UUID(as_uuid=False)),
        sa.Column("item_id", UUID(as_uuid=False)),
        sa.Column("key", sa.String(1000), nullable=False),
        sa.Column("text", sa.String(1000), nullable=False, server_default=""),
        sa.Column("qty", JSONB()),
        sa.Column("user_id", sa.Integer()),
        _ts("device_at", False),
        _ts("server_at", False, True),
        sa.Column("undone", sa.Boolean(), nullable=False, server_default=sa.false()),
        indexes=[("ix_checkitem_checks_trip", ["note_id", "section_id", "device_at"], {}),
                 ("ix_checkitem_checks_key", ["note_id", "key"], {})],
    )
    create(
        "checkitem_ranks",
        sa.Column("note_id", sa.BigInteger(), sa.ForeignKey("notes.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("section_key", sa.String(36), primary_key=True),
        sa.Column("key", sa.String(1000), primary_key=True),
        sa.Column("rank", sa.Float(), nullable=False),
        sa.Column("trips", sa.Integer(), nullable=False, server_default="0"),
    )
    create(
        "checkitem_usual_dismissed",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("note_id", sa.BigInteger(), sa.ForeignKey("notes.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("key", sa.String(1000), primary_key=True),
        sa.Column("text", sa.String(1000), nullable=False, server_default=""),
        sa.Column("until", sa.Date()),
        _ts("created_at", False, True),
    )


def downgrade() -> None:
    for t in TABLES:
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
