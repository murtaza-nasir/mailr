# ACT-SURFACES: api
"""The image proxy and tracker blocking (core/imageproxy.py, core/trackers.py, core/render.py).

Covers (api): ACT mail.show_images — GET /messages/{id}/images/{hash} serves only images the
caller's own message references; fetches go through netguard (no LAN addresses, redirects
checked again), take PNG/JPEG/GIF/WebP/AVIF by magic bytes only (never SVG), respect the size
cap, use the egress proxy when one is set, and dedupe by content. Tracker pixels are removed and
counted, known link wrappers are unwrapped, and Spam is never prefetched.
"""

from __future__ import annotations

import base64
import os
import struct
import threading
import time
import uuid
import zlib
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlsplit

import pytest
from conftest import mail_date
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from mailr.core import imageproxy, netguard, trackers
from mailr.core.render import render_body


def png(w: int = 4, h: int = 3, salt: bytes = b"") -> bytes:
    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw + salt)) + chunk(b"IEND", b""))


SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


class _Server:
    """A local HTTP server; routes map a path to (status, headers, body). As an HTTP proxy it
    sees absolute URIs ("GET http://host/path"), which it records."""

    def __init__(self, routes: dict[str, tuple[int, dict, bytes]]):
        self.routes = routes
        self.seen: list[str] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.seen.append(self.path)
                path = urlsplit(self.path).path if self.path.startswith("http") else self.path.split("?")[0]
                status, headers, body = outer.routes.get(path, (404, {}, b"no"))
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def local_fetch(monkeypatch, tmp_path):
    """Plain http to 127.0.0.1 (and the made-up img.proxytest.example) passes the guard; every
    other address goes through the real netguard checks. A private cache directory."""
    monkeypatch.setattr(imageproxy, "ALLOW_HTTP", True)
    real = imageproxy._guard

    async def guard(url, egress=""):
        if urlsplit(url).hostname in ("127.0.0.1", "img.proxytest.example"):
            return
        await real(url, egress)

    monkeypatch.setattr(imageproxy, "_guard", guard)
    monkeypatch.setattr(imageproxy, "_root", lambda: tmp_path / "imgcache")
    return tmp_path


@pytest.fixture
def tmp_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(imageproxy, "_root", lambda: tmp_path / "imgcache")
    return tmp_path


def test_magic_bytes_decide_the_type_and_svg_is_refused():
    assert imageproxy.check_image(png(), "image/png")[:3] == ("image/png", 4, 3)
    # Mislabelled but real: the bytes win.
    assert imageproxy.check_image(png(), "application/octet-stream")[0] == "image/png"
    with pytest.raises(imageproxy.Refused):
        imageproxy.check_image(SVG, "image/svg+xml")
    with pytest.raises(imageproxy.Refused):
        imageproxy.check_image(SVG, "image/png")  # SVG bytes under an image label
    with pytest.raises(imageproxy.Refused):
        imageproxy.check_image(b"<html>hi</html>", "text/html")
    gif = b"GIF89a" + struct.pack("<HH", 1, 1) + b"\x00" * 20
    assert imageproxy.check_image(gif, "image/gif") == ("image/gif", 1, 1)
    webp = b"RIFF\x00\x00\x00\x00WEBPVP8X" + b"\x00" * 8 + (639).to_bytes(3, "little") + (479).to_bytes(3, "little")
    assert imageproxy.check_image(webp, "image/webp") == ("image/webp", 640, 480)
    huge = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + struct.pack(">II", 60000, 60000) + b"\x00" * 10
    with pytest.raises(imageproxy.Refused, match="too large"):
        imageproxy.check_image(huge, "image/png")


