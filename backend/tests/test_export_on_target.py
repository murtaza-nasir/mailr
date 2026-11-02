"""The mail export with a backup target (core/backup.py): the message files are written to the
target only, and the data volume keeps only the small state (manifest, cursor, run records).
While the target cannot be used nothing is written, nothing counts as backed up and deletion at
the provider waits; the export catches up when the target is back. Files a release before this
one wrote locally are moved to the target (`move_to_target`): a local file is removed only once
the target's copy has the same size. Without a target the export stays local, as before."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from helpers.connectors import create_connector, imap, make_upstream_account, msg, run_sync
from helpers.core_flow import _ingest

from mailr import cli
from mailr.config import get_settings
from mailr.connectors.tasks import exported_blobs, retention
from mailr.core import backup, health
from mailr.db import session_scope
from mailr.models import Connector

API = "/api/v1"


@pytest.fixture
def nas(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "data_dir", str(tmp_path / "data"))
    share = tmp_path / "nas"
    share.mkdir()
    (share / backup.TARGET_MARKER).touch()
    monkeypatch.setattr(s, "backup_target", str(share))
    monkeypatch.setattr(s, "mail_export", True)
    monkeypatch.setattr(s, "store_backup_check", True)
    health.clear_cache()
    yield share
    health.clear_cache()


def _files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.glob("*/cur/*") if p.is_file()}


def _offline(share: Path) -> None:
    (share / backup.TARGET_MARKER).unlink()  # the share is not mounted


def _online(share: Path) -> None:
    (share / backup.TARGET_MARKER).touch()


async def test_the_files_are_written_to_the_target_only(user, raw, nas):
    ids = [(await _ingest(user, raw(f"Harvest note {i}", body=f"Row {i}")))[0] for i in range(3)]
    n, caught_up = await backup.export_until_caught_up(user.id)
    assert n == 3 and caught_up
    base, tgt = backup.export_root(user.id), backup.target_root(user.id)
    manifest = backup.exported_manifest(user.id)
    assert len(manifest) == 3
    assert _files(base) == set() and not any(d.is_dir() for d in base.iterdir())  # state files only
    assert _files(tgt) == set(manifest.values())
    assert (tgt / ".exported").read_text() == (base / ".exported").read_text()
    assert backup._read_cursor(base / ".cursor")[0] == ids[-1]
    assert backup.last_run(base) is not None and backup.last_run(tgt) is not None
    assert backup.files_root(user.id) == tgt
    assert exported_blobs(user.id) == set(manifest)
    assert (await backup.verify(user.id))["ok"] is True
    assert (base / backup.ON_TARGET).read_text().strip() == str(nas)


async def test_an_offline_target_holds_the_export_and_deletion_then_it_catches_up(client, user, nas):
    """Deletion at the provider is on. The share goes away: a new message is not exported anywhere,
    nothing counts as backed up, nothing is deleted at the provider, and the health check names
    the problem. With the share back, the export catches up and deletion goes ahead."""
    email, pw = make_upstream_account()
    c = imap(email, pw)
    c.append("INBOX", msg("Seed catalogue", msgid="<t1@farm.example>", body="Spring list"))
    c.append("INBOX", msg("Tractor service", msgid="<t2@farm.example>"))
    c.logout()
    conn = await create_connector(client, email, pw)
    await run_sync(conn["id"])
    c = imap(email, pw)
    c.append("INBOX", msg("Feed invoice", msgid="<t3@farm.example>"))
    c.logout()
    assert (await run_sync(conn["id"]))["new"] == 1
    await backup.export_until_caught_up(user.id)
    assert (await client.post(f"{API}/connectors/{conn['id']}/arm-deletion",
                              json={"confirm": conn["name"]})).status_code == 200
    async with session_scope() as db:
        (await db.get(Connector, conn["id"])).retention_days = 0

    _offline(nas)
    c = imap(email, pw)
    c.append("INBOX", msg("Fence quote", msgid="<t4@farm.example>"))
    c.logout()
    assert (await run_sync(conn["id"]))["new"] == 1
    base = backup.export_root(user.id)
    cursor = (base / ".cursor").read_text()
    r = await backup.export_batch(user.id)
    assert r["waiting"] and r["stalled"] and not r["complete"] and r["written"] == 0
    assert (base / ".cursor").read_text() == cursor
    assert _files(base) == set() and len(backup.exported_manifest(user.id)) == 3
    assert await backup.export_until_caught_up(user.id) == (0, False)
    assert exported_blobs(user.id) == set()
    await retention({"connector_id": conn["id"]}, None)
    c = imap(email, pw)
    c.select_folder("INBOX")
    assert len(c.search(["ALL"])) == 4
    c.logout()
    ok, detail = await health.check_backup_target()
    assert ok is False and "not mounted" in str(detail)
    assert (await backup.verify(user.id))["ok"] is False

    _online(nas)
    n, caught_up = await backup.export_until_caught_up(user.id)
    assert n == 1 and caught_up
    assert _files(base) == set() and len(_files(backup.target_root(user.id))) == 4
    assert len(exported_blobs(user.id)) == 4
    await retention({"connector_id": conn["id"]}, None)
    c = imap(email, pw)
    c.select_folder("INBOX")
    assert len(c.search(["ALL"])) == 0
    c.logout()


async def test_a_share_that_goes_away_mid_batch_keeps_what_was_written(user, raw, nas, monkeypatch):
    for i in range(3):
        await _ingest(user, raw(f"Irrigation log {i}"))
    real = backup._maildir_write
    calls = []

    def flaky(folder, tmp, data, dest):
        calls.append(dest)
        if len(calls) == 2:
            raise OSError(5, "Input/output error")
        real(folder, tmp, data, dest)

    monkeypatch.setattr(backup, "_maildir_write", flaky)
    r = await backup.export_batch(user.id)
    assert r["waiting"] and r["written"] == 1
    assert len(backup.exported_manifest(user.id)) == 1
    monkeypatch.setattr(backup, "_maildir_write", real)
    n, caught_up = await backup.export_until_caught_up(user.id)
    assert n == 2 and caught_up and len(_files(backup.target_root(user.id))) == 3


async def test_no_folder_is_created_under_an_empty_mount_point(user, raw, nas, monkeypatch):
    """The share disappears between the check and the write (its mount point stays): the user's
    folder is not there, so nothing is created on the local disk under the mount point."""
    await _ingest(user, raw("Greenhouse"))
    real = backup._ready_target

    def vanished(uid):
        tgt = real(uid)
        import shutil

        shutil.rmtree(tgt)
        return tgt

    monkeypatch.setattr(backup, "_ready_target", vanished)
    monkeypatch.setattr(backup, "move_to_target", lambda uid: {"problem": None})
    r = await backup.export_batch(user.id)
    assert r["waiting"] and r["written"] == 0
    assert not backup.target_root(user.id).exists()


def _old_release_export(base: Path, files: dict[str, bytes]) -> None:
    """A local export as an earlier release left it."""
    lines = []
    for i, (rel, data) in enumerate(files.items()):
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        lines.append(f"blob{i}\t{rel}\n")
    (base / ".exported").write_text("".join(lines))
    (base / ".cursor").write_text("99\tblob9")


def test_the_one_time_move_removes_only_verified_local_files(nas, monkeypatch, caplog):
    caplog.set_level(logging.INFO, "mailr.backup")
    uid = 31
    base, tgt = backup.export_root(uid), backup.target_root(uid)
    same = "Inbox/cur/1.1.mailr:2,S"
    absent = "Inbox/cur/2.2.mailr:2,"
    differs = "Archive/cur/3.3.mailr:2,"
    unverifiable = "Archive/cur/4.4.mailr:2,"
    _old_release_export(base, {same: b"same bytes", absent: b"only here", differs: b"the right bytes",
                               unverifiable: b"cannot be copied"})
    (base / "Inbox" / "cur" / "9.9.mailr:2,").write_bytes(b"in no manifest line")
    (base / ".exported").write_text((base / ".exported").read_text() + "blob-lost\tInbox/cur/8.8.mailr:2,\n")
    for rel, data in ((same, b"same bytes"), (differs, b"short")):
        (tgt / rel).parent.mkdir(parents=True, exist_ok=True)
        (tgt / rel).write_bytes(data)

    # The dry run counts and changes nothing.
    r = backup.move_to_target(uid, dry_run=True)
    assert (r["verified"], r["copied"], r["kept"], r["lost"], r["unlisted"]) == (1, 3, 0, 1, 1)
    assert len(_files(base)) == 5 and not (tgt / absent).exists() and not (base / backup.ON_TARGET).exists()

    real = backup._copy_verified

    def bad_copy(src, dest):
        if dest.name.startswith("4.4"):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"cut")  # the copy on the share comes out short
            return dest.stat().st_size
        return real(src, dest)

    monkeypatch.setattr(backup, "_copy_verified", bad_copy)
    r = backup.move_to_target(uid)
    assert (r["verified"], r["copied"], r["kept"], r["lost"], r["unlisted"]) == (1, 2, 1, 1, 1)
    assert r["done"] is False and not (base / backup.ON_TARGET).exists()
    assert _files(base) == {unverifiable, "Inbox/cur/9.9.mailr:2,"}  # unverified and unlisted files stay
    assert (tgt / absent).read_bytes() == b"only here" and (tgt / differs).read_bytes() == b"the right bytes"
    # The line whose file is on neither side is dropped and the cursor rewound: exported again.
    assert "blob-lost" not in backup.exported_manifest(uid) and not (base / ".cursor").exists()
    assert (tgt / ".exported").read_text() == (base / ".exported").read_text()
    assert "removed locally" in caplog.text

    # The batches right after it do not walk the export again; a later run finishes the move once
    # the copy succeeds.
    monkeypatch.setattr(backup, "_copy_verified", real)
    assert backup.move_to_target(uid)["skipped"] is True and _files(base) != {"Inbox/cur/9.9.mailr:2,"}
    monkeypatch.setattr(backup, "MOVE_RETRY_SECONDS", 0)
    r = backup.move_to_target(uid)
    assert (r["copied"], r["kept"], r["done"]) == (1, 0, True)
    assert _files(base) == {"Inbox/cur/9.9.mailr:2,"}
    assert (tgt / unverifiable).read_bytes() == b"cannot be copied"
    assert (base / backup.ON_TARGET).read_text().strip() == str(nas)
    assert not (base / "Archive").exists()  # emptied local folders go
    assert backup.move_to_target(uid)["skipped"] is True


def test_the_move_stops_without_dropping_anything_when_the_share_goes(nas, monkeypatch):
    uid = 32
    base, tgt = backup.export_root(uid), backup.target_root(uid)
    _old_release_export(base, {f"Inbox/cur/{i}.{i}.mailr:2,": b"x" * i for i in range(1, 5)})
    real = backup._size
    seen = []

    def gone_after_two(p):
        if str(p).startswith(str(tgt)):
            seen.append(p)
            if len(seen) > 4:
                raise OSError(116, "Stale file handle")
        return real(p)

    monkeypatch.setattr(backup, "_size", gone_after_two)
    r = backup.move_to_target(uid)
    assert r["problem"] and r["done"] is False
    assert len(_files(base)) == 2 and len(_files(tgt)) == 2  # resumable: two moved, two still here
    assert len(backup.exported_manifest(uid)) == 4 and (base / ".cursor").exists()

    # The share is not mounted at all: nothing is dropped although no file is found there.
    monkeypatch.setattr(backup, "_size", real)
    _offline(nas)
    assert backup.move_to_target(uid)["problem"]
    assert len(backup.exported_manifest(uid)) == 4
    _online(nas)
    r = backup.move_to_target(uid)
    assert r["done"] and r["lost"] == 0 and _files(base) == set() and len(_files(tgt)) == 4


async def test_an_upgraded_install_moves_its_export_in_the_export_run(user, raw, nas, monkeypatch):
    """Files exported with no target (or by the release before this one) are moved by the next
    export run, which then writes new files to the target only."""
    monkeypatch.setattr(get_settings(), "backup_target", "")
    await _ingest(user, raw("Before the share"))
    await backup.export_until_caught_up(user.id)
    base = backup.export_root(user.id)
    assert len(_files(base)) == 1
    monkeypatch.setattr(get_settings(), "backup_target", str(nas))
    assert exported_blobs(user.id) == set()  # not on the target yet
    await _ingest(user, raw("After the share"))
    n, caught_up = await backup.export_until_caught_up(user.id)
    assert n == 1 and caught_up
    assert _files(base) == set() and len(_files(backup.target_root(user.id))) == 2
    assert len(exported_blobs(user.id)) == 2


async def test_without_a_target_the_export_stays_local(user, raw, nas, monkeypatch):
    monkeypatch.setattr(get_settings(), "backup_target", "")
    await _ingest(user, raw("Local only"))
    assert await backup.export_user(user.id) == 1
    base = backup.export_root(user.id)
    assert len(_files(base)) == 1 and backup.files_root(user.id) == base
    assert not (base / backup.ON_TARGET).exists()
    assert list(nas.iterdir()) == [nas / backup.TARGET_MARKER]
    assert exported_blobs(user.id) == set(backup.exported_manifest(user.id))


async def test_a_target_taken_out_of_the_configuration_starts_a_local_export(user, raw, nas, monkeypatch):
    """The files are on the share, and MAILR_BACKUP_TARGET is removed: the manifest names files that
    are not here, so nothing counts as backed up, and the next run exports everything locally."""
    await _ingest(user, raw("On the share"))
    await backup.export_until_caught_up(user.id)
    monkeypatch.setattr(get_settings(), "backup_target", "")
    assert backup.files_elsewhere(user.id)
    assert exported_blobs(user.id) == set()
    n, caught_up = await backup.export_until_caught_up(user.id)
    assert n == 1 and caught_up
    base = backup.export_root(user.id)
    assert len(_files(base)) == 1 and not backup.files_elsewhere(user.id)
    assert (base / ".exported.on-target").exists()
    assert exported_blobs(user.id) == set(backup.exported_manifest(user.id))


async def test_the_command_reports_and_moves(user, nas, capsys):
    base = backup.export_root(user.id)
    _old_release_export(base, {"Inbox/cur/1.1.mailr:2,": b"one", "Inbox/cur/2.2.mailr:2,": b"two"})
    assert await cli._export_move(user.email, dry_run=True) == 0
    out = capsys.readouterr().out
    assert "2 would be copied" in out and "dry run, nothing changed" in out
    assert len(_files(base)) == 2
    assert await cli._export_move(user.email, dry_run=False) == 0
    assert "2 were copied" in capsys.readouterr().out
    assert _files(base) == set() and len(_files(backup.target_root(user.id))) == 2
    _offline(nas)
    assert await cli._export_move(user.email, dry_run=True) == 1
    assert "cannot be used" in capsys.readouterr().out


async def test_import_export_reads_the_files_on_the_target(user, raw, nas, capsys):
    from mailr.models import StoreSyncState

    await _ingest(user, raw("Survey on the share"))
    await backup.export_until_caught_up(user.id)
    async with session_scope() as db:
        st = await db.get(StoreSyncState, user.id)
        if st is None:
            db.add(StoreSyncState(user_id=user.id, email_state="s-1"))
        elif st.email_state is None:
            st.email_state = "s-1"
    assert await cli._store_import_export(user.email) == 0
    assert "1 exported messages queued for import" in capsys.readouterr().out
    _offline(nas)
    assert await cli._store_import_export(user.email) == 1
    assert "cannot be used" in capsys.readouterr().out
