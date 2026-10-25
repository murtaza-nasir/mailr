# ACT-SURFACES: api
"""Local models only at two levels, Model use and the model on each Ambrose answer
(intel/locality.py, intel/usage.py, intel/llm.resolve).

Covered, with their action ids: settings.local_models_household, settings.local_models_me,
settings.model_use, agent.answer_model. Also: the effective rule (household OR person OR the content's own flag) in
llm.resolve and llm.resolve_embed, the fallback to a local endpoint, "not available with local
models only", the locked switch with its reason, counting, and the answer's model.
"""

from __future__ import annotations

import uuid

import pytest
from helpers.agent import _drop_endpoint, _endpoint, _events  # fixture
from sqlalchemy import delete

from mailr.db import sessionmaker
from mailr.intel import llm, locality
from mailr.models import InstanceSetting, ModelEndpoint, User

API = "/api/v1"


@pytest.fixture
async def household():
    """Set the household rule for one test; back to the default after."""
    async def set_(v: str) -> None:
        async with sessionmaker()() as db:
            await locality.set_household(db, v)
            await db.commit()

    yield set_
    async with sessionmaker()() as db:
        await db.execute(delete(InstanceSetting).where(InstanceSetting.key == locality.SETTING))
        await db.commit()


@pytest.fixture
async def eps():
    """A hosted chat default and a local one, with names unique to the test."""
    tag = uuid.uuid4().hex[:6]
    names = {"hosted": f"cloud-{tag}", "local": f"lan-{tag}"}
    async with sessionmaker()() as db:
        db.add(ModelEndpoint(name=names["hosted"], base_url="https://cloud.invalid/v1", locality="hosted",
                             models=["big"], defaults={"chat": "big", "embed": "e-big"}, enabled=True))
        db.add(ModelEndpoint(name=names["local"], base_url="http://lan.invalid/v1", locality="local",
                             models=["small"], defaults={}, enabled=True))
        await db.commit()
    yield names
    async with sessionmaker()() as db:
        await db.execute(delete(ModelEndpoint).where(ModelEndpoint.name.in_(list(names.values()))))
        await db.commit()


async def _me(user_id: int) -> User:
    async with sessionmaker()() as db:
        return await db.get(User, user_id)


async def _choose(user_id: int, endpoint: str, model: str) -> None:
    async with sessionmaker()() as db:
        u = await db.get(User, user_id)
        u.settings = {**(u.settings or {}), "models": {"chat": {"endpoint": endpoint, "model": model}}}
        await db.commit()


async def test_my_switch_keeps_my_calls_local(client, user, household, eps):
    await household("per_user")
    await _choose(user.id, eps["hosted"], "big")
    async with sessionmaker()() as db:
        assert (await llm.resolve(db, await db.get(User, user.id), "chat", sensitive=False, probe=True)).endpoint.name \
            == eps["hosted"]
    st = (await client.get(f"{API}/me/local-models")).json()
    assert st["household"] == "per_user" and not st["locked"] and st["mine"] is False and st["effective"] is False

    r = await client.put(f"{API}/me/local-models", json={"on": True})
    assert r.status_code == 200 and r.json()["effective"] is True
    # The hosted choice is not used: no local chat default, so the call is refused plainly.
    async with sessionmaker()() as db:
        with pytest.raises(llm.LocalModelsOnly) as ex:
            await llm.resolve(db, await db.get(User, user.id), "chat", sensitive=False)
    assert str(ex.value).startswith("Not available with local models only")
    assert any(x["task"] == "chat" for x in (await client.get(f"{API}/me/local-models")).json()["no_local"])
    # With a local chat default it falls back to it.
    async with sessionmaker()() as db:
        ep = (await db.execute(ModelEndpoint.__table__.select().where(ModelEndpoint.name == eps["local"]))).first()
        row = await db.get(ModelEndpoint, ep.id)
        row.defaults = {"chat": "small"}
        await db.commit()
    async with sessionmaker()() as db:
        r = await llm.resolve(db, await db.get(User, user.id), "chat", sensitive=False)
    assert r.endpoint.name == eps["local"] and r.local
    # The search index: the hosted embedder is not used for my content.
    async with sessionmaker()() as db:
        assert await llm.resolve_embed(db, sensitive=False, user_id=user.id) is None
        assert (await llm.resolve_embed(db, sensitive=False)) is not None  # someone else's content

    # The setting is not changed through PATCH /me/settings.
    assert (await client.patch(f"{API}/me/settings", json={"local_models_only": False})).status_code == 400
    assert (await client.put(f"{API}/me/local-models", json={"on": False})).json()["effective"] is False


