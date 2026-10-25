# ACT-SURFACES: api
"""Sharing and tokens: regression tests. Each one is an attack that must fail.

- When the owner stops sharing with someone who left, "Add back" no longer restores access.
- A restricted token never reads the subject of mail it may not see through a note or table
  link: linking such a conversation is a 404, and a stored link shows the placeholder.
- GET /notes leaves local-only tables out for a token that may not see local-only notes.
- Note link titles show the owner's subjects only to viewers who can see the conversation.
- A template saved from a local-only item stays local-only (hidden from such tokens, and what
  is made from it is local-only).
- The share dialog lists the people in the viewer's groups; others only on a typed search.
"""

from __future__ import annotations

import uuid

from helpers.sharing_tokens_hardening import _bearer, _other, _tok

from mailr.db import sessionmaker

# ---------------------------------------------------------------- Revoking a sharee who left


async def test_owner_revokes_a_sharee_who_left_and_add_back_fails(app, client, user):
    """note.unshare, note.leave: the owner's stop on a left sharee sticks; their Add back is a 404."""
    bob = await _other(app)
    nid = (await client.post("/api/v1/notes", json={"kind": "text", "title": "Salary notes", "body": "secret v1"})
           ).json()["note"]["id"]
    assert (await client.put(f"/api/v1/notes/{nid}/shares/{bob.user_id}", json={"level": "edit"})).status_code == 200
    assert (await bob.delete(f"/api/v1/notes/{nid}/shares/me")).status_code == 200
    r = await client.delete(f"/api/v1/notes/{nid}/shares/{bob.user_id}")
    assert r.status_code == 200, r.text
    # The owner's dialog no longer lists Bob as "left": nothing reaches him.
    assert all(x["id"] != bob.user_id for x in r.json()["left"])
    await client.patch(f"/api/v1/notes/{nid}", json={"body": "secret v2 after revoking Bob"})

    r = await bob.post(f"/api/v1/notes/{nid}/shares/me")
    assert r.status_code == 404, r.text
    assert (await bob.get(f"/api/v1/notes/{nid}")).status_code == 404
    assert (await bob.patch(f"/api/v1/notes/{nid}", json={"body": "Bob was here"})).status_code == 404
    # Sharing again from the dialog still works, at the new level.
    assert (await client.put(f"/api/v1/notes/{nid}/shares/{bob.user_id}", json={"level": "view"})).status_code == 200
    got = await bob.get(f"/api/v1/notes/{nid}")
    assert got.status_code == 200 and got.json()["level"] == "view"
    await bob.aclose()


async def test_add_back_still_works_while_the_share_stands(app, client, user):
    """note.leave's undo: without a stop from the owner, Add back restores the old level."""
    bob = await _other(app)
    nid = (await client.post("/api/v1/notes", json={"kind": "text", "title": "Plan"})).json()["note"]["id"]
    assert (await client.put(f"/api/v1/notes/{nid}/shares/{bob.user_id}", json={"level": "edit"})).status_code == 200
    assert (await bob.delete(f"/api/v1/notes/{nid}/shares/me")).status_code == 200
    assert (await bob.post(f"/api/v1/notes/{nid}/shares/me")).status_code == 200
    got = await bob.get(f"/api/v1/notes/{nid}")
    assert got.status_code == 200 and got.json()["level"] == "edit"
    await bob.aclose()


# ---------------------------------------------------------------- Restricted tokens and hidden subjects


