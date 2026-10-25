# ACT-SURFACES: api assistant
"""Show in tab (ACT mail.show_in_tab, core/tabplace.py): the menu lists the active layout's tabs with
one current tab, computed with the lists' own rules (claims, leftmost claiming tab wins), and picking
a tab makes the conversation show there in one action with an Undo. The assistant's show_in_tab
tool (assistant) names the tab by its name."""

from helpers.core_flow import _ingest
from sqlalchemy import select, update

from mailr.core import layouts, tabplace
from mailr.db import sessionmaker
from mailr.models import Conversation, LabelDecision, Message


async def _label(client, name):
    r = await client.post("/api/v1/labels", json={"name": name})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _set_cat(cids, cat):
    async with sessionmaker()() as db:
        await db.execute(update(Conversation).where(Conversation.id.in_(cids)).values(category=cat))
        await db.execute(update(Message).where(Message.conversation_id.in_(cids)).values(category=cat))
        await db.commit()


async def _owner_layout(client):
    """A common shape: Inbox first, four claiming label tabs, then a claiming Promotions tab."""
    ids = {n: await _label(client, n) for n in ("Financial", "Later", "Github", "Shopping")}

    def label_tab(n):
        return {"name": n, "icon": "tag", "claims": True, "sections": [{"title": n, "base": f"label:{ids[n]}"}]}

    lay = {"name": "Mine", "tabs": [
        {"name": "Inbox", "icon": "inbox", "sections": [{"title": "Inbox", "base": "inbox"}]},
        *[label_tab(n) for n in ("Financial", "Later", "Github", "Shopping")],
        {"name": "Promotions", "icon": "tag", "claims": True,
         "sections": [{"title": "Promotions", "base": "inbox", "category": "promotions"}]},
    ]}
    r = await client.put("/api/v1/layouts", json={"layouts": [lay], "active": "mine"})
    assert r.status_code == 200, r.text
    return ids


async def _choices(client, *cids):
    r = await client.get("/api/v1/layouts/show-in-tab", params={"conversation_ids": ",".join(map(str, cids))})
    assert r.status_code == 200, r.text
    return r.json()


async def _show(client, cids, tab):
    r = await client.post("/api/v1/conversations/actions", json={"ids": cids, "action": "show_in_tab", "tab": tab})
    assert r.status_code == 200, r.text
    return r.json()


async def _labels(cid):
    async with sessionmaker()() as db:
        return (await db.get(Conversation, cid)).label_ids or []


async def _cat(cid):
    async with sessionmaker()() as db:
        return (await db.get(Conversation, cid)).category


async def test_current_tab_in_the_owner_layout(client, user, raw):
    ids = await _owner_layout(client)
    _, plain = await _ingest(user, raw("Lunch?", msgid="<s1@x.org>"))
    _, promo = await _ingest(user, raw("Weekend sale", msgid="<s2@x.org>"))
    _, shop = await _ingest(user, raw("30% off jeans", msgid="<s3@x.org>"))
    _, two = await _ingest(user, raw("Read later: release notes", msgid="<s4@x.org>"))
    await _set_cat([plain, two], "primary")
    await _set_cat([promo, shop], "promotions")
    await client.post("/api/v1/conversations/actions", json={"ids": [shop], "action": "add_labels", "label_ids": [ids["Shopping"]]})
    await client.post("/api/v1/conversations/actions", json={"ids": [two], "action": "add_labels",
                                                             "label_ids": [ids["Later"], ids["Github"]]})

    got = await _choices(client, plain)
    assert [t["name"] for t in got["tabs"]] == ["Inbox", "Financial", "Later", "Github", "Shopping", "Promotions"]
    assert [t["kind"] for t in got["tabs"]] == ["first", "label", "label", "label", "label", "category"]
    assert got["tabs"][5]["category"] == "promotions" and got["tabs"][1]["label_id"] == ids["Financial"]
    assert got["current"] == "inbox"
    assert (await _choices(client, promo))["current"] == "promotions"
    # A promotion with the Shopping label: Shopping claims it first (one tab, not two checkmarks).
    assert (await _choices(client, shop))["current"] == "shopping"
    # Two claiming labels: the leftmost claiming tab wins.
    assert (await _choices(client, two))["current"] == "later"
    # Several conversations in different tabs: no single current tab.
    both = await _choices(client, plain, promo)
    assert both["current"] is None
    assert {x["tab"] for x in both["placements"]} == {"inbox", "promotions"}
    assert (await _choices(client, plain, plain))["current"] == "inbox"


