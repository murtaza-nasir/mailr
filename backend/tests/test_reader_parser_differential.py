"""The reader's first pass (BeautifulSoup's html.parser) and the sanitiser (nh3, an HTML5 parser)
once read two constructs differently, so markup the first pass never saw reached the output:
"<![CDATA[" (html.parser takes it up to "]]>", HTML5 ends it at the first ">") and "<image>"
(HTML5 reads it as <img>). The first pass now removes such nodes and reads <image> as <img>, and a
last pass on the sanitiser's output holds images, links and mailr's markers to what the first pass
decided (core/render.py _finish)."""

from __future__ import annotations

import random
import re
import string

import nh3
from bs4 import BeautifulSoup

from mailr.core.render import _TAGS, render_body

PROXY = "/api/v1/messages/abc/images/"


def _mail(html: str) -> bytes:
    return ("From: a@sender.test\r\nTo: me@mailr.test\r\nSubject: x\r\n"
            "Content-Type: text/html; charset=utf-8\r\n\r\n" + html).encode()


def _without_kept_sources(html: str) -> str:
    return re.sub(r'data-mailr-src="[^"]*"', "", html)


def test_cdata_cannot_carry_a_remote_image():
    out = render_body(_mail("<![CDATA[>" + '<img src="https://tracker.example/open.gif">'), 1,
                      allow_remote=False, proxy_base=PROXY)
    assert "tracker.example" not in _without_kept_sources(out["html"]), out["html"]


def test_an_image_tag_is_blocked_like_img():
    out = render_body(_mail('<image src="https://tracker.example/open.gif">'), 1, allow_remote=False, proxy_base=PROXY)
    assert "tracker.example" not in _without_kept_sources(out["html"]), out["html"]
    assert out["remote_images"] is True


def test_cdata_cannot_carry_relative_or_data_links():
    out = render_body(_mail('<![CDATA[><a href="/settings">a</a><a href="data:text/html,x">b</a>'), 1)
    assert 'href="/settings"' not in out["html"], out["html"]
    assert 'href="data:' not in out["html"], out["html"]


def test_relative_and_data_links_and_sources_never_reach_the_output():
    out = render_body(_mail('<a href="/settings">a</a><a href="data:text/html,x">b</a><a href="//evil.example/">c</a>'
                            '<img src="/api/v1/me">'), 1)
    assert "href" not in out["html"] and 'src="/api' not in out["html"], out["html"]


def test_cdata_cannot_forge_mailr_markers():
    out = render_body(_mail('<![CDATA[><div data-mailr-quote="1">hidden as history</div>'
                            '<span id="mailr-hl-1" class="mailr-hl">x</span>'), 1)
    assert "data-mailr-quote" not in out["html"], out["html"]
    assert "mailr-hl" not in out["html"], out["html"]


def test_mailrs_own_markers_still_come_through():
    html = ('<p>Thanks</p><div class="gmail_quote">On Monday Ana wrote:<blockquote>old</blockquote></div>'
            '<img src="https://images.example/a.png">')
    out = render_body(_mail(html), 1, allow_remote=False, proxy_base=PROXY)
    assert out["has_quote"] and 'data-mailr-quote="1"' in out["html"]
    assert 'data-mailr-src="https://images.example/a.png"' in out["html"] and out["remote_images"]
    assert not re.search(r"data-m[0-9a-f]{12}-", out["html"])


def test_a_reply_quote_has_no_live_remote_image():
    # drafts.reply_payload renders with keep_source and without the proxy while images are blocked.
    for html in ('<image src="https://tracker.example/reply.gif">', '<![CDATA[><img src="https://tracker.example/reply.gif">'):
        out = render_body(_mail(html), 1, False, None, True)
        assert 'src="https://tracker.example' not in _without_kept_sources(out["html"]), out["html"]


# ---------------------------------------------------------------- random mixes of hostile tokens

_TOKENS = [
    "<![CDATA[", "]]>", "<image src=https://t.example/im>", '<img src="https://t.example/p.png">',
    "<img src=//t.example/q>", '<a href="/rel">', '<a href="data:text/html,x">', '<a href="javascript:x()">',
    "<a href=https://ok.example>", "<div data-mailr-quote=1>", '<span class="mailr-hl" id="mailr-hl-2">',
    "<!--", "-->", "<!x ", "<?x ", "<!DOCTYPE x>", ">", '"', "'", "<", "</", '<p title="', '">', "<style>",
    "</style>", "<svg>", "<math>", "<table>", "<td background=https://t.example/bg>", "<template>", "<textarea>",
    "</textarea>", "<noscript>", "<xmp>", "</xmp>", "x", "<p>", "</p>", "<img srcset=\"https://t.example/s.png 2x\">",
    '<div style="background:url(https://x.example/s)">', "<style>p{background:url(https://e.example/b)}</style>",
    "<img src=x onerror=alert(1)>", "<base href=https://evil.example/>", '<p style="position:fixed">',
]


def _html5(html: str) -> BeautifulSoup:
    """The output as an HTML5 parser (the browser's kind) reads it, every tag and attribute kept."""
    return BeautifulSoup(nh3.clean(html, tags=_TAGS | {"image", "svg", "math", "template", "textarea", "xmp", "base",
                                                      "noscript"},
                                   clean_content_tags=set(), attributes={},
                                   generic_attribute_prefixes=set(string.ascii_lowercase),
                                   url_schemes={"javascript", "data", "http", "https", "mailto", "tel"}, link_rel=None,
                                   strip_comments=False, filter_style_properties=None), "html.parser")


def _problems(html: str) -> list[str]:
    out = []
    for el in _html5(html).find_all(True):
        if el.name not in _TAGS:
            out.append("tag:" + el.name)
        for k in el.attrs:
            if k.startswith("on"):
                out.append("event:" + k)
            if k.startswith("data-mailr") and k not in ("data-mailr-src", "data-mailr-bg"):
                out.append("marker:" + k)
        if any(str(c).startswith("mailr-") for c in el.get("class") or []) or str(el.get("id") or "").startswith("mailr"):
            out.append("marker:class/id")
        if el.name == "a" and el.get("href") and not re.match(r"(?i)(https?://[^/]|mailto:|tel:)", el["href"]):
            out.append("href:" + el["href"])
        if el.name == "img" and el.get("src") and not el["src"].startswith("data:image/"):
            out.append("src:" + el["src"])
        if el.get("background"):
            out.append("background:" + el["background"])
        if re.search(r"url\(|position|@import", el.get("style", ""), re.I):
            out.append("style:" + el["style"])
        if el.name == "style" and re.search(r"url\(|@import|position|<", el.get_text(), re.I):
            out.append("styletext")
    return out


def test_random_mixes_of_the_two_constructs_and_others_stay_inside_the_rules():
    rng = random.Random(20261024)
    bad = []
    for _ in range(1500):
        html = "".join(rng.choice(_TOKENS) for _ in range(rng.randint(2, 14)))
        for allow, keep in ((False, False), (False, True)):
            out = render_body(_mail(html), 1, allow_remote=allow, keep_source=keep)["html"]
            if probs := _problems(out):
                bad.append((html, probs))
    assert not bad, bad[:5]
