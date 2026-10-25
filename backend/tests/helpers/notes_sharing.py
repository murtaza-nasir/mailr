"""Shared helpers, from test_notes_sharing.py."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from mailr.db import sessionmaker
from mailr.models import User

API = "/api/v1"


PW = "share-password-123"


EMAILS: dict[int, str] = {}  # user id -> address: what a people search shows


@pytest.fixture
async def people(app):
    """make(name, groups=()) → a signed-in client with `.uid`; closed at the end."""
    made: list[AsyncClient] = []

    async def make(name: str, groups: tuple[str, ...] = (), admin: bool = False) -> AsyncClient:
        from mailr.core.users import create_user

        async with sessionmaker()() as db:
            u = await create_user(db, f"{name.lower()}-{uuid.uuid4().hex[:8]}@example.com", name, password=PW)
            u = await db.get(User, u.id)
            u.assigned_groups = list(groups)
            if admin:
                u.role = "admin"
            await db.commit()
            uid, email = u.id, u.email
        c = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        r = await c.post(f"{API}/auth/login", json={"email": email, "password": PW})
        assert r.status_code == 200, r.text
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
        c.uid = uid  # type: ignore[attr-defined]
        c.email = email  # type: ignore[attr-defined]
        EMAILS[uid] = email
        made.append(c)
        return c

    yield make
    for c in made:
        await c.aclose()


@pytest.fixture
async def policy():
    """Set the instance sharing policy for one test; put it back after."""
    from mailr.calendar import sharing as cal

    async with sessionmaker()() as db:
        before = await cal.policy(db)

    async def set_(v: str) -> None:
        async with sessionmaker()() as db:
            await cal.set_policy(db, v)
            await db.commit()

    yield set_
    await set_(before)


async def new_note(c, **kw):
    r = await c.post(f"{API}/notes", json={"kind": "checklist", "title": "Groceries", "lines": "milk\neggs", **kw})
    assert r.status_code == 200, r.text
    return r.json()["note"]


async def new_table(c, **kw):
    r = await c.post(f"{API}/tables", json={"name": "Expenses", **kw})
    assert r.status_code == 200, r.text
    return r.json()["table"]


async def share(c, kind, pid, principal, level, status=200):
    """PUT a share; a person is named by id with the address a search showed."""
    body = {"level": level}
    if str(principal).isdigit() and int(principal) in EMAILS:
        body["email"] = EMAILS[int(principal)]
    r = await c.put(f"{API}/{kind}s/{pid}/shares/{principal}", json=body)
    assert r.status_code == status, r.text
    return r.json()