async def test_restricted_token_cannot_link_or_read_a_hidden_subject(client, user, raw):
    """note.link: a token that may not see a conversation gets 404 for it, and a link the user made
    shows the placeholder to that token, never the subject."""
    from helpers.agent import _ingest
    from helpers.security_authz import _conn

    uni = await _conn(user.id, "local_only")
    normal = await _conn(user.id)
    _, hidden_cid = await _ingest(user.id, raw("FERPA appeal student 4711", frm="Dean <dean@uni.example>",
                                               body="confidential"), uni)
    for kw in ({}, {"connector_ids": [normal]}):
        nid = (await client.post("/api/v1/notes", json={"kind": "text", "title": "scratch"})).json()["note"]["id"]
        async with _bearer(client, await _tok(client, ["read", "notes"], **kw)) as a:
            r = await a.post(f"/api/v1/notes/{nid}/links",
                             json={"target_kind": "conversation", "target_ref": {"conversation_id": hidden_cid}})
            assert r.status_code == 404, r.text
            assert "FERPA" not in r.text
        # The signed-in user links it; the token then reads the note.
        r = await client.post(f"/api/v1/notes/{nid}/links",
                              json={"target_kind": "conversation", "target_ref": {"conversation_id": hidden_cid}})
        assert r.status_code == 200 and "FERPA appeal student 4711" in r.text
        async with _bearer(client, await _tok(client, ["read", "notes"], **kw)) as a:
            got = await a.get(f"/api/v1/notes/{nid}")
            assert got.status_code == 200
            assert "FERPA" not in got.text
            (ln,) = got.json()["links"]
            assert ln["state"] == "hidden" and ln["title"] == "Not shared with you"
            grid = await a.get("/api/v1/notes")
            assert grid.status_code == 200 and "FERPA" not in grid.text
        # The session still sees its own subject.
        assert "FERPA appeal student 4711" in (await client.get(f"/api/v1/notes/{nid}")).text


async def test_restricted_token_reads_no_hidden_subject_in_a_table_link_cell(client, user, raw):
    """table.get through the agent tool get_table: a link cell to a local-only conversation shows the
    placeholder to a token that is not local-only."""
    from helpers.agent import _ingest
    from helpers.security_authz import _conn

    uni = await _conn(user.id, "local_only")
    _, hidden_cid = await _ingest(user.id, raw("Grant panel decision: declined", frm="Provost <p@uni.example>",
                                               body="confidential"), uni)
    r = await client.post("/api/v1/tables", json={"name": "Follow-ups", "columns": [
        {"name": "Item", "type": "text"}, {"name": "Mail", "type": "link", "config": {"target": "conversation"}}]})
    assert r.status_code == 200, r.text
    t = r.json()["table"]
    c_item, c_mail = t["columns"][0]["id"], t["columns"][1]["id"]
    r = await client.post(f"/api/v1/tables/{t['id']}/rows", json={"cells": {
        c_item: "reply", c_mail: [{"kind": "conversation", "conversation_id": hidden_cid}]}})
    assert r.status_code == 200, r.text
    async with _bearer(client, await _tok(client, ["read", "notes"])) as a:
        g = await a.post("/api/v1/agent/tools/get_table", json={"table": t["id"]})
        assert g.status_code == 200, g.text
        assert "Grant panel decision" not in g.text
    async with _bearer(client, await _tok(client, ["read", "notes"], local_only=True)) as a:
        g = await a.post("/api/v1/agent/tools/get_table", json={"table": t["id"]})
        if g.status_code == 200:  # a local-only token (when the policy allows them) sees it
            assert "Grant panel decision" in g.text or "local" in g.text.lower()


# ---------------------------------------------------------------- Local-only tables in the notes grid


async def test_notes_grid_hides_local_only_tables_from_a_non_local_token(client, user):
    """note.search: GET /notes gives a non-local-only token no local-only table card."""
    r = await client.post("/api/v1/tables", json={"name": "Medical log", "local_only": True,
                                                  "columns": [{"name": "Entry", "type": "text"}]})
    assert r.status_code == 200, r.text
    t = r.json()["table"]
    col = t["columns"][0]["id"]
    r = await client.post(f"/api/v1/tables/{t['id']}/rows", json={"cells": {col: "HIV test result positive"}})
    assert r.status_code == 200, r.text
    r = await client.post("/api/v1/tables", json={"name": "Open log", "columns": [{"name": "Entry", "type": "text"}]})
    open_id = r.json()["table"]["id"]
    async with _bearer(client, await _tok(client, ["notes"])) as a:
        for view in ("all", "tables"):
            g = await a.get("/api/v1/notes", params={"view": view})
            assert g.status_code == 200, g.text
            assert "HIV test result positive" not in g.text and "Medical log" not in g.text
            assert open_id in {x["id"] for x in g.json()["tables"]}
    # The signed-in user still sees both.
    g = await client.get("/api/v1/notes", params={"view": "tables"})
    assert {t["id"], open_id} <= {x["id"] for x in g.json()["tables"]}


# ---------------------------------------------------------------- Note link titles


