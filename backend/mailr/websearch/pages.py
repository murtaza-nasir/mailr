"""Reading one web page as plain text (the assistant's `read_page`).

A page is content other people wrote, fetched because of a query that may quote mail, so it goes
out exactly like a fetch caused by a sender (core/egress.py): through the egress proxy when one is
set, never directly while MAILR_REQUIRE_EGRESS is on, https only, public addresses only, every
redirect hop checked again, at most MAX_HOPS hops. Nothing runs: no JavaScript, no images, no
stylesheets, no cookies, no referrer. HTML becomes text (scripts, styles, navigation, headers,
footers and forms removed; the page's <main> or <article> when it has one). The body is read with
a MAX_BYTES cap and the text is cut at MAX_CHARS.

This is the built-in reader, the first of the chain in readers.py: when it fails or finds almost
no text, the page readers an admin registered are tried next.

Site rules. A Reddit post page (share links such as /r/<sub>/s/<code> redirect to it, and the
redirect is followed like any other) becomes the post and its first top-level comments
(reddit_post), read from the server-rendered markup.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from mailr.core import egress, netguard
from mailr.websearch.adapters import site_of

MAX_BYTES = 3 * 1024 * 1024
MAX_CHARS = 20_000
MAX_HOPS = 5
TIMEOUT = 15.0
USER_AGENT = "Mozilla/5.0 (compatible; mailr-reader)"
ACCEPT = "text/html,application/xhtml+xml,text/plain;q=0.8"
_TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
_DROP = ("script", "style", "noscript", "template", "svg", "iframe", "object", "embed", "form", "nav", "header",
         "footer", "aside", "button", "select", "canvas", "dialog")


class PageError(Exception):
    """The page could not be read. The message is safe to show to users and models."""


def extract(html: str, content_type: str = "text/html") -> dict[str, Any]:
    """{title, description, published, text} from a page's markup (or plain text)."""
    if not content_type.startswith(("text/html", "application/xhtml")):
        return {"title": "", "description": "", "published": None, "text": _tidy(html)}
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")

    def meta(*names: str) -> str:
        for n in names:
            tag = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
            if tag is not None and tag.get("content"):
                return " ".join(str(tag["content"]).split())
        return ""

    title = meta("og:title") or (" ".join(soup.title.get_text().split()) if soup.title else "")
    description = meta("og:description", "description")
    published = meta("article:published_time", "datePublished", "date") or None
    post = reddit_post(soup)
    if post is not None:
        return {"title": (post["title"] or title)[:300], "description": description[:500],
                "published": post["published"] or published, "text": post["text"]}
    for tag in soup(_DROP):
        tag.decompose()
    root = None
    for cand in (soup.find("main"), soup.find("article"), soup.find(attrs={"role": "main"})):
        if cand is not None and len(cand.get_text(" ", strip=True)) > 200:
            root = cand
            break
    root = root or soup.body or soup
    for br in root.find_all("br"):
        br.replace_with("\n")
    text = root.get_text("\n")
    return {"title": title[:300], "description": description[:500], "published": published, "text": _tidy(text)}


REDDIT_COMMENTS = 10
REDDIT_COMMENT_CHARS = 1500


def _md_text(el) -> str:
    """The text of a block of Reddit's rendered Markdown, one line per paragraph or list item."""
    if el is None:
        return ""
    for br in el.find_all("br"):
        br.replace_with("\n")
    for a in el.find_all("a", href=True):
        href = str(a["href"])
        label = a.get_text(" ", strip=True)
        if href.startswith(("https://", "http://")) and href not in label:
            a.replace_with(f"{label} ({href})" if label else href)
    return _tidy(el.get_text("\n"))


