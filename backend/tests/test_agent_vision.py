"""Images for the built-in assistant (agent/vision.py, agent/loop.py, intel/modelcaps.py) and files
attached to a chat message (agent/chat_files.py).

# ACT-SURFACES: api
Covers settings.model_capabilities, settings.assistant_images and agent.chat_attach.
"""

import base64
import io
import json
import uuid

import httpx
import pytest
from helpers.agent import _events, _ingest, _setup_identity, _text_turn, _tool_turn
from PIL import Image
from sqlalchemy import delete, select

from mailr.core import attachfetch
from mailr.db import sessionmaker
from mailr.intel import modelcaps
from mailr.models import ChatAttachment, ModelEndpoint, User


def _mail(files, subject="Photos", body="See attached."):
    b = "b0"
    parts = [f"--{b}\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{body}\r\n"]
    for name, ctype, data in files:
        enc = base64.encodebytes(data).decode().replace("\n", "\r\n")
        parts.append(f"--{b}\r\nContent-Type: {ctype}; name=\"{name}\"\r\nContent-Disposition: attachment; "
                     f"filename=\"{name}\"\r\nContent-Transfer-Encoding: base64\r\n\r\n{enc}\r\n")
    head = (f"From: Me <me@example.com>\r\nTo: me@example.com\r\nSubject: {subject}\r\n"
            f"Message-ID: <{uuid.uuid4().hex}@x.org>\r\nDate: Mon, 21 Sep 2026 10:00:00 +0000\r\n"
            f"MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=\"{b}\"\r\n\r\n")
    return (head + "".join(parts) + f"--{b}--\r\n").encode()


def _jpeg(w=1200, h=900, colour=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), colour).save(buf, "JPEG", quality=80)
    return buf.getvalue()


async def _ep(user_id, locality, *, vision: bool | None, choose=True):
    name = f"vis-{locality}-{uuid.uuid4().hex[:6]}"
    caps = {} if vision is None else {"m": {"vision_manual": vision}}
    async with sessionmaker()() as db:
        db.add(ModelEndpoint(name=name, base_url=f"http://{name}.invalid/v1", locality=locality, models=["m"],
                             defaults={"chat": "m"}, enabled=True, capabilities=caps))
        if choose:
            u = await db.get(User, user_id)
            u.settings = {**(u.settings or {}), "models": {"chat": {"endpoint": name, "model": "m"}}}
        await db.commit()
    return name


async def _drop(*names):
    async with sessionmaker()() as db:
        await db.execute(delete(ModelEndpoint).where(ModelEndpoint.name.in_(names)))
        await db.commit()


async def _setting(user_id, value):
    async with sessionmaker()() as db:
        u = await db.get(User, user_id)
        u.settings = {**(u.settings or {}), "assistant_images": value}
        await db.commit()


def _images_in(body) -> int:
    return sum(1 for m in body["messages"] if isinstance(m.get("content"), list)
               for p in m["content"] if p.get("type") == "image_url")


@pytest.fixture(autouse=True)
def _no_rate_limit():
    attachfetch._CALLS.clear()
    yield
    attachfetch._CALLS.clear()


# ---------------------------------------------------------------- capability detection


def test_reading_each_server_style():
    # OpenAI-compatible /models (OpenRouter, recent llama.cpp)
    assert modelcaps.from_models_entry({"id": "q", "architecture": {"input_modalities": ["text", "image"]}}) is True
    assert modelcaps.from_models_entry({"id": "q", "architecture": {"input_modalities": ["text"]}}) is False
    assert modelcaps.from_models_entry({"id": "q", "capabilities": ["completion", "multimodal"]}) is True
    assert modelcaps.from_models_entry({"id": "q", "capabilities": ["completion"]}) is False
    assert modelcaps.from_models_entry({"id": "q", "capabilities": {"vision": True}}) is True
    assert modelcaps.from_models_entry({"id": "q", "object": "model", "owned_by": "x"}) is None
    # llama.cpp /props
    assert modelcaps.from_props({"modalities": {"vision": True, "audio": False}}) is True
    assert modelcaps.from_props({"modalities": {"vision": False}}) is False
    assert modelcaps.from_props({"build_info": "b1"}) is None
    # Ollama /api/show
    assert modelcaps.from_ollama_show({"capabilities": ["completion", "vision", "tools"]}) is True
    assert modelcaps.from_ollama_show({"capabilities": ["completion", "tools"]}) is False
    assert modelcaps.root_of("http://h:5001/v1/") == "http://h:5001"


