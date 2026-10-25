"""The interactive API docs and the OpenAPI document (api/apidocs_routes.py): only for a signed-in
person who may make API tokens; a 404 for everyone else, signed out included."""

from contextlib import asynccontextmanager

import pytest
from conftest import make_db_user
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.db_user

PATHS = ("/api/v1/docs", "/api/v1/openapi.json", "/api/v1/docs/start.js")


@asynccontextmanager
async def _signed_in(app, u):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/v1/auth/login", json={"email": u.email, "password": "test-password-123"})
        assert r.status_code == 200, r.text
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
        yield c


async def test_signed_out_gets_404(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        for path in PATHS:
            assert (await c.get(path)).status_code == 404, path


async def test_member_without_token_rights_gets_404(app):
    # No group with a policy: the built-in Everyone policy, where Agents & API tokens are off.
    u = await make_db_user(model_access=False)
    async with _signed_in(app, u) as c:
        assert (await c.post("/api/v1/tokens", json={"name": "x", "scopes": ["read"]})).status_code == 403
        for path in PATHS:
            assert (await c.get(path)).status_code == 404, path


async def test_admin_gets_the_docs_and_the_schema(app):
    u = await make_db_user(role="admin", model_access=False)
    async with _signed_in(app, u) as c:
        page = await c.get("/api/v1/docs")
        assert page.status_code == 200
        csp = page.headers["content-security-policy"]
        # Swagger UI comes from this server: no CDN, no inline script.
        assert "script-src 'self';" in csp and "cdn" not in page.text and "<script>" not in page.text
        schema = await c.get("/api/v1/openapi.json")
        assert schema.status_code == 200 and "/api/v1/tokens" in schema.json()["paths"]
        assert "/api/v1/docs" not in schema.json()["paths"]


async def test_member_with_token_rights_gets_them_but_not_with_a_token(app):
    # The test group's policy turns every switch on, Agents & API tokens included.
    async with _signed_in(app, await make_db_user()) as c:
        assert (await c.get("/api/v1/docs")).status_code == 200
        assert (await c.get("/api/v1/openapi.json")).status_code == 200
        tok = (await c.post("/api/v1/tokens", json={"name": "r", "scopes": ["read"]})).json()["token"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        h = {"Authorization": f"Bearer {tok}"}
        assert (await c.get("/api/v1/openapi.json", headers=h)).status_code == 404


# What a reader of the public reference must not see: source paths, catalogue citations and
# internal constant names (environment variables are documented, so MAILR_* may stay).
_INTERNAL = (
    r"\b[\w-]+(?:/[\w.-]+)*\.(?:py|ts|tsx|kt)\b",
    r"\bACT [a-z]+\.[a-z_]+",
    r"module docstring",
    r"\b(?!MAILR_)[A-Z]+_[A-Z][A-Z_]*\b",
)


def _descriptions(doc: dict):
    for path, methods in doc["paths"].items():
        for method, op in methods.items():
            yield f"{method.upper()} {path}", op.get("description") or ""
            for p in op.get("parameters", []):
                yield f"{method.upper()} {path} ?{p['name']}", p.get("description") or ""
    for name, schema in doc.get("components", {}).get("schemas", {}).items():
        yield name, schema.get("description") or ""
        for prop, v in (schema.get("properties") or {}).items():
            yield f"{name}.{prop}", (v.get("description") or "") if isinstance(v, dict) else ""


def test_openapi_has_the_server_version_and_no_internal_notes(app):
    import re

    from mailr.config import server_version

    doc = app.openapi()
    assert doc["info"]["version"] == server_version() != "0.1.0"
    leaks = [(where, m.group(0)) for where, text in _descriptions(doc)
             for pat in _INTERNAL for m in [re.search(pat, text)] if m]
    assert not leaks, leaks
