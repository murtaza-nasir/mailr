# ACT-SURFACES: api
"""agent.dictate: dictation as the model task `transcribe` (agent/transcribe.py): off without a speech
endpoint, MAILR_ASR_URL as a built-in local endpoint, the clip passed through unchanged to
/v1/audio/transcriptions, limits, refusal of a local endpoint that resolves to a public address,
errors without detail, nothing stored, features.transcribe in GET /me, and the context's data class
(a hosted speech service for normal contexts, never for local-only ones or a missing context)."""

import httpx
import pytest
from sqlalchemy import select

from mailr.agent import transcribe
from mailr.config import get_settings
from mailr.db import sessionmaker
from mailr.models import AuditLog

CLIP = b"\x00\x00\x00\x18ftypM4A " + b"\x01" * 4096


class FakeAsr:
    def __init__(self):
        self.calls: list[dict] = []
        self.status = 200
        self.text = "move this to Friday and email Dana"

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = request.read()
        self.calls.append({"url": str(request.url), "body": body, "type": request.headers.get("content-type", "")})
        if self.status != 200:
            return httpx.Response(self.status, text="internal detail that must not leak")
        return httpx.Response(200, json={"text": f"  {self.text} "})


@pytest.fixture
def asr(monkeypatch):
    fake = FakeAsr()
    s = get_settings()
    monkeypatch.setattr(s, "asr_url", "http://192.168.1.41:9000/")
    transcribe.use_transport(httpx.MockTransport(fake.handler))
    yield fake
    transcribe.use_transport(None)


async def test_off_without_url(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "asr_url", "")
    me = (await client.get("/api/v1/me")).json()
    assert me["features"]["transcribe"] is False
    r = await client.post("/api/v1/assistant/transcribe", files={"file": ("clip.m4a", CLIP, "audio/mp4")})
    assert r.status_code == 404


async def test_clip_goes_to_the_speech_server_and_text_comes_back(client, user, asr):
    me = (await client.get("/api/v1/me")).json()
    assert me["features"]["transcribe"] is True
    r = await client.post("/api/v1/assistant/transcribe", files={"file": ("clip.m4a", CLIP, "audio/mp4")},
                          data={"duration_ms": "4200", "language": "en"})
    assert r.status_code == 200, r.text
    assert r.json() == {"text": "move this to Friday and email Dana"}
    call = asr.calls[-1]
    assert call["url"] == "http://192.168.1.41:9000/v1/audio/transcriptions"
    assert CLIP in call["body"] and b'name="model"' in call["body"] and b"whisper-1" in call["body"]
    assert b'name="language"' in call["body"] and call["type"].startswith("multipart/form-data")
    # Nothing keeps the words: no audit row (a token's call is audited by size and length only).
    async with sessionmaker()() as db:
        rows = (await db.execute(select(AuditLog).where(AuditLog.user_id == user.id))).scalars().all()
    assert all("Friday" not in str(r.inputs) + str(r.result) for r in rows)


async def test_browser_clips_pass_through_with_their_format(client, asr):
    """agent.dictate from the web: Chrome sends WebM/Opus, Firefox Ogg/Opus, both with a codecs
    parameter and often named "blob"; the speech server gets the clip unchanged, named by its type."""
    webm = b"\x1aE\xdf\xa3" + b"\x02" * 4096
    for ctype, ext in (("audio/webm;codecs=opus", "webm"), ("audio/ogg;codecs=opus", "ogg"), ("audio/mp4", "m4a")):
        r = await client.post("/api/v1/assistant/transcribe", files={"file": ("blob", webm, ctype)}, data={"duration_ms": "3000"})
        assert r.status_code == 200, r.text
        call = asr.calls[-1]
        assert webm in call["body"]
        assert f'filename="clip.{ext}"'.encode() in call["body"]
        assert f"Content-Type: {ctype.split(';')[0]}".encode() in call["body"]


