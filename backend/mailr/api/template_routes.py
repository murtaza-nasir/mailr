"""Templates: fixed text inserted in compose.

Reading and expanding need `read`; writing needs `draft` (templates are compose content). Every
route answers 404 for another user's template.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import templates as tpl
from mailr.core.audit import audit
from mailr.core.auth import Principal, scope
from mailr.core.compose import clean_html
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.models import Identity, Person, Template

router = APIRouter(tags=["templates"])

MAX_BODY = 100_000


class TemplateIn(BaseModel):
    name: str | None = Field(None, max_length=200)
    shortcut: str | None = Field(None, max_length=49)
    subject: str | None = Field(None, max_length=1000)
    body_html: str | None = Field(None, max_length=MAX_BODY)


class Recipient(BaseModel):
    email: str = ""
    name: str = ""


class ExpandIn(BaseModel):
    to: list[Recipient] = []
    cc: list[Recipient] = []
    bcc: list[Recipient] = []
    identity_id: int | None = None


def template_out(t: Template) -> dict:
    return {"id": t.id, "name": t.name, "shortcut": t.shortcut, "subject": t.subject, "body_html": t.body_html,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None}


async def _template(db: AsyncSession, p: Principal, tid: int) -> Template:
    t = await db.get(Template, tid)
    if t is None or t.user_id != p.user_id:
        raise ApiError(404, "templates.template_not_found")
    return t


async def _apply(db: AsyncSession, p: Principal, t: Template, body: TemplateIn) -> None:
    if body.name is not None:
        if not body.name.strip():
            raise ApiError(400, "templates.name_required")
        t.name = body.name.strip()
    if body.shortcut is not None:
        try:
            shortcut = tpl.clean_shortcut(body.shortcut)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        # Checked before the change is set: the query would otherwise flush it first.
        if shortcut:
            q = select(Template.id).where(Template.user_id == p.user_id, Template.shortcut == shortcut)
            if t.id is not None:
                q = q.where(Template.id != t.id)
            if (await db.execute(q)).first():
                raise ApiError(409, "templates.shortcut_taken", shortcut=shortcut)
        t.shortcut = shortcut
    if body.subject is not None:
        t.subject = body.subject.replace("\r", " ").replace("\n", " ").strip()
    if body.body_html is not None:
        t.body_html = clean_html(body.body_html)


@router.get("/templates")
async def list_templates(p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Template).where(Template.user_id == p.user_id)
                             .order_by(Template.name, Template.id))).scalars()
    return {"items": [template_out(t) for t in rows], "variables": list(tpl.VARIABLES)}


@router.get("/templates/{tid}")
async def get_template(tid: int, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    return template_out(await _template(db, p, tid))


# ACT template.create @api
@router.post("/templates", status_code=201)
async def create_template(body: TemplateIn, p: Principal = Depends(scope("draft")), db: AsyncSession = Depends(get_db)):
    if not (body.name or "").strip():
        raise ApiError(400, "templates.name_required")
    t = Template(user_id=p.user_id, name="", shortcut="", subject="", body_html="")
    await _apply(db, p, t, body)
    db.add(t)
    await audit(db, p, "template.create", t.name, {"shortcut": t.shortcut})
    await db.commit()
    return template_out(t)


# ACT template.edit @api
@router.patch("/templates/{tid}")
async def update_template(tid: int, body: TemplateIn, p: Principal = Depends(scope("draft")),
                          db: AsyncSession = Depends(get_db)):
    t = await _template(db, p, tid)
    await _apply(db, p, t, body)
    await audit(db, p, "template.edit", t.name, {"fields": sorted(body.model_dump(exclude_none=True))})
    await db.commit()
    return template_out(t)


# ACT template.delete @api
@router.delete("/templates/{tid}")
async def delete_template(tid: int, p: Principal = Depends(scope("draft")), db: AsyncSession = Depends(get_db)):
    t = await _template(db, p, tid)
    await db.delete(t)
    await audit(db, p, "template.delete", t.name)
    await db.commit()
    return {"ok": True}


# ACT template.insert @api
@router.post("/templates/{tid}/expand")
async def expand_template(tid: int, body: ExpandIn, p: Principal = Depends(scope("read")),
                          db: AsyncSession = Depends(get_db)):
    """The template with its variables filled for the draft's recipients and sending address."""
    from mailr.core.visibility import is_restricted

    t = await _template(db, p, tid)
    first = next((r for r in (*body.to, *body.cc, *body.bcc) if r.email.strip()), None)
    rcpt = {"email": first.email.strip(), "name": first.name.strip()} if first else None
    # A recipient typed without a name takes the name mailr knows for the address. A token
    # limited to some accounts does not get names from the others.
    if rcpt and not rcpt["name"] and not is_restricted(p):
        rcpt["name"] = (await db.execute(select(Person.name).where(
            Person.user_id == p.user_id, Person.email == rcpt["email"].lower()))).scalar() or ""
    my_name = ""
    if body.identity_id is not None:
        ident = await db.get(Identity, body.identity_id)
        if ident and ident.user_id == p.user_id:
            my_name = ident.name or ""
    if not my_name:
        ident = (await db.execute(select(Identity).where(Identity.user_id == p.user_id)
                                  .order_by(Identity.is_default.desc(), Identity.id).limit(1))).scalar()
        my_name = (ident.name if ident else "") or p.user.name or ""
    from mailr.calendar.tz import user_zone_name

    vals = tpl.values(rcpt, my_name, user_zone_name(p.user))
    return {"id": t.id, **tpl.expand(t.subject, t.body_html, vals)}
