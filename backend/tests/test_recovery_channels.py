"""Recovery takes back the standing channels set up while the ended sessions were signed in
(core/recovery.py): an address that speaks for the user, "Follow my forwarded notes" on Allow,
forwarding addresses and Allow rules for Ambrose. Older choices stay, and the user gets a
"Review after signing out" list (the setting recovery_review) until they dismiss it.
"""

from __future__ import annotations

import types
from datetime import UTC, datetime, timedelta

from helpers.recovery_signs_out_apps import store  # noqa: F401
from sqlalchemy import select

from mailr.db import sessionmaker
from mailr.models import ForwardAddress, PermissionRule, TrustedAddress, User


async def _trust(client, monkeypatch, email: str) -> None:
    from mailr.core import trusted

    monkeypatch.setattr(trusted, "secrets", types.SimpleNamespace(randbelow=lambda n: 424242))
    r = await client.post("/api/v1/assistant/trusted-addresses", json={"email": email})
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/v1/assistant/trusted-addresses/{r.json()['id']}/confirm", json={"code": "424242"})
    assert r.status_code == 200, r.text


async def _rows(user_id: int):
    async with sessionmaker()() as db:
        rules = (await db.execute(select(PermissionRule).where(PermissionRule.user_id == user_id))).scalars().all()
        fwd = {f.email: f for f in (await db.execute(select(ForwardAddress).where(ForwardAddress.user_id == user_id))).scalars()}
        tr = {t.email: t for t in (await db.execute(select(TrustedAddress).where(TrustedAddress.user_id == user_id))).scalars()}
        return rules, fwd, tr


async def test_sign_out_everywhere_resets_what_an_intruder_set_up(app, client, user, store, monkeypatch):
    from helpers.agent import _setup_identity

    await _setup_identity(user)
    await _trust(client, monkeypatch, "me@intruder.example")
    r = await client.put("/api/v1/assistant/trusted-addresses/mode", json={"mode": "allow"})
    assert r.status_code == 200 and r.json()["mode"] == "allow", r.text
    long_ago = datetime.now(UTC) - timedelta(days=60)
    async with sessionmaker()() as db:
        db.add(ForwardAddress(user_id=user.id, email="copy@intruder.example", verified_at=datetime.now(UTC)))
        db.add(ForwardAddress(user_id=user.id, email="old@home.example", verified_at=long_ago, created_at=long_ago))
        db.add(PermissionRule(user_id=user.id, subject="assistant", action="mail.label", effect="allow",
                              constraints={}, source="user"))
        db.add(PermissionRule(user_id=user.id, subject="assistant", action="calendar.create", effect="allow",
                              constraints={}, source="user", created_at=long_ago))
        await db.commit()

    r = await client.post("/api/v1/me/sign-out-everywhere")
    assert r.status_code == 200, r.text
    review = r.json()["review"]
    assert review["forwarded_notes_reset"] is True
    assert [a["email"] for a in review["trusted"]] == ["me@intruder.example"]
    assert [a["email"] for a in review["forwards"]] == ["copy@intruder.example"]
    assert [x["action"] for x in review["paused_rules"]] == ["mail.label"]

    body = (await client.get("/api/v1/assistant/trusted-addresses")).json()
    assert body["mode"] == "ask"
    assert [a["state"] for a in body["addresses"] if a["email"] == "me@intruder.example"] != ["confirmed"]
    rules, fwd, tr = await _rows(user.id)
    effects = {r.action: r.effect for r in rules}
    assert effects["mail.label"] == "ask" and effects["calendar.create"] == "allow"  # older choices stay
    assert fwd["copy@intruder.example"].verified_at is None and fwd["old@home.example"].verified_at is not None
    assert tr["me@intruder.example"].confirmed_at is None

    # The list stays on the account for the user until dismissed; a client cannot write it.
    me = (await client.get("/api/v1/me")).json()["settings"]
    assert me["recovery_review"]["trusted"][0]["email"] == "me@intruder.example"
    assert (await client.patch("/api/v1/me/settings", json={"recovery_review": {}})).status_code == 400
    assert (await client.delete("/api/v1/me/recovery-review")).status_code == 200
    assert "recovery_review" not in (await client.get("/api/v1/me")).json()["settings"]


async def test_a_password_change_resets_them_too(client, user, store, monkeypatch):
    from helpers.agent import _setup_identity

    await _setup_identity(user)
    await _trust(client, monkeypatch, "me@intruder.example")
    r = await client.post("/api/v1/me/password", json={"current": "test-password-123", "new": "a-new-password-456"})
    assert r.status_code == 200, r.text
    assert [a["email"] for a in r.json()["review"]["trusted"]] == ["me@intruder.example"]


async def test_an_admin_reset_resets_them_for_the_next_sign_in(admin_client, client, user, store, monkeypatch):
    from helpers.agent import _setup_identity

    await _setup_identity(user)
    await _trust(client, monkeypatch, "me@intruder.example")
    r = await admin_client.post(f"/api/v1/admin/users/{user.id}/password", json={"password": "reset-password-789"})
    assert r.status_code == 200 and r.json()["reset_channels"] is True, r.text
    async with sessionmaker()() as db:
        assert (await db.get(User, user.id)).settings["recovery_review"]["trusted"][0]["email"] == "me@intruder.example"
    _, _, tr = await _rows(user.id)
    assert tr["me@intruder.example"].confirmed_at is None


async def test_nothing_new_means_nothing_to_review(client, user, store):
    """A routine recovery with nothing set up recently changes nothing and leaves no notice."""
    long_ago = datetime.now(UTC) - timedelta(days=60)
    async with sessionmaker()() as db:
        db.add(PermissionRule(user_id=user.id, subject="assistant", action="mail.label", effect="allow",
                              constraints={}, source="user", created_at=long_ago))
        await db.commit()
    r = await client.post("/api/v1/me/sign-out-everywhere")
    assert r.status_code == 200 and r.json()["review"] == {}, r.text
    rules, _, _ = await _rows(user.id)
    assert [r.effect for r in rules] == ["allow"]
    assert "recovery_review" not in (await client.get("/api/v1/me")).json()["settings"]


def test_the_window_reaches_back_to_the_oldest_ended_session():
    from mailr.core.recovery import REVIEW_DAYS, window_start

    now = datetime.now(UTC)
    assert window_start([], now) == now - timedelta(days=REVIEW_DAYS)
    old = now - timedelta(days=25)
    assert window_start([now - timedelta(days=1), old], now) == old
