"""Uploads are authenticated before their body is read, large ones stream to their destination with
their limits checked while they are written, and only a few run at once (core/uploadgate.py,
core/streamupload.py).

Each upload here sends its body from a generator that counts what the server asked for: a refused
request reads none of it, and an upload over its quota stops soon after the limit.
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from mailr.config import get_settings

BOUNDARY = "test-boundary-0d1e2f"
CHUNK = 64 * 1024


@pytest.fixture
def spool_meter(monkeypatch):
    """The bytes Starlette's multipart parser writes to its spool files (TMPDIR)."""
    import starlette.formparsers as fp

    written = {"bytes": 0}
    real = fp.SpooledTemporaryFile

    class Metered(real):  # type: ignore[misc, valid-type]
        def write(self, b):
            written["bytes"] += len(b)
            return super().write(b)

    monkeypatch.setattr(fp, "SpooledTemporaryFile", Metered)
    return written


@pytest.fixture
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "data_dir", str(tmp_path))
    return tmp_path


class Body:
    """A multipart body with one file of `size` bytes, sent in chunks; `sent` counts the bytes the
    server read."""

    def __init__(self, size: int, filename: str = "a.mbox", fields: dict[str, str] | None = None,
                 hold: asyncio.Event | None = None):
        self.size, self.filename, self.fields, self.hold = size, filename, fields or {}, hold
        self.sent = 0

    headers = {"Content-Type": f"multipart/form-data; boundary={BOUNDARY}"}

    async def __aiter__(self):
        head = "".join(f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'
                       for k, v in self.fields.items())
        head += (f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="file"; filename="{self.filename}"\r\n'
                 "Content-Type: application/octet-stream\r\n\r\n")
        self.sent += len(head)
        yield head.encode()
        if self.hold is not None:
            await self.hold.wait()
        left = self.size
        while left > 0:
            n = min(CHUNK, left)
            left -= n
            self.sent += n
            yield b"y" * n
        tail = f"\r\n--{BOUNDARY}--\r\n".encode()
        self.sent += len(tail)
        yield tail


ROUTES = [("POST", "/api/v1/connectors/uploads"), ("POST", "/api/v1/speakr/recordings"),
          ("POST", "/api/v1/admin/phone-apps/mailr"), ("POST", "/api/v1/uploads"), ("PUT", "/api/v1/me/picture"),
          ("POST", "/api/v1/assistant/transcribe"), ("POST", "/api/v1/rules/import-gmail")]


async def _send(c: AsyncClient, method: str, path: str, body: Body, headers: dict | None = None):
    return await c.request(method, path, content=body, headers={**Body.headers, **(headers or {})})


async def test_a_signed_out_upload_is_refused_before_its_body_is_read(app, spool_meter, data_dir):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        for method, path in ROUTES:
            body = Body(8 * 1024 * 1024)
            r = await _send(anon, method, path, body)
            assert r.status_code == 401, (path, r.text)
            assert body.sent == 0, path
        # A bearer token that is not one, too.
        body = Body(8 * 1024 * 1024)
        r = await _send(anon, "POST", "/api/v1/connectors/uploads", body, {"Authorization": "Bearer mlr_nothing"})
        assert r.status_code == 401 and body.sent == 0
    assert spool_meter["bytes"] == 0


