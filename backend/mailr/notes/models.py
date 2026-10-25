"""The tables of notes and checklists.

The model is built for sharing: a note is owned by a user or by a group
(`owner_user_id` or `owner_group`, exactly one), and pins, archive, labels, grid order and
collapsed sections are per viewer (`note_viewer_state`, `note_section_viewer`). Every query goes
through notes/access.py, the one place that decides who sees a note.

Items and sections have UUIDs the client may make (an offline add replays once). Their order is a
fractional key (notes/order.py) made by the server. Checks are kept as an event log
(`checkitem_checks`) for learned order and usual items; `checkitem_ranks` holds the ranks learned
from it.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, column_property, mapped_column

from mailr.db import Base

EMBED_DIM = 512


def _now():
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Note(Base):
    """A Keep-style note: `kind` text (a body) or checklist (items in `note_items`)."""

    __tablename__ = "notes"
    __mapper_args__ = {"eager_defaults": True}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    pid: Mapped[str] = mapped_column(String(16), unique=True)  # the public id, "n…"
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    owner_group: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(10), default="text", server_default="text")
    title: Mapped[str] = mapped_column(String(500), default="", server_default="")
    body: Mapped[str] = mapped_column(Text, default="", server_default="")
    color: Mapped[str | None] = mapped_column(String(20))
    shopping: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    checked_at_bottom: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    suggest_usual: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    local_only: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # The top group (items with no section) has an order mode and learned state like a section.
    top_order_mode: Mapped[str] = mapped_column(String(10), default="manual", server_default="manual")
    top_learned_through: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    top_trips: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    import_key: Mapped[str | None] = mapped_column(String(100))  # the source's id when imported
    # A repeating checklist (notes/repeat.py): the rule, the setter's zone, the next and last reset.
    repeat_rule: Mapped[str | None] = mapped_column(String(200))
    repeat_zone: Mapped[str | None] = mapped_column(String(64))
    repeat_next: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    repeat_last: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()
    # {"kind": user | device | assistant | agent | import | restore, "id"?, "name"?}
    updated_by: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[int | None] = mapped_column(Integer)
    tsv: Mapped[Any] = mapped_column(TSVECTOR, Computed(
        "to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(body, ''))", persisted=True))
    embedding: Mapped[Any] = mapped_column(HALFVEC(EMBED_DIM), nullable=True)
    embed_model: Mapped[str | None] = mapped_column(String(200))
    embed_hash: Mapped[str | None] = mapped_column(String(64))  # of the text last embedded
    __table_args__ = (
        CheckConstraint("(owner_user_id IS NULL) <> (owner_group IS NULL)", name="ck_notes_one_owner"),
        Index("ix_notes_owner_user", "owner_user_id", "deleted_at"),
        Index("ix_notes_owner_group", "owner_group", postgresql_where=text("owner_group IS NOT NULL")),
        Index("ix_notes_tsv", "tsv", postgresql_using="gin"),
        Index("ix_notes_updated", "updated_at"),
        Index("ix_notes_repeat_next", "repeat_next", postgresql_where=text("repeat_next IS NOT NULL")),
    )


class NoteViewerState(Base):
    """One viewer's own view of a note or a table: pins, archive, labels, grid order."""

    __tablename__ = "note_viewer_state"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    item_kind: Mapped[str] = mapped_column(String(8), primary_key=True)  # note | table
    item_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    labels: Mapped[list[str]] = mapped_column(ARRAY(String(50)), default=list, server_default="{}")
    order: Mapped[str | None] = mapped_column(String(64))
    seen_version: Mapped[int | None] = mapped_column(Integer)
    # When this viewer last opened it, for the "changed since you looked" dot.
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    top_collapsed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # A table only: the date column this viewer shows on their calendar (tables/calendar.py).
    calendar_column: Mapped[str | None] = mapped_column(String(16))
    updated_at: Mapped[datetime] = _now()
    __table_args__ = (Index("ix_note_viewer_labels", "labels", postgresql_using="gin"),)


