# ACT-SURFACES: api assistant
"""People: the strength ranking, contact groups (group.create, group.rename,
group.add_members, group.remove_members, group.delete, group.list), group expansion for the
assistant (group.list, person.find) with the send gate still asking for new recipients, and
suggested groups from sent mail (group.suggest, group.accept_suggestion, group.dismiss_suggestion)."""

import uuid
from datetime import timedelta

from helpers.agent import _setup_identity, _token_client
from helpers.people_groups import NOW, _msg
from sqlalchemy import select, text

from mailr.contacts import suggest
from mailr.core import people
from mailr.db import sessionmaker
from mailr.models import Person, PersonGroup


async def _rank(client, q=""):
    r = await client.get("/api/v1/people", params={"q": q, "limit": 50})
    assert r.status_code == 200, r.text
    return [x["email"] for x in r.json()]


async def test_strength_ranks_recent_two_way_correspondents_first(client, user):
    """person.find: two-way and recent beat one-way and old; a mass mail adds little to each recipient."""
    old = NOW - timedelta(days=700)
    # Old but frequent, one-way (I only ever wrote to them).
    for _ in range(6):
        await _msg(user.id, sent=True, to=["old@x.example"], when=old)
    # Recent two-way.
    await _msg(user.id, sent=False, frm="pal@x.example", when=NOW - timedelta(days=3))
    await _msg(user.id, sent=True, to=["pal@x.example"], when=NOW - timedelta(days=2))
    # Recent one-way inbound newsletter-ish sender (no list id) and a mailing list.
    await _msg(user.id, sent=False, frm="vendor@x.example", when=NOW - timedelta(days=1))
    for _ in range(5):
        await _msg(user.id, sent=False, frm="list@x.example", when=NOW, list_id="news.x.example")
    # A roster mail to 30 people.
    roster = [f"s{i}@x.example" for i in range(30)]
    await _msg(user.id, sent=True, to=roster, when=NOW)
    order = await _rank(client, "x.example")
    assert order[0] == "pal@x.example", order
    assert order.index("pal@x.example") < order.index("old@x.example")
    assert order.index("vendor@x.example") < order.index("list@x.example")  # list mail does not count
    body = (await client.get("/api/v1/people", params={"q": "pal@"})).json()[0]
    assert body["two_way"] is True and body["strength"] > 0

    # The rescore from mail gives the same numbers as ingest's increments.
    async with sessionmaker()() as db:
        before = dict((await db.execute(select(Person.email, Person.strength).where(Person.user_id == user.id))).all())
        await db.execute(text("UPDATE people SET strength=0 WHERE user_id=:u"), {"u": user.id})
        await people.rescore(db, user.id)
        await db.commit()
        after = dict((await db.execute(select(Person.email, Person.strength).where(Person.user_id == user.id))).all())
    for e, v in before.items():
        assert abs(after[e] - v) <= 1e-6 * max(1.0, v), (e, v, after[e])


async def test_a_reply_counts_more_than_a_plain_message(client, user):
    await _msg(user.id, sent=False, frm="asker@y.example", msgid="<q1@y>", when=NOW - timedelta(days=1))
    await _msg(user.id, sent=False, frm="other@y.example", msgid="<q2@y>", when=NOW - timedelta(days=1))
    await _msg(user.id, sent=True, to=["asker@y.example"], irt="<q1@y>", when=NOW)
    await _msg(user.id, sent=True, to=["other@y.example"], when=NOW)
    async with sessionmaker()() as db:
        s = dict((await db.execute(select(Person.email, Person.strength).where(Person.user_id == user.id))).all())
    assert s["asker@y.example"] > s["other@y.example"]


