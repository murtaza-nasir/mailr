"""Regression tests for untrusted content: each test is an attack that must fail."""

import asyncio

import pytest


def _html(body: str) -> bytes:
    return ("From: a@b.c\r\nContent-Type: text/html\r\n\r\n" + body).encode()


def test_parse_never_raises_on_hostile_headers_or_nesting():
    from mailr.core.mime import parse

    p = parse(b"From: =?utf-8?b?eA0KeQ==?= <a@b.c>\r\nTo: =?utf-8?b?eA0KeQ==?= <d@e.f>\r\nSubject: s\r\n\r\nb\r\n")
    assert p.from_email == "a@b.c" and "\n" not in p.from_name and p.to[0]["email"] == "d@e.f"
    deep = b"From: a@b.c\r\nSubject: deep\r\nContent-Type: multipart/mixed; boundary=b0\r\n\r\n"
    for i in range(1200):
        deep += f"--b{i}\r\nContent-Type: multipart/mixed; boundary=b{i + 1}\r\n\r\n".encode()
    p = parse(deep)
    assert p.from_email == "a@b.c" and p.subject == "deep"


def test_css_escapes_cannot_close_the_style_element():
    from mailr.core.render import render_body

    for payload in ["<style>a{color:red}\\3c/style\\3e\\3cimg src=x onerror=alert(1)\\3e</style><p>x</p>",
                    '<style>\\3c/style\\3e\\3cmeta http-equiv=refresh content="0;url=https://evil/"\\3e</style>']:
        out = render_body(_html(payload), 1)["html"]
        style = out.split("<style>", 1)[1].split("</style>", 1)[0]
        assert "<" not in style, out
        assert "<img" not in out and "<meta" not in out
    assert "base-uri 'none'" in render_body(_html("<p>x</p>"), 1)["csp"]


def test_links_are_limited_to_absolute_web_mail_and_phone():
    from mailr.core.render import render_body

    out = render_body(_html('<a href="da\tta:text/html,x">d</a><a href="/api/v1/auth/logout">r</a>'
                            '<a href="//evil.com">p</a><a href="https://ok.example/">ok</a>'
                            '<a href="mailto:a@b.c">m</a>'), 1)["html"]
    assert out.count("href=") == 2
    assert 'href="https://ok.example/"' in out and 'href="mailto:a@b.c"' in out


def test_outlook_separators_are_linear():
    import time

    from mailr.core.render import render_body

    def took(n: int) -> float:
        body = "".join('<div id="divRplyFwdMsg">x</div>' for _ in range(n))
        t = time.perf_counter()
        render_body(_html(body), 1)
        return time.perf_counter() - t

    # Four times the separators: about four times the time when linear, sixteen when quadratic (the
    # old loop took over a minute at 40,000). A ratio, so a slow machine does not fail the test.
    small, large = took(10000), took(40000)
    assert large < 8 * max(small, 0.05), (small, large)


def test_sent_by_me_needs_dmarc_or_aligned_dkim():
    from mailr.core.ingest import IngestOptions, is_sent_by_me
    from mailr.core.mime import parse

    def mine(ar):
        raw = f"From: Me <me@example.org>\r\nAuthentication-Results: mx; {ar}\r\nSubject: x\r\n\r\nb".encode()
        return is_sent_by_me(parse(raw), {"me@example.org"}, IngestOptions())

    assert mine("spf=pass; dkim=pass header.d=evil.example; dmarc=fail") is False
    assert mine("dkim=pass header.d=example.org") is True
    assert mine("dmarc=pass header.from=example.org") is True


def test_search_is_bounded():
    from mailr.core.search import compile_query

    with pytest.raises(ValueError):
        compile_query(" OR ".join(f"w{i}" for i in range(500)), 1, [])
    with pytest.raises(ValueError):
        compile_query("(" * 50 + "x" + ")" * 50, 1, [])


def test_urls_from_mail_are_https_and_public_only():
    from mailr.core.netguard import UnsafeTarget, check_untrusted_url

    for bad in ["http://example.com/", "https://10.0.0.1/", "https://192.168.1.5/", "https://[2002:7f00:1::]/",
                "https://127.0.0.1/"]:
        with pytest.raises(UnsafeTarget):
            asyncio.run(check_untrusted_url(bad))


async def test_unknown_api_paths_are_404(client):
    r = await client.get("/api/v1/definitely-not-a-route")
    assert r.status_code == 404
    assert "text/html" not in r.headers.get("content-type", "")


def test_remote_images_are_upgraded_to_https_when_shown():
    from mailr.core.render import render_body

    html = ('<img src="http://img.example.com/a.png"><img src="//img.example.com/b.png">'
            '<table><tr><td background="http://img.example.com/bg.png">y</td></tr></table>'
            '<div style="background:url(http://img.example.com/c.png)">x</div>')
    shown = render_body(_html(html), 1, allow_remote=True)["html"]
    assert "http://" not in shown and shown.count("https://img.example.com/") == 4, shown
    blocked = render_body(_html(html), 1)["html"]
    assert "https://img.example.com" not in blocked


