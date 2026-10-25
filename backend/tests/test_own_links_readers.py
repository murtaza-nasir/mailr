# ACT-SURFACES: assistant web.settings
"""Own links and page readers.

Part 1 (agent.read_own_link), links the user put in their own mail (agent/own_links.py): read_page in the built-in
assistant opens a link in the user's own note to self and in their phone's "Send to self" share;
it refuses a link in a message with the user's address in From that failed authentication and has
no Sent copy, a link in a quoted or forwarded part of the user's own reply (inline and attached),
a link in another person's mail, a draft's link, and a link in a conversation it has not read.

Part 2, the reader chain (websearch/readers.py; admin: settings.page_readers): the built-in reader first; on an error or a page
with almost no text, the admin's readers in order (browserless, Firecrawl, Jina Reader) with fake
servers; the browser's proxy (Chrome's --proxy-server and its sign-in) under the egress proxy,
refusal under MAILR_REQUIRE_EGRESS without one, private requests blocked without any egress; local
non-browser readers skipped under MAILR_REQUIRE_EGRESS; keys never in errors; limits; the admin
API for readers; Reddit share links and the post's text; the Jina and Firecrawl search adapters.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
import pytest
from helpers.agent import _ingest
from helpers.own_links_readers import PAGE, WEBRTC, _lan_ok, _reader, _web_on
from helpers.websearch import (  # noqa: F401 - fixtures
    _clean,
    _ctx,
    _egress,
    _mock,
    _page_client,
    _require,
)
from sqlalchemy import delete

from mailr.agent import own_links
from mailr.db import sessionmaker
from mailr.models import Message, WebSearchProvider
from mailr.websearch import adapters, pages, readers, service


def _now_hdr() -> str:
    return datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S +0000")


def _html_page(monkeypatch, seen: list[str] | None = None, text: str = PAGE):
    def handler(req):
        if seen is not None:
            seen.append(str(req.url))
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=text)

    _page_client(monkeypatch, handler)


async def _read(user, url: str, *, opened=(), chat_conversation=None):
    from mailr.agent.tools import run_tool

    ctx = await _ctx(user.id)
    ctx.opened = set(opened)
    if chat_conversation:
        ctx.chat_context = {"conversation_id": chat_conversation}
    try:
        out = await run_tool(ctx, "read_page", {"url": url})
        await ctx.db.commit()  # the audit row
        return out
    finally:
        await ctx.db.close()


# ---------------------------------------------------------------- part 1: own links


def test_own_text_keeps_only_the_users_part():
    from email.message import EmailMessage

    inner = EmailMessage()
    inner["From"] = "x@evil.example"
    inner["Subject"] = "hi"
    inner.set_content("Click https://evil.example/attached")
    m = EmailMessage()
    m["From"] = "me@example.com"
    m.set_content("See https://good.example/mine\n\n> https://evil.example/quoted\n\n"
                  "---------- Forwarded message ---------\nFrom: X <x@evil.example>\nhttps://evil.example/fwd\n")
    m.add_attachment(inner)
    links = own_links.links_in(own_links.own_text(m.as_bytes()))
    assert links == ["https://good.example/mine"]

    h = EmailMessage()
    h["From"] = "me@example.com"
    h.set_content("<p>Mine: <a href='https://good.example/h'>here</a></p><div class='gmail_quote'>On Mon, X wrote:"
                  "<blockquote><a href='https://evil.example/q'>q</a></blockquote></div>", subtype="html")
    assert own_links.links_in(own_links.own_text(h.as_bytes())) == ["https://good.example/h"]


async def test_own_note_link_opens(user, raw, monkeypatch):
    await _web_on()
    _egress(monkeypatch)
    seen: list[str] = []
    _html_page(monkeypatch, seen)
    link = "https://www.reddit.com/r/selfhosted/s/WrTYQxK8Ti"
    mid, cid = await _ingest(user.id, raw("Note", frm=f"Me <{user.email}>", to=user.email, date=_now_hdr(),
                                          body=link), source="send", roles=["sent"])
    # Not read yet in this turn and not the chat's conversation: refused.
    with pytest.raises(Exception, match="never opened"):
        await _read(user, link)
    out = await _read(user, link, opened={cid})
    assert out.data["title"]["content"] == "Self-hosted Photoshop" and seen == [link]
    # The chat about that conversation may open it too.
    assert (await _read(user, link, chat_conversation=cid)).data["url"] == link
    from sqlalchemy import select

    from mailr.models import AuditLog
    async with sessionmaker()() as db:
        a = (await db.execute(select(AuditLog).where(AuditLog.action == "agent.read_page", AuditLog.target == link)
                              .order_by(AuditLog.id.desc()))).scalars().first()
    assert a.inputs.get("own_link_in") == mid


async def test_android_share_link_opens(user, raw, monkeypatch):
    await _web_on()
    _egress(monkeypatch)
    _html_page(monkeypatch)
    link = "https://news.example.org/story/42"
    _, cid = await _ingest(user.id, raw("Shared", frm=f"Me <{user.email}>", to=user.email, date=_now_hdr(),
                                        body=f"Interesting story\n{link}", extra="X-Mailr-Origin: android-share"),
                           source="send", roles=["sent"])
    assert (await _read(user, link, opened={cid})).data["site"] == "news.example.org"


async def test_spoofed_from_is_refused(user, raw, monkeypatch):
    await _web_on()
    _egress(monkeypatch)
    _html_page(monkeypatch)
    link = "https://phish.example.net/login"
    mid, cid = await _ingest(user.id, raw("Note", frm=f"Me <{user.email}>", to=user.email, date=_now_hdr(), body=link,
                                          extra="Authentication-Results: mx.example.com; dkim=fail; dmarc=fail "
                                                f"header.from={user.email.split('@')[1]}"))
    async with sessionmaker()() as db:
        assert (await db.get(Message, mid)).sent_by_me is False
    with pytest.raises(Exception, match="never opened"):
        await _read(user, link, opened={cid})


async def test_quoted_and_forwarded_parts_of_own_reply_are_refused(user, raw, monkeypatch):
    await _web_on()
    _egress(monkeypatch)
    _html_page(monkeypatch)
    body = ("Thanks, will look at https://mine.example.com/ok\r\n\r\n"
            "On Mon, 5 Oct 2026, Alice <alice@example.org> wrote:\r\n> Try https://quoted.example.com/q\r\n")
    _, cid = await _ingest(user.id, raw("Re: x", frm=f"Me <{user.email}>", to="alice@example.org", date=_now_hdr(),
                                        body=body), source="send", roles=["sent"])
    fwd = ("FYI\r\n\r\n---------- Forwarded message ---------\r\nFrom: Bob <bob@example.org>\r\n"
           "Subject: offer\r\n\r\nSee https://forwarded.example.com/f\r\n")
    _, cid2 = await _ingest(user.id, raw("Fwd: offer", frm=f"Me <{user.email}>", to="carol@example.org",
                                         date=_now_hdr(), body=fwd), source="send", roles=["sent"])
    assert (await _read(user, "https://mine.example.com/ok", opened={cid})).data["site"] == "mine.example.com"
    for url, c in (("https://quoted.example.com/q", cid), ("https://forwarded.example.com/f", cid2)):
        with pytest.raises(Exception, match="never opened"):
            await _read(user, url, opened={c})


async def test_link_in_an_attached_forward_is_refused(user, monkeypatch):
    from email.message import EmailMessage

    await _web_on()
    _egress(monkeypatch)
    _html_page(monkeypatch)
    inner = EmailMessage()
    inner["From"] = "Eve <eve@example.org>"
    inner["Subject"] = "deal"
    inner.set_content("https://attached.example.com/a")
    m = EmailMessage()
    m["From"] = f"Me <{user.email}>"
    m["To"] = "dan@example.org"
    m["Subject"] = "Fwd: deal"
    m["Date"] = _now_hdr()
    m["Message-ID"] = f"<{uuid.uuid4().hex}@example.com>"
    m.set_content("Have a look.")
    m.add_attachment(inner)
    _, cid = await _ingest(user.id, m.as_bytes(), source="send", roles=["sent"])
    with pytest.raises(Exception, match="never opened"):
        await _read(user, "https://attached.example.com/a", opened={cid})


async def test_other_peoples_mail_and_drafts_are_refused(user, raw, monkeypatch):
    await _web_on()
    _egress(monkeypatch)
    _html_page(monkeypatch)
    _, cid = await _ingest(user.id, raw("Offer", body="https://other.example.com/x", date=_now_hdr()))
    with pytest.raises(Exception, match="never opened"):
        await _read(user, "https://other.example.com/x", opened={cid})
    mid, cid2 = await _ingest(user.id, raw("Draft", frm=f"Me <{user.email}>", to=user.email, date=_now_hdr(),
                                           body="https://drafted.example.com/d"), source="send", roles=["sent"])
    async with sessionmaker()() as db:
        (await db.get(Message, mid)).draft = True
        await db.commit()
    with pytest.raises(Exception, match="never opened"):
        await _read(user, "https://drafted.example.com/d", opened={cid2})


# ---------------------------------------------------------------- part 2: readers


async def test_builtin_first_and_no_readers_means_builtin_only(monkeypatch):
    _egress(monkeypatch)
    _page_client(monkeypatch, lambda req: httpx.Response(403, text="blocked"))
    async with sessionmaker()() as db:
        with pytest.raises(pages.PageError, match="HTTP 403"):
            await readers.read(db, "https://www.reddit.com/r/x/comments/1/a/")
    _html_page(monkeypatch)
    async with sessionmaker()() as db:
        out = await readers.read(db, "https://docs.example.org/a")
    assert out["reader"] == "Built-in" and out["chars"] > 200


async def test_browserless_after_a_blocked_builtin_with_the_egress_proxy(monkeypatch):
    _egress(monkeypatch, "http://vpnuser:s3cret@gluetun.internal:8888")
    _lan_ok(monkeypatch)
    _page_client(monkeypatch, lambda req: httpx.Response(403, text="blocked"))
    pid = await _reader(key="bl-token-1")
    seen = _mock(monkeypatch, lambda req: httpx.Response(
        200, headers={"content-type": "text/html", "x-response-code": "200",
                      "x-response-url": "https://app.example.com/final"}, text=PAGE))
    async with sessionmaker()() as db:
        out = await readers.read(db, "https://app.example.com/start")
    assert out["reader"].startswith("R-") and out["final_url"] == "https://app.example.com/final"
    assert out["via"] == "lan" and "A long article." in out["text"]
    req = seen[-1]
    assert req.url.path == "/content" and req.url.params["token"] == "bl-token-1"
    assert json.loads(req.url.params["launch"]) == {"args": ["--proxy-server=http://gluetun.internal:8888",
                                                              "--proxy-bypass-list=<-loopback>", *WEBRTC]}
    body = json.loads(req.content)
    assert body["url"] == "https://app.example.com/start"
    # The proxy's password never goes to the browser; the request filter
    # goes on every route.
    assert "authenticate" not in body and "s3cret" not in req.content.decode() and body["rejectRequestPattern"]
    async with sessionmaker()() as db:
        row = await db.get(WebSearchProvider, pid)
        assert row.last_error == "" and row.last_ok_at is not None and row.usage_count == 1
    # MAILR_BROWSER_EGRESS_PROXY: the address the browser reaches the proxy at.
    from mailr.config import get_settings
    monkeypatch.setattr(get_settings(), "browser_egress_proxy", "http://192.168.1.5:8888")
    async with sessionmaker()() as db:
        await readers.read(db, "https://app.example.com/start")
    assert json.loads(seen[-1].url.params["launch"])["args"][0] == "--proxy-server=http://192.168.1.5:8888"
    assert "authenticate" not in json.loads(seen[-1].content)


async def test_thin_builtin_page_goes_to_the_next_reader_and_comes_back_when_all_fail(monkeypatch):
    _egress(monkeypatch)
    _lan_ok(monkeypatch)
    _page_client(monkeypatch, lambda req: httpx.Response(200, headers={"content-type": "text/html"},
                                                         text="<title>App</title><p>Enable JavaScript.</p>"))
    await _reader(key="tok-secret-xyz")
    _mock(monkeypatch, lambda req: httpx.Response(401, text="Bad or missing authentication."))
    async with sessionmaker()() as db:
        out = await readers.read(db, "https://spa.example.com/")
    assert out["reader"] == "Built-in" and out["text"] == "Enable JavaScript."
    async with sessionmaker()() as db:
        rows = await service.providers(db, purpose="reader")
        assert "refused the API key (HTTP 401)" in rows[0].last_error and "tok-secret" not in rows[0].last_error


async def test_browserless_under_required_egress_without_one_refuses(monkeypatch):
    _require(monkeypatch)
    _egress(monkeypatch, "")
    _lan_ok(monkeypatch)
    await _reader()
    seen = _mock(monkeypatch, lambda req: httpx.Response(200, text=PAGE))
    async with sessionmaker()() as db:
        with pytest.raises(pages.PageError) as e:
            await readers.read(db, "https://app.example.com/")
    assert "Reading pages with a browser is paused" in str(e.value) and seen == []


async def test_browserless_without_any_egress_blocks_private_requests(monkeypatch):
    _egress(monkeypatch, "")
    _lan_ok(monkeypatch)
    _page_client(monkeypatch, lambda req: httpx.Response(500))

    async def public(url):
        if "10.0.0.5" in url or "intranet" in url:
            from mailr.core import netguard
            raise netguard.UnsafeTarget("resolves to a private address")

    async def addresses(url):
        await public(url)
        return urlsplit(url).hostname, ["93.184.216.34"]

    from mailr.core import netguard
    monkeypatch.setattr(netguard, "check_untrusted_url", public)
    monkeypatch.setattr(netguard, "untrusted_url_addresses", addresses)
    monkeypatch.setattr(pages.netguard, "check_untrusted_url", public)
    await _reader()
    seen = _mock(monkeypatch, lambda req: httpx.Response(200, headers={"x-response-url": "https://intranet.example.com/"},
                                                         text=PAGE))
    async with sessionmaker()() as db:
        with pytest.raises(pages.PageError, match="moved to"):
            await readers.read(db, "https://app.example.com/")
    body = json.loads(seen[-1].content)
    # No proxy; the page host is pinned to its checked address.
    assert json.loads(seen[-1].url.params["launch"]) == {
        "args": [*WEBRTC, "--host-resolver-rules=MAP app.example.com 93.184.216.34"]}
    assert body["rejectRequestPattern"]
    import re
    pats = [re.compile(p) for p in body["rejectRequestPattern"]]
    for bad in ("http://192.168.1.1/admin", "http://10.1.2.3/", "http://router.lan/", "http://localhost:8080/",
                "http://nas/", "https://[::1]/"):
        assert any(p.search(bad) for p in pats), bad
    for good in ("https://www.reddit.com/r/x", "https://cdn.example.com/app.js"):
        assert not any(p.search(good) for p in pats), good


async def test_local_firecrawl_is_skipped_under_required_egress_and_hosted_readers_work(monkeypatch):
    _require(monkeypatch)
    _egress(monkeypatch)
    _lan_ok(monkeypatch)
    _page_client(monkeypatch, lambda req: httpx.Response(503))
    await _reader(kind="firecrawl", base_url="http://firecrawl.lan:3002", priority=10)
    await _reader(kind="jina", base_url="https://r.jina.ai", network="internet", priority=20, key="jina-k")

    def handler(req):
        assert req.url.host == "r.jina.ai", "the local Firecrawl must not be asked"
        assert req.headers["authorization"] == "Bearer jina-k" and json.loads(req.content) == {
            "url": "https://news.example.com/a"}
        return httpx.Response(200, json={"code": 200, "data": {"title": "News", "url": "https://news.example.com/a",
                                                                "content": "# News\n\nBody text here."}})

    _mock(monkeypatch, handler)
    async with sessionmaker()() as db:
        out = await readers.read(db, "https://news.example.com/a")
    assert out["title"] == "News" and "Body text here." in out["text"] and out["via"] == "egress"


async def test_firecrawl_reader_mapping_and_errors(monkeypatch):
    _egress(monkeypatch, "")
    _page_client(monkeypatch, lambda req: httpx.Response(404))
    pid = await _reader(kind="firecrawl", base_url="https://api.firecrawl.example", network="internet", key="fc")
    answers = [
        {"success": True, "data": {"markdown": "Hello **world**", "metadata": {"title": "T", "statusCode": 200,
                                                                              "sourceURL": "https://x.example.com/"}}},
        {"success": True, "data": {"markdown": "", "metadata": {"statusCode": 404}}},
        {"success": False, "error": "Rate limit"},
    ]

    def handler(req):
        assert req.url.path == "/v1/scrape" and req.headers["authorization"] == "Bearer fc"
        assert json.loads(req.content)["formats"] == ["markdown"]
        return httpx.Response(200, json=answers.pop(0))

    _mock(monkeypatch, handler)

    async def pub(url, **kw):
        return None

    from mailr.core import netguard
    monkeypatch.setattr(netguard, "check_url", pub)
    async with sessionmaker()() as db:
        out = await readers.read(db, "https://x.example.com/")
        assert out["text"] == "Hello **world**" and out["title"] == "T" and out["via"] == "direct"
        with pytest.raises(pages.PageError, match="HTTP 404 \\(Firecrawl\\)"):
            await readers.read(db, "https://x.example.com/")
        with pytest.raises(pages.PageError, match="Rate limit"):
            await readers.read(db, "https://x.example.com/")
    assert pid


async def test_reader_limits_and_page_address_checks(monkeypatch):
    _egress(monkeypatch)
    _lan_ok(monkeypatch)
    _page_client(monkeypatch, lambda req: httpx.Response(403))
    await _reader(per_day=1)
    seen = _mock(monkeypatch, lambda req: httpx.Response(200, headers={"content-type": "text/html"}, text=PAGE))
    async with sessionmaker()() as db:
        await readers.read(db, "https://a.example.com/")
        with pytest.raises(pages.PageError, match="limit of 1 page reads a day"):
            await readers.read(db, "https://a.example.com/")
        # No reader is ever asked for an address on the admin's network.
        n = len(seen)
        for bad in ("https://router.lan/", "https://192.168.1.1/", "https://example.com:8443/"):
            with pytest.raises(pages.PageError):
                await readers.read(db, bad)
        assert len(seen) == n


def test_browser_proxy_forms(monkeypatch):
    from mailr.config import get_settings

    monkeypatch.setattr(get_settings(), "browser_egress_proxy", "")
    assert readers.browser_proxy("http://u:p%40ss@vpn:8888").server == "http://vpn:8888"
    assert readers.browser_proxy("http://u:p%40ss@vpn:8888").password == "p@ss"
    assert readers.browser_proxy("socks5://vpn:1080").server == "socks5://vpn:1080"
    with pytest.raises(service.ProviderError, match="SOCKS"):
        readers.browser_proxy("socks5://u:p@vpn:1080")


# ---------------------------------------------------------------- Reddit

REDDIT = """<html><head><title>Self-hosted Photoshop and Acrobat Pro : r/selfhosted</title></head><body>
<shreddit-app><nav>Reddit home</nav>
<shreddit-post author="alex_ops" created-timestamp="2026-10-08T14:02:11.000000+0000"
  post-title="Self-hosted Photoshop and Acrobat Pro?" score="412" comment-count="87"
  subreddit-prefixed-name="r/selfhosted" content-href="https://www.reddit.com/r/selfhosted/comments/1x0eu16/x/">
  <h1 slot="title">Self-hosted Photoshop and Acrobat Pro?</h1>
  <div slot="text-body"><div class="md"><p>Looking for alternatives.</p><p>See <a href="https://www.photopea.com/">Photopea</a>.</p></div></div>
