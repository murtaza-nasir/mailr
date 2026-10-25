"""Regression tests for authentication, sessions, authorization and isolation: each test is an
attack that must fail."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import mail_date
from helpers.agent import _ingest, _token_client
from helpers.security_authz import _conn
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from mailr.db import sessionmaker
from mailr.models import ApiToken, AssistantRun, LabelDecision, User


async def _tok(client, scopes, **kw):
    r = await client.post("/api/v1/tokens", json={"name": f"t-{uuid.uuid4().hex[:4]}", "scopes": scopes, **kw})
    assert r.status_code == 200, r.text
    return r.json()["token"]


async def _clear_rate_events():
    async with sessionmaker()() as db:
        await db.execute(text("DELETE FROM rate_events"))
        await db.commit()


# ---------------------------------------------------------------- Run outputs and briefs


async def test_run_outputs_and_briefs_hidden_from_restricted_tokens(client, user, raw):
    uni = await _conn(user.id, "local_only")
    normal = await _conn(user.id)
    _, c_loc = await _ingest(user.id, raw("Student record", frm="Dean <dean@uni.example>",
                                          body="FERPA grade appeal for student 4711"), uni)
    _, c_norm = await _ingest(user.id, raw("Lunch", frm="Bob <bob@x.example>", body="Lunch?"), normal)
    async with sessionmaker()() as db:
        for cid, marker in ((c_loc, "LOCAL-BRIEF-MARK"), (c_norm, "NORMAL-BRIEF-MARK")):
            db.add(AssistantRun(user_id=user.id, trigger="manual", slot=f"b{uuid.uuid4().hex[:6]}",
                                conversation_id=cid, status="done", output=marker, delivered=["brief"]))
        db.add(AssistantRun(user_id=user.id, trigger="schedule", slot=f"r{uuid.uuid4().hex[:6]}",
                            status="done", output="ROUTINE-DIGEST-MARK", delivered=["digest"]))
        await db.commit()

    # Restricted: not local_only, and one limited to the normal account.
    for kw in ({}, {"connector_ids": [normal]}):
        async with _token_client(client, await _tok(client, ["read", "draft"], **kw)) as a:
            runs = await a.get("/api/v1/assistant/runs")
            assert runs.status_code == 200
            for marker in ("LOCAL-BRIEF-MARK", "NORMAL-BRIEF-MARK", "ROUTINE-DIGEST-MARK"):
                assert marker not in runs.text
            for cid in (c_loc, c_norm):
                r = await a.post("/api/v1/agent/tools/get_brief", json={"conversation_id": cid})
                assert "BRIEF-MARK" not in r.text
                r = await a.post("/api/v1/agent/tools/update_brief", json={"conversation_id": cid, "text": "pwned"})
                assert r.status_code != 200 or not r.json().get("ok"), r.text
    # The session and an unrestricted (local_only, every account) token still see them.
    runs = await client.get("/api/v1/assistant/runs")
    assert "LOCAL-BRIEF-MARK" in runs.text and "ROUTINE-DIGEST-MARK" in runs.text
    async with _token_client(client, await _tok(client, ["read"], local_only=True)) as a:
        r = await a.post("/api/v1/agent/tools/get_brief", json={"conversation_id": c_loc})
        assert "LOCAL-BRIEF-MARK" in r.text
        assert "ROUTINE-DIGEST-MARK" in (await a.get("/api/v1/assistant/runs")).text
    async with sessionmaker()() as db:
        outs = (await db.execute(select(AssistantRun.output).where(AssistantRun.user_id == user.id))).scalars().all()
    assert "pwned" not in outs


# ---------------------------------------------------------------- Smart reasons


async def test_smart_reason_never_from_a_hidden_message(client, user, raw):
    uni = await _conn(user.id, "local_only")
    normal = await _conn(user.id)
    a_id, conv = await _ingest(user.id, raw("Appeal", frm="Dean <dean@uni.example>", msgid="<hid-a@uni.example>",
                                            body="secret", date=mail_date()), uni)
    b_id, conv_b = await _ingest(user.id, raw("Re: Appeal", frm="Dean <dean@uni.example>", irt="<hid-a@uni.example>",
                                              body="visible", date=mail_date(1)), normal)
    assert conv == conv_b
    async with sessionmaker()() as db:
        db.add(LabelDecision(user_id=user.id, message_id=b_id, kind="smart", target="needs_reply", value=True,
                             source="llm", reason="VISIBLE-REASON", status="applied"))
        await db.flush()
        db.add(LabelDecision(user_id=user.id, message_id=a_id, kind="smart", target="needs_reply", value=True,
                             source="llm", reason="HIDDEN-MARK approve the appeal", status="applied"))
        await db.commit()
    async with _token_client(client, await _tok(client, ["read"])) as a:
        r = await a.get("/api/v1/smart/needs_reply")
        assert r.status_code == 200 and "HIDDEN-MARK" not in r.text
        assert "VISIBLE-REASON" in r.text
    r = await client.get("/api/v1/smart/needs_reply")
    assert "HIDDEN-MARK" in r.text


async def test_smart_counts_recomputed_for_tokens(client, user, raw):
    uni = await _conn(user.id, "local_only")
    mid, _ = await _ingest(user.id, raw("Hidden", frm="Dean <dean@uni.example>", body="x"), uni)
    async with sessionmaker()() as db:
        db.add(LabelDecision(user_id=user.id, message_id=mid, kind="smart", target="needs_reply", value=True,
                             source="llm", reason="r", status="applied"))
        await db.commit()
    session = {x["id"]: x["total"] for x in (await client.get("/api/v1/smart")).json()}
    assert session["needs_reply"] >= 1
    async with _token_client(client, await _tok(client, ["read"])) as a:
        tok = {x["id"]: x["total"] for x in (await a.get("/api/v1/smart")).json()}
    assert tok["needs_reply"] == session["needs_reply"] - 1


# ---------------------------------------------------------------- Login timing


async def test_unknown_email_costs_one_argon2_verify(app, user, monkeypatch):
    from mailr.core import auth

    calls = []

    class Counting:
        def __init__(self, inner):
            self.inner = inner

        def verify(self, h, pw):
            calls.append(h)
            return self.inner.verify(h, pw)

        def hash(self, pw):
            return self.inner.hash(pw)

    monkeypatch.setattr(auth, "_ph", Counting(auth._ph))
    await _clear_rate_events()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        for email in (f"nobody-{uuid.uuid4().hex[:6]}@example.com", user.email):
            calls.clear()
            r = await c.post("/api/v1/auth/login", json={"email": email, "password": "wrong-password"})
            assert r.status_code == 401 and r.json()["detail"] == "wrong email or password"
            assert len(calls) == 1, email
    await _clear_rate_events()


# ---------------------------------------------------------------- Avatar deletes


async def test_tokens_cannot_delete_pictures(app, client, user):
    from mailr.api import avatar_routes

    async with sessionmaker()() as db:
        from mailr.models import Identity

        i = Identity(user_id=user.id, email=f"me-{uuid.uuid4().hex[:4]}@example.com", name="Me", receives=[])
        db.add(i)
        await db.commit()
        iid = i.id
    f = avatar_routes._user_file(user.id, "people", avatar_routes._key(avatar_routes._norm("bob@x.example")))
    avatar_routes._store(f, b"\x89PNG\r\n\x1a\n", "image/png")
    g = avatar_routes._user_file(user.id, "identities", str(iid))
    avatar_routes._store(g, b"\x89PNG\r\n\x1a\n", "image/png")
    async with _token_client(client, await _tok(client, [])) as a:
        assert (await a.delete("/api/v1/people/bob@x.example/avatar")).status_code == 403
        assert (await a.delete(f"/api/v1/identities/{iid}/avatar")).status_code == 403
    async with _token_client(client, await _tok(client, ["read", "label"])) as a:
        assert (await a.delete("/api/v1/people/bob@x.example/avatar")).status_code == 403
    assert f.exists() and g.exists()
    assert (await client.delete("/api/v1/people/bob@x.example/avatar")).status_code == 200
    assert not f.exists()


# ---------------------------------------------------------------- Shared throttle


async def test_spraying_many_addresses_from_one_ip_is_throttled(app, user):
    from mailr.core import throttle

    await _clear_rate_events()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            codes = [(await c.post("/api/v1/auth/login", json={"email": f"spray{i}-{uuid.uuid4().hex[:4]}@example.com",
                                                               "password": "x"})).status_code
                     for i in range(throttle.LOGIN_PER_IP.count + 2)]
            assert codes[:throttle.LOGIN_PER_IP.count] == [401] * throttle.LOGIN_PER_IP.count
            assert codes[-1] == 429
            # Once the IP is over budget even the right password is refused (no oracle).
            r = await c.post("/api/v1/auth/login", json={"email": user.email, "password": "test-password-123"})
            assert r.status_code == 429
        async with sessionmaker()() as db:
            keys = (await db.execute(text("SELECT key FROM rate_events"))).scalars().all()
        # Bucket names are hashed: no email address or IP sits in the table.
        assert keys and not any("@" in k or "127.0.0.1" in k for k in keys)
    finally:
        await _clear_rate_events()


async def test_one_address_is_throttled_and_old_rows_are_pruned(app, user):
    from mailr.core import throttle

    await _clear_rate_events()
    try:
        async with sessionmaker()() as db:
            for _ in range(500):
                await db.execute(text("INSERT INTO rate_events (key, created_at) VALUES ('old:x', :t)"),
                                 {"t": datetime.now(UTC) - timedelta(hours=2)})
            await db.commit()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            codes = [(await c.post("/api/v1/auth/login", json={"email": user.email, "password": f"w{i}"})).status_code
                     for i in range(throttle.LOGIN_PER_IP_ACCOUNT.count + 1)]
        assert codes[-1] == 429 and codes[0] == 401
        async with sessionmaker()() as db:
            old = (await db.execute(text("SELECT count(*) FROM rate_events WHERE key='old:x'"))).scalar()
        assert old == 0  # pruned on write: the table cannot grow without bound
    finally:
        await _clear_rate_events()


async def test_signup_and_native_sign_in_are_throttled(app, monkeypatch):
    from mailr.api import native_auth
    from mailr.config import get_settings
    from mailr.core import throttle

    await _clear_rate_events()
    s = get_settings()
    monkeypatch.setattr(s, "allow_signup", True)
    monkeypatch.setattr(s, "oidc_issuer", "https://idp.example")
    monkeypatch.setattr(s, "oidc_native_client_id", "app")

    async def discovery():
        return {"issuer": "https://idp.example", "jwks_uri": "https://idp.example/jwks"}

    async def jwks(uri, refresh=False):
        return {"keys": []}

    monkeypatch.setattr(native_auth, "_discovery", discovery)
    monkeypatch.setattr(native_auth, "_jwks", jwks)
    monkeypatch.setattr(throttle, "SIGNUP_PER_IP", throttle.Limit("signup-ip", 2, timedelta(hours=1)))
    monkeypatch.setattr(throttle, "NATIVE_PER_IP", throttle.Limit("native-ip", 3, timedelta(minutes=15)))
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            codes = [(await c.post("/api/v1/auth/signup", json={"email": f"s{i}-{uuid.uuid4().hex[:4]}@example.com",
                                                                "password": "short"})).status_code for i in range(3)]
            assert codes == [400, 400, 429]
            codes = [(await c.post("/api/v1/auth/oidc/native", json={"id_token": "x" * 40})).status_code
                     for _ in range(4)]
            assert codes == [401, 401, 401, 429]
    finally:
        await _clear_rate_events()


# ---------------------------------------------------------------- Tokens and password changes


async def test_password_change_and_admin_reset_revoke_tokens(app, client, user):
    tok = await _tok(client, ["read"])
    async with _token_client(client, tok) as a:
        assert (await a.get("/api/v1/me")).status_code == 200
        r = await client.post("/api/v1/me/password", json={"current": "test-password-123", "new": "new-password-456"})
        assert r.status_code == 200 and r.json()["revoked_tokens"] >= 1
        assert (await a.get("/api/v1/me")).status_code == 401
    # Administrator reset: the same.
    from mailr.core.users import create_user

    async with sessionmaker()() as db:
        adm = await create_user(db, f"adm-{uuid.uuid4().hex[:6]}@example.com", "Admin", password="admin-password-1",
                                role="admin")
    tok2 = await _tok(client, ["read"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.post("/api/v1/auth/login", json={"email": adm.email, "password": "admin-password-1"})
        ac.headers["X-CSRF-Token"] = r.json()["csrf"]
        r = await ac.post(f"/api/v1/admin/users/{user.id}/password", json={"password": "reset-password-789"})
        assert r.status_code == 200 and r.json()["revoked_tokens"] >= 1
    async with _token_client(client, tok2) as a:
        assert (await a.get("/api/v1/me")).status_code == 401
    async with sessionmaker()() as db:
        live = (await db.execute(select(ApiToken).where(ApiToken.user_id == user.id,
                                                        ApiToken.revoked.is_(False)))).scalars().all()
    assert live == []


# ---------------------------------------------------------------- Idempotency


async def test_replay_needs_a_live_credential_and_csrf(app, client, user):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c2:
        r = await c2.post("/api/v1/auth/login", json={"email": user.email, "password": "test-password-123"})
        c2.headers["X-CSRF-Token"] = r.json()["csrf"]
        raw2 = c2.cookies.get("mailr_session")
        key = f"idem-{uuid.uuid4().hex[:6]}"
        first = await c2.post("/api/v1/labels", json={"name": f"Idem {key}"}, headers={"Idempotency-Key": key})
        assert first.status_code == 200, first.text
        again = await c2.post("/api/v1/labels", json={"name": f"Idem {key}"}, headers={"Idempotency-Key": key})
        assert again.headers.get("idempotent-replayed") == "true"
        # Without the CSRF header the stored response is not replayed.
        no_csrf = await c2.post("/api/v1/labels", json={"name": f"Idem {key}"},
                                headers={"Idempotency-Key": key, "X-CSRF-Token": ""})
        assert no_csrf.status_code == 403 and no_csrf.headers.get("idempotent-replayed") is None
    # The session is revoked (password change); a replay with the dead cookie gets 401.
    r = await client.post("/api/v1/me/password", json={"current": "test-password-123", "new": "new-password-456"})
    assert r.status_code == 200
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c3:
        c3.cookies.set("mailr_session", raw2)
        r = await c3.post("/api/v1/labels", json={"name": f"Idem {key}"}, headers={"Idempotency-Key": key})
        assert r.status_code in (401, 403) and r.headers.get("idempotent-replayed") is None


async def test_no_rows_for_unauthenticated_callers_and_bodies_encrypted(app, client, user, raw):
    async with sessionmaker()() as db:
        before = (await db.execute(text("select count(*) from idempotency_keys"))).scalar()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c5:
        c5.cookies.set("mailr_session", "junk")
        for i in range(3):
            await c5.post("/api/v1/reminders/act", json={"token": "x" * 20, "action": "dismiss"},
                          headers={"Idempotency-Key": f"junk-{i}-{uuid.uuid4().hex[:4]}"})
            await c5.post("/api/v1/integrations/speakr/nope/webhook", json={},
                          headers={"Idempotency-Key": f"junkw-{i}-{uuid.uuid4().hex[:4]}"})
    async with sessionmaker()() as db:
        after = (await db.execute(text("select count(*) from idempotency_keys"))).scalar()
    assert after == before
    # A tool result (mail content) is stored encrypted, never as plain JSON.
    mid, _ = await _ingest(user.id, raw("Secret plan", body="IDEM-SECRET-BODY-MARK"))
    tok = await _tok(client, ["read"])
    key = f"tool-{uuid.uuid4().hex[:6]}"
    async with _token_client(client, tok) as a:
        r = await a.post("/api/v1/agent/tools/get_message_body", json={"message_id": mid},
                         headers={"Idempotency-Key": key})
        assert r.status_code == 200 and "IDEM-SECRET-BODY-MARK" in r.text
        r2 = await a.post("/api/v1/agent/tools/get_message_body", json={"message_id": mid},
                          headers={"Idempotency-Key": key})
        assert r2.headers.get("idempotent-replayed") == "true" and "IDEM-SECRET-BODY-MARK" in r2.text
    async with sessionmaker()() as db:
        leaked = (await db.execute(text("select count(*) from idempotency_keys "
                                        "where response::text like '%IDEM-SECRET-BODY-MARK%'"))).scalar()
        users = (await db.execute(text("select count(*) from idempotency_keys where user_id = 0 "
                                       "and key not like 'idtok:%'"))).scalar()
    assert leaked == 0 and users == 0


async def test_rows_expire_after_a_day(client, user):
    from mailr.core import tasks

    key = f"old-{uuid.uuid4().hex[:6]}"
    r = await client.post("/api/v1/labels", json={"name": f"Old {key}"}, headers={"Idempotency-Key": key})
    assert r.status_code == 200
    async with sessionmaker()() as db:
        await db.execute(text("UPDATE idempotency_keys SET created_at = now() - interval '25 hours' "
                              "WHERE user_id = :u"), {"u": user.id})
        await db.commit()
    r = await client.post("/api/v1/labels", json={"name": f"Old {key}"}, headers={"Idempotency-Key": key})
    assert r.headers.get("idempotent-replayed") is None
    await tasks._cleanup_db()
    async with sessionmaker()() as db:
        n = (await db.execute(text("select count(*) from idempotency_keys where user_id = :u "
                                   "and created_at < now() - interval '24 hours'"), {"u": user.id})).scalar()
    assert n == 0


# ---------------------------------------------------------------- Sign-up and OIDC linking


async def test_signup_account_is_never_linked_by_email(app, monkeypatch):
    from fastapi import HTTPException

    from mailr.config import get_settings
    from mailr.core.users import user_for_oidc_claims

    await _clear_rate_events()
    monkeypatch.setattr(get_settings(), "allow_signup", True)
    victim = f"victim-{uuid.uuid4().hex[:6]}@org.example"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/v1/auth/signup", json={"email": victim, "password": "attacker-pass-1"})
        assert r.status_code == 200 and r.json()["user"]["email_verified"] is False
    async with sessionmaker()() as db:
        with pytest.raises(HTTPException) as e:
            await user_for_oidc_claims(db, {"sub": "victim-sub", "email": victim, "email_verified": True})
        assert e.value.status_code == 403
        u = (await db.execute(select(User).where(User.email == victim))).scalar_one()
        assert u.oidc_sub is None
    await _clear_rate_events()


# ---------------------------------------------------------------- Defaults and caches


async def test_auto_create_off_by_default_and_logout_clears_cache(app, client, monkeypatch):
    from fastapi import HTTPException

    from mailr.config import Settings, get_settings
    from mailr.core.users import user_for_oidc_claims

    assert Settings.model_fields["oidc_auto_create"].default is False
    monkeypatch.setattr(get_settings(), "oidc_auto_create", False)
    claims = {"sub": f"s-{uuid.uuid4().hex[:6]}", "email": f"new-{uuid.uuid4().hex[:6]}@example.com",
              "email_verified": True, "groups": ["staff"]}
    async with sessionmaker()() as db:
        with pytest.raises(HTTPException):
            await user_for_oidc_claims(db, claims)
    # With auto-create on and a group required, only members get an account.
    monkeypatch.setattr(get_settings(), "oidc_auto_create", True)
    monkeypatch.setattr(get_settings(), "oidc_auto_create_group", "mail-users")
    async with sessionmaker()() as db:
        with pytest.raises(HTTPException):
            await user_for_oidc_claims(db, claims)
        u = await user_for_oidc_claims(db, {**claims, "groups": ["mail-users"]})
        assert u.email == claims["email"]
        await db.rollback()
    r = await client.post("/api/v1/auth/logout")
    assert r.status_code == 200 and r.headers.get("clear-site-data") == '"cache"'


# ---------------------------------------------------------------- ID tokens with odd algorithms


async def test_hs256_id_token_is_401_not_500(app, monkeypatch):
    import base64

    from mailr.api import native_auth
    from mailr.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "oidc_issuer", "https://idp.example")
    monkeypatch.setattr(s, "oidc_native_client_id", "app")

    async def discovery():
        return {"issuer": "https://idp.example", "jwks_uri": "https://idp.example/jwks"}

    async def jwks(uri, refresh=False):
        return {"keys": [{"kty": "RSA", "kid": "k1", "n": "sXch", "e": "AQAB"}]}

    monkeypatch.setattr(native_auth, "_discovery", discovery)
    monkeypatch.setattr(native_auth, "_jwks", jwks)

    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    await _clear_rate_events()
    tok = f"{b64({'alg': 'HS256', 'typ': 'JWT'})}.{b64({'sub': 'x', 'aud': 'app'})}.c2ln"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/v1/auth/oidc/native", json={"id_token": tok})
    assert r.status_code == 401, r.text
    await _clear_rate_events()


# ---------------------------------------------------------------- CSRF


async def test_csrf_compare_is_constant_time(client, monkeypatch):
    import secrets as pysecrets

    from mailr.core import auth

    seen = []
    real = pysecrets.compare_digest

    def spy(a, b):
        seen.append(1)
        return real(a, b)

    monkeypatch.setattr(auth.secrets, "compare_digest", spy)
    assert (await client.post("/api/v1/labels", json={"name": f"c-{uuid.uuid4().hex[:4]}"})).status_code == 200
    assert seen
    r = await client.post("/api/v1/labels", json={"name": "x"}, headers={"X-CSRF-Token": "wrong"})
    assert r.status_code == 403


# ---------------------------------------------------------------- Zero-scope tokens


async def test_zero_scope_token_reads_almost_nothing(client, user):
    async with _token_client(client, await _tok(client, [])) as a:
        me = (await a.get("/api/v1/me")).json()
        assert me["scopes"] == [] and "settings" not in me and "identities" not in me and "counts" not in me
        assert (await a.get("/api/v1/me/imap")).status_code == 403
        assert (await a.get("/api/v1/forwarding")).status_code == 403
        tools = (await a.get("/api/v1/agent/tools")).json()
        assert tools["tools"] == [] and tools["all"] == []
    async with _token_client(client, await _tok(client, ["read"])) as a:
        me = (await a.get("/api/v1/me")).json()
        assert "settings" in me
        assert (await a.get("/api/v1/me/imap")).status_code == 200
        assert (await a.get("/api/v1/forwarding")).status_code == 200


# ---------------------------------------------------------------- Calendar check


async def test_calendar_check_needs_scope_and_account(client, user):
    cal = await _conn(user.id, kind="caldav")
    other = await _conn(user.id)
    async with _token_client(client, await _tok(client, ["read"])) as a:
        assert (await a.post(f"/api/v1/calendar/accounts/{cal}/check")).status_code == 403
    async with _token_client(client, await _tok(client, ["read", "calendar"], connector_ids=[other])) as a:
        assert (await a.post(f"/api/v1/calendar/accounts/{cal}/check")).status_code == 404
    async with _token_client(client, await _tok(client, ["read", "calendar"])) as a:
        assert (await a.post(f"/api/v1/calendar/accounts/{cal}/check")).status_code == 200


# ---------------------------------------------------------------- Reminder button tokens


async def test_reminder_token_is_bound_to_one_action(app, user):
    from mailr.calendar.reminders import act_token, act_tokens

    key = "t|task-uid-1|2026-10-05|0"
    snooze = act_token(user.id, key, "snooze")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        r = await anon.post("/api/v1/reminders/act", json={"token": snooze, "action": "done"})
        assert r.status_code == 403
        r = await anon.post("/api/v1/reminders/act", json={"token": snooze, "action": "dismiss"})
        assert r.status_code == 403
        r = await anon.post("/api/v1/reminders/act", json={"token": snooze, "action": "snooze"})
        assert r.status_code == 200
    toks = act_tokens(user.id, key)
    assert set(toks) == {"snooze", "done", "dismiss"} and len(set(toks.values())) == 3


# ---------------------------------------------------------------- JSON body size before auth


async def test_public_id_middleware_caps_json_bodies(app, monkeypatch):
    from mailr.config import get_settings
    from mailr.core import publicid_http

    monkeypatch.setattr(get_settings(), "public_ids", True)
    monkeypatch.setattr(publicid_http, "MAX_JSON_BODY", 1024)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/v1/labels", content=b'{"name": "' + b"x" * 4096 + b'"}',
                         headers={"Content-Type": "application/json"})
        assert r.status_code == 413

        async def chunks():
            for _ in range(8):
                yield b" " * 512

        r = await c.post("/api/v1/labels", content=chunks(), headers={"Content-Type": "application/json"})
        assert r.status_code == 413
