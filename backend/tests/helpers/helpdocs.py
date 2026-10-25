"""Shared helpers, from test_helpdocs.py."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

INLINE = 'var palette=__md_get("__palette");'


@pytest.fixture
def help_site(tmp_path, monkeypatch):
    """A small built site in place of mailr/help."""
    from mailr.core import helpdocs

    root = tmp_path / "help"
    (root / "user-guide" / "inbox").mkdir(parents=True)
    (root / "assets" / "javascripts").mkdir(parents=True)
    (root / "index.html").write_text(
        f'<!doctype html><title>mailr</title><script>{INLINE}</script>'
        '<script id="__config" type="application/json">{"base": "."}</script>'
        '<script src="assets/javascripts/bundle.d7400e89.min.js"></script><h1>Home</h1>')
    (root / "user-guide" / "inbox" / "index.html").write_text("<!doctype html><h1>The inbox</h1>")
    (root / "assets" / "javascripts" / "bundle.d7400e89.min.js").write_text("console.log(1)")
    (root / "search").mkdir()
    (root / "search" / "search_index.json").write_text("{}")
    (root / "404.html").write_text("<!doctype html><h1>Page not found</h1>")
    (root / ".secret").write_text("no")
    (tmp_path / "outside.txt").write_text("outside")
    monkeypatch.setattr(helpdocs, "DIR", root)
    return root


@pytest.fixture
async def anon(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
