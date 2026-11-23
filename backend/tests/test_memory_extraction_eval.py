"""The memory extraction eval (tests/memory_extraction_eval.py). The fixture is checked always;
the eval itself needs a real chat model (MAILR_MEMORY_EVAL_CHAT_URL and _CHAT_MODEL; optional
MAILR_MEMORY_EVAL_EMBED_URL and MAILR_MEMORY_EVAL_MICA_URL) and prints its report."""

import asyncio
import json
import os
import re
import uuid
from pathlib import Path

import pytest
from helpers.agent import _ingest, _setup_identity
from memory_extraction_eval import load, report, run_batch
from sqlalchemy import delete

from mailr.db import sessionmaker
from mailr.models import ModelEndpoint, User


def test_the_fixture_is_well_formed():
    fx = load()
    ids = [b["id"] for b in fx["batches"]]
    assert len(ids) == len(set(ids)) >= 10
    for b in fx["batches"]:
        assert b["emails"], b["id"]
        for k in b.get("same") or []:
            assert k in b["known"] and re.compile(b["known"][k]["match"]), (b["id"], k)
        for pat in [*(b.get("want") or {}).values(), *(b.get("avoid") or [])]:
            re.compile(pat)
        # A known note never matches what the batch says to avoid (the pattern would be meaningless).
        for n in (b.get("known") or {}).values():
            assert not any(re.search(p, n["text"]) for p in b.get("avoid") or []), (b["id"], n["text"])


@pytest.mark.timeout(1800)
@pytest.mark.skipif(not os.environ.get("MAILR_MEMORY_EVAL_CHAT_URL"), reason="real endpoints not given")
async def test_memory_extraction_eval_real_endpoints(client, user, raw, monkeypatch):
    if os.environ.get("MAILR_MEMORY_EVAL_NO_KNOWN"):  # the comparison: the model sees no known notes
        from mailr.knowledge import tasks

        async def none(*a, **k):
            return []

        monkeypatch.setattr(tasks, "known_notes", none)
    names: list[str] = []
    async with sessionmaker()() as db:
        def ep(kind, url, **kw):
            n = f"eval-{kind}-{uuid.uuid4().hex[:6]}"
            names.append(n)
            db.add(ModelEndpoint(name=n, base_url=url, locality="local", enabled=True, **kw))
            return n
        chat_model = os.environ.get("MAILR_MEMORY_EVAL_CHAT_MODEL", "m")
        chat = ep("chat", os.environ["MAILR_MEMORY_EVAL_CHAT_URL"], models=[chat_model], defaults={"chat": chat_model})
        if os.environ.get("MAILR_MEMORY_EVAL_EMBED_URL"):
            em = os.environ.get("MAILR_MEMORY_EVAL_EMBED_MODEL", "qwen3-embedding-0.6b")
            ep("embed", os.environ["MAILR_MEMORY_EVAL_EMBED_URL"], models=[em], defaults={"embed": em})
        if os.environ.get("MAILR_MEMORY_EVAL_MICA_URL"):
            mm = os.environ.get("MAILR_MEMORY_EVAL_MICA_MODEL", "decider")
            ep("decider", os.environ["MAILR_MEMORY_EVAL_MICA_URL"], adapter="jev", models=[mm], defaults={"classify": mm})
        u = await db.get(User, user.id)
        u.settings = {**(u.settings or {}), "models": {"chat": {"endpoint": chat, "model": chat_model}}}
        await db.commit()
    try:
        _, me = await _setup_identity(user)
        fx = load()
        only = os.environ.get("MAILR_MEMORY_EVAL_BATCH")
        results = [await run_batch(user, fx, b, ingest=_ingest, raw=raw, me=me)
                   for b in fx["batches"] if not only or b["id"] == only]
        for r in results:
            print(json.dumps(r, ensure_ascii=False))
        out = report(results)
        print("REPORT", json.dumps(out))
        if os.environ.get("MAILR_MEMORY_EVAL_OUT"):  # tools/test-backend keeps stdout
            text = "\n".join(json.dumps(r, ensure_ascii=False) for r in results) + "\nREPORT " + json.dumps(out) + "\n"
            await asyncio.to_thread(Path(os.environ["MAILR_MEMORY_EVAL_OUT"]).write_text, text)
        if not only and not os.environ.get("MAILR_MEMORY_EVAL_NO_KNOWN"):
            assert out["duplicates"] == 0 and out["avoided_kept"] == 0 and out["wanted_kept"] >= 0.8, out
    finally:
        async with sessionmaker()() as db:
            await db.execute(delete(ModelEndpoint).where(ModelEndpoint.name.in_(names)))
            await db.commit()