async def test_fetch_types_size_cap_and_dedupe(local_fetch, monkeypatch):
    img = png(salt=b"a")
    srv = _Server({
        "/a.png": (200, {"Content-Type": "image/png"}, img),
        "/same.png": (200, {"Content-Type": "image/png"}, img),
        "/logo.svg": (200, {"Content-Type": "image/svg+xml"}, SVG),
        "/sneaky.png": (200, {"Content-Type": "image/png"}, SVG),
        "/big.png": (200, {"Content-Type": "image/png"}, png(salt=b"b") + b"\x00" * 5000),
        "/page": (200, {"Content-Type": "text/html"}, b"<html></html>"),
    })
    try:
        monkeypatch.setattr(imageproxy, "MAX_BYTES", 2000)
        a = await imageproxy.fetch(f"{srv.base}/a.png")
        assert a.status == "ok" and a.type == "image/png" and (a.width, a.height) == (4, 3)
        same = await imageproxy.fetch(f"{srv.base}/same.png")
        assert same.sha == a.sha
        assert len(list((local_fetch / "imgcache" / "blobs").glob("*/*"))) == 1  # stored once
        assert imageproxy.read_blob(a) == img
        assert (await imageproxy.fetch(f"{srv.base}/logo.svg")).status == "refused"
        assert (await imageproxy.fetch(f"{srv.base}/sneaky.png")).status == "refused"
        assert (await imageproxy.fetch(f"{srv.base}/page")).status == "refused"
        big = await imageproxy.fetch(f"{srv.base}/big.png")
        assert big.status == "refused" and "size limit" in big.error
        # A cached result is used again without asking the server.
        n = len(srv.seen)
        await imageproxy.fetch(f"{srv.base}/a.png")
        assert len(srv.seen) == n
    finally:
        srv.close()


async def test_lan_addresses_and_redirects_to_them_are_refused(local_fetch):
    srv = _Server({"/hop": (302, {"Location": "http://10.1.2.3/x.png"}, b""),
                   "/hop2": (302, {"Location": "http://169.254.169.254/latest/meta-data"}, b""),
                   "/ok": (302, {"Location": "/a.png"}, b""),
                   "/a.png": (200, {"Content-Type": "image/png"}, png(salt=b"r"))})
    try:
        for url in ("http://192.168.1.10/a.png", "https://10.0.0.5/a.png", "http://[::1]/a.png",
                    "http://localhost/a.png"):
            e = await imageproxy.fetch(url)
            assert e.status == "refused", (url, e)
        assert (await imageproxy.fetch(f"{srv.base}/hop")).status == "refused"
        assert (await imageproxy.fetch(f"{srv.base}/hop2")).status == "refused"
        assert (await imageproxy.fetch(f"{srv.base}/ok")).status == "ok"  # a public redirect is followed
    finally:
        srv.close()


async def test_production_guard_is_https_and_public_only():
    with pytest.raises(netguard.UnsafeTarget):
        await imageproxy._guard("http://127.0.0.1/a.png")
    with pytest.raises(netguard.UnsafeTarget):
        await imageproxy._guard("https://192.168.1.1/a.png")
    assert imageproxy.normalize("http://cdn.example.org/a.png") == "https://cdn.example.org/a.png"
    assert imageproxy.normalize("//cdn.example.org/a.png") == "https://cdn.example.org/a.png"
    assert imageproxy.normalize("javascript:alert(1)") is None


async def test_the_egress_proxy_carries_the_fetch(local_fetch):
    proxy = _Server({"/p.png": (200, {"Content-Type": "image/png"}, png(salt=b"p"))})
    try:
        e = await imageproxy.fetch("http://img.proxytest.example/p.png", egress=proxy.base)
        assert e.status == "ok"
        assert proxy.seen == ["http://img.proxytest.example/p.png"]  # an absolute URI: a proxy request
    finally:
        proxy.close()


