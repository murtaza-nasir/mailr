"""Search and memory queries over REST declare their data class (outbound data policy, gaps 3 and 4).

A local-only token reads local-only mail, so what it types may quote that mail: its semantic search,
passage search, memory search and memory lookup embed the query only on a local endpoint. An
ordinary token's query still reaches a hosted embedding endpoint (the check is not vacuous). Compose
assist for a draft in local-only mail looks up memory with query_sensitive set."""

import uuid

import embedfake
import pytest
from helpers.agent import _ingest, _setup_identity, _token_client

CANARY = f"zebraquery{uuid.uuid4().hex[:8]}"


def _sent(word: str) -> bool:
    return any(word in t for call in embedfake.CALLS for t in call)


@pytest.mark.parametrize("path", ["/api/v1/search/semantic", "/api/v1/passages/search",
                                  "/api/v1/assistant/memories", "/api/v1/assistant/recall"])
async def test_local_only_token_queries_stay_off_hosted_embeddings(client, user, monkeypatch, path):
    loc = (await client.post("/api/v1/tokens", json={"name": "l", "scopes": ["read"], "local_only": True})).json()
    hosted = (await client.post("/api/v1/tokens", json={"name": "h", "scopes": ["read"], "local_only": False})).json()
    async with embedfake.embedding_endpoint(monkeypatch, locality="hosted"):
        embedfake.CALLS.clear()
        word = f"{CANARY}local"
        async with _token_client(client, loc["token"]) as a:
            r = await a.get(path, params={"q": f"budget {word}"})
            assert r.status_code == 200, r.text
        assert not _sent(word)
        word = f"{CANARY}normal"
        async with _token_client(client, hosted["token"]) as a:
            r = await a.get(path, params={"q": f"budget {word}"})
            assert r.status_code == 200, r.text
        if path != "/api/v1/assistant/recall" or embedfake.CALLS:
            # recall may skip the vector lookup for a short command; the others always embed
            assert _sent(word)


async def test_compose_assist_in_local_only_mail_marks_the_memory_query(client, user, raw, monkeypatch):
    from mailr.knowledge import memory

    conn_id, me = await _setup_identity(user, data_class="local_only")
    _mid, conv = await _ingest(user.id, raw("Grade appeal", frm="S <s@uni.example>", to=me, body="private"),
                               connector_id=conn_id)
    seen: list[dict] = []
    real = memory.context_block

    async def spy(*a, **kw):
        seen.append(kw)
        return await real(*a, **kw)

    monkeypatch.setattr(memory, "context_block", spy)
    await client.post("/api/v1/agent/draft", json={"mode": "reply", "conversation_id": conv,
                                                   "instructions": f"say yes {CANARY}"})
    assert seen and seen[-1]["query_sensitive"] is True


async def test_skill_try_it_stays_off_hosted_models_for_a_user_with_local_only_mail(client, user, monkeypatch):
    """The "Try it" box: its sample may be pasted from local-only mail, so a user who has such mail
    gets no hosted model for it; a user without local-only data does (the check is not vacuous)."""
    from helpers.outbound_sweep import FakeNet
    from sqlalchemy import delete, select, update

    from mailr.db import sessionmaker
    from mailr.models import ModelEndpoint

    net = FakeNet().install(monkeypatch)
    host = net.add("llm-try.sweep.test", "openai", "hosted")
    name = f"try-{uuid.uuid4().hex[:6]}"
    async with sessionmaker()() as db:
        on = list((await db.execute(select(ModelEndpoint.id).where(ModelEndpoint.enabled.is_(True)))).scalars())
        await db.execute(update(ModelEndpoint).values(enabled=False))
        db.add(ModelEndpoint(name=name, base_url=f"https://{host}/v1", locality="hosted", models=["m"],
                             defaults={"chat": "m"}, enabled=True))
        await db.commit()
    try:
        r = await client.post("/api/v1/assistant/skills/test", json={"sample": f"plain {CANARY}",
                                                                       "instructions": "Be brief."})
        assert r.status_code == 200, r.text
        assert net.leaks([CANARY])
        await _setup_identity(user, data_class="local_only")
        word = f"{CANARY}secret"
        r = await client.post("/api/v1/assistant/skills/test", json={"sample": word, "instructions": "Be brief."})
        assert r.status_code != 200
        assert not net.leaks([word])
    finally:
        async with sessionmaker()() as db:
            await db.execute(delete(ModelEndpoint).where(ModelEndpoint.name == name))
            if on:
                await db.execute(update(ModelEndpoint).where(ModelEndpoint.id.in_(on)).values(enabled=True))
            await db.commit()
