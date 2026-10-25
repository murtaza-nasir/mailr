# ACT-SURFACES: api
"""Sign-in, codes and rules: regression tests. Each one is an attack that must fail.

- Strangers who fill the per-address sign-in bucket do not lock the owner out of a client the
  account signed in from before (the sign-in cookie, or an IP with a live session). An unknown
  client still meets the per-address bucket, so slow guessing from many IPs stays limited.
- Code mails are capped per user per day across addresses; removing and adding again does
  not reset the cap; the mail names the account's address, never the free-text name.
- Guesses sent at once never pass the attempt limit.
- `*` and `agent.*` allow rules do not reach "Follow my forwarded notes"; a deny still does.
- handled_elsewhere compares whole addresses.
"""

from __future__ import annotations

import asyncio
import uuid

from helpers.agent import _setup_identity
from helpers.signin_codes_rules_hardening import (
    _clear_rate_events,
    _fill_account_bucket,
    _from,
)
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from mailr.core import trusted
from mailr.db import sessionmaker
from mailr.models import Outbox, TrustedAddress

# ---------------------------------------------------------------- Lockouts by strangers


async def test_strangers_do_not_lock_the_owner_out_of_a_known_browser(app, user):
    await _clear_rate_events()
    try:
        # The owner signed in on this browser before: it holds the sign-in cookie.
        async with _from(app, "198.51.100.10") as owner:
            r = await owner.post("/api/v1/auth/login", json={"email": user.email, "password": "test-password-123"})
            assert r.status_code == 200, r.text
            cookie = owner.cookies.get("mailr_signin")
            assert cookie
        await _fill_account_bucket(app, user.email)
        # A new IP, the same browser (its cookie): the owner gets in.
        async with _from(app, "198.51.100.77", cookies={"mailr_signin": cookie}) as owner:
            r = await owner.post("/api/v1/auth/login", json={"email": user.email, "password": "test-password-123"})
            assert r.status_code == 200, r.text
        # An IP with a live session of the account is known too.
        async with _from(app, "198.51.100.10") as owner:
            r = await owner.post("/api/v1/auth/login", json={"email": user.email, "password": "test-password-123"})
            assert r.status_code == 200, r.text
        # An unknown client still meets the per-address bucket (no oracle for the right password).
        async with _from(app, "198.51.100.200") as stranger:
            r = await stranger.post("/api/v1/auth/login", json={"email": user.email, "password": "test-password-123"})
            assert r.status_code == 429, r.text
    finally:
        await _clear_rate_events()


async def test_a_cookie_of_another_account_does_not_count(app, user):
    """The sign-in cookie names its account: it makes no client known for anyone else."""
    from mailr.core.users import create_user

    await _clear_rate_events()
    try:
        async with sessionmaker()() as db:
            other = await create_user(db, f"o-{uuid.uuid4().hex[:6]}@example.com", "O", password="other-password-123")
        async with _from(app, "198.51.100.11") as c:
            r = await c.post("/api/v1/auth/login", json={"email": other.email, "password": "other-password-123"})
            assert r.status_code == 200
            cookie = c.cookies.get("mailr_signin")
        await _fill_account_bucket(app, user.email)
        async with _from(app, "198.51.100.78", cookies={"mailr_signin": cookie}) as c:
            r = await c.post("/api/v1/auth/login", json={"email": user.email, "password": "test-password-123"})
            assert r.status_code == 429
        async with _from(app, "198.51.100.79", cookies={"mailr_signin": "forged.value.x"}) as c:
            r = await c.post("/api/v1/auth/login", json={"email": user.email, "password": "test-password-123"})
            assert r.status_code == 429
    finally:
        await _clear_rate_events()


async def test_a_known_client_has_its_own_limit(app, user):
    """Wrong passwords from a known browser stop at its own bucket."""
    from mailr.core import throttle

    await _clear_rate_events()
    try:
        async with _from(app, "198.51.100.12") as c:
            assert (await c.post("/api/v1/auth/login", json={"email": user.email,
                                                             "password": "test-password-123"})).status_code == 200
            cookie = c.cookies.get("mailr_signin")
        codes = []
        for i in range(throttle.LOGIN_PER_KNOWN_CLIENT.count + 1):
            async with _from(app, f"192.0.2.{i + 1}", cookies={"mailr_signin": cookie}) as c:
                codes.append((await c.post("/api/v1/auth/login", json={"email": user.email,
                                                                       "password": "nope-0000"})).status_code)
        assert codes[:-1] == [401] * throttle.LOGIN_PER_KNOWN_CLIENT.count and codes[-1] == 429
    finally:
        await _clear_rate_events()


