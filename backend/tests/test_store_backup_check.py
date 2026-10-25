"""MAILR_STORE_BACKUP_CHECK=false: store mode works without the daily mail export, and deletion at
the provider checks each message against the mail store instead of the export
(connectors/tasks.py retention, stored_blobs). The retention window still applies, and a message
the store does not hold is never deleted upstream. The web, account health, Admin → Diagnostics
and `mailr check-config` say that no backup is checked. With the setting on, nothing changes
(test_connectors.py, test_mail_export_off.py)."""

from __future__ import annotations

import pytest
from helpers.connectors import create_connector, imap, make_upstream_account, msg, run_sync
from sqlalchemy import select, text

from mailr.config import get_settings
from mailr.core import backup, health
from mailr.db import session_scope
from mailr.models import Connector, Job, Message

API = "/api/v1"


@pytest.fixture
def unchecked(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "data_dir", str(tmp_path / "data"))
    monkeypatch.setattr(s, "backup_target", "")
    monkeypatch.setattr(s, "mail_export", False)
    monkeypatch.setattr(s, "store_backup_check", False)
    health.clear_cache()
    yield tmp_path
    health.clear_cache()


def _upstream_count(email: str, pw: str, folder: str = "INBOX") -> int:
    c = imap(email, pw)
    c.select_folder(folder)
    n = len(c.search(["ALL"]))
    c.logout()
    return n


async def _store_account(client) -> tuple[dict, str, str]:
    """A store-mode account with two messages from its first sync and one after it (arming needs
    mail that arrived after the first sync)."""
    email, pw = make_upstream_account()
    c = imap(email, pw)
    c.append("INBOX", msg("Quarterly figures", msgid="<q1@ledger.example>", body="Totals attached"))
    c.append("INBOX", msg("Board minutes", msgid="<q2@ledger.example>"))
    c.logout()
    conn = await create_connector(client, email, pw)
    assert conn["mode"] == "store"
    await run_sync(conn["id"])
    c = imap(email, pw)
    c.append("INBOX", msg("New supplier", msgid="<q3@ledger.example>"))
    c.logout()
    assert (await run_sync(conn["id"]))["new"] == 1
    return conn, email, pw


async def test_store_mode_is_offered_and_armed_without_the_export(client, user, unchecked):
    me = (await client.get(f"{API}/me")).json()
    assert me["features"]["store_mode"] is True and me["features"]["store_backup_check"] is False

    conn, _email, _pw = await _store_account(client)
    r = await client.post(f"{API}/connectors/{conn['id']}/arm-deletion", json={"confirm": conn["name"]})
    assert r.status_code == 200, r.text
    assert r.json()["delete_upstream"] is True
    assert not backup.export_root(user.id).exists()  # no export was needed or written

    # No "paused" warning; the export check itself still reports off.
    accounts = (await client.get(f"{API}/connectors")).json()
    h = next(a for a in accounts if a["id"] == conn["id"])["health"]
    assert h["code"] != "account.deletion_paused_export_off"
    assert await health.check_export() == (True, "off (MAILR_MAIL_EXPORT=false)", health.OFF)


async def test_retention_deletes_stored_mail_after_the_window_without_an_export(client, user, unchecked):
    from mailr.connectors.tasks import retention
    from mailr.core import tasks as core_tasks

    conn, email, pw = await _store_account(client)
    assert (await client.post(f"{API}/connectors/{conn['id']}/arm-deletion",
                              json={"confirm": conn["name"]})).status_code == 200

    # Inside the retention window: nothing goes.
    async with session_scope() as db:
        (await db.get(Connector, conn["id"])).retention_days = 30
    await retention({"connector_id": conn["id"]}, None)
    assert _upstream_count(email, pw) == 3

    # The scheduler queues the run although the export is off.
    await core_tasks._retention()
    async with session_scope() as db:
        assert (await db.execute(select(Job).where(Job.kind == "connector.retention",
                                                   Job.user_id == user.id))).first() is not None

    # Past the window, a message whose copy the store does not hold stays at the provider.
    async with session_scope() as db:
        (await db.get(Connector, conn["id"])).retention_days = 0
        missing = (await db.execute(select(Message).where(Message.user_id == user.id,
                                                          Message.message_id_hdr == "<q2@ledger.example>"))).scalar_one()
        real_jid = missing.jmap_id
        missing.jmap_id = "no-such-email"
    await retention({"connector_id": conn["id"]}, None)
    assert _upstream_count(email, pw) == 1
    async with session_scope() as db:
        left = (await db.execute(text(
            "SELECT message_id_hdr FROM messages WHERE connector_id = :c AND upstream_deleted_at IS NULL"),
            {"c": conn["id"]})).scalars().all()
    assert left == ["<q2@ledger.example>"]

    # Once it is in the store it goes too.
    async with session_scope() as db:
        (await db.get(Message, missing.id)).jmap_id = real_jid
    await retention({"connector_id": conn["id"]}, None)
    assert _upstream_count(email, pw) == 0
    # The mail is still in mailr.
    body = (await client.get(f"{API}/conversations", params={"view": "all", "q": "Totals"})).json()
    assert body["total"] == 1


