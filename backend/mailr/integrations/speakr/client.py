"""The Speakr HTTP client.

- Authentication is the `Authorization: Bearer <token>` header only. The token never goes into a
  URL, an error message or a log line.
- Redirects are not followed: Speakr answers API calls with JSON, so a redirect (usually to
  /login) means a wrong address or an old Speakr.
- Before each request the base URL is checked with core/netguard. Plain http:// is accepted only
  for a private address or a host in MAILR_OUTBOUND_ALLOW.
- Errors: 401 → SpeakrAuthError; 403 with `"code": "insufficient_scope"` → SpeakrScopeError (a
  configuration error: the token lacks a scope, and the message names the token to create); any
  other 403 and 404 → SpeakrNotFound (a recording of someone else is "not found" to us); 410 with
  `cursor_expired` → SpeakrCursorExpired; 429 → SpeakrRateLimited with Retry-After, and no request
  goes out on that connector until Retry-After has passed; 5xx and network failures →
  SpeakrUnavailable, a web page instead of JSON → NotSpeakr. Every message is safe to show.
- Reads are limited to 60 a minute per connector (a token bucket), under Speakr's per-token 120.
- Transcripts arrive in Speakr's canonical shape (`kind`, and per segment `index`, `speaker`,
  `speaker_label`, `speaker_id`, `text`, `start_time`, `end_time`) on a server with API v1.1, and
  with two older key sets (`sentence`/`start_time`/`end_time` and `text`/`start`/`end`) or as plain
  text on older servers; all of them become `{t, end, speaker, text}` (plus `speaker_id` and
  `label` from the canonical shape) in `segments`. Datetimes become aware UTC; `participants` is
  split on commas.
- Features of a newer Speakr (scoped tokens, the changes feed, ETags, search, external references,
  share links in v1, chat sources) are read from GET /capabilities (`capabilities`); an older
  server answers 404 there and gets exactly the requests of before. Features are never inferred
  from the version string.

In tests, requests go to an `httpx.MockTransport` instead (`use_transport`; tests/speakr_fake.py).
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from mailr.core import netguard, outbound

READ_TIMEOUT = 10.0
UPLOAD_TIMEOUT = 120.0
PER_PAGE = 100
MAX_PAGES = 200
GETS_PER_MINUTE = 60
DEFAULT_RETRY_AFTER = 60.0

_TRANSPORT: httpx.AsyncBaseTransport | None = None
# The largest API answer mailr reads (transcripts are text; audio is never downloaded here).
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
_BUCKETS: dict[str, tuple[float, float]] = {}
_BLOCKED: dict[str, float] = {}  # bucket → monotonic time until which Speakr asked to wait (429)


def use_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Route every Speakr request through `transport` (tests); None restores the network."""
    global _TRANSPORT
    _TRANSPORT = transport
    _BUCKETS.clear()
    _BLOCKED.clear()


class SpeakrError(Exception):
    """A Speakr request failed. The message is safe to show to the user."""


class SpeakrAuthError(SpeakrError):
    """401: the token is invalid, expired or revoked."""


class SpeakrNotFound(SpeakrError):
    """403 or 404: no such recording for this account (or it was deleted)."""


class SpeakrScopeError(SpeakrError):
    """403 insufficient_scope: the token lacks a scope this request needs (a configuration error)."""

    def __init__(self, required: list[str], token_scopes: list[str]):
        self.required = [str(x) for x in required or []]
        self.token_scopes = [str(x) for x in token_scopes or []]
        need = ", ".join(self.required) or "another"
        super().__init__(f"This Speakr token does not have the {need} scope. In Speakr, create a token with "
                         f"the {need} scope (Account Settings → API Tokens) and paste it in Settings → Mail → Mail accounts → "
                         "Speakr.")


class SpeakrRefused(SpeakrError):
    """403 with a code Speakr explains (feature_disabled, not_permitted, https_required)."""

    def __init__(self, code: str, reason: str):
        self.code = code
        super().__init__(f"Speakr refused it ({code})" + (f": {reason}" if reason else ""))


class SpeakrCursorExpired(SpeakrError):
    """410 cursor_expired from the changes feed: start again without a cursor."""


class SpeakrRateLimited(SpeakrError):
    def __init__(self, retry_after: float):
        super().__init__(f"Speakr asked to slow down; try again in {int(retry_after)} s")
        self.retry_after = retry_after


class SpeakrUnavailable(SpeakrError):
    """5xx, a timeout or a network failure."""


class SpeakrModelsRefused(SpeakrError):
    """Not sent: the request would have taken local-only text to Speakr's models, which are not
    local (core/outbound.py)."""


