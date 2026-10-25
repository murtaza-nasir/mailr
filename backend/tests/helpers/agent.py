"""Shared helpers, from test_agent.py."""

import json
import uuid
from contextlib import asynccontextmanager

import pytest
from conftest import mail_date
from httpx import AsyncClient
from sqlalchemy import delete

from mailr.core.ingest import IngestOptions, ingest_raw
from mailr.core.store import user_store
from mailr.db import sessionmaker
from mailr.models import Connector, Identity, ModelEndpoint, User


async def _ingest(user_id, data, connector_id=None, **kw):
    async with sessionmaker()() as db:
        u = await db.get(User, user_id)
        conn = await db.get(Connector, connector_id) if connector_id else None
        store = await user_store(db, u)
        res = await ingest_raw(db, store, data, IngestOptions(connector=conn, **kw))
        await db.commit()
        return res.message.id, res.message.conversation_id


async def _setup_identity(user, data_class="normal"):
    """A fake outgoing connector + identity so drafts and the send gate work."""
    async with sessionmaker()() as db:
        c = Connector(user_id=user.id, kind="imap", name=f"test-{data_class}", mode="mirror", data_class=data_class,
                      enabled=False, status="paused", config={"email": "me@example.com"})
        db.add(c)
        await db.flush()
        i = Identity(user_id=user.id, connector_id=c.id, email=f"me-{c.id}@example.com", name="Me",
                     is_default=True, receives=[])
        db.add(i)
        await db.commit()
        return c.id, i.email


def _token_client(client, tok):
    a = AsyncClient(transport=client._transport, base_url="http://test")
    a.headers["Authorization"] = f"Bearer {tok}"
    return a


async def _seed_thread(user, raw, me):
    q = raw("Q4 budget review", frm="Alice Ng <alice@acme.example>", to=me, msgid="<q1@acme.example>",
            body="Can you send the revised numbers by Friday? Also, are we still meeting on October 12?",
            date=mail_date())
    m1, conv = await _ingest(user.id, q)
    mine = raw("Re: Q4 budget", frm=f"Me <{me}>", to="Alice Ng <alice@acme.example>", msgid="<old1@me>",
               body="Thanks Alice, I will send the draft deck on Monday.", date=mail_date(-7, "09:00"))
    await _ingest(user.id, mine, source="send", roles=["sent"])
    other = raw("Office party", frm="Bob <bob@acme.example>", to=me, body="Cake at 3pm.",
                date=mail_date(1))
    await _ingest(user.id, other)
    return m1, conv


class FakeStream:
    """Stand-in for httpx.AsyncClient inside mailr.intel.llm: scripted SSE model turns."""

    requests: list[dict] = []
    script: list[list[dict]] = []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    @asynccontextmanager
    async def stream(self, method, url, headers=None, json=None):
        FakeStream.requests.append({"url": url, "body": __import__("copy").deepcopy(json)})
        turn = FakeStream.script.pop(0) if FakeStream.script else [{"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}]

        class R:
            status_code = 200

            async def aiter_lines(self):
                for chunk in turn:
                    yield "data: " + __import__("json").dumps(chunk)
                yield "data: [DONE]"

            async def aread(self):
                return b""

        yield R()


def _tool_turn(name, args, call_id="c1"):
    return [{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": call_id, "function": {"name": name, "arguments": json.dumps(args)}}]},
                          "finish_reason": "tool_calls"}]}]


def _text_turn(t):
    return [{"choices": [{"delta": {"content": t}}]}, {"choices": [{"delta": {}, "finish_reason": "stop"}]}]


@pytest.fixture
def fake_llm(monkeypatch):
    from mailr.intel import llm

    monkeypatch.setattr(llm, "_http", lambda r, task, **kw: FakeStream(**kw))
    FakeStream.requests = []
    FakeStream.script = []
    return FakeStream


async def _endpoint(user_id, locality):
    name = f"test-{locality}-{uuid.uuid4().hex[:6]}"
    async with sessionmaker()() as db:
        db.add(ModelEndpoint(name=name, base_url=f"http://{name}.invalid/v1", locality=locality,
                             models=["m"], defaults={"chat": "m"}, enabled=True))
        u = await db.get(User, user_id)
        u.settings = {**(u.settings or {}), "models": {"chat": {"endpoint": name, "model": "m"}}}
        await db.commit()
    return name


async def _drop_endpoint(name):
    async with sessionmaker()() as db:
        await db.execute(delete(ModelEndpoint).where(ModelEndpoint.name == name))
        await db.commit()


def _events(text_body):
    """Parse the SSE wire format (named events) back into the loop's event dicts."""
    out = []
    for block in text_body.replace("\r\n", "\n").split("\n\n"):
        name, data = "message", None
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                try:
                    data = json.loads(line[5:].strip())
                except ValueError:
                    data = None
        if data is None or name in ("tool_call", "tool_result"):  # legacy duplicates of tool_start/tool
            continue
        if name == "token":
            out.append({"type": "delta", "text": data.get("text", "")})
        elif name == "tool":
            out.append({"type": "tool", **data, "result_summary": data.get("summary")})
        elif name == "error":
            out.append({"type": "error", "message": data.get("detail", ""),
                        **({"code": data["code"]} if data.get("code") else {})})
        else:
            out.append({"type": name, **{k: v for k, v in data.items() if k != "type"}})
    return out
