# ACT agent.dictate @api
"""Dictation for the assistant (ACT agent.dictate): the model task `transcribe`.

The Android app turns speech into text on the phone when it can. When it cannot (no on-device
recognizer, or the user chose the server), it records a short clip and sends it here, and so does
the web app. mailr passes the clip to the endpoint of the Speech to text task (an OpenAI-compatible
`/audio/transcriptions`) and returns the text.

- The endpoint is resolved like any model call (intel/llm.resolve, task "transcribe"): the
  person's choice, else the server default. Hosted speech services (OpenAI, Groq, any compatible
  API) can serve it. `MAILR_ASR_URL` (e.g. WhisperX on the LAN) adds a built-in local endpoint
  (sync_env_asr): its URL comes from the environment, and an admin cannot change it.
- The request names its context (`context`): `chat:<session id>`, `draft:<message id>`,
  `note:<note id>`, `task:<uid>` or `none`. The clip's data class is that context's (a sensitive
  chat, a draft of a local-only account, a local-only note or a task of a local-only calendar are
  local-only), plus the request's taint scope and the local-models-only rule. A request without a
  context (an older app) is local-only, as dictation was before. Local-only clips go to a local
  endpoint only; without one the answer is "Not available with local models only".
- Every connection goes through core/outbound.client: pinned, no proxy from the environment, and a
  local endpoint's addresses are checked to be private when it is connected to.
- GET /me reports `features.transcribe` (an endpoint can do the task for this person).
- The clip is held in memory for the one request and never stored or logged. The audit row holds
  its size and length only, not the words.
- Limits: `MAILR_ASR_MAX_BYTES` (8 MB) and `MAILR_ASR_MAX_SECONDS` (120 s, as the app reports it).
- Whisper makes up stock phrases from silence or noise ("Thanks for watching!", "Subtitles by …").
  A transcript that is only such a phrase comes back as `{"text": "", "dropped": "no speech"}`, and
  such a phrase as the last sentence of a longer transcript is cut off (`no_speech_phrase`).
"""

from __future__ import annotations

import logging
import re
import unicodedata

import httpx
from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.config import get_settings
from mailr.core import outbound
from mailr.core.audit import audit
from mailr.core.auth import Principal, scope
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.intel import llm
from mailr.models import ModelEndpoint, User

router = APIRouter(tags=["assistant"])
log = logging.getLogger("mailr.transcribe")

# What the apps record, passed through unchanged: AAC in MP4 (Android, Safari), Opus in WebM
# (Chrome, Edge) or Ogg (Firefox). WhisperX reads all of them as they are, including a WebM that
# MediaRecorder wrote without a duration, so nothing converts them.
AUDIO_TYPES = {"audio/mp4", "audio/m4a", "audio/x-m4a", "audio/aac", "audio/mpeg", "audio/wav", "audio/x-wav",
               "audio/webm", "audio/ogg", "audio/flac"}

# The file name the speech server sees: some servers tell the format by its extension, and a
# browser may name the clip "blob".
_EXT = {"audio/mp4": "m4a", "audio/m4a": "m4a", "audio/x-m4a": "m4a", "audio/aac": "aac", "audio/mpeg": "mp3",
        "audio/wav": "wav", "audio/x-wav": "wav", "audio/webm": "webm", "audio/ogg": "ogg", "audio/flac": "flac"}


def clip_name(ctype: str) -> str:
    return f"clip.{_EXT.get(ctype, 'm4a')}"

# Phrases Whisper invents from silence, noise or music: subtitle credits and video sign-offs from
# its training data. Compared after _norm (lower case, no punctuation, single spaces).
_STOCK = {
    "you", "thank you", "thank you very much", "thank you so much", "thanks", "thanks a lot",
    "thanks for watching", "thank you for watching", "thank you so much for watching",
    "thank you for watching and see you next time", "thanks for watching and see you next time",
    "please subscribe", "please like and subscribe", "like and subscribe", "dont forget to subscribe",
    "see you next time", "see you in the next video", "bye", "bye bye",
    "merci", "merci davoir regardé", "merci davoir regardé cette vidéo", "merci à tous",
    "danke", "vielen dank", "danke fürs zuschauen", "vielen dank fürs zuschauen",
    "gracias", "gracias por ver", "gracias por ver el video", "muchas gracias",
    "grazie", "grazie per la visione", "obrigado", "obrigado por assistir",
    "продолжение следует", "спасибо за просмотр", "ご視聴ありがとうございました", "谢谢观看", "謝謝觀看",
}
# Credits that name someone after a fixed opening ("Subtitles by the Amara.org community").
_STOCK_OPENINGS = (
    "subtitles by", "subtitled by", "captions by", "captioning by", "closed captioning by",
    "transcribed by", "transcription by", "translated by", "english subtitles", "subtitles made by",
    "sous-titres réalisés par", "sous-titres par", "sous-titrage", "untertitel", "untertitelung",
    "subtítulos por", "subtítulos realizados por", "subtitulado por", "sottotitoli", "ondertitels",
    "ondertiteling", "napisy", "legendas por", "редактор субтитров", "субтитры", "字幕",
    "amara.org", "amaraorg",
)


