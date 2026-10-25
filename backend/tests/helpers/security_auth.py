"""Shared helpers, from test_security_auth.py."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def admin_client(app):
    from mailr.core.users import create_user
    from mailr.db import sessionmaker

    async with sessionmaker()() as db:
        u = await create_user(db, f"adm-{uuid.uuid4().hex[:8]}@example.com", "Admin",
                              password="admin-password-123", role="admin")
    c = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    r = await c.post("/api/v1/auth/login", json={"email": u.email, "password": "admin-password-123"})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf"]
    yield c
    await c.aclose()
