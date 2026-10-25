"""Access policies keyed by identity-provider groups (core/access.py, site/docs/admin/access.md):
resolution (union, admin bypass, no group, assigned-groups fallback), groups refreshed at sign-in,
and enforcement on every model path (llm.resolve and its callers, the lab, re-embedding, model
choices, API tokens, MCP, the assistant and standing orders)."""

from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from helpers.access import PW, _client, _policy, _tag, _user  # noqa: F401
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from mailr.core import access
from mailr.db import sessionmaker
from mailr.intel import llm
from mailr.models import AccessPolicy, AuditLog, IdpGroupSeen, ModelEndpoint, User


@pytest.fixture(autouse=True)
def _quiet():
    access.reset_audit_quiet()
    yield
    access.reset_audit_quiet()


@pytest.fixture
async def no_everyone():
    """The built-in Everyone policy removed for one test (users in no group then get nothing), put
    back as it was afterwards."""
    async with sessionmaker()() as db:
        row = (await db.execute(select(AccessPolicy).where(AccessPolicy.group_name == access.EVERYONE))).scalar_one()
        saved = {"group_name": row.group_name, "description": row.description, "models": row.models,
                 "features": row.features, "needs": row.needs}
        await db.delete(row)
        await db.commit()
    yield
    async with sessionmaker()() as db:
        db.add(AccessPolicy(**saved))
        await db.commit()


@asynccontextmanager
async def _endpoints(*specs):
    """Endpoints for one test (names made unique), yielded as {short name: ModelEndpoint}."""
    made = {}
    async with sessionmaker()() as db:
        for short, kw in specs:
            e = ModelEndpoint(name=f"{short}-{_tag()}", base_url=f"http://{short}.invalid/v1", **kw)
            db.add(e)
            await db.flush()
            made[short] = e
        await db.commit()
    try:
        yield made
    finally:
        async with sessionmaker()() as db:
            await db.execute(delete(ModelEndpoint).where(ModelEndpoint.id.in_([e.id for e in made.values()])))
            await db.commit()


async def _refusals(uid: int) -> list[AuditLog]:
    async with sessionmaker()() as db:
        return list((await db.execute(select(AuditLog).where(AuditLog.user_id == uid,
                                                             AuditLog.action == "access.refused"))).scalars())


# ---------------------------------------------------------------- groups and resolution


def test_groups_claim_is_a_set_of_names():
    assert access.normalise_groups(["staff", "family", "staff", " ", 3, None]) == ["family", "staff"]
    assert access.normalise_groups("family") == ["family"]
    assert access.normalise_groups({"x": 1}) == [] and access.normalise_groups(None) == []


async def test_union_of_several_groups(app):
    g1, g2 = f"family-{_tag()}", f"staff-{_tag()}"
    async with _endpoints(("qwen", dict(locality="local", models=["q"], defaults={"chat": "q"})),
                          ("decider", dict(locality="local", adapter="jev", models=["decider"], defaults={})),
                          ("big", dict(locality="hosted", models=["b"], defaults={"chat": "b"}))) as e:
        await _policy(g1, chat=[e["qwen"]], classify=[e["decider"]], default_chat=e["qwen"], assistant=True, lab=True)
        # hosted_models off in this group: its hosted entry does not count, whatever the other group says
        await _policy(g2, chat=[e["big"]], classify=[(e["qwen"], "q")], api_tokens=True)
        u = await _user([g1, g2])
        async with sessionmaker()() as db:
            acc = await access.for_user(db, u)
        assert not acc.unrestricted and acc.policies == sorted([g1, g2])
        assert acc.features["assistant"] and acc.features["lab"] and acc.features["api_tokens"]
        assert not acc.features["choose_models"] and not acc.features["hosted_models"]
        assert set(acc.allowed["chat"]) == {e["qwen"].id}  # "big" is hosted and g2 allows no hosted models
        assert acc.allowed["classify"] == {e["decider"].id: None, e["qwen"].id: {"q"}}
        assert acc.defaults["chat"] == (e["qwen"].id, "q")


async def test_admin_bypass_and_no_group(app, no_everyone):
    admin = await _user(role="admin")
    nobody = await _user()
    async with sessionmaker()() as db:
        assert (await access.for_user(db, admin)).unrestricted
        assert (await access.for_user(db, None)).unrestricted  # a system job
        acc = await access.for_user(db, nobody)
    assert not acc.unrestricted and not any(acc.features.values()) and not acc.allowed["chat"]
    assert acc.as_dict([])["message"] == access.NO_ASSISTANT_MESSAGE


