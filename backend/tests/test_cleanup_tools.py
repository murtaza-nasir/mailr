# ACT-SURFACES: assistant
"""The assistant's subscriptions and cleanup tools (agent/cleanup_tools.py): subscription.list and
cleanup.groups only read; subscription.unsubscribe and cleanup.plan always wait for the user's
approval, even when a rule allows the class, and the approved request runs exactly what the
card showed. Local-only mail stays out of what a hosted model or a normal token sees."""

import types

from helpers.mailgroups import LIST, _ago, _cleanup_box, _ingest, _labels, _role, _set
from helpers.permissions import _token
from sqlalchemy import select, update

from mailr.core import permissions
from mailr.db import sessionmaker
from mailr.models import Conversation, Job, MailGroupState, Message, PermissionRule


async def _run_job(jid, user_id):
    from mailr.core import tasks

    async with sessionmaker()() as db:
        j = await db.get(Job, jid)
        payload = dict(j.payload)
    await tasks.bulk_conversations(payload, types.SimpleNamespace(job_id=jid, user_id=user_id))
    await _set(Job, jid, status="done")


async def test_list_tools_are_read_only_and_hide_local_only(client, user, raw):
    """subscription.list and cleanup.groups through a token: local-only mail is not shown to a
    token that is not local-only."""
    mid, _ = await _ingest(user, raw("Digest", frm="Weekly <digest@news.example>", to=user.email, extra=LIST))
    await _ingest(user, raw("Secret list", frm="Private <p@private.example>", to=user.email,
                            extra="List-Id: <private.list.example>"))
    async with sessionmaker()() as db:
        await db.execute(update(Message).where(Message.from_email == "p@private.example",
                                               Message.user_id == user.id).values(local_only=True))
        await db.commit()
    agent, _ = await _token(client, ["read"])
    r = await agent.post("/api/v1/agent/tools/list_subscriptions", json={})
    assert r.status_code == 200, r.text
    keys = [i["subscription"] for i in r.json()["result"]["items"]]
    assert "list:weekly.news.example" in keys and "list:private.list.example" not in keys
    r = await agent.post("/api/v1/agent/tools/list_cleanup_groups", json={"by": "list", "where": "all"})
    assert r.status_code == 200, r.text
    keys = [i["key"] for i in r.json()["result"]["items"]]
    assert "weekly.news.example" in keys and "private.list.example" not in keys


async def test_hosted_assistant_does_not_see_local_only(client, user, raw):
    from mailr.agent.cleanup_tools import list_subscriptions
    from mailr.agent.tools import ToolCtx
    from mailr.core.auth import ALL_SCOPES, Principal
    from mailr.models import User

    await _ingest(user, raw("Secret list", frm="Private <p@private.example>", to=user.email,
                            extra="List-Id: <private.list.example>"))
    await _ingest(user, raw("Digest", frm="Weekly <digest@news.example>", to=user.email, extra=LIST))
    async with sessionmaker()() as db:
        await db.execute(update(Message).where(Message.from_email == "p@private.example",
                                               Message.user_id == user.id).values(local_only=True))
        await db.commit()
        u = await db.get(User, user.id)
        p = Principal(user=u, scopes=set(ALL_SCOPES))
        hosted = ToolCtx(db=db, p=p, assistant=True, local_model=False)
        out = await list_subscriptions(hosted, {})
        assert [i["subscription"] for i in out.data["items"]] == ["list:weekly.news.example"]
        assert "local-only" in out.data["note"]
        local = ToolCtx(db=db, p=p, assistant=True, local_model=True)
        out = await list_subscriptions(local, {})
        assert {i["subscription"] for i in out.data["items"]} == {"list:weekly.news.example",
                                                                   "list:private.list.example"}
        assert out.sensitive is True


async def test_cleanup_plan_always_asks_then_runs_exactly_the_card(client, user, raw):
    """cleanup.plan: even with a rule allowing mail.label, the plan waits for approval; nothing
    changes before; the approved plan archives exactly the shown conversations as a bulk job."""
    box = await _cleanup_box(user, raw)
    archive = await _role(user, "archive")
    inbox = await _role(user, "inbox")
    agent, tid = await _token(client, ["read", "label"])
    async with sessionmaker()() as db:
        db.add(PermissionRule(user_id=user.id, subject=f"token:{tid}", action="mail.label", effect="allow",
                              constraints={}))
        await db.commit()
    lab = (await client.post("/api/v1/labels", json={"name": "Bulk archive"})).json()["id"]
    r = await agent.post("/api/v1/agent/tools/propose_cleanup",
                         json={"by": "sender", "key": "news@bulk.example", "action": "label", "label": "Bulk archive",
                               "where": "all"})
    assert r.status_code == 202, r.text
    res = r.json()["result"]
    assert res["status"] == "pending_approval" and res["action"] == "mail.label"
    assert res["plan"]["conversations"] == 2 and res["plan"]["protected"] == {"starred": 1, "important": 1,
                                                                             "replied": 1}
    for name in box:
        assert lab not in await _labels(box[name][1]), "nothing changes before approval"
    aid = res["approval_id"]
    r = await client.post(f"/api/v1/approvals/{aid}/decide", json={"decision": "approve"})
    assert r.status_code == 200, r.text
    await permissions.execute_job({"pending_id": permissions_id(aid)}, None)
    async with sessionmaker()() as db:
        jid = (await db.execute(select(Job.id).where(Job.user_id == user.id, Job.kind == "bulk.conversations")
                                .order_by(Job.id.desc()))).scalars().first()
    await _run_job(jid, user.id)
    for name in ("plain", "inbox"):
        assert lab in await _labels(box[name][1]), name
    for name in ("starred", "important", "replied"):
        assert lab not in await _labels(box[name][1]), name
    assert archive in await _labels(box["plain"][1]) and inbox in await _labels(box["inbox"][1])


