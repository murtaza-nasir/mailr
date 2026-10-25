"""Attachments for agents: list_attachments and get_attachment (agent/attachment_tools.py,
core/attachfetch.py, core/attachwork.py) and the MCP image block (mcp/server.py)."""

import base64
import io
import uuid

import pytest
from helpers.agent import _ingest, _setup_identity
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select

from mailr.agent.tools import ToolCtx, ToolError, run_tool
from mailr.core import attachfetch, publicid
from mailr.core.auth import ALL_SCOPES, Principal
from mailr.db import sessionmaker
from mailr.models import AuditLog, User


def _mail(files, subject="Files", body="See attached."):
    """A multipart message; files are (name, content type, bytes)."""
    b = "b0"
    parts = [f"--{b}\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{body}\r\n"]
    for name, ctype, data in files:
        enc = base64.encodebytes(data).decode().replace("\n", "\r\n")
        parts.append(f"--{b}\r\nContent-Type: {ctype}; name=\"{name}\"\r\nContent-Disposition: attachment; "
                     f"filename=\"{name}\"\r\nContent-Transfer-Encoding: base64\r\n\r\n{enc}\r\n")
    head = (f"From: Dana <dana@example.org>\r\nTo: me@example.com\r\nSubject: {subject}\r\n"
            f"Message-ID: <{uuid.uuid4().hex}@x.org>\r\nDate: Mon, 21 Sep 2026 10:00:00 +0000\r\n"
            f"MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=\"{b}\"\r\n\r\n")
    return (head + "".join(parts) + f"--{b}--\r\n").encode()


def _photo() -> bytes:
    """A 3000×2000 JPEG, red left half and blue right half, stored sideways (EXIF orientation 6)
    with a camera make and GPS position."""
    im = Image.new("RGB", (3000, 2000), (0, 0, 255))
    im.paste((255, 0, 0), (0, 0, 1500, 2000))
    exif = Image.Exif()
    exif[0x0112] = 6
    exif[0x010F] = "SecretCam"
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2] = "N", (51.0, 30.0, 12.0)
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif, quality=90)
    return buf.getvalue()


def _png_alpha() -> bytes:
    im = Image.new("RGBA", (400, 300), (0, 128, 0, 255))
    im.paste((0, 0, 0, 0), (0, 0, 200, 300))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _pdf(pages: list[str]) -> bytes:
    """A small valid PDF with one line of Helvetica text per page."""
    objs: list[bytes] = []
    n = len(pages)
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    font = 3 + 2 * n
    for i, t in enumerate(pages):
        stream = f"BT /F1 24 Tf 72 700 Td ({t}) Tj ET".encode()
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + 2 * i} 0 R "
                    f"/Resources << /Font << /F1 {font} 0 R >> >> >>".encode())
        objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def _pid(mid: int) -> int:
    return publicid.encode("message", mid) if publicid.enabled() else mid


async def _ctx(db, user_id, *, token=None, assistant=False, images=False, **kw):
    u = await db.get(User, user_id)
    p = Principal(user=u, token=token, scopes=set(ALL_SCOPES) - {"admin"})
    return ToolCtx(db=db, p=p, assistant=assistant, images=images,
                   chat_session_id=f"s-{uuid.uuid4().hex[:6]}" if assistant else None, **kw)


async def _call(user_id, name, args, **kw):
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user_id, **kw)
        res = await run_tool(ctx, name, args)
        await db.commit()
        return res


@pytest.fixture(autouse=True)
def _no_rate_limit():
    attachfetch._CALLS.clear()
    yield
    attachfetch._CALLS.clear()


async def test_list_and_message_body_index_attachments(user):
    mid, conv = await _ingest(user.id, _mail([("a.txt", "text/plain", b"one"), ("b.png", "image/png", _png_alpha())]))
    res = await _call(user.id, "list_attachments", {"conversation_id": conv})
    items = res.data["items"]
    assert [(i["index"], i["filename"], i["type"], i["inline"]) for i in items] == [
        (0, "a.txt", "text/plain", False), (1, "b.png", "image/png", False)]
    assert items[0]["size"] == 3 and items[0]["message_id"] == mid
    body = await _call(user.id, "get_message_body", {"message_id": mid})
    assert body.data["attachments"]["untrusted"] is True  # file names are the sender's text
    assert [a["filename"] for a in body.data["attachments"]["content"]] == ["a.txt", "b.png"]
    with pytest.raises(ToolError):
        await _call(user.id, "list_attachments", {})