async def test_label_tab_adds_its_label_and_drops_the_ones_that_win(client, user, raw):
    ids = await _owner_layout(client)
    _, two = await _ingest(user, raw("Release notes", msgid="<l1@x.org>"))
    await client.post("/api/v1/conversations/actions", json={"ids": [two], "action": "add_labels",
                                                             "label_ids": [ids["Later"], ids["Github"]]})
    assert (await _choices(client, two))["current"] == "later"
    res = await _show(client, [two], "github")
    labels = await _labels(two)
    assert ids["Github"] in labels and ids["Later"] not in labels
    assert (await _choices(client, two))["current"] == "github"
    assert res["undo"]["action"] == "restore_tab"

    _, plain = await _ingest(user, raw("Invoice 42", msgid="<l2@x.org>"))
    await _show(client, [plain], "financial")
    assert ids["Financial"] in await _labels(plain)
    assert (await _choices(client, plain))["current"] == "financial"


async def test_category_tab_sets_the_category_and_drops_claiming_labels(client, user, raw):
    ids = await _owner_layout(client)
    _, shop = await _ingest(user, raw("New arrivals", msgid="<c1@x.org>"))
    await _set_cat([shop], "promotions")
    await client.post("/api/v1/conversations/actions", json={"ids": [shop], "action": "add_labels", "label_ids": [ids["Shopping"]]})
    assert (await _choices(client, shop))["current"] == "shopping"
    await _show(client, [shop], "promotions")
    assert ids["Shopping"] not in await _labels(shop)
    assert (await _choices(client, shop))["current"] == "promotions"

    _, plain = await _ingest(user, raw("Our newsletter", msgid="<c2@x.org>"))
    await _set_cat([plain], "primary")
    await _show(client, [plain], "promotions")
    assert await _cat(plain) == "promotions"
    assert (await _choices(client, plain))["current"] == "promotions"
    async with sessionmaker()() as db:
        rows = (await db.execute(select(LabelDecision.target, LabelDecision.source).where(
            LabelDecision.kind == "category", LabelDecision.source == "user",
            LabelDecision.message_id.in_(select(Message.id).where(Message.conversation_id == plain))))).all()
    assert ("promotions", "user") in rows, "a category change is the user's correction"


async def test_first_tab_drops_claiming_labels_and_resets_a_claimed_category(client, user, raw):
    ids = await _owner_layout(client)
    _, shop = await _ingest(user, raw("Jeans", msgid="<f1@x.org>"))
    await _set_cat([shop], "promotions")
    await client.post("/api/v1/conversations/actions", json={"ids": [shop], "action": "add_labels",
                                                             "label_ids": [ids["Shopping"], ids["Github"]]})
    await _show(client, [shop], "inbox")
    labels = await _labels(shop)
    assert ids["Shopping"] not in labels and ids["Github"] not in labels
    assert await _cat(shop) == "primary", "Promotions claims promotions, so the Inbox needs Primary"
    assert (await _choices(client, shop))["current"] == "inbox"

    # An archived conversation comes back to the Inbox.
    _, old = await _ingest(user, raw("Old thread", msgid="<f2@x.org>"))
    await client.post("/api/v1/conversations/actions", json={"ids": [old], "action": "archive"})
    assert (await _choices(client, old))["current"] is None
    await _show(client, [old], "inbox")
    assert (await _choices(client, old))["current"] == "inbox"


async def test_default_layout_category_tabs(client, user, raw):
    r = await client.patch("/api/v1/me/settings", json={"categories": ["primary", "social", "promotions"],
                                                         "inbox_type": "default"})
    assert r.status_code == 200, r.text
    _, c = await _ingest(user, raw("Party on Friday", msgid="<d1@x.org>"))
    await _set_cat([c], "primary")
    got = await _choices(client, c)
    assert [t["id"] for t in got["tabs"]] == ["primary", "social", "promotions"]
    assert [t["kind"] for t in got["tabs"]] == ["first", "category", "category"]
    assert got["current"] == "primary"
    await _show(client, [c], "social")
    assert await _cat(c) == "social"
    assert (await _choices(client, c))["current"] == "social"
    await _show(client, [c], "primary")
    assert await _cat(c) == "primary"


async def test_undo_puts_labels_and_category_back(client, user, raw):
    ids = await _owner_layout(client)
    _, c = await _ingest(user, raw("Deal of the day", msgid="<u1@x.org>"))
    await _set_cat([c], "promotions")
    await client.post("/api/v1/conversations/actions", json={"ids": [c], "action": "add_labels", "label_ids": [ids["Shopping"]]})
    labels_before = sorted(await _labels(c))
    res = await _show(client, [c], "inbox")
    assert await _cat(c) == "primary"
    undo = res["undo"]
    assert undo["action"] == "restore_tab" and undo["labels"] and undo["categories"]
    r = await client.post("/api/v1/conversations/actions", json=undo)
    assert r.status_code == 200, r.text
    assert sorted(await _labels(c)) == labels_before
    assert await _cat(c) == "promotions"
    assert (await _choices(client, c))["current"] == "shopping"
    async with sessionmaker()() as db:
        n = (await db.execute(select(LabelDecision.id).where(
            LabelDecision.kind == "category", LabelDecision.source == "user",
            LabelDecision.message_id.in_(select(Message.id).where(Message.conversation_id == c))))).all()
    assert not n, "an undone change teaches the category model nothing"