def permissions_id(aid):
    from mailr.core import publicid

    return publicid.decode("approval", str(aid)) if not str(aid).isdigit() else int(aid)


async def test_cleanup_plan_trash_asks_as_trash(client, user, raw):
    await _cleanup_box(user, raw)
    agent, _ = await _token(client, ["read", "label"])
    r = await agent.post("/api/v1/agent/tools/propose_cleanup",
                         json={"by": "domain", "key": "bulk.example", "action": "trash"})
    res = r.json()["result"]
    assert res["status"] == "pending_approval" and res["action"] == "mail.trash"
    assert "Bulk plain" in " ".join(str(x) for x in res.get("preview", {}).get("lines", [])) or res["plan"]["samples"]


async def test_unsubscribe_tool_asks_and_records(client, user, raw, monkeypatch):
    """subscription.unsubscribe through an agent waits for approval; the approved request
    unsubscribes from that message and marks the subscription."""
    from mailr.core import unsubscribe

    await _ingest(user, raw("Digest", frm="Weekly <digest@news.example>", to=user.email, extra=LIST, date=_ago(1)))
    sent = []

    async def execute(db, p, m, pl):
        sent.append((m.id, pl["method"]))
        return {"method": pl["method"], "ok": True, "status": 200}

    monkeypatch.setattr(unsubscribe, "execute", execute)
    agent, tid = await _token(client, ["read", "send"])
    async with sessionmaker()() as db:
        db.add(PermissionRule(user_id=user.id, subject=f"token:{tid}", action="mail.unsubscribe", effect="allow",
                              constraints={}))
        await db.commit()
    r = await agent.post("/api/v1/agent/tools/unsubscribe", json={"subscription": "list:weekly.news.example"})
    assert r.status_code == 202, r.text
    res = r.json()["result"]
    assert res["status"] == "pending_approval" and res["action"] == "mail.unsubscribe"
    assert sent == []
    await client.post(f"/api/v1/approvals/{res['approval_id']}/decide", json={"decision": "approve"})
    await permissions.execute_job({"pending_id": permissions_id(res["approval_id"])}, None)
    assert sent and sent[0][1] == "one_click"
    async with sessionmaker()() as db:
        st = (await db.execute(select(MailGroupState).where(MailGroupState.user_id == user.id,
                                                            MailGroupState.kind == "sub"))).scalar_one()
        assert st.key == "list:weekly.news.example" and st.unsubscribed_at is not None


async def test_cleanup_plan_with_a_restricted_token_acts_only_on_what_it_sees(client, user, raw):
    """An approved plan runs as the requesting token: a local-only message in a planned
    conversation is not changed by a token that may not see local-only mail."""
    box = await _cleanup_box(user, raw)
    # A local-only message joins the plain conversation.
    mid2, _ = await _ingest(user, raw("Re: Bulk plain", frm="News <news@bulk.example>", to=user.email,
                                      irt=f"<plain-{user.id}@bulk.example>", date=_ago(399)))
    cid = box["plain"][1]
    async with sessionmaker()() as db:
        m2 = await db.get(Message, mid2)
        m2.local_only = True
        if m2.conversation_id != cid:
            m2.conversation_id = cid
        archive = await _role(user, "archive")
        m2.label_ids = [archive]
        (await db.get(Conversation, cid)).label_ids = [archive]
        await db.commit()
    agent, _ = await _token(client, ["read", "label"])
    r = await agent.post("/api/v1/agent/tools/propose_cleanup",
                         json={"by": "sender", "key": "news@bulk.example", "action": "read"})
    assert r.status_code == 202, r.text
    res = r.json()["result"]
    await client.post(f"/api/v1/approvals/{res['approval_id']}/decide", json={"decision": "approve"})
    await permissions.execute_job({"pending_id": permissions_id(res["approval_id"])}, None)
    async with sessionmaker()() as db:
        jid = (await db.execute(select(Job.id).where(Job.user_id == user.id, Job.kind == "bulk.conversations")
                                .order_by(Job.id.desc()))).scalars().first()
    await _run_job(jid, user.id)
    async with sessionmaker()() as db:
        assert (await db.get(Message, box["plain"][0])).seen is True
        assert (await db.get(Message, mid2)).seen is False, "the token never saw it"