class NoteShare(Base):
    """A note or a table shared with a mailr user or a mailr group.

    `principal` is the user's id (as text) or the group's name. `level` is view, edit or share. A
    user who leaves keeps a row with `left_at` set: it hides the item from them even when a group
    they are in still has it. Leaving a share that came only through a group writes such a row
    with level `none`. A user's own row wins over their groups' rows, as in calendar sharing."""

    __tablename__ = "note_shares"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    item_kind: Mapped[str] = mapped_column(String(8))  # note | table
    item_id: Mapped[int] = mapped_column(BigInteger)
    principal_kind: Mapped[str] = mapped_column(String(8))  # user | group
    principal: Mapped[str] = mapped_column(String(200))
    level: Mapped[str] = mapped_column(String(8))  # view | edit | share | none (a leave)
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _now()
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("item_kind", "item_id", "principal_kind", "principal", name="uq_note_shares"),
        Index("ix_note_shares_principal", "principal_kind", "principal"),
    )


def share_acl(kind: str, id_col: Any) -> Any:
    """The shares of a note or a table, loaded with it: [[principal_kind, principal, level, left], …].
    notes/access.py and tables/access.py read it, so `level()` needs no query of its own."""
    from sqlalchemy import select

    s = NoteShare.__table__
    return column_property(
        select(func.coalesce(func.jsonb_agg(func.jsonb_build_array(
            s.c.principal_kind, s.c.principal, s.c.level, s.c.left_at.is_not(None))), text("'[]'::jsonb")))
        .where(s.c.item_kind == kind, s.c.item_id == id_col).correlate_except(s).scalar_subquery())


Note.share_acl = share_acl("note", Note.id)


class NoteSection(Base):
    __tablename__ = "note_sections"
    __mapper_args__ = {"eager_defaults": True}
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    note_id: Mapped[int] = mapped_column(ForeignKey("notes.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    order: Mapped[str] = mapped_column(String(64))
    order_mode: Mapped[str] = mapped_column(String(10), default="manual", server_default="manual")
    learned_through: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trips: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = _now()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_note_sections_note", "note_id"),)


class NoteSectionViewer(Base):
    __tablename__ = "note_section_viewer"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    section_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("note_sections.id", ondelete="CASCADE"),
                                            primary_key=True)
    collapsed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class NoteItem(Base):
    """One checklist item. `checked_at` is the device time of the tap (learned order reads it);
    `checked_server_at` when the server received it."""

    __tablename__ = "note_items"
    __mapper_args__ = {"eager_defaults": True}
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    note_id: Mapped[int] = mapped_column(ForeignKey("notes.id", ondelete="CASCADE"))
    section_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False),
                                                   ForeignKey("note_sections.id", ondelete="SET NULL"))
    text: Mapped[str] = mapped_column(String(1000))
    key: Mapped[str] = mapped_column(String(1000))
    qty_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    qty_unit: Mapped[str | None] = mapped_column(String(10))
    qty_text: Mapped[str | None] = mapped_column(String(40))
    extra: Mapped[str | None] = mapped_column(String(1000))
    checked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_server_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_by: Mapped[int | None] = mapped_column(Integer)
    order: Mapped[str] = mapped_column(String(64))
    task_uid: Mapped[str | None] = mapped_column(String(300))
    added_by: Mapped[int | None] = mapped_column(Integer)
    added_at: Mapped[datetime] = _now()
    updated_by: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    updated_at: Mapped[datetime] = _now()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tsv: Mapped[Any] = mapped_column(TSVECTOR, Computed(
        "to_tsvector('simple', coalesce(text, '') || ' ' || coalesce(extra, ''))", persisted=True))
    __table_args__ = (
        Index("ix_note_items_note", "note_id", "section_id"),
        Index("ix_note_items_tsv", "tsv", postgresql_using="gin"),
    )


class NoteLabel(Base):
    """A user's own label for notes and tables (not mail labels, not task tags)."""

    __tablename__ = "note_labels"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(50))
    color: Mapped[str | None] = mapped_column(String(20))
    order: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _now()
    __table_args__ = (Index("uq_note_labels_name", "user_id", text("lower(name)"), unique=True),)


class NoteRevision(Base):
    """A saved version of a note for history and restore (30 days, the newest always kept).
    Separate from the merge versions in item_versions."""

    __tablename__ = "note_revisions"
    __mapper_args__ = {"eager_defaults": True}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    note_id: Mapped[int] = mapped_column(ForeignKey("notes.id", ondelete="CASCADE"))
    rev: Mapped[int] = mapped_column(Integer)
    opened_at: Mapped[datetime] = _now()  # the start of the editing window it holds
    at: Mapped[datetime] = _now()  # its last write
    by: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    source: Mapped[str] = mapped_column(String(12), default="edit", server_default="edit")
    closed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # {kind, title, body, color, shopping, sections: [...], items: [...]}
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    summary: Mapped[str] = mapped_column(String(300), default="", server_default="")
    __table_args__ = (UniqueConstraint("note_id", "rev", name="uq_note_revisions"),
                      Index("ix_note_revisions_at", "at"))


