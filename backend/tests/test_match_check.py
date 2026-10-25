# ACT-SURFACES: assistant
"""The match check (20 Oct 2026): before a filter or a message-triggered standing order from the assistant
waits for approval, the tool counts the user's emails of the last 6 months its search matches and names
the newest. The card shows it; a search that matches nothing tells the model to check it with the user.

Covers agent.match_check."""

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

from helpers.core_flow import _ingest

from mailr.db import sessionmaker
from mailr.models import PendingAction, User


def _ago(days: int) -> str:
    return format_datetime(datetime.now(UTC) - timedelta(days=days))


async def _ctx(db, user_id):
    from mailr.agent.tools import ToolCtx
    from mailr.assistant.orders import principal_for

    return ToolCtx(db=db, p=principal_for(await db.get(User, user_id)), assistant=True, chat_session_id="mc1")


async def _mail(user, raw):
    await _ingest(user, raw("Elevator out of order", frm="Building <board@condo.example>", date=_ago(9)))
    await _ingest(user, raw("Re: Elevator", frm="Building <board@condo.example>", date=_ago(2)))
    # Older than six months: not counted.
    await _ingest(user, raw("Annual meeting", frm="Building <board@condo.example>", date=_ago(400)))


async def test_a_filter_names_how_much_mail_it_matches(client, user, raw):
    from mailr.agent.tools import run_tool

    await _mail(user, raw)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        res = await run_tool(ctx, "manage_filter", {"action": "create", "query": "from:board@condo.example",
                                                    "archive": True})
        assert res.data["status"] == "pending_approval"
        assert res.data["matches_existing"]["emails"] == 2 and res.data["matches_existing"]["newest_date"]
        assert "check_search" not in res.data
        assert "Elevator" not in str(res.data)  # the subject is email text: on the card only
        pa = await db.get(PendingAction, res.data["approval_id"])
    assert pa.preview["match"]["count"] == 2 and pa.preview["match"]["newest_subject"] == "Re: Elevator"
    card = (await client.get(f"/api/v1/approvals/{pa.id}")).json()
    row = next(f for f in card["display"]["fields"] if f["label"] == "Matches now")
    assert row["value"]["text"].startswith("2 emails in the last 6 months, the newest “Re: Elevator” on ")
    assert row["value"]["msg"]["key"] == "approvals.match.some"


async def test_a_search_that_matches_nothing_is_checked_with_the_user(client, user, raw):
    from mailr.agent.tools import run_tool

    await _mail(user, raw)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        res = await run_tool(ctx, "manage_filter", {"action": "create", "query": "from:alice@condo-list.example",
                                                    "archive": True})
        assert res.data["matches_existing"]["emails"] == 0
        assert "list+name@domain" in res.data["check_search"]
        pa = await db.get(PendingAction, res.data["approval_id"])
    card = (await client.get(f"/api/v1/approvals/{pa.id}")).json()
    row = next(f for f in card["display"]["fields"] if f["label"] == "Matches now")
    assert row["value"]["text"] == "No email in the last 6 months. Check the search before you approve."


async def test_a_message_order_is_checked_and_a_routine_is_not(client, user, raw):
    from mailr.agent.tools import run_tool

    await _mail(user, raw)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        res = await run_tool(ctx, "manage_order", {"action": "create", "name": "Building", "trigger": "message",
                                                   "query": "subject:elevator", "question": "Is this urgent?",
                                                   "star": True})
        assert res.data["status"] == "pending_approval" and res.data["matches_existing"]["emails"] == 2
        pa = await db.get(PendingAction, res.data["approval_id"])
        assert pa.preview["match"]["count"] == 2
        none = await run_tool(ctx, "manage_order", {"action": "create", "name": "Nothing", "trigger": "message",
                                                    "query": "from:nobody@nowhere.example", "star": True})
        assert none.data["matches_existing"]["emails"] == 0 and "check_search" in none.data
        routine = await run_tool(ctx, "manage_order", {"action": "create", "name": "Digest", "trigger": "schedule",
                                                       "schedule": "0 7 * * 1", "query": "subject:elevator",
                                                       "errand": "summarise the week", "deliver": ["digest"]})
        assert "matches_existing" not in routine.data
    card = (await client.get(f"/api/v1/approvals/{pa.id}")).json()
    assert any(f["label"] == "Matches now" and f["value"]["text"].startswith("2 emails")
               for f in card["display"]["fields"])
