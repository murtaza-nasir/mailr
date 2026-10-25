"""Smaller data-safety and security fixes: a removed user's notes and calendar exports go too (and
wait for an unreachable target), the mail store account goes after the user's rows, an older image
on a newer database stops before dumping, an old /changes cursor is told to resync, a token never
sets the device zone, error reports leave out invite tokens, only admins mint tls tokens, the device
label is capped, an always-asking card takes no standing grant, and download names in any script."""

from datetime import UTC, datetime
from urllib.parse import quote

import pytest
from fastapi import HTTPException
from helpers.agent import _token_client
from sqlalchemy import select, text

from mailr.config import get_settings
from mailr.core import backup, webheaders
from mailr.db import session_scope, sessionmaker
from mailr.models import Job, PendingAction, User


def test_a_removed_users_exports_all_go_and_wait_for_the_target(tmp_path, monkeypatch):
    from mailr.calendar import backup as calbackup
    from mailr.notes import backup as notesbackup

    s = get_settings()
    monkeypatch.setattr(s, "data_dir", str(tmp_path / "data"))
    nas = tmp_path / "nas"
    nas.mkdir()
    monkeypatch.setattr(s, "backup_target", str(nas))
    dirs = [backup.export_root(41), notesbackup.export_dir(41), calbackup.export_dir(41),
            backup.target_root(41), notesbackup.target_dir(41), calbackup.target_dir(41)]
    for d in dirs:
        d.mkdir(parents=True)
        (d / "x").write_text("y")
    # The share is not mounted (no marker): the local copies go, the target's wait.
    assert backup.remove_user_export(41) is False
    assert [d.exists() for d in dirs] == [False, False, False, True, True, True]
    (nas / backup.TARGET_MARKER).write_text("")
    assert backup.remove_user_export(41) is True
    assert not any(d.exists() for d in dirs)


async def test_the_store_account_goes_after_the_users_rows(app, admin_client, user, monkeypatch):
    from mailr.api import admin_routes

    async def down(u):
        raise RuntimeError("store unreachable")

    monkeypatch.setattr(admin_routes, "delete_store_account", down)
    async with sessionmaker()() as db:
        acct = (await db.get(User, user.id)).stalwart_account_id
    r = await admin_client.delete(f"/api/v1/admin/users/{user.id}", params={"confirm": user.email})
    assert r.status_code == 200, r.text
    async with sessionmaker()() as db:
        assert await db.get(User, user.id) is None
        jobs = (await db.execute(select(Job).where(Job.kind == "users.delete_store_account"))).scalars().all()
    assert {"account_id": acct} in [j.payload for j in jobs]


async def test_an_older_image_on_a_newer_database_stops_before_dumping(app, monkeypatch):
    from mailr.core import dbdump

    async def ahead(conn):
        return ["f00dfacecafe"]

    monkeypatch.setattr(dbdump, "_revision", ahead)
    with pytest.raises(RuntimeError, match="does not know"):
        await dbdump.pending_migrations()


async def test_an_old_changes_cursor_is_told_to_resync(client, user):
    """The log was pruned past the cursor (as the 60-day pruning does): a full resync, even when
    newer changes are there."""
    async with session_scope() as db:
        for i in range(5):
            await db.execute(text("INSERT INTO change_log (user_id, entity, entity_id, op) "
                                  "VALUES (:u, 'conversation', :i, 'upsert')"), {"u": user.id, "i": i + 1})
        await db.execute(text("DELETE FROM change_log WHERE id < (SELECT max(id) FROM change_log)"))
    got = (await client.get("/api/v1/changes", params={"since": 1})).json()
    assert got["reset"] is True
    cur = (await client.get("/api/v1/changes", params={"since": 0})).json()["cursor"]
    assert "reset" not in (await client.get("/api/v1/changes", params={"since": cur})).json()


async def test_a_token_never_sets_the_device_zone(client, user):
    tok = (await client.post("/api/v1/tokens", json={"name": "zone", "scopes": ["read"]})).json()["token"]
    async with _token_client(client, tok) as t:
        r = await t.get("/api/v1/me", headers={"X-Mailr-Zone": "Pacific/Auckland"})
        assert r.status_code == 200
    async with sessionmaker()() as db:
        assert (await db.get(User, user.id)).settings.get("device_timezone") != "Pacific/Auckland"


def test_error_reports_leave_out_invite_tokens():
    from mailr.core.errors import _strip_query

    assert _strip_query("https://mail.example/join/s3cr3t?x=1") == "https://mail.example/join/***"
    assert _strip_query("/api/v1/invites/s3cr3t/accept") == "/api/v1/invites/***/accept"
    assert _strip_query("/mail/inbox") == "/mail/inbox"


async def test_only_admins_mint_tls_tokens_and_device_labels_are_capped(user):
    from mailr.core.auth import create_session, create_token

    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        with pytest.raises(HTTPException):
            await create_token(db, u, "cert", ["tls"])
        _, s = await create_session(db, u, device="x" * 500)
        assert len(s.device) == 40


async def test_an_always_asking_card_takes_no_standing_grant(user):
    from mailr.core import permissions

    async with sessionmaker()() as db:
        pa = PendingAction(user_id=user.id, actor="Ambrose", action="mail.label", target="3 conversation(s)",
                           ctx={permissions.GATE_KEY: {"force": "a review always asks"}}, payload={"op": "review"},
                           preview={"title": "Review"}, status="pending", subject="assistant",
                           expires_at=datetime(2099, 1, 1, tzinfo=UTC))
        db.add(pa)
        await db.commit()
        u = await db.get(User, user.id)
        with pytest.raises(HTTPException) as e:
            await permissions.decide_full(db, u, pa.id, "approve", "always")
        assert e.value.status_code == 400
        await db.refresh(pa)
        assert pa.status == "pending"


def test_download_names_in_any_script():
    h = webheaders.attachment("Покупки.xlsx")
    h.encode("latin-1")  # a header Starlette can send
    assert h == f"attachment; filename=\"_______.xlsx\"; filename*=UTF-8''{quote('Покупки.xlsx', safe='')}"