def _fake_server(routes):
    """outbound.client replaced by a MockTransport answering `routes` {(method, path): json}."""
    seen = []

    def handler(req: httpx.Request):
        seen.append((req.method, req.url.path, dict(req.url.params), req.content))
        body = routes.get((req.method, req.url.path))
        if body is None:
            return httpx.Response(404, text="not found")
        return httpx.Response(200, json=body(req) if callable(body) else body)

    def client(service, **kw):
        kw.pop("local_only", None)
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw)

    return client, seen


@pytest.mark.parametrize("style", ["models", "props", "ollama", "silent"])
async def test_detect_each_server_style(monkeypatch, style):
    routes = {
        "models": {("GET", "/v1/models"): {"data": [
            {"id": "qwen3.8-flash-next-iq3_s", "architecture": {"input_modalities": ["text", "image"]}},
            {"id": "text-only", "architecture": {"input_modalities": ["text"]}}]}},
        "props": {("GET", "/v1/models"): {"data": [{"id": "qwen3.8-flash-next-iq3_s"}, {"id": "text-only"}]},
                  ("GET", "/props"): lambda r: {"modalities": {"vision": r.url.params.get("model") != "text-only"}}},
        "ollama": {("GET", "/v1/models"): {"data": [{"id": "qwen3.8-flash-next-iq3_s"}, {"id": "text-only"}]},
                   ("POST", "/api/show"): lambda r: {"capabilities": ["completion"] + (
                       [] if json.loads(r.content)["model"] == "text-only" else ["vision"])}},
        "silent": {("GET", "/v1/models"): {"data": [{"id": "qwen3.8-flash-next-iq3_s"}, {"id": "text-only"}]}},
    }[style]
    client, _seen = _fake_server(routes)
    monkeypatch.setattr(modelcaps.outbound, "client", client)
    ep = ModelEndpoint(name="x", base_url="http://models.example/v1", locality="local", adapter="openai",
                       models=["qwen3.8-flash-next-iq3_s", "text-only"], defaults={}, capabilities={})
    found = await modelcaps.detect(ep, None)
    if style == "silent":
        assert found == {"qwen3.8-flash-next-iq3_s": None, "text-only": None}
    else:
        assert found == {"qwen3.8-flash-next-iq3_s": True, "text-only": False}
    modelcaps.apply_detected(ep, found)
    assert modelcaps.can_view_images(ep, "qwen3.8-flash-next-iq3_s") is (style != "silent")
    # The admin's override wins over detection, and None goes back to it.
    modelcaps.set_manual(ep, "text-only", True)
    assert modelcaps.can_view_images(ep, "text-only") is True
    modelcaps.apply_detected(ep, {"text-only": False})  # a refresh keeps the override
    assert modelcaps.can_view_images(ep, "text-only") is True
    assert modelcaps.describe(ep)["text-only"] == {"vision": True, "source": "manual",
                                                    "detected": False}
    modelcaps.set_manual(ep, "text-only", None)
    assert modelcaps.can_view_images(ep, "text-only") is False