def _norm(s: str) -> str:
    """Lower case, punctuation removed (a hyphen and dot inside a word kept), single spaces."""
    s = unicodedata.normalize("NFC", s).casefold().replace("’", "").replace("'", "")
    s = "".join(" " if unicodedata.category(ch)[0] in "PS" and ch not in "-." else ch for ch in s)
    s = re.sub(r"(?<!\w)[-.]|[-.](?!\w)", " ", s)
    return " ".join(s.split())


def no_speech_phrase(sentence: str) -> bool:
    """True when [sentence] is one of the phrases Whisper makes up from silence."""
    n = _norm(sentence)
    if not n:
        return True
    return n in _STOCK or n.startswith(_STOCK_OPENINGS) or n.replace(" ", "").startswith(("字幕", "amaraorg"))


# A sentence ends at . ! ? … or their CJK forms, followed by space or the end.
_SENTENCES = re.compile(r"[^.!?…。！？]+(?:[.!?…。！？]+|$)\s*")


def clean(text: str) -> str:
    """The transcript without made-up stock phrases at its end; "" when nothing else is left."""
    text = text.strip()
    parts = [m.group(0) for m in _SENTENCES.finditer(text) if m.group(0).strip()]
    # A credit may carry its own dots ("Amara.org"), so the whole text is checked first.
    if not parts or no_speech_phrase(text):
        return ""
    # The longest closing run of sentences that is a stock phrase goes; a credit's own dots split
    # it into several "sentences". Then again, for a sign-off followed by another ("… Thank you.").
    cut = True
    while cut:
        cut = False
        for i in range(1, len(parts)):
            if no_speech_phrase("".join(parts[i:])):
                parts, cut = parts[:i], True
                break
    rest = "".join(parts).strip()
    return "" if no_speech_phrase(rest) else rest


# Tests route requests through a MockTransport (tests/test_transcribe.py).
_TRANSPORT: httpx.AsyncBaseTransport | None = None


def use_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    global _TRANSPORT
    _TRANSPORT = transport


ENV_ENDPOINT = "Speech server (server)"


def env_base() -> str:
    """The endpoint base URL MAILR_ASR_URL names ("" when unset): http://asr:9000 becomes
    http://asr:9000/v1, as the other endpoints' base URLs end before /audio/transcriptions."""
    u = (get_settings().asr_url or "").strip().rstrip("/")
    if not u:
        return ""
    return u if u.lower().endswith("/v1") else u + "/v1"


async def sync_env_asr(db: AsyncSession | None = None) -> None:
    """The built-in speech endpoint the environment sets up (MAILR_ASR_URL), like the page reader of
    MAILR_BROWSER_URL: made the first time (local, task default "transcribe" = MAILR_ASR_MODEL), its
    URL and locality kept equal to the environment's. An admin's later choices (models, defaults,
    on or off) are kept. Without MAILR_ASR_URL the row stays but is not used (llm.enabled_endpoints)."""
    base = env_base()
    if not base:
        return
    from mailr.db import session_scope

    async def run(s: AsyncSession) -> None:
        row = (await s.execute(select(ModelEndpoint).where(ModelEndpoint.from_env.is_(True))
                               .order_by(ModelEndpoint.id).limit(1))).scalars().first()
        model = (get_settings().asr_model or "whisper-1").strip()
        if row is None:
            names = set((await s.execute(select(ModelEndpoint.name))).scalars())
            name, n = ENV_ENDPOINT, 2
            while name in names:
                name, n = f"{ENV_ENDPOINT} {n}", n + 1
            s.add(ModelEndpoint(name=name, base_url=base, locality=outbound.LOCAL, adapter="openai", models=[model],
                                defaults={"transcribe": model}, enabled=True, from_env=True))
        else:
            if (row.base_url, row.locality, row.adapter) != (base, outbound.LOCAL, "openai"):
                row.base_url, row.locality, row.adapter = base, outbound.LOCAL, "openai"
            if model not in (row.models or []):
                row.models = [*(row.models or []), model]

    try:
        if db is not None:
            async with db.begin_nested():
                await run(db)
        else:
            async with session_scope() as s:
                await run(s)
    except IntegrityError:
        return  # another process made it at the same moment


async def available(db: AsyncSession, user: User | None) -> bool:
    """Can dictation reach any endpoint for this person (GET /me features.transcribe)?"""
    await sync_env_asr()
    return await llm.available(db, user, "transcribe", sensitive=False)


