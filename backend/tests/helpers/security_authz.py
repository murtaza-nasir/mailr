"""Shared helpers, from test_security_authz.py."""

import uuid

from httpx import AsyncClient

from mailr.core import permissions
from mailr.db import session_scope, sessionmaker
from mailr.models import Connector, PendingAction, User


async def _token(client, scopes, local_only=False, connector_ids=None, name="agent", **extra):
    r = await client.post("/api/v1/tokens", json={"name": name, "scopes": scopes, "local_only": local_only,
                                                  "connector_ids": connector_ids or [], **extra})
    assert r.status_code == 200, r.text
    a = AsyncClient(transport=client._transport, base_url="http://test")
    a.headers["Authorization"] = f"Bearer {r.json()['token']}"
    a.token_id = r.json()["api_token"]["id"]
    return a


async def _conn(user_id, data_class="normal", kind="imap"):
    async with session_scope() as db:
        c = Connector(user_id=user_id, kind=kind, name=f"acct-{uuid.uuid4().hex[:4]}", mode="mirror",
                      data_class=data_class, config={}, state={}, enabled=False)
        db.add(c)
        await db.flush()
        return c.id


async def _decide(user_id, pid, decision="approve", scope="once"):
    async with sessionmaker()() as db:
        u = await db.get(User, user_id)
        await permissions.decide(db, u, pid, decision, scope)
        await db.commit()
    await permissions.execute_job({"pending_id": pid}, None)
    async with sessionmaker()() as db:
        return await db.get(PendingAction, pid)


def _radicale_up():
    from dev_services import radicale_up as up

    return up()


async def _caldav(user_id, data_class="normal"):
    import caldav
    from dev_services import RADICALE

    from mailr.connectors.loader import set_secrets

    username = f"u{uuid.uuid4().hex[:8]}"
    c = caldav.DAVClient(url=RADICALE, username=username, password="x").principal().make_calendar(
        name="Cal", supported_calendar_component_set=["VEVENT", "VTODO"])
    async with sessionmaker()() as db:
        row = Connector(user_id=user_id, kind="caldav", name=f"dav-{data_class}", mode="mirror", enabled=True,
                        data_class=data_class, config={"url": RADICALE, "username": username,
                                                       "default_calendar": str(c.url), "default_tasklist": str(c.url)})
        db.add(row)
        await db.flush()
        await set_secrets(db, row, {"password": "x"})
        await db.commit()
        cid = row.id
    return cid, c