def test_egress_setting_is_validated_and_masked():
    assert imageproxy.check_egress("") == ""
    assert imageproxy.check_egress("http://127.0.0.1:8888") == "http://127.0.0.1:8888"
    for bad in ("socks5://127.0.0.1:1080", "http://127.0.0.1:8888/path", "ftp://x"):
        with pytest.raises(ValueError):
            imageproxy.check_egress(bad)
    assert imageproxy.mask_egress("http://u:secret@media:8888") == "http://u:***@media:8888"
    sealed = imageproxy.seal_egress("http://u:secret@media:8888")
    assert "secret" not in sealed and imageproxy.open_egress(sealed) == "http://u:secret@media:8888"


# ---------------- trackers and links ----------------

def _html(html: str) -> bytes:
    m = EmailMessage()
    m["From"] = "news@example.org"
    m["To"] = "me@example.com"
    m["Subject"] = "News"
    m["Message-ID"] = f"<{uuid.uuid4().hex}@example.org>"
    m["Date"] = mail_date(9)
    m.set_content("plain")
    m.add_alternative(html, subtype="html")
    return bytes(m)


TRACKED = (
    '<p>Hello</p><img src="https://cdn.example.org/hero.png" alt="Hero">'
    '<img src="https://example.us1.list-manage.com/track/open.php?u=1&id=2&e=3">'
    '<img src="https://url123.shop.example/wf/open?upn=abc">'
    '<img src="https://pics.example.net/p.gif" width="1" height="1">'
    '<img src="https://pics.example.net/q.gif" style="display:none">'
    '<img src="https://email.mg.shop.example/o/eJwFwUEKgCAQ">'
    '<img src="https://www.google-analytics.com/collect?v=1&tid=UA-1">'
)


def test_trackers_are_removed_and_counted():
    for allow in (False, True):
        out = render_body(_html(TRACKED), 1, allow_remote=allow)
        assert out["trackers"]["count"] == 6
        assert out["trackers"]["domains"] == sorted({"example.us1.list-manage.com", "url123.shop.example",
                                                      "pics.example.net", "email.mg.shop.example",
                                                      "www.google-analytics.com"})
        assert "list-manage" not in out["html"] and "wf/open" not in out["html"] and "p.gif" not in out["html"]
        assert "hero.png" in out["html"]


def test_tracker_rules():
    yes = ["https://us5.list-manage.com/track/open.php?u=x", "https://u1.ct.sendgrid.net/wf/open?upn=x",
           "https://x.r.us-east-1.awstrack.me/I0/0100/abc", "https://ea.pstmrk.it/open?x=1",
           "https://www.linkedin.com/emimp/ip_abc.gif", "https://mailtrack.io/trace/mail/abc.png",
           "https://eotrx.substackcdn.com/open?token=x", "https://click.shop.example/q/abc~~/AAAA~/RgR"]
    no = ["https://cdn.example.org/logo.png", "https://us5.list-manage.com/track/click?u=x",
          "https://mcusercontent.com/abc/images/x.png", "https://firebasestorage.googleapis.com/v0/b/x/o/eJpg.png",
          "https://x.r.us-east-1.awstrack.me/L0/https:%2F%2Fexample.com/1/abc"]
    assert [u for u in yes if not trackers.is_tracker_url(u)] == []
    assert [u for u in no if trackers.is_tracker_url(u)] == []