async def test_admin_detects_on_add_and_overrides(admin_client, monkeypatch):
    client, seen = _fake_server({("GET", "/v1/models"): {"data": [
        {"id": "vl", "architecture": {"input_modalities": ["text", "image"]}}, {"id": "plain"}]}})
    monkeypatch.setattr(modelcaps.outbound, "client", client)
    name = f"caps-{uuid.uuid4().hex[:6]}"
    r = await admin_client.post("/api/v1/admin/models", json={
        "name": name, "base_url": "http://127.0.0.1:9/v1", "locality": "local", "models": ["vl", "plain"]})
    assert r.status_code == 200, r.text
    ep = r.json()
    assert ep["capabilities"]["vl"] == {"vision": True, "source": "detected", "detected": True}
    assert ep["capabilities"]["plain"] == {"vision": False, "source": "unknown", "detected": None}
    try:
        r = await admin_client.put(f"/api/v1/admin/models/{ep['id']}/capabilities", json={"model": "plain", "vision": True})
        assert r.status_code == 200 and r.json()["capabilities"]["plain"]["source"] == "manual"
        assert r.json()["capabilities"]["plain"]["vision"] is True
        r = await admin_client.put(f"/api/v1/admin/models/{ep['id']}/capabilities", json={"model": "nope", "vision": True})
        assert r.status_code == 400
        # Detect again: the override stays.
        n = len(seen)
        r = await admin_client.post(f"/api/v1/admin/models/{ep['id']}/detect")
        assert r.status_code == 200 and len(seen) > n
        assert r.json()["capabilities"]["plain"]["vision"] is True
        listed = next(e for e in (await admin_client.get("/api/v1/admin/models")).json() if e["id"] == ep["id"])
        assert listed["capabilities"]["vl"]["vision"] is True
    finally:
        await _drop(name)


async def test_unchecked_endpoints_are_detected_by_the_job(monkeypatch):
    """Existing endpoints need no Detect: the job (queued at worker start and daily) asks about models
    nobody has checked; an override stays; an unreachable server stays unknown and is asked again."""
    from mailr.core.jobs import _HANDLERS
    from mailr.models import Job

    tag = uuid.uuid4().hex[:6]
    up, over, down = f"auto-up-{tag}", f"auto-over-{tag}", f"auto-down-{tag}"
    reachable = {"ok": False}

    def handler(req: httpx.Request):
        if req.url.host == f"{down}.example" and not reachable["ok"]:
            raise httpx.ConnectError("refused", request=req)
        if req.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [
                {"id": "vl", "architecture": {"input_modalities": ["text", "image"]}},
                {"id": "plain", "architecture": {"input_modalities": ["text"]}}]})
        return httpx.Response(404, text="no")

    def client(service, **kw):
        kw.pop("local_only", None)
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setattr(modelcaps.outbound, "client", client)
    async with sessionmaker()() as db:
        for name, caps in ((up, {}), (over, {"vl": {"vision_manual": False}}), (down, {})):
            db.add(ModelEndpoint(name=name, base_url=f"http://{name}.example/v1", locality="local",
                                 models=["vl", "plain"], defaults={}, enabled=True, capabilities=caps))
        await db.commit()
    try:
        assert modelcaps.JOB in _HANDLERS
        # Queued once, however often the worker starts.
        async with sessionmaker()() as db:
            await modelcaps.queue_check(db)
            await modelcaps.queue_check(db)
            await db.commit()
            queued = (await db.execute(select(Job).where(Job.kind == modelcaps.JOB, Job.status == "queued"))).scalars().all()
            assert len(queued) == 1
            await db.execute(delete(Job).where(Job.kind == modelcaps.JOB))
            await db.commit()
        await _HANDLERS[modelcaps.JOB]({}, None)

        async def caps(name):
            async with sessionmaker()() as db:
                return (await db.execute(select(ModelEndpoint).where(ModelEndpoint.name == name))).scalar_one()

        e = await caps(up)
        assert modelcaps.can_view_images(e, "vl") and not modelcaps.can_view_images(e, "plain")
        assert modelcaps.unchecked(e) == []
        e = await caps(over)
        assert modelcaps.entry(e, "vl") == {"vision_manual": False}  # the override, untouched and not asked
        assert modelcaps.can_view_images(e, "vl") is False
        assert modelcaps.entry(e, "plain")["vision"] is False
        e = await caps(down)
        assert modelcaps.unchecked(e) == ["vl", "plain"] and not e.capabilities
        # The next run (next start, or the daily one) asks again; now the server answers.
        reachable["ok"] = True
        res = await modelcaps.check_unknown()
        assert down in res and up not in res and over not in res
        assert modelcaps.can_view_images(await caps(down), "vl")
    finally:
        await _drop(up, over, down)