async def test_limits_and_types(client, asr, monkeypatch):
    r = await client.post("/api/v1/assistant/transcribe", files={"file": ("x.pdf", CLIP, "application/pdf")})
    assert r.status_code == 415
    r = await client.post("/api/v1/assistant/transcribe", files={"file": ("clip.m4a", CLIP, "audio/mp4")},
                          data={"duration_ms": str(121_000)})
    assert r.status_code == 413
    monkeypatch.setattr(get_settings(), "asr_max_bytes", 1000)
    r = await client.post("/api/v1/assistant/transcribe", files={"file": ("clip.m4a", CLIP, "audio/mp4")})
    assert r.status_code == 413
    r = await client.post("/api/v1/assistant/transcribe", files={"file": ("clip.m4a", b"x", "audio/mp4")})
    assert r.status_code == 400
    assert asr.calls == []


async def test_public_speech_server_is_refused(client, asr, monkeypatch):
    """MAILR_ASR_URL is a local endpoint: an address that resolves to a public one is refused when the
    connection is made (core/outbound.py), so the real transport is used here."""
    monkeypatch.setattr(get_settings(), "asr_url", "https://8.8.8.8")
    transcribe.use_transport(None)
    r = await client.post("/api/v1/assistant/transcribe", files={"file": ("clip.m4a", CLIP, "audio/mp4")})
    assert r.status_code == 503
    assert asr.calls == []


async def test_server_errors_carry_no_detail(client, asr):
    asr.status = 500
    r = await client.post("/api/v1/assistant/transcribe", files={"file": ("clip.m4a", CLIP, "audio/mp4")})
    assert r.status_code == 502
    assert "internal detail" not in r.text


async def test_needs_a_session(app, asr):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as anon:
        r = await anon.post("/api/v1/assistant/transcribe", files={"file": ("clip.m4a", CLIP, "audio/mp4")})
    assert r.status_code in (401, 403)
    assert asr.calls == []


@pytest.mark.parametrize("made_up", [
    "Thanks for watching!", "Thank you.", "you", " Thank you. Thank you. ", "Subtitles by the Amara.org community",
    "Sous-titres réalisés par la communauté d'Amara.org", "Untertitel im Auftrag des ZDF, 2017",
    "Vielen Dank fürs Zuschauen!", "Gracias por ver el video.", "ご視聴ありがとうございました", "THANK YOU FOR WATCHING",
])
async def test_a_made_up_phrase_alone_is_dropped(client, asr, made_up):
    """agent.dictate: Whisper's stock phrases from silence come back as no text, with the reason."""
    asr.text = made_up
    r = await client.post("/api/v1/assistant/transcribe", files={"file": ("clip.m4a", CLIP, "audio/mp4")})
    assert r.status_code == 200, r.text
    assert r.json() == {"text": "", "dropped": "no speech"}


async def test_a_made_up_closing_sentence_is_cut(client, asr):
    for said, kept in (
        ("Move this to Friday and email Dana. Thank you.", "Move this to Friday and email Dana."),
        ("Move it to 3 p.m. tomorrow. Thanks for watching! you", "Move it to 3 p.m. tomorrow."),
        ("Reply to Dana and say thank you.", "Reply to Dana and say thank you."),
        ("Thank you. Now move it to Friday.", "Thank you. Now move it to Friday."),
        ("Book the review. Subtitles by the Amara.org community", "Book the review."),
    ):
        asr.text = said
        r = await client.post("/api/v1/assistant/transcribe", files={"file": ("clip.m4a", CLIP, "audio/mp4")})
        assert r.json() == {"text": kept}, said


# ---------------------------------------------------------------- the context's data class


@pytest.fixture
async def hosted_asr(monkeypatch):
    """Only a hosted speech service (no MAILR_ASR_URL)."""
    import uuid

    from sqlalchemy import delete

    from mailr.models import ModelEndpoint

    fake = FakeAsr()
    monkeypatch.setattr(get_settings(), "asr_url", "")
    transcribe.use_transport(httpx.MockTransport(fake.handler))
    name = f"speech-{uuid.uuid4().hex[:6]}"
    async with sessionmaker()() as db:
        db.add(ModelEndpoint(name=name, base_url="https://speech.example.test/v1", locality="hosted",
                             models=["whisper-large"], defaults={"transcribe": "whisper-large"}, enabled=True))
        await db.commit()
    yield fake
    transcribe.use_transport(None)
    async with sessionmaker()() as db:
        await db.execute(delete(ModelEndpoint).where(ModelEndpoint.name == name))
        await db.commit()


