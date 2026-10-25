"""MAILR_MAIL_EXPORT=false: no daily or first mail export is queued and `mailr export` skips mail
(core/tasks.py, cli.py), the export check reports "off" and /health/ready stays 200 (core/health.py),
store mode is refused (api/connector_routes.py `check_store_allowed`), and an existing store-mode
account deletes nothing at the provider and says so (connectors/tasks.py, core/accounthealth.py).
The calendar, contacts, notes and tables exports run as before."""

from __future__ import annotations

import argparse

import pytest
from sqlalchemy import select, text

from mailr.config import get_settings
from mailr.core import backup, health
from mailr.db import session_scope
from mailr.models import Connector, Job
from mailr.notes import backup as notes_backup

pytestmark = pytest.mark.db_user

API = "/api/v1"
OFF_REASON = "“Mailr keeps the mail” is not available on this server"


@pytest.fixture
def off(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "data_dir", str(tmp_path / "data"))
    monkeypatch.setattr(s, "backup_target", "")
    monkeypatch.setattr(s, "mail_export", False)
    health.clear_cache()
    yield tmp_path
    health.clear_cache()


def _ns(user=None, mail=False, notes=False, calendars=False):
    return argparse.Namespace(user=user, mail=mail, notes=notes, calendars=calendars)


async def _jobs(kind: str, uid: int) -> int:
    async with session_scope() as db:
        return (await db.execute(text("SELECT count(*) FROM jobs WHERE kind = :k AND user_id = :u"),
                                 {"k": kind, "u": uid})).scalar_one()


async def _account(uid: int, mode: str, *, armed: bool = False) -> int:
    async with session_scope() as db:
        c = Connector(user_id=uid, kind="imap", name="Home", mode=mode, data_class="normal",
                      config={"email": "home@northwind.example",
                              "imap": {"host": "imap.northwind.example", "port": 993, "tls": "ssl",
                                       "username": "home@northwind.example"}},
                      state={"initial_done": True}, retention_days=0, delete_upstream=armed, enabled=True,
                      status="idle")
        db.add(c)
        await db.flush()
        return c.id


# ---------------------------------------------------------------- the worker and the command


async def test_no_mail_export_is_queued_but_notes_and_calendars_are(app, user, off, monkeypatch):
    from mailr.calendar import backup as cal_backup
    from mailr.core import tasks

    async def only_this_user():
        return [user.id]

    async def nobody():
        return []

    async def must_not_ask():
        raise AssertionError("the mail owners are not looked up while the mail export is off")

    monkeypatch.setattr(health, "_mail_users", must_not_ask)
    monkeypatch.setattr(notes_backup, "owners_with_data", only_this_user)
    monkeypatch.setattr(cal_backup, "owners_with_data", nobody)

    await tasks._first_exports()
    assert await _jobs("export.user", user.id) == 0
    assert await _jobs("export.notes", user.id) == 1

    async with session_scope() as db:
        await tasks._user_exports(db, user.id)  # the user's daily exports (core/tasks.py SPREAD)
    assert await _jobs("export.user", user.id) == 0
    assert await _jobs("export.calendars", user.id) == 1
    assert await _jobs("export.notes", user.id) == 1  # deduplicated with the first export's job

    # A job queued before the switch was turned off writes nothing.
    await tasks.export_user({"user_id": user.id}, None)
    assert not backup.export_root(user.id).exists()


async def test_the_command_skips_mail_and_refuses_an_explicit_mail(app, user, client, off, monkeypatch, capsys):
    from mailr import cli
    from mailr.calendar import backup as cal_backup

    r = await client.post(f"{API}/notes", json={"kind": "text", "title": "Wifi", "body": "On the fridge."})
    assert r.status_code == 200, r.text
    calendars: list[int] = []

    async def cal_export(uid: int):
        calendars.append(uid)
        return {}

    monkeypatch.setattr(cal_backup, "export_user", cal_export)

    # --mail alone: nothing to do, and the exit code says so.
    assert await cli._export(_ns(user=user.email, mail=True)) == 1
    err = capsys.readouterr().err
    assert "the mail export is off (MAILR_MAIL_EXPORT=false)" in err
    assert not backup.export_root(user.id).exists()

    # Everything: mail is skipped with a note, notes and calendars are written.
    assert await cli._export(_ns(user=user.email)) == 0
    out = capsys.readouterr().out
    assert "the mail export is off" in out and "notes and tables snapshot" in out
    assert calendars == [user.id]
    assert backup.last_run(notes_backup.export_dir(user.id)) is not None
    assert not backup.export_root(user.id).exists()


# ---------------------------------------------------------------- health


