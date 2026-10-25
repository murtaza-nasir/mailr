"""Shared helpers, from test_browser_routes.py."""

from __future__ import annotations

import httpx

from helpers.websearch import _mock


def _browserless(monkeypatch, content, function=None):
    """A fake browserless: /content answers `content(req)`, /function answers `function(req)` (404 when None)."""
    def handler(req):
        if req.url.path == "/function":
            return function(req) if function else httpx.Response(404, text="Not Found")
        return content(req)

    return _mock(monkeypatch, handler)
