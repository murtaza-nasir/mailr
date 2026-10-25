"""Shared helpers, from test_agent_approvals.py."""

from mailr.core import permissions
from mailr.db import sessionmaker
from mailr.models import User


async def _decide(user_id, pid, scope="once"):
    async with sessionmaker()() as db:
        u = await db.get(User, user_id)
        await permissions.decide(db, u, pid, "approve", scope)
        await db.commit()
    await permissions.execute_job({"pending_id": pid}, None)