# ---------------------------------------------------------------- Sign-in code mails


async def test_code_mails_are_capped_per_user_and_carry_no_free_text_name(client, user):
    await _setup_identity(user)
    r = await client.patch("/api/v1/me", json={"name": "Visit evil.example to claim your prize"})
    assert r.status_code == 200, r.text
    sent = 0
    statuses = []
    for i in range(trusted.CODES_PER_DAY + 5):
        v = f"victim{i}-{uuid.uuid4().hex[:4]}@strangers.example"
        r = await client.post("/api/v1/assistant/trusted-addresses", json={"email": v})
        statuses.append(r.status_code)
        if r.status_code == 200:
            sent += 1
            assert (await client.delete(f"/api/v1/assistant/trusted-addresses/{r.json()['id']}")).status_code == 200
    assert sent == trusted.CODES_PER_DAY
    assert statuses[-1] == 429
    async with sessionmaker()() as db:
        rows = (await db.execute(select(Outbox).where(
            Outbox.user_id == user.id,
            Outbox.payload["subject"].astext.like(f"{trusted.SUBJECT_TAG}%")))).scalars().all()
    assert len(rows) == trusted.CODES_PER_DAY
    assert not any("evil.example" in (ob.payload.get("text") or "") for ob in rows)
    assert all(user.email in (ob.payload.get("text") or "") for ob in rows)


# ---------------------------------------------------------------- Concurrent wrong codes


async def test_concurrent_wrong_codes_stay_within_the_attempt_limit(app, client, user):
    await _setup_identity(user)
    addr = f"me-{uuid.uuid4().hex[:6]}@work.example"
    tid = (await client.post("/api/v1/assistant/trusted-addresses", json={"email": addr})).json()["id"]
    n = 10
    clients = [AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=client.cookies,
                           headers={"X-CSRF-Token": client.headers["X-CSRF-Token"]}) for _ in range(n)]
    try:
        guesses = [f"{i:06d}" for i in range(900000, 900000 + n)]
        rs = await asyncio.gather(*(c.post(f"/api/v1/assistant/trusted-addresses/{tid}/confirm", json={"code": g})
                                    for c, g in zip(clients, guesses, strict=True)))
    finally:
        for c in clients:
            await c.aclose()
    evaluated = [r for r in rs if r.status_code in (200, 400)]  # 400 = wrong code: the guess was checked
    async with sessionmaker()() as db:
        t = await db.get(TrustedAddress, tid)
    assert len(evaluated) <= trusted.MAX_ATTEMPTS, [r.status_code for r in rs]
    assert t.attempts == len(evaluated)


# ---------------------------------------------------------------- Broad allow rules and forwarded notes


async def test_broad_allow_rules_do_not_reach_forwarded_notes(client, user):
    from mailr.assistant import orders
    from mailr.models import User

    for action in ("*", "agent.*"):
        r = await client.post("/api/v1/permissions", json={"subject": "assistant", "action": action, "effect": "allow"})
        assert r.status_code == 200, r.text
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        effect, _src = await orders.permission(db, u)
    assert effect == "ask"
    # A broad deny still stops it.
    r = await client.post("/api/v1/permissions", json={"subject": "assistant", "action": "agent.*", "effect": "deny"})
    assert r.status_code == 200, r.text
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        effect, _src = await orders.permission(db, u)
    assert effect == "deny"


# ---------------------------------------------------------------- Handled elsewhere


def test_handled_elsewhere_names_whole_addresses():
    assert not trusted.names_address("to:memory@example.com", ["y@example.com"])
    assert not trusted.names_address("to:memory@example.com", ["ory@example.com"])
    assert not trusted.names_address("to:memory@example.com.au", ["memory@example.com"])
    assert trusted.names_address("to:memory@example.com is:unread", ["memory@example.com"])
    assert trusted.names_address('from:"Memory@Example.com"', ["memory@example.com"])
    assert trusted.names_address("memory@example.com", ["memory@example.com"])
