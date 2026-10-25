"""Public ids (core/publicid.py): the codec, and the main API flows with it switched on."""

import random

import pytest
from helpers.public_ids import _set, pub  # noqa: F401

from mailr.core import publicid


def test_codec_roundtrip_range_and_kinds(pub):
    for n in [1, 2, 3, 19846, 93206, 2**40 - 1]:
        c, m = pub.encode("conversation", n), pub.encode("message", n)
        assert 2**52 <= c < 2**53 and 2**52 <= m < 2**53 and c != m
        assert pub.decode("conversation", c) == n and pub.decode("message", m) == n
        assert pub.encode("conversation", c) == c  # idempotent
        with pytest.raises(pub.InvalidId):
            pub.decode("message", c)  # a conversation id is not a message id
    # Neighbours are not neighbours.
    assert abs(pub.encode("conversation", 1001) - pub.encode("conversation", 1000)) > 1000
    # Internal ids are refused once legacy is off; random public-range numbers almost never decode.
    with pytest.raises(pub.InvalidId):
        pub.decode("conversation", 19846)
    rng = random.Random(7)
    hits = 0
    for _ in range(4000):
        try:
            pub.decode("conversation", rng.randrange(2**52, 2**53))
            hits += 1
        except pub.InvalidId:
            pass
    assert hits < 10  # ~1 in 4096 by design (2-bit kind + 10-bit check)


def test_legacy_ids_accepted_only_while_allowed(pub, monkeypatch):
    _set(monkeypatch, "MAILR_PUBLIC_IDS_LEGACY", "true")
    assert pub.decode("conversation", 42) == 42
    _set(monkeypatch, "MAILR_PUBLIC_IDS_LEGACY", "false")
    with pytest.raises(pub.InvalidId):
        pub.decode("conversation", 42)


def test_out_and_into_by_key(pub):
    body = {"id": pub.ConvId(5), "conversation_id": 5, "messages": [{"id": pub.MsgId(9), "message_id": "<hdr@x>"}],
            "label_ids": [1, 2], "draft_id": 9}
    o = pub.out(body)
    assert o["id"] == o["conversation_id"] == pub.encode("conversation", 5)
    assert o["messages"][0]["id"] == o["draft_id"] == pub.encode("message", 9)
    assert o["messages"][0]["message_id"] == "<hdr@x>"  # the Message-ID header stays text
    assert o["label_ids"] == [1, 2]  # other ids are untouched
    back = pub.into({"conversation_id": o["conversation_id"], "ids": [o["id"]]}, {"ids": "conversation"})
    assert back == {"conversation_id": 5, "ids": [5]}


async def test_api_speaks_public_ids(pub, client, user, raw):
    from helpers.core_flow import _ingest

    mid, cid = await _ingest(user, raw("Public id test", body="hello there"))
    lst = (await client.get("/api/v1/conversations", params={"view": "inbox"})).json()
    conv = next(c for c in lst["items"] if c["subject"] == "Public id test")
    pc = conv["id"]
    assert pc == publicid.encode("conversation", cid) and pc >= 2**52
    # Open by public id; the internal id is refused (legacy off).
    d = (await client.get(f"/api/v1/conversations/{pc}")).json()
    pm = d["messages"][0]["id"]
    assert pm == publicid.encode("message", mid) and d["messages"][0]["conversation_id"] == pc
    assert (await client.get(f"/api/v1/conversations/{cid}")).status_code == 404
    assert (await client.get(f"/api/v1/messages/{pm}/body")).status_code == 200
    assert (await client.get(f"/api/v1/messages/{mid}/body")).status_code == 404
    # A made-up public id is a plain 404.
    assert (await client.get(f"/api/v1/conversations/{2**52 + 12345}")).status_code == 404
    # Actions take public ids in the body.
    r = await client.post("/api/v1/conversations/actions", json={"ids": [pc], "action": "star"})
    assert r.status_code == 200 and r.json()["affected"] == 1, r.text
    assert (await client.get(f"/api/v1/conversations/{pc}")).json()["conversation"]["starred"] is True
    # Assistant tools (REST and MCP) take and return public ids.
    t = (await client.post("/api/v1/agent/tools/get_conversation", json={"conversation_id": pc})).json()
    assert t["ok"] and pc in [t["result"].get("conversation_id"), t["result"].get("id")], t
    assert str(cid) not in [str(t["result"].get("conversation_id"))]
    # Citation links carry the short public form, never the internal id.
    from mailr.intel.citations import url_for

    u = url_for(cid, mid)
    assert f"/{cid}" not in u and f"m={pm}" in u