def reddit_post(soup) -> dict[str, Any] | None:
    """A Reddit post page (the server-rendered "shreddit" markup that www.reddit.com sends without
    JavaScript): the post's title, community, author, score, comment count, text or link, then
    the first REDDIT_COMMENTS top-level comments with their authors and scores. None for any
    other page."""
    post = soup.find("shreddit-post")
    if post is None:
        return None

    def attr(el, *names: str) -> str:
        for n in names:
            v = el.get(n)
            if v:
                return " ".join(str(v).split())
        return ""

    title = attr(post, "post-title")
    if not title:
        h = post.find(attrs={"slot": "title"}) or soup.find("h1")
        title = " ".join(h.get_text(" ").split()) if h is not None else ""
    sub = attr(post, "subreddit-prefixed-name", "subreddit-name")
    author = attr(post, "author")
    score = attr(post, "score")
    ncomments = attr(post, "comment-count")
    created = attr(post, "created-timestamp") or None
    facts = [x for x in (sub, f"u/{author}" if author else "", f"score {score}" if score else "",
                         f"{ncomments} comments" if ncomments else "", (created or "")[:10]) if x]
    lines = [title, " · ".join(facts)]
    body = post.find(attrs={"slot": "text-body"})
    text = _md_text(body.find(class_="md") or body) if body is not None else ""
    if text:
        lines += ["", text]
    link = attr(post, "content-href")
    if link and link.startswith(("https://", "http://")) and "reddit.com/r/" not in link:
        lines += ["", f"Link: {link}"]
    comments = [c for c in soup.find_all("shreddit-comment") if attr(c, "depth") in ("", "0")]
    shown = []
    for c in comments:
        box = c.find(attrs={"slot": "comment"})
        ctext = _md_text(box.find(class_="md") or box) if box is not None else ""
        if not ctext:
            continue
        if len(ctext) > REDDIT_COMMENT_CHARS:
            ctext = ctext[:REDDIT_COMMENT_CHARS].rsplit(" ", 1)[0] + " …"
        who = attr(c, "author") or "[deleted]"
        cs = attr(c, "score")
        shown.append(f"u/{who}" + (f" (score {cs})" if cs else "") + ":\n" + ctext)
        if len(shown) >= REDDIT_COMMENTS:
            break
    if shown:
        lines += ["", "Top comments:", ""]
        lines += ["\n\n".join(shown)]
    return {"title": title, "published": created, "text": _tidy("\n".join(lines))}


_FEED_TYPES = ("application/atom+xml", "application/rss+xml", "application/xml", "text/xml")
_REDDIT_POST = re.compile(r"^https://(?:www\.|old\.|new\.)?reddit\.com/r/([A-Za-z0-9_]{2,21})/comments/([a-z0-9]{3,12})(?:/[^?#]*)?(?:[?#].*)?$",
                          re.IGNORECASE)


def reddit_feed_url(url: str) -> str | None:
    """A Reddit post's address as its Atom feed (the post and its comments). Reddit refuses its post
    pages to readers and headless browsers but serves the feed (9 Oct 2026). None for other pages,
    and for share links (/r/<sub>/s/<code>), which become post addresses after their redirect."""
    m = _REDDIT_POST.match((url or "").strip())
    if not m:
        return None
    return f"https://www.reddit.com/r/{m.group(1)}/comments/{m.group(2).lower()}/.rss"