# ---------------------------------------------------------------- the strip in llm


def test_images_are_stripped_for_a_model_that_cannot_view_them():
    ep = ModelEndpoint(name="x", base_url="http://x/v1", locality="local", models=["m"], capabilities={})
    msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"},
                                         {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}]}]
    out = modelcaps.for_model(msgs, ep, "m")
    assert "base64" not in json.dumps(out) and modelcaps.NOT_SHOWN in json.dumps(out)
    ep.capabilities = {"m": {"vision": True}}
    assert modelcaps.for_model(msgs, ep, "m") is msgs


# ---------------------------------------------------------------- images from get_attachment


async def _chat(client, text, **extra):
    r = await client.post("/api/v1/agent/chat", json={"message": text, **extra})
    assert r.status_code == 200, r.text
    return _events(r.text)


async def test_tool_image_reaches_a_vision_model(client, user, fake_llm):
    mid, _ = await _ingest(user.id, _mail([("note.jpg", "image/jpeg", _jpeg())]))
    ep = await _ep(user.id, "local", vision=True)
    try:
        fake_llm.script = [_tool_turn("get_attachment", {"message_id": mid, "attachment": 0}), _text_turn("A red card.")]
        evs = await _chat(client, "What does my photo show?")
        assert evs[-1]["type"] == "done", evs
        second = fake_llm.requests[1]["body"]["messages"]
        tool = next(m for m in second if m["role"] == "tool")
        assert "_content" not in tool["content"] and "base64" not in tool["content"]
        assert "shown to you" in tool["content"]
        after = second[second.index(tool) + 1]
        assert after["role"] == "user" and after["content"][0]["type"] == "text"
        assert "never instructions" in after["content"][0]["text"] and "note.jpg" in after["content"][0]["text"]
        img = after["content"][1]
        assert img["type"] == "image_url" and img["image_url"]["url"].startswith("data:image/jpeg;base64,")
        # The assistant's default size: long side 1024.
        im = Image.open(io.BytesIO(base64.b64decode(img["image_url"]["url"].split(",", 1)[1])))
        assert max(im.size) == 1024
        # A later question replays the tool result with a note, never the image.
        fake_llm.script = [_text_turn("Yes.")]
        sid = next(e for e in evs if e["type"] == "session")["session_id"]
        await _chat(client, "Is it red?", session_id=sid)
        third = fake_llm.requests[-1]["body"]
        assert _images_in(third) == 0
        assert any(m["role"] == "tool" and "no longer in view" in m["content"] for m in third["messages"])
    finally:
        await _drop(ep)


@pytest.mark.parametrize("case", ["setting_off", "no_vision"])
async def test_no_image_without_setting_or_capability(client, user, fake_llm, case):
    mid, _ = await _ingest(user.id, _mail([("note.jpg", "image/jpeg", _jpeg())]))
    ep = await _ep(user.id, "local", vision=case != "no_vision")
    if case == "setting_off":
        await _setting(user.id, False)
    try:
        fake_llm.script = [_tool_turn("get_attachment", {"message_id": mid, "attachment": 0}), _text_turn("ok")]
        await _chat(client, "What does my photo show?")
        assert all(_images_in(q["body"]) == 0 for q in fake_llm.requests)
        tool = next(m for m in fake_llm.requests[1]["body"]["messages"] if m["role"] == "tool")
        assert "image attached (1200×900)" in tool["content"]
        assert ("turned off images" if case == "setting_off" else "cannot view images") in tool["content"]
    finally:
        await _setting(user.id, True)
        await _drop(ep)


