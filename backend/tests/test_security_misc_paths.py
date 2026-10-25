"""Regression tests outside mail parsing and the calendar (see also
test_instance_secret_context.py and test_security_server.py)."""

from __future__ import annotations

import io
import os
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from mailr.config import get_settings
from mailr.db import sessionmaker


def _png(color=(10, 20, 30), size=(32, 32), fmt="PNG") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, fmt)
    return out.getvalue()


async def _identity(user_id: int, email: str) -> int:
    from mailr.models import Identity

    async with sessionmaker()() as db:
        i = Identity(user_id=user_id, email=email.lower(), name="")
        db.add(i)
        await db.commit()
        return i.id


async def _connector(user_id: int, login: str, *, synced: bool = True, data_class: str = "normal") -> int:
    from mailr.models import Connector

    async with sessionmaker()() as db:
        c = Connector(user_id=user_id, kind="imap", name="acct", config={"imap": {"username": login}},
                      enabled=True, data_class=data_class, last_sync_at=datetime.now(UTC) if synced else None)
        db.add(c)
        await db.commit()
        return c.id


async def _person(user_id: int, email: str) -> None:
    from mailr.models import Person

    async with sessionmaker()() as db:
        db.add(Person(user_id=user_id, email=email.lower(), name=""))
        await db.commit()


async def _other(app, role="user"):
    from mailr.core.users import create_user

    async with sessionmaker()() as db:
        u = await create_user(db, f"o-{uuid.uuid4().hex[:8]}@example.com", "Other", password="test-password-123",
                              role=role)
    c = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    r = await c.post("/api/v1/auth/login", json={"email": u.email, "password": "test-password-123"})
    c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return u, c


async def _token(client, scopes, **kw) -> str:
    r = await client.post("/api/v1/tokens", json={"name": "t", "scopes": scopes, **kw})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _bearer(app, tok):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {tok}"})


# ---------------------------------------------------------------- Proven identities


async def test_only_proven_identities_lend_a_picture(app, client, user):
    from mailr.core import account_picture as ap

    other, oc = await _other(app)
    third, tc = await _other(app)
    try:
        bank = f"alerts-{uuid.uuid4().hex[:6]}@bank.example"
        await _person(user.id, bank)
        await _identity(other.id, bank)
        await oc.put("/api/v1/me/picture", files={"file": ("o.png", _png((200, 0, 0)), "image/png")})
        # A bare claim: nobody else sees a picture for it, and the hash list leaves it out.
        assert (await client.get(f"/api/v1/avatars/{bank}")).status_code == 404
        assert ap.address_key(bank) not in (await client.get("/api/v1/pictures/local")).json()["keys"]
        # The claimant still sees their own picture on their own identity.
        assert (await oc.get(f"/api/v1/avatars/{bank}")).status_code == 200
        # A mail account that never synced proves nothing (the login may be wrong).
        await _connector(other.id, bank, synced=False)
        assert (await client.get(f"/api/v1/avatars/{bank}")).status_code == 404
        # One that signs in as that address and has synced does.
        await _connector(other.id, bank)
        r = await client.get(f"/api/v1/avatars/{bank}")
        assert r.status_code == 200
        assert Image.open(io.BytesIO(r.content)).convert("RGB").getpixel((3, 3)) == (200, 0, 0)
        # A later proven claim to the same address never replaces the first owner's picture.
        await _identity(third.id, bank)
        await _connector(third.id, bank)
        await tc.put("/api/v1/me/picture", files={"file": ("t.png", _png((0, 0, 200)), "image/png")})
        await oc.delete("/api/v1/me/picture")
        assert (await client.get(f"/api/v1/avatars/{bank}")).status_code == 404
        # An alias on the same (non-shared) domain as a synced login is proven; on a shared
        # mailbox domain it is not.
        alias = f"other-{uuid.uuid4().hex[:6]}@bank.example"
        await _identity(third.id, alias)
        await _person(user.id, alias)
        assert (await client.get(f"/api/v1/avatars/{alias}")).status_code == 200
        gm = f"x{uuid.uuid4().hex[:6]}@gmail.com"
        await _connector(third.id, f"y{uuid.uuid4().hex[:6]}@gmail.com")
        await _identity(third.id, gm)
        assert (await client.get(f"/api/v1/avatars/{gm}")).status_code == 404
        # The sign-in address of the user (set by the admin) is proven.
        async with sessionmaker()() as db:
            proven = await ap.proven_identities(db)
        assert all(r[2] != gm for r in proven)
    finally:
        await oc.aclose()
        await tc.aclose()