async def context_local_only(db: AsyncSession, p: Principal, context: str | None) -> bool:
    """The data class of what the clip is spoken into. A missing, unknown or unreadable context is
    local-only (fail closed: an older app keeps the old rule)."""
    if p.token is not None and p.token.local_only:
        return True
    kind, _, ref = (context or "").strip().partition(":")
    ref = ref.strip()
    try:
        if kind == "none" and not ref:
            return False
        if kind == "chat" and ref:
            from mailr.models import ChatSession

            sess = await db.get(ChatSession, ref)
            return sess is None or sess.user_id != p.user_id or bool(sess.sensitive)
        if kind == "draft" and ref:
            from mailr.core import publicid
            from mailr.models import Connector, Message

            m = await db.get(Message, publicid.decode("message", ref))
            if m is None or m.user_id != p.user_id:
                return True
            conn = await db.get(Connector, m.connector_id) if m.connector_id else None
            return bool(m.local_only) or (conn is not None and conn.data_class == "local_only")
        if kind == "note" and ref:
            from mailr.notes import access as note_access
            from mailr.notes.models import Note

            n = (await db.execute(select(Note).where(Note.pid == ref))).scalar_one_or_none()
            if n is None or note_access.level(p.user, n) == "none":
                return True
            return bool(n.local_only)
        if kind == "task" and ref:
            from mailr.models import CalObject, Connector

            obj = (await db.execute(select(CalObject).where(CalObject.user_id == p.user_id, CalObject.uid == ref)
                                    .limit(1))).scalar_one_or_none()
            if obj is None:
                return True
            conn = await db.get(Connector, obj.connector_id)
            return conn is None or conn.data_class == "local_only"
    except Exception:  # noqa: BLE001 - a context that cannot be read counts as local-only
        log.info("dictation context %r could not be read; the clip counts as local-only", kind)
        return True
    return True


@router.post("/assistant/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    duration_ms: int | None = Form(None),
    language: str | None = Form(None, max_length=16),
    context: str | None = Form(None, max_length=200),
    p: Principal = Depends(scope("read")),
    db: AsyncSession = Depends(get_db),
):
    s = get_settings()
    await sync_env_asr()
    local_only = await context_local_only(db, p, context)
    try:
        r = await llm.resolve(db, p.user, "transcribe", sensitive=local_only, probe=True)
    except llm.LocalOnlyViolation:
        raise ApiError(409, "dictation.local_only_unavailable") from None
    except llm.LocalModelsOnly:
        raise ApiError(409, "dictation.local_only_unavailable") from None
    except llm.NoModel:
        raise ApiError(404, "dictation.not_set_up") from None
    ctype = (file.content_type or "").split(";")[0].strip().lower()
    if ctype not in AUDIO_TYPES:
        raise ApiError(415, "dictation.not_audio")
    if duration_ms is not None and duration_ms > s.asr_max_seconds * 1000:
        raise ApiError(413, "dictation.too_long", max=s.asr_max_seconds)
    data = await file.read(s.asr_max_bytes + 1)
    if len(data) > s.asr_max_bytes:
        raise ApiError(413, "dictation.too_big", max=s.asr_max_bytes // 1_000_000)
    if len(data) < 100:
        raise ApiError(400, "dictation.empty_clip")
    size = len(data)
    await llm.counted(p.user, "dictation", r)
    fields = {"model": r.model}
    if language:
        fields["language"] = language
    headers = {"Authorization": f"Bearer {r.api_key}"} if r.api_key else {}
    try:
        async with outbound.client(outbound.endpoint_service(r.endpoint, "transcribe"), local_only=local_only,
                                   transport=_TRANSPORT, timeout=60.0) as c:
            resp = await c.post(llm._url(r, "/audio/transcriptions"), headers=headers,
                                files={"file": (clip_name(ctype), data, ctype)}, data=fields)
    except outbound.Refused:
        raise ApiError(409, "dictation.local_only_unavailable") from None
    except httpx.TimeoutException:
        raise ApiError(504, "dictation.timeout") from None
    except httpx.ConnectError as e:
        if "public address" in str(e):  # a local endpoint that resolves to a public address now
            raise ApiError(503, "dictation.server_not_local") from None
        raise ApiError(502, "dictation.unreachable") from None
    except httpx.HTTPError:
        raise ApiError(502, "dictation.unreachable") from None
    finally:
        del data
    if resp.status_code != 200:
        log.warning("speech server answered %s", resp.status_code)
        raise ApiError(502, "dictation.transcribe_failed")
    try:
        text = str(resp.json().get("text") or "").strip()
    except ValueError:
        raise ApiError(502, "dictation.bad_answer") from None
    heard = clean(text)
    await audit(db, p, "assistant.transcribe", "", {"bytes": size, "duration_ms": duration_ms, "speech": bool(heard),
                                                    "local_only": local_only})
    await db.commit()
    if not heard:
        return {"text": "", "dropped": "no speech"}
    return {"text": heard}
