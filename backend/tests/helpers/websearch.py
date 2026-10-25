"""Shared helpers, from test_websearch.py."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from sqlalchemy import delete

from mailr.config import get_settings
from mailr.core import access, egress
from mailr.db import sessionmaker
from mailr.models import User, WebSearchProvider
from mailr.websearch import service


@pytest.fixture(autouse=True)
async def _clean(monkeypatch):
    """Providers are instance-wide: each test starts and ends with none, and with no egress."""
    async def none(db=None):
        return ""

    monkeypatch.setattr(egress, "configured", none)
    egress.clear_cache()
    access.reset_audit_quiet()
    async with sessionmaker()() as db:
        await db.execute(delete(WebSearchProvider))
        await db.commit()
    yield
    async with sessionmaker()() as db:
        await db.execute(delete(WebSearchProvider))
        await db.commit()
    egress.clear_cache()


def _mock(monkeypatch, handler):
    """Every provider request goes to `handler` (the route is still decided); returns the log."""
    seen: list[httpx.Request] = []

    async def h(req: httpx.Request):
        seen.append(req)
        out = handler(req)
        if asyncio.iscoroutine(out):
            out = await out
        return out

    monkeypatch.setattr(service, "_TRANSPORT", httpx.MockTransport(h))
    return seen


def _require(monkeypatch, on=True):
    monkeypatch.setattr(get_settings(), "require_egress", on)


def _egress(monkeypatch, url="http://proxy.invalid:8888", state=None):
    async def configured(db=None):
        return url

    async def st(use_cache=True):
        return state

    monkeypatch.setattr(egress, "configured", configured)
    monkeypatch.setattr(egress, "state", st)


def _page_client(monkeypatch, handler):
    monkeypatch.setattr(egress, "client", lambda eg, **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw))


async def _ctx(user_id: int, *, assistant=True, session="ws-session", sensitive=False, token=None):
    from mailr.agent.tools import ToolCtx
    from mailr.core.auth import ALL_SCOPES, Principal

    db = sessionmaker()()
    u = await db.get(User, user_id)
    p = Principal(user=u, token=token, scopes=set(ALL_SCOPES) - {"admin"})
    return ToolCtx(db=db, p=p, assistant=assistant, chat_session_id=session, session_sensitive=sensitive)
