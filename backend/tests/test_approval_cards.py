# ACT-SURFACES: api
"""The approval card's head (agent/present.py card_head): the kind of request for its type label
and group header, the object's own name for its heading, and the short line of why it asks. And
"Allow all" / "Deny all" of a group (approval.approve_many): each card decided once, with the same
checks as its own buttons, and a result per id."""

from types import SimpleNamespace

import pytest
from httpx import AsyncClient

from mailr import i18n
from mailr.agent import present
from mailr.agent.agentwrite import TAINTED


def _pa(action, payload=None, token=None):
    return SimpleNamespace(action=action, payload=payload or {}, token_id=token)


def test_head_of_a_task_from_a_token():
    long = "Research workshop: dry run in Woolsey with Michael and Shane (check projector, outlets and the HDMI cable)"
    pv = {"title": f"Add task “{long[:80]}”", "kind": "task", "reason": TAINTED,
          "task": {"summary": long, "due": "2026-11-30T09:00", "list_name": "Calendar"},
          "display": {"title": f"Add task “{long[:80]}”", "title_msg": {"key": "approvals.task.add"}}}
    h = present.card_head(_pa("tasks.create", token=4), pv)
    assert h["kind"] == "task_new" and h["kind_label"] == "New task"
    assert h["kind_label_msg"]["key"] == "approvals.kind.task_new"
    # The whole title, never the card title's 80 characters.
    assert h["name"] == long
    # A token's text: the short line says so; the whole reason stays on the display.
    assert h["reason_short"] == "Asks first: written through an API token"
    h = present.card_head(_pa("tasks.create"), pv)
    assert h["reason_short"] == "Asks first: written after reading email"


@pytest.mark.parametrize(("action", "payload", "pv", "kind", "name"), [
    ("mail.send", {}, {"email": {"subject": "Workshop room", "to": [{"email": "a@x.example"}]}}, "send", "Workshop room"),
    ("mail.send", {}, {"email": {"subject": "", "to": [{"email": "a@x.example"}]}}, "send", None),
    ("mail.draft", {"draft_id": 3}, {"email": {"subject": "Re: plan"}}, "draft_change", "Re: plan"),
    ("mail.draft", {}, {"email": {"subject": "Plan"}}, "draft_new", "Plan"),
    ("calendar.create", {}, {"event": {"summary": "Dry run"}}, "event_new", "Dry run"),
    ("calendar.update", {}, {"event": {"start": "x"}, "before": {"summary": "Weekly sync"}}, "event_change", "Weekly sync"),
    ("calendar.delete", {}, {"title": "Delete “Weekly sync”"}, "event_delete", "Weekly sync"),
    ("labels.write", {"op": "label.create"}, {"title": "Create label “Travel”"}, "label_new", "Travel"),
    ("labels.write", {"op": "label.update"}, {"title": "Change label “Travel”"}, "label_change", "Travel"),
    ("labels.delete", {"op": "label.delete"}, {"title": "Delete label “Travel”"}, "label_delete", "Travel"),
    ("mail.trash", {"action": "trash", "message_ids": [1]}, {"title": "Move 1 message to Trash"}, "mail_trash", None),
    ("mail.label", {"action": "archive", "message_ids": [1]}, {}, "mail_organise", None),
    ("rules.write", {"op": "review"}, {"kind": "review"}, "review", None),
    ("mail.trash", {"op": "cleanup"}, {}, "cleanup", None),
    ("notes.create", {}, {"title": "Make a note “Packing”"}, "note_new", "Packing"),
    ("web.search", {}, {"title": "Do “a” and “b”"}, "request", None),
])
def test_kind_and_name(action, payload, pv, kind, name):
    h = present.card_head(_pa(action, payload), pv)
    assert h["kind"] == kind and h["kind"] in present.KINDS
    assert h["name"] == name
    assert h["reason_short"] is None and h["reason_short_msg"] is None


def test_every_kind_and_short_reason_has_words():
    """Each kind has a type label and a group header with plural forms; each short reason a text."""
    cat = i18n.catalogue("en")
    for k in present.KINDS:
        assert f"approvals.kind.{k}" in cat, k
        assert f"approvals.group.{k}_one" in cat and f"approvals.group.{k}_other" in cat, k
        assert "{{who}}" in cat[f"approvals.group.{k}_other"] and "{{count}}" in cat[f"approvals.group.{k}_other"]
    for _, rid in present._REASON_SHORT:
        assert f"approvals.reason_short.{rid}" in cat, rid
    assert present.reason_short("hard rule: something new", token=False)["key"] == "approvals.reason_short.other"
    assert present.reason_short("hard rule: first message to a new address", token=True)["key"] \
        == "approvals.reason_short.new_address"


