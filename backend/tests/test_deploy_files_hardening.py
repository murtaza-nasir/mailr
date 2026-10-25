"""The deployment files (deploy/docker-compose.yml, Dockerfile,
deploy/stalwart/Dockerfile, the docs build).

Each test passes while the protection holds. A gap is xfail(strict=True)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
COMPOSE = yaml.safe_load((REPO / "deploy" / "docker-compose.yml").read_text())
SERVICES = COMPOSE["services"]


def _nets(name: str) -> set[str]:
    n = SERVICES[name].get("networks") or ["default"]
    return set(n if isinstance(n, list) else n.keys())


def test_network_membership():
    """The network table: who shares a network with whom."""
    assert {s: _nets(s) for s in SERVICES} == {
        "app": {"default", "egress", "errors"},
        "worker": {"default", "egress", "errors"},
        "worker-extra": {"default", "egress", "errors"},
        "postgres": {"default"},
        "stalwart": {"default", "dns"},
        "resolver": {"dns"},
        "egress": {"egress"},
        "browser": {"browser-net"},
        "browser-gate": {"browser-net", "egress", "browser-out"},
        "cli": {"default", "egress"},
        "dav-gateway": {"default"},
        "glitchtip": {"errors", "errors-db"},
        "glitchtip-postgres": {"errors-db"},
    }
    # The error tracker shares a network only with the app and the workers, and its database only with
    # it: it reaches neither mailr's database nor the mail store.
    assert COMPOSE["networks"]["errors-db"] == {"internal": True}
    # The browser's network is internal (no way out) and shared only with the gate:
    # nothing else, not the app, the stores or the VPN container, reaches it.
    assert COMPOSE["networks"]["browser-net"] == {"internal": True}


def test_the_browser_shares_no_network_with_the_stores():
    for store in ("postgres", "stalwart", "resolver", "dav-gateway"):
        assert not _nets("browser") & _nets(store), store
    # The egress container likewise reaches neither store.
    for store in ("postgres", "stalwart", "resolver"):
        assert not _nets("egress") & _nets(store), store
    # And the error tracker and its database (the `errors` profile).
    for store in ("postgres", "stalwart", "resolver"):
        assert not (_nets("glitchtip") | _nets("glitchtip-postgres")) & _nets(store), store


def test_the_browser_service_is_locked_down():
    b = SERVICES["browser"]
    assert b["profiles"] == ["browser"]
    assert "ports" not in b and "expose" not in b
    assert b["cap_drop"] == ["ALL"] and "cap_add" not in b
    assert "no-new-privileges:true" in b["security_opt"]
    assert b["read_only"] is True and "privileged" not in b
    assert "volumes" not in b  # no host paths, no mailr data
    assert "env_file" not in b  # only TOKEN and limits, never .env's other secrets
    assert set(b["environment"]) == {"TOKEN", "CONCURRENT", "QUEUED", "TIMEOUT"}
    assert b["environment"]["TOKEN"] == "${MAILR_BROWSER_TOKEN:-}"


@pytest.mark.parametrize("token", ["", None])
def test_the_browser_refuses_to_start_without_a_token(tmp_path, token):
    """The entrypoint itself, as compose runs it ("$$" is compose's escape for "$")."""
    import shutil
    import subprocess

    argv = [a.replace("$$", "$") for a in SERVICES["browser"]["entrypoint"]]
    assert shutil.which(argv[0]), argv[0]
    env = {"PATH": "/usr/bin:/bin"} | ({"TOKEN": token} if token is not None else {})
    r = subprocess.run(argv, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=10)
    assert r.returncode == 1 and "set MAILR_BROWSER_TOKEN" in r.stderr
    # With a token the check lets it through, to the browser's own start script (absent here: 127).
    r = subprocess.run(argv, cwd=tmp_path, env=env | {"TOKEN": "t0k"}, capture_output=True, text=True, timeout=10)
    assert r.returncode == 127 and "MAILR_BROWSER_TOKEN" not in r.stderr


def test_the_egress_proxy_is_not_published_and_asks_for_a_password():
    e = SERVICES["egress"]
    assert "ports" not in e and e["profiles"] == ["vpn"]
    assert e["environment"]["HTTPPROXY_USER"] and e["environment"]["HTTPPROXY_PASSWORD"] == "${MAILR_EGRESS_PASSWORD:-}"
    assert e["cap_add"] == ["NET_ADMIN", "CHOWN", "DAC_OVERRIDE"]


def test_the_cli_service_is_the_app_without_its_address_or_secrets():
    c, w = SERVICES["cli"], SERVICES["worker"]
    assert c["profiles"] == ["tools"] and c["restart"] == "no"
    assert "ports" not in c
    assert c["read_only"] is True and c["cap_drop"] == ["ALL"]
    for k in ("POSTGRES_PASSWORD", "STALWART_ADMIN_PASSWORD", "MAILR_SVC_PASSWORD", "MAILR_VPN_WIREGUARD_KEY",
              "MAILR_EGRESS_PASSWORD", "MAILR_OIDC_CLIENT_SECRET", "MAILR_BOOTSTRAP_ADMIN_PASSWORD"):
        assert c["environment"][k] == "" == w["environment"][k], k
    nets = c["networks"]
    assert all(not (v or {}).get("ipv4_address") for v in nets.values())  # never the app's fixed address


def test_only_the_intended_ports_are_published():
    published = {s: v.get("ports", []) for s, v in SERVICES.items() if v.get("ports")}
    assert set(published) == {"app", "stalwart", "dav-gateway", "glitchtip"}
    assert published["glitchtip"] == ["${GLITCHTIP_BIND:-127.0.0.1}:${GLITCHTIP_PORT:-8027}:8000"]
    assert published["app"] == ["${MAILR_HTTP_BIND:-127.0.0.1}:${MAILR_HTTP_PORT:-8025}:8025"]
    assert any(p.startswith("${MAILR_IMAP_BIND:-127.0.0.1}") for p in published["stalwart"])
    assert published["dav-gateway"][0].startswith("${MAILR_DAV_BIND:-127.0.0.1}")


def _listens_everywhere(name: str) -> bool:
    cmd = SERVICES[name].get("command") or []
    if name in ("app",):
        return "--host" not in cmd or cmd[cmd.index("--host") + 1] in ("0.0.0.0", "::")
    return False


def test_nothing_listening_on_all_interfaces_shares_a_network_with_the_browser():
    for s in SERVICES:
        if s != "browser" and _nets(s) & _nets("browser"):
            assert not _listens_everywhere(s), s


# ---------------------------------------------------------------- least privilege and limits

# The capabilities each service gets back after `cap_drop: [ALL]`, and why.
CAPS = {
    # The entrypoint sets the owner of the data and socket folders as root, then runs Postgres as postgres.
    "postgres": {"CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"},
    "glitchtip-postgres": {"CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"},
    # The binary carries the file capability for ports below 1024 and does not start without it.
    "stalwart": {"NET_BIND_SERVICE"},
    # Unbound starts as root and switches to its own user.
    "resolver": {"SETGID", "SETUID"},
    # The tunnel, routes and firewall; the state files handed to gluetun's own user.
    "egress": {"NET_ADMIN", "CHOWN", "DAC_OVERRIDE"},
}
# A writable root filesystem, and why.
WRITABLE_ROOT = {"egress": "gluetun adds its user to /etc/passwd at every start"}
# A healthcheck from the image rather than the compose file, or none, and why.
IMAGE_HEALTHCHECK = {"app": "the Dockerfile's HEALTHCHECK", "egress": "gluetun's own (gluetun-entrypoint healthcheck)"}
NO_HEALTHCHECK = {"cli": "one-off commands, never a running service"}
LIMIT = re.compile(r"^\$\{([A-Z][A-Z0-9_]*):-(\d+)([kmg])\}$")


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_every_service_runs_with_least_privilege(name):
    svc = SERVICES[name]
    assert svc.get("cap_drop") == ["ALL"], name
    assert set(svc.get("cap_add") or []) == CAPS.get(name, set()), name
    assert "no-new-privileges:true" in (svc.get("security_opt") or []), name
    assert not svc.get("privileged") and "pid" not in svc and svc.get("network_mode") is None, name
    assert bool(svc.get("read_only")) is (name not in WRITABLE_ROOT), name


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_every_service_has_memory_and_process_limits_from_env(name):
    svc = SERVICES[name]
    mem = LIMIT.match(str(svc.get("mem_limit") or ""))
    assert mem, (name, svc.get("mem_limit"))
    size = int(mem[2]) * {"k": 1, "m": 1024, "g": 1024 * 1024}[mem[3]]
    assert 64 * 1024 <= size <= 8 * 1024 * 1024, (name, svc["mem_limit"])  # 64 MiB to 8 GiB by default
    pids = re.match(r"^\$\{([A-Z][A-Z0-9_]*):-(\d+)\}$", str(svc.get("pids_limit") or ""))
    assert pids and 32 <= int(pids[2]) <= 8192, (name, svc.get("pids_limit"))
    # Each limit is a variable of its own, documented with the others.
    docs = (REPO / "site" / "docs" / "reference" / "environment.md").read_text()
    assert f"`{mem[1]}`" in docs and f"`{pids[1]}`" in docs, (mem[1], pids[1])


def test_limit_variables_are_per_service():
    owners: dict[str, set[str]] = {}
    for name, svc in SERVICES.items():
        for key in ("mem_limit", "pids_limit"):
            var = re.match(r"^\$\{([A-Z0-9_]+)", str(svc[key]))[1]
            owners.setdefault(var, set()).add(name)
    # Only the app's clones share a variable: the cli has the app's limits, the extra workers the worker's.
    shared = {v: s for v, s in owners.items() if len(s) > 1}
    assert shared == {"MAILR_APP_MEMORY": {"app", "cli"}, "MAILR_APP_PIDS": {"app", "cli"},
                      "MAILR_WORKER_MEMORY": {"worker", "worker-extra"},
                      "MAILR_WORKER_PIDS": {"worker", "worker-extra"}}, shared


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_every_service_has_a_healthcheck(name):
    hc = SERVICES[name].get("healthcheck")
    if name in NO_HEALTHCHECK:
        assert hc == {"disable": True}
    elif name in IMAGE_HEALTHCHECK:
        assert hc is None or hc.get("test"), name
    else:
        assert hc and hc.get("test") and not hc.get("disable"), name
        assert hc["test"][0] in ("CMD", "CMD-SHELL"), name


def test_writable_paths_are_small_and_listed():
    """A read-only service writes only to its volumes and to tmpfs mounts of a bounded size."""
    for name, svc in SERVICES.items():
        for t in svc.get("tmpfs") or []:
            path, _, opts = t.partition(":")
            assert path.startswith("/") and re.search(r"(^|,)size=\d+[kmg]", opts), (name, t)


# ---------------------------------------------------------------- pinned images and downloads


def test_every_compose_image_is_pinned_by_digest():
    for name, svc in SERVICES.items():
        img = svc.get("image") or ""
        if svc.get("build") or img.startswith("${MAILR_IMAGE"):
            continue  # built here from pinned bases (below), or the app's image
        assert re.search(r"@sha256:[0-9a-f]{64}$", img), (name, img)


def test_every_dockerfile_base_and_download_is_pinned():
    for f in (REPO / "Dockerfile", REPO / "deploy" / "stalwart" / "Dockerfile"):
        text = f.read_text()
        for line in re.findall(r"(?m)^FROM\s+(?:--platform=\S+\s+)?(\S+)", text):
            assert re.search(r"@sha256:[0-9a-f]{64}$", line), (f.name, line)
        for m in re.finditer(r"(?m)^ADD\s+(.*)$", text):
            assert "--checksum=sha256:" in m.group(1), (f.name, m.group(0))
        for m in re.finditer(r"--from=(\S+)", text):
            ref = m.group(1)
            assert ref in ("web", "docs") or re.search(r"@sha256:[0-9a-f]{64}$", ref), (f.name, ref)


def test_app_dependencies_are_locked_with_hashes():
    import json
    import tomllib

    lock = tomllib.loads((REPO / "backend" / "uv.lock").read_text())
    registry = [p for p in lock["package"] if "registry" in p.get("source", {})]
    assert registry
    for p in registry:
        files = [*p.get("wheels", []), *([p["sdist"]] if "sdist" in p else [])]
        assert files and all(f.get("hash", "").startswith("sha256:") for f in files), p["name"]
    npm = json.loads((REPO / "frontend" / "package-lock.json").read_text())["packages"]
    fetched = {k: v for k, v in npm.items() if v.get("resolved")}
    assert fetched
    assert all(v.get("integrity", "").startswith("sha512-") for v in fetched.values()), [
        k for k, v in fetched.items() if not v.get("integrity", "").startswith("sha512-")]
    # The image installs from those locks. A Dockerfile instruction runs only in a docker build,
    # which the tests do not do, so its text is what can be checked here.
    docker = (REPO / "Dockerfile").read_text()
    assert "uv sync --frozen" in docker and "npm ci" in docker


def test_the_docs_build_is_locked_with_hashes():
    lock = (REPO / "site" / "requirements-docs.lock").read_text()
    pins = [ln for ln in lock.splitlines() if "==" in ln]
    assert pins and all("--hash=sha256:" in ln for ln in pins)
    assert "--require-hashes" in (REPO / "Dockerfile").read_text()


def test_only_the_app_and_cli_build_the_app_image():
    # Two services in one `up` that each build the shared tag are built in parallel, and the
    # second export fails with 'image "mailr:local": already exists' (Docker's containerd store).
    built = {n for n, s in SERVICES.items() if s.get("build") and (s.get("image") or "").startswith("${MAILR_IMAGE")}
    assert built == {"app", "cli"}, built
    assert SERVICES["cli"].get("profiles"), "cli must stay out of a plain `up`"