async def test_a_session_without_csrf_and_a_member_on_admin_routes_read_nothing(app, client, user, spool_meter):
    csrf = client.headers.pop("X-CSRF-Token")
    try:
        body = Body(4 * 1024 * 1024)
        r = await _send(client, "POST", "/api/v1/uploads", body)
        assert r.status_code == 403 and body.sent == 0
    finally:
        client.headers["X-CSRF-Token"] = csrf
    body = Body(4 * 1024 * 1024, "a.apk")
    r = await _send(client, "POST", "/api/v1/admin/phone-apps/mailr", body)
    assert r.status_code == 403 and r.json()["code"] == "auth.admin_only" and body.sent == 0
    # A token cannot publish phone apps either.
    tok = (await client.post("/api/v1/tokens", json={"name": "t", "scopes": ["read"]})).json()["token"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as t:
        body = Body(4 * 1024 * 1024, "a.apk")
        r = await _send(t, "POST", "/api/v1/admin/phone-apps/mailr", body, {"Authorization": f"Bearer {tok}"})
        assert r.status_code == 403 and body.sent == 0
    assert spool_meter["bytes"] == 0


async def test_an_import_over_the_quota_is_cut_off_while_it_streams(client, user, data_dir, spool_meter, monkeypatch):
    from mailr.connectors import mbox as mb

    monkeypatch.setattr(mb, "import_quota_bytes", lambda: 1024 * 1024)
    body = Body(40 * 1024 * 1024)
    r = await _send(client, "POST", "/api/v1/connectors/uploads", body)
    assert r.status_code == 413 and r.json()["code"] == "accounts.import_space_full", r.text
    assert body.sent < 2 * 1024 * 1024  # stopped at the quota, not after the whole body
    assert [f.name for f in mb.imports_root(user.id).iterdir()] == [mb.UPLOAD_LEDGER]
    assert spool_meter["bytes"] == 0  # streamed, never spooled
    # Within the quota it is written straight to the import folder.
    body = Body(300 * 1024)
    r = await _send(client, "POST", "/api/v1/connectors/uploads", body)
    assert r.status_code == 200, r.text
    assert (mb.imports_root(user.id) / r.json()["path"]).stat().st_size == 300 * 1024
    assert r.json()["filename"] == "a.mbox" and spool_meter["bytes"] == 0


async def test_an_import_with_the_wrong_name_or_no_file_is_refused(client, user, data_dir):
    from mailr.connectors import mbox as mb

    body = Body(1024 * 1024, "notes.txt")
    r = await _send(client, "POST", "/api/v1/connectors/uploads", body)
    assert r.status_code == 400 and r.json()["code"] == "accounts.upload_mbox"
    assert body.sent < 256 * 1024
    r = await client.post("/api/v1/connectors/uploads", data={"other": "x"},
                          files={"x": ("a.txt", b"abc", "text/plain")})
    assert r.status_code == 400
    r = await client.post("/api/v1/connectors/uploads", content=b"abc", headers={"Content-Type": "text/plain"})
    assert r.status_code == 400
    root = mb.imports_root(user.id)
    assert not root.exists() or [f.name for f in root.iterdir() if f.name != mb.UPLOAD_LEDGER] == []


async def test_a_recording_over_the_limit_is_cut_off_and_leaves_no_file(client, user, monkeypatch, tmp_path):
    import tempfile

    monkeypatch.setenv("MAILR_SPEAKR_MAX_UPLOAD_MB", "1")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    body = Body(20 * 1024 * 1024, "talk.mp3", {"title": "Weekly"})
    r = await _send(client, "POST", "/api/v1/speakr/recordings", body)
    assert r.status_code == 413 and r.json()["code"] == "speakr.recording_too_large", r.text
    assert body.sent < 2 * 1024 * 1024
    assert list(tmp_path.iterdir()) == []


async def test_uploads_at_once_are_capped_per_user(app, client, user, data_dir, monkeypatch):
    from mailr.core import uploadgate

    monkeypatch.setattr(uploadgate, "UPLOADS_PER_USER", 1)
    monkeypatch.setattr(uploadgate, "SLOT_WAIT", 0.3)
    monkeypatch.setattr(uploadgate, "_slots", None)
    hold = asyncio.Event()
    first_body = Body(64 * 1024, hold=hold)
    first = asyncio.create_task(_send(client, "POST", "/api/v1/connectors/uploads", first_body))
    for _ in range(100):
        if first_body.sent:
            break
        await asyncio.sleep(0.02)
    second = Body(64 * 1024)
    r = await _send(client, "POST", "/api/v1/connectors/uploads", second)
    assert r.status_code == 429 and r.json()["code"] == "common.uploads_busy"
    assert second.sent == 0
    hold.set()
    assert (await first).status_code == 200
    # The slot is free again.
    assert (await _send(client, "POST", "/api/v1/connectors/uploads", Body(1024))).status_code == 200
    assert uploadgate.slots().total == 0


async def test_requests_that_are_not_uploads_pass_untouched(app, client):
    # JSON posts and reads are not gated (their own dependencies authenticate them).
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        assert (await anon.post("/api/v1/auth/login", json={"email": "nobody@example.org", "password": "x"})).status_code == 401
    assert (await client.get("/api/v1/me")).status_code == 200