def test_link_unwrapping():
    b64 = base64.urlsafe_b64encode(b'{"e":"https://example.com/post?ref=1","p":1}').decode().rstrip("=")
    mandrill = base64.b64encode(b'{"s":"x","v":1,"p":"{\\"u\\":1,\\"url\\":\\"https:\\\\/\\\\/example.com\\\\/m\\"}"}').decode()
    cases = {
        f"https://nam12.safelinks.protection.outlook.com/?url={quote('https://example.com/a?b=1&c=2', safe='')}&data=x":
            "https://example.com/a?b=1&c=2",
        "https://www.google.com/url?q=https://example.com/g&sa=D": "https://example.com/g",
        "https://urldefense.proofpoint.com/v2/url?u=https-3A__example.com_path-3Fx-3D1&d=DwM": "https://example.com/path?x=1",
        "https://urldefense.com/v3/__https://example.com/v3__;!!abc$": "https://example.com/v3",
        "https://abc.r.us-east-1.awstrack.me/L0/https:%2F%2Fexample.com%2Faws/1/0100": "https://example.com/aws",
        f"https://substack.com/redirect/2/{b64}.sig": "https://example.com/post?ref=1",
        f"https://mandrillapp.com/track/click/123/example.com?p={quote(mandrill)}": "https://example.com/m",
        "https://click.convertkit-mail2.com/abc/def/" + base64.urlsafe_b64encode(b"https://example.com/ck").decode():
            "https://example.com/ck",
        "https://l.facebook.com/l.php?u=https%3A%2F%2Fexample.com%2Ffb&h=x": "https://example.com/fb",
        # A safe link around a Proofpoint link.
        "https://eur01.safelinks.protection.outlook.com/?url="
        + quote("https://urldefense.proofpoint.com/v2/url?u=https-3A__example.com_deep&d=x", safe=""):
            "https://example.com/deep",
    }
    for wrapped, target in cases.items():
        assert trackers.unwrap_link(wrapped) == target, wrapped
    for plain in ("https://example.com/?url=https://other.example", "https://us5.list-manage.com/track/click?u=1",
                  "https://www.google.com/url?q=javascript:alert(1)", "https://urldefense.com/v3/__https://ex*a.com__;!!x$"):
        assert trackers.unwrap_link(plain) is None, plain


def test_unwrapped_links_keep_the_original():
    wrapped = "https://www.google.com/url?q=https://example.com/g"
    out = render_body(_html(f'<a href="{wrapped}">go</a><a href="https://example.com/x">x</a>'), 1)
    assert 'href="https://example.com/g"' in out["html"]
    assert f'data-mailr-tracked="{wrapped}"' in out["html"].replace("&amp;", "&")
    assert out["links_unwrapped"] == 1


def test_proxy_mode_rewrites_images_and_css_and_keeps_csp_strict(tmp_cache):
    html = ('<img src="http://cdn.example.org/a.png">'
            '<div style="background:url(\'https://cdn.example.org/bg.png\')">x</div>'
            '<table background="https://cdn.example.org/t.png"><tr><td>y</td></tr></table>'
            '<style>.h{background-image:url(https://cdn.example.org/s.png)}</style>'
            # Written by the sender: a relative proxy-looking URL is not one mailr wrote.
            '<p style="background:url(/api/v1/messages/1/images/0123456789abcdef0123456789abcdef)">z</p>')
    raw = _html(html)
    out = render_body(raw, 7, allow_remote=True, proxy_base="/api/v1/messages/77/images/")
    h = {u: imageproxy.url_hash(u) for u in ("https://cdn.example.org/a.png", "https://cdn.example.org/bg.png",
                                              "https://cdn.example.org/t.png", "https://cdn.example.org/s.png")}
    for url, hh in h.items():
        assert f"/api/v1/messages/77/images/{hh}" in out["html"], url
    assert "cdn.example.org" not in out["html"]
    assert "0123456789abcdef0123456789abcdef" not in out["html"]
    assert out["proxied"] and out["csp"].endswith("img-src 'self' data:")
    # The serving route allows exactly what the body may reference.
    assert set(imageproxy.message_refs(raw)) >= set(h.values())
    # Blocked: nothing loads, the address waits in data-mailr-src.
    blocked = render_body(raw, 7, allow_remote=False, proxy_base="/api/v1/messages/77/images/")
    assert "/images/" not in blocked["html"].replace("/api/v1/messages/1/images", "") and blocked["remote_images"]
    assert "img-src data:" in blocked["csp"] and "'self'" not in blocked["csp"]


