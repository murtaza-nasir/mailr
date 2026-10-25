# ACT-SURFACES: api
"""More → Category (ACT mail.category): a category change is the user's correction, recorded as a "user"
decision the category model learns; its Undo (core/actions.restore_category) puts each message's
earlier category back, deletes that decision and gives a contradicted model decision its status
back, so an undone change teaches nothing."""

from helpers.public_ids import pub  # noqa: F401
from sqlalchemy import select

from mailr.core.ingest import IngestOptions, ingest_raw
from mailr.core.store import user_store
from mailr.db import sessionmaker
from mailr.models import Conversation, LabelDecision, Message, User


async def _ingest(user, data):
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        res = await ingest_raw(db, await user_store(db, u), data, IngestOptions())
        await db.commit()
        return res.message.id, res.message.conversation_id


async def _state(mid):
    async with sessionmaker()() as db:
        m = await db.get(Message, mid)
        rows = (await db.execute(select(LabelDecision).where(
            LabelDecision.message_id == mid, LabelDecision.kind == "category").order_by(LabelDecision.id))).scalars()
        return m.category, [(d.source, d.target, d.value, d.status) for d in rows]


async def test_category_change_trains_and_undo_restores(client, user, raw):
    mid, cid = await _ingest(user, raw("Weekly deals", frm="deals@shop.example", to=user.email))
    async with sessionmaker()() as db:
        m = await db.get(Message, mid)
        m.category = "promotions"
        await db.execute(LabelDecision.__table__.delete().where(LabelDecision.message_id == mid))
        # A model said "not updates": the user's choice contradicts it, so it is rejected.
        db.add(LabelDecision(user_id=user.id, message_id=mid, kind="category", target="updates", value=False,
                             source="classifier", confidence=0.7, reason="", status="applied"))
        await db.commit()
    before = await _state(mid)

    r = await client.post("/api/v1/conversations/actions", json={"ids": [cid], "action": "category", "category": "updates"})
    assert r.status_code == 200, r.text
    undo = r.json()["undo"]
    assert undo["action"] == "restore_category" and undo["ids"] == [cid]
    cat, rows = await _state(mid)
    assert cat == "updates"
    assert ("user", "updates", True, "accepted") in rows and ("classifier", "updates", False, "rejected") in rows

    r = await client.post("/api/v1/conversations/actions", json=undo)
    assert r.status_code == 200, r.text
    assert await _state(mid) == before, "category and decisions are as they were"


async def test_undo_back_to_no_category_clears_the_conversation(client, user, raw):
    mid, cid = await _ingest(user, raw("Plain note", frm="ann@people.example", to=user.email))
    async with sessionmaker()() as db:
        (await db.get(Message, mid)).category = None
        (await db.get(Conversation, cid)).category = None
        await db.commit()
    r = await client.post("/api/v1/conversations/actions", json={"ids": [cid], "action": "category", "category": "promotions"})
    assert r.status_code == 200, r.text
    async with sessionmaker()() as db:
        assert (await db.get(Conversation, cid)).category == "promotions"
    r = await client.post("/api/v1/conversations/actions", json=r.json()["undo"])
    assert r.status_code == 200, r.text
    async with sessionmaker()() as db:
        assert (await db.get(Message, mid)).category is None
        assert (await db.get(Conversation, cid)).category is None, "the list shows it in Primary again"


async def test_restore_category_needs_its_record(client, user, raw):
    _mid, cid = await _ingest(user, raw("Club news", frm="club@forum.example", to=user.email))
    r = await client.post("/api/v1/conversations/actions", json={"ids": [cid], "action": "restore_category"})
    assert r.status_code == 400