async def test_local_only_for_everyone_locks_the_switch_on(client, user, household, eps):
    await _choose(user.id, eps["hosted"], "big")
    await household("local_all")
    st = (await client.get(f"{API}/me/local-models")).json()
    assert st["locked"] and st["effective"] and st["reason"] == locality.REASONS["local_all"]
    r = await client.put(f"{API}/me/local-models", json={"on": False})
    assert r.status_code == 409 and r.json()["detail"] == locality.REASONS["local_all"]
    async with sessionmaker()() as db:
        with pytest.raises(llm.LocalModelsOnly):
            await llm.resolve(db, await db.get(User, user.id), "chat", sensitive=False)
        with pytest.raises(llm.LocalModelsOnly):
            await llm.resolve(db, None, "chat", sensitive=False)  # system jobs too

    # Each person chooses: the switch is free again and can always make things stricter.
    await household("per_user")
    st = (await client.get(f"{API}/me/local-models")).json()
    assert not st["locked"] and st["reason"] is None and st["effective"] is False
    async with sessionmaker()() as db:
        assert (await llm.resolve(db, await db.get(User, user.id), "chat", sensitive=False)).endpoint.name \
            == eps["hosted"]
        # The content's own local_only flag still wins (admin OR user OR item).
        with pytest.raises(llm.LocalOnlyViolation):
            await llm.resolve(db, await db.get(User, user.id), "chat", sensitive=True)
    assert (await client.put(f"{API}/me/local-models", json={"on": True})).json()["effective"] is True


async def test_an_old_hosted_value_reads_as_each_person_chooses(client, household):
    """The third choice of the first build ("hosted allowed for everyone") is folded into per_user:
    a stored value reads as per_user and never locks the switch off."""
    async with sessionmaker()() as db:
        row = await db.get(InstanceSetting, locality.SETTING)
        if row is None:
            db.add(InstanceSetting(key=locality.SETTING, value="hosted"))
        else:
            row.value = "hosted"
        await db.commit()
    st = (await client.get(f"{API}/me/local-models")).json()
    assert st["household"] == "per_user" and not st["locked"]
    assert (await client.put(f"{API}/me/local-models", json={"on": True})).json()["effective"] is True
    assert locality.effective("per_user", None) is False and locality.effective("local_all", None) is True


async def test_admin_sets_the_household_rule(admin_client, household):
    r = await admin_client.get(f"{API}/admin/models/local-policy")
    assert r.json() == {"policy": "per_user", "choices": ["per_user", "local_all"]}
    assert (await admin_client.put(f"{API}/admin/models/local-policy", json={"policy": "local_all"})).json()["policy"] \
        == "local_all"
    # "hosted" is taken for one release as an alias of per_user.
    assert (await admin_client.put(f"{API}/admin/models/local-policy", json={"policy": "hosted"})).json()["policy"] \
        == "per_user"
    assert (await admin_client.put(f"{API}/admin/models/local-policy", json={"policy": "nope"})).status_code == 422


async def test_regular_users_cannot_set_the_household_rule(client):
    assert (await client.put(f"{API}/admin/models/local-policy", json={"policy": "local_all"})).status_code == 403


async def test_model_use_counts_calls_per_feature(client, user, household, eps):
    await _choose(user.id, eps["hosted"], "big")
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        for _ in range(3):
            await llm.resolve(db, u, "chat", sensitive=False, feature="summaries")
        await llm.resolve(db, u, "chat", sensitive=False, probe=True)  # a probe is not a call
        r = await llm.resolve(db, u, "chat", sensitive=False, feature="triage", count=False)
        await llm.counted(u, "triage", r)
    rows = (await client.get(f"{API}/me/model-use")).json()["rows"]
    got = {(x["feature"], x["endpoint"], x["local"]): x["calls"] for x in rows}
    assert got[("summaries", eps["hosted"], False)] == 3 and got[("triage", eps["hosted"], False)] == 1
    assert next(x for x in rows if x["feature"] == "summaries")["label"] == "Summaries"


async def test_each_answer_names_its_model(client, user, fake_llm, household):
    ep = await _endpoint(user.id, "local")
    try:
        fake_llm.script = [[{"choices": [{"delta": {"content": "Hello."}}]},
                            {"choices": [{"delta": {}, "finish_reason": "stop"}]}]]
        evs = _events((await client.post(f"{API}/agent/chat", json={"message": "hi"})).text)
        final = next(e for e in evs if e["type"] == "final")
        assert final["model"] == {"name": "m", "endpoint": ep, "local": True}
        sid = next(e for e in evs if e["type"] == "session")["session_id"]
        turns = (await client.get(f"{API}/agent/sessions/{sid}")).json()["turns"]
        assert turns[-1]["model"] == {"name": "m", "endpoint": ep, "local": True}
        rows = (await client.get(f"{API}/me/model-use")).json()["rows"]
        assert any(x["feature"] == "assistant" and x["endpoint"] == ep and x["local"] for x in rows)
    finally:
        await _drop_endpoint(ep)


async def test_new_notes_and_tables_inherit_my_switch(client, household):
    await household("per_user")
    assert (await client.put(f"{API}/me/local-models", json={"on": True})).status_code == 200
    n = (await client.post(f"{API}/notes", json={"kind": "text", "title": "Diary", "body": "x"})).json()["note"]
    t = (await client.post(f"{API}/tables", json={"name": "Budget"})).json()["table"]
    assert n["local_only"] is True and t["local_only"] is True
    await client.put(f"{API}/me/local-models", json={"on": False})
    n = (await client.post(f"{API}/notes", json={"kind": "text", "title": "Open", "body": "x"})).json()["note"]
    assert n["local_only"] is False
