"""Regression tests for hostile content.

Each test is an attack that must no longer work.
"""

import re

from helpers.agent import _setup_identity
from helpers.core_flow import _ingest

LEAK = "https://evil.test/leak-forward-reply.png"
FORWARD_LEAK = (
    "From: Eve Sender <eve@evil.test>\r\nTo: {to}\r\nSubject: SEC forward leak\r\n"
    "Message-ID: <fwd-leak@evil.test>\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
    "<p>Quarterly numbers attached, please forward to finance.</p>"
    f'<img src="{LEAK}" width="320" height="200" alt="chart">'
)


# ---------------- Reply and forward quotes never load remote images directly ----------------


async def test_reply_and_forward_bodies_have_no_direct_remote_image(client, user):
    from mailr.assistant.orders import principal_for
    from mailr.core.drafts import build_for_payload
    from mailr.db import sessionmaker
    from mailr.models import User

    await _setup_identity(user)
    mid, _ = await _ingest(user, FORWARD_LEAK.format(to=user.email).encode())
    for mode in ("forward", "reply"):
        # Default (the reader blocks images): a placeholder that keeps the address aside.
        got = (await client.get("/api/v1/compose/reply", params={"message_id": mid, "mode": mode})).json()
        assert not re.search(r'(?<![-\w])src="(?:https?:)?//', got["html"]), got["html"]
        assert f'data-mailr-src="{LEAK}"' in got["html"]
        # The reader shows the images: the quote shows them through mailr's image proxy only.
        got = (await client.get("/api/v1/compose/reply",
                                params={"message_id": mid, "mode": mode, "images": "allowed"})).json()
        assert not re.search(r'(?<![-\w])src="(?:https?:)?//', got["html"]), got["html"]
        assert re.search(r'src="/api/v1/messages/[^/"]+/images/[0-9a-f]{32}"', got["html"])

    # The mail that goes out carries the image's own address again, and no mailr attribute.
    async with sessionmaker()() as db:
        p = principal_for(await db.get(User, user.id))
        fwd = (await client.get("/api/v1/compose/reply",
                                params={"message_id": mid, "mode": "forward", "images": "allowed"})).json()
        fwd["to"] = [{"email": "b@example.org"}]
        raw, _, _ = await build_for_payload(db, p, fwd)
        text = raw.decode("utf-8", "replace")
        assert LEAK in text and "data-mailr-src" not in text and "/images/" not in text
        blocked = (await client.get("/api/v1/compose/reply", params={"message_id": mid, "mode": "forward"})).json()
        blocked["to"] = [{"email": "b@example.org"}]
        raw, _, _ = await build_for_payload(db, p, blocked)
        assert LEAK in raw.decode("utf-8", "replace")


async def test_images_never_shows_them_in_the_quote(client, user):
    mid, _ = await _ingest(user, FORWARD_LEAK.format(to=user.email).encode())
    r = await client.patch("/api/v1/me/settings", json={"images": "never"})
    assert r.status_code == 200, r.text
    got = (await client.get("/api/v1/compose/reply",
                            params={"message_id": mid, "mode": "forward", "images": "allowed"})).json()
    assert "/images/" not in got["html"] and f'data-mailr-src="{LEAK}"' in got["html"]


def test_restore_remote_images_drops_orphan_proxy_refs():
    from mailr.core.compose import clean_html, restore_remote_images

    html = ('<p><img src="/api/v1/messages/m1/images/' + "a" * 32 + '" data-mailr-src="https://x.example/a.png" alt="A">'
            '<img src="/api/v1/messages/m1/images/' + "b" * 32 + '" alt="B">'
            '<img src="" data-mailr-src="javascript:alert(1)" alt="C"></p>')
    out = restore_remote_images(clean_html(html))
    assert 'src="https://x.example/a.png"' in out
    assert "/images/" not in out and "javascript" not in out and "data-mailr-src" not in out
    assert "B" in out and "C" in out


# ---------------- Bidi controls and invisible characters in header text ----------------