async def test_local_map_names_only_the_viewers_correspondents(app, client, user):
    from mailr.core import account_picture as ap

    other, oc = await _other(app)
    try:
        known = f"k-{uuid.uuid4().hex[:6]}@family.example"
        unknown = f"u-{uuid.uuid4().hex[:6]}@family.example"
        for a in (known, unknown):
            await _identity(other.id, a)
        await _connector(other.id, known)
        await oc.put("/api/v1/me/picture", files={"file": ("o.png", _png(), "image/png")})
        await _person(user.id, known)
        keys = (await client.get("/api/v1/pictures/local")).json()["keys"]
        assert ap.address_key(known) in keys
        assert ap.address_key(unknown) not in keys  # proven (same domain) but not a correspondent
        # The owner's own list holds their own addresses.
        own = (await oc.get("/api/v1/pictures/local")).json()["keys"]
        assert {ap.address_key(known), ap.address_key(unknown)} <= set(own)
    finally:
        await oc.aclose()


# ---------------------------------------------------------------- Picture decoding


async def test_large_pictures_are_refused_and_decoded_off_the_loop(client, monkeypatch):
    import asyncio

    from mailr.core import account_picture as ap

    big = _png(size=(5000, 4000))  # 20 MP, a few kB as a flat PNG
    assert len(big) < ap.MAX_UPLOAD
    r = await client.put("/api/v1/me/picture", files={"file": ("b.png", big, "image/png")})
    assert r.status_code == 400 and "too large" in r.text
    r = await client.put("/api/v1/identities/1/avatar", files={"file": ("b.png", big, "image/png")})
    assert r.status_code in (400, 404)

    threads = []
    real = asyncio.to_thread

    async def spy(fn, *a, **kw):
        threads.append(getattr(fn, "__name__", ""))
        return await real(fn, *a, **kw)

    monkeypatch.setattr(asyncio, "to_thread", spy)
    jpeg = _png(size=(3000, 3000), fmt="JPEG")
    r = await client.put("/api/v1/me/picture", files={"file": ("j.jpg", jpeg, "image/jpeg")})
    assert r.status_code == 200, r.text
    assert "reencode" in threads  # decoded in a thread, not on the event loop


# ---------------------------------------------------------------- Providers list


async def test_providers_needs_read_and_follows_token_limits(app, client, user):
    a = await _connector(user.id, f"a-{uuid.uuid4().hex[:6]}@one.example")
    b = await _connector(user.id, f"b-{uuid.uuid4().hex[:6]}@two.example", data_class="local_only")
    from mailr.models import Identity

    async with sessionmaker()() as db:
        for cid, e in ((a, "me@one.example"), (b, "me@two.example"), (None, "free@three.example")):
            db.add(Identity(user_id=user.id, connector_id=cid, email=e, name=""))
        await db.commit()
    profile = await _token(client, ["profile"])
    read = await _token(client, ["read"])
    limited = await _token(client, ["read"], connector_ids=[a])
    async with _bearer(app, profile) as c:
        assert (await c.get("/api/v1/me/picture/providers")).status_code == 403
    async with _bearer(app, read) as c:
        emails = {i["email"] for i in (await c.get("/api/v1/me/picture/providers")).json()["items"]}
    assert emails == {"me@one.example", "free@three.example"}  # no local-only account
    async with _bearer(app, limited) as c:
        r = await c.get("/api/v1/me/picture/providers")
        assert r.status_code == 200 and {i["email"] for i in r.json()["items"]} == {"me@one.example"}
    emails = {i["email"] for i in (await client.get("/api/v1/me/picture/providers")).json()["items"]}
    assert emails == {"me@one.example", "me@two.example", "free@three.example"}


# ---------------------------------------------------------------- OIDC groups