class SpeakrTooLarge(SpeakrError):
    """413: the file is over the Speakr server's upload limit."""


TOO_LARGE_MESSAGE = ("Speakr refused the file as too large (413). The limit is set on the Speakr server "
                     "(its upload size and any proxy in front of it); upload a smaller file in Speakr itself.")


class NotSpeakr(SpeakrError):
    """The address answered with a web page or a redirect, not with Speakr's JSON API."""


AUTH_MESSAGE = "This token is invalid, expired or revoked"
NOT_SPEAKR_MESSAGE = "This address is not a Speakr server (it answered with a web page)"


def normalize_base(url: str) -> str:
    """The server's root URL: no trailing slash, no /api/v1 a user may have pasted with it."""
    u = (url or "").strip().rstrip("/")
    u = re.sub(r"/api(/v1)?$", "", u, flags=re.IGNORECASE)
    return u


async def check_base_url(url: str, private_ok: bool | None = None) -> None:
    """netguard (with the owner's private-network policy), then https unless the host is
    private or explicitly allowed."""
    try:
        await netguard.check_url(url, private_ok=private_ok)
    except netguard.UnsafeTarget as e:
        raise SpeakrError(str(e)) from None
    u = urlsplit(url)
    if u.scheme == "https":
        return
    if not await asyncio.to_thread(netguard.is_private_target, (u.hostname or "").lower()):
        raise SpeakrError("Use https:// for a Speakr server on the internet; http:// is accepted only "
                          "on a private network")


# ---------------------------------------------------------------- normalisation


def parse_dt(v: Any) -> datetime | None:
    """An ISO datetime as aware UTC (Speakr's naive datetimes are UTC)."""
    if not v or not isinstance(v, str):
        return None
    try:
        d = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)


