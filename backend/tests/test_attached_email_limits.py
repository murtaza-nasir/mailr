"""Attached emails (core/mime.py, message/rfc822): the depth,
count and size limits hold, and broken input never raises.

Each test passes while the protection holds."""

from __future__ import annotations

import random
import uuid

import pytest

from mailr.core import mime


def _email(subject: str, body: str, attached: list[bytes] = (), boundary: str | None = None) -> bytes:
    head = (f"From: Sender <sender@example.org>\r\nTo: you@example.org\r\nSubject: {subject}\r\n"
            "Date: Mon, 05 Oct 2026 10:00:00 +0000\r\nMIME-Version: 1.0\r\n")
    if not attached:
        return (head + "Content-Type: text/plain; charset=utf-8\r\n\r\n" + body + "\r\n").encode()
    b = boundary or f"b-{subject.replace(' ', '-')}"
    out = head + f'Content-Type: multipart/mixed; boundary="{b}"\r\n\r\n'
    out += f"--{b}\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{body}\r\n"
    data = out.encode()
    for a in attached:
        data += f"--{b}\r\nContent-Type: message/rfc822\r\n\r\n".encode() + a + b"\r\n"
    return data + f"--{b}--\r\n".encode()


def _chain(levels: int) -> bytes:
    msg = _email(f"level-{levels}", f"MARKER{levels} deepest")
    for n in range(levels - 1, -1, -1):
        msg = _email(f"level-{n}", f"MARKER{n} text", [msg])
    return msg


def test_attached_emails_are_read_four_levels_deep_and_no_deeper():
    p = mime.parse(_chain(7))
    assert "MARKER0" in p.body_text
    for n in (1, 2, 3, 4):
        assert f"MARKER{n}" in p.attached_text, n
    for n in (5, 6, 7):
        assert f"MARKER{n}" not in p.attached_text and f"MARKER{n}" not in p.body_text, n
    assert mime.MAX_ATTACHED_DEPTH == 4


def test_at_most_fifty_attached_emails_are_summarised():
    p = mime.parse(_email("outer", "outer body", [_email(f"inner {i}", f"text {i}") for i in range(60)]))
    emails = [a for a in p.attachments if a["type"] == "message/rfc822"]
    assert len(emails) == 60
    assert sum(1 for a in emails if "email" in a) == 50 == mime.MAX_ATTACHED_EMAILS
    assert all(a["filename"].endswith(".eml") for a in emails)


def test_attached_text_stops_at_thirty_thousand_characters():
    big = [_email(f"inner {i}", f"word{i} " * 2000) for i in range(10)]
    p = mime.parse(_email("outer", "outer body", big))
    assert len(p.attached_text) <= mime.MAX_ATTACHED_TEXT == 30_000
    assert len(p.body_text) <= mime.MAX_BODY


def test_summaries_are_bounded():
    inner = _email("S" * 5000, "B" * 5000).replace(b"Sender <sender@example.org>", b'"' + b"N" * 5000 + b'" <x@example.org>')
    p = mime.parse(_email("outer", "x", [inner]))
    e = p.attachments[0]["email"]
    assert len(e["subject"]) <= 300 and len(e["from_name"]) <= 200 and len(e["excerpt"]) <= mime.EXCERPT
    assert len(p.attachments[0]["filename"]) <= 125


def test_a_wide_and_deep_tree_parses_quickly(monkeypatch):
    """Each level may hold 50 summarised emails; the work stays bounded by the bytes present: each
    attached email is parsed once, and each byte is parsed at most once per level it sits under."""
    leaf = [_email(f"leaf {i}", "leaf text") for i in range(50)]
    mid = [_email(f"mid {i}", "mid", leaf) for i in range(8)]
    raw = _email("outer", "outer", [_email("top", "top", mid)])
    parsed: list[int] = []
    real = mime._parse

    def counted(data, depth=0):
        parsed.append(len(data))
        return real(data, depth)

    monkeypatch.setattr(mime, "_parse", counted)
    p = mime.parse(raw)
    assert p.attachments[0]["email"]["subject"] == "top"
    assert len(parsed) <= 1 + 1 + 8 + 8 * 50, len(parsed)
    assert sum(parsed) <= (mime.MAX_ATTACHED_DEPTH + 1) * len(raw), (sum(parsed), len(raw))


def test_deep_mime_inside_an_attached_email_is_not_parsed():
    inner = b"Subject: deep\r\nContent-Type: multipart/mixed; boundary=a0\r\n\r\n"
    for i in range(200):
        inner += f"--a{i}\r\nContent-Type: multipart/mixed; boundary=a{i + 1}\r\n\r\n".encode()
    p = mime.parse(_email("outer", "outer", [inner]))  # read from its headers and raw text only
    assert p.subject == "outer" and not any("email" in a for a in p.attachments)


