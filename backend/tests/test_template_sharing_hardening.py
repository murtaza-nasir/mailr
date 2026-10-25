# ACT-SURFACES: api
"""Sharing and templates.

- A token that may not see local-only templates gets the same 404 for one
  as for a missing template, at the request and after approval, and the undo never carries it.

- PUT and DELETE …/shares/{user_id} answer an account the caller may not
  see exactly as an id nobody has (same status, same body), send no notice and never name it. A
  group-mate, someone already on the item, or the address a search showed still works. People
  searches outside one's groups are rate limited.
"""

from __future__ import annotations

import uuid

import pytest
from helpers.sharing_tokens_hardening import _other

from mailr.db import sessionmaker


async def _outsider(name: str = "Zed"):
    from mailr.core.users import create_user

    tag = uuid.uuid4().hex[:6]
    async with sessionmaker()() as db:
        u = await create_user(db, f"zed-{tag}@elsewhere.example", f"{name} Hidden{tag}", password="other-password-123")
        u.assigned_groups, u.idp_groups = [], None
        await db.commit()
        return u


@pytest.mark.parametrize("kind", ["note", "table"])
async def test_share_by_id_never_names_an_account_the_caller_cannot_see(app, kind):
    bob = await _other(app)  # a non-admin in the test group
    out = await _outsider()
    if kind == "note":
        iid = (await bob.post("/api/v1/notes", json={"kind": "text", "title": "x"})).json()["note"]["id"]
        base = f"/api/v1/notes/{iid}/shares"
    else:
        r = await bob.post("/api/v1/tables", json={"name": "T"})
        assert r.status_code in (200, 201), r.text
        iid = r.json().get("table", r.json())["id"]
        base = f"/api/v1/tables/{iid}/shares"
    missing = 10**9 + 7
    r_missing = await bob.put(f"{base}/{missing}", json={"level": "view"})
    r_hidden = await bob.put(f"{base}/{out.id}", json={"level": "view"})
    assert r_missing.status_code == r_hidden.status_code == 404
    assert r_missing.json() == r_hidden.json()
    assert out.email not in r_hidden.text and "Hidden" not in r_hidden.text
    d_missing = await bob.delete(f"{base}/{missing}")
    d_hidden = await bob.delete(f"{base}/{out.id}")
    assert d_missing.status_code == d_hidden.status_code == 404 and d_missing.json() == d_hidden.json()
    # Nothing was shared and no notice went out.
    oc = await _login(app, out.email)
    assert (await oc.get("/api/v1/notes/share-notices")).json()["notices"] == []
    # With the address a search showed, the share works.
    r = await bob.put(f"{base}/{out.id}", json={"level": "view", "email": out.email.upper()})
    assert r.status_code == 200, r.text
    # Once on the item, the id alone changes and stops it.
    assert (await bob.put(f"{base}/{out.id}", json={"level": "edit"})).status_code == 200
    assert (await bob.delete(f"{base}/{out.id}")).status_code == 200
    # A wrong address is the same 404.
    r = await bob.put(f"{base}/{out.id}", json={"level": "view", "email": "someone@else.example"})
    assert r.status_code == 404 and r.json() == r_missing.json()


async def test_group_mates_are_shared_with_by_id(app):
    bob = await _other(app)
    ann = await _other(app, "Ann")
    nid = (await bob.post("/api/v1/notes", json={"kind": "text", "title": "x"})).json()["note"]["id"]
    assert (await bob.put(f"/api/v1/notes/{nid}/shares/{ann.user_id}", json={"level": "view"})).status_code == 200


async def test_people_search_is_rate_limited(app, monkeypatch):
    from mailr.notes import sharing

    monkeypatch.setattr(sharing, "SEARCH_PER_WINDOW", 3)
    bob = await _other(app)
    out = await _outsider()
    nid = (await bob.post("/api/v1/notes", json={"kind": "text", "title": "x"})).json()["note"]["id"]
    seen = []
    for _ in range(5):
        r = await bob.get(f"/api/v1/notes/{nid}/shares", params={"q": out.email})
        seen.append(out.email in r.text)
    assert seen == [True, True, True, False, False]


async def _login(app, email: str):
    from httpx import ASGITransport, AsyncClient

    c = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    r = await c.post("/api/v1/auth/login", json={"email": email, "password": "other-password-123"})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return c


# ---------------------------------------------------------------- Local-only templates


async def _local_template(client) -> str:
    r = await client.post("/api/v1/notes", json={"kind": "checklist", "title": "Meds", "local_only": True,
                                                 "lines": "Sertraline 100mg\nLithium 300mg"})
    nid = r.json()["note"]["id"]
    r = await client.post(f"/api/v1/notes/{nid}/save-template", json={"name": "Meds template"})
    assert r.status_code == 200 and r.json()["template"]["local_only"] is True
    return r.json()["template"]["id"]


async def test_non_local_token_cannot_delete_a_local_only_template(client, user):
    from helpers.sharing_tokens_hardening import _bearer, _tok

    tid = await _local_template(client)
    async with _bearer(client, await _tok(client, ["notes"])) as a:
        r_hidden = await a.delete(f"/api/v1/notes/templates/{tid}")
        r_missing = await a.delete("/api/v1/notes/templates/u999999999")
    assert r_hidden.status_code == r_missing.status_code == 404
    assert r_hidden.json() == r_missing.json() and "Lithium" not in r_hidden.text
    # No approval card was made, and the template is still there.
    lst = (await client.get("/api/v1/notes/templates")).json()["templates"]
    assert any(t["id"] == tid for t in lst)
    # A local-only token may still ask.
    async with _bearer(client, await _tok(client, ["notes"], local_only=True)) as a:
        r = await a.delete(f"/api/v1/notes/templates/{tid}")
    assert r.status_code in (200, 202), r.text


async def test_an_approved_delete_by_a_non_local_token_never_returns_the_content(client, user):
    """A card made before the template became visible to the check (or replayed) still runs as the
    token: the executor answers 404 and the undo never carries the template."""
    from fastapi import HTTPException
    from helpers.sharing_tokens_hardening import _tok

    from mailr.core import permissions
    from mailr.models import ApiToken, User
    from mailr.notes import gate

    tid = await _local_template(client)
    await _tok(client, ["notes"])
    async with sessionmaker()() as db:
        from sqlalchemy import select

        tok = (await db.execute(select(ApiToken).where(ApiToken.user_id == user.id)
                                .order_by(ApiToken.id.desc()))).scalars().first()
        u = await db.get(User, user.id)
        payload = {"ops": [{"op": "template.delete", "note": None, "args": {"template": tid}}], "_by": "agent:t",
                   permissions.REQUESTER_KEY: tok.id}
        with pytest.raises(HTTPException) as e:
            await gate.run_payload(db, u, payload)
        assert e.value.status_code == 404
        await db.rollback()
    lst = (await client.get("/api/v1/notes/templates")).json()["templates"]
    assert any(t["id"] == tid for t in lst)
