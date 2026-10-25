"""Shared helpers, from test_sharing_tokens_hardening.py."""

from __future__ import annotations

import uuid

from httpx import ASGITransport, AsyncClient

from mailr.db import sessionmaker


async def _other(app, name: str = "Bob"):
    """A second signed-in user (in the test group, like every test user)."""
    from conftest import grant_model_access

    from mailr.core.users import create_user

    async with sessionmaker()() as db:
        u = await create_user(db, f"s2-{uuid.uuid4().hex[:8]}@example.com", name, password="other-password-123")
    await grant_model_access(u.id)
    c = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    r = await c.post("/api/v1/auth/login", json={"email": u.email, "password": "other-password-123"})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf"]
    c.user_id = u.id
    c.email = u.email
    return c


async def _tok(client, scopes, **kw):
    r = await client.post("/api/v1/tokens", json={"name": f"t-{uuid.uuid4().hex[:4]}", "scopes": scopes, **kw})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _bearer(client, tok):
    a = AsyncClient(transport=client._transport, base_url="http://test")
    a.headers["Authorization"] = f"Bearer {tok}"
    return a
