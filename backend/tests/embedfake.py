"""A deterministic stand-in for an OpenAI-compatible embedding endpoint.

Vectors are hashed bags of words (512-d, L2-normalised) with a few synonym classes, so
"revenue forecast" lands near "profit projections" the way a real model would. Tests patch
`llm.embed_request` with it; nothing is sent over the network.

Email addresses are left out of the vector. Test mail and test users get random addresses
(`s3f9a1c@example.org`, `t-1a2b3c4d@example.com`), and a passage carries its sender
(embed.passage_input). A random address token lands on a random one of the 512 dimensions. When
it lands on a word's dimension, the vector moves towards that word. A label model then scored
differently from run to run: once in a few hundred runs the probe's sender in
test_label_autoapply::test_user_removal_is_a_strong_negative hashed onto "twitter", and the
removal case scored 0.517 (9 Oct 2026). The sender's display name still counts."""

import hashlib
import math
import re
import uuid
from contextlib import asynccontextmanager

from sqlalchemy import delete

from mailr.db import sessionmaker
from mailr.intel import embed, llm
from mailr.models import EMBED_DIM, ModelEndpoint

SYN = {"revenue": "money", "profit": "money", "income": "money", "forecast": "outlook", "projections": "outlook",
       "projection": "outlook", "deadline": "due", "due": "due", "slides": "deck", "deck": "deck"}
STOP = {"subject", "given", "question", "about", "user's", "email", "retrieve", "passages", "answer",
        "instruct", "query", "example", "there", "their", "which", "would", "could"}
CALLS: list[list[str]] = []
ADDRESS = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")


def vector(text: str) -> list[float]:
    v = [0.0] * EMBED_DIM
    for w in re.findall(r"[a-z0-9]+", ADDRESS.sub(" ", text.lower())):
        if len(w) < 5 or w in STOP:
            continue
        w = SYN.get(w, w)
        h = int(hashlib.md5(w.encode()).hexdigest(), 16)
        v[h % EMBED_DIM] += 1.0 if (h >> 20) % 2 else -1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


async def fake_embed_request(r, texts, dim, *, timeout=60):
    CALLS.append(list(texts))
    return [vector(t.split("Query: ", 1)[-1]) for t in texts]


@asynccontextmanager
async def embedding_endpoint(monkeypatch, locality="local"):
    name = f"fake-embed-{uuid.uuid4().hex[:6]}"
    monkeypatch.setattr(llm, "embed_request", fake_embed_request)
    embed.reset_breaker()
    async with sessionmaker()() as db:
        db.add(ModelEndpoint(name=name, base_url="http://embed.fake/v1", locality=locality,
                             models=["fake-embed"], defaults={"embed": "fake-embed"}))
        await db.commit()
    try:
        yield name
    finally:
        async with sessionmaker()() as db:
            await db.execute(delete(ModelEndpoint).where(ModelEndpoint.name == name))
            await db.commit()