async def test_a_hosted_speech_service_serves_a_normal_context(client, hosted_asr):
    assert (await client.get("/api/v1/me")).json()["features"]["transcribe"] is True
    r = await client.post("/api/v1/assistant/transcribe", files={"file": ("clip.m4a", CLIP, "audio/mp4")},
                          data={"context": "none"})
    assert r.status_code == 200, r.text
    assert hosted_asr.calls[-1]["url"] == "https://speech.example.test/v1/audio/transcriptions"
    assert b"whisper-large" in hosted_asr.calls[-1]["body"]


async def test_a_missing_or_unknown_context_is_local_only(client, hosted_asr):
    for data in ({}, {"context": "chat:no-such-session"}, {"context": "nonsense"}):
        r = await client.post("/api/v1/assistant/transcribe", files={"file": ("clip.m4a", CLIP, "audio/mp4")},
                              data=data)
        assert r.status_code == 409, (data, r.text)
        assert r.json()["code"] == "dictation.local_only_unavailable"
    assert hosted_asr.calls == []


async def test_a_sensitive_chat_or_a_local_only_note_keeps_the_clip_local(client, user, hosted_asr):
    import uuid

    from mailr.models import ChatSession
    from mailr.notes.models import Note

    sid, plain = f"s-{uuid.uuid4().hex[:8]}", f"s-{uuid.uuid4().hex[:8]}"
    pid = f"n{uuid.uuid4().hex[:10]}"
    async with sessionmaker()() as db:
        db.add(ChatSession(id=sid, user_id=user.id, sensitive=True))
        db.add(ChatSession(id=plain, user_id=user.id, sensitive=False))
        db.add(Note(pid=pid, owner_user_id=user.id, title="private", local_only=True))
        await db.commit()
    for ctx in (f"chat:{sid}", f"note:{pid}"):
        r = await client.post("/api/v1/assistant/transcribe", files={"file": ("clip.m4a", CLIP, "audio/mp4")},
                              data={"context": ctx})
        assert r.status_code == 409, (ctx, r.text)
    assert hosted_asr.calls == []
    r = await client.post("/api/v1/assistant/transcribe", files={"file": ("clip.m4a", CLIP, "audio/mp4")},
                          data={"context": f"chat:{plain}"})
    assert r.status_code == 200, r.text
    assert len(hosted_asr.calls) == 1


async def test_the_local_endpoint_serves_a_local_only_context(client, user, asr):
    import uuid

    from mailr.models import ChatSession

    sid = f"s-{uuid.uuid4().hex[:8]}"
    async with sessionmaker()() as db:
        db.add(ChatSession(id=sid, user_id=user.id, sensitive=True))
        await db.commit()
    r = await client.post("/api/v1/assistant/transcribe", files={"file": ("clip.m4a", CLIP, "audio/mp4")},
                          data={"context": f"chat:{sid}"})
    assert r.status_code == 200, r.text
    assert asr.calls[-1]["url"] == "http://192.168.1.41:9000/v1/audio/transcriptions"


async def test_the_environment_endpoint_keeps_its_address(admin_client, asr):
    from mailr.models import ModelEndpoint

    await transcribe.sync_env_asr()
    async with sessionmaker()() as db:
        row = (await db.execute(select(ModelEndpoint).where(ModelEndpoint.from_env.is_(True)))).scalars().first()
    assert row is not None and row.base_url == "http://192.168.1.41:9000/v1" and row.locality == "local"
    eps = (await admin_client.get("/api/v1/admin/models")).json()
    mine = next(e for e in eps if e["id"] == row.id)
    assert mine["from_env"] is True
    r = await admin_client.patch(f"/api/v1/admin/models/{row.id}", json={
        "name": mine["name"], "base_url": "https://speech.example.test/v1", "locality": "hosted", "adapter": "openai",
        "models": mine["models"]})
    assert r.status_code == 400 and r.json()["code"] == "dictation.env_endpoint"
