"""The tables of tables.

A table is owned by a user or by a group, as a note is (`owner_user_id` or `owner_group`, exactly
one); pins, archive, labels and grid order live in `note_viewer_state` with `item_kind = "table"`.
Every query goes through tables/access.py.

Columns are rows of `table_columns` with a short stable `col_id`. A row is one JSONB document
(`cells`: col_id → typed value, empty cells absent; `raw`: col_id → text that did not convert).
Views are rows of `table_views`. Every row change is kept for 30 days in `table_row_changes`, every
schema change (columns, views, the table's own fields) in `table_column_changes`, both numbered by
one sequence per table (`tables.seq`). `table_snapshots` holds the nightly snapshots that a
restore to a point in time starts from. `table_row_refs` indexes the link cells for "Linked from".
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mailr.db import Base

EMBED_DIM = 512


def _now():
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DataTable(Base):
    """A table: its own fields. Columns, rows and views are in their own tables."""

    __tablename__ = "tables"
    __mapper_args__ = {"eager_defaults": True}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    pid: Mapped[str] = mapped_column(String(16), unique=True)  # the public id, "t…"
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    owner_group: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(200), default="", server_default="")
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    color: Mapped[str | None] = mapped_column(String(20))
    local_only: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    primary_column: Mapped[str | None] = mapped_column(String(16))
    # The last change number given out (row and schema changes share it).
    seq: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    import_key: Mapped[str | None] = mapped_column(String(100))  # the source's id when imported
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()
    updated_by: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[int | None] = mapped_column(Integer)
    tsv: Mapped[Any] = mapped_column(TSVECTOR, Computed(
        "to_tsvector('simple', coalesce(name, '') || ' ' || coalesce(description, ''))", persisted=True))
    embedding: Mapped[Any] = mapped_column(HALFVEC(EMBED_DIM), nullable=True)
    embed_model: Mapped[str | None] = mapped_column(String(200))
    embed_hash: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (
        CheckConstraint("(owner_user_id IS NULL) <> (owner_group IS NULL)", name="ck_tables_one_owner"),
        Index("ix_tables_owner_user", "owner_user_id", "deleted_at"),
        Index("ix_tables_owner_group", "owner_group", postgresql_where=text("owner_group IS NOT NULL")),
        Index("ix_tables_tsv", "tsv", postgresql_using="gin"),
    )


from mailr.notes.models import share_acl  # the shares live with the notes tables  # noqa: E402

DataTable.share_acl = share_acl("table", DataTable.id)


class TableColumn(Base):
    __tablename__ = "table_columns"
    __mapper_args__ = {"eager_defaults": True}
    table_id: Mapped[int] = mapped_column(ForeignKey("tables.id", ondelete="CASCADE"), primary_key=True)
    col_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    type: Mapped[str] = mapped_column(String(16))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    required: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    width: Mapped[int | None] = mapped_column(Integer)
    order: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _now()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TableRow(Base):
    __tablename__ = "table_rows"
    __mapper_args__ = {"eager_defaults": True}
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    table_id: Mapped[int] = mapped_column(ForeignKey("tables.id", ondelete="CASCADE"))
    cells: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    order: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _now()
    updated_by: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    updated_at: Mapped[datetime] = _now()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[int | None] = mapped_column(Integer)
    # The text the row is found by: text cells, kept-as-text values and option names (tables/core.py).
    search_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    import_key: Mapped[str | None] = mapped_column(String(100))
    tsv: Mapped[Any] = mapped_column(TSVECTOR, Computed("to_tsvector('simple', coalesce(search_text, ''))",
                                                        persisted=True))
    __table_args__ = (
        Index("ix_table_rows_table", "table_id", "deleted_at"),
        Index("ix_table_rows_tsv", "tsv", postgresql_using="gin"),
        Index("ix_table_rows_cells", "cells", postgresql_using="gin", postgresql_ops={"cells": "jsonb_path_ops"}),
    )


class TableView(Base):
    __tablename__ = "table_views"
    __mapper_args__ = {"eager_defaults": True}
    table_id: Mapped[int] = mapped_column(ForeignKey("tables.id", ondelete="CASCADE"), primary_key=True)
    view_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    # {sort, filters, group_by, hidden, footers, phone_columns} (tables/views.py)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    order: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class TableRowChange(Base):
    """One add, edit, delete, restore or move of a row: the changed fields before and after.
    `before` and `after` are {cells?: {col: value | null}, raw?: {...}, order?, deleted?}."""

    __tablename__ = "table_row_changes"
    __mapper_args__ = {"eager_defaults": True}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    table_id: Mapped[int] = mapped_column(ForeignKey("tables.id", ondelete="CASCADE"))
    row_id: Mapped[str] = mapped_column(UUID(as_uuid=False))
    seq: Mapped[int] = mapped_column(BigInteger)
    at: Mapped[datetime] = _now()
    by: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    # edit | assistant | agent | import | restore | paste | undo
    source: Mapped[str] = mapped_column(String(12), default="edit", server_default="edit")
    op: Mapped[str] = mapped_column(String(10))  # add | edit | delete | restore | move
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    batch: Mapped[str | None] = mapped_column(String(36))
    __table_args__ = (Index("ix_table_row_changes_seq", "table_id", "seq"),
                      Index("ix_table_row_changes_row", "table_id", "row_id", "seq"),
                      Index("ix_table_row_changes_batch", "table_id", "batch"))


class TableSchemaChange(Base):
    """One change of the table's schema, views or own fields. `col_id` names the column (null for
    a view or the table); `values` holds the cells a delete or a retype removed or converted:
    {"before": {row: [cell, raw]}, "after": {row: [cell, raw]}}."""

    __tablename__ = "table_column_changes"
    __mapper_args__ = {"eager_defaults": True}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    table_id: Mapped[int] = mapped_column(ForeignKey("tables.id", ondelete="CASCADE"))
    col_id: Mapped[str | None] = mapped_column(String(16))
    view_id: Mapped[str | None] = mapped_column(String(16))
    seq: Mapped[int] = mapped_column(BigInteger)
    at: Mapped[datetime] = _now()
    by: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    source: Mapped[str] = mapped_column(String(12), default="edit", server_default="edit")
    # add | rename | retype | config | options | reorder | delete | restore | primary
    # | table (name, description, colour) | view_save | view_delete
    op: Mapped[str] = mapped_column(String(12))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    values: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    batch: Mapped[str | None] = mapped_column(String(36))
    __table_args__ = (Index("ix_table_column_changes_seq", "table_id", "seq"),
                      Index("ix_table_column_changes_batch", "table_id", "batch"))


class TableSnapshot(Base):
    """The whole table at change `seq`: fields, columns (deleted ones too), views and live rows,
    as zlib-compressed JSON (tables/history.py)."""

    __tablename__ = "table_snapshots"
    __mapper_args__ = {"eager_defaults": True}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    table_id: Mapped[int] = mapped_column(ForeignKey("tables.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(BigInteger)
    taken_at: Mapped[datetime] = _now()
    data: Mapped[bytes] = mapped_column(LargeBinary)
    __table_args__ = (Index("ix_table_snapshots_table", "table_id", "seq"),)


class TableRowRef(Base):
    """A link cell's target, for "Linked from" and the links panels of mail, events and tasks.
    `target_key`: "row:<uuid>", "conversation:<id>", "event:<uid>", "task:<uid>", "note:<pid>"."""

    __tablename__ = "table_row_refs"
    table_id: Mapped[int] = mapped_column(ForeignKey("tables.id", ondelete="CASCADE"), primary_key=True)
    row_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    col_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    target_key: Mapped[str] = mapped_column(String(400), primary_key=True)
    target_kind: Mapped[str] = mapped_column(String(16))
    __table_args__ = (Index("ix_table_row_refs_target", "target_key"),)
