"""Shared helpers, from test_speakr_tools.py."""

import uuid

from mailr.agent.tools import ToolCtx
from mailr.core.auth import ALL_SCOPES, Principal
from mailr.models import User


async def _ctx(db, user_id, *, scopes=None, token=None, local_model=True):
    u = await db.get(User, user_id)
    p = Principal(user=u, token=token, scopes=set(scopes or (set(ALL_SCOPES) - {"admin"})))
    return ToolCtx(db=db, p=p, assistant=token is None, chat_session_id=f"s-{uuid.uuid4().hex[:6]}",
                   local_model=local_model)
