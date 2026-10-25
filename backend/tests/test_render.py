"""Message rendering: sanitising, remote content, quotes, inline images."""

from email.message import EmailMessage

from mailr.core.render import render_body


def _html(html: str) -> bytes:
    m = EmailMessage()
    m["From"] = "a@example.org"
    m["Subject"] = "x"
    m.set_content("plain")
    m.add_alternative(html, subtype="html")
    return bytes(m)


def test_styles_scripts_and_remote_images():
    out = render_body(_html(
        '<div style="color:red;position:fixed;background:url(https://t.example/p.gif)" onclick="x()">Hi</div>'
        '<script>alert(1)</script><img src="https://t.example/a.png"><a href="javascript:x()">l</a>'), 1)
    assert "color:red" in out["html"] and "position" not in out["html"] and "url(" not in out["html"]
    assert "script" not in out["html"] and "onclick" not in out["html"] and "javascript:" not in out["html"]
    assert out["remote_images"] and 'data-mailr-src="https://t.example/a.png"' in out["html"]
    allowed = render_body(_html('<img src="https://t.example/a.png">'), 1, allow_remote=True)
    assert 'src="https://t.example/a.png"' in allowed["html"]


def test_quote_marking():
    out = render_body(_html('<div>New text</div><div class="gmail_quote">On Mon, A wrote:'
                            '<blockquote>old</blockquote></div>'), 1)
    assert out["has_quote"] and 'data-mailr-quote="1"' in out["html"]


def test_inline_cid_images_become_data_uris():
    m = EmailMessage()
    m["From"] = "a@example.org"
    m.set_content("plain")
    m.add_alternative('<img src="cid:logo1">', subtype="html")
    m.get_payload()[1].add_related(b"\x89PNG\r\n\x1a\nxx", "image", "png", cid="<logo1>")
    out = render_body(bytes(m), 1)
    assert 'src="data:image/png;base64,' in out["html"]


# Regression cases: remote content must not load with images blocked.
BYPASSES = {
    "image-set attr": '<div style="background-image:image-set(\'https://evil.test/a.png\' 1x)">x</div>',
    "escape attr": '<div style="background:u\\72l(https://evil.test/b.png)">x</div>',
    "style tag image-set": '<style>div{background-image:image-set("https://evil.test/c.png" 1x)}</style><div>x</div>',
    "style tag escape": '<style>div{background:u\\72l(https://evil.test/d.png)}</style><div>x</div>',
    "import no semicolon": '<style>@import "https://evil.test/e.css"</style><div>x</div>',
    "font-face": '<style>@font-face{font-family:x;src:url(https://evil.test/f.woff)}</style>',
    "img srcset": '<img srcset="https://evil.test/g.png 1x">',
    "picture source": '<picture><source srcset="https://evil.test/h.png"><img src="cid:x"></picture>',
    "relative url": '<div style="background:url(/api/v1/me)">x</div>',
}


def test_remote_content_bypasses_are_blocked():
    import re

    from mailr.core.render import render_body

    for name, html in BYPASSES.items():
        raw = ("Content-Type: text/html\r\nSubject: t\r\n\r\n" + html).encode()
        out = render_body(raw, 1, allow_remote=False)
        live = re.sub(r'data-mailr-(src|bg)="[^"]*"', "", out["html"])
        assert "evil.test" not in live and "/api/v1" not in live, (name, out["html"])
        assert "img-src data:" in out["csp"]


def test_allowed_remote_images_are_kept():
    from mailr.core.render import render_body

    raw = b'Content-Type: text/html\r\n\r\n<img src="https://ok.test/a.png"><div style="background:url(https://ok.test/b.png)">x</div>'
    out = render_body(raw, 1, allow_remote=True)
    assert "https://ok.test/a.png" in out["html"] and "https://ok.test/b.png" in out["html"]
    assert out["remote_images"]
