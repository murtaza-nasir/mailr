"""The maintenance commands (mailr read-page,
reparse-attached-emails, calendar-fix-zones) and the `cli` compose service.

Each test passes while the protection holds. A gap is xfail(strict=True)."""

from __future__ import annotations

import json
import sys

import pytest

from mailr import cli
from mailr.core import reparse
from mailr.websearch import pages, readers, service


async def test_read_page_prints_the_facts_never_the_text(monkeypatch, capsys):
    async def fake(db, url, **kw):
        return {"reader": "Built-in", "route": "egress", "status": 200, "chars": 1234,
                "title": "Title\x1b[2J\x1b]52;c;cGF5bG9hZA==\x07 end", "text": "PAGE-BODY-TEXT secret words"}

    monkeypatch.setattr(readers, "read", fake)
    assert await cli._read_page("https://www.example.org/", None) == 0
    out = capsys.readouterr().out
    assert "PAGE-BODY-TEXT" not in out and "secret words" not in out
    assert "\x1b" not in out and "\x07" not in out
    assert "title:  Title[2J]52;c;cGF5bG9hZA== end" in out and "chars:  1234" in out


async def test_read_page_errors_are_printed_without_control_characters(monkeypatch, capsys):
    async def fail(db, url, **kw):
        raise pages.PageError("the page could not be read: \x1b]8;;https://evil.example.org\x07click\x1b]8;;\x07")

    monkeypatch.setattr(readers, "read", fail)
    assert await cli._read_page("https://www.example.org/", None) == 1
    out = capsys.readouterr().out
    assert out.startswith("error: ") and "\x1b" not in out and "\x07" not in out


async def test_read_page_with_a_named_reader_keeps_the_page_rules(monkeypatch, capsys):
    """--reader skips the chain, not the checks: http:// is refused and read_with still checks the address."""
    class Row:
        name, enabled, from_env = "Fire", True, False

    async def rows(db, **kw):
        return [Row()]

    asked: list[str] = []

    async def read_with(db, p, url, **kw):
        asked.append(url)
        raise service.ProviderError("not read: x")

    monkeypatch.setattr(service, "providers", rows)
    monkeypatch.setattr(readers, "read_with", read_with)
    assert await cli._read_page("http://www.example.org/", "Fire") == 1
    assert asked == [] and "only https://" in capsys.readouterr().out
    assert await cli._read_page("https://www.example.org/", "fire") == 1
    assert asked == ["https://www.example.org/"]


def test_reparse_state_files_hold_counts_only(tmp_path, monkeypatch):
    from mailr.config import get_settings

    monkeypatch.setattr(get_settings(), "data_dir", str(tmp_path))
    path = reparse._attached_state_path(7, dry_run=False)
    assert path == tmp_path / "state" / "reparse-attached-emails" / "7.json"
    assert reparse._attached_state_path(7, dry_run=True).name == "7-dry-run.json"
    stats = {"seen": 3, "with_attached": 1, "changed": 1, "failed": 0, "memories_withdrawn": 0,
             "accounts": {"2": 1}, "resumed_from": 0}
    reparse._save_state(path, 42, stats)
    data = json.loads(path.read_text())
    assert set(data) == {"cursor", "done", "stats"} and data["cursor"] == 42
    assert set(data["stats"]) == {"seen", "with_attached", "changed", "failed", "memories_withdrawn", "accounts"}
    assert not path.with_suffix(".tmp").exists()


def test_fix_zones_report_line_is_counts_only():
    from mailr.calendar import fix_zones

    line = fix_zones.line("a@example.org", {"scanned": 5, "affected": 2, "fixed": 1, "read_only": 1, "failed": 0},
                          False)
    assert line == "a@example.org: 5 with a TZID, 2 with a non-IANA zone, 1 fixed, 1 read-only (left), 0 failed"


@pytest.mark.parametrize("argv", [["mailr", "calendar-fix-zones", "--dry-run"],
                                  ["mailr", "reparse-attached-emails", "--dry-run"],
                                  ["mailr", "calendars", "export"]])
def test_commands_that_write_files_run_with_the_restrictive_umask(monkeypatch, argv):
    import os

    calls: list[int] = []
    real = os.umask
    monkeypatch.setattr(os, "umask", lambda m: calls.append(m) or real(m))
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(cli, "_run", lambda coro: coro.close())
    try:
        cli.main()
    except SystemExit:
        pass
    assert 0o027 in calls
