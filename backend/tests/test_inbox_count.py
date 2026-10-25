# ACT-SURFACES: api assistant
"""settings.inbox_count: the sidebar's Inbox count (GET /inbox/count, the `inbox_count`
setting), the unread mail the active layout's first tab lists ("first_tab", the default) or
every unread conversation in the Inbox ("all"). The first tab's own unread indicator is the
same number."""

import pytest
from helpers.core_flow import _ingest
from sqlalchemy import update

from mailr.db import sessionmaker
from mailr.models import Conversation


async def _count(client, mode=None):
    if mode is not None:
        r = await client.patch("/api/v1/me/settings", json={"inbox_count": mode})
        assert r.status_code == 200, r.text
    r = await client.get("/api/v1/inbox/count")
    assert r.status_code == 200, r.text
    return r.json()


async def _category(ids, cat):
    async with sessionmaker()() as db:
        await db.execute(update(Conversation).where(Conversation.id.in_(ids)).values(category=cat))
        await db.commit()


async def test_owner_layout_unread_only_in_claimed_tabs(client, user, raw):
    """A common shape: Inbox (Important, Unread, Everything else), then claiming label tabs
    and a claiming Promotions category tab. Unread mail only in those tabs counts 0 in
    "first_tab" and N in "all"."""
    fin = (await client.post("/api/v1/labels", json={"name": "Financial"})).json()["id"]
    gh = (await client.post("/api/v1/labels", json={"name": "Github"})).json()["id"]
    later = (await client.post("/api/v1/labels", json={"name": "Later"})).json()["id"]
    _, bank = await _ingest(user, raw("Statement ready", frm="Bank <bank@example.org>", msgid="<ic1@x.org>"))
    _, issue = await _ingest(user, raw("[mailr] Issue", frm="GH <notifications@github.com>", msgid="<ic2@x.org>"))
    _, sale = await _ingest(user, raw("Weekend sale", frm="Store <news@store.example>", msgid="<ic3@x.org>"))
    await client.post("/api/v1/conversations/actions", json={"ids": [bank], "action": "add_labels", "label_ids": [fin]})
    await client.post("/api/v1/conversations/actions", json={"ids": [issue], "action": "add_labels", "label_ids": [gh]})
    await _category([bank, issue], "primary")
    await _category([sale], "promotions")
    lay = {"name": "Mine", "tabs": [
        {"name": "Inbox", "icon": "inbox", "badge": "unread", "sections": [
            {"title": "Important", "base": "inbox", "filter": "important", "limit": 10},
            {"title": "Unread", "base": "inbox", "filter": "unread", "limit": 10, "exclude_above": True},
            {"title": "Everything else", "base": "inbox", "exclude_above": True}]},
        {"name": "Financial", "icon": "tag", "badge": "unread", "claims": True, "sections": [{"title": "All", "base": f"label:{fin}"}]},
        {"name": "Later", "icon": "clock", "badge": "unread", "sections": [{"title": "All", "base": f"label:{later}"}]},
        {"name": "Github", "icon": "tag", "badge": "unread", "claims": True, "sections": [{"title": "All", "base": f"label:{gh}"}]},
        {"name": "Promotions", "icon": "tag", "badge": "unread", "claims": True,
         "sections": [{"title": "All", "base": "inbox", "category": "promotions"}]},
    ]}
    r = await client.put("/api/v1/layouts", json={"layouts": [lay], "active": "mine"})
    assert r.status_code == 200, r.text

    first = await _count(client)
    assert first == {"count": 0, "mode": "first_tab", "layout": "mine", "tab": "inbox"}
    tabs = (await client.get("/api/v1/layouts/mine/counts", params={"detail": True})).json()["tabs"]
    assert tabs["inbox"]["unread"] == 0
    assert tabs["financial"]["unread"] == 1 and tabs["github"]["unread"] == 1 and tabs["promotions"]["unread"] == 1
    assert (await _count(client, "all"))["count"] == 3

    # Plain mail lands in the first tab; both modes count it, and the tab indicator agrees.
    _, plain = await _ingest(user, raw("Lunch?", frm="Bob <bob@example.org>", msgid="<ic4@x.org>"))
    await _category([plain], "primary")
    assert (await _count(client, "all"))["count"] == 4
    assert (await _count(client, "first_tab"))["count"] == 1
    assert (await client.get("/api/v1/layouts/mine/counts")).json()["inbox"] == 1
    await client.post("/api/v1/conversations/actions", json={"ids": [plain], "action": "read"})
    assert (await _count(client))["count"] == 0