async def test_a_deleted_message_is_never_counted_as_stored(user, unchecked):
    from mailr.connectors.tasks import stored_blobs

    async with session_scope() as db:
        m = Message(user_id=user.id, jmap_id=None, blob_id="blob-x", deleted=True)
        assert await stored_blobs(db, user.id, [m]) == set()
        assert await stored_blobs(db, user.id, []) == set()


async def test_diagnostics_and_check_config_say_no_backup_is_checked(app, admin_client, user, unchecked, monkeypatch):
    from mailr.core import configcheck

    d = (await admin_client.get(f"{API}/admin/diagnostics")).json()
    assert d["backup"]["store_backup_check"] is False and d["backup"]["mail_export"] is False

    async with session_scope() as db:
        # Accounts armed by earlier tests in this database would count too.
        await db.execute(text("UPDATE connectors SET delete_upstream = false"))
        c = Connector(user_id=user.id, kind="imap", name="Ledger", mode="store", data_class="normal",
                      config={"email": "desk@ledger.example"}, state={"initial_done": True}, retention_days=7,
                      delete_upstream=True, enabled=True, status="idle")
        db.add(c)
        await db.flush()
        cid = c.id
    found = [p for p in await configcheck.online_problems() if p.var == "MAILR_STORE_BACKUP_CHECK"]
    assert len(found) == 1 and found[0].level == configcheck.WARNING
    assert "without a backup check" in found[0].message
    # The account mode by its name in the app, not the API value.
    assert "1 account where Mailr keeps the mail" in found[0].message and "store-mode" not in found[0].message
    assert not configcheck.fatal(found)

    # Not with the check on, and not without an armed store account.
    monkeypatch.setattr(get_settings(), "store_backup_check", True)
    assert not [p for p in await configcheck.online_problems() if p.var == "MAILR_STORE_BACKUP_CHECK"]
    monkeypatch.setattr(get_settings(), "store_backup_check", False)
    async with session_scope() as db:
        (await db.get(Connector, cid)).delete_upstream = False
    assert not [p for p in await configcheck.online_problems() if p.var == "MAILR_STORE_BACKUP_CHECK"]


async def test_with_the_check_on_store_mode_still_needs_the_export(client, user, unchecked, monkeypatch):
    monkeypatch.setattr(get_settings(), "store_backup_check", True)
    me = (await client.get(f"{API}/me")).json()
    assert me["features"]["store_mode"] is False and me["features"]["store_backup_check"] is True
    body = {"kind": "imap", "name": "Ledger", "mode": "store", "skip_test": True,
            "config": {"email": "desk@ledger.example",
                       "imap": {"host": "imap.ledger.example", "port": 993, "tls": "ssl"}},
            "secrets": {"password": "pw"}}
    r = await client.post(f"{API}/connectors", json=body)
    assert r.status_code == 400


def test_provider_notes_name_the_modes_as_the_app_does():
    from mailr.connectors.presets import PRESETS

    notes = " ".join(p.get("notes", "") for p in PRESETS.values())
    assert "store mode" not in notes and "mirror mode" not in notes
    assert "“Mailr keeps the mail”" in notes
