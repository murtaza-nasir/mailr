"""Shared helpers, from test_agent_calendar_v2.py."""

import uuid

from mailr.agent import screen
from mailr.agent.tools import ToolCtx
from mailr.core.auth import ALL_SCOPES, Principal
from mailr.db import sessionmaker
from mailr.models import (
    Connector,
    Identity,
    PermissionRule,
    User,
)


async def _ctx(db, user_id, *, assistant=True, ui=None, sensitive=False):
    u = await db.get(User, user_id)
    p = Principal(user=u, scopes=set(ALL_SCOPES) - {"admin"})
    return ToolCtx(db=db, p=p, assistant=assistant, chat_session_id=f"s-{uuid.uuid4().hex[:6]}",
                   ui=screen.sanitize(ui or {}), session_sensitive=sensitive)


async def _allow(user_id, *actions):
    async with sessionmaker()() as db:
        for a in actions:
            db.add(PermissionRule(user_id=user_id, subject="assistant", action=a, effect="allow", constraints={}))
        await db.commit()


async def _identity(user) -> str:
    """A sending identity (an outgoing account), as the organizer tests set it up."""
    async with sessionmaker()() as db:
        c = Connector(user_id=user.id, kind="imap", name="out", mode="mirror", enabled=False, status="paused", config={})
        db.add(c)
        await db.flush()
        me = f"me-{c.id}@example.com"
        db.add(Identity(user_id=user.id, connector_id=c.id, email=me, name="Me", is_default=True, receives=[]))
        await db.commit()
    return me
