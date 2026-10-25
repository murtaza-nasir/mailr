# ACT-SURFACES: assistant
"""recording.ask: "Ask assistant" from the recording sheet sends `focused_recording` at the top of
the chat's `context`. The session keeps it, the prompt names the recording (by id only when its
Speakr account is local-only), and a recording the user cannot see adds nothing."""

from speakr_fake import connect, speakr  # noqa: F401

from mailr.agent import loop, screen
from mailr.core.auth import ALL_SCOPES, Principal
from mailr.db import sessionmaker
from mailr.models import Connector, User


async def test_recording_ask_focus_is_kept_and_described(client, user, speakr):
    speakr.add(12, "Budget review")
    conn = await connect(client, speakr)
    keep, ui = loop.split_context({"focused_recording": {"connector_id": conn["id"], "recording_id": 12}})
    assert keep == {"focused_recording": {"connector_id": conn["id"], "recording_id": 12}} and ui == {}
    for bad in ({"connector_id": "x", "recording_id": 12}, {"recording_id": 12}, {"connector_id": 1, "recording_id": -4},
                {"connector_id": 1, "recording_id": True}, "12"):
        assert loop.split_context({"focused_recording": bad})[0] == {}
    ui = screen.with_focus({}, None, None, keep["focused_recording"])
    assert ui == {"focused_recording": {"connector_id": conn["id"], "recording_id": 12}}
    # Inside `ui` too, and the turn's own focus wins over the session's.
    short = screen.sanitize({"focused_recording": {"connector_id": conn["id"], "recording_id": 99}})
    assert screen.with_focus(short, None, None, keep["focused_recording"])["focused_recording"]["recording_id"] == 99

    async with sessionmaker()() as db:
        p = Principal(user=await db.get(User, user.id), scopes=set(ALL_SCOPES) - {"admin"})
        lines = await screen.describe(db, p, ui)
        # Local-only (the default): the id, never the title.
        assert "The user asked about one recording" in lines and "recording_id 12" in lines
        assert "Budget review" not in lines and "local-only" in lines and "[[r:12]]" in lines
        c = await db.get(Connector, conn["id"])
        c.data_class = "normal"
        await db.commit()
        lines = await screen.describe(db, p, ui)
        assert "'Budget review'" in lines and "local-only" not in lines
        # A recording that is not in the user's mirror adds nothing.
        assert await screen.describe(db, p, short) == ""