class NoteLink(Base):
    """A link from a note (or a table) to a conversation, event, task or recording."""

    __tablename__ = "note_links"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    note_id: Mapped[int | None] = mapped_column(ForeignKey("notes.id", ondelete="CASCADE"))
    table_id: Mapped[int | None] = mapped_column(BigInteger)
    item_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))  # an item's link (checkitem.to_task)
    target_kind: Mapped[str] = mapped_column(String(16))  # conversation | event | task | recording
    target_ref: Mapped[dict[str, Any]] = mapped_column(JSONB)
    target_key: Mapped[str] = mapped_column(String(400))  # "<kind>:<id>", for the reverse lookup
    title: Mapped[str] = mapped_column(String(300), default="", server_default="")
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _now()
    __table_args__ = (Index("ix_note_links_note", "note_id"), Index("ix_note_links_target", "target_key"))


class CheckitemCheck(Base):
    """One check of an item, for learned order and usual items (kept 365 days). `device_at` is
    the time of the tap on the device. `undone`: unchecked again within its trip."""

    __tablename__ = "checkitem_checks"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    note_id: Mapped[int] = mapped_column(ForeignKey("notes.id", ondelete="CASCADE"))
    section_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    item_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    key: Mapped[str] = mapped_column(String(1000))
    text: Mapped[str] = mapped_column(String(1000), default="", server_default="")
    qty: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    user_id: Mapped[int | None] = mapped_column(Integer)
    device_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    server_at: Mapped[datetime] = _now()
    undone: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    __table_args__ = (Index("ix_checkitem_checks_trip", "note_id", "section_id", "device_at"),
                      Index("ix_checkitem_checks_key", "note_id", "key"))


class CheckitemRank(Base):
    """The learned rank of an item key in one section of one list. `section_key` is the section's
    id, or "" for the top group."""

    __tablename__ = "checkitem_ranks"
    note_id: Mapped[int] = mapped_column(ForeignKey("notes.id", ondelete="CASCADE"), primary_key=True)
    section_key: Mapped[str] = mapped_column(String(36), primary_key=True)
    key: Mapped[str] = mapped_column(String(1000), primary_key=True)
    rank: Mapped[float] = mapped_column(Float)
    trips: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class CheckitemUsualDismissed(Base):
    """A usual-item suggestion this viewer hid: until a day, or for good (`until` null)."""

    __tablename__ = "checkitem_usual_dismissed"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    note_id: Mapped[int] = mapped_column(ForeignKey("notes.id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(String(1000), primary_key=True)
    text: Mapped[str] = mapped_column(String(1000), default="", server_default="")
    until: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = _now()


class NoteReminder(Base):
    """A time-based reminder on a note. It belongs to the viewer who
    set it: a sharee's reminder reminds only them. The key every device plans with is
    `n|<note pid>|abs|<instant>` (notes/reminders.py); its state (fired, snoozed, handled) lives in
    `reminder_states` with the event and task reminders, so handled anywhere is gone everywhere."""

    __tablename__ = "note_reminders"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    note_id: Mapped[int] = mapped_column(ForeignKey("notes.id", ondelete="CASCADE"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()
    __table_args__ = (UniqueConstraint("user_id", "note_id", "at", name="uq_note_reminders"),
                      Index("ix_note_reminders_user_at", "user_id", "at"),
                      Index("ix_note_reminders_note", "note_id"))


class NoteTemplate(Base):
    """A user's own template (`note.save_template`): a text note, a checklist (its
    sections and items, all unchecked) or a table (its columns). Built-in starter templates are in
    notes/templates.py, not in this table."""

    __tablename__ = "note_templates"
    __mapper_args__ = {"eager_defaults": True}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(10))  # text | checklist | table
    name: Mapped[str] = mapped_column(String(200))
    # text: {title, body}; checklist: {title, shopping, sections: [{name, order_mode}],
    # items: [{text, qty, extra, section}]}; table: {name, description, columns: [{name, type, config, required, width}]}
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()
    __table_args__ = (Index("ix_note_templates_user", "user_id"),)