</shreddit-post>
<shreddit-comment-tree>
  <shreddit-comment author="first" score="120" depth="0"><div slot="comment"><div class="md"><p>Stirling PDF for Acrobat.</p></div></div>
    <shreddit-comment author="reply" score="40" depth="1"><div slot="comment"><p>Agreed.</p></div></shreddit-comment>
  </shreddit-comment>
  <shreddit-comment author="second" score="88" depth="0"><div slot="comment"><div class="md"><p>GIMP with PhotoGIMP.</p></div></div></shreddit-comment>
</shreddit-comment-tree></shreddit-app></body></html>"""


async def test_an_empty_page_is_an_error(monkeypatch):
    """Reddit answers plain requests from some networks with a bot check: 200, a logo and a form."""
    _egress(monkeypatch)
    _page_client(monkeypatch, lambda req: httpx.Response(200, headers={"content-type": "text/html"},
                                                         text="<html><head><title>Reddit</title></head><body><main><svg></svg>"
                                                         "<form><input name=\"x\"></form></main></body></html>"))
    async with sessionmaker()() as db:
        with pytest.raises(pages.PageError, match="no text without JavaScript"):
            await readers.read(db, "https://www.reddit.com/r/selfhosted/comments/1x0eu16/x/")


def test_reddit_post_text():
    d = pages.extract(REDDIT)
    t = d["text"]
    assert d["title"] == "Self-hosted Photoshop and Acrobat Pro?" and d["published"].startswith("2026-10-08")
    assert "r/selfhosted · u/alex_ops · score 412 · 87 comments" in t
    assert "Looking for alternatives." in t and "Photopea (https://www.photopea.com/)" in t
    assert "u/first (score 120):\nStirling PDF for Acrobat." in t and "u/second (score 88)" in t
    assert "Agreed." not in t and "Reddit home" not in t  # replies and navigation left out
    assert t.index("u/first") < t.index("u/second")


async def test_reddit_share_link_follows_the_redirect(monkeypatch):
    _egress(monkeypatch)
    seen = []

    def handler(req):
        seen.append(str(req.url))
        if "/s/" in req.url.path:
            return httpx.Response(301, headers={"location": "https://www.reddit.com/r/selfhosted/comments/1x0eu16/"
                                                            "selfhosted_photoshop_and_acrobat_pro/"})
        return httpx.Response(200, headers={"content-type": "application/atom+xml; charset=UTF-8"}, text=REDDIT_FEED)

    _page_client(monkeypatch, handler)
    async with sessionmaker()() as db:
        out = await readers.read(db, "https://www.reddit.com/r/selfhosted/s/WrTYQxK8Ti")
    # The share link's redirect lands on the post, which is read as its feed (Reddit refuses the page).
    assert seen == ["https://www.reddit.com/r/selfhosted/s/WrTYQxK8Ti",
                    "https://www.reddit.com/r/selfhosted/comments/1x0eu16/.rss"]
    assert out["title"] == "Self-hosted Photoshop and Acrobat Pro?" and "Stirling PDF for Acrobat." in out["text"]


REDDIT_FEED = """<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom">
<category term="selfhosted" label="r/selfhosted"/><title>Self-hosted Photoshop and Acrobat Pro? : selfhosted</title>
<entry><author><name>/u/alex_ops</name></author><category term="selfhosted" label="r/selfhosted"/>
<content type="html">&lt;!-- SC_OFF --&gt;&lt;div class="md"&gt;&lt;p&gt;Looking for alternatives.&lt;/p&gt;&lt;/div&gt;&lt;!-- SC_ON --&gt; submitted by /u/alex_ops</content>
<published>2026-10-08T14:00:00+00:00</published><title>Self-hosted Photoshop and Acrobat Pro?</title></entry>
<entry><author><name>/u/first</name></author><content type="html">&lt;div class="md"&gt;&lt;p&gt;Stirling PDF for Acrobat.&lt;/p&gt;&lt;/div&gt;</content>
<title>/u/first on Self-hosted Photoshop and Acrobat Pro?</title></entry>
<entry><author><name>/u/second</name></author><content type="html">&lt;div class="md"&gt;&lt;p&gt;Photopea.&lt;/p&gt;&lt;/div&gt;</content>
<title>/u/second on Self-hosted Photoshop and Acrobat Pro?</title></entry>
</feed>"""


def test_reddit_post_feed():
    assert pages.reddit_feed_url("https://old.reddit.com/r/selfhosted/comments/1X0EU16/x/?utm=1") == \
        "https://www.reddit.com/r/selfhosted/comments/1x0eu16/.rss"
    assert pages.reddit_feed_url("https://www.reddit.com/r/selfhosted/s/WrTYQxK8Ti") is None
    assert pages.reddit_feed_url("https://example.com/r/selfhosted/comments/1x0eu16/") is None
    d = pages.reddit_feed(REDDIT_FEED)
    t = d["text"]
    assert d["title"] == "Self-hosted Photoshop and Acrobat Pro?" and d["published"].startswith("2026-10-08")
    assert "r/selfhosted · /u/alex_ops" in t and "Looking for alternatives." in t and "submitted by" not in t
    assert t.index("/u/first:\nStirling PDF") < t.index("/u/second:\nPhotopea.")
    assert pages.reddit_feed("<feed/>") is None


# ---------------------------------------------------------------- search adapters


def test_jina_and_firecrawl_search_adapters():
    j = adapters.ADAPTERS["jina"]
    req = j.request("kernel", 5, None, "jk")
    assert req.path == "/" and req.params == {"q": "kernel"} and req.headers["Authorization"] == "Bearer jk"
    assert req.headers["X-Respond-With"] == "no-content" and j.needs_key
    rs = j.parse({"code": 200, "data": [{"title": "K", "url": "https://kernel.org", "description": "d"}, {"title": "x"}]})
    assert [r.url for r in rs] == ["https://kernel.org"] and rs[0].snippet == "d"
    f = adapters.ADAPTERS["firecrawl"]
    req = f.request("kernel", 30, "week", None)
    assert req.json == {"query": "kernel", "limit": 20, "tbs": "qdr:w"} and "Authorization" not in req.headers
    assert f.parse({"success": True, "data": [{"url": "https://a.example", "title": "A", "description": "s"}]})[0].title == "A"
    with pytest.raises(adapters.AdapterError, match="Insufficient credits"):
        f.parse({"success": False, "error": "Insufficient credits"})


# ---------------------------------------------------------------- admin


async def test_admin_readers_crud_and_test(admin_client, monkeypatch):
    _lan_ok(monkeypatch)
    _egress(monkeypatch)
    r = await admin_client.get("/api/v1/admin/websearch")
    assert {k["kind"] for k in r.json()["reader_kinds"]} == {"browserless", "firecrawl", "jina"}
    assert {"jina", "firecrawl"} <= {k["kind"] for k in r.json()["kinds"]}
    r = await admin_client.post("/api/v1/admin/websearch/providers", json={
        "name": "Browser", "purpose": "reader", "kind": "browserless", "base_url": "http://browserless.lan:3000",
        "network": "local", "api_key": "bl-secret-9", "timeout_s": 30})
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    assert r.json()["purpose"] == "reader" and "bl-secret" not in r.text
    ov = (await admin_client.get("/api/v1/admin/websearch")).json()
    assert [x["id"] for x in ov["readers"]] == [rid] and rid not in [x["id"] for x in ov["providers"]]
    # A search kind is not a reader kind, and a row keeps its purpose.
    bad = await admin_client.post("/api/v1/admin/websearch/providers",
                                  json={"name": "X", "purpose": "reader", "kind": "exa"})
    assert bad.status_code == 400
    bad = await admin_client.patch(f"/api/v1/admin/websearch/providers/{rid}",
                                   json={"name": "Browser", "kind": "exa", "base_url": "https://api.exa.ai"})
    assert bad.status_code == 400
    # Readers never make web search available.
    async with sessionmaker()() as db:
        assert not await service.available(db)
    seen = _mock(monkeypatch, lambda req: httpx.Response(200, headers={"content-type": "text/html"}, text=PAGE))
    t = (await admin_client.post(f"/api/v1/admin/websearch/providers/{rid}/test", json={})).json()
    assert t["ok"] and t["page"]["title"] == "Self-hosted Photoshop" and t["via"] == "lan"
    assert json.loads(seen[-1].content)["url"] == "https://en.wikipedia.org/wiki/Email"
    t = (await admin_client.post("/api/v1/admin/websearch/test", json={
        "purpose": "reader", "kind": "jina", "network": "internet", "url": "https://example.com/"})).json()
    assert t["ok"] is False and "JSON" in t["error"]
    async with sessionmaker()() as db:
        assert (await db.get(WebSearchProvider, rid)).usage_count == 0
        await db.execute(delete(WebSearchProvider))
        await db.commit()
