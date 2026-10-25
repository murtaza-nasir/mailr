# ACT-SURFACES: api
"""The page readers' network edge.

- The browser gate takes a CONNECT target only of plain host characters (ASCII letters,
  digits, dots, hyphens; an IPv6 literal's characters in brackets) and an ASCII port.
- A host that goes into Chrome's --host-resolver-rules is held to the same characters.
- check-config warns when the gate is set for a browser outside the stack.
"""

from __future__ import annotations

import pytest

from mailr.core import netguard
from mailr.websearch import gate


@pytest.mark.parametrize("target", [
    "www.example.org\t.evil:443", "www.exa mple.org:443", "a,b.example:443", "*.example.org:443",
    "www.example.org:44³", "www.example.org:٤٤٣", "www.example.org:000443",
    "exämple.org:443", "www.example.org%0a:443", "[::1%eth0]:443", "[2606:2800::1;x]:443",
    "a" * 300 + ".example:443",
])
def test_connect_targets_with_odd_characters_are_refused(target):
    with pytest.raises(gate.Refused) as e:
        gate.parse_request(f"CONNECT {target} HTTP/1.1\r\n\r\n".encode())
    assert e.value.status in (400, 403)


def test_plain_targets_still_parse():
    assert gate.parse_request(b"CONNECT xn--bcher-kva.example:443 HTTP/1.1\r\n\r\n") == ("xn--bcher-kva.example", 443)
    assert gate.parse_request(b"CONNECT [2606:2800::1]:443 HTTP/1.1\r\n\r\n") == ("2606:2800::1", 443)


@pytest.mark.parametrize(("host", "ok"), [
    ("www.example.org", True), ("xn--bcher-kva.example", True), ("2606:2800::1", True), ("[2606:2800::1]", True),
    ("a,b.example", False), ("*.example", False), ("a b", False), ("exämple.org", False), ("", False),
    ("example.org EXCLUDE x", False),
])
def test_plain_host(host, ok):
    assert netguard.plain_host(host) is ok


# ---------------------------------------------------------------- check-config and the gate


def test_check_config_warns_about_the_gate_with_an_outside_browser(monkeypatch, tmp_path):
    import base64
    import os

    from mailr.core import configcheck

    for k in list(os.environ):
        if k.startswith("MAILR_"):
            monkeypatch.delenv(k)
    for k, v in {"MAILR_SECRET_KEY": base64.b64encode(os.urandom(32)).decode(), "MAILR_DEV": "false",
                 "MAILR_BASE_URL": "https://mail.example.com", "MAILR_DATA_DIR": str(tmp_path),
                 "MAILR_STALWART_ADMIN_PASSWORD": "x", "MAILR_DATABASE_URL": "postgresql+asyncpg://u:p@db/x",
                 "MAILR_IMAP_HOSTNAME": "mail.example.com", "MAILR_BROWSER_GATE": "browser-gate"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAILR_BROWSER_URL", "http://browser:3000")
    assert not [p for p in configcheck.problems() if p.var == "MAILR_BROWSER_GATE"]
    monkeypatch.setenv("MAILR_BROWSER_URL", "http://10.0.0.9:3000")
    assert [p.level for p in configcheck.problems() if p.var == "MAILR_BROWSER_GATE"] == ["warning"]
    monkeypatch.setenv("MAILR_BROWSER_GATE", "")
    assert not [p for p in configcheck.problems() if p.var == "MAILR_BROWSER_GATE"]
