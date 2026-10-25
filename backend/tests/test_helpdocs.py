"""The user guide at /help/ (core/helpdocs.py): signed-in people only, its own policy with the
hashes of the docs theme's inline scripts, private caching, no indexing, and the help build
(site/mkdocs.help.yml, site/build_help.py, the Dockerfile's docs stage) kept in step."""

from __future__ import annotations

import base64
import hashlib
import os
import re
from pathlib import Path

import pytest
import yaml
from helpers.helpdocs import INLINE

REPO = Path(__file__).resolve().parents[2]


def _sha(text: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


async def test_signed_out_goes_to_sign_in_and_back(anon, help_site):
    r = await anon.get("/help/user-guide/inbox/?q=1")
    assert r.status_code == 302
    assert r.headers["location"] == "/login?next=%2Fhelp%2Fuser-guide%2Finbox%2F%3Fq%3D1"
    assert r.headers["x-robots-tag"].startswith("noindex")
    assert r.headers["cache-control"] == "no-store"
    assert "The inbox" not in r.text
    for path in ("/help/", "/help/search/search_index.json", "/help/404.html", "/help/assets/javascripts/bundle.d7400e89.min.js"):
        r = await anon.get(path)
        assert r.status_code == 302, path
        assert r.headers["location"].startswith("/login?next=")


async def test_bad_session_cookie_is_signed_out(anon, help_site):
    anon.cookies.set("mailr_session", "not-a-session")
    r = await anon.get("/help/")
    assert r.status_code == 302


async def test_help_without_slash(anon):
    r = await anon.get("/help")
    assert r.status_code == 308
    assert r.headers["location"] == "/help/"


async def test_signed_in_gets_the_pages(client, help_site):
    r = await client.get("/help/")
    assert r.status_code == 200
    assert "<h1>Home</h1>" in r.text
    assert r.headers["cache-control"] == "private, no-cache"
    assert r.headers["x-robots-tag"].startswith("noindex")
    assert r.headers["x-frame-options"] == "DENY"
    csp = r.headers["content-security-policy"]
    script_src = next(d for d in csp.split("; ") if d.startswith("script-src "))
    assert script_src == f"script-src 'self' {_sha(INLINE)}"  # JSON data blocks are not hashed
    assert "'unsafe-inline'" not in script_src and "'unsafe-eval'" not in script_src
    assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp and "base-uri 'none'" in csp
    assert "connect-src 'self'" in csp and "frame-src 'none'" in csp
    assert "googleapis" not in csp and "https:" not in csp

    r = await client.get("/help/user-guide/inbox/")
    assert r.status_code == 200 and "The inbox" in r.text

    r = await client.get("/help/assets/javascripts/bundle.d7400e89.min.js")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "private, max-age=31536000, immutable"

    r = await client.get("/help/search/search_index.json")
    assert r.status_code == 200 and r.headers["cache-control"] == "private, no-cache"


async def test_folder_without_slash_and_missing_pages(client, help_site):
    r = await client.get("/help/user-guide/inbox")
    assert r.status_code == 308 and r.headers["location"] == "/help/user-guide/inbox/"
    r = await client.get("/help/nothing-here/")
    assert r.status_code == 404 and "Page not found" in r.text
    assert r.headers["x-robots-tag"].startswith("noindex")


@pytest.mark.parametrize("path", [
    "/help/.secret", "/help/..%2Foutside.txt", "/help/%2E%2E/outside.txt", "/help/user-guide/../../outside.txt",
    "/help/.git/config", "/help/user-guide/.hidden/index.html", "/help/%252e%252e/outside.txt",
    "/help/..%5coutside.txt", "/help/%2e%2e%2foutside.txt", "/help/link-out", "/help/link-out-dir/outside.txt",
    "/help//etc/passwd", "/help/%2Fetc%2Fpasswd",
])
async def test_nothing_outside_the_help_folder(client, help_site, path):
    """Hidden files and folders, encoded and doubled dots, symlinks out of the folder, absolute paths."""
    (help_site / ".git").mkdir()
    (help_site / ".git" / "config").write_text("no")
    (help_site / "user-guide" / ".hidden").mkdir()
    (help_site / "user-guide" / ".hidden" / "index.html").write_text("no")
    os.symlink(help_site.parent / "outside.txt", help_site / "link-out")
    os.symlink(help_site.parent, help_site / "link-out-dir")
    r = await client.get(path)
    assert r.status_code in (404, 308)
    assert "outside" not in r.text and r.text != "no" and "root:" not in r.text


@pytest.mark.parametrize("path", ["x" * 5000, "user-guide/" + "y" * 300, "a/" * 600, "user-guide/" + "é" * 200])
async def test_a_long_help_path_is_not_found(client, help_site, path):
    """A segment over the file system's name limit gave a 500 with a traceback."""
    r = await client.get("/help/" + path)
    assert r.status_code in (404, 414), r.status_code
    assert "Traceback" not in r.text


async def test_no_help_pages_built(client, tmp_path, monkeypatch):
    from mailr.core import helpdocs

    monkeypatch.setattr(helpdocs, "DIR", tmp_path / "none")
    r = await client.get("/help/")
    assert r.status_code == 404
    assert "tools/build-help" in r.text


async def test_the_app_policy_is_unchanged(client, help_site):
    """The help pages' script hashes never reach the web app's own policy."""
    r = await client.get("/__mailr-page-headers")
    csp = r.headers["content-security-policy"]
    assert "sha256-" not in csp
    assert "default-src 'self'" in csp


def test_help_routes_come_before_the_app(app):
    paths = [getattr(r, "path", "") for r in app.routes]
    assert "/help/{path:path}" in paths
    if "/{path:path}" in paths:
        assert paths.index("/help/{path:path}") < paths.index("/{path:path}")


class _AnyTag(yaml.SafeLoader):
    pass


_AnyTag.add_multi_constructor("", lambda loader, suffix, node: None)


def _yaml(name: str) -> dict:
    return yaml.load((REPO / "site" / name).read_text(), Loader=_AnyTag)  # SafeLoader subclass


def test_help_build_matches_the_public_site():
    main, help_ = _yaml("mkdocs.yml"), _yaml("mkdocs.help.yml")
    assert help_["INHERIT"] == "mkdocs.yml"
    assert help_["site_url"] == "" and help_["repo_url"] == ""
    assert help_["theme"]["font"] is False
    want = [f for f in main["theme"]["features"] if not f.startswith("navigation.instant")]
    assert help_["theme"]["features"] == want
    assert main["docs_dir"] == "docs"  # site/docs only: the internal docs/ never go in


def test_mermaid_pins_agree():
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_help", REPO / "site" / "build_help.py")
    build_help = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build_help)
    version, sha = build_help.MERMAID_VERSION, build_help.MERMAID_SHA256
    assert re.fullmatch(r"[0-9a-f]{64}", sha)
    # The docs stage of the image downloads the same file. A Dockerfile instruction runs only in a
    # docker build, which the tests do not do, so its text is what can be checked here.
    docker = (REPO / "Dockerfile").read_text()
    assert f"--checksum=sha256:{sha}" in docker
    assert f"mermaid@{version}/dist/mermaid.min.js" in docker
    assert "COPY --from=docs /help /app/mailr/help" in docker
    assert "assets/vendor/mermaid.min.js" in _yaml("mkdocs.help.yml")["extra_javascript"]


def test_docs_lock_pins_every_requirement():
    lock = {line.split("==")[0].lower() for line in (REPO / "site" / "requirements-docs.lock").read_text().splitlines()
            if "==" in line}
    for line in (REPO / "site" / "requirements-docs.txt").read_text().splitlines():
        name = re.split(r"[<>=\s#]", line.strip(), maxsplit=1)[0].lower()
        if name:
            assert name in lock, name
