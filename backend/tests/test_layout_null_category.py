from helpers.core_flow import _ingest
from sqlalchemy import update

from mailr.db import sessionmaker
from mailr.models import Conversation


async def test_an_uncategorised_conversation_stays_in_the_inbox_tab(client, user, raw):
    """A conversation with no category (NULL) is not claimed by a Promotions tab, so it shows in
    the Inbox tab's sections and counts. NOT(category = 'promotions') alone is NULL for it, and
    SQL dropped it from every tab."""
    _, plain = await _ingest(user, raw("Give me the PRD", frm="Bob <bob@example.org>", msgid="<nc1@x.org>"))
    _, promo = await _ingest(user, raw("50% off", frm="Shop <deals@shop.example>", msgid="<nc2@x.org>"))
    async with sessionmaker()() as db:
        await db.execute(update(Conversation).where(Conversation.id == plain).values(category=None))
        await db.execute(update(Conversation).where(Conversation.id == promo).values(category="promotions"))
        await db.commit()

    lay = {"name": "Nulls", "tabs": [
        {"name": "Inbox", "icon": "inbox", "badge": "unread", "sections": [
            {"title": "Unread", "base": "inbox", "filter": "unread"},
            {"title": "Everything else", "base": "inbox", "exclude_above": True}]},
        {"name": "Promotions", "icon": "tag", "badge": "unread", "claims": True,
         "sections": [{"title": "All", "base": "inbox", "category": "promotions"}]},
    ]}
    r = await client.put("/api/v1/layouts", json={"layouts": [lay], "active": "nulls"})
    assert r.status_code == 200, r.text
    tab = next(x for x in r.json()["layouts"] if x["id"] == "nulls")["tabs"][0]
    unread_sec, rest_sec = (s["id"] for s in tab["sections"])

    async def ids(sec):
        r = await client.get("/api/v1/conversations", params={"layout": "nulls", "tab": "inbox", "sec": sec})
        assert r.status_code == 200, r.text
        return [c["id"] for c in r.json()["items"]]

    assert await ids(unread_sec) == [plain]
    await client.post("/api/v1/conversations/actions", json={"ids": [plain], "action": "read"})
    assert await ids(rest_sec) == [plain]
    counts = (await client.get("/api/v1/layouts/nulls/counts")).json()
    assert counts == {"inbox": 0, "promotions": 1}, counts
    await client.post("/api/v1/conversations/actions", json={"ids": [plain], "action": "unread"})
    assert (await client.get("/api/v1/layouts/nulls/counts")).json() == {"inbox": 1, "promotions": 1}


async def test_tab_counts_read_the_labels_once(client, user):
    """A layout's counts build every section's and every claiming tab's conditions; each
    used to read the label list again. Now one read per request."""
    from tests.scale_fixture import counting

    lay = {"id": "many", "name": "Many", "tabs": [
        {"name": f"Tab {i}", "icon": "inbox", "badge": "unread", "claims": i > 0, "sections": [
            {"title": "A", "base": "inbox", "category": c}, {"title": "B", "base": "inbox", "filter": "unread"}]}
        for i, c in enumerate(["primary", "social", "updates", "forums", "promotions"])]}
    r = await client.put("/api/v1/layouts", json={"layouts": [lay], "active": "many"})
    assert r.status_code == 200, r.text
    with counting() as c:
        r = await client.get("/api/v1/layouts/many/counts", params={"detail": True})
    assert r.status_code == 200, r.text
    reads = [s for s in c.statements if "FROM labels" in s and "labels.sort" in s]
    assert len(reads) == 1, len(reads)
