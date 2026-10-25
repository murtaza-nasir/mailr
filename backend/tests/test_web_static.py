"""The web app's files go out precompressed with cache headers (core/webstatic.py), and the API's
JSON is gzipped (core/compress.py)."""

from __future__ import annotations

import gzip

import pytest
from httpx import ASGITransport, AsyncClient

from mailr.core import webstatic

JS = b"export const x = 1;\n" * 400


@pytest.fixture
def build(tmp_path, monkeypatch):
    """A small web build: a hashed script with its copies, index.html, sw.js and an icon."""
    d = tmp_path / "static"
    (d / "assets").mkdir(parents=True)
    (d / "assets" / "index-abc123.js").write_bytes(JS)
    (d / "assets" / "index-abc123.js.br").write_bytes(b"BR" + JS[:10])  # content does not matter here
    (d / "assets" / "index-abc123.js.gz").write_bytes(gzip.compress(JS))
    (d / "assets" / "plain-def456.css").write_bytes(b"a{}")
    (d / "index.html").write_bytes(b"<!doctype html><title>mailr</title>" * 50)
    (d / "index.html.gz").write_bytes(gzip.compress((d / "index.html").read_bytes()))
    (d / "sw.js").write_bytes(b"const VERSION = 'x'\n")
    (d / "icon-192.png").write_bytes(b"\x89PNG....")
    import mailr.main

    monkeypatch.setattr(mailr.main, "STATIC", d)
    return d


@pytest.fixture
async def web(build, _bootstrapped):
    from mailr.config import get_settings
    from mailr.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url=get_settings().base_url) as c:
        yield c


async def test_hashed_assets_brotli_first_and_kept_for_a_year(web):
    r = await web.get("/assets/index-abc123.js", headers={"Accept-Encoding": "gzip, deflate, br"})
    assert r.status_code == 200
    assert r.headers["content-encoding"] == "br"
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert r.headers["vary"] == "Accept-Encoding"
    assert r.headers["content-type"].startswith("text/javascript")
    assert r.headers["content-length"] == str(len(b"BR" + JS[:10]))


async def test_gzip_when_brotli_is_not_accepted(web):
    r = await web.get("/assets/index-abc123.js", headers={"Accept-Encoding": "gzip"})
    assert r.headers["content-encoding"] == "gzip"
    assert r.content == JS  # httpx undoes the gzip
    r = await web.get("/assets/index-abc123.js", headers={"Accept-Encoding": "br;q=0, gzip"})
    assert r.headers["content-encoding"] == "gzip"


async def test_no_coding_the_plain_file(web):
    r = await web.get("/assets/index-abc123.js", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in r.headers
    assert r.content == JS
    assert r.headers["vary"] == "Accept-Encoding"
    # A file without copies: no Vary, still immutable.
    r = await web.get("/assets/plain-def456.css", headers={"Accept-Encoding": "br, gzip"})
    assert "content-encoding" not in r.headers and "vary" not in r.headers
    assert r.headers["cache-control"] == webstatic.IMMUTABLE


async def test_revalidation_per_coding(web):
    r = await web.get("/assets/index-abc123.js", headers={"Accept-Encoding": "br"})
    etag = r.headers["etag"]
    r2 = await web.get("/assets/index-abc123.js", headers={"Accept-Encoding": "br", "If-None-Match": etag})
    assert r2.status_code == 304
    # The gzip copy is another representation: the Brotli ETag does not match it.
    r3 = await web.get("/assets/index-abc123.js", headers={"Accept-Encoding": "gzip", "If-None-Match": etag})
    assert r3.status_code == 200 and r3.headers["content-encoding"] == "gzip"


async def test_the_page_and_the_service_worker_are_always_revalidated(web):
    for path in ("/", "/mail/inbox", "/index.html"):
        r = await web.get(path, headers={"Accept-Encoding": "gzip"})
        assert r.status_code == 200, path
        assert r.headers["cache-control"] == "no-cache", path
        assert r.headers["content-encoding"] == "gzip", path
        assert r.text.startswith("<!doctype html>")
    r = await web.get("/sw.js")
    assert r.headers["cache-control"] == "no-cache"
    r = await web.get("/icon-192.png")
    assert r.headers["cache-control"] == "public, max-age=86400"


def test_accept_encoding_parsing():
    assert webstatic.accepted("gzip, deflate, br, zstd") >= {"gzip", "br"}
    assert webstatic.accepted("br;q=0, gzip;q=0.5") == {"gzip"}
    assert webstatic.accepted("*") >= {"br", "gzip"}
    assert webstatic.accepted("") == set()


@pytest.mark.db_user
async def test_api_json_is_gzipped(client):
    r = await client.get("/api/v1/layouts", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert len(r.content) > 1024
    assert r.headers["content-encoding"] == "gzip"
    assert "Accept-Encoding" in r.headers["vary"]
    assert r.json()["layouts"]
    # Without gzip in the request: plain.
    r = await client.get("/api/v1/layouts", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in r.headers


@pytest.mark.db_user
async def test_api_sign_in_answers_are_not_compressed(client):
    r = await client.get("/api/v1/auth/session", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert "content-encoding" not in r.headers


async def test_api_compresses_only_text(app):
    """An attachment or image (any type outside the list) goes as it is."""
    from fastapi import Response

    from mailr.core.compress import ApiGZip

    async def inner(scope, receive, send):
        r = Response(b"\0" * 5000, media_type="application/pdf")
        await r(scope, receive, send)

    mw = ApiGZip(inner)
    async with AsyncClient(transport=ASGITransport(app=mw), base_url="http://t") as c:
        r = await c.get("/api/v1/x", headers={"Accept-Encoding": "gzip"})
        assert "content-encoding" not in r.headers and len(r.content) == 5000

    async def text(scope, receive, send):
        r = Response(b"a" * 5000, media_type="application/json")
        await r(scope, receive, send)

    async with AsyncClient(transport=ASGITransport(app=ApiGZip(text)), base_url="http://t") as c:
        r = await c.get("/api/v1/x", headers={"Accept-Encoding": "gzip"})
        assert r.headers["content-encoding"] == "gzip"
        assert int(r.headers["content-length"]) < 100

    async def small(scope, receive, send):
        r = Response(b"a" * 1000, media_type="application/json")
        await r(scope, receive, send)

    async with AsyncClient(transport=ASGITransport(app=ApiGZip(small)), base_url="http://t") as c:
        r = await c.get("/api/v1/x", headers={"Accept-Encoding": "gzip"})
        assert "content-encoding" not in r.headers


async def test_a_top_level_file_without_copies_revalidates(web):
    # A file with no precompressed copy (the app icons) once answered 500 to If-None-Match: its
    # ETag was set only when the response was sent, after the comparison.
    r = await web.get("/icon-192.png")
    etag = r.headers["etag"]
    r2 = await web.get("/icon-192.png", headers={"If-None-Match": etag})
    assert r2.status_code == 304
    r3 = await web.get("/icon-192.png", headers={"If-None-Match": '"other", '})
    assert r3.status_code == 200