BROKEN = [
    b"",
    b"\x00\xff\xfe",
    b"Content-Type: message/rfc822\r\n\r\n",
    _email("x", "y").replace(b"Content-Type: text/plain", b"Content-Type: message/rfc822"),
    (b"Content-Type: multipart/mixed; boundary=X\r\n\r\n--X\r\nContent-Type: message/rfc822\r\n"
     b"Content-Transfer-Encoding: base64\r\n\r\n!!!!not base64\r\n--X--\r\n"),
    (b"Content-Type: multipart/mixed; boundary=X\r\n\r\n--X\r\nContent-Type: message/rfc822\r\n"
     b"Content-Transfer-Encoding: quoted-printable\r\n\r\n=ZZ=\r\n--X--\r\n"),
    b"Content-Type: multipart/digest; boundary=X\r\n\r\n--X\r\n\r\nSubject: digest part\r\n\r\nbody\r\n--X--\r\n",
    (b"Content-Type: multipart/mixed; boundary=X\r\n\r\n--X\r\nContent-Type: message/rfc822\r\n\r\n"
     b"From: \"\\\r\n =?utf-8?q?=FF?= <\r\nTo: ,,,@@\r\nSubject: =?bogus?x?zz?=\r\nDate: not a date\r\n\r\nb\r\n--X--\r\n"),
    b"Content-Type: multipart/mixed; boundary=X\r\n\r\n--X\r\nContent-Type: message/global\r\n\r\n"
    + "Subject: ünïcödé\r\n\r\nbödy".encode() + b"\r\n--X--\r\n",
]


def _mutations(seed_msgs: list[bytes], n: int) -> list[bytes]:
    rng = random.Random(4)  # fixed: the same inputs on every run
    out = []
    for i in range(n):
        m = bytearray(seed_msgs[i % len(seed_msgs)])
        for _ in range(rng.randint(1, 8)):
            if not m:
                break
            pos = rng.randrange(len(m))
            op = rng.random()
            if op < 0.4:
                m[pos] = rng.randrange(256)
            elif op < 0.7:
                del m[pos:pos + rng.randint(1, 20)]
            else:
                m[pos:pos] = rng.choice([b"\r\n", b"--", b"=?", b"boundary=", b"message/rfc822", b"\x00", b";"])
        out.append(bytes(m))
    return out


def test_broken_attached_emails_never_raise():
    seeds = [_chain(3), _email("o", "b", [_email("i", "t")]), *BROKEN]
    for raw in [*BROKEN, *_mutations(seeds, 300)]:
        p = mime.parse(raw)  # never raises
        assert isinstance(p.attachments, list)
        for idx in range(8):
            try:
                mime.get_part(raw, idx)  # the download: never raises either
            except KeyError:
                pass


# An attached email with a raw 8-bit byte in a structured header (MIME-Version here); an outside
# sender writes this, and older mailers send 8-bit headers on their own.
EIGHT_BIT = (b"From: a@example.org\r\nSubject: outer\r\nMIME-Version: 1.0\r\n"
             b"Content-Type: multipart/mixed; boundary=X\r\n\r\n--X\r\nContent-Type: text/plain\r\n\r\nhi\r\n"
             b"--X\r\nContent-Type: message/rfc822\r\n\r\nFrom: b@example.org\r\nSubject: inner\r\n"
             b"MIME-Version: 1.0\xa7\r\nContent-Type: text/plain\r\n\r\ninner body\r\n--X--\r\n")


def test_an_attached_email_with_an_8bit_header_opens():
    assert mime.parse(EIGHT_BIT).attachments[0]["email"]["subject"] == "inner"
    data, _ = mime.attached_email_at(EIGHT_BIT, 2)
    assert b"inner body" in data and b"MIME-Version: 1.0\xa7" in data  # the bytes as they came
    assert b"inner body" in mime.get_part(EIGHT_BIT, 2)[0]


async def test_the_open_route_does_not_fail_on_an_8bit_header(client, user):
    from helpers.agent import _ingest

    mid, cid = await _ingest(user.id, EIGHT_BIT.replace(b"Subject: outer", b"Subject: outer " + uuid.uuid4().hex.encode()),
                             roles=["inbox"])
    conv = (await client.get(f"/api/v1/conversations/{cid}")).json()
    (a,) = [x for x in conv["messages"][0]["attachments"] if x["type"] == "message/rfc822"]
    try:
        r = await client.get(f"/api/v1/messages/{mid}/attachments/{a['part']}/email")
    except UnicodeError:
        pytest.fail("the route raised")
    assert r.status_code == 200, r.text
    assert r.json()["subject"] == "inner"
    d = await client.get(f"/api/v1/messages/{mid}/attachments/{a['part']}")  # the download
    assert d.status_code == 200 and b"inner body" in d.content
