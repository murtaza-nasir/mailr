"""Settings → Assistant → Models: per-task choice of endpoint (api/model_prefs.py)."""

import asyncio

import pytest

from mailr.db import sessionmaker
from mailr.models import ModelEndpoint


@pytest.fixture(autouse=True)
async def _no_endpoints(app):
    """Endpoints are server-wide: each test here starts without any."""
    from sqlalchemy import delete

    async with sessionmaker()() as db:
        await db.execute(delete(ModelEndpoint))
        await db.commit()
    yield
    async with sessionmaker()() as db:
        await db.execute(delete(ModelEndpoint))
        await db.commit()


async def _eps():
    async with sessionmaker()() as db:
        db.add(ModelEndpoint(name="local-qwen", base_url="http://127.0.0.1:9/v1", locality="local", adapter="openai",
                             models=["qwen"], defaults={"chat": "qwen", "classify": "qwen"}, enabled=True))
        db.add(ModelEndpoint(name="jev", base_url="https://api.typesafe.ai/v1", locality="hosted", adapter="jev",
                             models=["jev-latest"], defaults={"classify": "jev-latest"}, enabled=True))
        await db.commit()


async def test_choose_a_model_per_task_and_keep_local_only_mail_local(client, user):
    await _eps()
    out = (await client.get("/api/v1/me/models")).json()
    by = {t["task"]: t for t in out["tasks"]}
    assert [o["endpoint"] for o in by["chat"]["options"]] == ["local-qwen"]  # Jev cannot chat
    assert {o["endpoint"] for o in by["classify"]["options"]} == {"local-qwen", "jev"}
    assert by["classify"]["effective"]["endpoint"] == "local-qwen" and by["classify"]["choice"] is None

    r = await client.put("/api/v1/me/models/classify", json={"endpoint": "jev"})
    assert r.status_code == 200, r.text
    c = {t["task"]: t for t in r.json()["tasks"]}["classify"]
    assert c["choice"] == {"endpoint": "jev", "model": "jev-latest"} and c["effective"]["endpoint"] == "jev"
    assert c["effective_local_only"]["endpoint"] == "local-qwen"  # local-only mail stays local

    assert (await client.put("/api/v1/me/models/chat", json={"endpoint": "jev"})).status_code == 400
    assert (await client.put("/api/v1/me/models/classify", json={"endpoint": "jev", "model": "gpt"})).status_code == 400
    assert (await client.put("/api/v1/me/models/embed", json={"endpoint": "jev"})).status_code == 404
    r = await client.put("/api/v1/me/models/classify", json={"endpoint": None})
    assert {t["task"]: t for t in r.json()["tasks"]}["classify"]["choice"] is None


async def test_admin_sets_one_default_and_one_local_model_per_task(admin_client, monkeypatch):
    """Admin → Models per task: exclusive defaults; local-only mail gets the local model chosen for it."""
    from mailr.api import admin_routes

    async def ok(url):
        return url.rstrip("/")

    monkeypatch.setattr(admin_routes, "_safe_base", ok)
    monkeypatch.setattr(admin_routes, "_check_local", lambda *_a: asyncio.sleep(0))  # names that do not resolve
    for body in ({"name": "qwen", "base_url": "http://gpu/v1", "locality": "local", "models": ["qwen"],
                  "defaults": {"chat": "qwen", "classify": "qwen"}},
                 {"name": "decider", "base_url": "http://gpu:8130/v1", "locality": "local", "adapter": "jev", "models": ["decider"]},
                 {"name": "jev", "base_url": "https://api.typesafe.ai/v1", "locality": "hosted", "adapter": "jev",
                  "models": ["jev-latest"]}):
        r = await admin_client.post("/api/v1/admin/models", json=body)
        assert r.status_code == 200, r.text
    rows = {t["task"]: t for t in (await admin_client.get("/api/v1/admin/models/defaults")).json()["tasks"]}
    assert set(rows) == {"chat", "classify", "embed", "transcribe"} and rows["classify"]["default"]["endpoint"] == "qwen"
    assert [o["endpoint"] for o in rows["chat"]["options"]] == ["qwen"]  # Jev-protocol endpoints only decide

    r = await admin_client.put("/api/v1/admin/models/defaults/classify",
                               json={"endpoint": "jev", "local_endpoint": "decider"})
    assert r.status_code == 200, r.text
    assert r.json()["default"] == {"endpoint": "jev", "model": "jev-latest"}
    assert r.json()["local"] == {"endpoint": "decider", "model": "decider"}
    eps = {e["name"]: e for e in (await admin_client.get("/api/v1/admin/models")).json()}
    assert "classify" not in eps["qwen"]["defaults"] and eps["qwen"]["defaults"]["chat"] == "qwen"  # exclusive
    assert (await admin_client.put("/api/v1/admin/models/defaults/chat", json={"endpoint": "jev"})).status_code == 400
    assert (await admin_client.put("/api/v1/admin/models/defaults/classify",
                                   json={"endpoint": "jev", "local_endpoint": "jev"})).status_code == 400

    # Saving an endpoint's connection details keeps its defaults.
    decider = eps["decider"]
    r = await admin_client.patch(f"/api/v1/admin/models/{decider['id']}", json={
        "name": "decider", "base_url": "http://gpu:8130/v1", "locality": "local", "adapter": "jev", "models": ["decider"]})
    assert r.json()["defaults"] == {"classify.local": "decider"}

    from mailr.db import sessionmaker
    from mailr.intel import llm
    from mailr.models import User

    # Resolved as this test's admin. The worker database keeps users from earlier tests, and the
    # first of them may be a member whose access policy allows no hosted model (PolicyRefused).
    me = (await admin_client.get("/api/v1/me")).json()["user"]["id"]
    async with sessionmaker()() as db:
        u = await db.get(User, me)
        assert (await llm.resolve(db, u, "classify", sensitive=False)).endpoint.name == "jev"
        assert (await llm.resolve(db, u, "classify", sensitive=True)).endpoint.name == "decider"


