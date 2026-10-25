"""Each message vector records its embedding model, and vector queries compare only
vectors of the current model. `mailr embed-model-backfill` records the current model on vectors
stored before the column existed."""

from embedfake import embedding_endpoint, vector
from helpers.core_flow import _ingest
from helpers.passages import LONG, _process
from sqlalchemy import select, update

from mailr.agent import context
from mailr.core.auth import ALL_SCOPES, Principal
from mailr.db import sessionmaker
from mailr.intel import embed
from mailr.models import Message, MessagePassage, User

KEY = "fake-embed:512"


async def test_embedding_records_its_model(monkeypatch, user, raw):
    async with embedding_endpoint(monkeypatch):
        mid, _ = await _ingest(user, raw("Budget review", body=LONG, msgid="<em1@x.org>"))
        await _process(mid)
    async with sessionmaker()() as db:
        m = await db.get(Message, mid)
        assert m.embedding is not None and m.embed_model == KEY


async def test_neighbours_come_only_from_the_current_model(monkeypatch, user, raw):
    """Two messages with the same vector: the one recorded under another model is never compared."""
    _, same = await _ingest(user, raw("Quarterly forecast", body="numbers", msgid="<em2@x.org>"), roles=["inbox"])
    _, other = await _ingest(user, raw("Old model mail", body="numbers", msgid="<em3@x.org>"), roles=["inbox"])
    v = vector("quarterly forecast numbers")

    async def query(db, q, *, sensitive=False):
        return v, KEY

    monkeypatch.setattr(embed, "embed_query", query)
    async with sessionmaker()() as db:
        await db.execute(update(Message).where(Message.conversation_id == same).values(embedding=v, embed_model=KEY))
        await db.execute(update(Message).where(Message.conversation_id == other)
                         .values(embedding=v, embed_model="old-model:512"))
        await db.flush()
        p = Principal(user=await db.get(User, user.id), scopes=set(ALL_SCOPES) - {"admin"})
        got = [r["conversation_id"] for r in await context._related(db, p, [], None, "zzzz")]
        await db.rollback()
    assert got == [same]


async def test_backfill_records_the_current_model(monkeypatch, user, raw):
    """Vectors without a model get the current one; a message whose passages are of another model
    (a re-embedding in progress) is left to the re-embedding; a message without a vector stays
    without a model. A second run changes nothing."""
    v = vector("anything at all here")
    ids = []
    for i in range(3):
        mid, _ = await _ingest(user, raw(f"Old vector {i}", body="text", msgid=f"<bf{i}@x.org>"))
        ids.append(mid)
    plain, _ = await _ingest(user, raw("No vector", body="text", msgid="<bf9@x.org>"))
    async with sessionmaker()() as db:
        await db.execute(update(Message).where(Message.id.in_(ids)).values(embedding=v, embed_model=None))
        db.add(MessagePassage(user_id=user.id, message_id=ids[2], idx=0, start=0, end=4, text="text",
                              content_hash="h-bf2", embedding=v[:], embed_model="old-model:512"))
        await db.commit()
    async with embedding_endpoint(monkeypatch):
        st = await embed.backfill_message_models(user.id, batch=2)
        again = await embed.backfill_message_models(user.id, batch=2)
    assert (st["stamped"], st["skipped"], st["model"]) == (2, 1, KEY)
    assert (again["stamped"], again["skipped"]) == (0, 1)
    async with sessionmaker()() as db:
        got = dict((await db.execute(select(Message.id, Message.embed_model)
                                     .where(Message.id.in_([*ids, plain])))).all())
    assert got == {ids[0]: KEY, ids[1]: KEY, ids[2]: None, plain: None}


async def test_backfill_needs_an_embedding_model(user):
    import pytest

    with pytest.raises(LookupError):
        await embed.backfill_message_models(user.id)


async def test_a_model_change_reembeds_down_the_message_ids(monkeypatch, user, raw):
    """After a model change the passages of the old model are re-embedded a batch at a time, down
    the message ids: each batch starts where the last one stopped (`before`), so it never walks the
    passages already done again, and the last batch finds nothing left."""
    v = vector("an old model vector")
    ids = []
    for i in range(5):
        mid, _ = await _ingest(user, raw(f"Old passage {i}", body="text", msgid=f"<re{i}@x.org>"))
        ids.append(mid)
    async with sessionmaker()() as db:
        for mid in ids:
            db.add(MessagePassage(user_id=user.id, message_id=mid, idx=0, start=0, end=4, text=f"text {mid}",
                                  content_hash=f"h-re-{mid}", embedding=v[:], embed_model="old-model:512"))
        await db.commit()
    seen, before = [], None
    async with embedding_endpoint(monkeypatch):
        for _ in range(5):
            async with sessionmaker()() as db:
                res = await embed.embed_pending_passages(db, user.id, limit=2, before=before)
                await db.commit()
            if not res["pending"]:
                break
            seen.append(res["before"])
            before = res["before"]
    assert seen == [ids[3], ids[1], ids[0]]
    async with sessionmaker()() as db:
        models = set((await db.execute(select(MessagePassage.embed_model)
                                       .where(MessagePassage.message_id.in_(ids)))).scalars())
    assert models == {KEY}
