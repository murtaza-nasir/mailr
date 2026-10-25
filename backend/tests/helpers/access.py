"""Shared helpers, from test_access.py."""

import uuid
from contextlib import asynccontextmanager

from httpx import ASGITransport, AsyncClient

from mailr.core import access
from mailr.db import sessionmaker
from mailr.models import AccessPolicy, User

PW = "test-password-123"


def _tag() -> str:
    return uuid.uuid4().hex[:8]


async def _policy(group: str, *, chat=(), classify=(), default_chat=None, default_classify=None, any_chat=False,
                  any_classify=False, **features) -> AccessPolicy:
    def ent(x):
        ep, model = x if isinstance(x, tuple) else (x, None)
        return {"endpoint_id": ep.id, "model": model}

    models = {"chat": {"any": any_chat, "allowed": [ent(x) for x in chat], "default": ent(default_chat) if default_chat else None},
              "classify": {"any": any_classify, "allowed": [ent(x) for x in classify],
                           "default": ent(default_classify) if default_classify else None}}
    feats = {k: bool(features.get(k, False)) for k in access.FEATURES}
    async with sessionmaker()() as db:
        p = AccessPolicy(group_name=group, models=models, features=feats, needs={})
        db.add(p)
        await db.commit()
        return p


async def _user(groups=(), role="user", idp=None) -> User:
    from mailr.core.users import create_user

    async with sessionmaker()() as db:
        u = await create_user(db, f"acc-{_tag()}@example.com", "Access User", password=PW, role=role)
        u.assigned_groups = list(groups)
        if idp is not None:
            u.oidc_sub = f"sub-{_tag()}"
            await access.record_idp_groups(db, u, idp)
        await db.commit()
        return await db.get(User, u.id)


@asynccontextmanager
async def _client(app, u: User):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/v1/auth/login", json={"email": u.email, "password": PW})
        assert r.status_code == 200, r.text
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
        yield c
