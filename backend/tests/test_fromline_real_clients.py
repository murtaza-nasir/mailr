# ACT-SURFACES: api
"""The strict From reader (core/fromline.py) must read the From lines that real mail clients and
senders write as one plain address, and keep flagging the attack shapes.
"""

from __future__ import annotations

import pytest

from mailr.core import fromline, mime
from mailr.core.trusted import check_headers

WORK = "work.example"

# Real From lines that must read cleanly: (header, the address read).
REAL = [
    # obs-phrase: a period in an unquoted display name (RFC 5322 section 4.1)
    ("Booking.com <noreply@booking.com>", "noreply@booking.com"),
    ("Dr. Jane Doe <jane@example.com>", "jane@example.com"),
    ("J. Smith <j@example.com>", "j@example.com"),
    ("U.S. Bank <alerts@usbank.example>", "alerts@usbank.example"),
    ("Mr. & Mrs. Smith <smith@example.com>", "smith@example.com"),
    ("St. John's Hospital <info@example.org>", "info@example.org"),
    ("Amazon.com <shipment-tracking@amazon.example>", "shipment-tracking@amazon.example"),
    ("Jane Doe Jr. <jane@example.com>", "jane@example.com"),
    ("J.  R.  R. Tolkien <jrr@example.com>", "jrr@example.com"),
    # quoted names, commas inside quotes, quoted-pair
    ('"Doe, Jane" <jane@example.com>', "jane@example.com"),
    ('"Public, Jane Q., Jr." <jane@example.com>', "jane@example.com"),
    ('"Jane \\"JD\\" Doe" <jane@example.com>', "jane@example.com"),
    ('"Amazon.com" <shipment-tracking@amazon.example>', "shipment-tracking@amazon.example"),
    ('"Jane.Doe@example.com" <jane.doe@example.com>', "jane.doe@example.com"),  # same address in the name
    ('"" <jane@example.com>', "jane@example.com"),
    # encoded words (Thunderbird, Outlook, Apple Mail, Gmail for non-ASCII names)
    ("=?UTF-8?B?SsO8cmdlbiBNw7xsbGVy?= <j@example.de>", "j@example.de"),
    ("=?utf-8?q?Fran=C3=A7ois_Dupont?= <f@example.fr>", "f@example.fr"),
    ("=?UTF-8?Q?Amazon.com?= <a@amazon.example>", "a@amazon.example"),
    ("=?UTF-8?Q?Jane_O=27Brien?= <jane@example.com>", "jane@example.com"),
    ("=?iso-8859-1?q?Andr=E9?= Pirard <pirard@example.be>", "pirard@example.be"),
    ("=?ISO-2022-JP?B?GyRCJUYlOSVIGyhC?= <t@example.jp>", "t@example.jp"),
    # apostrophes, parentheses, comments
    ("Pat O'Brien <pat@example.com>", "pat@example.com"),
    ("Jane Doe (Marketing) <jane@example.com>", "jane@example.com"),
    ("jane@example.com (Jane Doe)", "jane@example.com"),
    ('"Jane Doe (via Slack)" <feedback@slack.example>', "feedback@slack.example"),
    # Unicode names (RFC 6532)
    ("Jürgen Müller <j@example.de>", "j@example.de"),
    ("张伟 <z@example.cn>", "z@example.cn"),
    ("Zoë Saldaña <zoe@example.com>", "zoe@example.com"),
    # bare and angle-only addresses, plus-addresses, IDN, case, spacing
    ("jane@example.com", "jane@example.com"),
    ("<jane@example.com>", "jane@example.com"),
    ("Jane Doe <jane+news@example.com>", "jane+news@example.com"),
    ("Jane Doe <jane@xn--bcher-kva.example>", "jane@xn--bcher-kva.example"),
    ("Jane Doe <JANE@Example.COM>", "jane@example.com"),
    ("Jane Doe<jane@example.com>", "jane@example.com"),
    ("Jane  Doe   <jane@example.com>", "jane@example.com"),
    # service senders
    ("Jane Doe via Google Docs <drive-shares-noreply@google.example>", "drive-shares-noreply@google.example"),
    ('"Microsoft account team" <account-security-noreply@accountprotection.example>',
     "account-security-noreply@accountprotection.example"),
    # a group holding one address
    ("Team: Jane <jane@example.com>;", "jane@example.com"),
]


@pytest.mark.parametrize(("header", "addr"), REAL)
def test_real_from_lines_are_not_flagged(header, addr):
    got = fromline.read([header])
    assert got.problem == "", (header, got)
    assert got.email == addr


@pytest.mark.parametrize(("header", "addr"), REAL)
def test_real_from_lines_verify_and_pass_the_header_check(header, addr):
    raw = (f"Authentication-Results: mx.example; dkim=pass header.d={addr.split('@')[1]}\r\n"
           f"From: {header}\r\nTo: x@{WORK}\r\nSubject: s\r\nMessage-ID: <a@b>\r\n"
           f"Content-Type: text/plain; charset=utf-8\r\n\r\nhi\r\n").encode()
    p = mime.parse(raw)
    assert "from_problem" not in p.auth_results
    assert check_headers(raw, addr) == ""


def test_empty_group_is_missing_not_malformed():
    got = fromline.read(["undisclosed-recipients:;"])
    assert got.problem == "missing" and got.email == ""
    raw = b"From: undisclosed-recipients:;\r\nTo: x@work.example\r\nSubject: s\r\n\r\nhi\r\n"
    assert "from_problem" not in mime.parse(raw).auth_results  # no warning; still verifies nobody


# The attack shapes and the obsolete forms that touch the address itself stay flagged.
ATTACK = [
    f"boss@{WORK} <student@{WORK}>",
    f"boss@{WORK}.<student@{WORK}>",
    f"<boss@{WORK}>student@{WORK}",
    f'"boss@{WORK}" <student@{WORK}>',
    f"=?utf-8?q?boss=40{WORK}?= <student@{WORK}>",
    f"Boss <boss@{WORK}> <student@{WORK}>",
    f"boss@{WORK} (x) <student@{WORK}>",
    f"boss@{WORK} student@{WORK}",
    f"Boss. <boss@{WORK}",
    f"Boss <boss@{WORK}>junk",
    f"<@evil.example:boss@{WORK}>",  # obs-route
    f"Boss <@evil.example:boss@{WORK}>",
    f"boss . x@{WORK}",  # obs-local-part with white space
    f"Boss <boss@{WORK}>, Other <o@{WORK}>",
    f"boss@{WORK}, student@{WORK}",
    f"Doe, Jane <jane@{WORK}>",  # unquoted comma: two entries to RFC 5322
]


@pytest.mark.parametrize("header", ATTACK)
def test_attack_shapes_stay_flagged(header):
    assert fromline.read([header]).problem in ("malformed", "several", "name_address"), header
