"""Tables

Revision ID: 260bc64180d2
Revises: a18d0157110d
Create Date: 2026-10-06 01:00:00

Tables with typed
columns, rows as JSONB documents, saved views, 30 days of per-row and schema history, nightly
snapshots for a restore to a point in time, and an index of link cells. Per-viewer state reuses
`note_viewer_state` (item_kind "table"); links to mail, events, tasks and recordings reuse
`note_links.table_id`.

New tables only, so nothing locks an existing table. Re-runnable: each table and index is made
only when it is missing.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import HALFVEC
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID

revision: str = '260bc64180d2'
down_revision: str | Sequence[str] | None = 'a18d0157110d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("table_row_refs", "table_snapshots", "table_column_changes", "table_row_changes", "table_views",
          "table_rows", "table_columns", "tables")


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
        "tables",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("pid", sa.String(16), nullable=False, unique=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("owner_group", sa.String(200)),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("name", sa.String(200), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("color", sa.String(20)),
        sa.Column("local_only", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("primary_column", sa.String(16)),
        sa.Column("seq", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("import_key", sa.String(100)),
        _ts("created_at", False, True),
        _ts("updated_at", False, True),
        sa.Column("updated_by", JSONB(), nullable=False, server_default="{}"),
        _ts("deleted_at"),
        sa.Column("deleted_by", sa.Integer()),
        sa.Column("tsv", TSVECTOR(), sa.Computed(
            "to_tsvector('simple', coalesce(name, '') || ' ' || coalesce(description, ''))", persisted=True)),
        sa.Column("embedding", HALFVEC(512)),
        sa.Column("embed_model", sa.String(200)),
        sa.Column("embed_hash", sa.String(64)),
        sa.CheckConstraint("(owner_user_id IS NULL) <> (owner_group IS NULL)", name="ck_tables_one_owner"),
        indexes=[("ix_tables_owner_user", ["owner_user_id", "deleted_at"], {}),
                 ("ix_tables_owner_group", ["owner_group"], {"postgresql_where": sa.text("owner_group IS NOT NULL")}),
                 ("ix_tables_tsv", ["tsv"], {"postgresql_using": "gin"})],
    )
    create(
        "table_columns",
        sa.Column("table_id", sa.BigInteger(), sa.ForeignKey("tables.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("col_id", sa.String(16), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("config", JSONB(), nullable=False, server_default="{}"),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("width", sa.Integer()),
        sa.Column("order", sa.String(64), nullable=False),
        _ts("created_at", False, True),
        _ts("deleted_at"),
    )
    create(
        "table_rows",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("table_id", sa.BigInteger(), sa.ForeignKey("tables.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cells", JSONB(), nullable=False, server_default="{}"),
        sa.Column("raw", JSONB(), nullable=False, server_default="{}"),
        sa.Column("order", sa.String(64), nullable=False),
        sa.Column("created_by", sa.Integer()),
        _ts("created_at", False, True),
        sa.Column("updated_by", JSONB(), nullable=False, server_default="{}"),
        _ts("updated_at", False, True),
        _ts("deleted_at"),
        sa.Column("deleted_by", sa.Integer()),
        sa.Column("search_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("import_key", sa.String(100)),
        sa.Column("tsv", TSVECTOR(), sa.Computed("to_tsvector('simple', coalesce(search_text, ''))", persisted=True)),
        indexes=[("ix_table_rows_table", ["table_id", "deleted_at"], {}),
                 ("ix_table_rows_tsv", ["tsv"], {"postgresql_using": "gin"}),
                 ("ix_table_rows_cells", ["cells"], {"postgresql_using": "gin",
                                                     "postgresql_ops": {"cells": "jsonb_path_ops"}})],
    )
    create(
        "table_views",
        sa.Column("table_id", sa.BigInteger(), sa.ForeignKey("tables.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("view_id", sa.String(16), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("spec", JSONB(), nullable=False, server_default="{}"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("order", sa.String(64), nullable=False),
        _ts("created_at", False, True),
        _ts("updated_at", False, True),
    )
    create(
        "table_row_changes",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("table_id", sa.BigInteger(), sa.ForeignKey("tables.id", ondelete="CASCADE"), nullable=False),
        sa.Column("row_id", UUID(as_uuid=False), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        _ts("at", False, True),
        sa.Column("by", JSONB(), nullable=False, server_default="{}"),
        sa.Column("source", sa.String(12), nullable=False, server_default="edit"),
        sa.Column("op", sa.String(10), nullable=False),
        sa.Column("before", JSONB()),
        sa.Column("after", JSONB()),
        sa.Column("batch", sa.String(36)),
        indexes=[("ix_table_row_changes_seq", ["table_id", "seq"], {}),
                 ("ix_table_row_changes_row", ["table_id", "row_id", "seq"], {}),
                 ("ix_table_row_changes_batch", ["table_id", "batch"], {})],
    )
    create(
        "table_column_changes",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("table_id", sa.BigInteger(), sa.ForeignKey("tables.id", ondelete="CASCADE"), nullable=False),
        sa.Column("col_id", sa.String(16)),
        sa.Column("view_id", sa.String(16)),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        _ts("at", False, True),
        sa.Column("by", JSONB(), nullable=False, server_default="{}"),
        sa.Column("source", sa.String(12), nullable=False, server_default="edit"),
        sa.Column("op", sa.String(12), nullable=False),
        sa.Column("before", JSONB()),
        sa.Column("after", JSONB()),
        sa.Column("values", JSONB()),
        sa.Column("batch", sa.String(36)),
        indexes=[("ix_table_column_changes_seq", ["table_id", "seq"], {}),
                 ("ix_table_column_changes_batch", ["table_id", "batch"], {})],
    )
    create(
        "table_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("table_id", sa.BigInteger(), sa.ForeignKey("tables.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        _ts("taken_at", False, True),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        indexes=[("ix_table_snapshots_table", ["table_id", "seq"], {})],
    )
    create(
        "table_row_refs",
        sa.Column("table_id", sa.BigInteger(), sa.ForeignKey("tables.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("row_id", UUID(as_uuid=False), primary_key=True),
        sa.Column("col_id", sa.String(16), primary_key=True),
        sa.Column("target_key", sa.String(400), primary_key=True),
        sa.Column("target_kind", sa.String(16), nullable=False),
        indexes=[("ix_table_row_refs_target", ["target_key"], {})],
    )


def downgrade() -> None:
    for t in TABLES:
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
