"""The mail export after the database was restored from an older dump (core/backup.py): the cursor
points past the restored rows, so the export starts again from the first row, and the worker's chain
of export jobs follows the rows a batch went past, not the files it wrote."""

from __future__ import annotations

import pytest
from helpers.core_flow import _ingest
from sqlalchemy import select, text

from mailr.config import get_settings
from mailr.core import backup, tasks
from mailr.db import session_scope
from mailr.models import Job, Message


@pytest.fixture
def export_dirs(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "data_dir", str(tmp_path / "data"))
    monkeypatch.setattr(s, "backup_target", "")
    monkeypatch.setattr(s, "mail_export", True)


async def _blob(mid: int) -> str:
    async with session_scope() as db:
        return (await db.execute(select(Message.blob_id).where(Message.id == mid))).scalar_one()


async def test_the_cursor_keeps_the_blob_of_its_row_and_a_run_goes_on_from_it(user, raw, export_dirs):
    a, _c = await _ingest(user, raw("Seed order", body="Tomato seeds"))
    assert await backup.export_user(user.id) == 1
    cur = backup.export_root(user.id) / ".cursor"
    assert cur.read_text() == f"{a}\t{await _blob(a)}"
    b, _c = await _ingest(user, raw("Seed invoice"))
    assert await backup.export_user(user.id) == 1
    assert backup._read_cursor(cur) == (b, await _blob(b))
    # A cursor written before the blob id was kept still counts.
    cur.write_text(str(b))
    assert backup._read_cursor(cur) == (b, None)
    r = await backup.export_batch(user.id)
    assert r == {"written": 0, "scanned": 0, "complete": True, "stalled": False}


async def test_a_cursor_ahead_of_a_restored_database_starts_again(user, raw, export_dirs):
    """The dump is older than the export: the cursor names a row the restored database does not
    have, and the ids handed out next are below it. Those messages are exported all the same."""
    old, _c = await _ingest(user, raw("Before the dump", body="Kept in the dump"))
    assert await backup.export_user(user.id) == 1
    base = backup.export_root(user.id)
    async with session_scope() as db:
        top = (await db.execute(text("SELECT max(id) FROM messages"))).scalar_one()
    # What the export wrote after the dump was taken: a cursor far past the restored rows.
    (base / ".cursor").write_text(f"{top + 500}\tblob-from-after-the-dump")
    (base / backup.FORGOTTEN).write_text(f"{top + 1}\t9999999999\n")  # ids that are handed out again
    new, _c = await _ingest(user, raw("After the restore", body="New mail"))
    assert new < top + 500

    r = await backup.export_batch(user.id)
    assert r["written"] == 1 and r["complete"] is True
    assert set(backup.exported_manifest(user.id)) == {await _blob(old), await _blob(new)}
    assert backup._read_cursor(base / ".cursor") == (new, await _blob(new))
    assert not (base / backup.FORGOTTEN).exists()


async def test_a_reused_id_with_other_content_also_starts_again(user, raw, export_dirs):
    """The row at the cursor's id holds another message than the export recorded (the id was handed
    out again after a restore): the rows below it are checked against the export again."""
    await _ingest(user, raw("First"))
    b, _c = await _ingest(user, raw("Second"))
    assert await backup.export_user(user.id) == 2
    base = backup.export_root(user.id)
    (base / ".exported").write_text("")  # the restored rows are not in the export
    (base / ".cursor").write_text(f"{b}\tanother-message-had-this-id")
    assert await backup.export_user(user.id) == 2
    # With the right blob, the same cursor is trusted: nothing is read again.
    assert await backup.export_user(user.id) == 0


async def test_the_export_chain_continues_through_batches_already_exported(user, raw, export_dirs, monkeypatch):
    """After a rewind every batch is in the manifest already: the worker still chains the next job
    until the run catches up, instead of stopping after one batch a day."""
    monkeypatch.setattr(backup, "BATCH", 2)
    ids = [(await _ingest(user, raw(f"Batch {i}")))[0] for i in range(5)]
    while not (await backup.export_batch(user.id))["complete"]:
        pass
    assert len(backup.exported_manifest(user.id)) == 5
    (backup.export_root(user.id) / ".cursor").unlink()  # a rewind

    # Two rows, both exported: nothing written, and the job runs again for the next batch (a Retry
    # keeps the job and its dedupe key).
    from mailr.core.jobs import Retry

    with pytest.raises(Retry):
        await tasks.export_user({"user_id": user.id}, None)
    assert backup._read_cursor(backup.export_root(user.id) / ".cursor")[0] == ids[1]
    async with session_scope() as db:
        await db.execute(text("UPDATE jobs SET status = 'done' WHERE kind = 'export.user' AND user_id = :u"),
                         {"u": user.id})
    # The CLI's loop goes on the same way.
    total, caught_up = await backup.export_until_caught_up(user.id)
    assert (total, caught_up) == (0, True)


async def test_an_unreadable_message_stops_the_chain(user, raw, export_dirs, monkeypatch):
    await _ingest(user, raw("Unreadable"))

    async def broken(self, m):
        raise RuntimeError("blob gone")

    from mailr.core.store import UserStore

    monkeypatch.setattr(UserStore, "get_raw", broken)
    r = await backup.export_batch(user.id)
    assert r["stalled"] is True and r["complete"] is False and r["written"] == 0
    async with session_scope() as db:
        before = len((await db.execute(select(Job).where(Job.kind == "export.user", Job.user_id == user.id,
                                                         Job.status == "queued"))).scalars().all())
    await tasks.export_user({"user_id": user.id}, None)
    async with session_scope() as db:
        after = len((await db.execute(select(Job).where(Job.kind == "export.user", Job.user_id == user.id,
                                                        Job.status == "queued"))).scalars().all())
    assert after == before  # retried by the next daily run, not every two seconds
