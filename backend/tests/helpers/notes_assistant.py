"""Shared helpers, from test_notes_assistant.py."""

import uuid

from mailr.agent import screen
from mailr.agent.tools import ToolCtx
from mailr.core.auth import ALL_SCOPES, Principal


def _ctx(db, u, *, untrusted=False, ui=None, sensitive=False, local_model=True, errand=False, session=None):
    p = Principal(user=u, scopes=set(ALL_SCOPES) - {"admin"})
    return ToolCtx(db=db, p=p, assistant=True, chat_session_id=session or f"s-{uuid.uuid4().hex[:6]}",
                   ui=screen.sanitize(ui or {}), session_sensitive=sensitive, untrusted=untrusted,
                   local_model=local_model, chat_context={"errand": 1} if errand else {})
