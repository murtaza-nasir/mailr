"""Shared helpers, from test_agent_calendar.py."""

import uuid

import caldav
from dev_services import RADICALE

from mailr.agent.tools import ToolCtx
from mailr.connectors.loader import set_secrets
from mailr.core.auth import ALL_SCOPES, Principal
from mailr.db import sessionmaker
from mailr.models import Connector, User


async def _connect_direct(user_id: int) -> tuple[str, str]:
    """A CalDAV connector with a default calendar, created without the /calendar/connect route."""
    username = f"u{uuid.uuid4().hex[:8]}"
    principal = caldav.DAVClient(url=RADICALE, username=username, password="x").principal()
    c = principal.make_calendar(name="Personal", supported_calendar_component_set=["VEVENT", "VTODO"])
    href = str(c.url)
    async with sessionmaker()() as db:
        row = Connector(user_id=user_id, kind="caldav", name="Radicale", mode="mirror", enabled=True,
                        config={"url": RADICALE, "username": username, "default_calendar": href,
                                "default_tasklist": href})
        db.add(row)
        await db.flush()
        await set_secrets(db, row, {"password": "x"})
        await db.commit()
    return username, href


async def _ctx(db, user_id, assistant=True):
    u = await db.get(User, user_id)
    p = Principal(user=u, scopes=set(ALL_SCOPES) - {"admin"})
    return ToolCtx(db=db, p=p, assistant=assistant, chat_session_id=f"s-{uuid.uuid4().hex[:6]}")