async def test_group_crud(client, user):
    """group.create, group.rename, group.add_members, group.remove_members, group.delete"""
    r = await client.post("/api/v1/groups", json={"name": "Committee", "colour": "#1a73e8",
                                                 "members": ["a@c.example", {"email": "B@c.example", "name": "Bea"}]})
    assert r.status_code == 200, r.text
    g = r.json()
    assert g["name"] == "Committee" and g["source"] == "manual" and g["count"] == 2
    assert [m["email"] for m in g["members"]] == ["a@c.example", "b@c.example"]
    gid = g["id"]
    r = await client.patch(f"/api/v1/groups/{gid}", json={"name": "Search committee", "add": ["c@c.example"],
                                                        "remove": ["a@c.example"]})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Search committee" and [m["email"] for m in r.json()["members"]] == ["b@c.example", "c@c.example"]
    assert (await client.post("/api/v1/groups", json={"name": "Bad", "members": ["not-an-address"]})).status_code == 400
    assert (await client.patch(f"/api/v1/groups/{gid}", json={"colour": "red"})).status_code == 400
    listed = (await client.get("/api/v1/groups", params={"q": "search"})).json()
    assert [x["id"] for x in listed] == [gid]
    # The person card names the groups a person is in.
    await _msg(user.id, sent=False, frm="b@c.example")
    card = (await client.get("/api/v1/people/b@c.example")).json()
    assert [x["name"] for x in card["groups"]] == ["Search committee"]
    assert (await client.delete(f"/api/v1/groups/{gid}")).status_code == 200
    assert (await client.get(f"/api/v1/groups/{gid}")).status_code == 404


async def test_token_group_change_asks_and_another_user_cannot_see_it(client, user, app):
    from httpx import ASGITransport, AsyncClient

    from mailr.core.users import create_user

    gid = (await client.post("/api/v1/groups", json={"name": "Family", "members": ["mum@f.example"]})).json()["id"]
    tok = (await client.post("/api/v1/tokens", json={"name": "agent", "scopes": ["read", "label"]})).json()["token"]
    async with _token_client(client, tok) as a:
        r = await a.patch(f"/api/v1/groups/{gid}", json={"add": ["dad@f.example"]})
        assert r.status_code == 202, r.text
        assert r.json()["approval"]["action"] == "contacts.write"
    assert [m["email"] for m in (await client.get(f"/api/v1/groups/{gid}")).json()["members"]] == ["mum@f.example"]
    async with sessionmaker()() as db:
        other = await create_user(db, f"o-{uuid.uuid4().hex[:6]}@example.com", "Other", password="test-password-123")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c2:
        r = await c2.post("/api/v1/auth/login", json={"email": other.email, "password": "test-password-123"})
        c2.headers["X-CSRF-Token"] = r.json()["csrf"]
        assert (await c2.get(f"/api/v1/groups/{gid}")).status_code == 404
        assert (await c2.delete(f"/api/v1/groups/{gid}")).status_code == 404
        assert (await c2.get("/api/v1/groups")).json() == []


async def test_group_in_recipients_expands_and_new_members_still_ask(client, user):
    """group.list / group.expand for the assistant: "group:<name>" becomes the members; the send gate
    asks because one member was never written to (the first-message hard rule)."""
    _, me = await _setup_identity(user)
    await _msg(user.id, sent=True, to=["known@g.example"], when=NOW - timedelta(days=5))
    await _msg(user.id, sent=False, frm="known@g.example", when=NOW - timedelta(days=4))
    await client.post("/api/v1/groups", json={"name": "Reading group", "members": ["known@g.example", "new@g.example"]})
    tok = (await client.post("/api/v1/tokens", json={"name": "w", "scopes": ["read", "draft", "send"]})).json()["token"]
    async with _token_client(client, tok) as a:
        r = await a.post("/api/v1/agent/tools/list_groups", json={})
        assert r.status_code == 200, r.text
        [g] = r.json()["result"]["items"]
        assert g["name"]["content"] == "Reading group" and {m["email"] for m in g["members"]} == {"known@g.example", "new@g.example"}
        # A token's new message to people outside a conversation asks; a rule allowing drafts lets it through.
        r = await a.post("/api/v1/agent/tools/create_draft",
                         json={"to": ["group:reading group"], "subject": "Next meeting", "body": "Thursday?"})
        assert r.status_code == 202, r.text
        await client.post("/api/v1/permissions", json={"subject": "*", "action": "mail.draft", "effect": "allow"})
        r = await a.post("/api/v1/agent/tools/create_draft",
                         json={"to": ["group:reading group"], "subject": "Next meeting", "body": "Thursday?"})
        assert r.status_code == 200, r.text
        res = r.json()["result"]
        assert sorted(res["to"]) == ["known@g.example", "new@g.example"]
        r = await a.post("/api/v1/agent/tools/send_draft", json={"draft_id": res["draft_id"]})
        assert r.status_code == 202, r.text
        appr = r.json()["approval"]
        assert appr["action"] == "mail.send"
        r = await a.post("/api/v1/agent/tools/create_draft", json={"to": ["group:nobody"], "subject": "x", "body": "y"})
        assert r.status_code in (400, 422) and "no group" in r.text
        r = await a.post("/api/v1/agent/tools/find_people", json={"query": "g.example"})
        assert r.status_code == 200, r.text
        assert [x["email"] for x in r.json()["result"]["items"]][0] == "known@g.example"


