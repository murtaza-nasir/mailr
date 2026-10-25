"""`mailr check-config`, the start-up check (core/configcheck.py) and the trusted-proxy
default (core/proxies.py)."""

import base64
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mailr.config import get_settings
from mailr.core import configcheck, proxies

GOOD_KEY = base64.b64encode(os.urandom(32)).decode()
SECRET_VALUES = ("hunter2-not-printed", "s3cret-client", "postgresql+asyncpg://u:pw-not-printed@db/x")


@pytest.fixture
def env(monkeypatch, tmp_path):
    """A clean, valid production-like environment; tests change one thing each."""
    for k in list(os.environ):
        if k.startswith("MAILR_"):
            monkeypatch.delenv(k)
    base = {
        "MAILR_SECRET_KEY": GOOD_KEY, "MAILR_BASE_URL": "https://mail.example.com", "MAILR_DEV": "false",
        "MAILR_STALWART_ADMIN_PASSWORD": SECRET_VALUES[0], "MAILR_DATA_DIR": str(tmp_path),
        "MAILR_DATABASE_URL": SECRET_VALUES[2], "MAILR_IMAP_HOSTNAME": "mail.example.com",
    }
    for k, v in base.items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir(tmp_path)  # no .env file here
    return monkeypatch


def _vars(found):
    return {(p.level, p.var) for p in found}


def test_clean_config_has_no_problems(env):
    assert configcheck.problems() == []
    assert configcheck.report([]).startswith("ok")


@pytest.mark.parametrize("key,value,expect", [
    ("MAILR_SECRET_KEY", "", ("error", "MAILR_SECRET_KEY")),
    ("MAILR_SECRET_KEY", "not base64!!", ("error", "MAILR_SECRET_KEY")),
    ("MAILR_DB_POOL_SIZE", "ten", ("error", "MAILR_DB_POOL_SIZE")),
    ("MAILR_BASE_URL", "mail.example.com", ("error", "MAILR_BASE_URL")),
    ("MAILR_BASE_URL", "http://localhost:8025", ("warning", "MAILR_BASE_URL")),
    ("MAILR_OIDC_ISSUER", "https://id.example.com", ("error", "MAILR_OIDC_CLIENT_ID")),
    ("MAILR_OIDC_CLIENT_ID", SECRET_VALUES[1], ("warning", "MAILR_OIDC_ISSUER")),
    ("MAILR_LOCAL_LOGIN", "false", ("error", "MAILR_LOCAL_LOGIN")),
    ("MAILR_SEED_MODEL_ENDPOINTS", "[{\"name\": ", ("error", "MAILR_SEED_MODEL_ENDPOINTS")),
    ("MAILR_SEED_MODEL_ENDPOINTS", '[{"name": "a"}]', ("error", "MAILR_SEED_MODEL_ENDPOINTS")),
    ("MAILR_TRUSTED_PROXIES", "10.0.0.1,proxy.lan", ("error", "MAILR_TRUSTED_PROXIES")),
    ("MAILR_ALLOW_PRIVATE_HOSTS", "sometimes", ("error", "MAILR_ALLOW_PRIVATE_HOSTS")),
    ("MAILR_NTFY_URL", "ntfy.example.com", ("warning", "MAILR_NTFY_URL")),
    # The default store name is not a trusted authserv-id.
    ("MAILR_IMAP_HOSTNAME", "localhost", ("warning", "MAILR_IMAP_HOSTNAME")),
])
def test_each_problem_names_its_variable(env, key, value, expect):
    env.setenv(key, value)
    found = configcheck.problems()
    assert expect in _vars(found), found
    assert configcheck.fatal(found) == (expect[0] == "error")


def test_missing_store_password_is_fatal_outside_dev(env):
    env.delenv("MAILR_STALWART_ADMIN_PASSWORD")
    assert ("error", "MAILR_STALWART_ADMIN_PASSWORD") in _vars(configcheck.problems())
    env.setenv("MAILR_DEV", "true")
    assert ("error", "MAILR_STALWART_ADMIN_PASSWORD") not in _vars(configcheck.problems())


def test_backup_target_without_marker_warns(env, tmp_path):
    target = tmp_path / "nas"
    target.mkdir()
    env.setenv("MAILR_BACKUP_TARGET", str(target))
    found = configcheck.problems()
    assert ("warning", "MAILR_BACKUP_TARGET") in _vars(found)
    assert "not mounted" in next(p.message for p in found if p.var == "MAILR_BACKUP_TARGET")


