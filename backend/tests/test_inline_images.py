# ACT-SURFACES: api
"""Inline images in compose (mail.insert_image).

In the editor HTML an inline image is <img src="/api/v1/uploads/<blob>">. Each one is sent as a
Content-ID part under multipart/related, a draft has the same cid from save to save, and the
cid: images of the sent message are shown in the reader. Width, height and cid: sources survive
sanitising; SVG and javascript: sources do not.
"""

import base64
import email
import email.policy
import re

from helpers.agent import _setup_identity
from helpers.core_flow import _ingest

from mailr.core.compose import build_message, clean_html, content_id, plain_text
from mailr.core.mime import inline_images, parse
from mailr.core.render import render_body

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
BLOB = "a" * 32


def _tree(msg) -> list[str]:
    """The MIME tree as indented content types."""
    out = []

    def walk(part, depth):
        out.append("  " * depth + part.get_content_type())
        if part.is_multipart():
            for sub in part.iter_parts():
                walk(sub, depth + 1)

    walk(email.message_from_bytes(msg.as_bytes(), policy=email.policy.default), 0)
    return out


def _html(blob=BLOB, alt="Q3 chart"):
    return (f'<p>See the chart:</p><p><img src="/api/v1/uploads/{blob}" alt="{alt}" width="320" height="180" '
            'style="max-width:100%;height:auto;display:block;margin-left:auto;margin-right:auto"></p>')


def test_mime_tree_with_inline_image_and_attachment():
    """mail.insert_image: mixed > related > alternative (text, html) + image, then the attachment."""
    msg = build_message({"to": [{"email": "b@example.org"}], "subject": "Chart", "html": _html()},
                        from_addr={"email": "me@example.com"},
                        attachments=[(b"%PDF-1.4", "application/pdf", "report.pdf")],
                        inline_loader=lambda b: (PNG, "image/png") if b == BLOB else None)
    assert _tree(msg) == [
        "multipart/mixed",
        "  multipart/related",
        "    multipart/alternative",
        "      text/plain",
        "      text/html",
        "    image/png",
        "  application/pdf",
    ]
    parsed = email.message_from_bytes(msg.as_bytes(), policy=email.policy.default)
    img = next(p for p in parsed.walk() if p.get_content_type() == "image/png")
    cid = content_id(BLOB, "example.com")
    assert img["Content-ID"] == f"<{cid}>" and img.get_content_disposition() == "inline"
    html = parsed.get_body(("html",)).get_content()
    assert f'src="cid:{cid}"' in html and 'width="320"' in html and 'height="180"' in html
    assert "/api/v1/uploads" not in html
    assert "[image: Q3 chart]" in parsed.get_body(("plain",)).get_content()
    # The image is not listed as an attachment; the PDF is.
    assert [a["filename"] for a in parse(msg.as_bytes()).attachments] == ["report.pdf"]


def test_mime_tree_inline_only_and_plain():
    inline = build_message({"to": [{"email": "b@example.org"}], "html": _html()}, from_addr={"email": "me@example.com"},
                           inline_loader=lambda b: (PNG, "image/png"))
    assert _tree(inline)[:2] == ["multipart/related", "  multipart/alternative"]
    plain = build_message({"to": [{"email": "b@example.org"}], "html": "<p>hi</p>"}, from_addr={"email": "me@example.com"})
    assert _tree(plain) == ["multipart/alternative", "  text/plain", "  text/html"]


def test_sent_message_renders_cid_images_in_the_reader():
    msg = build_message({"to": [{"email": "b@example.org"}], "html": _html()}, from_addr={"email": "me@example.com"},
                        inline_loader=lambda b: (PNG, "image/png"))
    body = render_body(msg.as_bytes(), 1)
    assert re.search(r'<img[^>]+src="data:image/png;base64,', body["html"])


def test_sanitiser_keeps_size_and_cid_and_drops_svg_and_javascript():
    out = clean_html(
        '<p style="text-align:center"><img src="cid:x@y" width="200" height="100" style="max-width:100%;'
        'background-image:url(https://evil.example/t.gif)" alt="ok"></p>'
        '<img src="data:image/svg+xml;base64,PHN2Zz4=" alt="svg">'
        '<img src="javascript:alert(1)" alt="js">'
        '<img src="/api/v1/other/thing" alt="rel">'
        '<a href="data:text/html,<script>alert(1)</script>">link</a>')
    assert 'src="cid:x@y"' in out and 'width="200"' in out and 'height="100"' in out
    assert "text-align:center" in out and "max-width:100%" in out
    assert "url(" not in out
    assert "svg+xml" not in out and "javascript:" not in out and "/api/v1/other" not in out
    assert "data:text/html" not in out


