"""One way out (core/outbound.py): no module opens its own HTTP client, except those listed here.

The test reads every module under mailr/ and fails on a call of httpx.AsyncClient, httpx.Client,
httpx.get/post/put/patch/delete/head/options/request/stream, an import of those names from httpx,
any import of aiohttp, requests or urllib.request, and urllib's urlopen. A model or service call
uses outbound.client(); a fetch of a URL from mail content uses core/egress.client() or
netguard.guarded_client(), which are allowed here. A new entry needs a reason that says why the
module's traffic cannot carry user data to a model or service.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "mailr"

ALLOWED: dict[str, str] = {
    "core/outbound.py": "the one way out itself",
    "core/netguard.py": "the SSRF guard's pinned clients, used for own-account protocols and mail-content fetches",
    "core/egress.py": "fetches of URLs from mail content through the egress proxy, and its own health check",
    "core/jmap.py": "the mail store (Stalwart JMAP), the user's own mailbox",
    "calendar/dav.py": "CalDAV and CardDAV of the user's own accounts",
    "api/auth_routes.py": "OIDC sign-in: discovery, token and userinfo requests to the identity provider",
    "api/native_auth.py": "OIDC sign-in for the Android app",
    "core/account_picture.py": "the OIDC userinfo picture of the signed-in user",
    "core/health.py": "health checks of the mail store and the push server (no user data)",
    "core/configcheck.py": "`mailr check-config`: the mail store's health and sign-in (no user data)",
    "core/users.py": "waiting for the mail store at start-up (no user data)",
    "core/langid.py": "downloads the language-detection model file in a development checkout (no user data)",
    "api/apidocs_routes.py": "downloads the API docs' static files in development (no user data)",
    "mcp/server.py": "the MCP bridge is a client of Mailr's own API, not an outbound service",
}
ALLOWED_DIRS: dict[str, str] = {
    "dev/": "development and test tooling (demo seeds, e2e helpers, the dev Radicale)",
}

VERBS = {"AsyncClient", "Client", "get", "post", "put", "patch", "delete", "head", "options", "request", "stream"}
BANNED_MODULES = ("aiohttp", "requests", "urllib.request")


def _offences(tree: ast.AST) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            v = n.func.value
            if isinstance(v, ast.Name) and v.id == "httpx" and n.func.attr in VERBS:
                out.append((n.lineno, f"httpx.{n.func.attr}()"))
            if n.func.attr == "urlopen":
                out.append((n.lineno, "urlopen()"))
        if isinstance(n, ast.ImportFrom) and n.module == "httpx":
            for a in n.names:
                if a.name in VERBS:
                    out.append((n.lineno, f"from httpx import {a.name}"))
        mods = [a.name for a in n.names] if isinstance(n, ast.Import) else \
            [n.module or ""] if isinstance(n, ast.ImportFrom) else []
        for m in mods:
            if any(m == b or m.startswith(b + ".") for b in BANNED_MODULES):
                out.append((n.lineno, f"import {m}"))
    return out


def test_no_module_opens_its_own_http_client():
    found = []
    for f in sorted(ROOT.rglob("*.py")):
        rel = f.relative_to(ROOT).as_posix()
        if rel in ALLOWED or any(rel.startswith(d) for d in ALLOWED_DIRS):
            continue
        for line, what in _offences(ast.parse(f.read_text(), str(f))):
            found.append(f"mailr/{rel}:{line}: {what}")
    assert not found, ("Open model and service connections with core/outbound.client() (or, for a fetch of a URL "
                       "from mail content, core/egress.client()); a new exception needs an entry in ALLOWED with "
                       "its reason:\n" + "\n".join(found))


def test_every_allowed_module_exists_and_has_a_reason():
    for rel, why in {**ALLOWED, **ALLOWED_DIRS}.items():
        assert why.strip(), rel
        assert (ROOT / rel).exists(), f"{rel} is allowed but does not exist: remove its entry"


def test_the_lint_sees_each_kind_of_client():
    src = ("import httpx\nimport aiohttp\nimport urllib.request\nfrom httpx import AsyncClient\n"
           "httpx.AsyncClient()\nhttpx.post('x')\nurllib.request.urlopen('x')\n")
    kinds = {w for _, w in _offences(ast.parse(src))}
    assert kinds >= {"import aiohttp", "import urllib.request", "from httpx import AsyncClient", "httpx.AsyncClient()",
                     "httpx.post()", "urlopen()"}