def test_answer_links_must_come_from_what_the_model_read():
    from mailr.intel.citations import verify_links

    tail_a = "Rk7Tq2mWz_P4vLn8QsYd3Hc0XbJf6GtUa1EoNiKr5ZyMw9VlC2pDhSx7BqFj0TgOuW3eAnLc8IzRm4Ks6YdfxPYIlJTCFlAwLyK"
    tail_b = "Rk7Tq2mWz_P4vLn8QsYd3Hc0XbJf6GtUa1EoNiKr5ZyMw9VlC2pDhSx7BqFj0TgOuW3eAnLc8IzRm4Ks6Ydf29sCK8qTH57syU"
    real_a = f"https://tracking.example.com/f/a/q3Lr8xWb2TnPd0KeY7cZuA~~/AAAbCdE~/{tail_a}"
    real_b = f"https://tracking.example.com/f/a/Zp1Hc5RvJ9mXo2QsLt4BeQ~~/AAAbCdE~/{tail_b}"
    source = f"Data Analyst\n{real_a}\nOperations Analyst\n{real_b}\n"
    # The model kept the right start but copied one tail onto both links.
    wrong_b = real_b.replace(tail_b, tail_a + "Q")
    answer = f"- Data [apply]({real_a})\n- Operations [apply]({wrong_b})\n- Other [apply](https://evil.example/x)"
    out = verify_links(answer, source)
    assert f"[apply]({real_a})" in out and f"[apply]({real_b})" in out
    assert "evil.example" not in out and "- Other apply" in out
    # A bare URL that is not in the source is not left clickable.
    assert "https://" not in verify_links("see https://unknown.example/page", source)
    # An exact bare URL survives.
    assert real_a in verify_links(f"see {real_a}", source)


def test_answers_name_emails_by_link_not_by_internal_id():
    from mailr.intel.citations import humanize_ids

    known = ('{"conversation_id": 19867, "message_id": 92773, "subject": "19 New Jobs"}\n'
             "- Open conversation 20001\n    - 20002: 'Lunch?', from Carol")
    out = humanize_ids("I pulled the brief from conversation 19867 (the job alert), see message #92773 "
                       "and thread 20002; the salary is $81,149 and the order 123456 shipped (ID: 20001).", known)
    assert "[[c:19867]]" in out and "[[m:92773]]" in out and "[[c:20002]]" in out
    assert "$81,149" in out and "order 123456" in out and "20001)" not in out
    # Unknown numbers stay text, even after "conversation": the model never saw them as ids.
    assert humanize_ids("conversation 555 and message 19867", known) == "conversation 555 and message 19867"


def test_a_drafts_links_show_where_they_go():
    import mailr.agent.tools  # noqa: F401  (loads draft_tools in its order)
    from mailr.agent.draft_tools import agent_html

    md = agent_html("Log in at [your bank portal](https://login.bank.example.net/) or [bank.example](https://other.example/).")
    assert '<a href="https://login.bank.example.net/">your bank portal</a> (https://login.bank.example.net/)' in md
    assert '<a href="https://other.example/">bank.example</a> (https://other.example/)' in md
    raw = agent_html('<p>Pay <a href="https://other.example/pay">https://bank.example/pay</a> now</p>')
    assert "(https://other.example/pay)" in raw
    # A link that is its own address, or already shows it, stays as it is.
    same = agent_html("See https://docs.example/a and [docs.example/a](https://docs.example/a/).")
    assert "(" not in same
    twice = agent_html("[portal](https://login.example/) (https://login.example/)")
    assert twice.count("https://login.example/") == 3  # the href, the shown address and its link


def test_the_markdown_converter_takes_hostile_text_in_linear_time():
    """Unclosed marks on one long line, and deep lists or quotes, cost time in proportion to the
    text and never recurse without end (a crafted recording summary or an injected draft)."""
    import time

    from mailr.core import mdmail

    for hostile in (" **a" * 20000, " __a" * 20000, " [a" * 20000, " **[a _b `c" * 8000,
                    "".join(" " * i + "- a\n" for i in range(3000)), ">" * 5000 + " a",
                    "\n".join(">" * (i % 50) + " a" for i in range(5000))):
        t = time.perf_counter()
        mdmail.to_html(hostile)
        mdmail.to_text(hostile)
        assert time.perf_counter() - t < 2, hostile[:20]
    # Ordinary marks still work, and text past the parsed part is kept.
    assert "<strong>bold</strong>" in mdmail.to_html("a **bold** word")
    long = "line\n" * 50000 + "**the end**"
    assert "**the end**" in mdmail.to_html(long) and mdmail.to_text(long).endswith("**the end**")
