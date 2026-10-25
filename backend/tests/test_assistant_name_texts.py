"""The name a user gives the assistant (Settings → Ambrose → About you) in the texts the server
writes for them: coded errors, approval cards, model task labels and table history."""

from __future__ import annotations

from httpx import AsyncClient

from mailr import i18n
from mailr.agent import present
from mailr.intel import llm


async def _rename(client, name: str) -> None:
    r = await client.put("/api/v1/assistant/profile", json={"options": {"assistant_name": name}})
    assert r.status_code == 200, r.text
    assert r.json()["options"]["assistant_name"] == name


async def _token_client(client) -> AsyncClient:
    tok = (await client.post("/api/v1/tokens", json={"name": "agent", "scopes": ["read", "notes"]})).json()
    return AsyncClient(transport=client._transport, base_url="http://test",
                       headers={"Authorization": f"Bearer {tok['token']}"})


async def test_errors_name_the_users_assistant(client, user):
    await _rename(client, "Jeeves")
    async with await _token_client(client) as a:
        r = await a.get("/api/v1/tables")
        assert r.status_code == 403
        body = r.json()
        assert body["code"] == "tables.tokens_use_tools" and body["params"] == {"name": "Jeeves"}
        assert body["detail"] == "API tokens can only reach tables through Jeeves's tools"
        # Another language fills the same name from the params.
        assert "Jeeves" in i18n.t("errors.tables.tokens_use_tools", "de", **body["params"])

        r = await a.get("/api/v1/assistant/trusted-addresses")
        assert r.status_code == 403
        assert r.json()["detail"] == "only you can change trusted addresses, in Settings → Jeeves"


async def test_errors_keep_ambrose_by_default(client, user):
    async with await _token_client(client) as a:
        r = await a.get("/api/v1/tables")
    assert r.json()["detail"] == "API tokens can only reach tables through Ambrose's tools"


async def test_forward_card_summary_names_the_assistant(client, user):
    from mailr.db import session_scope

    await _rename(client, "Jeeves")
    pv = {"kind": "forward", "note": "add this to my calendar", "subject": "Lunch", "address": "me@work.example"}
    async with session_scope() as db:
        d = await present.build_display("agent.forwarded_instructions", {}, pv, present.Labels([]), None,
                                        db=db, user_id=user.id)
    assert d["summary"].startswith("You forwarded this from a trusted address. Jeeves carries out")
    assert d["summary_msg"] == {"key": "approvals.forward.summary", "params": {"name": "Jeeves"}}
    assert "Jeeves" in i18n.render(d["summary_msg"], "fr")
    # Without a user (fixtures): the default name.
    d = await present.build_display("agent.forwarded_instructions", {}, pv, present.Labels([]), None)
    assert "Ambrose carries out" in d["summary"]


async def test_model_task_labels_use_the_name(client, user):
    assert llm.task_words("chat")["label"] == "Ambrose and writing"
    assert llm.task_words("chat", name="Jeeves")["label"] == "Jeeves and writing"
    assert "Jeeves's memory" in llm.task_words("embed", name="Jeeves")["desc"]
    await _rename(client, "Jeeves")
    r = await client.get("/api/v1/me/models")
    assert r.status_code == 200
    labels = {t["task"]: t["label"] for t in r.json()["tasks"]}
    assert labels["chat"] == "Jeeves and writing"


def test_table_history_names_the_users_assistant():
    from mailr.tables import history

    by = {"kind": "assistant", "id": 7, "name": "Dana"}
    assert history._by_name(by, "assistant") == "Ambrose"
    assert history._by_name(by, "assistant", {7: "Jeeves"}) == "Jeeves"