BIDI_FROM = "From: =?utf-8?b?Qm9iIOKArm1vYy5sYXB5YXBAZWNpdnJlcw==?= <bob@evil.test>\r\n"


def test_bidi_override_is_removed_from_names_and_subjects():
    from mailr.core.mime import parse
    from mailr.core.textsafe import BIDI

    raw = (BIDI_FROM + "To: =?utf-8?q?Al=E2=80=AEice?= <a@example.org>\r\n"
           "Subject: =?utf-8?q?Pay=E2=80=8Bment_=E2=80=AEfdp.exe?=\r\n\r\nbody\r\n").encode()
    p = parse(raw)
    assert p.from_email == "bob@evil.test"
    assert p.from_name == "Bob moc.lapyap@ecivres"
    assert not any(c in BIDI for c in p.from_name + p.subject + p.to[0]["name"])
    assert p.subject == "Payment fdp.exe"


def test_rtlo_attachment_name_is_cleaned_for_display_and_download():
    from mailr.core.mime import get_part, parse

    raw = (b"From: a@evil.test\r\nSubject: inv\r\nMIME-Version: 1.0\r\n"
           b'Content-Type: multipart/mixed; boundary="b"\r\n\r\n--b\r\nContent-Type: text/plain\r\n\r\nsee\r\n'
           b"--b\r\nContent-Type: application/pdf\r\n"
           b"Content-Disposition: attachment; filename*=utf-8''invoice%E2%80%AEfdp.exe\r\n"
           b"Content-Transfer-Encoding: base64\r\n\r\nJVBERi0=\r\n--b--\r\n")
    p = parse(raw)
    assert [a["filename"] for a in p.attachments] == ["invoicefdp.exe"]
    _, _, fname = get_part(raw, p.attachments[0]["part"])
    assert fname == "invoicefdp.exe"


def test_brand_check_sees_through_lookalikes_and_invisible_characters():
    from mailr.core.spamrules import claimed_brand

    for name in ("PаyPal", "Pay\u200bPal", "Pay‍Pal", "ＰａｙＰａｌ", "Αpple Support"):
        assert claimed_brand(name, "x@evil.test"), name
    assert claimed_brand("PayPal", "service@paypal.com") is None
    assert claimed_brand("Иван Петров", "ivan@example.ru") is None


# ---------------- Deep MIME nesting ----------------


def _nested(n: int) -> bytes:
    h = "From: a@evil.test\r\nSubject: deep\r\nMIME-Version: 1.0\r\n"
    body = ""
    for i in range(n):
        body += f'Content-Type: multipart/mixed; boundary="b{i}"\r\n\r\n--b{i}\r\n'
    body += "Content-Type: text/plain\r\n\r\nhello deep\r\n"
    for i in reversed(range(n)):
        body += f"--b{i}--\r\n"
    return (h + body).encode()


def test_deep_nesting_renders_as_text_without_recursion():
    from mailr.core.mime import MAX_DEPTH, get_part, inline_images, mime_depth, parse, too_deep
    from mailr.core.render import render_body

    raw = _nested(3000)
    assert mime_depth(raw) == 3000 and too_deep(raw)
    out = render_body(raw, 1)
    assert out["source"] == "text" and "multipart/mixed" in out["text"]  # the raw text, shown as text
    assert inline_images(raw) == {}
    assert parse(raw).subject == "deep"
    try:
        get_part(raw, 3)
        raise AssertionError("expected KeyError")
    except KeyError:
        pass
    # Ordinary nesting is untouched.
    ok = _nested(MAX_DEPTH)
    assert not too_deep(ok) and "hello deep" in render_body(ok, 1)["text"]


# ---------------- <style> through the property allowlist; planted mailr markers ----------------


def _html(body: str) -> bytes:
    return ("From: a@b.c\r\nContent-Type: text/html\r\n\r\n" + body).encode()