async def test_undo_round_trips_with_public_ids(pub, client, user, raw):
    from helpers.core_flow import _ingest

    await _ingest(user, raw("Undo with public ids", body="x"))
    pc = next(c["id"] for c in (await client.get("/api/v1/conversations", params={"view": "inbox"})).json()["items"]
              if c["subject"] == "Undo with public ids")
    undo = (await client.post("/api/v1/conversations/actions", json={"ids": [pc], "action": "archive"})).json()["undo"]
    assert undo["ids"] == [pc] and all(int(k) >= 2**52 for k in undo["labels"])
    r = await client.post("/api/v1/conversations/actions", json={"ids": [pc], **undo})
    assert r.status_code == 200, r.text
    inbox = (await client.get("/api/v1/conversations", params={"view": "inbox"})).json()["items"]
    assert pc in [c["id"] for c in inbox]


async def test_middleware_keeps_json_post_streams_open(pub):
    """A streaming reply to a JSON POST (the assistant's SSE) waits on receive() for the client to
    leave. After replaying the rewritten body the middleware must pass receive() through, not
    answer "disconnect" (that closed every chat stream at once)."""
    import asyncio

    from mailr.core.publicid_http import PublicIdMiddleware

    seen = []

    async def app(scope, receive, send):
        body = await receive()
        seen.append(body["type"])
        try:
            msg = await asyncio.wait_for(receive(), 0.2)
            seen.append(msg["type"])
        except TimeoutError:
            seen.append("still connected")
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/event-stream")]})
        await send({"type": "http.response.body", "body": b"data: x\n\n"})

    queue = [{"type": "http.request", "body": b'{"message": "hi"}', "more_body": False}]

    async def receive():
        if queue:
            return queue.pop(0)
        await asyncio.sleep(10)
        return {"type": "http.disconnect"}

    sent = []

    async def send(msg):
        sent.append(msg)

    scope = {"type": "http", "method": "POST", "path": "/api/v1/agent/chat", "query_string": b"",
             "headers": [(b"content-type", b"application/json")]}
    await PublicIdMiddleware(app)(scope, receive, send)
    assert seen == ["http.request", "still connected"]
    assert sent[-1]["body"] == b"data: x\n\n"


async def test_cursor_screen_lists_and_old_links_with_public_ids(pub, client, user, raw):
    import base64
    import json

    from helpers.core_flow import _ingest

    ids = [(await _ingest(user, raw(f"Note {i}", frm="Ann <ann@example.org>")))[1] for i in range(3)]
    page = (await client.get("/api/v1/conversations", params={"view": "all", "limit": 1})).json()
    d, cid, *carried = json.loads(base64.urlsafe_b64decode(page["next_cursor"]))
    assert cid >= 2**52  # no internal id in the cursor
    assert carried == [page["total"], False]  # the first page's total, no other number
    nxt = (await client.get("/api/v1/conversations", params={"view": "all", "limit": 1, "cursor": page["next_cursor"]}))
    assert nxt.status_code == 200 and nxt.json()["items"][0]["id"] != page["items"][0]["id"]
    bad = base64.urlsafe_b64encode(json.dumps([d, 12345]).encode()).decode()
    assert (await client.get("/api/v1/conversations", params={"view": "all", "cursor": bad})).status_code == 400
    # A stale id in the screen lists is dropped, not a 404 for the whole request.
    assert pub.into({"ui": {"open_conversation_ids": [1, "x", pub.encode("conversation", ids[0])]}}) == \
        {"ui": {"open_conversation_ids": [ids[0]]}}
    # Old /mail/<view>/<internal> links move to the public form for the owner only.
    from starlette.requests import Request

    from mailr.main import _legacy_mail_link

    def req(cookie):
        return Request({"type": "http", "method": "GET", "path": f"/mail/all/{ids[1]}", "query_string": b"",
                        "headers": [(b"cookie", f"mailr_session={cookie}".encode())] if cookie else []})

    sess = client.cookies.get("mailr_session")
    assert await _legacy_mail_link(f"mail/all/{ids[1]}", req(sess)) == f"/mail/all/{pub.token('conversation', ids[1])}"
    assert await _legacy_mail_link(f"mail/all/{ids[1]}", req(None)) is None
    assert await _legacy_mail_link(f"mail/all/{ids[1]}", req("someone-else")) is None
