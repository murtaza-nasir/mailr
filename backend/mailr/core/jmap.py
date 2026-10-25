"""Minimal async JMAP client (RFC 8620 core, RFC 8621 mail)."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

log = logging.getLogger("mailr.jmap")

CORE = "urn:ietf:params:jmap:core"
MAIL = "urn:ietf:params:jmap:mail"
SUBMISSION = "urn:ietf:params:jmap:submission"
SIEVE = "urn:ietf:params:jmap:sieve"
USING = [CORE, MAIL]


class JMAPError(Exception):
    def __init__(self, kind: str, detail: Any = None):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


class JMAPClient:
    def __init__(self, base_url: str, auth: httpx.Auth | tuple[str, str], timeout: float = 60):
        self.base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(auth=auth, timeout=timeout, follow_redirects=True)
        self._session: dict[str, Any] | None = None
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        await self._http.aclose()

    # ----- session -----

    async def session(self, refresh: bool = False) -> dict[str, Any]:
        async with self._lock:
            if self._session is None or refresh:
                r = await self._http.get(f"{self.base_url}/.well-known/jmap")
                r.raise_for_status()
                self._session = r.json()
        return self._session


    def _rebase(self, url: str) -> str:
        """Stalwart advertises URLs with its public hostname; rewrite to our base URL."""
        from urllib.parse import urlsplit

        u = urlsplit(url)
        return self.base_url + u.path + (f"?{u.query}" if u.query else "")

    # ----- method calls -----

    async def call(self, calls: list[list[Any]], using: list[str] | None = None) -> list[list[Any]]:
        s = await self.session()
        body = {"using": using or USING, "methodCalls": calls}
        r = await self._http.post(self._rebase(s["apiUrl"]), json=body)
        if r.status_code >= 400:
            raise JMAPError("http", f"{r.status_code} {r.text[:500]}")
        data = r.json()
        out = data.get("methodResponses", [])
        for name, args, tag in out:
            if name == "error":
                raise JMAPError(args.get("type", "error"), {"tag": tag, **args})
        return out

    async def one(self, method: str, args: dict[str, Any], using: list[str] | None = None) -> dict:
        res = await self.call([[method, args, "0"]], using=using)
        return res[0][1]

    # ----- blobs -----

    async def upload(self, acc: str, data: bytes, content_type: str = "message/rfc822") -> dict[str, Any]:
        s = await self.session()
        url = self._rebase(s["uploadUrl"].replace("{accountId}", acc))
        r = await self._http.post(url, content=data, headers={"Content-Type": content_type})
        if r.status_code >= 400:
            raise JMAPError("upload", f"{r.status_code} {r.text[:300]}")
        return r.json()

    async def download(self, acc: str, blob_id: str, name: str = "blob",
                       type_: str = "application/octet-stream") -> bytes:
        s = await self.session()
        from urllib.parse import quote

        url = (
            s["downloadUrl"]
            .replace("{accountId}", acc)
            .replace("{blobId}", blob_id)
            .replace("{name}", quote(name, safe=""))
            .replace("{type}", quote(type_, safe=""))
        )
        r = await self._http.get(self._rebase(url))
        if r.status_code >= 400:
            raise JMAPError("download", f"{r.status_code} {r.text[:300]}")
        return r.content

    # ----- push -----

    async def events(self, types: str = "*") -> AsyncIterator[dict[str, Any]]:
        # With the admin service account, the stream carries changes for every account.
        """Yield StateChange objects from the EventSource endpoint. Reconnects on error."""
        s = await self.session()
        url = self._rebase(
            s["eventSourceUrl"].replace("{types}", types).replace("{closeafter}", "no")
            .replace("{ping}", "60")
        )
        while True:
            try:
                async with self._http.stream("GET", url, timeout=None) as r:
                    event, data = None, []
                    async for line in r.aiter_lines():
                        if line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("data:"):
                            data.append(line[5:].strip())
                        elif line == "":
                            if event == "state" and data:
                                try:
                                    yield json.loads("\n".join(data))
                                except ValueError:
                                    pass
                            event, data = None, []
            except (TimeoutError, httpx.HTTPError) as e:
                log.info("JMAP event stream reconnecting: %s", e)
                await asyncio.sleep(3)
