"""Group notes and tables follow any member's "Local models only for me" (outbound data policy,
gap 12). A group's content has no owning person, so its embedding job used to ask only the household
rule: with one member's switch on, a hosted embedding endpoint now receives none of it; with the
switch off it does (the check is not vacuous)."""

import uuid

import embedfake
from helpers.notes_sharing import API, people  # noqa: F401

from mailr.db import sessionmaker
from mailr.models import User


async def _switch(uid, on):
    async with sessionmaker()() as db:
        u = await db.get(User, uid)
        u.settings = {**(u.settings or {}), "local_models_only": on}
        await db.commit()


async def test_group_content_stays_off_hosted_embeddings_when_a_member_wants_local(people, monkeypatch):
    from mailr.notes.embed import embed_pending
    from mailr.tables.embed import embed_pending as table_embed

    g = f"House-{uuid.uuid4().hex[:6]}"
    canary = f"grpcanary{uuid.uuid4().hex[:8]}"
    a = await people("Ann", (g,))
    b = await people("Bea", (g,))
    note = (await a.post(f"{API}/notes", json={"kind": "text", "title": "House rules",
                                               "body": f"{canary} " + "word " * 80, "group": g}))
    assert note.status_code == 200, note.text
    t = await a.post(f"{API}/tables", json={"name": "Chores", "group": g, "columns": [{"name": "Task"}],
                                            "description": f"{canary} " + "who does which chore " * 20})
    assert t.status_code == 200, t.text
    await _switch(b.uid, True)
    async with embedfake.embedding_endpoint(monkeypatch, locality="hosted"):
        embedfake.CALLS.clear()
        async with sessionmaker()() as db:
            assert await embed_pending(db, None, group=g) == 0
            assert await table_embed(db, None, group=g) == 0
            await db.commit()
        assert not any(canary in x for call in embedfake.CALLS for x in call)
        await _switch(b.uid, False)
        async with sessionmaker()() as db:
            assert await embed_pending(db, None, group=g) == 1
            assert await table_embed(db, None, group=g) == 1
            await db.commit()
        assert any(canary in x for call in embedfake.CALLS for x in call)