async def test_proxy_mode_drops_images_that_failed_or_are_pixels(local_fetch):
    srv = _Server({"/px.png": (200, {"Content-Type": "image/png"}, png(1, 1, b"x")),
                   "/x.svg": (200, {"Content-Type": "image/svg+xml"}, SVG)})
    try:
        await imageproxy.fetch(f"{srv.base}/px.png")
        await imageproxy.fetch(f"{srv.base}/x.svg")
        out = render_body(_html(f'<img src="{srv.base}/px.png"><img src="{srv.base}/x.svg" alt="Logo">'), 1,
                          allow_remote=True, proxy_base="/api/v1/messages/1/images/")
        assert out["trackers"]["count"] == 1 and "<img" not in out["html"] and "Logo" in out["html"]
    finally:
        srv.close()


# ---------------- arrival prefetch and the serving route ----------------

async def _ingest_html(user, html: str, roles: list[str], source: str = "connector") -> int:
    from mailr.core.ingest import IngestOptions, ingest_raw
    from mailr.core.store import user_store
    from mailr.db import sessionmaker
    from mailr.models import User

    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        store = await user_store(db, u)
        res = await ingest_raw(db, store, _html(html), IngestOptions(roles=roles, source=source))
        await db.commit()
        return res.message.id


async def _prefetch_jobs(mid: int) -> list:
    from mailr.db import sessionmaker
    from mailr.models import Job

    async with sessionmaker()() as db:
        return list((await db.execute(select(Job).where(Job.dedupe_key == f"images.prefetch:{mid}"))).scalars())


async def test_spam_is_never_prefetched(client, user, monkeypatch):
    fetched = []

    async def fake_fetch_all(urls, egress, user_id=None):
        fetched.extend(urls)
        return {}

    monkeypatch.setattr(imageproxy, "fetch_all", fake_fetch_all)
    html = '<img src="https://cdn.example.org/n.png">'
    inbox = await _ingest_html(user, html, ["inbox"])
    junk = await _ingest_html(user, html, ["junk"])
    history = await _ingest_html(user, html, ["inbox"], source="import")
    assert len(await _prefetch_jobs(inbox)) == 1
    assert await _prefetch_jobs(junk) == [] and await _prefetch_jobs(history) == []
    # Moved to Spam before the job ran: the job fetches nothing.
    r = await client.post("/api/v1/messages/actions", json={"ids": [inbox], "action": "spam"})
    assert r.status_code == 200, r.text
    await imageproxy.prefetch_job({"message_id": inbox}, None)
    assert fetched == []
    # "Ask" means no prefetch at all.
    await client.patch("/api/v1/me/settings", json={"images": "ask"})
    asked = await _ingest_html(user, html, ["inbox"])
    assert await _prefetch_jobs(asked) == []


async def test_prefetch_job_fetches_a_new_message(client, user, monkeypatch):
    fetched = []

    async def fake_fetch_all(urls, egress, user_id=None):
        fetched.extend(urls)
        return {}

    monkeypatch.setattr(imageproxy, "fetch_all", fake_fetch_all)
    mid = await _ingest_html(user, '<img src="https://cdn.example.org/new.png">' + TRACKED, ["inbox"])
    from mailr.db import sessionmaker
    from mailr.models import Job

    async with sessionmaker()() as db:  # the triage jobs ran
        for j in (await db.execute(select(Job).where(Job.dedupe_key.in_([f"intel.message:{mid}", f"spam.classify:{mid}"])))).scalars():
            j.status = "done"
        await db.commit()
    await imageproxy.prefetch_job({"message_id": mid}, None)
    assert fetched == ["https://cdn.example.org/new.png", "https://cdn.example.org/hero.png"]  # no trackers


