"""The server's own texts for the apps.

- GET /i18n/messages?lang=de → {"lang", "version", "messages"}: the `errors` section of the server
  catalogue (mailr/i18n/<lang>.json), keyed by the `code` API errors carry, English filling the gaps.
  No sign-in: the sign-in screens show errors too. An unknown language answers English.
- GET /i18n/languages → {"languages": [...], "default": "en"}: the languages the server has text for.
"""

from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Request, Response

from mailr import i18n

router = APIRouter(tags=["i18n"])


@router.get("/i18n/messages")
async def messages(request: Request, response: Response, lang: str | None = None):
    code = i18n.match(lang) or i18n.accept_language(request.headers.get("accept-language")) or i18n.DEFAULT
    table = i18n.section("errors", code)
    version = hashlib.sha1(json.dumps(table, sort_keys=True).encode()).hexdigest()[:12]
    response.headers["Cache-Control"] = "public, max-age=3600"
    response.headers["ETag"] = f'"{code}-{version}"'
    return {"lang": code, "version": version, "messages": table}


@router.get("/i18n/languages")
async def languages():
    return {"languages": i18n.available(), "default": i18n.DEFAULT}