async def test_already_there_changes_nothing(client, user, raw):
    await _owner_layout(client)
    _, c = await _ingest(user, raw("Hello", msgid="<n1@x.org>"))
    await _set_cat([c], "primary")
    res = await _show(client, [c], "inbox")
    assert res["undo"] is None


async def test_search_and_state_tabs_are_disabled(client, user, raw):
    lay = {"name": "Mixed", "tabs": [
        {"name": "Inbox", "icon": "inbox", "sections": [{"title": "Inbox", "base": "inbox"}]},
        {"name": "Boss", "icon": "star", "claims": True, "sections": [{"title": "Boss", "base": "inbox", "q": "from:boss@example.org"}]},
        {"name": "Unread", "icon": "mail", "sections": [{"title": "Unread", "base": "inbox", "filter": "unread"}]},
        {"name": "Starred", "icon": "star", "sections": [{"title": "Starred", "base": "starred"}]},
    ]}
    r = await client.put("/api/v1/layouts", json={"layouts": [lay], "active": "mixed"})
    assert r.status_code == 200, r.text
    _, c = await _ingest(user, raw("Status", msgid="<q1@x.org>"))
    got = await _choices(client, c)
    kinds = {t["id"]: (t["kind"], t["reason"]) for t in got["tabs"]}
    assert kinds["inbox"] == ("first", None)
    assert kinds["boss"][0] == "disabled" and "search" in kinds["boss"][1]
    assert kinds["unread"][0] == "disabled" and "unread" in kinds["unread"][1]
    assert kinds["starred"][0] == "disabled" and "Starred" in kinds["starred"][1]
    r = await client.post("/api/v1/conversations/actions", json={"ids": [c], "action": "show_in_tab", "tab": "boss"})
    assert r.status_code == 400 and "search" in r.json()["detail"]
    r = await client.post("/api/v1/conversations/actions", json={"ids": [c], "action": "show_in_tab", "tab": "nope"})
    assert r.status_code == 400


def test_plan_is_pure_and_follows_claim_order():
    settings = {"inbox_type": "layout:l", "inbox_layouts": [{"id": "l", "name": "L", "tabs": [
        {"id": "inbox", "name": "Inbox", "sections": [{"id": "a", "base": "inbox"}]},
        {"id": "x", "name": "X", "claims": True, "sections": [{"id": "a", "base": "label:7"}]},
        {"id": "promo", "name": "Promo", "claims": True, "sections": [{"id": "a", "base": "inbox", "category": "promotions"}]},
        {"id": "y", "name": "Y", "claims": True, "sections": [{"id": "a", "base": "label:9"}]},
    ]}]}
    lay = layouts.find(settings, "l")
    # Y is right of X and Promo: both win over it, so X goes and a promotion becomes Primary.
    assert tabplace.plan(settings, lay, "y", "promotions", 1) == {"add": [9], "remove": [7], "category": "primary"}
    # X is leftmost of the claiming tabs: nothing to remove, the category stays.
    assert tabplace.plan(settings, lay, "x", "promotions", 1) == {"add": [7], "remove": [], "category": None}
    assert tabplace.plan(settings, lay, "promo", None, 1) == {"add": [1], "remove": [7], "category": "promotions"}
    assert tabplace.plan(settings, lay, "inbox", "social", 1) == {"add": [1], "remove": [7, 9], "category": None}


async def test_assistant_show_in_tab_by_name(client, user, raw):
    ids = await _owner_layout(client)
    _, c = await _ingest(user, raw("Pull request merged", msgid="<a1@x.org>"))
    r = await client.post("/api/v1/agent/tools/show_in_tab", json={"conversation_ids": [c], "tab": "github"})
    assert r.status_code == 200, r.text
    assert ids["Github"] in await _labels(c)
    r = await client.post("/api/v1/agent/tools/show_in_tab", json={"conversation_ids": [c], "tab": "Inbox"})
    assert r.status_code == 200, r.text
    assert ids["Github"] not in await _labels(c)
    r = await client.post("/api/v1/agent/tools/show_in_tab", json={"conversation_ids": [c], "tab": "Nowhere"})
    assert r.status_code >= 400 and "Financial" in r.text
