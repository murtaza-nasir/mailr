"""Response models of the notes reads (notes/routes.py, notes/core.py base_out, note_cards, note_full)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from mailr.api.contract.base import IsoTime, Out, Untyped, absent


class NoteState(Out):
    """The viewer's own state of a note (pinned, archived, labels, order)."""

    pinned: bool
    archived: bool
    labels: list[str]
    order: str | None
    seen_version: int | None = None
    seen_at: IsoTime | None = None
    calendar_column: str | None = None


class NoteCounts(Out):
    items: int
    unchecked: int
    checked: int
    sections: int


class PreviewItem(Out):
    id: str
    text: str
    qty: Untyped | None
    section: str | None


class NotePreview(Out):
    items: list[PreviewItem]
    more: int
    checked: int


class NoteSection(Out):
    id: str
    name: str
    order: str
    order_mode: str
    collapsed: bool
    trips: int | None = None
    unchecked: int | None = None


class NoteItem(Out):
    id: str
    section_id: str | None
    text: str
    key: str | None = None
    qty: Untyped | None
    extra: str | None
    checked: bool
    checked_at: IsoTime | None
    checked_by: Untyped | None = None
    order: str
    rank: float | None
    added_by: Untyped | None = None
    added_at: IsoTime | None
    updated_by: Untyped | None = None
    updated_at: IsoTime | None = None
    version: int | None = None
    task_uid: str | None = None


class NoteTop(Out):
    order_mode: str | None
    collapsed: bool
    trips: int
    unchecked: int


class Note(Out):
    """A note as a card (GET /notes) or in full with sections and items (GET /notes/{id})."""

    id: str
    kind: Literal["text", "checklist"]
    title: str
    body: str
    color: str | None
    shopping: bool
    checked_at_bottom: bool
    suggest_usual: bool
    local_only: bool
    top_order_mode: str | None
    owner: Untyped
    created_by: Untyped | None
    created_at: IsoTime | None
    updated_at: IsoTime | None
    updated_by: Untyped | None
    deleted_at: IsoTime | None
    purge_at: IsoTime | None
    version: int
    level: str = Field(description="The viewer's access: owner, edit or view.")
    state: NoteState
    counts: NoteCounts
    links: list[Untyped]
    sharing: Untyped | None
    changed: bool
    can_delete: bool
    can_lower_local_only: bool
    repeat: Untyped | None
    reminders: list[Untyped]
    next_reminder: IsoTime | None
    preview: NotePreview = absent(description="Cards of checklists only.")
    sections: list[NoteSection] = absent(description="GET /notes/{id} only.")
    top: NoteTop = absent(description="GET /notes/{id} only: the items with no section.")
    items: list[NoteItem] = absent(description="GET /notes/{id} only.")


class NoteLabel(Out):
    name: str
    color: str | None
    order: str
    count: int


class NoteList(Out):
    """GET /notes: the grid's notes and tables, the viewer's labels and groups."""

    notes: list[Note]
    tables: list[Untyped]
    labels: list[NoteLabel]
    groups: list[str]