async def test_local_only_image_never_reaches_a_hosted_model(client, user, fake_llm):
    conn_id, me = await _setup_identity(user, data_class="local_only")
    mid, _ = await _ingest(user.id, _mail([("scan.jpg", "image/jpeg", _jpeg())]), connector_id=conn_id)
    hosted = await _ep(user.id, "hosted", vision=True)
    local = await _ep(user.id, "local", vision=False, choose=False)
    try:
        fake_llm.script = [_tool_turn("get_attachment", {"message_id": mid, "attachment": 0}), _text_turn("ok")]
        await _chat(client, "What is in the scan?")
        assert all(_images_in(q["body"]) == 0 for q in fake_llm.requests)
        assert all(hosted not in q["url"] for q in fake_llm.requests[1:])
        tool = next(m for m in fake_llm.requests[1]["body"]["messages"] if m["role"] == "tool")
        assert "cannot view images" in tool["content"]
        # With a local model that can view images, the image goes there, and only there.
        await _drop(local)
        local = await _ep(user.id, "local", vision=True, choose=False)
        fake_llm.requests.clear()
        fake_llm.script = [_tool_turn("get_attachment", {"message_id": mid, "attachment": 0}), _text_turn("ok")]
        await _chat(client, "What is in the scan?")
        with_images = [q for q in fake_llm.requests if _images_in(q["body"])]
        assert with_images and all(local in q["url"] for q in with_images)
        assert all(hosted not in q["url"] for q in fake_llm.requests[1:])
    finally:
        await _drop(hosted, local)


async def test_at_most_four_images_per_question(client, user, fake_llm):
    mid, _ = await _ingest(user.id, _mail([(f"p{i}.jpg", "image/jpeg", _jpeg(300 + i, 200)) for i in range(5)]))
    ep = await _ep(user.id, "local", vision=True)
    try:
        calls = [{"index": i, "id": f"c{i}", "function": {"name": "get_attachment",
                                                            "arguments": json.dumps({"message_id": mid, "attachment": i})}}
                 for i in range(5)]
        fake_llm.script = [[{"choices": [{"delta": {"tool_calls": calls}, "finish_reason": "tool_calls"}]}],
                           _text_turn("Five photos.")]
        await _chat(client, "Describe all five photos")
        body = fake_llm.requests[1]["body"]
        assert _images_in(body) == 4
        tools = [m for m in body["messages"] if m["role"] == "tool"]
        assert "already shown 4 images" in tools[4]["content"]
        # The prompt budget counts an image as a few hundred tokens, not its base64.
        from mailr.agent.loop import prompt_tokens

        assert prompt_tokens(body["messages"]) < 20_000
    finally:
        await _drop(ep)


async def test_rest_and_mcp_callers_keep_the_image(user):
    """The REST tool API (ToolCtx.images, no vision callback) still returns `_content`."""
    from mailr.agent.tools import ToolCtx, run_tool
    from mailr.core.auth import ALL_SCOPES, Principal

    mid, _ = await _ingest(user.id, _mail([("a.jpg", "image/jpeg", _jpeg())]))
    async with sessionmaker()() as db:
        p = Principal(user=await db.get(User, user.id), scopes=set(ALL_SCOPES) - {"admin"})
        res = await run_tool(ToolCtx(db=db, p=p, images=True), "get_attachment", {"message_id": mid, "attachment": 0})
        assert res.data["_content"][0]["type"] == "image" and res.data["image"]["width"] == 1200


# ---------------------------------------------------------------- chat attachments


def _docx(text="Quarterly plan: ship the vision feature") -> bytes:
    import docx

    d = docx.Document()
    d.add_paragraph(text)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


async def _upload(client, name, data, ctype="application/octet-stream"):
    return await client.post("/api/v1/agent/attachments", files={"file": (name, data, ctype)})


