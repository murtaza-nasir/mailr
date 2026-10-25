"""Templates of notes, checklists and tables (notes/templates.py). Registered before notes/routes.py, so `/notes/templates` is not read as a note id.
Writes run through the permission gate as the other notes routes do (notes/gate.py)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core.auth import Principal, scope
from mailr.db import get_db
from mailr.notes import gate, templates

router = APIRouter(tags=["notes"])
notes_user = scope("notes")


@router.get("/notes/templates")
async def list_templates(p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    """The built-in starter templates, then the caller's own (a token that may not see local-only
    notes does not see local-only templates either)."""
    from mailr.notes import access

    return {"templates": await templates.list_templates(db, p.user, hide_local=access.hidden_from(p))}


class TemplateIn(BaseModel):
    kind: str = Field(pattern="^(text|checklist|table)$")
    name: str = Field("", max_length=200)
    content: dict[str, Any]


# (the API's own template, and the undo of note.delete_template)
@router.post("/notes/templates")
async def create_template(body: TemplateIn, p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    return await gate.route(db, p, "template.create", None, body.model_dump())


# ACT note.delete_template @api
@router.delete("/notes/templates/{template_id}")
async def delete_template(template_id: str, p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    from mailr.notes import access

    if template_id not in templates.BUILTIN:
        # A template this caller may not see is a 404 before any approval card is made.
        await templates.get_template(db, p.user, template_id, hide_local=access.hidden_from(p))
    return await gate.route(db, p, "template.delete", None, {"template": template_id})


class FromTemplateIn(BaseModel):
    template: str = Field(min_length=1, max_length=40)
    title: str | None = Field(None, max_length=500)
    labels: list[str] | None = Field(None, max_length=200)
    color: str | None = None
    group: str | None = Field(None, max_length=200)


# ACT note.from_template @api
@router.post("/notes/from-template")
async def from_template(body: FromTemplateIn, p: Principal = Depends(notes_user), db: AsyncSession = Depends(get_db)):
    """A new note, list or table from a template: `note` or `table` in the answer, with an undo."""
    return await gate.route(db, p, "note.from_template", None, body.model_dump(exclude_none=True))


class SaveIn(BaseModel):
    name: str | None = Field(None, max_length=200)


# ACT note.save_template @api
@router.post("/notes/{note_id}/save-template")
async def save_template(note_id: str, body: SaveIn | None = None, p: Principal = Depends(notes_user),
                        db: AsyncSession = Depends(get_db)):
    """Save the note as a template: a text note's title and text, or a checklist's sections and
    items (unchecked)."""
    return await gate.route(db, p, "note.save_template", note_id, {"name": body.name if body else None})