async def test_image_is_upright_downscaled_and_stripped(user):
    mid, _ = await _ingest(user.id, _mail([("IMG_0001.jpg", "image/jpeg", _photo())]))
    res = await _call(user.id, "get_attachment", {"message_id": mid, "attachment": 0}, images=True)
    d = res.data
    assert d["kind"] == "image" and d["attachment"]["filename"] == "IMG_0001.jpg"
    # Orientation 6 turns the 3000×2000 landscape upright: portrait, long side 1600.
    assert (d["image"]["width"], d["image"]["height"]) == (1067, 1600)
    assert (d["image"]["original_width"], d["image"]["original_height"]) == (3000, 2000)
    block = d["_content"][0]
    assert block["type"] == "image" and block["mimeType"] == "image/jpeg"
    out = base64.b64decode(block["data"])
    assert b"SecretCam" not in out and b"Exif" not in out
    im = Image.open(io.BytesIO(out))
    assert im.format == "JPEG" and not dict(im.getexif()) and im.size == (1067, 1600)
    top, bottom = im.getpixel((533, 20)), im.getpixel((533, 1580))
    assert top[0] > 200 and top[2] < 60 and bottom[2] > 200 and bottom[0] < 60  # red went to the top
    # By file name, with max_px clamped to 256.
    small = await _call(user.id, "get_attachment", {"message_id": mid, "attachment": "img_0001.JPG", "max_px": 10},
                        images=True)
    assert max(small.data["image"]["width"], small.data["image"]["height"]) == 256


async def test_transparent_png_stays_png(user):
    mid, _ = await _ingest(user.id, _mail([("shot.png", "application/octet-stream", _png_alpha())]))
    d = (await _call(user.id, "get_attachment", {"message_id": mid, "attachment": "shot.png"}, images=True)).data
    assert d["_content"][0]["mimeType"] == "image/png" and (d["image"]["width"], d["image"]["height"]) == (400, 300)
    im = Image.open(io.BytesIO(base64.b64decode(d["_content"][0]["data"])))
    assert im.mode == "RGBA" and im.getpixel((10, 10))[3] == 0


async def test_builtin_assistant_gets_a_stub_not_the_image(user):
    mid, _ = await _ingest(user.id, _mail([("p.jpg", "image/jpeg", _photo())]))
    d = (await _call(user.id, "get_attachment", {"message_id": mid, "attachment": 0}, assistant=True)).data
    assert "_content" not in d and "data" not in d["image"]
    assert d["image"]["note"].startswith("image attached (1067×1600)")


async def test_text_like_files(user):
    csv = "name,amount\nRené,12\n".encode("latin-1")
    html = b"<html><body><h1>Agenda</h1><p>Item <b>one</b></p><script>x()</script></body></html>"
    inner = (b"From: Bob <bob@example.org>\r\nTo: me@example.com\r\nSubject: Original\r\n"
             b"Date: Mon, 21 Sep 2026 09:00:00 +0000\r\n\r\nThe forwarded words.\r\n")
    big = b"x" * 250_000
    mid, _ = await _ingest(user.id, _mail([("t.csv", "text/csv; charset=iso-8859-1", csv),
                                           ("agenda.html", "text/html", html),
                                           ("fwd.eml", "application/octet-stream", inner),
                                           ("big.txt", "text/plain", big)]))
    r = (await _call(user.id, "get_attachment", {"message_id": mid, "attachment": 0})).data
    assert r["kind"] == "text" and r["text"] == {"untrusted": True, "content": "name,amount\nRené,12\n"}
    h = (await _call(user.id, "get_attachment", {"message_id": mid, "attachment": "agenda.html"})).data
    assert "Agenda" in h["text"]["content"] and "one" in h["text"]["content"] and "<b>" not in h["text"]["content"]
    e = (await _call(user.id, "get_attachment", {"message_id": mid, "attachment": "fwd.eml"})).data
    assert "Subject: Original" in e["text"]["content"] and "The forwarded words." in e["text"]["content"]
    b = (await _call(user.id, "get_attachment", {"message_id": mid, "attachment": 3})).data
    assert b["truncated"] is True and len(b["text"]["content"]) == attachfetch.TEXT_CAP and "truncated" in b["note"]