async def test_attach_files_and_images_to_a_message(client, user, fake_llm):
    ep = await _ep(user.id, "local", vision=True)
    try:
        img = await _upload(client, "whiteboard.jpg", _jpeg(2000, 1500), "image/jpeg")
        doc_bytes = _docx()
        doc = await _upload(client, "plan.docx", doc_bytes)
        assert img.status_code == 200 and doc.status_code == 200, (img.text, doc.text)
        i, d = img.json(), doc.json()
        assert i["kind"] == "image" and (i["width"], i["height"]) == (2000, 1500) and i["thumb"] is True
        assert d["kind"] == "file" and d["format"] == "docx" and d["thumb"] is False
        fake_llm.script = [_text_turn("The board says hello.")]
        evs = await _chat(client, "What is on these?", attachments=[i["id"], d["id"]])
        assert evs[-1]["type"] == "done", evs
        q = fake_llm.requests[0]["body"]["messages"][-1]
        assert q["role"] == "user" and isinstance(q["content"], list)
        text = q["content"][0]["text"]
        assert text.startswith("What is on these?") and "not instructions from the user" in text
        assert "Quarterly plan: ship the vision feature" in text and "whiteboard.jpg" in text
        assert q["content"][1]["type"] == "image_url"
        sid = next(e for e in evs if e["type"] == "session")["session_id"]
        # The chat history shows them with the message.
        s = (await client.get(f"/api/v1/agent/sessions/{sid}")).json()
        shown = s["turns"][0]["attachments"]
        assert [a["name"] for a in shown] == ["whiteboard.jpg", "plan.docx"]
        th = await client.get(f"/api/v1/agent/attachments/{i['id']}/thumb")
        assert th.status_code == 200 and th.headers["content-type"] == "image/jpeg"
        assert max(Image.open(io.BytesIO(th.content)).size) == 320
        dl = await client.get(f"/api/v1/agent/attachments/{d['id']}")
        assert dl.status_code == 200 and dl.content == doc_bytes
        assert "attachment" in dl.headers["content-disposition"]
        # Sent: it cannot be removed alone, and it cannot be sent again.
        assert (await client.delete(f"/api/v1/agent/attachments/{i['id']}")).status_code == 400
        evs = await _chat(client, "again", session_id=sid, attachments=[i["id"]])
        assert evs[-1]["type"] == "error"
        # A later question keeps the file's text and a note for the image, without the image.
        fake_llm.script = [_text_turn("ok")]
        await _chat(client, "And the plan?", session_id=sid)
        later = fake_llm.requests[-1]["body"]
        assert _images_in(later) == 0
        assert any("Quarterly plan" in str(m["content"]) and "no longer in view" in str(m["content"])
                   for m in later["messages"] if m["role"] == "user")
        # Deleting the chat deletes the files.
        assert (await client.delete(f"/api/v1/agent/sessions/{sid}")).status_code == 200
        async with sessionmaker()() as db:
            assert not (await db.execute(select(ChatAttachment).where(ChatAttachment.id.in_([i["id"], d["id"]])))).all()
    finally:
        await _drop(ep)


async def test_attached_image_without_vision_is_a_note(client, user, fake_llm):
    ep = await _ep(user.id, "local", vision=False)
    try:
        i = (await _upload(client, "a.png", _jpeg(640, 480), "image/png")).json()
        fake_llm.script = [_text_turn("ok")]
        await _chat(client, "", attachments=[i["id"]])
        q = fake_llm.requests[0]["body"]["messages"][-1]
        assert isinstance(q["content"], str) and "image attached (640×480); the model cannot view images" in q["content"]
    finally:
        await _drop(ep)


