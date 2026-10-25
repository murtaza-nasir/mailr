"""A message's inline images are parsed once for a burst of cid: requests (a short cache),
off the event loop; each request is still authorised."""

from helpers.audit_fixes import _ingest

from mailr.core import mime


async def test_twenty_cid_images_parse_once(client, user, monkeypatch):
    parts = b"".join(
        b"--B\r\nContent-Type: image/png\r\nContent-ID: <i%d>\r\nContent-Transfer-Encoding: base64\r\n\r\n"
        b"iVBORw0KGgo=\r\n" % i for i in range(20))
    raw = (b"From: a@b.c\r\nTo: me@x.org\r\nSubject: pics\r\nMessage-ID: <pics1@x.org>\r\nMIME-Version: 1.0\r\n"
           b"Content-Type: multipart/related; boundary=B\r\n\r\n--B\r\nContent-Type: text/html\r\n\r\n"
           b"<img src=\"cid:i0\">\r\n" + parts + b"--B--\r\n")
    mid, _, _ = await _ingest(user, raw)
    calls = []
    real = mime.inline_images
    monkeypatch.setattr(mime, "inline_images", lambda r: calls.append(1) or real(r))
    for i in range(20):
        r = await client.get(f"/api/v1/messages/{mid}/cid/i{i}")
        assert r.status_code == 200, (i, r.text)
    assert len(calls) == 1
    # Another user's request is refused before the cache is consulted.
    assert (await client.get(f"/api/v1/messages/{mid + 10_000_000}/cid/i0")).status_code == 404


async def test_export_writes_and_fsyncs_off_the_event_loop(user, raw, monkeypatch):
    """The mail export wrote and fsynced each file on the event loop."""
    import threading

    from mailr.core import backup

    await _ingest(user, raw("Export me", msgid="<exp-f30@x.org>"))
    threads = []
    real = backup._fsync_file
    monkeypatch.setattr(backup, "_fsync_file", lambda p: threads.append(threading.current_thread()) or real(p))
    assert await backup.export_user(user.id) >= 1
    assert threads and all(t is not threading.main_thread() for t in threads)
