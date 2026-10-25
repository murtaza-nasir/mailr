from helpers.core_flow import _ingest
from sqlalchemy import update

from mailr.db import sessionmaker
from mailr.models import Conversation


async def test_mail_two_claiming_tabs_match_goes_to_the_leftmost(client, user, raw):
    """A "Shopping" label tab and a Promotions category tab both claim. A promotion with the
    Shopping label shows in Shopping (the left one), not in no tab; Promotions keeps the rest."""
    shop = (await client.post("/api/v1/labels", json={"name": "Shopping"})).json()["id"]
    _, both = await _ingest(user, raw("30% off jeans", frm="Deals <deals@shop.example>", msgid="<co1@x.org>"))
    _, promo = await _ingest(user, raw("Weekend sale", frm="News <news@store.example>", msgid="<co2@x.org>"))
    _, plain = await _ingest(user, raw("Lunch?", frm="Bob <bob@example.org>", msgid="<co3@x.org>"))
    async with sessionmaker()() as db:
        await db.execute(update(Conversation).where(Conversation.id.in_([both, promo])).values(category="promotions"))
        await db.execute(update(Conversation).where(Conversation.id == plain).values(category="primary"))
        await db.commit()
    await client.post("/api/v1/conversations/actions", json={"ids": [both], "action": "add_labels", "label_ids": [shop]})
    lay = {"name": "Claims", "tabs": [
        {"name": "Inbox", "icon": "inbox", "badge": "unread", "sections": [{"title": "All", "base": "inbox"}]},
        {"name": "Shopping", "icon": "tag", "badge": "unread", "claims": True, "sections": [{"title": "All", "base": f"label:{shop}"}]},
        {"name": "Promotions", "icon": "tag", "badge": "unread", "claims": True,
         "sections": [{"title": "All", "base": "inbox", "category": "promotions"}]},
    ]}
    r = await client.put("/api/v1/layouts", json={"layouts": [lay], "active": "claims"})
    assert r.status_code == 200, r.text

    async def ids(tab):
        r = await client.get("/api/v1/conversations", params={"layout": "claims", "tab": tab, "sec": "all"})
        assert r.status_code == 200, r.text
        return {c["id"] for c in r.json()["items"]}

    assert await ids("inbox") == {plain}
    assert await ids("shopping") == {both}
    assert await ids("promotions") == {promo}
    assert (await client.get("/api/v1/layouts/claims/counts")).json() == {"inbox": 1, "shopping": 1, "promotions": 1}