async def _token(client, scopes, name="claude-code"):
    r = await client.post("/api/v1/tokens", json={"name": name, "scopes": scopes})
    assert r.status_code == 200, r.text
    a = AsyncClient(transport=client._transport, base_url="http://test")
    a.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return a


async def _create_label(agent, name):
    r = await agent.post("/api/v1/labels", json={"name": name})
    assert r.status_code == 202, r.text
    return r.json()["approval"]["id"]


async def test_cards_carry_their_head(client, user):
    agent = await _token(client, ["read", "label"])
    aid = await _create_label(agent, "Receipts")
    card = (await client.get(f"/api/v1/approvals/{aid}")).json()
    assert card["head"]["kind"] == "label_new"
    assert card["head"]["kind_label"] == "New label"
    assert card["head"]["name"] == "Receipts"
    listed = (await client.get("/api/v1/approvals")).json()
    assert [c["head"]["kind"] for c in listed if c["id"] == aid] == ["label_new"]
    await agent.aclose()


async def test_deny_all_decides_each_listed_request_once(client, user):
    agent = await _token(client, ["read", "label"])
    ids = [await _create_label(agent, n) for n in ("Receipts", "Travel")]
    other = await _create_label(agent, "School")
    r = await client.post("/api/v1/approvals/decide-many", json={"ids": ids, "decision": "deny"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 2 and body["skipped"] == []
    assert [(c["id"], c["status"], c["decided_by"]) for c in body["decided"]] == [(i, "denied", "user") for i in ids]
    # Only the listed requests: the third one still waits.
    assert (await client.get(f"/api/v1/approvals/{other}")).json()["status"] == "pending"
    await agent.aclose()


async def test_batch_reports_each_id(client, user):
    """A request decided elsewhere and an id that names nothing are reported; the rest are decided."""
    agent = await _token(client, ["read", "label"])
    ids = [await _create_label(agent, n) for n in ("One", "Two", "Three")]
    assert (await client.post(f"/api/v1/approvals/{ids[1]}/decide", json={"decision": "approve"})).status_code == 200
    r = await client.post("/api/v1/approvals/decide-many", json={"ids": [*ids, 999_999_999], "decision": "deny"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [c["id"] for c in body["decided"]] == [ids[0], ids[2]]
    assert {"id": ids[1], "status": "approved"} in body["skipped"]
    assert any(s["status"] == "not_found" for s in body["skipped"])
    assert body["count"] == 2
    await agent.aclose()


async def test_another_users_request_is_not_found(client, user, admin_client):
    """Another account's request is never decided through the batch, and is reported as not found."""
    agent = await _token(client, ["read", "label"])
    mine = await _create_label(agent, "Mine")
    r = await admin_client.post("/api/v1/approvals/decide-many", json={"ids": [mine], "decision": "approve"})
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 0 and r.json()["skipped"][0]["status"] == "not_found"
    assert (await client.get(f"/api/v1/approvals/{mine}")).json()["status"] == "pending"
    await agent.aclose()


def test_people_outside_the_conversation_stay_on_the_short_line():
    """After reading mail, who was not in that conversation is safety information: the short line
    names them instead of hiding them behind ⓘ."""
    raw = "hard rule: an agent that read email (or an API token) writes to people outside the conversation"
    one = present.reason_short(raw, False, {"outsiders": ["Marco Silva"]})
    assert i18n.render(one) == "Asks first: Marco Silva was not in that conversation"
    two = present.reason_short(raw, False, {"outsiders": ["Marco Silva", "Dana", "Lee"]})
    assert i18n.render(two) == "Asks first: Marco Silva, Dana and Lee were not in that conversation"
    bcc = present.reason_short(raw, False, {"outsiders": [], "email": {"bcc": [{"email": "x@y.example"}]}})
    assert bcc["key"] == "approvals.reason_short.bcc"


async def test_a_long_token_name_still_asks(client, user):
    """A token with a long name (the audit log keeps 50 characters of "token:<name>") still gets its
    request onto a card, under its whole name."""
    name = "research-assistant-agent-for-the-workshop-team-and-more"
    agent = await _token(client, ["read", "label"], name=name)
    aid = await _create_label(agent, "Long name")
    card = (await client.get(f"/api/v1/approvals/{aid}")).json()
    assert card["actor"] == name
    await agent.aclose()
