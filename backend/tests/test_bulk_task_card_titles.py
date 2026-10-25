"""A bulk task card (bulk_tasks) names the tasks by title, not by uid."""
import uuid

from helpers.agent_calendar import _connect_direct

from mailr.agent.tools import ToolCtx, run_tool
from mailr.core.auth import ALL_SCOPES, Principal
from mailr.db import sessionmaker
from mailr.models import PendingAction, User


async def test_bulk_delete_card_shows_task_titles(client, user):
    await _connect_direct(user.id)
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        ctx = ToolCtx(db=db, p=Principal(user=u, scopes=set(ALL_SCOPES) - {"admin"}), assistant=True,
                      chat_session_id=f"s-{uuid.uuid4().hex[:6]}")
        a = (await run_tool(ctx, "create_task", {"summary": "Renew the parking permit"})).data
        b = (await run_tool(ctx, "create_task", {"summary": "Return the library books"})).data
        res = await run_tool(ctx, "bulk_tasks", {"op": "delete", "uids": [a["uid"], b["uid"]]})
        assert res.pending is not None, res.data
        pa = await db.get(PendingAction, res.data["approval_id"])
        lines = (pa.preview or {}).get("lines") or []
        assert "Renew the parking permit" in lines and "Return the library books" in lines
        assert a["uid"] not in lines
