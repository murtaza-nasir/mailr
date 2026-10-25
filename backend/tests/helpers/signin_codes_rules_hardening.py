"""Shared helpers, from test_signin_codes_rules_hardening.py."""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from mailr.db import sessionmaker


async def _clear_rate_events():
    async with sessionmaker()() as db:
        await db.execute(text("DELETE FROM rate_events"))
        await db.commit()


def _from(app, ip: str, **kw) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app, client=(ip, 40000)), base_url="http://test", **kw)


async def _fill_account_bucket(app, email: str) -> None:
    for i in range(5):  # five addresses x 10 tries (the per-address-and-account limit)
        async with _from(app, f"203.0.113.{i + 1}") as c:
            for _ in range(10):
                r = await c.post("/api/v1/auth/login", json={"email": email, "password": "wrong-guess-0000"})
                assert r.status_code == 401