async def test_pdf_text_and_rendered_page(user):
    mid, _ = await _ingest(user.id, _mail([("report.pdf", "application/pdf", _pdf(["Alpha page", "Beta page"]))]))
    d = (await _call(user.id, "get_attachment", {"message_id": mid, "attachment": 0}, images=True)).data
    assert d["kind"] == "pdf" and d["pages"] == 2
    assert "Alpha page" in d["text"]["content"] and "Beta page" in d["text"]["content"]
    assert d["image"]["page"] == 1 and max(d["image"]["width"], d["image"]["height"]) == 1600
    assert d["_content"][0]["mimeType"] == "image/jpeg"
    p2 = (await _call(user.id, "get_attachment", {"message_id": mid, "attachment": 0, "page": 2}, images=True)).data
    assert "Beta page" in p2["text"]["content"] and "Alpha" not in p2["text"]["content"] and p2["image"]["page"] == 2
    # The built-in assistant gets the text and no rendered page.
    a = (await _call(user.id, "get_attachment", {"message_id": mid, "attachment": 0}, assistant=True)).data
    assert "Alpha page" in a["text"]["content"] and "image" not in a and "_content" not in a
    with pytest.raises(ToolError, match="2 page"):
        await _call(user.id, "get_attachment", {"message_id": mid, "attachment": 0, "page": 5})


async def test_unknown_heic_broken_and_too_large(user, monkeypatch):
    heic = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64
    xlsx = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    mid, _ = await _ingest(user.id, _mail([("data.xlsx", xlsx, b"PK\x03\x04zzzz"),
                                           ("phone.heic", "application/octet-stream", heic),
                                           ("broken.jpg", "image/jpeg", b"\xff\xd8\xff not a jpeg"),
                                           ("big.bin", "application/octet-stream", b"y" * 5000)]))
    x = (await _call(user.id, "get_attachment", {"message_id": mid, "attachment": "data.xlsx"})).data
    assert x["kind"] == "other" and "cannot be returned" in x["note"] and x["attachment"]["size"] == 8
    assert "text" not in x and "image" not in x
    h = (await _call(user.id, "get_attachment", {"message_id": mid, "attachment": 1}, images=True)).data
    assert h["kind"] == "other" and "HEIC" in h["note"]
    with pytest.raises(ToolError, match="could not be decoded"):
        await _call(user.id, "get_attachment", {"message_id": mid, "attachment": 2}, images=True)
    with pytest.raises(ToolError, match="no attachment"):
        await _call(user.id, "get_attachment", {"message_id": mid, "attachment": 9})
    monkeypatch.setattr(attachfetch, "MAX_ORIGINAL", 4096)
    with pytest.raises(ToolError, match="are not returned"):
        await _call(user.id, "get_attachment", {"message_id": mid, "attachment": "big.bin"})


async def test_visibility_follows_the_message(client, user):
    from helpers.security_authz import _token

    local_conn, _ = await _setup_identity(user, data_class="local_only")
    normal_conn, _ = await _setup_identity(user)
    lo, _ = await _ingest(user.id, _mail([("secret.txt", "text/plain", b"FERPA")]), connector_id=local_conn)
    ok, _ = await _ingest(user.id, _mail([("plain.txt", "text/plain", b"fine")]), connector_id=normal_conn)

    async def fetch(c: AsyncClient, mid: int):
        return await c.post("/api/v1/agent/tools/get_attachment", json={"message_id": _pid(mid), "attachment": 0})

    hosted = await _token(client, ["read"], local_only=False)
    assert (await fetch(hosted, ok)).json()["result"]["text"]["content"] == "fine"
    r = await fetch(hosted, lo)
    assert r.status_code == 400 and "not found" in r.json()["error"]
    r = await hosted.post("/api/v1/agent/tools/list_attachments", json={"message_id": _pid(lo)})
    assert r.status_code == 400
    local = await _token(client, ["read"], local_only=True, name="local")
    r = await fetch(local, lo)
    assert r.status_code == 200 and r.json()["result"]["text"]["content"] == "FERPA"
    assert r.headers["X-Mailr-Content"] == "untrusted"
    narrow = await _token(client, ["read"], local_only=True, connector_ids=[local_conn], name="narrow")
    assert (await fetch(narrow, ok)).status_code == 400
    assert (await fetch(narrow, lo)).status_code == 200