def _sends(sets: list[tuple[list[str], str, int]]):
    out = []
    for members, subject, n in sets:
        for i in range(n):
            out.append((frozenset(members), subject, NOW - timedelta(days=i)))
    return out


def test_clustering_finds_recurring_sets():
    """group.suggest: a roster with absentees, a committee, a family; one-off sends are ignored."""
    roster = [f"st{i}@springfield.edu" for i in range(12)]
    committee = ["ann@c.example", "bob@c.example", "cy@d.example"]
    sends = _sends([(roster, "CHEM 101 week 1", 3), (roster[:-1], "CHEM 101 project", 2), (roster[1:], "CHEM 101 grades", 1),
                    (committee, "Search committee", 4), (committee + ["guest@e.example"], "Search committee: candidates", 1),
                    (["mum@gmail.com", "sis@gmail.com"], "Sunday lunch", 3),
                    (["one@x.example", "two@x.example"], "Once", 1),
                    (["solo@x.example"], "Solo", 9)])
    cl = suggest.cluster(sends)
    cores = [set(c.core()) for c in cl]
    assert set(roster) in cores
    assert set(committee) in cores
    assert {"mum@gmail.com", "sis@gmail.com"} in cores
    assert all(len(c) >= 2 for c in cores) and len(cores) == 3
    by = {frozenset(c.core()): c for c in cl}
    assert by[frozenset(roster)].n == 6
    assert suggest.name_hint(frozenset(roster), by[frozenset(roster)].subjects, {}) == "CHEM 101"
    assert suggest.name_hint(frozenset(["mum@gmail.com", "sis@gmail.com"]), ["Sunday lunch"] * 3,
                             {"mum@gmail.com": "Ruth Khan", "sis@gmail.com": "Sara Khan"}) == "Khan family"
    assert suggest.name_hint(frozenset(roster[:3]), ["x", "y", "z"], {}) == "Springfield"