def split_participants(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return []


def seconds(v: Any) -> float | None:
    """Seconds from a number or an "hh:mm:ss(.fff)" / "mm:ss" string."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    try:
        return float(s)
    except ValueError:
        pass
    parts = s.split(":")
    if 2 <= len(parts) <= 3:
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            return None
        total = 0.0
        for n in nums:
            total = total * 60 + n
        return total
    return None


def _duration(d: dict[str, Any]) -> float | None:
    for k in ("audio_duration", "duration_seconds", "duration"):
        v = seconds(d.get(k))
        if v is not None:
            return v
    return None


def recording_meta(d: dict[str, Any]) -> dict[str, Any]:
    """The metadata mailr mirrors from one recording of GET /recordings (nothing else is kept)."""
    tags = [{"id": t.get("id"), "name": str(t.get("name") or ""), "color": t.get("color")}
            for t in d.get("tags") or [] if isinstance(t, dict)]
    folder = d.get("folder")
    if isinstance(folder, dict):
        folder = {"id": folder.get("id"), "name": str(folder.get("name") or "")}
    elif isinstance(folder, str) and folder:
        folder = {"id": None, "name": folder}
    else:
        folder = None
    audio = d.get("audio_available")
    if audio is None:
        audio = not d.get("audio_deleted_at")
    return {
        "recording_id": int(d["id"]),
        "title": str(d.get("title") or "")[:500],
        "meeting_date": parse_dt(d.get("meeting_date")),
        "created_upstream": parse_dt(d.get("created_at")),
        "completed_upstream": parse_dt(d.get("completed_at")),
        "duration_s": _duration(d),
        "participants": split_participants(d.get("participants"))[:50],
        "tags": tags,
        "folder": folder,
        "status": str(d.get("status") or "").lower()[:20],
        "has_transcript": bool(d.get("has_transcription") or d.get("has_transcript")),
        "has_summary": bool(d.get("has_summary")),
        "audio_available": bool(audio),
        "is_archived": bool(d.get("is_archived")),
        # Recordings other users shared with this account (scope=all on a newer Speakr).
        **shared_meta(d),
        "mailr_refs": [{"kind": str(r.get("kind") or "")[:40], "ref": str(r["ref"])[:500]} for r in external_refs(d)][:50],
    }


def shared_meta(d: dict[str, Any]) -> dict[str, Any]:
    """is_shared, owner_name and can_edit of one recording. Own recordings (and every recording of
    an older Speakr, which has no `is_shared`) are not shared and editable."""
    if not d.get("is_shared"):
        return {"is_shared": False, "owner_name": "", "can_edit": True}
    owner = d.get("owner") if isinstance(d.get("owner"), dict) else {}
    share = d.get("share") if isinstance(d.get("share"), dict) else {}
    name = str(owner.get("name") or owner.get("username") or "")[:200]
    return {"is_shared": True, "owner_name": name, "can_edit": bool(share.get("can_edit"))}


def external_refs(d: dict[str, Any], system: str = "mailr") -> list[dict[str, Any]]:
    """The caller's external references of one system on a recording (absent on older servers)."""
    return [r for r in d.get("external_refs") or [] if isinstance(r, dict) and r.get("system") == system
            and isinstance(r.get("ref"), str)]


def segments(body: Any) -> list[dict[str, Any]]:
    """Transcript segments as [{t, end, speaker, text}] from Speakr's canonical shape (`kind` in the
    answer: every segment has `text`, `start_time`, `end_time`, `speaker`, `speaker_label` and
    `speaker_id`), else from either older key set, or paragraphs of a plain-text transcript (t and
    end are None there). Canonical segments also carry `label` and `speaker_id`."""
    raw = ""
    canonical = isinstance(body, dict) and body.get("kind") in ("segments", "plain")
    if isinstance(body, dict):
        segs = body.get("segments")
        raw = body.get("raw") or body.get("text") or body.get("transcription") or ""
        if isinstance(raw, list):
            segs, raw = raw, ""
    else:
        segs = body
    out: list[dict[str, Any]] = []
    for s in segs if isinstance(segs, list) else []:
        if not isinstance(s, dict):
            continue
        if canonical:
            text = str(s.get("text") or "").strip()
            if not text:
                continue
            sid = s.get("speaker_id")
            out.append({"t": seconds(s.get("start_time")), "end": seconds(s.get("end_time")),
                        "speaker": str(s.get("speaker") or ""), "text": text,
                        "label": str(s.get("speaker_label") or ""),
                        "speaker_id": sid if isinstance(sid, int) and not isinstance(sid, bool) else None})
            continue
        text = s.get("sentence") if s.get("sentence") is not None else s.get("text")
        text = str(text or "").strip()
        if not text:
            continue
        t = seconds(s.get("start_time") if s.get("start_time") is not None else s.get("start"))
        end = seconds(s.get("end_time") if s.get("end_time") is not None else s.get("end"))
        out.append({"t": t, "end": end, "speaker": str(s.get("speaker") or s.get("speaker_name") or ""),
                    "text": text})
    if not out and isinstance(raw, str) and raw.strip():
        out = [{"t": None, "end": None, "speaker": "", "text": p.strip()}
               for p in re.split(r"\n\s*\n|\n", raw) if p.strip()]
    return out


def event_out(e: dict[str, Any]) -> dict[str, Any]:
    """One extracted event. A newer Speakr adds `attendees` ({name, email} objects), the extracted
    `reminder_minutes` and `floating` (wall-clock times without a zone); an older one gives
    neither, and its attendees, when present, are plain strings."""
    attendees = []
    for a in e.get("attendees") or []:
        if isinstance(a, str) and a.strip():
            v = a.strip()
            attendees.append({"name": "" if "@" in v else v[:200], "email": v.lower()[:320] if "@" in v else None})
        elif isinstance(a, dict) and (a.get("name") or a.get("email")):
            email = str(a.get("email") or "").strip().lower() or None
            attendees.append({"name": str(a.get("name") or "")[:200], "email": email if email and "@" in email else None})
    rem = e.get("reminder_minutes")
    floating = e.get("floating")
    return {"title": str(e.get("title") or ""), "start": e.get("start_datetime"), "end": e.get("end_datetime"),
            "location": str(e.get("location") or ""), "description": str(e.get("description") or "")[:2000],
            "attendees": attendees[:50],
            "reminder_minutes": int(rem) if isinstance(rem, (int, float)) and not isinstance(rem, bool) and rem >= 0 else None,
            # Without the key (an older Speakr) the times are taken as they come, as before.
            "floating": floating if isinstance(floating, bool) else None}


def capabilities_out(d: Any) -> dict[str, Any]:
    """GET /capabilities, reduced to what mailr stores in the connector's config. `available`
    is False for an older server (no such route): every feature is then off."""
    if not isinstance(d, dict) or not isinstance(d.get("features"), dict):
        return {"available": False, "features": {}, "models_local": False}
    feats = {}
    for k, v in d["features"].items():
        if isinstance(v, bool):
            feats[str(k)[:40]] = v
        elif isinstance(v, dict):
            feats[str(k)[:40]] = {str(a)[:40]: bool(b) for a, b in v.items() if isinstance(b, bool)}
    return {"available": True, "features": feats, "models_local": d.get("models_local") is True,
            "api_version": str(d.get("api_version") or "")[:20], "speakr_version": str(d.get("speakr_version") or "")[:40]}


def has_feature(caps: dict[str, Any] | None, name: str, sub: str | None = None) -> bool:
    """A feature from stored capabilities; a missing key means false."""
    v = ((caps or {}).get("features") or {}).get(name)
    if sub is not None:
        return isinstance(v, dict) and v.get(sub) is True
    return v is True


# ---------------------------------------------------------------- the client


async def _take(key: str) -> None:
    """One GET from the connector's bucket (60 a minute), waiting when it is empty."""
    now = time.monotonic()
    tokens, last = _BUCKETS.get(key, (float(GETS_PER_MINUTE), now))
    tokens = min(float(GETS_PER_MINUTE), tokens + (now - last) * GETS_PER_MINUTE / 60.0)
    if tokens < 1:
        wait = (1 - tokens) * 60.0 / GETS_PER_MINUTE
        await asyncio.sleep(wait)
        tokens, now = 1.0, time.monotonic()
    _BUCKETS[key] = (tokens - 1, now)


class SpeakrClient:
    def __init__(self, base_url: str, token: str, *, bucket: str | None = None,
                 http: httpx.AsyncClient | None = None, private_ok: bool | None = None,
                 models_local: bool = False):
        self.base_url = normalize_base(base_url)
        self._token = token
        self._bucket = bucket or self.base_url
        self._http = http
        self.private_ok = private_ok
        # Speakr's own models (semantic search, chat): local only when the connector says so
        # (store.speakr_models_local), and then checked again at connect time.
        self.models_local = models_local

    def _service(self, models: bool) -> outbound.Service:
        if models:
            return outbound.Service(kind="speakr_models", name=self._bucket, base_url=self.base_url,
                                    locality=outbound.LOCAL if self.models_local else outbound.HOSTED,
                                    source="speakr")
        # The person's own account: the API itself (what may be written there is the caller's rule).
        return outbound.Service(kind="account", name=self._bucket, base_url=self.base_url,
                                locality=outbound.HOSTED, source="speakr")

    def __repr__(self) -> str:  # never shows the token
        return f"SpeakrClient({self.base_url!r})"

    async def _request(self, method: str, path: str, params: dict[str, Any] | None = None,
                       json: Any = None, files: Any = None, data: Any = None,
                       timeout: float = READ_TIMEOUT, models: bool = False) -> Any:
        return _check(await self._raw(method, path, params=params, json=json, files=files, data=data,
                                      timeout=timeout, models=models))

    async def _raw(self, method: str, path: str, params: dict[str, Any] | None = None,
                   json: Any = None, files: Any = None, data: Any = None,
                   timeout: float = READ_TIMEOUT, etag: str | None = None, models: bool = False) -> httpx.Response:
        await check_base_url(self.base_url, self.private_ok)
        wait = _BLOCKED.get(self._bucket, 0.0) - time.monotonic()
        if wait > 0:
            # Speakr answered 429 on this connector: nothing goes out until Retry-After has passed.
            raise SpeakrRateLimited(wait)
        if method == "GET":
            await _take(self._bucket)
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        if etag:
            headers["If-None-Match"] = etag
        kw: dict[str, Any] = {"params": params, "headers": headers}
        if files is not None:
            kw.update(files=files, data=data)
        else:
            kw["json"] = json
        try:
            if self._http is not None and not models:
                r = await netguard.send_capped(self._http, self._http.build_request(method, self.base_url + path, **kw),
                                               MAX_RESPONSE_BYTES)
            else:
                # _TRANSPORT: the tests' fake Speakr.
                async with outbound.client(self._service(models), private_ok=self.private_ok, transport=_TRANSPORT,
                                           timeout=timeout) as c:
                    r = await netguard.send_capped(c, c.build_request(method, self.base_url + path, **kw),
                                                   MAX_RESPONSE_BYTES)
        except outbound.Refused:
            raise SpeakrModelsRefused("This question may not go to Speakr's models: they are not local, and the "
                                      "question may hold local-only text") from None
        except netguard.TooLarge:
            raise SpeakrUnavailable("Speakr answered with more data than Mailr reads") from None
        except httpx.TimeoutException:
            raise SpeakrUnavailable("Speakr did not answer in time") from None
        except httpx.HTTPError as e:
            raise SpeakrUnavailable(f"Couldn't reach Speakr ({type(e).__name__})") from None
        if r.status_code == 429:
            ra = seconds(r.headers.get("Retry-After"))
            _BLOCKED[self._bucket] = time.monotonic() + (ra if ra is not None and ra >= 0 else DEFAULT_RETRY_AFTER)
        return r

    async def get_cached(self, path: str, params: dict[str, Any] | None, etag: str | None) -> tuple[bool, Any, str | None]:
        """A GET with If-None-Match: (not_modified, body, etag). A 304 has no body; Speakr sends a
        weak ETag on recording reads once it supports them (capabilities `etags`)."""
        r = await self._raw("GET", "/api/v1" + path, params=params, etag=etag)
        if r.status_code == 304:
            return True, None, etag
        return False, _check(r), r.headers.get("etag") or None

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("GET", "/api/v1" + path, params=params)

    # Reads used by mailr (section 1.3).

    async def me(self) -> dict[str, Any]:
        d = await self.get("/users/me")
        if not isinstance(d, dict) or "id" not in d:
            raise NotSpeakr(NOT_SPEAKR_MESSAGE)
        return d

    async def stats(self) -> dict[str, Any]:
        try:
            d = await self.get("/stats")
        except SpeakrNotFound:
            return {}
        return d if isinstance(d, dict) else {}

    async def capabilities(self) -> dict[str, Any]:
        """GET /capabilities (capabilities_out). An older Speakr has no such route: 404, a web page
        or any other refusal reads as "no features"; a 401 still raises."""
        try:
            d = await self.get("/capabilities")
        except (SpeakrNotFound, NotSpeakr):
            return capabilities_out(None)
        except SpeakrError as e:
            if isinstance(e, (SpeakrAuthError, SpeakrUnavailable, SpeakrRateLimited)):
                raise
            return capabilities_out(None)
        return capabilities_out(d)

    async def token_info(self) -> dict[str, Any] | None:
        """GET /tokens/current: the token's name, scopes ("full" for a full-access token) and expiry;
        None on an older Speakr."""
        try:
            d = await self.get("/tokens/current")
        except (SpeakrNotFound, NotSpeakr):
            return None
        if not isinstance(d, dict) or not isinstance(d.get("scopes"), list):
            return None
        return {"name": str(d.get("name") or "")[:100], "scopes": [str(x)[:20] for x in d["scopes"]][:20],
                "expires_at": d.get("expires_at") if isinstance(d.get("expires_at"), str) else None}

    async def changes(self, cursor: str | None, *, limit: int = 200, scope: str = "own") -> tuple[list[dict[str, Any]], str | None, bool]:
        """GET /recordings/changes: (changes, next_cursor, has_more). Raises
        SpeakrCursorExpired on 410."""
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if scope != "own":
            params["scope"] = scope
        d = await self.get("/recordings/changes", params)
        if not isinstance(d, dict) or not isinstance(d.get("changes"), list):
            raise NotSpeakr(NOT_SPEAKR_MESSAGE)
        nxt = d.get("next_cursor")
        return ([c for c in d["changes"] if isinstance(c, dict)], nxt if isinstance(nxt, str) and nxt else None,
                bool(d.get("has_more")))

    async def recordings_page(self, page: int, per_page: int | None = None, *, scope: str = "own") -> tuple[list[dict[str, Any]], bool]:
        per_page = per_page or PER_PAGE  # read at call time: tests page through a few recordings
        params: dict[str, Any] = {"page": page, "per_page": per_page, "sort_by": "created_at", "sort_order": "desc"}
        if scope != "own":
            params["scope"] = scope
        d = await self.get("/recordings", params)
        if not isinstance(d, dict):
            raise NotSpeakr(NOT_SPEAKR_MESSAGE)
        items = [x for x in d.get("recordings") or [] if isinstance(x, dict) and x.get("id") is not None]
        pag = d.get("pagination") or {}
        if "has_next" in pag:
            more = bool(pag["has_next"])
        elif pag.get("total_pages") or pag.get("pages"):
            more = page < int(pag.get("total_pages") or pag["pages"])
        elif pag.get("total") is not None:
            more = page * per_page < int(pag["total"])
        else:
            more = len(items) >= per_page
        return items, more and bool(items)

    async def all_recordings(self, *, scope: str = "own") -> AsyncIterator[dict[str, Any]]:
        page = 1
        while page <= MAX_PAGES:
            items, more = await self.recordings_page(page, scope=scope)
            for it in items:
                yield it
            if not more:
                return
            page += 1

    async def recording(self, rid: int) -> dict[str, Any]:
        d = await self.get(f"/recordings/{int(rid)}", {"format": "minimal"})
        return d if isinstance(d, dict) else {}

    async def transcript(self, rid: int, *, start: float | None = None, end: float | None = None) -> list[dict[str, Any]]:
        """The segments; `start` and `end` (seconds) ask a newer Speakr for that window only."""
        params: dict[str, Any] = {"format": "json"}
        if start is not None:
            params["start"] = start
        if end is not None:
            params["end"] = end
        return segments(await self.get(f"/recordings/{int(rid)}/transcript", params))

    async def summary(self, rid: int) -> str:
        d = await self.get(f"/recordings/{int(rid)}/summary")
        return str((d or {}).get("summary") or "") if isinstance(d, dict) else ""

    async def notes(self, rid: int) -> str:
        d = await self.get(f"/recordings/{int(rid)}/notes")
        return str((d or {}).get("notes") or "") if isinstance(d, dict) else ""

    async def speakers(self, rid: int) -> list[dict[str, Any]]:
        d = await self.get(f"/recordings/{int(rid)}/speakers")
        out = []
        for s in (d or {}).get("speakers") or [] if isinstance(d, dict) else []:
            if isinstance(s, dict):
                row = {"label": str(s.get("label") or ""), "name": str(s.get("identified_name") or s.get("name") or ""),
                       "speaker_id": s.get("speaker_id"), "segments": s.get("segment_count")}
                if "email" in s:
                    # A newer Speakr: the address the owner gave the speaker, or null.
                    row["email"] = str(s.get("email") or "").strip().lower() or None
                out.append(row)
        return out

    async def events(self, rid: int) -> list[dict[str, Any]]:
        d = await self.get(f"/recordings/{int(rid)}/events")
        return [event_out(e) for e in ((d or {}).get("events") or [] if isinstance(d, dict) else []) if isinstance(e, dict)]


    async def set_speaker_email(self, speaker_id: int, email: str | None) -> dict[str, Any]:
        """PUT /speakers/{id} {"email"} (only the email changes)."""
        d = await self._request("PUT", f"/api/v1/speakers/{int(speaker_id)}", json={"email": email or ""})
        return d if isinstance(d, dict) else {}

    async def search(self, q: str, *, mode: str = "keyword", limit: int = 20, recording_ids: list[int] | None = None,
                     date_from: str | None = None, date_to: str | None = None, scope: str = "own") -> dict[str, Any]:
        """GET /search: keyword hits in titles, participants, notes, summaries and transcript
        segments; `semantic` sends the query to Speakr's embedding model."""
        params: dict[str, Any] = {"q": q[:500], "mode": mode, "limit": max(1, min(int(limit), 50))}
        if recording_ids:
            params["recording_ids"] = ",".join(str(int(x)) for x in recording_ids[:100])
        if date_from:
            params["date_from"] = date_from
        if date_to:
            params["date_to"] = date_to
        if scope != "own":
            params["scope"] = scope
        d = await self._request("GET", "/api/v1/search", params=params, models=mode == "semantic")
        if not isinstance(d, dict) or not isinstance(d.get("results"), list):
            raise NotSpeakr(NOT_SPEAKR_MESSAGE)
        return d

    async def chat(self, rid: int, message: str, *, with_sources: bool = True) -> dict[str, Any]:
        """POST /recordings/{id}/chat with `with_sources`. Only called for a connector whose
        recordings may reach Speakr's model (store.speakr_model_ok)."""
        d = await self._request("POST", f"/api/v1/recordings/{int(rid)}/chat",
                                json={"message": message[:4000], "with_sources": bool(with_sources)}, timeout=60.0,
                                models=True)
        return d if isinstance(d, dict) else {}

    async def tags(self) -> list[dict[str, Any]]:
        """GET /tags: the user's own tags and the tags of their groups."""
        d = await self.get("/tags")
        rows = (d or {}).get("tags") if isinstance(d, dict) else d
        return [{"id": int(t["id"]), "name": str(t.get("name") or ""), "color": t.get("color"),
                 "group": bool(t.get("is_group_tag")),
                 # A group tag that shares the recording with its group when applied (Speakr's
                 # auto_share_on_apply, default on, or share_with_group_lead).
                 "shares": bool(t.get("is_group_tag")) and (t.get("auto_share_on_apply") is not False
                                                            or bool(t.get("share_with_group_lead")))}
                for t in rows or [] if isinstance(t, dict) and t.get("id") is not None]

    async def folders(self) -> list[dict[str, Any]]:
        """GET /folders: a bare list (empty when folders are turned off in Speakr)."""
        d = await self.get("/folders")
        rows = (d or {}).get("folders") if isinstance(d, dict) else d
        return [{"id": int(f["id"]), "name": str(f.get("name") or ""), "color": f.get("color"),
                 "group": f.get("group_id") is not None}
                for f in rows or [] if isinstance(f, dict) and f.get("id") is not None]

    # Writes. Each is reached only through a permission class (speakr.upload,
    # speakr.write, speakr.share) and, for write and share, a switch in the connector card.

    async def upload(self, filename: str, content: Any, content_type: str, *, notes: str = "",
                     file_last_modified: int | None = None, tag_ids: list[int] | None = None,
                     title: str | None = None, meeting_date: str | None = None,
                     folder_id: int | None = None, participants: str | None = None,
                     refs: list[dict[str, Any]] | None = None, idempotency_key: str | None = None,
                     strict: bool = False) -> dict[str, Any]:
        """POST /recordings/upload (multipart). `content` is bytes or a file object. Speakr reads
        title, meeting_date (ISO 8601), folder_id and tag_ids[i] on the upload itself. A newer
        Speakr (capabilities `external_refs`, `upload_idempotency`) also reads participants,
        external_refs (JSON), idempotency_key and strict; callers send those only then, and an
        older one needs a PATCH for the participants. Returns Speakr's answer: {id, title, status, ...}."""
        data: dict[str, Any] = {"notes": notes}
        if participants:
            data["participants"] = participants[:500]
        if refs:
            import json as _json

            data["external_refs"] = _json.dumps(refs)
        if idempotency_key:
            data["idempotency_key"] = idempotency_key[:100]
        if strict:
            data["strict"] = "true"
        if file_last_modified is not None:
            data["file_last_modified"] = str(int(file_last_modified))
        if title:
            data["title"] = title
        if meeting_date:
            data["meeting_date"] = meeting_date
        if folder_id is not None:
            data["folder_id"] = str(int(folder_id))
        for i, t in enumerate(tag_ids or []):
            data[f"tag_ids[{i}]"] = str(int(t))
        d = await self._request("POST", "/api/v1/recordings/upload", files={"file": (filename, content, content_type)},
                                data=data, timeout=UPLOAD_TIMEOUT)
        if not isinstance(d, dict) or d.get("id") is None:
            raise SpeakrError("Speakr accepted the upload but returned no recording id")
        return d

    async def patch_recording(self, rid: int, fields: dict[str, Any]) -> dict[str, Any]:
        """PATCH /recordings/{id} with title and/or participants."""
        d = await self._request("PATCH", f"/api/v1/recordings/{int(rid)}", json=fields)
        return d if isinstance(d, dict) else {}

    async def add_tags(self, rid: int, tag_ids: list[int]) -> None:
        """POST /recordings/{id}/tags {tag_ids}."""
        await self._request("POST", f"/api/v1/recordings/{int(rid)}/tags", json={"tag_ids": [int(t) for t in tag_ids]})

    async def set_tags(self, rid: int, tag_ids: list[int], current: list[int]) -> dict[str, Any]:
        """The recording's tags become exactly `tag_ids`: PUT /recordings/{id}/tags on a newer
        Speakr; an older one answers 405 or 404 there, and the change goes through POST for the
        added tags and DELETE /recordings/{id}/tags/{tag} for the removed ones."""
        ids = [int(t) for t in tag_ids]
        r = await self._raw("PUT", f"/api/v1/recordings/{int(rid)}/tags", json={"tag_ids": ids})
        if r.status_code not in (404, 405):
            d = _check(r)
            return d if isinstance(d, dict) else {}
        added = [t for t in ids if t not in current]
        removed = [t for t in current if t not in ids]
        if added:
            await self.add_tags(rid, added)
        for t in removed:
            await self._request("DELETE", f"/api/v1/recordings/{int(rid)}/tags/{int(t)}")
        return {"added": added, "removed": removed}

    async def put_refs(self, rid: int, refs: list[dict[str, Any]], system: str = "mailr") -> list[dict[str, Any]]:
        """PUT /recordings/{id}/external-refs?system=mailr: mailr's references on the recording
        become exactly `refs` (references of other systems stay)."""
        d = await self._request("PUT", f"/api/v1/recordings/{int(rid)}/external-refs", params={"system": system},
                                json={"external_refs": refs})
        return list((d or {}).get("external_refs") or []) if isinstance(d, dict) else []

    # Webhooks (/api/v1/webhooks, Speakr src/api/webhooks.py). The secret comes back once, in the
    # answer to the create call.

    async def create_webhook(self, name: str, url: str, events: list[str], *, include_shared: bool = False) -> dict[str, Any]:
        body: dict[str, Any] = {"name": name, "url": url, "events": list(events), "enabled": True}
        if include_shared:
            body["include_shared"] = True  # a newer Speakr: events of recordings shared with the user too
        d = await self._request("POST", "/api/v1/webhooks", json=body)
        if not isinstance(d, dict) or d.get("id") is None or not d.get("secret"):
            raise SpeakrError("Speakr created no webhook (its answer had no id or secret)")
        return d

    async def update_webhook(self, webhook_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        """PATCH /webhooks/{id} (for include_shared on a newer Speakr)."""
        d = await self._request("PATCH", f"/api/v1/webhooks/{int(webhook_id)}", json=fields)
        return d if isinstance(d, dict) else {}

    async def delete_webhook(self, webhook_id: int) -> None:
        r = await self._raw("DELETE", f"/api/v1/webhooks/{int(webhook_id)}")
        if r.status_code in (204, 404):
            return
        _check(r)

    async def test_webhook(self, webhook_id: int) -> dict[str, Any]:
        """POST /webhooks/{id}/test: Speakr queues a `webhook.test` delivery and answers with it."""
        d = await self._request("POST", f"/api/v1/webhooks/{int(webhook_id)}/test")
        return d if isinstance(d, dict) else {}

    async def webhook_delivery(self, webhook_id: int, delivery_id: int) -> dict[str, Any]:
        d = await self._request("GET", f"/api/v1/webhooks/{int(webhook_id)}/deliveries/{int(delivery_id)}")
        return d if isinstance(d, dict) else {}

    async def put_notes(self, rid: int, notes: str) -> None:
        """PUT /recordings/{id}/notes: replaces the notes (callers read, append and write)."""
        await self._request("PUT", f"/api/v1/recordings/{int(rid)}/notes", json={"notes": notes})

    async def share(self, rid: int, *, share_summary: bool = True, share_notes: bool = False,
                    v1: bool = False) -> dict[str, Any]:
        """A public link, created or reused. With `v1` (capabilities `share_links`), POST
        /api/v1/recordings/{id}/share: a reused link keeps its flags and the answer reports them
        (`share`, `flags_differ`). Otherwise the web route POST /api/recording/{id}/share (outside v1,
        a newer Speakr), which changes an existing link's flags to the request's."""
        path = f"/api/v1/recordings/{int(rid)}/share" if v1 else f"/api/recording/{int(rid)}/share"
        d = await self._request("POST", path, json={"share_summary": bool(share_summary), "share_notes": bool(share_notes)})
        if not isinstance(d, dict) or not d.get("share_url"):
            raise SpeakrError("Speakr returned no share link")
        return d

    async def shares(self, rid: int) -> list[dict[str, Any]]:
        """GET /recordings/{id}/shares: the public links of a recording (v1 share links)."""
        d = await self.get(f"/recordings/{int(rid)}/shares")
        return [x for x in (d or {}).get("shares") or [] if isinstance(x, dict)] if isinstance(d, dict) else []

    async def revoke_share(self, share_id: int) -> None:
        """DELETE /shares/{id}: the link stops working at once."""
        r = await self._raw("DELETE", f"/api/v1/shares/{int(share_id)}")
        if r.status_code in (204, 404):
            return
        _check(r)


def _check(r: httpx.Response) -> Any:
    if 300 <= r.status_code < 400:
        raise NotSpeakr("This address redirects (usually to a sign-in page); enter the Speakr address itself")
    if r.status_code == 401:
        raise SpeakrAuthError(AUTH_MESSAGE)
    if r.status_code in (403, 410):
        body = _body(r)
        if r.status_code == 403 and body.get("code") == "insufficient_scope":
            raise SpeakrScopeError(body.get("required_scopes") or [], body.get("token_scopes") or [])
        if r.status_code == 410 and body.get("code") == "cursor_expired":
            raise SpeakrCursorExpired("Speakr's change cursor expired; a full pass follows")
        if r.status_code == 403 and body.get("code") in ("feature_disabled", "not_permitted", "https_required"):
            raise SpeakrRefused(body["code"], _reason(body))
    if r.status_code in (403, 404):
        raise SpeakrNotFound("not found in Speakr")
    if r.status_code == 413:
        raise SpeakrTooLarge(TOO_LARGE_MESSAGE)
    if r.status_code == 429:
        ra = seconds(r.headers.get("Retry-After"))
        raise SpeakrRateLimited(ra if ra is not None and ra >= 0 else DEFAULT_RETRY_AFTER)
    if r.status_code >= 500:
        raise SpeakrUnavailable(f"Speakr answered {r.status_code}")
    if r.status_code >= 400:
        # Speakr's own reason ({"error": ...}) when it gives one, e.g. a webhook address it refuses.
        reason = _reason(_body(r))
        raise SpeakrError(f"Speakr refused the request ({r.status_code})" + (f": {reason}" if reason else ""))
    ctype = r.headers.get("content-type", "").lower()
    text = r.text.lstrip()
    if "json" not in ctype or text.startswith("<"):
        raise NotSpeakr(NOT_SPEAKR_MESSAGE)
    try:
        return r.json()
    except ValueError:
        raise NotSpeakr(NOT_SPEAKR_MESSAGE) from None


def _body(r: httpx.Response) -> dict[str, Any]:
    try:
        body = r.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _reason(body: dict[str, Any]) -> str:
    return re.sub(r"\s+", " ", str(body.get("error") or "")).strip()[:400]
