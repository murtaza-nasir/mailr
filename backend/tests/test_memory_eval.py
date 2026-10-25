"""The memory relevance eval as a test (tests/memory_eval.py, tests/fixtures/memory_relevance.yaml).

The default run uses the deterministic test embedder and no decision model, and holds the
thresholds. With MAILR_MEMORY_EVAL_EMBED_URL set (an OpenAI-compatible /v1 with the model in
MAILR_MEMORY_EVAL_EMBED_MODEL) and optionally MAILR_MEMORY_EVAL_MICA_URL (a Jev-protocol /v1), the
second test runs against those endpoints and prints the report; run it with `-s`.
"""

import os
import uuid

import pytest
from sqlalchemy import delete

from mailr.db import sessionmaker
from mailr.models import ModelEndpoint, User


async def _principal(user_id):
    from mailr.core.auth import ALL_SCOPES, Principal

    db = sessionmaker()()
    u = await db.get(User, user_id)
    return db, Principal(user=u, scopes=set(ALL_SCOPES) - {"admin"})


async def _seed_and_run(user, *, held_out=None):
    import memory_eval

    from mailr.knowledge import memory

    fx = memory_eval.load(held_out=held_out)
    db, p = await _principal(user.id)
    async with db:
        ids = await memory_eval.seed(db, p, fx)
        await db.commit()
        while await memory.embed_pending(db, user.id):
            await db.commit()
        await db.commit()
        out = await memory_eval.run(db, p, fx, ids)
        if os.environ.get("MAILR_MEMORY_EVAL_CALIBRATE"):
            for row in await memory_eval.calibrate(db, p, fx, ids):
                print(row)
            for c in out["checks"]:
                print("check", c)
        await db.rollback()
    return out


async def test_memory_relevance_eval(client, user, monkeypatch):
    """Thresholds for the deterministic run (no decision model; the 42 calibration requests). Before the gate (7 Oct
    2026, this embedder): precision 0.735, recall 0.893, 1.45 notes per turn, 6 of 16 requests with
    nothing to use kept empty. With the gate: 0.765, 0.929, 1.26, 13 of 16."""
    import memory_eval
    from embedfake import embedding_endpoint

    async with embedding_endpoint(monkeypatch):
        out = await _seed_and_run(user, held_out=False)
    print(memory_eval.report(out, "memory relevance eval (test embedder)"))
    assert out["precision"] >= 0.75
    assert out["recall"] >= 0.9
    assert out["notes_per_turn"] <= 1.4
    kept, empty = (int(x) for x in out["empty_kept_empty"].split("/"))
    assert kept >= empty - 3
    # "Can you also add razors" uses no note at all.
    razors = next(r for r in out["rows"] if r["q"] == "Can you also add razors")
    assert razors["got"] == []


@pytest.mark.skipif(not os.environ.get("MAILR_MEMORY_EVAL_EMBED_URL"), reason="real endpoints not given")
async def test_memory_relevance_eval_real_endpoints(client, user):
    import memory_eval

    from mailr.intel import embed

    names = []
    embed.reset_breaker()
    async with sessionmaker()() as db:
        name = f"eval-embed-{uuid.uuid4().hex[:6]}"
        model = os.environ.get("MAILR_MEMORY_EVAL_EMBED_MODEL", "qwen3-embedding-0.6b")
        db.add(ModelEndpoint(name=name, base_url=os.environ["MAILR_MEMORY_EVAL_EMBED_URL"], locality="local",
                             models=[model], defaults={"embed": model}))
        names.append(name)
        if os.environ.get("MAILR_MEMORY_EVAL_MICA_URL"):
            name = f"eval-decider-{uuid.uuid4().hex[:6]}"
            db.add(ModelEndpoint(name=name, base_url=os.environ["MAILR_MEMORY_EVAL_MICA_URL"], adapter="jev",
                                 locality="local", models=["decider"], defaults={"classify": "decider"}))
            names.append(name)
        await db.commit()
    try:
        out = await _seed_and_run(user)
        print(memory_eval.report(out, "memory relevance eval (real endpoints)"))
        # Measured 7 Oct 2026 with qwen3-embedding-0.6b on all 58 requests: precision 0.97, recall 0.95
        # with Decider. A part of the set (MAILR_MEMORY_EVAL_SET=held_out or calibration) is only reported.
        if os.environ.get("MAILR_MEMORY_EVAL_SET"):
            return
        assert out["precision"] >= (0.9 if os.environ.get("MAILR_MEMORY_EVAL_MICA_URL") else 0.65)
        assert out["recall"] >= 0.9
    finally:
        async with sessionmaker()() as db:
            await db.execute(delete(ModelEndpoint).where(ModelEndpoint.name.in_(names)))
            await db.commit()