async def test_builtin_assistant_local_only_needs_a_local_model(user, monkeypatch):
    from mailr.intel import llm

    conn, _ = await _setup_identity(user, data_class="local_only")
    mid, _ = await _ingest(user.id, _mail([("grades.txt", "text/plain", b"FERPA")]), connector_id=conn)

    async def no_local(*a, sensitive=False, **k):
        return not sensitive

    monkeypatch.setattr(llm, "available", no_local)
    with pytest.raises(ToolError, match="local-only"):
        await _call(user.id, "get_attachment", {"message_id": mid, "attachment": 0}, assistant=True)

    async def has_local(*a, **k):
        return True

    monkeypatch.setattr(llm, "available", has_local)
    res = await _call(user.id, "get_attachment", {"message_id": mid, "attachment": 0}, assistant=True)
    assert res.data["text"]["content"] == "FERPA" and res.message_ids == [mid]  # marks the chat sensitive


async def test_every_fetch_is_audited(user):
    mid, _ = await _ingest(user.id, _mail([("a.txt", "text/plain", b"hello")]))
    await _call(user.id, "get_attachment", {"message_id": mid, "attachment": 0})
    async with sessionmaker()() as db:
        rows = list((await db.execute(select(AuditLog).where(
            AuditLog.user_id == user.id, AuditLog.action == "agent.get_attachment"))).scalars())
    assert len(rows) == 1 and rows[0].target == f"{mid}/0"
    assert rows[0].result["kind"] == "text" and rows[0].result["bytes_in"] == 5


def test_rate_limit():
    for i in range(attachfetch.RATE_LIMIT):
        attachfetch.check_rate("t1", now=1000.0 + i)
    with pytest.raises(attachfetch.AttachmentError, match="too many"):
        attachfetch.check_rate("t1", now=1100.0)
    attachfetch.check_rate("t2", now=1100.0)  # per caller
    attachfetch.check_rate("t1", now=1000.0 + attachfetch.RATE_WINDOW + 1)  # the window moves


def test_kind_of():
    k = attachfetch.kind_of
    assert k("application/octet-stream", "x.bin", b"%PDF-1.7") == "pdf"
    assert k("application/octet-stream", "scan.JPG") == "image"
    assert k("image/svg+xml", "logo.svg") == "other"
    assert k("text/calendar", "invite.ics") == "text"
    assert k("application/zip", "a.zip") == "other"


def test_mcp_content_result_translation():
    from mailr.mcp.server import content_result

    r = content_result({"kind": "image", "image": {"width": 2}, "_content": [
        {"type": "image", "data": "AAAA", "mimeType": "image/jpeg"}, {"type": "text", "text": "hi"},
        {"type": "image", "data": "AAAA", "mimeType": "image/svg+xml"}, "junk"]})
    assert [c.type for c in r.content] == ["text", "image", "text"]
    assert r.content[1].data == "AAAA" and r.content[1].mime_type == "image/jpeg"
    assert "_content" not in r.content[0].text and r.structured_content == {"kind": "image", "image": {"width": 2}}


async def test_mcp_returns_an_image_block(client, user):
    from mcp import Client

    from mailr.mcp.server import build_server

    mid, _ = await _ingest(user.id, _mail([("p.jpg", "image/jpeg", _photo()), ("n.txt", "text/plain", b"note")]))
    tok = (await client.post("/api/v1/tokens", json={"name": "mcp", "scopes": ["read"], "local_only": True})).json()["token"]
    server = build_server(url="http://test", token=tok, transport=client._transport)
    async with Client(server) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        assert tools["get_attachment"].annotations.read_only_hint
        res = await c.call_tool("get_attachment", {"message_id": _pid(mid), "attachment": 0})
        assert [b.type for b in res.content] == ["text", "image"]
        img = res.content[1]
        assert img.mime_type == "image/jpeg"
        assert Image.open(io.BytesIO(base64.b64decode(img.data))).size == (1067, 1600)
        assert '"_content"' not in res.content[0].text and '"kind": "image"' in res.content[0].text
        txt = await c.call_tool("get_attachment", {"message_id": _pid(mid), "attachment": "n.txt"})
        assert [b.type for b in txt.content] == ["text"] and '"untrusted": true' in txt.content[0].text

