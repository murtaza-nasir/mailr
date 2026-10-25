"""The HTML sanitiser (nh3) behind compose.clean_html and render_body: the allow-lists and the
places where its parser reads HTML differently from the html5lib-based one it replaced."""

import time
from email.message import EmailMessage

import pytest

from mailr.core import compose, render
from mailr.core.compose import clean_html
from mailr.core.render import render_body


def _html(html: str) -> bytes:
    m = EmailMessage()
    m["From"] = "a@example.org"
    m["Subject"] = "x"
    m.set_content("plain")
    m.add_alternative(html, subtype="html")
    return bytes(m)


def test_script_and_style_go_with_their_content_in_outgoing_html():
    # The old sanitiser kept the text of a removed element ("alert(1)", the CSS) as visible text.
    assert clean_html("<p>Hi<script>alert(1)</script></p>") == "<p>Hi</p>"
    assert clean_html("<style>p{color:red}</style><p>x</p>") == "<p>x</p>"
    assert clean_html("<svg><script>alert(1)</script></svg><p>x</p>") == "<p>x</p>"


def test_the_reader_keeps_style_elements_and_drops_script_content():
    out = render._sanitize("<style>p{color:red}</style><p>x</p><script>alert(1)</script>")
    assert out == "<style>p{color:red}</style><p>x</p>"


@pytest.mark.parametrize("tag", ["noscript", "textarea", "noembed", "noframes", "xmp"])
def test_markup_inside_raw_text_elements_stays_text(tag):
    # The parsers read these elements' content as text; it is shown escaped, never as markup.
    out = render_body(_html(f'<p>a</p><{tag}><b>bold</b><img src=x onerror="alert(1)"></{tag}>'), 1)["html"]
    assert out.startswith("<p>a</p>") and "<img" not in out and "<b>" not in out and f"<{tag}" not in out


@pytest.mark.parametrize("value", ["javascript:alert(1)", "JaVaScRiPt:x", "java\tscript:x", "&#106;avascript:x",
                                   " vbscript:x"])
def test_cite_and_background_urls_are_checked(value):
    assert "cite" not in clean_html(f'<blockquote cite="{value}">c</blockquote>')
    out = render._sanitize(f'<blockquote cite="{value}">c</blockquote><table background="{value}"><tr>'
                           f'<td background="{value}">x</td></tr></table>')
    assert "cite" not in out and "background" not in out
    ok = render._sanitize('<blockquote cite="https://ok.example/a">c</blockquote><td background="/x.png">')
    assert 'cite="https://ok.example/a"' in ok


def test_links_keep_their_own_rel_and_get_none_added():
    assert clean_html('<a href="https://ok.example/">x</a>') == '<a href="https://ok.example/">x</a>'
    assert 'rel="noopener"' in clean_html('<a href="https://ok.example/" rel="noopener">x</a>')
    assert "href" not in clean_html('<a href="data:text/html,x">x</a>')
    assert "href" not in clean_html('<a href=" javascript:x">x</a>')


def test_image_attributes_in_outgoing_html():
    out = clean_html('<img src="cid:a@x" alt="i" dir="rtl" onerror="x" data-mailr-src="https://i.example/a.png">')
    assert out == '<img src="cid:a@x" alt="i" data-mailr-src="https://i.example/a.png">'
    assert "src" not in clean_html('<img src="data:image/svg+xml;base64,PHN2Zz4=">')
    assert 'src="data:image/png;base64,iVBORw0KGgo="' in clean_html('<img src="data:image/png;base64,iVBORw0KGgo=">')


def test_inline_styles_keep_only_allowed_properties():
    out = clean_html('<p style="position:fixed;z-index:9;color: red !important;behavior:url(x.htc)">x</p>')
    assert "position" not in out and "z-index" not in out and "behavior" not in out
    assert "color:red ! important" in out  # "! important" is the same declaration to a browser
    out = render._sanitize('<div style="background:url(javascript:alert(1));color:red">x</div>')
    assert "javascript" not in out and "color:red" in out


def test_comments_and_entities():
    assert clean_html("<p>a<!-- hidden -->b</p>") == "<p>ab</p>"
    assert clean_html("<p>&lt;b&gt; &amp; &nbsp;x</p>") == "<p>&lt;b&gt; &amp; &nbsp;x</p>"
    # A quote inside an attribute stays inside it.
    assert clean_html('<a title="&quot;><script>alert(1)</script>">x</a>') == (
        '<a title="&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;">x</a>')


def test_the_allow_lists_are_sets_of_known_names():
    assert "script" not in compose.ALLOWED_TAGS and "style" not in compose.ALLOWED_TAGS
    assert "script" not in render._TAGS and "style" in render._TAGS
    assert "position" not in compose.CSS and "position" not in render._CSS_PROPS
    assert render.RENDER_VERSION >= 2  # the body cache keys on it: the sanitiser changed


def test_a_large_mail_is_cleaned_quickly():
    row = ('<tr><td style="padding:4px;color:#333;font-family:Arial">Item <b>{i}</b> '
           '<a href="https://shop.example/p/{i}?ref=mail">link</a><img src="https://img.example/{i}.png" '
           'width="20" alt=""></td><td background="https://img.example/bg.png">x</td></tr>')
    html = "<table>" + "".join(row.format(i=i) for i in range(6500)) + "</table>"
    assert len(html) > 1_500_000
    t = time.perf_counter()
    out = render._sanitize(html)
    clean_html(html)
    assert time.perf_counter() - t < 5
    assert out.count("<tr>") == 6500