async def test_note_link_title_is_hidden_from_sharees(app, client, user, raw):
    """note.link, note.share: a sharee sees the placeholder for the owner's conversation."""
    from helpers.agent import _ingest

    _, cid = await _ingest(user.id, raw("Divorce lawyer: settlement draft", frm="Lawyer <l@law.example>", body="x"))
    bob = await _other(app)
    nid = (await client.post("/api/v1/notes", json={"kind": "text", "title": "Groceries"})).json()["note"]["id"]
    r = await client.post(f"/api/v1/notes/{nid}/links",
                          json={"target_kind": "conversation", "target_ref": {"conversation_id": cid}})
    assert r.status_code == 200, r.text
    assert (await client.put(f"/api/v1/notes/{nid}/shares/{bob.user_id}", json={"level": "view"})).status_code == 200
    got = await bob.get(f"/api/v1/notes/{nid}")
    assert got.status_code == 200
    assert "Divorce lawyer" not in got.text
    assert got.json()["links"][0]["state"] == "hidden"
    grid = await bob.get("/api/v1/notes", params={"view": "shared"})
    assert "Divorce lawyer" not in grid.text
    mine = await client.get(f"/api/v1/notes/{nid}")
    assert mine.json()["links"][0]["title"] == "Divorce lawyer: settlement draft"
    await bob.aclose()


# ---------------------------------------------------------------- Templates of local-only lists


async def test_template_of_a_local_only_list_stays_local_only(client, user):
    """note.save_template, note.from_template: the template carries local-only; a non-local token
    neither lists nor uses it; what the user makes from it is local-only."""
    r = await client.post("/api/v1/notes", json={"kind": "checklist", "title": "Meds", "local_only": True,
                                                 "lines": "Sertraline 100mg\nLithium 300mg"})
    assert r.status_code == 200, r.text
    nid = r.json()["note"]["id"]
    r = await client.post(f"/api/v1/notes/{nid}/save-template", json={"name": "Meds template"})
    assert r.status_code == 200, r.text
    tid = r.json()["template"]["id"]
    assert r.json()["template"]["local_only"] is True
    async with _bearer(client, await _tok(client, ["notes"])) as a:
        g = await a.get("/api/v1/notes/templates")
        assert g.status_code == 200
        assert "Meds template" not in g.text and "Lithium" not in g.text
        r = await a.post("/api/v1/notes/from-template", json={"template": tid})
        assert r.status_code in (403, 404), r.text
        assert "Lithium" not in r.text
    g = await client.get("/api/v1/notes/templates")
    assert "Meds template" in g.text
    r = await client.post("/api/v1/notes/from-template", json={"template": tid})
    assert r.status_code == 200, r.text
    assert r.json()["note"]["local_only"] is True


# ---------------------------------------------------------------- The share dialog's member list


async def test_share_dialog_lists_only_group_members_until_a_search(app, client, user):
    """note.share: no directory of every account; a typed search of three or more characters finds
    others, at most eight."""
    from mailr.core.users import create_user

    tag = uuid.uuid4().hex[:6]
    async with sessionmaker()() as db:
        outsider = await create_user(db, f"zed-{tag}@elsewhere.example", f"Zed Hidden{tag}",
                                     password="other-password-123")
        outsider.assigned_groups, outsider.idp_groups = [], None
        await db.commit()
    nid = (await client.post("/api/v1/notes", json={"kind": "text", "title": "x"})).json()["note"]["id"]
    r = await client.get(f"/api/v1/notes/{nid}/shares")
    assert r.status_code == 200
    assert outsider.email not in r.text
    assert r.json()["search_min"] == 3
    # Two letters are not enough.
    r = await client.get(f"/api/v1/notes/{nid}/shares", params={"q": "ze"})
    assert outsider.email not in r.text
    for q in (f"zed-{tag}", f"hidden{tag}", outsider.email):
        r = await client.get(f"/api/v1/notes/{nid}/shares", params={"q": q})
        assert outsider.email in r.text, q
        assert outsider.id not in r.json()["household"]
    r = await client.get(f"/api/v1/notes/{nid}/shares", params={"q": "example"})
    assert len([c for c in r.json()["candidates"] if c["id"] not in r.json()["household"]]) <= 8
