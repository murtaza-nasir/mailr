"""Shared helpers, from test_own_links_readers.py."""

from __future__ import annotations

import uuid

from mailr.db import sessionmaker
from mailr.models import WebSearchProvider
from mailr.websearch import service

# Chrome's WebRTC switches on every browser read.
WEBRTC = ["--force-webrtc-ip-handling-policy=disable_non_proxied_udp", "--webrtc-ip-handling-policy=disable_non_proxied_udp"]


PAGE = "<html><head><title>Self-hosted Photoshop</title></head><body><main><p>" + "A long article. " * 40 + \
    "</p></main></body></html>"


async def _web_on():
    """read_page needs an enabled search provider (the web tools are offered only then)."""
    async with sessionmaker()() as db:
        db.add(WebSearchProvider(name=f"S-{uuid.uuid4().hex[:6]}", kind="searxng", base_url="http://searx.lan",
                                 network="local", priority=100, max_results=10, timeout_s=5))
        await db.commit()


async def _reader(kind="browserless", base_url="http://browserless.lan:3000", key=None, network="local", **kw) -> int:
    from mailr.core.crypto import encrypt_instance, web_search_provider_context

    async with sessionmaker()() as db:
        p = WebSearchProvider(name=f"R-{uuid.uuid4().hex[:6]}", purpose="reader", kind=kind, base_url=base_url,
                              network=network, **{"priority": 100, "max_results": 10, "timeout_s": 20, **kw})
        db.add(p)
        await db.flush()
        if key:
            p.api_key_enc = encrypt_instance(key, context=web_search_provider_context(p.id))
        await db.commit()
        return p.id


def _lan_ok(monkeypatch):
    async def ok(network, base):
        return None

    monkeypatch.setattr(service, "check_target", ok)