def test_groups_claim_is_a_set():
    from mailr.core.users import oidc_groups

    assert oidc_groups({"groups": ["family", "admins"]}) == {"family", "admins"}
    assert oidc_groups({"groups": "not-admins"}) == {"not-admins"}
    assert "admins" not in oidc_groups({"groups": "not-admins"})
    assert oidc_groups({"groups": {"a": 1}}) == set()
    assert oidc_groups({"groups": None}) == set() and oidc_groups({}) == set()
    assert oidc_groups({"groups": ["ok", 3, None]}) == {"ok"}


async def test_group_is_checked_at_every_sign_in_for_auto_created_accounts(app, monkeypatch):
    from fastapi import HTTPException

    from mailr.core.users import create_user, user_for_oidc_claims

    s = get_settings()
    monkeypatch.setattr(s, "oidc_auto_create", True)
    monkeypatch.setattr(s, "oidc_auto_create_group", "family")
    monkeypatch.setattr(s, "oidc_admin_group", "admins")
    claims = {"sub": f"s-{uuid.uuid4().hex[:6]}", "email": f"n-{uuid.uuid4().hex[:6]}@example.com",
              "email_verified": True}
    async with sessionmaker()() as db:
        # A string that merely contains the group name is not membership.
        with pytest.raises(HTTPException):
            await user_for_oidc_claims(db, {**claims, "groups": "family-guests"})
        u = await user_for_oidc_claims(db, {**claims, "groups": "family"})
        assert u.oidc_auto_created and u.role == "user"
        await db.commit()
    async with sessionmaker()() as db:
        assert (await user_for_oidc_claims(db, {**claims, "groups": ["family"]})).id == u.id
        with pytest.raises(HTTPException):  # removed from the group
            await user_for_oidc_claims(db, {**claims, "groups": ["friends"]})
    # An account the admin made is not subject to the auto-create group.
    async with sessionmaker()() as db:
        made = await create_user(db, f"m-{uuid.uuid4().hex[:6]}@example.com", "", password="test-password-123")
        got = await user_for_oidc_claims(db, {"sub": f"s-{uuid.uuid4().hex[:6]}", "email": made.email,
                                              "email_verified": True, "groups": "not-admins"})
        assert got.id == made.id and got.role == "user"
        await db.rollback()


# ---------------------------------------------------------------- Health endpoints


@pytest.fixture
def _health(monkeypatch):
    from mailr.core import egress, health

    health.clear_cache()

    async def fine():
        return True, "3 accounts exported in the last 26 h"

    async def bad():
        return False, "1 account not syncing"

    for n in health.CHECKS:
        monkeypatch.setitem(health.CHECK_FUNCS, n, fine)
    monkeypatch.setitem(health.CHECK_FUNCS, "sync", bad)

    async def state(use_cache=True):
        return {"state": "leak", "ok": False, "detail": "LEAK: exit address is the server's own",
                "exit_ip": "203.0.113.9", "at": time.time()}

    async def configured():
        return "http://mailr:pw@egress:8888"

    monkeypatch.setattr(egress, "state", state)
    monkeypatch.setattr(egress, "_configured_from_db", configured)
    monkeypatch.setattr(get_settings(), "health_token", "kuma-token-123")
    yield
    health.clear_cache()


async def test_anonymous_health_gets_only_the_verdict(app, _health):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/health/ready")
        assert r.status_code == 503 and set(r.json()) == {"ok", "checked_at"}
        assert "account" not in r.text
        r = await c.get("/api/v1/health/egress")
        assert r.status_code == 503 and r.json() == {"ok": False, "state": "failing"}
        # A wrong token is the same as none.
        r = await c.get("/api/v1/health/ready", headers={"X-Health-Token": "nope"})
        assert set(r.json()) == {"ok", "checked_at"}
        # The monitor token gets the details.
        h = {"X-Health-Token": "kuma-token-123"}
        r = await c.get("/api/v1/health/ready", headers=h)
        assert r.json()["checks"]["sync"]["detail"] == "1 account not syncing"
        r = await c.get("/api/v1/health/egress", headers=h)
        assert r.json()["state"] == "leak" and "203.0.113" not in r.text