def test_svg_upload_never_becomes_an_inline_part():
    """An upload that is not a raster image (here SVG with a script) is dropped; its alt text stays."""
    msg = build_message({"to": [{"email": "b@example.org"}], "html": _html(alt="logo")},
                        from_addr={"email": "me@example.com"}, inline_loader=lambda b: (SVG, "image/png"))
    assert "image/svg+xml" not in _tree(msg) and not any("image/" in t for t in _tree(msg))
    parsed = email.message_from_bytes(msg.as_bytes(), policy=email.policy.default)
    html = parsed.get_body(("html",)).get_content()
    assert "<img" not in html and "logo" in html
    assert "svg" not in plain_text({"html": '<img src="data:image/svg+xml;base64,PHN2Zz4=">'}).lower()


async def test_upload_route_serves_raster_images_only(client, user):
    """mail.insert_image: the editor shows the uploaded image; an SVG upload is refused."""
    r = await client.post("/api/v1/uploads", files={"file": ("dot.png", PNG, "image/png")})
    blob = r.json()["blob"]
    got = await client.get(f"/api/v1/uploads/{blob}")
    assert got.status_code == 200 and got.headers["content-type"] == "image/png" and got.content == PNG
    assert got.headers["x-content-type-options"] == "nosniff" and "sandbox" in got.headers["content-security-policy"]
    r = await client.post("/api/v1/uploads", files={"file": ("x.png", SVG, "image/png")})
    assert (await client.get(f"/api/v1/uploads/{r.json()['blob']}")).status_code == 415
    assert (await client.get("/api/v1/uploads/not-a-blob")).status_code in (400, 404)


async def test_draft_round_trip_keeps_cid_stable(client, user):
    """mail.insert_image: a saved draft holds the image as a cid part in the Drafts mailbox; reopening
    it gives the same upload reference, and a lost upload comes back from the stored draft."""
    from mailr.api.mail_write import upload_dir

    _, me = await _setup_identity(user)
    blob = (await client.post("/api/v1/uploads", files={"file": ("dot.png", PNG, "image/png")})).json()["blob"]
    payload = {"to": [{"email": "a@example.org"}], "subject": "Inline", "html": _html(blob)}
    r = await client.post("/api/v1/drafts", json={"payload": payload})
    assert r.status_code in (200, 201), r.text
    did = r.json()["draft"]["id"]

    async def stored_cids():
        from mailr.core.store import user_store
        from mailr.db import sessionmaker
        from mailr.models import Message, User

        async with sessionmaker()() as db:
            m = await db.get(Message, did)
            raw = await (await user_store(db, await db.get(User, user.id))).get_raw(m)
        return raw, set(inline_images(raw))

    raw1, cids1 = await stored_cids()
    cid = content_id(blob, me.split("@", 1)[1])
    stored_html = email.message_from_bytes(raw1, policy=email.policy.default).get_body(("html",)).get_content()
    assert cids1 == {cid} and f'src="cid:{cid}"' in stored_html

    (upload_dir(user.id) / blob).unlink()
    got = (await client.get(f"/api/v1/drafts/{did}")).json()["payload"]
    assert f"/api/v1/uploads/{blob}" in got["html"]
    assert (upload_dir(user.id) / blob).read_bytes() == PNG

    r = await client.put(f"/api/v1/drafts/{did}", json={"payload": {**got, "subject": "Inline 2"}})
    assert r.status_code == 200, r.text
    _, cids2 = await stored_cids()
    assert cids2 == {cid}


async def test_forward_carries_inline_images_and_reply_keeps_them_with_the_quote(client, user):
    """Forward: the original's cid images go out again as cid parts. Reply: only while the quote is kept."""
    from mailr.assistant.orders import principal_for
    from mailr.core.drafts import build_for_payload
    from mailr.db import sessionmaker

    await _setup_identity(user)
    orig = build_message({"to": [{"email": user.email}], "subject": "Photo", "html": _html()},
                         from_addr={"email": "alice@example.org", "name": "Alice"},
                         inline_loader=lambda b: (PNG, "image/png"))
    mid, _ = await _ingest(user, orig.as_bytes())

    fwd = (await client.get("/api/v1/compose/reply", params={"message_id": mid, "mode": "forward"})).json()
    assert "data:image" not in fwd["html"] and re.search(r"/api/v1/uploads/[0-9a-f]{32}", fwd["html"])
    rep = (await client.get("/api/v1/compose/reply", params={"message_id": mid, "mode": "reply"})).json()
    assert re.search(r"/api/v1/uploads/[0-9a-f]{32}", rep["html"])

    async with sessionmaker()() as db:
        from mailr.models import User

        p = principal_for(await db.get(User, user.id))
        fwd["to"] = [{"email": "b@example.org"}]
        raw, _, _ = await build_for_payload(db, p, fwd)
        assert len(inline_images(raw)) == 1
        kept, _, _ = await build_for_payload(db, p, {**rep, "in_reply_to_message_id": None})
        assert len(inline_images(kept)) == 1
        body_only = rep["html"].split('<div class="mailr_quote"', 1)[0]
        dropped, _, _ = await build_for_payload(db, p, {**rep, "html": body_only, "in_reply_to_message_id": None})
        assert inline_images(dropped) == {}