async def test_serving_route_checks_ownership(app, client, user, local_fetch):
    from mailr.core.users import create_user
    from mailr.db import sessionmaker

    img = png(salt=b"serve")
    srv = _Server({"/s.png": (200, {"Content-Type": "image/png"}, img),
                   "/t.svg": (200, {"Content-Type": "image/svg+xml"}, SVG)})
    try:
        url = f"{srv.base}/s.png"
        mid = await _ingest_html(user, f'<p>hi</p><img src="{url}"><img src="{srv.base}/t.svg">', ["inbox"])
        h = imageproxy.url_hash(url)
        body = (await client.get(f"/api/v1/messages/{mid}/body", params={"images": "allowed"})).json()
        assert f"/images/{h}" in body["html"] and body["proxied"] and body["junk"] is False
        assert body["trackers"] == {"count": 0, "domains": []}
        r = await client.get(f"/api/v1/messages/{mid}/images/{h}")  # fetched on first open
        assert r.status_code == 200 and r.content == img
        assert r.headers["content-type"] == "image/png" and r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["cache-control"].startswith("private")
        # SVG is never served; an unknown hash is not fetched.
        assert (await client.get(f"/api/v1/messages/{mid}/images/{imageproxy.url_hash(srv.base + '/t.svg')}")).status_code == 404
        assert (await client.get(f"/api/v1/messages/{mid}/images/{'0' * 32}")).status_code == 404
        assert (await client.get(f"/api/v1/messages/{mid}/images/..%2f..%2fetc")).status_code == 404
        # Another user, even knowing the id and the hash, gets nothing.
        async with sessionmaker()() as db:
            other = await create_user(db, f"o-{uuid.uuid4().hex[:8]}@example.com", "Other", password="test-password-123")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c2:
            r = await c2.post("/api/v1/auth/login", json={"email": other.email, "password": "test-password-123"})
            assert r.status_code == 200
            assert (await c2.get(f"/api/v1/messages/{mid}/images/{h}")).status_code == 404
        # The cache served the second read without the sender's server.
        n = len(srv.seen)
        assert (await client.get(f"/api/v1/messages/{mid}/images/{h}")).status_code == 200
        assert len(srv.seen) == n
    finally:
        srv.close()


async def test_a_cached_image_is_fetched_again_for_another_user(local_fetch):
    """The first time a user asks for a URL the cache knows from someone else's mail, it is fetched
    again: how quickly the answer comes never tells one user what another's mail showed. The image
    is stored once."""
    img = png(salt=b"shared")
    srv = _Server({"/shared.png": (200, {"Content-Type": "image/png"}, img)})
    try:
        url = f"{srv.base}/shared.png"
        first = await imageproxy.fetch(url, user_id=101)
        assert first.status == "ok" and len(srv.seen) == 1
        assert imageproxy.lookup_for(imageproxy.url_hash(url), 101) is not None
        assert imageproxy.lookup_for(imageproxy.url_hash(url), 202) is None
        await imageproxy.fetch(url, user_id=101)
        assert len(srv.seen) == 1  # cached for the user it was fetched for
        again = await imageproxy.fetch(url, user_id=202)
        assert len(srv.seen) == 2 and again.sha == first.sha
        assert imageproxy.lookup_for(imageproxy.url_hash(url), 202) is not None
        assert imageproxy.lookup_for(imageproxy.url_hash(url), 101) is not None
        assert len(list((local_fetch / "imgcache" / "blobs").glob("*/*"))) == 1
        # Seeded images (demo, e2e) count for everyone.
        imageproxy.put("https://img.seed.example/s.png", img)
        assert imageproxy.lookup_for(imageproxy.url_hash("https://img.seed.example/s.png"), 303) is not None
    finally:
        srv.close()


async def test_settings_values_and_admin_policy(client):
    assert (await client.get("/api/v1/me")).json()["settings"]["images"] == "always"
    assert (await client.patch("/api/v1/me/settings", json={"images": "sometimes"})).status_code == 400
    assert (await client.patch("/api/v1/me/settings", json={"images": "never"})).status_code == 200


