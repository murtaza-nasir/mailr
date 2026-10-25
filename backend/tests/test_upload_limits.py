"""Per-user limits on uploads: one attachment, all of a user's pending attachments, import bytes per
day, file imports running at once, and the free space an import upload leaves on the disk."""

import os
import time

import pytest

from mailr.config import get_settings
from mailr.connectors import mbox as mb
from mailr.models import Connector


@pytest.fixture
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "data_dir", str(tmp_path))
    return tmp_path


async def _attach(client, data: bytes, name="a.bin"):
    return await client.post("/api/v1/uploads", files={"file": (name, data, "application/octet-stream")})


async def test_one_attachment_is_limited(client, data_dir, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_attachment_bytes", 3 * 1024 * 1024)
    assert (await _attach(client, b"a" * 3 * 1024 * 1024)).status_code == 200
    r = await _attach(client, b"b" * (3 * 1024 * 1024 + 1))
    assert r.status_code == 413
    assert r.json()["code"] == "mail.attachments_limit" and r.json()["params"] == {"max": 3}
    assert r.json()["detail"] == "attachments are limited to 3 MB"


def test_the_attachment_route_has_the_attachment_limit(monkeypatch):
    from mailr.main import body_limit

    monkeypatch.setattr(get_settings(), "max_attachment_bytes", 100 * 1024 * 1024)
    assert body_limit("POST", "/api/v1/uploads") == 101 * 1024 * 1024


async def test_pending_attachments_are_limited_and_unused_ones_make_room(client, user, data_dir, monkeypatch):
    from mailr.api.mail_write import upload_dir

    monkeypatch.setattr(get_settings(), "uploads_quota_bytes", 2500)
    a = (await _attach(client, b"a" * 1000)).json()["blob"]
    assert (await _attach(client, b"b" * 1000)).status_code == 200
    # The same file again takes no more room.
    assert (await _attach(client, b"a" * 1000)).json()["blob"] == a
    r = await _attach(client, b"c" * 1000)
    assert r.status_code == 413 and r.json()["code"] == "mail.uploads_full"
    assert r.json()["params"] == {"max": 1}
    # Uploads no draft uses, older than an hour, are removed to make room.
    old = time.time() - 2 * 3600
    for f in upload_dir(user.id).iterdir():
        os.utime(f, (old, old))
    assert (await _attach(client, b"c" * 1000)).status_code == 200
    assert sorted(f.name for f in upload_dir(user.id).iterdir() if not f.name.endswith(".json")) == [
        (await _attach(client, b"c" * 1000)).json()["blob"]]


async def _import_upload(client, size: int):
    return await client.post("/api/v1/connectors/uploads", files={"file": ("a.mbox", b"y" * size)})


async def test_import_bytes_per_day(client, user, data_dir, monkeypatch):
    monkeypatch.setattr(get_settings(), "import_bytes_per_day", 1000)
    assert (await _import_upload(client, 600)).status_code == 200
    r = await _import_upload(client, 600)
    assert r.status_code == 429 and r.json()["code"] == "accounts.import_daily_limit"
    # Refused uploads still count what they sent; nothing of them stays.
    assert mb.import_bytes_today(user.id) == 1200
    assert len([f for f in mb.imports_root(user.id).iterdir() if f.name != mb.UPLOAD_LEDGER]) == 1
    assert (await _import_upload(client, 10)).status_code == 429
    # Uploads older than a day no longer count.
    ledger = mb.imports_root(user.id) / mb.UPLOAD_LEDGER
    ledger.write_text("".join(f"{time.time() - 90000:.0f} {line.split()[1]}\n"
                              for line in ledger.read_text().splitlines()))
    assert mb.import_bytes_today(user.id) == 0
    assert (await _import_upload(client, 600)).status_code == 200
    # 0 turns the limit off.
    monkeypatch.setattr(get_settings(), "import_bytes_per_day", 0)
    assert (await _import_upload(client, 600)).status_code == 200


async def test_an_import_upload_stops_before_the_disk_fills(client, user, data_dir, monkeypatch):
    import shutil

    from mailr.core import streamupload

    total = 100 * 1024**3

    class Usage:
        def __init__(self, free):
            self.total, self.used, self.free = total, total - free, free

    monkeypatch.setattr(get_settings(), "disk_fail_percent", 5.0)
    monkeypatch.setattr(streamupload.shutil, "disk_usage", lambda p: Usage(int(total * 0.05) + 100))
    r = await _import_upload(client, 600)
    assert r.status_code == 507 and r.json()["code"] == "accounts.import_disk_full"
    assert [f.name for f in mb.imports_root(user.id).iterdir()] == [mb.UPLOAD_LEDGER]
    monkeypatch.setattr(streamupload.shutil, "disk_usage", lambda p: Usage(total // 2))
    assert (await _import_upload(client, 600)).status_code == 200
    assert shutil.disk_usage  # the module's own function is untouched


async def test_file_imports_running_at_once(client, user, data_dir, monkeypatch):
    from mailr.db import session_scope

    monkeypatch.setattr(get_settings(), "max_concurrent_imports", 1)
    async with session_scope() as db:
        running = Connector(user_id=user.id, kind="file", name="Old", mode="import", config={"path": "x"},
                            state={}, enabled=True, status="syncing")
        db.add(running)
        await db.flush()
        cid = running.id
    path = (await _import_upload(client, 100)).json()["path"]
    body = {"kind": "file", "name": "More", "config": {"path": path}}
    r = await client.post("/api/v1/connectors", json=body)
    assert r.status_code == 429 and r.json()["code"] == "accounts.imports_running"
    assert r.json()["params"] == {"max": 1}
    # A finished import no longer counts.
    async with session_scope() as db:
        (await db.get(Connector, cid)).state = {"done": True}
    r = await client.post("/api/v1/connectors", json=body)
    assert r.status_code == 200, r.text