async def test_suggestions_accept_and_dismiss_persist(client, user):
    """group.suggest, group.accept_suggestion, group.dismiss_suggestion: a dismissed set never returns;
    an accepted one is an ordinary group and is not suggested again."""
    _, me = await _setup_identity(user)
    team = ["t1@team.example", "t2@team.example", "t3@team.example"]
    fam = ["mum@fam.example", "dad@fam.example"]
    for i in range(4):
        await _msg(user.id, sent=True, to=team + [me], subject="Team sync", when=NOW - timedelta(days=i))
        await _msg(user.id, sent=True, to=fam, subject="Dinner", when=NOW - timedelta(days=i))
    r = await client.post("/api/v1/groups/suggestions/refresh")
    assert r.status_code == 200, r.text
    sug = {frozenset(m["email"] for m in g["members"]): g for g in r.json()["suggestions"]}
    assert set(sug) == {frozenset(team), frozenset(fam)}  # my own address is never a member
    t, f = sug[frozenset(team)], sug[frozenset(fam)]
    assert t["state"] == "suggested" and t["evidence"]["messages"] == 4
    assert (await client.get("/api/v1/groups")).json() == []  # suggestions are not groups yet
    r = await client.post(f"/api/v1/groups/{t['id']}/accept", json={"name": "Team"})
    assert r.status_code == 200 and r.json()["state"] == "active" and r.json()["name"] == "Team"
    assert (await client.post(f"/api/v1/groups/{f['id']}/dismiss")).status_code == 200
    for i in range(3):  # more of the same mail: the job runs again
        await _msg(user.id, sent=True, to=fam, subject="Dinner", when=NOW - timedelta(hours=i))
    r = await client.post("/api/v1/groups/suggestions/refresh")
    assert r.json()["suggestions"] == [] and r.json()["skipped"] == 2
    async with sessionmaker()() as db:
        states = sorted((await db.execute(select(PersonGroup.state).where(PersonGroup.user_id == user.id))).scalars())
    assert states == ["active", "dismissed"]
    # Tokens neither see nor decide suggestions.
    tok = (await client.post("/api/v1/tokens", json={"name": "r", "scopes": ["read"]})).json()["token"]
    async with _token_client(client, tok) as a:
        assert (await a.get("/api/v1/groups", params={"state": "suggested"})).json() == []
        assert (await a.post(f"/api/v1/groups/{f['id']}/dismiss")).status_code == 403


async def test_places_for_the_location_field(client, user):
    """event.suggest_location: guests' addresses first, then strong correspondents'; q narrows."""
    await _msg(user.id, sent=True, to=["near@p.example"])
    await _msg(user.id, sent=False, frm="near@p.example")
    await _msg(user.id, sent=False, frm="guest@p.example", when=NOW - timedelta(days=300))
    async with sessionmaker()() as db:
        for e, addr in (("near@p.example", "1 Elm St, Anytown, ST 00001"), ("guest@p.example", "9 Oak Ave, Otherville, ST 00002")):
            x = (await db.execute(select(Person).where(Person.user_id == user.id, Person.email == e))).scalar_one()
            x.addresses = [{"text": addr, "source": "signature"}]
        await db.commit()
    r = (await client.get("/api/v1/contacts/places", params={"emails": "guest@p.example"})).json()
    assert [x["text"] for x in r] == ["9 Oak Ave, Otherville, ST 00002", "1 Elm St, Anytown, ST 00001"] and r[0]["guest"]
    r = (await client.get("/api/v1/contacts/places", params={"q": "elm"})).json()
    assert [x["email"] for x in r] == ["near@p.example"]


async def test_a_person_ambrose_knew_before_any_mail_is_listed_once(client, user):
    """person.find: a person Ambrose learnt about before any mail (an entry with an address but no
    contact link) is one row once mail with them arrives: the contact's row, not a second "No mail"
    row with the same address (the docs shot people-card found Dana twice, 7 Oct 2026)."""
    from mailr.models import Entity

    async with sessionmaker()() as db:
        db.add(Entity(user_id=user.id, kind="person", name="Dana Whitfield", emails=["dana@lumen.example"],
                      summary="Marketing Director at Lumen Coffee"))
        db.add(Entity(user_id=user.id, kind="person", name="Theo Martin", emails=["theo@nomail.example"]))
        await db.commit()
    # Mail with Dana arrives after the entry was saved, so the entry has no person_id.
    await _msg(user.id, sent=False, frm="dana@lumen.example", when=NOW - timedelta(days=1))
    rows = (await client.get("/api/v1/people", params={"q": "", "limit": 50})).json()
    dana = [r for r in rows if r["name"] == "Dana Whitfield" or r["email"] == "dana@lumen.example"]
    assert len(dana) == 1 and dana[0]["email"] == "dana@lumen.example", dana
    # Someone with no mail at all still shows as a "No mail" row.
    theo = [r for r in rows if r["name"] == "Theo Martin"]
    assert len(theo) == 1 and theo[0]["no_mail"] is True, theo
