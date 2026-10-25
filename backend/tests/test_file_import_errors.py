"""A mailbox file import (connectors/mbox.py) skips and counts a message that cannot be imported
instead of stopping for good; many failures in a row still stop the run, since then the database or
the mail store is the trouble, not the messages."""

from __future__ import annotations

import io
import mailbox
import zipfile

import pytest
from helpers.connectors import msg, msg_count, run_sync

from mailr.connectors import mbox as mbox_mod
from mailr.connectors import runner
from mailr.connectors.mbox import imports_root
from mailr.db import sessionmaker
from mailr.models import Connector


def _broken_on(monkeypatch, subjects: set[str]) -> None:
    real = runner.ingest_raw

    async def ingest_raw(db, store, raw, opts):
        if any(f"Subject: {s}".encode() in raw for s in subjects):
            raise ValueError("unparseable header")
        return await real(db, store, raw, opts)

    monkeypatch.setattr(runner, "ingest_raw", ingest_raw)


async def _file_connector(client, name: str) -> int:
    r = await client.post("/api/v1/connectors", json={"kind": "file", "name": "Archive", "config": {"path": name}})
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def _state(cid: int) -> dict:
    async with sessionmaker()() as db:
        return dict((await db.get(Connector, cid)).state or {})


async def test_one_bad_message_in_an_mbox_is_skipped_and_counted(client, user, monkeypatch):
    root = imports_root(user.id)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "archive.mbox"
    box = mailbox.mbox(str(path))
    for subject in ("Garden plan", "Broken one", "Seed list"):
        box.add(mailbox.mboxMessage(msg(subject)))
    box.flush()
    box.close()
    _broken_on(monkeypatch, {"Broken one"})
    cid = await _file_connector(client, path.name)
    totals = await run_sync(cid)
    assert totals["new"] == 2 and totals["failed"] == 1
    assert await msg_count(user.id) == 2
    st = await _state(cid)
    assert st["done"] is True and st["failed_count"] == 1
    assert st["failed"] == [{"at": "message 2 of archive.mbox", "error": "ValueError: unparseable header"}]
    assert "1 could not be imported" in st["summary"]
    assert not path.exists()  # finished: the upload no longer takes the user's import space


async def test_one_bad_message_in_a_maildir_zip_is_skipped(client, user, monkeypatch):
    root = imports_root(user.id)
    root.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i, subject in enumerate(("Invoice 1", "Broken two", "Invoice 3")):
            z.writestr(f"Archive/cur/{i}.mailr:2,S", msg(subject))
    (root / "export.zip").write_bytes(buf.getvalue())
    _broken_on(monkeypatch, {"Broken two"})
    cid = await _file_connector(client, "export.zip")
    totals = await run_sync(cid)
    assert totals["new"] == 2 and totals["failed"] == 1
    st = await _state(cid)
    assert st["done"] is True and st["failed"][0]["at"] == "Archive/cur/1.mailr:2,S"


async def test_many_failures_in_a_row_stop_the_run(client, user, monkeypatch):
    monkeypatch.setattr(mbox_mod, "FAILED_IN_A_ROW", 3)
    root = imports_root(user.id)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "down.mbox"
    box = mailbox.mbox(str(path))
    for i in range(5):
        box.add(mailbox.mboxMessage(msg(f"Broken {i}")))
    box.flush()
    box.close()
    _broken_on(monkeypatch, {f"Broken {i}" for i in range(5)})
    cid = await _file_connector(client, path.name)
    with pytest.raises(ValueError):
        await run_sync(cid)
    assert not (await _state(cid)).get("done")
    assert path.exists()  # the runner starts it again later