async def test_the_search_index_takes_only_a_model_that_makes_embeddings(admin_client, monkeypatch):
    """Admin → Models per task → Search index: one /embeddings call before a model is chosen. A
    chat-only model is refused with a plain message and listed as failed; a model that passed is
    listed as checked and not asked again; chat defaults are not probed."""
    import httpx

    from mailr.api import admin_routes
    from mailr.models import EMBED_DIM

    async def ok(url):
        return url.rstrip("/")

    monkeypatch.setattr(admin_routes, "_safe_base", ok)
    monkeypatch.setattr(admin_routes, "_check_local", lambda *_a: asyncio.sleep(0))
    calls: list[str] = []
    handler_ok = ["e5"]

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        model = json.loads(req.content)["model"]
        calls.append(f"{req.url.path} {model}")
        if model in handler_ok:
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1] * EMBED_DIM}]})
        return httpx.Response(400, json={"error": {"message": "This model does not support embeddings"}})

    from mailr.core import outbound
    from mailr.intel import llm

    monkeypatch.setattr(llm, "_http", lambda r, task, **kw: outbound.client(
        outbound.endpoint_service(r.endpoint, task), transport=httpx.MockTransport(handler), **kw))
    r = await admin_client.post("/api/v1/admin/models", json={
        "name": "gpu", "base_url": "http://gpu/v1", "locality": "local", "models": ["qwen", "e5"]})
    assert r.status_code == 200, r.text

    r = await admin_client.put("/api/v1/admin/models/defaults/embed", json={"endpoint": "gpu", "model": "qwen"})
    assert r.status_code == 400
    assert r.json()["detail"] == ("This model does not make search embeddings. Choose an embedding model, "
                                  "or leave Search index at None.")
    assert r.json()["code"] == "models.not_embedding_model"
    rows = {t["task"]: t for t in (await admin_client.get("/api/v1/admin/models/defaults")).json()["tasks"]}
    assert rows["embed"]["default"] is None and rows["embed"]["options"][0]["checked"] == []
    assert rows["embed"]["options"][0]["failed"] == ["qwen"]
    assert "checked" not in rows["chat"]["options"][0] and "failed" not in rows["chat"]["options"][0]
    assert not (rows["embed"].get("health") or {}).get("error")  # a refused check is no health error

    r = await admin_client.put("/api/v1/admin/models/defaults/embed", json={"endpoint": "gpu", "model": "e5"})
    assert r.status_code == 200, r.text
    assert r.json()["default"] == {"endpoint": "gpu", "model": "e5"} and r.json()["options"][0]["checked"] == ["e5"]
    assert r.json()["options"][0]["failed"] == ["qwen"]
    assert calls == ["/v1/embeddings qwen", "/v1/embeddings e5"]

    # Saved again (None, then e5): already checked, no second call.
    assert (await admin_client.put("/api/v1/admin/models/defaults/embed", json={"endpoint": None})).status_code == 200
    assert (await admin_client.put("/api/v1/admin/models/defaults/embed",
                                   json={"endpoint": "gpu", "model": "e5"})).status_code == 200
    # Chat and sorting take any model, unprobed.
    r = await admin_client.put("/api/v1/admin/models/defaults/chat", json={"endpoint": "gpu", "model": "qwen"})
    assert r.status_code == 200 and r.json()["default"] == {"endpoint": "gpu", "model": "qwen"}
    assert len(calls) == 2

    # Another address: its models are checked again.
    eid = {e["name"]: e for e in (await admin_client.get("/api/v1/admin/models")).json()}["gpu"]["id"]
    await admin_client.patch(f"/api/v1/admin/models/{eid}", json={
        "name": "gpu", "base_url": "http://gpu2/v1", "locality": "local", "models": ["qwen", "e5"]})
    rows = {t["task"]: t for t in (await admin_client.get("/api/v1/admin/models/defaults")).json()["tasks"]}
    assert rows["embed"]["options"][0]["checked"] == [] and rows["embed"]["options"][0]["failed"] == []

    # A model that failed and works later (loaded on the server since) leaves the failed list.
    assert (await admin_client.put("/api/v1/admin/models/defaults/embed",
                                   json={"endpoint": "gpu", "model": "qwen"})).status_code == 400
    handler_ok.append("qwen")
    r = await admin_client.put("/api/v1/admin/models/defaults/embed", json={"endpoint": "gpu", "model": "qwen"})
    assert r.status_code == 200, r.text
    assert r.json()["options"][0]["checked"] == ["qwen"] and r.json()["options"][0]["failed"] == []