async def test_health_reports_off_and_ready_stays_200(app, off, monkeypatch, tmp_path):
    assert await health.check_export() == (True, "off (MAILR_MAIL_EXPORT=false)", health.OFF)
    res = await health.run_checks(("export", "backup_target"))
    assert res["ok"] is True
    export = {k: v for k, v in res["checks"]["export"].items() if k != "detail_msg"}
    assert export == {"ok": True, "detail": "off (MAILR_MAIL_EXPORT=false)", "required": True, "off": True}
    assert "off" not in res["checks"]["backup_target"]

    # A mounted target is still checked: it holds the calendar and notes copies.
    nas = tmp_path / "nas"
    nas.mkdir()
    monkeypatch.setattr(get_settings(), "backup_target", str(nas))
    assert (await health.check_backup_target())[0] is False
    (nas / backup.TARGET_MARKER).touch()
    assert await health.check_backup_target() == (True, "mounted and writable; the mail export is off")

    # The endpoint: every other check passing, the real export and target checks.
    funcs = {n: (lambda: _ok()) for n in health.CHECKS}
    funcs.update(export=health.check_export, backup_target=health.check_backup_target)
    monkeypatch.setattr(health, "CHECK_FUNCS", funcs)
    monkeypatch.setattr(get_settings(), "health_token", "monitor-token-for-tests")
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"X-Health-Token": "monitor-token-for-tests"}) as c:
        r = await c.get("/api/v1/health/ready")
    assert r.status_code == 200, r.text
    assert r.json()["checks"]["export"]["off"] is True
    assert "warn" not in r.json()["checks"]["export"]


async def _ok():
    return True, "fine"


# ---------------------------------------------------------------- store mode


async def test_store_mode_is_refused_on_create_update_and_arming(app, user, client, off, monkeypatch):
    me = (await client.get(f"{API}/me")).json()
    assert me["features"]["store_mode"] is False

    body = {"kind": "imap", "name": "Work", "mode": "store", "skip_test": True,
            "config": {"email": "work@northwind.example",
                       "imap": {"host": "imap.northwind.example", "port": 993, "tls": "ssl"}},
            "secrets": {"password": "pw"}}
    r = await client.post(f"{API}/connectors", json=body)
    assert r.status_code == 400 and OFF_REASON in r.json()["detail"]
    async with session_scope() as db:
        assert (await db.execute(select(Connector).where(Connector.user_id == user.id))).first() is None

    # Without a mode, an IMAP account is a mirror while the export is off.
    r = await client.post(f"{API}/connectors", json={k: v for k, v in body.items() if k != "mode"})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "mirror"
    cid = r.json()["id"]

    r = await client.patch(f"{API}/connectors/{cid}", json={"mode": "store"})
    assert r.status_code == 400 and OFF_REASON in r.json()["detail"]
    async with session_scope() as db:
        assert (await db.get(Connector, cid)).mode == "mirror"

    store = await _account(user.id, "store")
    r = await client.post(f"{API}/connectors/{store}/arm-deletion", json={"confirm": "Home"})
    assert r.status_code == 400 and OFF_REASON in r.json()["detail"]

    # With the export on, the same change is accepted.
    monkeypatch.setattr(get_settings(), "mail_export", True)
    r = await client.patch(f"{API}/connectors/{cid}", json={"mode": "store"})
    assert r.status_code == 200 and r.json()["mode"] == "store"


async def test_an_existing_store_account_deletes_nothing_and_says_so(app, user, client, off, monkeypatch):
    from mailr.connectors import tasks as ctasks
    from mailr.core import accounthealth
    from mailr.core import tasks as core_tasks

    cid = await _account(user.id, "store", armed=True)
    # An export left from before the switch was turned off proves nothing now.
    root = backup.export_root(user.id)
    root.mkdir(parents=True)
    (root / ".exported").write_text("blob-1\tInbox/cur/1.1.mailr:2,S\n")
    assert ctasks.exported_blobs(user.id) == set()
    monkeypatch.setattr(get_settings(), "mail_export", True)
    assert ctasks.exported_blobs(user.id) == {"blob-1"}
    monkeypatch.setattr(get_settings(), "mail_export", False)

    async def no_connection(*a, **k):
        raise AssertionError("the provider is not contacted while the mail export is off")

    monkeypatch.setattr(ctasks, "connector_for", no_connection)
    await ctasks.retention({"connector_id": cid}, None)
    await core_tasks._retention()
    async with session_scope() as db:
        queued = (await db.execute(select(Job).where(Job.kind == "connector.retention", Job.user_id == user.id))).first()
    assert queued is None

    # The owner and the admin see why.
    accounts = (await client.get(f"{API}/connectors")).json()
    home = next(a for a in accounts if a["id"] == cid)
    assert home["health"]["state"] == "stale"
    assert home["health"]["code"] == "account.deletion_paused_export_off"
    assert home["health"]["text"] == "Deletion at the provider paused: the mail export is off"
    async with session_scope() as db:
        c = await db.get(Connector, cid)
        assert accounthealth.of(c, lang="de")["text"] != home["health"]["text"]
        c.mode = "mirror"
        assert accounthealth.of(c)["code"] != "account.deletion_paused_export_off"