async def test_first_tab_section_with_a_state_filter(client, user, raw):
    """A first tab that lists only starred inbox mail counts only starred unread mail."""
    _, a = await _ingest(user, raw("Starred one", msgid="<ic5@x.org>"))
    _, b = await _ingest(user, raw("Plain one", frm="Carol <carol@example.org>", msgid="<ic6@x.org>"))
    await client.post("/api/v1/conversations/actions", json={"ids": [a], "action": "star"})
    lay = {"name": "Stars", "tabs": [
        {"name": "Starred", "icon": "star", "badge": "unread", "sections": [{"title": "Starred", "base": "inbox", "filter": "starred"}]},
        {"name": "Rest", "icon": "inbox", "badge": "unread", "sections": [{"title": "All", "base": "inbox"}]},
    ]}
    assert (await client.put("/api/v1/layouts", json={"layouts": [lay], "active": "stars"})).status_code == 200
    got = await _count(client)
    assert got["count"] == 1 and got["tab"] == "starred"
    assert (await _count(client, "all"))["count"] == 2


async def test_layout_without_tabs_counts_the_whole_inbox(client, user, raw):
    """A built-in layout with one tab (no tab bar): the whole inbox list, less snoozed mail."""
    await _ingest(user, raw("One", msgid="<ic7@x.org>"))
    await _ingest(user, raw("Two", frm="Dan <dan@example.org>", msgid="<ic8@x.org>"))
    r = await client.patch("/api/v1/me/settings", json={"inbox_type": "important_first"})
    assert r.status_code == 200, r.text
    got = await _count(client)
    assert got == {"count": 2, "mode": "first_tab", "layout": "important_first", "tab": "inbox"}
    assert (await _count(client, "all"))["count"] == 2


async def test_default_layout_counts_primary(client, user, raw):
    """The Default layout with category tabs: the count is Primary's unread mail."""
    _, p1 = await _ingest(user, raw("Hi", msgid="<ic9@x.org>"))
    _, p2 = await _ingest(user, raw("Uncategorised", frm="Eve <eve@example.org>", msgid="<ic10@x.org>"))
    _, promo = await _ingest(user, raw("Sale", frm="Shop <shop@example.org>", msgid="<ic11@x.org>"))
    _, soc = await _ingest(user, raw("New follower", frm="Social <s@example.org>", msgid="<ic12@x.org>"))
    await _category([p1], "primary")
    await _category([p2], None)
    await _category([promo], "promotions")
    await _category([soc], "social")
    got = await _count(client)
    assert got == {"count": 2, "mode": "first_tab", "layout": "default", "tab": "primary"}
    primary = (await client.get("/api/v1/conversations", params={"layout": "default", "tab": "primary", "sec": "primary"})).json()
    assert sum(1 for c in primary["items"] if c["unread_count"] > 0) == 2
    assert (await _count(client, "all"))["count"] == 4


def test_the_assistant_can_change_the_setting():
    from mailr.agent.config_tools import validate_settings

    assert validate_settings({"inbox_count": "all"}, set()) == {"inbox_count": "all"}
    with pytest.raises(ValueError):
        validate_settings({"inbox_count": "everything"}, set())


async def test_the_setting_defaults_to_first_tab_and_takes_only_its_two_values(client, user):
    me = (await client.get("/api/v1/me")).json()
    assert me["settings"]["inbox_count"] == "first_tab"
    for bad in ("everything", "", None, 1, ["all"]):
        r = await client.patch("/api/v1/me/settings", json={"inbox_count": bad})
        assert r.status_code == 400, (bad, r.text)
    assert (await client.get("/api/v1/me")).json()["settings"]["inbox_count"] == "first_tab"
    r = await client.patch("/api/v1/me/settings", json={"inbox_count": "all"})
    assert r.status_code == 200 and r.json()["inbox_count"] == "all"
    r = await client.patch("/api/v1/me/settings", json={"inbox_count": "first_tab"})
    assert r.status_code == 200 and r.json()["inbox_count"] == "first_tab"