async def test_upload_checks(client, user, monkeypatch):
    from mailr.agent import chat_files

    r = await _upload(client, "setup.exe", b"MZ\x90\x00" + b"\x00" * 100)
    assert r.status_code == 415 and r.json()["code"] == "assistant.attachment_type"
    r = await _upload(client, "IMG.heic", b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64, "image/heic")
    assert r.status_code == 415 and r.json()["code"] == "assistant.attachment_heic"
    r = await _upload(client, "empty.txt", b"", "text/plain")
    assert r.status_code == 422
    monkeypatch.setattr(chat_files, "MAX_BYTES", 1000)
    r = await _upload(client, "big.txt", b"x" * 1001, "text/plain")
    assert r.status_code == 413 and r.json()["code"] == "assistant.attachment_too_large"
    monkeypatch.setattr(chat_files, "MAX_BYTES", 15 * 1024 * 1024)
    r = await _upload(client, "broken.docx", b"PK\x03\x04 not really a zip")
    assert r.status_code == 422 and r.json()["code"] == "assistant.attachment_unreadable"
    ok = (await _upload(client, "notes.txt", b"plain words", "text/plain")).json()
    # An unsent upload can be removed; another user's cannot be seen.
    assert (await client.delete(f"/api/v1/agent/attachments/{ok['id']}")).status_code == 200
    assert (await client.get(f"/api/v1/agent/attachments/{ok['id']}")).status_code == 404


async def test_another_users_upload_cannot_be_sent(client, user, fake_llm):
    from mailr.core.users import create_user

    async with sessionmaker()() as db:
        other = await create_user(db, f"o-{uuid.uuid4().hex[:8]}@example.com", "Other", password="test-password-123")
        a = ChatAttachment(id=uuid.uuid4().hex, user_id=other.id, filename="x.txt", content_type="text/plain", size=1,
                           kind="file", data=b"x", text="x", info={})
        db.add(a)
        await db.commit()
    evs = await _chat(client, "read it", attachments=[a.id])
    assert evs[-1]["type"] == "error" and not fake_llm.requests
    assert (await client.get(f"/api/v1/agent/attachments/{a.id}")).status_code == 404
    async with sessionmaker()() as db:
        assert (await db.get(ChatAttachment, a.id)).message_id is None  # still the other user's, unsent


# ---------------------------------------------------------------- document formats


def _zip(entries: dict[str, bytes]) -> bytes:
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in entries.items():
            z.writestr(n, b)
    return buf.getvalue()


def _xlsx(rows: int, cols: int, short=False) -> bytes:
    import openpyxl

    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet("Big")
    for r in range(rows):
        ws.append([c if short else f"r{r}c{c}" for c in range(cols)])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _pptx() -> bytes:
    from pptx import Presentation

    p = Presentation()
    s = p.slides.add_slide(p.slide_layouts[1])
    s.shapes.title.text = "Roadmap"
    s.placeholders[1].text = "Ship images"
    s.notes_slide.notes_text_frame.text = "Say it slowly"
    buf = io.BytesIO()
    p.save(buf)
    return buf.getvalue()


def _odf(kind: str) -> bytes:
    from odf import draw, presentation, style
    from odf.opendocument import OpenDocumentPresentation, OpenDocumentSpreadsheet, OpenDocumentText
    from odf.table import Table, TableCell, TableRow
    from odf.text import P

    if kind == "odt":
        d = OpenDocumentText()
        d.text.addElement(P(text="Minutes of the meeting"))
    elif kind == "ods":
        d = OpenDocumentSpreadsheet()
        t = Table(name="Budget")
        r = TableRow()
        c = TableCell()
        c.addElement(P(text="rent"))
        r.addElement(c)
        r.addElement(TableCell(numbercolumnsrepeated=16000))  # empty cells to the sheet's edge
        t.addElement(r)
        t.addElement(TableRow(numberrowsrepeated=1048000))  # empty rows to the sheet's end
        d.spreadsheet.addElement(t)
    else:
        d = OpenDocumentPresentation()
        pl = style.PageLayout(name="pl")
        d.automaticstyles.addElement(pl)
        mp = style.MasterPage(name="m", pagelayoutname=pl)
        d.masterstyles.addElement(mp)
        page = draw.Page(masterpagename=mp)
        for text, holder in (("Agenda", page), ("Speak up", None)):
            fr = draw.Frame(width="10cm", height="2cm", x="1cm", y="1cm")
            tb = draw.TextBox()
            tb.addElement(P(text=text))
            fr.addElement(tb)
            if holder is not None:
                holder.addElement(fr)
            else:
                n = presentation.Notes()
                n.addElement(fr)
                page.addElement(n)
        d.presentation.addElement(page)
    buf = io.BytesIO()
    d.write(buf)
    return buf.getvalue()


