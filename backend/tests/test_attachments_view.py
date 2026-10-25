"""The attachments view: GET /attachments lists files across the mailbox, newest first."""

from conftest import mail_date
from helpers.core_flow import _ingest


def _with_files(subject, date, files, frm="Dana <dana@example.org>"):
    b = "b0"
    parts = [f"--{b}\r\nContent-Type: text/plain\r\n\r\nSee attached.\r\n"]
    for name, ctype in files:
        parts.append(f"--{b}\r\nContent-Type: {ctype}; name=\"{name}\"\r\nContent-Disposition: attachment; "
                     f"filename=\"{name}\"\r\nContent-Transfer-Encoding: base64\r\n\r\naGVsbG8=\r\n")
    head = (f"From: {frm}\r\nTo: me@example.com\r\nSubject: {subject}\r\nMessage-ID: <{abs(hash(subject))}@x.org>\r\n"
            f"Date: {date}\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=\"{b}\"\r\n\r\n")
    return (head + "".join(parts) + f"--{b}--\r\n").encode()


async def test_lists_files_newest_first_with_filters(client, user):
    await _ingest(user, _with_files("Invoice", mail_date(),
                                    [("invoice-0921.pdf", "application/pdf"), ("logo.png", "image/png")]), roles=["inbox"])
    await _ingest(user, _with_files("Budget", mail_date(1),
                                    [("budget.xlsx", "application/octet-stream")], frm="Bob <bob@example.org>"), roles=["inbox"])
    got = (await client.get("/api/v1/attachments")).json()
    assert [i["filename"] for i in got["items"]] == ["budget.xlsx", "invoice-0921.pdf", "logo.png"]
    assert got["next_offset"] is None and got["items"][0]["from_email"] == "bob@example.org"
    assert [i["filename"] for i in (await client.get("/api/v1/attachments", params={"kind": "spreadsheet"})).json()["items"]] == ["budget.xlsx"]
    assert [i["filename"] for i in (await client.get("/api/v1/attachments", params={"kind": "image"})).json()["items"]] == ["logo.png"]
    assert [i["filename"] for i in (await client.get("/api/v1/attachments", params={"name": "invoice"})).json()["items"]] == ["invoice-0921.pdf"]
    assert [i["filename"] for i in (await client.get("/api/v1/attachments", params={"q": "from:bob@example.org"})).json()["items"]] == ["budget.xlsx"]
    page = (await client.get("/api/v1/attachments", params={"limit": 2})).json()
    assert len(page["items"]) == 2 and page["next_offset"] == 2
    it = got["items"][1]
    r = await client.get(f"/api/v1/messages/{it['message_id']}/attachments/{it['part']}")
    assert r.status_code == 200 and r.content == b"hello"
    assert (await client.get("/api/v1/attachments", params={"kind": "nope"})).status_code == 400


async def test_cursor_pages_through_every_file_once(client, user):
    """A keyset cursor (date, message, position) instead of OFFSET over the exploded join."""
    files = [(f"f{i}.pdf", "application/pdf") for i in range(3)]
    for day in (21, 22, 23):
        await _ingest(user, _with_files(f"Batch {day}", f"Mon, {day} Sep 2026 10:00:00 +0000", files), roles=["inbox"])
    seen, cursor = [], None
    for _ in range(10):
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        got = (await client.get("/api/v1/attachments", params=params)).json()
        seen += [(i["subject"], i["filename"]) for i in got["items"]]
        cursor = got["next_cursor"]
        if not cursor:
            break
    assert seen == [(f"Batch {d}", f"f{i}.pdf") for d in (23, 22, 21) for i in range(3)]
    assert (await client.get("/api/v1/attachments", params={"cursor": "nope"})).status_code == 400


async def test_thumb_is_a_small_copy_of_a_large_image():
    """?thumb=1 (the grid's cards): a large image comes back as a small copy; a small one as it is."""
    import io
    import os

    from PIL import Image

    from mailr.core.attachfetch import THUMB_PX, thumbnail

    im = Image.frombytes("RGB", (1200, 900), os.urandom(1200 * 900 * 3))  # noise: a big PNG
    buf = io.BytesIO()
    im.save(buf, "PNG")
    small = await thumbnail(buf.getvalue())
    assert small is not None and small[1] == "image/jpeg" and len(small[0]) < buf.tell()
    assert max(Image.open(io.BytesIO(small[0])).size) == THUMB_PX
    assert await thumbnail(b"tiny") is None
