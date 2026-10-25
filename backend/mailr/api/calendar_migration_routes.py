"""Copy a connected CalDAV account into the mailr calendar (calendar/migrate.py): Settings →
Calendar & Tasks → the account → "Copy into my mailr calendar…".

The user starts a dry run (the preview), reads the counts, then starts the copy; both run as the
job `calendar.migrate` and report progress. Afterwards the user may make the copies the defaults
and hide the account's copied calendars (the cut-over). Nothing changes on the account's server.
Session only: an API token cannot start a copy.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.calendar import builtin, migrate
from mailr.core.audit import audit
from mailr.core.auth import Principal, current
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.models import Connector, Job

router = APIRouter(prefix="/connectors", tags=["calendar"])


class MigrationIn(BaseModel):
    dry_run: bool = True
    overwrite: bool = False
    collections: list[str] = Field(default_factory=list, max_length=200)


class CutoverIn(BaseModel):
    make_default: bool = False
    hide_copied: bool = False


async def _source(db: AsyncSession, p: Principal, cid: int) -> Connector:
    if p.is_token:
        raise ApiError(403, "accounts.user_only")
    c = await db.get(Connector, cid)
    if c is None or c.user_id != p.user_id:
        raise ApiError(404, "accounts.account_not_found")
    if c.kind != "caldav" or c.builtin:
        raise ApiError(400, "calendar.copy_caldav_only")
    target = await builtin.find(db, p.user_id)
    if target is None or not await builtin.active(db, target):
        raise ApiError(409, "calendar.no_builtin_calendar")
    return c


def _jobs(cid: int):
    return select(Job).where(Job.kind == migrate.JOB, Job.payload["connector_id"].astext == str(cid))


# ACT calendar.migrate @api
@router.post("/{cid}/migration")
async def start_migration(cid: int, body: MigrationIn, p: Principal = Depends(current),
                          db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Start a preview (dry_run) or the copy. A run already going for this account is returned."""
    await _source(db, p, cid)
    jid = await migrate.start(db, p.user_id, cid, dry_run=body.dry_run, overwrite=body.overwrite,
                              collections=body.collections)
    await audit(db, p, "calendar.migrate", str(cid), {"dry_run": body.dry_run, "overwrite": body.overwrite},
                {"job": jid}, always=not body.dry_run)
    await db.commit()
    return migrate.job_out(await db.get(Job, jid))


@router.get("/{cid}/migration")
async def latest_migration(cid: int, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)
                           ) -> dict[str, Any]:
    """The newest run for this account (preview or copy), or {"status": "none"}."""
    await _source(db, p, cid)
    j = (await db.execute(_jobs(cid).where(Job.user_id == p.user_id).order_by(Job.id.desc()).limit(1))).scalar()
    copy = (await db.execute(_jobs(cid).where(Job.user_id == p.user_id, Job.status == "done",
                                              Job.payload["dry_run"].astext == "false")
                             .order_by(Job.id.desc()).limit(1))).scalar()
    out = migrate.job_out(j) if j is not None else {"status": "none"}
    out["last_copy"] = migrate.job_out(copy) if copy is not None else None
    return out


@router.get("/{cid}/migration/{job_id}")
async def migration_status(cid: int, job_id: int, p: Principal = Depends(current),
                           db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    await _source(db, p, cid)
    j = await db.get(Job, job_id)
    if j is None or j.kind != migrate.JOB or j.user_id != p.user_id or (j.payload or {}).get("connector_id") != cid:
        raise ApiError(404, "common.not_found")
    return migrate.job_out(j)


# ACT calendar.migrate_cutover @api
@router.post("/{cid}/migration/cutover")
async def migration_cutover(cid: int, body: CutoverIn, p: Principal = Depends(current),
                            db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """After a copy: the copies become the defaults, and/or this account's copied calendars are
    hidden in Calendar. Both only on request; the account's server is not touched."""
    await _source(db, p, cid)
    if not body.make_default and not body.hide_copied:
        raise ApiError(400, "calendar.choose_a_change")
    try:
        out = await migrate.cutover(p.user_id, cid, make_default=body.make_default, hide_source=body.hide_copied)
    except migrate.MigrationError as e:
        raise HTTPException(409, str(e)) from None
    await audit(db, p, "calendar.migrate_cutover", str(cid), body.model_dump(), {"hidden": out["hidden"]},
                always=True)
    await db.commit()
    return out
