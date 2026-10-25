# ACT-SURFACES: api
"""Templates.

Covers (api): ACT template.create, template.edit, template.delete (CRUD, shortcut rules,
sanitizing, owner-only with 404 for another user's, token scopes) and template.insert (the expand
route: variables from the first recipient, the sending identity and the date; unknown or empty
variables stay as typed)."""

import uuid
from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient

from mailr.core import templates as tpl
from mailr.db import sessionmaker
from mailr.models import Connector, Identity, Person


async def _create(client, **kw):
    body = {"name": "Thanks", "shortcut": "thx", "subject": "", "body_html": "<p>Hi {first_name},</p>", **kw}
    r = await client.post("/api/v1/templates", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def _other(app):
    from mailr.core.users import create_user

    async with sessionmaker()() as db:
        u = await create_user(db, f"o-{uuid.uuid4().hex[:8]}@example.com", "Other", password="other-password-123")
    c = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    r = await c.post("/api/v1/auth/login", json={"email": u.email, "password": "other-password-123"})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return c


async def _token(client, scopes):
    r = await client.post("/api/v1/tokens", json={"name": "t", "scopes": scopes, "local_only": False, "connector_ids": []})
    assert r.status_code == 200, r.text
    a = AsyncClient(transport=client._transport, base_url="http://test")
    a.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return a


async def test_template_create_edit_delete(client):
    """template.create, template.edit and template.delete through the API."""
    t = await _create(client, shortcut=";Thx ", subject="Thank you")
    assert t["shortcut"] == "thx" and t["subject"] == "Thank you" and t["name"] == "Thanks"
    items = (await client.get("/api/v1/templates")).json()
    assert [x["id"] for x in items["items"]] == [t["id"]]
    assert "first_name" in items["variables"]

    r = await client.patch(f"/api/v1/templates/{t['id']}", json={"name": "Thanks a lot", "shortcut": ""})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Thanks a lot" and r.json()["shortcut"] == "" and r.json()["subject"] == "Thank you"
    assert (await client.get(f"/api/v1/templates/{t['id']}")).json()["name"] == "Thanks a lot"

    assert (await client.delete(f"/api/v1/templates/{t['id']}")).json() == {"ok": True}
    assert (await client.get(f"/api/v1/templates/{t['id']}")).status_code == 404
    assert (await client.get("/api/v1/templates")).json()["items"] == []


async def test_template_shortcut_rules(client):
    """template.create: a shortcut is a slug, unique per user; several templates may have none."""
    a = await _create(client, shortcut="intro")
    assert (await client.post("/api/v1/templates", json={"name": "Dup", "shortcut": "intro"})).status_code == 409
    assert (await client.post("/api/v1/templates", json={"name": "Bad", "shortcut": "no spaces"})).status_code == 400
    assert (await client.post("/api/v1/templates", json={"name": "  "})).status_code == 400
    await _create(client, name="One", shortcut="")
    await _create(client, name="Two", shortcut="")
    b = await _create(client, name="Other", shortcut="other")
    # template.edit: taking another template's shortcut is refused; keeping your own is fine.
    assert (await client.patch(f"/api/v1/templates/{b['id']}", json={"shortcut": "intro"})).status_code == 409
    assert (await client.patch(f"/api/v1/templates/{a['id']}", json={"shortcut": "intro"})).status_code == 200


async def test_template_body_is_sanitized(client):
    """template.create / template.edit clean the body like a signature."""
    t = await _create(client, body_html='<p onclick="x()">Hi<script>alert(1)</script> <a href="javascript:x">l</a>'
                                        '<img src="https://e.org/a.png" onerror="y()"><b>bold</b></p>')
    assert "script" not in t["body_html"] and "onclick" not in t["body_html"] and "onerror" not in t["body_html"]
    assert "javascript:" not in t["body_html"] and "<b>bold</b>" in t["body_html"]
    r = await client.patch(f"/api/v1/templates/{t['id']}", json={"body_html": '<iframe src="https://e.org"></iframe><i>ok</i>'})
    assert "iframe" not in r.json()["body_html"] and "<i>ok</i>" in r.json()["body_html"]


async def test_template_insert_fills_variables(client, user):
    """template.insert: the expand route fills the variables for the draft's first recipient."""
    async with sessionmaker()() as db:
        conn = Connector(user_id=user.id, kind="imap", name="a", mode="mirror", data_class="normal", config={},
                         state={}, enabled=False)
        db.add(conn)
        await db.flush()
        ident = Identity(user_id=user.id, email="sam@example.org", name="Sam Sender", connector_id=conn.id)
        db.add(ident)
        db.add(Person(user_id=user.id, email="known@example.org", name="Lee Known"))
        await db.commit()
        iid = ident.id
    t = await _create(client, subject="For {first_name}: {unknown}",
                      body_html="<p>Dear {name} ({email}), {first_name}. {my_name}, {date}. {nope} {Name}</p>")
    r = await client.post(f"/api/v1/templates/{t['id']}/expand",
                          json={"to": [{"email": "dana@example.org", "name": "Dana <Tester>"}], "identity_id": iid})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["subject"] == "For Dana: {unknown}"
    today = tpl.today_text(None)
    assert out["body_html"] == (f"<p>Dear Dana &lt;Tester&gt; (dana@example.org), Dana. Sam Sender, {today}."
                                " {nope} {Name}</p>")

    # No name typed: the name mailr knows for the address. Cc counts when To is empty.
    out = (await client.post(f"/api/v1/templates/{t['id']}/expand", json={"cc": [{"email": "Known@example.org"}]})).json()
    assert "Dear Lee Known (Known@example.org), Lee." in out["body_html"]
    assert "Sam Sender" in out["body_html"]  # no identity given: the default (here the only) identity

    # No recipient: the recipient variables stay as typed.
    out = (await client.post(f"/api/v1/templates/{t['id']}/expand", json={})).json()
    assert out["subject"] == "For {first_name}: {unknown}"
    assert "Dear {name} ({email}), {first_name}." in out["body_html"]


def test_template_insert_values():
    """template.insert: first names, dates in the user's zone, and HTML escaping of values."""
    assert tpl.first_name("Dana Tester") == "Dana"
    assert tpl.first_name('"Tester, Dana"') == "Dana"
    assert tpl.first_name("") == ""
    late = datetime(2026, 10, 1, 2, 0, tzinfo=UTC)
    assert tpl.today_text("America/Chicago", late) == "September 30, 2026"
    assert tpl.today_text("Not/AZone", late) == "October 1, 2026"
    vals = tpl.values({"email": "a@b.c", "name": "<b>X</b>"}, "Me", None, late)
    assert tpl.expand("{name}", "<p>{name}</p>", vals) == {"subject": "<b>X</b>", "body_html": "<p>&lt;b&gt;X&lt;/b&gt;</p>"}


async def test_template_other_users_get_404(app, client):
    """template.edit / template.delete / template.insert: another user's template is not found."""
    t = await _create(client, body_html="<p>OWNERSECRET</p>")
    other = await _other(app)
    try:
        assert (await other.get(f"/api/v1/templates/{t['id']}")).status_code == 404
        assert (await other.patch(f"/api/v1/templates/{t['id']}", json={"name": "x"})).status_code == 404
        assert (await other.delete(f"/api/v1/templates/{t['id']}")).status_code == 404
        assert (await other.post(f"/api/v1/templates/{t['id']}/expand", json={})).status_code == 404
        assert (await other.get("/api/v1/templates")).json()["items"] == []
        # Shortcuts are per user.
        assert (await other.post("/api/v1/templates", json={"name": "Mine", "shortcut": "thx"})).status_code == 201
    finally:
        await other.aclose()
    assert (await client.get(f"/api/v1/templates/{t['id']}")).json()["body_html"] == "<p>OWNERSECRET</p>"


async def test_template_token_scopes(client):
    """template.create needs the draft scope; read lists and expands."""
    t = await _create(client)
    reader = await _token(client, ["read"])
    writer = await _token(client, ["read", "draft"])
    try:
        assert (await reader.get("/api/v1/templates")).status_code == 200
        assert (await reader.post(f"/api/v1/templates/{t['id']}/expand", json={})).status_code == 200
        assert (await reader.post("/api/v1/templates", json={"name": "x"})).status_code == 403
        assert (await reader.delete(f"/api/v1/templates/{t['id']}")).status_code == 403
        r = await writer.post("/api/v1/templates", json={"name": "From a token", "shortcut": "tok"})
        assert r.status_code == 201, r.text
    finally:
        await reader.aclose()
        await writer.aclose()