async def test_assigned_groups_count_only_without_the_provider(app, admin_client):
    g = f"family-{_tag()}"
    await _policy(g, assistant=True)
    local = await _user()
    r = await admin_client.put(f"/api/v1/admin/users/{local.id}/groups", json={"groups": [g, g, " "]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["groups"] == [g] and out["source"] == "assigned" and out["access"]["features"]["assistant"]
    sso = await _user(idp=["staff"])
    r = await admin_client.put(f"/api/v1/admin/users/{sso.id}/groups", json={"groups": [g]})
    assert r.status_code == 409  # the provider manages this account's groups
    users = (await admin_client.get("/api/v1/admin/users", params={"q": local.email})).json()["items"]
    assert users[0]["groups"] == [g] and users[0]["groups_source"] == "assigned"


async def test_groups_refreshed_at_every_sign_in(app):
    from mailr.core.users import user_for_oidc_claims

    u = await _user()
    async with sessionmaker()() as db:
        row = await db.get(User, u.id)
        row.oidc_sub = sub = f"sub-{_tag()}"
        await db.commit()
    g = f"family-{_tag()}"
    claims = {"sub": sub, "email": u.email, "email_verified": True, "groups": [g, "staff"]}
    async with sessionmaker()() as db:
        got = await user_for_oidc_claims(db, claims)
        await db.commit()
        assert got.idp_groups == sorted([g, "staff"]) and got.idp_groups_source == "oidc"
    async with sessionmaker()() as db:
        got = await user_for_oidc_claims(db, {**claims, "groups": "staff"})  # left the group at the provider
        await db.commit()
        assert got.idp_groups == ["staff"] and access.group_source(got) == "idp"
        seen = set((await db.execute(select(IdpGroupSeen.name))).scalars())
        assert {g, "staff"} <= seen
    async with sessionmaker()() as db:
        got = await user_for_oidc_claims(db, {k: v for k, v in claims.items() if k != "groups"})
        assert got.idp_groups == ["staff"]  # no claim at all: the stored groups stay


# ---------------------------------------------------------------- enforcement in llm.resolve


async def test_resolve_follows_the_policy(app):
    g = f"family-{_tag()}"
    async with _endpoints(("qwen", dict(locality="local", models=["q"], defaults={})),
                          ("other", dict(locality="local", models=["o"], defaults={"chat": "o", "classify": "o"})),
                          ("decider", dict(locality="local", adapter="jev", models=["decider"], defaults={}))) as e:
        await _policy(g, chat=[(e["qwen"], "q")], classify=[e["decider"]], default_chat=(e["qwen"], "q"),
                      default_classify=e["decider"], assistant=True)
        u = await _user([g])
        async with sessionmaker()() as db:
            row = await db.get(User, u.id)
            # A choice the policy does not allow (and model choice is off): the group default instead.
            row.settings = {"models": {"chat": {"endpoint": e["other"].name, "model": "o"}}}
            await db.commit()
            r = await llm.resolve(db, row, "chat", sensitive=False)
            assert (r.endpoint.name, r.model) == (e["qwen"].name, "q")
            r = await llm.resolve(db, row, "classify", sensitive=True)
            assert r.endpoint.name == e["decider"].name
            # The lab's pinned endpoint must be an allowed one.
            tok = llm.FORCE_ENDPOINT.set(e["other"].name)
            try:
                with pytest.raises(llm.PolicyRefused):
                    await llm.resolve(db, row, "classify", sensitive=False)
            finally:
                llm.FORCE_ENDPOINT.reset(tok)
    assert [a.target for a in await _refusals(u.id)] == ["model:classify"]


async def test_no_group_means_no_model_and_an_audited_refusal(app, client, no_everyone):
    async with _endpoints(("qwen", dict(locality="local", models=["q"], defaults={"chat": "q", "classify": "q"}))):
        u = await _user()
        async with sessionmaker()() as db:
            row = await db.get(User, u.id)
            with pytest.raises(llm.PolicyRefused) as ex:
                await llm.chat(db, row, [{"role": "user", "content": "hi"}], sensitive=False)
            assert str(ex.value) == access.NO_ASSISTANT_MESSAGE
            assert not await llm.available(db, row, "classify", sensitive=False)  # a probe is not audited
            with pytest.raises(llm.NoModel):  # callers that catch NoModel degrade as before
                await llm.resolve(db, row, "classify", sensitive=False)
        logs = await _refusals(u.id)
        assert [a.target for a in logs] == ["model:chat", "model:classify"]
        assert logs[0].inputs["reason"] == "no access policy for the user's groups" and not logs[0].ok


async def test_summaries_translation_and_the_assistant_refuse_without_access(app, user, raw, no_everyone):
    from helpers.core_flow import _ingest

    async with _endpoints(("qwen", dict(locality="local", models=["q"], defaults={"chat": "q", "classify": "q"}))):
        u = await _user()
        mid, cid = await _ingest(u, raw("Budget", to=u.email, body="Numbers please."), roles=["inbox"])
        async with _client(app, u) as c:
            r = await c.get(f"/api/v1/conversations/{cid}/summary", params={"wait": "true"})
            assert r.json()["status"] == "unavailable"
            r = await c.post(f"/api/v1/messages/{mid}/translate", json={"to": "French"})
            assert r.json()["status"] == "unavailable"
            r = await c.post("/api/v1/agent/chat", json={"message": "hello"})
            assert r.status_code == 403 and r.json()["detail"] == access.NO_ASSISTANT_MESSAGE
            r = await c.post("/api/v1/assistant/orders", json={"name": "x", "query": "from:a@example.org",
                                                               "actions": {"star": True}})
            assert r.status_code == 403
            me = (await c.get("/api/v1/me")).json()
            assert me["access"]["message"] == access.NO_ASSISTANT_MESSAGE and not me["access"]["unrestricted"]
            assert me["settings"]["advanced_settings"] is False and me["settings"]["auto_label"] is True


async def test_errands_and_routines_skip_without_the_assistant(app, raw, no_everyone):
    from mailr.agent.loop import run_chat
    from mailr.assistant.orders import assistant_allowed, principal_for

    u = await _user()
    async with sessionmaker()() as db:
        row = await db.get(User, u.id)
        assert not await assistant_allowed(db, row)
        evs = [ev async for ev in run_chat(db, principal_for(row), "summarise my inbox", errand=1)]
    assert evs == [{"type": "error", "message": access.NO_ASSISTANT_MESSAGE, "code": "no_access"}]


# ---------------------------------------------------------------- model choices, lab, re-embed


async def test_model_choice_only_among_allowed_models(app):
    g, g2 = f"family-{_tag()}", f"staff-{_tag()}"
    async with _endpoints(("qwen", dict(locality="local", models=["q", "q2"], defaults={"chat": "q"})),
                          ("other", dict(locality="local", models=["o"], defaults={"chat": "o"}))) as e:
        await _policy(g, chat=[(e["qwen"], "q")], default_chat=(e["qwen"], "q"), assistant=True)
        await _policy(g2, chat=[e["qwen"]], default_chat=(e["qwen"], "q"), assistant=True, choose_models=True)
        locked, free = await _user([g]), await _user([g2])
        async with _client(app, locked) as c:
            out = (await c.get("/api/v1/me/models")).json()
            chat = next(t for t in out["tasks"] if t["task"] == "chat")
            assert out["locked"] and chat["locked"] and [o["endpoint"] for o in chat["options"]] == [e["qwen"].name]
            assert chat["options"][0]["models"] == ["q"] and chat["effective"]["model"] == "q"
            r = await c.put("/api/v1/me/models/chat", json={"endpoint": e["qwen"].name, "model": "q"})
            assert r.status_code == 403 and "administrator" in r.json()["detail"]
        async with _client(app, free) as c:
            assert (await c.put("/api/v1/me/models/chat", json={"endpoint": e["qwen"].name, "model": "q2"})).status_code == 200
            r = await c.put("/api/v1/me/models/chat", json={"endpoint": e["other"].name})
            assert r.status_code == 403


async def test_the_lab_runs_only_the_users_allowed_sorting_models(app, raw, monkeypatch):
    from helpers.core_flow import _ingest

    g, off = f"family-{_tag()}", f"nolab-{_tag()}"
    async with _endpoints(("decider", dict(locality="local", adapter="jev", models=["decider"], defaults={})),
                          ("jev", dict(locality="local", adapter="jev", models=["jev"], defaults={"classify": "jev"}))) as e:
        await _policy(g, classify=[e["decider"]], default_classify=e["decider"], lab=True, assistant=True)
        await _policy(off, classify=[e["decider"]], assistant=True)
        u, nolab = await _user([g]), await _user([off])
        seen = []

        async def fake_chat(db, usr, messages, *, task="chat", sensitive=False, **kw):
            seen.append((await llm.resolve(db, usr, task, sensitive=sensitive)).endpoint.name)
            return {"content": '{"category": "updates"}', "_model": "m"}

        monkeypatch.setattr(llm, "chat", fake_chat)
        mid, _ = await _ingest(u, raw("Slides?", to=u.email, body="Send the slides?"), roles=["inbox"])
        async with _client(app, u) as c:
            assert [x["name"] for x in (await c.get("/api/v1/intel/lab/endpoints")).json()] == [e["decider"].name]
            r = await c.post("/api/v1/intel/lab", json={"message_ids": [mid], "endpoints": [e["jev"].name]})
            assert r.status_code == 403
            r = await c.post("/api/v1/intel/lab", json={"message_ids": [mid]})  # no "all endpoints"
            assert r.status_code == 200, r.text
            assert [x["name"] for x in r.json()["endpoints"]] == [e["decider"].name]
            assert set(seen) <= {e["decider"].name}
        async with _client(app, nolab) as c:
            assert (await c.get("/api/v1/intel/lab/endpoints")).status_code == 403
            assert (await c.post("/api/v1/intel/lab", json={"samples": True})).status_code == 403


async def test_reembed_is_admin_only(app, client, admin_client):
    r = await client.post("/api/v1/intel/reembed")
    assert r.status_code == 403
    r = await admin_client.post("/api/v1/intel/reembed")
    assert r.status_code in (200, 400)  # 400: no embedding endpoint here; never refused for an admin
    # An admin's API token cannot, as for every other administration.
    from helpers.agent import _token_client

    tok = (await admin_client.post("/api/v1/tokens", json={"name": "ops", "scopes": ["admin", "label"]})).json()["token"]
    async with _token_client(admin_client, tok) as t:
        r = await t.post("/api/v1/intel/reembed", params={"all_users": "true"})
        assert r.status_code == 403 and r.json()["code"] == "intel.index_admin_only", r.text


# ---------------------------------------------------------------- tokens and MCP


async def test_tokens_inherit_the_owners_policy(app):
    g_on, g_off = f"agents-{_tag()}", f"family-{_tag()}"
    async with _endpoints(("qwen", dict(locality="local", models=["q"], defaults={"chat": "q"})),
                          ("other", dict(locality="local", models=["o"], defaults={}))) as e:
        await _policy(g_on, chat=[e["other"]], default_chat=e["other"], api_tokens=True, assistant=True)
        await _policy(g_off, chat=[e["qwen"]], assistant=True)
        owner = await _user([g_on])
        async with _client(app, owner) as c:
            r = await c.post("/api/v1/tokens", json={"name": "agent", "scopes": ["read"]})
            assert r.status_code == 200, r.text
            raw_tok = r.json()["token"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                               headers={"Authorization": f"Bearer {raw_tok}"}) as t:
            assert (await t.get("/api/v1/me")).status_code == 200
            # A model call the token causes resolves as the owner: only the owner's allowed endpoint.
            from mailr.core.auth import _token_principal

            async with sessionmaker()() as db:
                p = await _token_principal(db, raw_tok)
                assert (await llm.resolve(db, p.user, "chat", sensitive=False)).endpoint.name == e["other"].name
            # The owner moves to a group without API tokens: the token stops working, MCP too.
            async with sessionmaker()() as db:
                row = await db.get(User, owner.id)
                row.assigned_groups = [g_off]
                await db.commit()
            r = await t.get("/api/v1/me")
            assert r.status_code == 403 and "API tokens" in r.json()["detail"]
            r = await t.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                             headers={"Accept": "application/json, text/event-stream"})
            assert r.status_code in (401, 403)
        async with _client(app, owner) as c:
            assert (await c.post("/api/v1/tokens", json={"name": "x", "scopes": ["read"]})).status_code == 403
        assert any(a.target.startswith("token:") for a in await _refusals(owner.id))


# ---------------------------------------------------------------- admin API


async def test_admin_edits_policies_and_sees_groups(app, admin_client):
    g = f"family-{_tag()}"
    async with _endpoints(("qwen", dict(locality="local", models=["q"], defaults={"chat": "q"})),
                          ("big", dict(locality="hosted", models=["b"], defaults={"chat": "b"}))) as e:
        body = {"group": g, "models": {"chat": {"allowed": [{"endpoint": e["qwen"].name, "model": "q"}],
                                                "default": {"endpoint": e["qwen"].name, "model": "q"}}},
                "features": {"assistant": True, "lab": True}}
        r = await admin_client.post("/api/v1/admin/access/policies", json=body)
        assert r.status_code == 200, r.text
        pol = r.json()
        assert pol["models"]["chat"]["allowed"][0]["endpoint"] == e["qwen"].name and pol["features"]["lab"]
        assert pol["features"]["api_tokens"] is False and pol["features"]["choose_models"] is False
        assert (await admin_client.post("/api/v1/admin/access/policies", json=body)).status_code == 409
        bad = {**body, "models": {"chat": {"allowed": [], "default": {"endpoint": e["qwen"].name}}}}
        assert (await admin_client.put(f"/api/v1/admin/access/policies/{pol['id']}", json=bad)).status_code == 400
        assert (await admin_client.post("/api/v1/admin/access/policies",
                                        json={"group": f"x-{_tag()}", "models": {"embed": {}}})).status_code == 400
        await _user(idp=[g])
        out = (await admin_client.get("/api/v1/admin/access")).json()
        seen = {x["name"]: x for x in out["groups_seen"]}
        assert seen[g]["has_policy"] and seen[g]["users"] == 1 and seen[g]["source"] == "oidc"
        assert {t["task"] for t in out["tasks"]} == {"chat", "classify"}
        assert (await admin_client.delete(f"/api/v1/admin/access/policies/{pol['id']}")).status_code == 200
        async with sessionmaker()() as db:
            assert (await db.execute(select(AuditLog).where(AuditLog.action == "admin.access_policy.add",
                                                            AuditLog.target == g))).first()


async def test_regular_users_cannot_read_or_change_policies(app, client):
    assert (await client.get("/api/v1/admin/access")).status_code == 403
    assert (await client.post("/api/v1/admin/access/policies", json={"group": "x"})).status_code == 403


# ---------------------------------------------------------------- migrations


def test_one_migration_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    assert len(ScriptDirectory.from_config(cfg).get_heads()) == 1


# ---------------------------------------------------------------- the built-in Everyone policy


async def test_everyone_gets_the_assistant_with_local_models(app):
    """A family member in no group has Ambrose on day one: the assistant, the local defaults, no
    hosted models and nothing else."""
    async with _endpoints(("qwen", dict(locality="local", models=["q"], defaults={"chat": "q", "classify": "q"})),
                          ("big", dict(locality="hosted", models=["b"], defaults={"chat": "b"}))) as e:
        u = await _user()
        async with sessionmaker()() as db:
            acc = await access.for_user(db, u)
            assert acc.policies == [access.EVERYONE] and acc.feature("assistant")
            assert not acc.feature("api_tokens") and not acc.feature("hosted_models") and not acc.feature("lab")
            assert e["qwen"].id in acc.allowed["chat"] and e["big"].id not in acc.allowed["chat"]
            r = await llm.resolve(db, await db.get(User, u.id), "chat", sensitive=False)
            assert r.endpoint.locality == "local"
        # A user in a group with a policy gets that policy only.
        g = f"family-{_tag()}"
        await _policy(g, assistant=False)
        member = await _user([g])
        async with sessionmaker()() as db:
            acc = await access.for_user(db, member)
        assert acc.policies == [g] and not acc.feature("assistant")


async def test_the_everyone_policy_is_built_in(app, admin_client):
    out = (await admin_client.get("/api/v1/admin/access")).json()
    pol = next(p for p in out["policies"] if p["builtin"])
    assert pol["group"] == access.EVERYONE and pol["label"] == "Everyone" and pol["features"]["assistant"]
    assert (await admin_client.delete(f"/api/v1/admin/access/policies/{pol['id']}")).status_code == 400
    body = {"group": access.EVERYONE, "description": pol["description"], "models": {},
            "features": {**pol["features"], "assistant": False}}
    assert (await admin_client.post("/api/v1/admin/access/policies", json=body)).status_code == 400
    assert (await admin_client.put(f"/api/v1/admin/access/policies/{pol['id']}",
                                   json={**body, "group": "renamed"})).status_code == 400
    # The admin narrows it: members without a group lose the assistant.
    try:
        r = await admin_client.put(f"/api/v1/admin/access/policies/{pol['id']}", json=body)
        assert r.status_code == 200, r.text
        u = await _user()
        async with sessionmaker()() as db:
            assert not (await access.for_user(db, u)).feature("assistant")
    finally:
        restore = {**body, "features": pol["features"],
                   "models": {t: {"any": pol["models"][t]["any"]} for t in access.POLICY_TASKS}}
        assert (await admin_client.put(f"/api/v1/admin/access/policies/{pol['id']}", json=restore)).status_code == 200
