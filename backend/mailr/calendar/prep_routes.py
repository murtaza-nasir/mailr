"""Meeting prep pack routes (calendar/meeting_prep.py; ACT event.prep).

    GET  /calendar/events/{uid}/prep?occurrence=          the pack (no model)
    POST /calendar/events/{uid}/prep/summary {occurrence}  one paragraph from the user's chat model

Both read as the caller may see (tokens: their accounts and data class). The pack carries text
from other people's mail, so a token's response is marked untrusted (X-Mailr-Content).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.calendar import meeting_prep
from mailr.core.auth import Principal, scope
from mailr.db import get_db
from mailr.i18n import ApiError

router = APIRouter(tags=["calendar"])


async def _pack(db: AsyncSession, p: Principal, uid: str, occurrence: str | None) -> dict:
    try:
        return await meeting_prep.build(db, p, uid, occurrence or None)
    except LookupError:
        raise ApiError(404, "calendar.event_not_found") from None


# ACT event.prep @api
@router.get("/calendar/events/{uid}/prep")
async def event_prep(uid: str, response: Response, occurrence: str = "", p: Principal = Depends(scope("read")),
                     db: AsyncSession = Depends(get_db)):
    """The meeting prep pack of an event occurrence: the last recording with its open action items,
    recent threads with the guests, related tasks, memory notes, open questions and waiting-on
    items, and recent attachments. Nothing here uses a model."""
    pack = await _pack(db, p, uid, occurrence)
    if p.is_token:
        response.headers["X-Mailr-Content"] = "untrusted"
    return pack


class SummaryIn(BaseModel):
    occurrence: str = Field("", max_length=40)


# ACT event.prep @api (the summary paragraph)
@router.post("/calendar/events/{uid}/prep/summary")
async def event_prep_summary(uid: str, response: Response, body: SummaryIn | None = None,
                             p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    """One short paragraph about the pack, written by the user's chat model under their access
    policy. A pack with local-only content is written only by a local model, else refused."""
    pack = await _pack(db, p, uid, (body.occurrence if body else "") or None)
    res = await meeting_prep.summarize(db, p, pack)
    if p.is_token:
        response.headers["X-Mailr-Content"] = "untrusted"
    return res