@pytest.mark.parametrize("name,make,expect", [
    ("a.docx", _docx, ["Quarterly plan"]),
    ("a.xlsx", lambda: _xlsx(2, 3), ["--- sheet: Big ---", "r0c0,r0c1,r0c2"]),
    ("a.pptx", _pptx, ["--- slide 1 ---", "Roadmap", "Ship images", "Notes: Say it slowly"]),
    ("a.odt", lambda: _odf("odt"), ["Minutes of the meeting"]),
    ("a.ods", lambda: _odf("ods"), ["--- sheet: Budget ---", "rent"]),
    ("a.odp", lambda: _odf("odp"), ["--- slide 1 ---", "Agenda", "Notes: Speak up"]),
    ("a.rtf", lambda: rb"{\rtf1\ansi Dear {\b Sam}\par see you}", ["Dear Sam", "see you"]),
])
async def test_document_formats(name, make, expect):
    data = make()
    for ctype in ("application/octet-stream", ""):
        res = await attachfetch.extract(data, ctype, name, None)
        assert res["kind"] == "text" and res["format"] == name.split(".")[1], res
        for e in expect:
            assert e in res["text"], (e, res["text"])
    if name == "a.ods":  # the repeated empty cells and rows were never expanded
        assert len(res["text"]) < 100


async def test_giant_sheet_is_capped():
    from mailr.core import attachwork

    res = await attachfetch.extract(_xlsx(attachwork.MAX_ROWS + 500, 3, short=True), "", "long.xlsx", None)
    lines = res["text"].splitlines()
    assert len(lines) == attachwork.MAX_ROWS + 1 and f"only the first {attachwork.MAX_ROWS} rows" in res["note"]
    res = await attachfetch.extract(_xlsx(10, attachwork.MAX_COLS + 40, short=True), "", "wide.xlsx", None)
    assert max(len(line.split(",")) for line in res["text"].splitlines()[1:]) == attachwork.MAX_COLS
    # Long cells: the text stops at the cap, said in the note.
    res = await attachfetch.extract(_xlsx(1500, 50), "", "big.xlsx", None)
    assert res["truncated"] and len(res["text"]) <= attachfetch.TEXT_CAP and "truncated" in res["note"]


async def test_zip_bomb_and_malformed_documents():
    bomb = _zip({"[Content_Types].xml": b"<x/>", "word/document.xml": b"<" + b"a" * (60 * 1024 * 1024) + b"/>"})
    assert len(bomb) < 200_000
    with pytest.raises(attachfetch.AttachmentError, match="too large once unpacked|ZIP bomb"):
        await attachfetch.extract(bomb, "", "bomb.docx", None)
    ratio = _zip({"word/document.xml": b"\x00" * (3 * 1024 * 1024)})
    with pytest.raises(attachfetch.AttachmentError, match="ZIP bomb"):
        await attachfetch.extract(ratio, "", "r.docx", None)
    with pytest.raises(attachfetch.AttachmentError):
        await attachfetch.extract(b"PK\x03\x04garbage" * 10, "", "x.xlsx", None)
    with pytest.raises(attachfetch.AttachmentError):
        await attachfetch.extract(_zip({"hello.txt": b"hi"}), "", "x.pptx", None)
    many = _zip({f"f{i}.xml": b"x" for i in range(5001)})
    with pytest.raises(attachfetch.AttachmentError, match="too many parts"):
        await attachfetch.extract(many, "", "x.odt", None)