def reddit_feed(xml: str) -> dict[str, Any] | None:
    """A Reddit post's Atom feed as {title, description, published, text}: the post (first entry)
    with its community and author, then up to REDDIT_COMMENTS comments with their authors."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(xml, "xml")
    entries = soup.find_all("entry")
    if not entries:
        return None

    def txt(el, name: str) -> str:
        t = el.find(name)
        return " ".join(t.get_text(" ").split()) if t is not None else ""

    def body(el) -> str:
        c = el.find("content")
        if c is None:
            return ""
        html = BeautifulSoup(c.get_text(), "lxml")
        for tag in html.find_all("table"):  # the feed's thumbnail table
            tag.decompose()
        md = html.find(class_="md")
        return _md_text(md or html.body or html)

    post = entries[0]
    title = txt(post, "title")
    author = txt(post.find("author") or post, "name")
    cat = post.find("category")
    sub = f"r/{cat.get('term')}" if cat is not None and cat.get("term") else ""
    published = txt(post, "published") or txt(post, "updated") or None
    lines = [title, " · ".join(x for x in (sub, author, (published or "")[:10]) if x)]
    text = body(post)
    text = re.sub(r"\s*submitted by\s.*$", "", text, flags=re.S).strip()
    if text:
        lines += ["", text]
    shown = []
    for e in entries[1:]:
        ctext = body(e)
        if not ctext:
            continue
        if len(ctext) > REDDIT_COMMENT_CHARS:
            ctext = ctext[:REDDIT_COMMENT_CHARS].rsplit(" ", 1)[0] + " …"
        who = txt(e.find("author") or e, "name") or "[deleted]"
        shown.append(f"{who}:\n{ctext}")
        if len(shown) >= REDDIT_COMMENTS:
            break
    if shown:
        lines += ["", "Comments:", "", "\n\n".join(shown)]
    return {"title": title[:300], "description": "", "published": published, "text": _tidy("\n".join(lines))}


def _tidy(text: str) -> str:
    lines = [" ".join(ln.split()) for ln in (text or "").splitlines()]
    out: list[str] = []
    for ln in lines:
        if not ln and (not out or not out[-1]):
            continue
        out.append(ln)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _charset(r: httpx.Response, body: bytes) -> str:
    enc = r.charset_encoding
    if not enc:
        m = re.search(rb"<meta[^>]+charset=[\"']?([A-Za-z0-9_-]{2,30})", body[:4096], re.IGNORECASE)
        enc = m.group(1).decode() if m else "utf-8"
    return enc


async def read(db, url: str, *, max_chars: int = MAX_CHARS) -> dict[str, Any]:
    """The page at `url` as text: {url, final_url, site, title, description, published, text,
    truncated, chars, via}."""
    url = (url or "").strip()
    if not url.lower().startswith("https://"):
        raise PageError("only https:// pages are read")
    try:
        eg = await egress.gate(await egress.configured(db))
    except egress.EgressUnavailable as e:
        raise PageError(str(e).replace("Remote content is paused", "Reading web pages is paused")) from None
    hop_url = reddit_feed_url(url) or url
    try:
        async with egress.client(eg, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT, "Accept": ACCEPT}) as c:
            for _ in range(MAX_HOPS + 1):
                await egress.check_url(hop_url, eg)
                r, body = await netguard.fetch_capped(c, "GET", hop_url, limit=MAX_BYTES, deadline=TIMEOUT * 2)
                if r.is_redirect and r.headers.get("location"):
                    hop_url = urljoin(hop_url, r.headers["location"])
                    feed = reddit_feed_url(hop_url)
                    if feed:
                        hop_url = feed
                    continue
                break
            else:
                raise PageError(f"the page redirected more than {MAX_HOPS} times")
    except netguard.UnsafeTarget as e:
        raise PageError(f"not read: {e}") from None
    except netguard.TooLarge:
        raise PageError(f"the page is larger than {MAX_BYTES // (1024 * 1024)} MB") from None
    except TimeoutError:
        raise PageError("the page did not load in time") from None
    except (httpx.HTTPError, OSError) as e:
        raise PageError(f"the page could not be loaded ({type(e).__name__})") from None
    if r.status_code != 200:
        raise PageError(f"the site answered HTTP {r.status_code}")
    ctype = (r.headers.get("content-type") or "text/html").split(";")[0].strip().lower()
    if ctype in _FEED_TYPES and reddit_feed_url(hop_url) == hop_url:
        doc = reddit_feed(body.decode(_charset(r, body), errors="replace"))
        if doc is None:
            raise PageError("the Reddit post could not be read")
        return _result(url, hop_url, doc, max_chars, eg, r.status_code)
    if not ctype.startswith(_TEXT_TYPES):
        raise PageError(f"not a web page ({ctype}); only HTML and plain text are read")
    doc = extract(body.decode(_charset(r, body), errors="replace"), ctype)
    return _result(url, hop_url, doc, max_chars, eg, r.status_code)


def _result(url: str, hop_url: str, doc: dict[str, Any], max_chars: int, eg: Any, status: int) -> dict[str, Any]:
    text = doc["text"]
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars].rsplit(" ", 1)[0] + " …"
    return {"url": url, "final_url": hop_url, "site": site_of(hop_url), "title": doc["title"] or site_of(hop_url),
            "description": doc["description"], "published": doc["published"], "text": text,
            "truncated": truncated, "chars": len(doc["text"]), "via": "egress" if eg else "direct",
            "route": "egress" if eg else "direct", "status": status}


def same_page(a: str, b: str) -> bool:
    """Two URLs of the same page (scheme, host without www, path without a trailing slash, query)."""
    def k(u: str) -> tuple[str, str, str]:
        s = urlsplit((u or "").strip())
        return ((s.hostname or "").lower().removeprefix("www."), s.path.rstrip("/") or "/", s.query)
    return k(a) == k(b)