async def test_ok_states_keep_the_monitor_contract(app, _health, monkeypatch):
    from mailr.core import egress, health

    async def fine():
        return True, "fine"

    monkeypatch.setitem(health.CHECK_FUNCS, "sync", fine)
    health.clear_cache()

    async def state(use_cache=True):
        return {"state": "ok", "ok": True, "detail": "via egress proxy", "exit_ip": "149.88.1.2", "at": time.time()}

    monkeypatch.setattr(egress, "state", state)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/health/ready")
        assert r.status_code == 200 and r.json()["ok"] is True
        r = await c.get("/api/v1/health/egress")
        assert r.status_code == 200 and '"state":"ok"' in r.text and "149.88" not in r.text


async def test_admin_session_sees_details(app, client, _health):
    _adm, ac = await _other(app, role="admin")
    try:
        r = await ac.get("/api/v1/health/ready")
        assert "checks" in r.json()
        # An ordinary user does not.
        assert set((await client.get("/api/v1/health/ready")).json()) == {"ok", "checked_at"}
    finally:
        await ac.aclose()


# Numeric hosts and other ports, refused offline: tests/test_egress.py, OFFLINE_REFUSED.


# ---------------------------------------------------------------- Backup marker writes


def test_marker_write_uses_unique_temp_and_cleans_up(tmp_path, monkeypatch):
    from mailr.core import backup

    target = tmp_path / ".last_run"
    # A symlink at the old predictable name is never followed.
    victim = tmp_path / "victim"
    victim.write_text("keep")
    os.symlink(victim, tmp_path / f".{target.name}.tmp-{os.getpid()}")
    backup._write_atomic(target, "2026-10-03")
    assert target.read_text() == "2026-10-03" and victim.read_text() == "keep"
    assert oct(target.stat().st_mode & 0o777) == oct(0o600)  # private, as every backup file (core/private.py)

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(backup.os, "replace", boom)
    with pytest.raises(OSError):
        backup._write_atomic(target, "later")
    left = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert left == [] and target.read_text() == "2026-10-03"


# ---------------------------------------------------------------- Draft header errors


async def test_line_break_in_a_draft_is_400(client, user):
    ident = await _identity(user.id, f"me-{uuid.uuid4().hex[:6]}@example.com")
    for payload in ({"subject": "Hi\r\nBcc: x@evil.example", "to": [{"email": "a@example.org", "name": ""}]},
                    {"subject": "Hi", "to": [{"email": "a@example.org", "name": "A\nBcc: x@evil.example"}]}):
        r = await client.post("/api/v1/drafts", json={"payload": {"identity_id": ident, "body_html": "<p>x</p>",
                                                                  **payload}})
        assert r.status_code == 400 and "line break" in r.text, r.text


# ---------------------------------------------------------------- People ranking


async def test_restricted_token_ranks_people_on_mail_it_may_see(app, client, user):
    from mailr.core import people
    from mailr.models import Message

    normal = await _connector(user.id, f"n-{uuid.uuid4().hex[:6]}@one.example")
    local = await _connector(user.id, f"l-{uuid.uuid4().hex[:6]}@two.example", data_class="local_only")
    now = datetime.now(UTC)

    async def msg(frm, cid, local_only, when):
        async with sessionmaker()() as db:
            m = Message(user_id=user.id, sent_by_me=False, from_email=frm, from_name="", to=[], cc=[], bcc=[],
                        date=when, subject="s", label_ids=[], references=[], attachments=[],
                        message_id_hdr=f"<{uuid.uuid4().hex}@t>", connector_id=cid, local_only=local_only)
            db.add(m)
            await db.flush()
            await people.record(db, m)
            await db.commit()

    # "secret" writes often, but only through the local-only account; "plain" once, long ago.
    for i in range(8):
        await msg("secret@p.example", local, True, now - timedelta(hours=i))
    await msg("secret@p.example", normal, False, now - timedelta(days=400))
    await msg("plain@p.example", normal, False, now - timedelta(days=30))
    session_order = [x["email"] for x in (await client.get("/api/v1/people", params={"limit": 50})).json()]
    assert session_order.index("secret@p.example") < session_order.index("plain@p.example")
    tok = await _token(client, ["read"])
    async with _bearer(app, tok) as c:
        order = [x["email"] for x in (await c.get("/api/v1/people", params={"limit": 50})).json()]
    assert order.index("plain@p.example") < order.index("secret@p.example")