def test_cache_prune_keeps_the_budget(local_fetch):
    root = local_fetch / "imgcache" / "blobs"
    for i in range(5):
        p = root / f"{i:02d}" / f"{i:02d}{'0' * 62}"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * 1000)
        os.utime(p, (time.time() - 5 + i, time.time() - 5 + i))  # served just now, oldest first
    assert imageproxy.prune(budget=2500) == 3
    assert len(list(root.glob("*/*"))) == 2


def test_tracker_list_parses():
    rules = trackers._rules()
    assert len(rules) > 30 and all(h for h, _ in rules)
    assert Path(trackers._RULES_FILE).is_file()


async def test_admin_sets_the_proxy_and_egress(app, client):
    from mailr.core.users import create_user
    from mailr.db import sessionmaker

    # A regular user cannot change it.
    assert (await client.put("/api/v1/admin/policies", json={"image_proxy": False})).status_code == 403
    async with sessionmaker()() as db:
        adm = await create_user(db, f"a-{uuid.uuid4().hex[:8]}@example.com", "Admin",
                                password="admin-password-123", role="admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/v1/auth/login", json={"email": adm.email, "password": "admin-password-123"})
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
        got = (await c.get("/api/v1/admin/policies")).json()
        assert got["image_proxy"] is True and got["image_proxy_egress"] == ""
        r = await c.put("/api/v1/admin/policies", json={"image_proxy_egress": "http://u:pw@127.0.0.1:8888"})
        assert r.status_code == 200 and r.json()["image_proxy_egress"] == "http://u:***@127.0.0.1:8888"
        # Sending the masked value back leaves the stored one alone; the domains list is untouched.
        r = await c.put("/api/v1/admin/policies", json={"image_proxy_egress": "http://u:***@127.0.0.1:8888",
                                                         "image_proxy": True})
        assert r.status_code == 200
        async with sessionmaker()() as db:
            assert await imageproxy.instance_config(db) == (True, "http://u:pw@127.0.0.1:8888")
        assert (await c.put("/api/v1/admin/policies", json={"image_proxy_egress": "socks5://x:1"})).status_code == 400
        r = await c.put("/api/v1/admin/policies", json={"image_proxy_egress": ""})
        assert r.json()["image_proxy_egress"] == ""


async def test_the_environment_egress_wins_over_the_admin_setting(app, client, monkeypatch):
    """MAILR_IMAGE_PROXY_EGRESS (the instance .env) sets the egress proxy; the admin form then
    shows it masked and refuses changes; an unusable value fails closed (no direct fetches)."""
    from mailr.config import get_settings
    from mailr.core.users import create_user
    from mailr.db import sessionmaker

    monkeypatch.setenv("MAILR_IMAGE_PROXY_EGRESS", "http://vpn:secret@127.0.0.1:8888")
    get_settings.cache_clear()
    try:
        async with sessionmaker()() as db:
            assert (await imageproxy.instance_config(db))[1] == "http://vpn:secret@127.0.0.1:8888"
            adm = await create_user(db, f"a-{uuid.uuid4().hex[:8]}@example.com", "Admin",
                                    password="admin-password-123", role="admin")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/v1/auth/login", json={"email": adm.email, "password": "admin-password-123"})
            c.headers["X-CSRF-Token"] = r.json()["csrf"]
            got = (await c.get("/api/v1/admin/policies")).json()
            assert got["image_proxy_egress_env"] is True and got["image_proxy_egress"] == "http://vpn:***@127.0.0.1:8888"
            assert (await c.put("/api/v1/admin/policies", json={"image_proxy_egress": ""})).status_code == 409
        monkeypatch.setenv("MAILR_IMAGE_PROXY_EGRESS", "socks5://nope:1")
        get_settings.cache_clear()
        async with sessionmaker()() as db:
            assert (await imageproxy.instance_config(db))[1].endswith(".invalid:1")
    finally:
        monkeypatch.delenv("MAILR_IMAGE_PROXY_EGRESS")
        get_settings.cache_clear()
