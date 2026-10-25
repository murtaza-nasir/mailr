"""Imports (core/imports.py): preview, import, undo, and the recent ones.

- POST /imports/preview {kind, blobs, calendar?}: counts and a few examples; nothing is written.
- POST /imports {kind, blobs, calendar?}: the import; answers the batch with its counts.
- POST /imports/{id}/undo: takes the whole import back.
- GET /imports?kind=: the user's imports, newest first.

`blobs` are uploads (POST /uploads). Only the signed-in user imports: an import writes many items
at once, so API tokens get 403.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.core import imports
from mailr.core.audit import audit
from mailr.core.auth import Principal, current
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.models import ImportBatch

router = APIRouter(tags=["imports"])


class ImportIn(BaseModel):
    kind: str = Field(pattern="^(keep|ics|vcf)$")
    blobs: list[str] = Field(min_length=1, max_length=imports.MAX_FILES)
    calendar: str | None = None


def _user_only(p: Principal) -> None:
    if p.is_token:
        raise ApiError(403, "import.user_only")


def _files(p: Principal, blobs: list[str]) -> tuple[list[imports.SourceFile], str]:
    from mailr.api.mail_write import read_upload

    files = []
    for b in blobs:
        data, _, name = read_upload(p.user_id, b)
        files.append(imports.SourceFile(name, data))
    return files, ", ".join(f.name for f in files)[:300]


# ACT note.import_keep @api, ACT contacts.import_vcf @api, ACT event.import_ics
@router.post("/imports/preview")
async def preview(body: ImportIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    _user_only(p)
    files, source = _files(p, body.blobs)
    pl = await imports.plan(db, p.user, p, body.kind, files, body.calendar)
    return {"kind": body.kind, "source": source, "counts": pl.counts, "sample": pl.sample, "calendar": pl.target}


@router.post("/imports")
async def run_import(body: ImportIn, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    _user_only(p)
    files, source = _files(p, body.blobs)
    pl = await imports.plan(db, p.user, p, body.kind, files, body.calendar)
    batch = await imports.run(db, p.user, p, pl, source)
    await audit(db, p, f"import.{body.kind}", source, None, batch.counts)
    await db.commit()
    return imports.batch_out(batch)


@router.get("/imports")
async def list_imports(kind: str | None = None, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    _user_only(p)
    q = select(ImportBatch).where(ImportBatch.user_id == p.user_id)
    if kind:
        q = q.where(ImportBatch.kind == kind)
    rows = (await db.execute(q.order_by(ImportBatch.id.desc()).limit(20))).scalars().all()
    return {"items": [imports.batch_out(b) for b in rows]}


# ACT import.undo @api
@router.post("/imports/{bid}/undo")
async def undo_import(bid: int, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    _user_only(p)
    b = await db.get(ImportBatch, bid)
    if b is None or b.user_id != p.user_id:
        raise ApiError(404, "import.not_found")
    out = await imports.undo(db, p.user, p, b)
    await audit(db, p, f"import.{b.kind}.undo", str(b.id), None, out)
    await db.commit()
    return {"ok": True, "undone": out, "import": imports.batch_out(b)}
