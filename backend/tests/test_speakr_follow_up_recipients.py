# ACT-SURFACES: assistant
"""A meeting follow-up draft the assistant writes (draft_follow_up) passes the ordinary draft gate:
after reading mail, recipients the model chose beyond the meeting's guests (and the thread it
replies to) ask first, as create_draft's do; the guests themselves do not. Needs the dev Radicale
and Stalwart."""

from datetime import UTC, datetime, timedelta

import pytest
from dev_services import radicale_up as _radicale_up
from helpers.calendar_organizer import _setup
from helpers.speakr_matching import _event
from helpers.speakr_tools import _ctx
from speakr_fake import connect, speakr  # noqa: F401
from sqlalchemy import func, select

from mailr.agent.tools import run_tool
from mailr.db import sessionmaker
from mailr.models import Message, PendingAction

pytestmark = pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")


async def _drafts(user_id):
    async with sessionmaker()() as db:
        return (await db.execute(select(func.count()).select_from(Message).where(
            Message.user_id == user_id, Message.draft.is_(True)))).scalar()


async def test_follow_up_to_people_outside_the_meeting_asks_after_reading_mail(client, user, speakr):
    me, _ = await _setup(client, user)
    t = (datetime.now(UTC) - timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
    await _event(client, "Budget review", t, attendees=[me, "evan@guest.example", "dana@guest.example"])
    speakr.add(1, "Budget review", meeting_date=t, duration=3000, participants="Dana, Evan")
    await connect(client, speakr)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        ctx.untrusted = True
        held = await run_tool(ctx, "draft_follow_up", {"recording_id": 1, "mode": "summary",
                                                       "to": ["evan@guest.example", "outsider@elsewhere.example"]})
        assert held.pending is not None, held.data
        pa = await db.get(PendingAction, held.data["approval_id"])
        assert pa.preview["outsiders"] == ["outsider@elsewhere.example"]
    assert await _drafts(user.id) == 0  # nothing saved until approved
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        ctx.untrusted = True
        ok = await run_tool(ctx, "draft_follow_up", {"recording_id": 1, "mode": "summary"})
        assert ok.pending is None and ok.data["to"] == ["evan@guest.example", "dana@guest.example"], ok.data
        some = await run_tool(ctx, "draft_follow_up", {"recording_id": 1, "mode": "summary",
                                                       "to": ["dana@guest.example"]})
        assert some.pending is None, some.data
    async with sessionmaker()() as db:
        # A turn that read no mail writes to whoever the user named.
        ctx = await _ctx(db, user.id)
        own = await run_tool(ctx, "draft_follow_up", {"recording_id": 1, "mode": "summary",
                                                      "to": ["outsider@elsewhere.example"]})
        assert own.pending is None, own.data