def test_style_elements_keep_only_allowed_properties():
    from mailr.core.render import render_body

    out = render_body(_html(
        "<style>.x{position:fixed;top:0;left:0;z-index:9;color:red}"
        ".y{posit\\69 on:fixed}"
        ".mailr-quoted{display:block!important}[data-mailr-quote]{display:block}"
        "@media (max-width:600px){.z{position:absolute;width:100%}}"
        "@font-face{font-family:x;src:url(https://evil.test/f.woff)}</style>"
        '<p class="x">hi</p>'), 1)["html"]
    style = out.split("<style>", 1)[1].split("</style>", 1)[0]
    assert "position" not in style and "z-index" not in style and "top:" not in style
    assert "posit" not in style
    assert "mailr" not in style and "font-face" not in style
    assert "color:red" in style and "width:100%" in style


def test_email_cannot_plant_mailr_ids_classes_or_attributes():
    from mailr.core.render import render_body

    out = render_body(_html(
        '<p id="mailr-hl-1" class="mailr-hl keep">fake citation</p>'
        '<div data-mailr-quote="1">hide me</div><img data-mailr-src="https://x.test/a.png" src="">'
        '<div class="mailr_quote">real quote marker</div>'), 1)["html"]
    assert 'id="mailr-hl-1"' not in out and "mailr-hl" not in out and 'class="keep"' in out
    assert "hide me" in out and out.count("data-mailr-quote") == 1  # only mailr's own, on the mailr_quote div
    assert "x.test" not in out


# ---------------- Image references capped per message ----------------


def test_css_image_references_are_capped():
    from mailr.core import imageproxy
    from mailr.core.render import collect_image_urls, render_body

    divs = "".join(f'<div style="background:url(https://victim.example/{i}.png)">x</div>' for i in range(100))
    raw = _html(divs)
    refs = collect_image_urls(raw)
    assert len(refs) == imageproxy.MAX_IMAGES
    assert refs[0] == "https://victim.example/0.png"
    html = render_body(raw, 1, allow_remote=True, proxy_base="/api/v1/messages/1/images/")["html"]
    emitted = set(__import__("re").findall(r"/api/v1/messages/1/images/([0-9a-f]{32})", html))
    assert emitted == {imageproxy.url_hash(u) for u in refs}


async def test_hash_past_the_cap_is_404(client, user):
    from mailr.core import imageproxy

    divs = "".join(f'<div style="background:url(https://victim.example/{i}.png)">x</div>' for i in range(100))
    mid, _ = await _ingest(user, ("From: a@victim.example\r\nTo: " + user.email +
                                  "\r\nSubject: css\r\nContent-Type: text/html\r\n\r\n" + divs).encode())
    h = imageproxy.url_hash("https://victim.example/61.png")
    r = await client.get(f"/api/v1/messages/{mid}/images/{h}")
    assert r.status_code == 404


# ---------------- Authentication-Results from a trusted receiver only ----------------
# tests/test_authres.py (test_only_the_top_header_and_a_trusted_authserv_id).


# ---------------- Inline PDFs carry a CSP the browsers' viewers accept ----------------


async def test_inline_pdf_has_a_restrictive_csp(client, user):
    raw = ("From: a@evil.test\r\nTo: " + user.email + "\r\nSubject: pdf\r\nMIME-Version: 1.0\r\n"
           'Content-Type: multipart/mixed; boundary="b"\r\n\r\n--b\r\nContent-Type: text/plain\r\n\r\nsee\r\n'
           "--b\r\nContent-Type: application/pdf\r\nContent-Disposition: attachment; filename=r.pdf\r\n"
           "Content-Transfer-Encoding: base64\r\n\r\nJVBERi0xLjQK\r\n--b--\r\n").encode()
    mid, _ = await _ingest(user, raw)
    msg = (await client.get(f"/api/v1/messages/{mid}")).json()
    part = msg["attachments"][0]["part"]
    r = await client.get(f"/api/v1/messages/{mid}/attachments/{part}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/pdf")
    csp = r.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "frame-ancestors 'self'" in csp and "script-src" not in csp
    assert "sandbox" not in csp  # both browsers refuse to render a sandboxed PDF