def test_errors_come_first_and_no_value_is_printed(env):
    env.setenv("MAILR_OIDC_CLIENT_ID", SECRET_VALUES[1])
    env.setenv("MAILR_BASE_URL", "http://localhost:8025")
    env.setenv("MAILR_SECRET_KEY", "x" + SECRET_VALUES[0])
    found = configcheck.problems()
    assert found[0].level == "error"
    text = configcheck.report(found)
    for secret in SECRET_VALUES:
        assert secret not in text


def test_cli_check_config_offline(env):
    env.setenv("MAILR_OIDC_ISSUER", "https://id.example.com")
    r = subprocess.run([sys.executable, "-m", "mailr.cli", "check-config", "--offline"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 1
    assert "MAILR_OIDC_CLIENT_ID" in r.stdout
    for secret in SECRET_VALUES:
        assert secret not in r.stdout + r.stderr


def test_serve_refuses_to_start_on_errors(env):
    env.setenv("MAILR_SECRET_KEY", "")
    r = subprocess.run([sys.executable, "-m", "mailr.cli", "worker"], capture_output=True, text=True, timeout=60)
    assert r.returncode != 0
    assert "MAILR_SECRET_KEY" in r.stderr
    assert "Traceback" not in r.stderr


# ---------------------------------------------------------------- trusted proxies


def test_derived_default_trusts_the_stack_gateway(monkeypatch):
    monkeypatch.setattr(get_settings(), "trusted_proxies", "")
    assert proxies.derived(subnet="10.253.54.0/24", in_container=False) == ["127.0.0.1", "::1", "10.253.54.1"]
    assert proxies.derived(subnet="", in_container=False) == ["127.0.0.1", "::1"]


def test_derived_default_reads_the_route_table_in_a_container(tmp_path):
    route = tmp_path / "route"
    route.write_text("Iface\tDestination\tGateway\tFlags\n"
                     "eth0\t00000000\t0136FD0A\t0003\n"  # 10.253.54.1, little-endian
                     "eth0\t0036FD0A\t00000000\t0001\n")
    assert proxies.derived(subnet="", in_container=True, route_file=str(route)) == ["127.0.0.1", "::1", "10.253.54.1"]


def test_set_value_wins(monkeypatch):
    monkeypatch.setattr(get_settings(), "trusted_proxies", "192.168.1.20")
    assert proxies.trusted() == ["192.168.1.20"]
    assert proxies.source() == "set"
    monkeypatch.setattr(get_settings(), "trusted_proxies", "")
    monkeypatch.setattr(get_settings(), "net_subnet", "10.9.0.0/24")
    assert proxies.forwarded_allow_ips() == "127.0.0.1,::1,10.9.0.1"
    assert proxies.source() == "derived"


async def test_untrusted_forwarded_header_is_noted_once(caplog):
    proxies.reset()
    seen = []

    async def app(scope, receive, send):
        seen.append(scope["client"])

    mw = proxies.ForwardedWatch(app)
    # From a trusted proxy uvicorn has already put the forwarded client in place.
    await mw({"type": "http", "client": ("203.0.113.9", 1), "headers": [(b"x-forwarded-for", b"203.0.113.9")]}, None, None)
    assert proxies.untrusted_seen() is None
    # From an untrusted peer the header is left alone, and the client is the peer.
    with caplog.at_level("WARNING", logger="mailr.proxies"):
        await mw({"type": "http", "client": ("10.253.54.1", 1), "headers": [(b"x-forwarded-for", b"203.0.113.9")]}, None, None)
        await mw({"type": "http", "client": ("10.253.54.1", 1), "headers": [(b"x-forwarded-for", b"203.0.113.9")]}, None, None)
    assert proxies.untrusted_seen() is not None
    assert sum("X-Forwarded-For" in r.message for r in caplog.records) == 1
    proxies.reset()


def test_compose_passes_the_subnet_to_the_app():
    compose = (Path(__file__).resolve().parents[2] / "deploy" / "docker-compose.yml").read_text()
    assert "MAILR_NET_SUBNET: ${MAILR_NET_SUBNET:-10.253.54.0/24}" in compose
