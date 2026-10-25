"""The HTTP edge of public ids (core/publicid.py): one ASGI middleware.

In:  conversation/message ids in the path (/conversations/{id}, /messages/{id}[/cid/{id}],
     /drafts/{id}), in the query string and in JSON bodies (by key name; `ids` by route) are
     decoded to internal ids. An invalid id is a 404, like a missing object.
Out: JSON responses under /api/ are encoded by key name. Conversation and message objects
     carry their own `id` as ConvId/MsgId, which PublicIdJSONResponse (the default response
     class) encodes before the middleware sees the bytes. Server-sent events are encoded where
     they are produced (publicid.out).
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode

from fastapi.responses import JSONResponse

from mailr.core import publicid

# (/messages/{id}/cid/{cid}: that cid is an inline image's Content-ID, not a conversation.)
_PATH = [
    (re.compile(r"^(/api/v1/conversations/)(\d+)(/.*)?$"), ("conversation",)),
    (re.compile(r"^(/api/v1/messages/)(\d+)(/.*)?$"), ("message",)),
    (re.compile(r"^(/api/v1/drafts/)(\d+)(/.*)?$"), ("message",)),
]
# Bodies whose bare `ids` / `exclude_ids` lists name conversations or messages.
_BODY_EXTRA = {
    "/api/v1/conversations/actions": {"ids": "conversation", "exclude_ids": "conversation"},
    "/api/v1/conversations/merge": {"ids": "conversation"},
    "/api/v1/messages/actions": {"ids": "message"},
}
_QUERY_KEYS = {**{k: "conversation" for k in publicid.CONV_KEYS}, **{k: "message" for k in publicid.MSG_KEYS},
               "exclude_ids": "conversation"}


class PublicIdJSONResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return super().render(publicid.out(content))


def _rewrite_path(path: str) -> str:
    for rx, (kind, *_rest) in _PATH:
        m = rx.match(path)
        if m:
            return f"{m.group(1)}{publicid.decode(kind, m.group(2))}{m.group(3) or ''}"
    return path


def _rewrite_query(qs: bytes) -> bytes:
    if not qs:
        return qs
    pairs = parse_qsl(qs.decode("latin-1"), keep_blank_values=True)
    changed = False
    out = []
    for k, v in pairs:
        kind = _QUERY_KEYS.get(k)
        if kind and v:
            v = ",".join(str(publicid.decode(kind, x)) for x in v.split(",") if x)
            changed = True
        out.append((k, v))
    return urlencode(out).encode("latin-1") if changed else qs


async def _not_found(send) -> None:
    body = b'{"detail":"not found"}'
    await send({"type": "http.response.start", "status": 404,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


# The largest JSON request body the middleware reads (drafts with long HTML are the largest).
MAX_JSON_BODY = 10 * 1024 * 1024


async def _too_large(send) -> None:
    body = b'{"detail":"the request is too large"}'
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


def _is_json(ctype: bytes) -> bool:
    """A JSON media type as FastAPI reads one: application/json or application/<x>+json, in any
    case and with any parameters. A narrower test would let a JSON body with internal ids slip
    past the translation while the route still parses it."""
    media = ctype.split(b";", 1)[0].strip().lower()
    main, _, sub = media.partition(b"/")
    return main == b"application" and (sub == b"json" or sub.endswith(b"+json"))


class PublicIdMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not publicid.enabled() or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)
        try:
            path = _rewrite_path(scope["path"])
            qs = _rewrite_query(scope.get("query_string", b""))
        except publicid.InvalidId:
            return await _not_found(send)
        scope = {**scope, "path": path, "raw_path": path.encode(), "query_string": qs}

        # Request body: JSON only, decoded by key (read fully; API bodies are small). Webhook
        # receivers under /api/v1/integrations/ get the exact bytes: their senders sign the raw
        # body, and re-serialised JSON would never match the signature.
        headers = dict(scope.get("headers") or [])
        outer_receive = receive
        raw_body = path.startswith("/api/v1/integrations/")
        if _is_json(headers.get(b"content-type", b"")) and not raw_body:
            # Read before authentication, so the size is capped here as well as by the outer
            # body limit: a JSON API body over MAX_JSON_BODY is refused, never buffered.
            try:
                declared = int(headers.get(b"content-length", b"0") or 0)
            except ValueError:
                declared = 0
            if declared > MAX_JSON_BODY:
                return await _too_large(send)
            chunks = []
            seen = 0
            more = True
            while more:
                msg = await receive()
                if msg["type"] != "http.request":
                    break
                chunks.append(msg.get("body", b""))
                seen += len(chunks[-1])
                if seen > MAX_JSON_BODY:
                    return await _too_large(send)
                more = msg.get("more_body", False)
            raw = b"".join(chunks)
            if raw:
                try:
                    data = publicid.into(json.loads(raw), _BODY_EXTRA.get(path))
                    # An undo payload's `labels` map is keyed by message id.
                    if path in _BODY_EXTRA and isinstance(data, dict) and isinstance(data.get("labels"), dict):
                        data["labels"] = {str(publicid.decode("message", k)): v for k, v in data["labels"].items()}
                except publicid.InvalidId:
                    return await _not_found(send)
                except ValueError:
                    data = None  # not JSON after all: let the route answer 422
                if data is not None:
                    raw = json.dumps(data).encode()
            scope["headers"] = [(k, v) for k, v in scope["headers"] if k != b"content-length"] + [
                (b"content-length", str(len(raw)).encode())]
            sent = False

            async def receive():  # replays the rewritten body once
                # After the body, pass through to the server: a streaming response (the assistant's
                # SSE) waits on receive() for the client to leave. Answering "disconnect" here
                # closed every chat stream right after it opened.
                nonlocal sent
                if sent:
                    return await outer_receive()
                sent = True
                return {"type": "http.request", "body": raw, "more_body": False}

        # Response: JSON bodies are encoded by key; streams pass through untouched.
        start: dict[str, Any] | None = None
        buf: list[bytes] = []

        async def send_wrapper(msg):
            nonlocal start
            if msg["type"] == "http.response.start":
                ctype = dict(msg.get("headers") or []).get(b"content-type", b"")
                if b"application/json" in ctype:
                    start = msg
                    return
                return await send(msg)
            if start is None:
                return await send(msg)
            buf.append(msg.get("body", b""))
            if msg.get("more_body"):
                return
            body = b"".join(buf)
            try:
                body = json.dumps(publicid.out(json.loads(body)), ensure_ascii=False).encode() if body else body
            except ValueError:
                pass
            hdrs = [(k, v) for k, v in start["headers"] if k != b"content-length"]
            hdrs.append((b"content-length", str(len(body)).encode()))
            await send({**start, "headers": hdrs})
            await send({"type": "http.response.body", "body": body})

        await self.app(scope, receive, send_wrapper)
