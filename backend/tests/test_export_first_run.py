"""A new install is healthy before its first daily export (core/health.py `_stale_waiting`), the
worker exports new owners within minutes (core/tasks.py `_first_exports`), and `mailr export`
runs the exports now (cli.py `_export`)."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from mailr.config import get_settings
from mailr.core import backup, health
from mailr.db import session_scope
from mailr.notes import backup as notes_backup

API = "/api/v1"


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "data_dir", str(tmp_path / "data"))
    monkeypatch.setattr(s, "backup_target", "")
    health.clear_cache()
    yield tmp_path
    health.clear_cache()


def _ns(user=None, mail=False, notes=False, calendars=False):
    return argparse.Namespace(user=user, mail=mail, notes=notes, calendars=calendars)


def test_new_owners_wait_and_overdue_ones_fail():
    now = datetime.now(UTC)
    young, old = now - timedelta(hours=1), now - timedelta(hours=30)
    # A fresh install: data an hour old, never exported: waiting, so healthy.
    assert health.evaluate_runs([None], now, "exported", [young]) == (
        True, "0 of 1 account exported in the last 26 h, 1 new (first export pending)")
    assert health.evaluate_runs([now, None, None], now, "exported", [old, young, young]) == (
        True, "1 of 3 accounts exported in the last 26 h, 2 new (first export pending)")
    # An export that should have happened and did not still fails.
    assert health.evaluate_runs([None], now, "exported", [old]) == (False, "1 of 1 account not exported in 26 h")
    assert health.evaluate_runs([None], now, "exported", [None]) == (False, "1 of 1 account not exported in 26 h")
    assert health.evaluate_runs([None], now, "exported") == (False, "1 of 1 account not exported in 26 h")
    # Once there was a run, a stale run fails however young the data is.
    assert health.evaluate_runs([now - timedelta(hours=27)], now, "exported", [young])[0] is False
    assert health.evaluate_runs([now], now, "exported", [young]) == (True, "1 account exported in the last 26 h")
    assert health.evaluate_runs([None], now, "copied", [young]) == (
        True, "0 of 1 account copied in the last 26 h, 1 new (first copy pending)")
    # Calendars and notes the same; a copy pending for a new owner waits too.
    assert health.evaluate_calendar_runs([None, now], None, now, [young, old]) == (
        True, "1 of 2 calendar exports in the last 26 h, 1 new (first export pending)")
    assert health.evaluate_calendar_runs([None], None, now, [old]) == (False, "1 of 1 calendar export not run in 26 h")
    assert health.evaluate_notes_runs([now], [None], now, [young]) == (
        True, "0 of 1 notes export in the last 26 h, 1 new (first export pending)")
    assert health.evaluate_notes_runs([now], [None], now, [old]) == (
        False, "1 of 1 notes export not copied in 26 h")
    assert health.evaluate_notes_runs([None], None, now, [young]) == (
        True, "0 of 1 notes export in the last 26 h, 1 new (first export pending)")


async def _age_user(uid: int, hours: int) -> None:
    async with session_scope() as db:
        await db.execute(text("UPDATE users SET created_at = now() - make_interval(hours => :h) WHERE id = :u"),
                         {"h": hours, "u": uid})
        await db.execute(text("UPDATE connectors SET created_at = now() - make_interval(hours => :h) "
                              "WHERE user_id = :u"), {"h": hours, "u": uid})


async def test_fresh_mail_is_ready_then_the_command_exports_it(app, user, raw, dirs, monkeypatch):
    from mailr import cli
    from mailr.core.ingest import IngestOptions, ingest_raw
    from mailr.core.store import user_store
    from mailr.models import User

    async with session_scope() as db:
        u = await db.get(User, user.id)
        await ingest_raw(db, await user_store(db, u), raw("First mail"), IngestOptions())

    async def only_this_user():
        return [user.id]

    monkeypatch.setattr(health, "_mail_users", only_this_user)
    # The first sync just happened: no export yet, and the readiness check passes.
    assert await health.check_export() == (True, "0 of 1 account exported in the last 26 h, 1 new (first export pending)")
    res = await health.run_checks(("export",))
    assert res["ok"] is True and res["checks"]["export"]["ok"] is True
    # A day later with still no export: that is a real failure.
    await _age_user(user.id, 30)
    assert await health.check_export() == (False, "1 of 1 account not exported in 26 h")
    # `mailr export --user … --mail` writes it now, and the check passes.
    assert await cli._export(_ns(user=user.email, mail=True)) == 0
    assert backup.last_run(backup.export_root(user.id)) is not None
    assert any((backup.export_root(user.id)).rglob("*.mailr:2,*"))
    assert await health.check_export() == (True, "1 account exported in the last 26 h")


@pytest.mark.db_user
async def test_first_note_waits_then_first_exports_and_the_command(app, user, client, dirs, monkeypatch):
    from mailr import cli
    from mailr.calendar import backup as cal_backup
    from mailr.core import tasks

    r = await client.post(f"{API}/notes", json={"kind": "text", "title": "Wifi", "body": "On the fridge."})
    assert r.status_code == 200, r.text

    async def only_this_user():
        return [user.id]

    async def nobody():
        return []

    monkeypatch.setattr(notes_backup, "owners_with_data", only_this_user)
    assert await health.check_notes_export() == (True, "0 of 1 notes export in the last 26 h, 1 new (first export pending)")

    # The worker queues the first export within minutes, once.
    monkeypatch.setattr(health, "_mail_users", nobody)
    monkeypatch.setattr(cal_backup, "owners_with_data", nobody)

    async def queued() -> int:
        async with session_scope() as db:
            return (await db.execute(text(
                "SELECT count(*) FROM jobs WHERE kind = 'export.notes' AND user_id = :u"), {"u": user.id})).scalar_one()

    await tasks._first_exports()
    assert await queued() == 1
    await tasks._first_exports()
    assert await queued() == 1
    assert (timedelta(minutes=5), tasks._first_exports) in tasks.PERIODIC

    # Old notes and still no export: failing.
    async with session_scope() as db:
        await db.execute(text("UPDATE notes SET created_at = now() - interval '30 hours' WHERE created_by = :u"),
                         {"u": user.id})
    assert await health.check_notes_export() == (False, "1 of 1 notes export not run in 26 h")
    assert await cli._export(_ns(user=str(user.id), notes=True)) == 0
    assert await health.check_notes_export() == (True, "1 notes export in the last 26 h")
    # Exported: nothing is queued for it any more.
    async with session_scope() as db:
        await db.execute(text("DELETE FROM jobs WHERE kind = 'export.notes' AND user_id = :u"), {"u": user.id})
    await tasks._first_exports()
    assert await queued() == 0


@pytest.mark.db_user
async def test_calendar_data_since(app, user, dirs):
    from mailr.calendar import backup as cal_backup

    assert await cal_backup.data_since([user.id]) == [None]
    assert await notes_backup.data_since([]) == []


def test_cli_parses_export(monkeypatch):
    from mailr import cli

    seen = []

    async def fake(a):
        seen.append((a.user, a.mail, a.notes, a.calendars))
        return 0

    monkeypatch.setattr(cli, "_export", fake)
    for argv in (["mailr", "export"], ["mailr", "export", "--user", "a@example.com", "--mail", "--notes"]):
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(SystemExit) as e:
            cli.main()
        assert e.value.code == 0
    assert seen == [(None, False, False, False), ("a@example.com", True, True, False)]
