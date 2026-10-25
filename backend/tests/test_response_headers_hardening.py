"""Response headers, CORS, the service worker's scope and the
/help/ pages (core/helpdocs.py).

Each test passes while the protection holds. A gap is xfail(strict=True)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


def _csp(value: str) -> dict[str, str]:
    out = {}
    for part in value.split(";"):
        part = part.strip()
        if part:
            name, _, rest = part.partition(" ")
            out[name] = rest
    return out


# ---------------------------------------------------------------- app-wide headers


async def test_api_responses_carry_the_base_headers(client):
    r = await client.get("/api/v1/auth/session")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-store"


async def test_pages_get_the_shell_policy(anon):
    from mailr import main

    main._IMG_DIRECT = (0.0, False)  # the image proxy on: no remote images in img-src
    r = await anon.get("/some/page")
    csp = _csp(r.headers["content-security-policy"])
    assert csp["default-src"] == "'self'" and "script-src" not in csp  # scripts: same origin only
    assert csp["object-src"] == "'none'" and csp["base-uri"] == "'none'"
    assert csp["frame-ancestors"] == "'none'" and csp["form-action"] == "'self'"
    assert csp["connect-src"] == "'self'"
    assert "unsafe-eval" not in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY" and r.headers["referrer-policy"] == "no-referrer"
    assert "https:" not in csp["img-src"]


@pytest.mark.parametrize("method,path", [("OPTIONS", "/api/v1/auth/session"), ("GET", "/api/v1/auth/session"),
                                         ("OPTIONS", "/api/v1/conversations/actions")])
async def test_no_cross_origin_access(anon, method, path):
    r = await anon.request(method, path, headers={"Origin": "https://evil.example.org",
                                                  "Access-Control-Request-Method": "POST",
                                                  "Access-Control-Request-Headers": "content-type,x-csrf-token"})
    assert not any(k.lower().startswith("access-control-allow") for k in r.headers)


async def _raw(app, path: str, headers: dict[str, str], body: bytes = b"") -> tuple[int, dict[str, str]]:
    out: dict = {}

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
            out["headers"] = {k.decode().lower(): v.decode() for k, v in msg["headers"]}

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http",
             "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
             "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
             "client": ("127.0.0.1", 1), "server": ("test", 80)}
    await app(scope, receive, send)
    return out["status"], out["headers"]


async def test_early_refusals_carry_the_base_headers(app, monkeypatch):
    """413 (body too large) and 426 (Android app too old) answer before the routes do."""
    from mailr.config import get_settings

    status, h = await _raw(app, "/api/v1/messages/1/move", {"content-length": str(10**10),
                                                            "content-type": "application/json"})
    assert status == 413
    assert h.get("x-content-type-options") == "nosniff" and h.get("cache-control") == "no-store"

    monkeypatch.setattr(get_settings(), "android_min_version", "9.0.0")
    status, h = await _raw(app, "/api/v1/me", {"user-agent": "mailr-android/1.0.0"})
    assert status == 426
    assert h.get("x-content-type-options") == "nosniff" and h.get("cache-control") == "no-store"


# ---------------------------------------------------------------- the service worker


async def test_no_answer_widens_the_service_worker_scope(anon):
    """The worker's scope stays its default, "/" (sw.js at the root): no answer carries
    Service-Worker-Allowed. What the worker leaves alone (/help/, other origins, non-GET) is tested
    by running sw.js itself: frontend/src/lib/swScope.test.ts."""
    for path in ("/sw.js", "/", "/some/page", "/help/", "/api/v1/auth/session"):
        r = await anon.get(path)
        assert "service-worker-allowed" not in {k.lower() for k in r.headers}, path


# ---------------------------------------------------------------- /help/


async def test_help_needs_a_browser_session(anon, help_site):
    for headers in ({}, {"Authorization": "Bearer mailr_pat_not_a_session"}, {"Cookie": "mailr_session=forged"}):
        for method in ("GET", "HEAD"):
            r = await anon.request(method, "/help/user-guide/inbox/", headers=headers)
            assert r.status_code == 302 and r.headers["location"].startswith("/login?next=%2Fhelp%2F")
            assert "The inbox" not in r.text and r.headers["cache-control"] == "no-store"


async def test_help_sign_in_redirect_stays_on_this_site(anon, help_site):
    for path in ("/help//evil.example.org", "/help/%2F%2Fevil.example.org", "/help/\\evil.example.org"):
        r = await anon.get(path)
        assert r.status_code == 302
        assert r.headers["location"].startswith("/login?next=%2Fhelp%2F")


async def test_help_is_not_served_to_a_signed_out_session(app, user, help_site):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/v1/auth/login", json={"email": user.email, "password": "test-password-123"})
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
        assert (await c.get("/help/")).status_code == 200
        cookie = c.cookies.get("mailr_session")
        await c.post("/api/v1/auth/logout")
        c.cookies.set("mailr_session", cookie)
        assert (await c.get("/help/")).status_code == 302


async def test_help_path_with_a_nul_byte_is_not_found(client, help_site):
    r = await client.get("/help/user-guide/%00")
    assert r.status_code == 404
