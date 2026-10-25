from helpers.core_flow import _ingest

from mailr.core.search import _tokens


def test_operator_groups_distribute():
    assert _tokens("from:(a@x.example OR b@y.com)") == ["(", "from:a@x.example", "OR", "from:b@y.com", ")"]
    assert _tokens("from:{a b} is:unread") == ["{", "from:a", "from:b", "}", "is:unread"]
    assert _tokens("-to:(a -b)") == ["-", "(", "to:a", "-to:b", ")"]
    assert _tokens("from: hello") == ["hello"]  # a dangling operator no longer matches everything


async def _ids(client, q):
    r = (await client.get("/api/v1/conversations", params={"view": "all", "q": q})).json()
    return {c["id"] for c in r["items"]}


async def test_address_lists_and_to_me(client, user, raw):
    _, a = await _ingest(user, raw("From A", frm="A <a@x.org>", to=user.email))
    _, b = await _ingest(user, raw("From B", frm="B <b@y.org>", to="someone@else.org"))
    _, c = await _ingest(user, raw("From C", frm="C <c@z.org>", to=user.email))
    _, note = await _ingest(user, raw("Look at this later", frm=user.email, to=user.email,
                                  extra=f"Authentication-Results: mx.example.com; dkim=pass header.d={user.email.split('@')[1]}"))
    # A forged From: me (no passing authentication) is not "from:me".
    _, forged = await _ingest(user, raw("Urgent", frm=user.email, to=user.email))

    assert await _ids(client, "from:a@x.org,b@y.org") == {a, b}
    assert await _ids(client, "from:(a@x.org OR c@z.org)") == {a, c}
    assert await _ids(client, "{from:b@y.org from:c@z.org}") == {b, c}
    assert await _ids(client, "to:me") >= {a, c, note}
    assert b not in await _ids(client, "to:me")
    assert await _ids(client, "from:me to:me") == {note}
    assert forged in await _ids(client, "to:me")


def test_nul_bytes_are_stripped():
    from mailr.core.mime import parse
    raw = b"From: a@x.org\r\nSubject: bad\x00subject\r\nContent-Type: text/plain\r\n\r\nbody\x00text\r\n"
    p = parse(raw)
    assert p.subject == "badsubject" and "\x00" not in p.text and "\x00" not in p.body_text


def test_data_errors_are_per_message_not_outages():
    from sqlalchemy.exc import DBAPIError, OperationalError

    from mailr.connectors.imap import _transient

    class Orig(Exception):
        def __init__(self, sqlstate):
            self.sqlstate = sqlstate

    assert _transient(DBAPIError("INSERT", {}, Orig("22021"))) is False  # bad UTF-8 / NUL
    assert _transient(DBAPIError("INSERT", {}, Orig("23505"))) is False  # unique violation
    assert _transient(DBAPIError("INSERT", {}, Orig("57P01"))) is True   # admin shutdown
    assert _transient(OperationalError("x", {}, Orig("08006"))) is True


def test_attachment_type_sniffing():
    from mailr.api.mail_read import _effective_type

    assert _effective_type("application/octet-stream", "notice.pdf", b"%PDF-1.7") == "application/pdf"
    assert _effective_type("application/octet-stream", "photo.JPG", b"\xff\xd8") == "image/jpeg"
    assert _effective_type("application/octet-stream", "page.html", b"<html>") == "application/octet-stream"
    assert _effective_type("text/html", "x.html", b"") == "text/html"


def test_citation_quotes_may_contain_quotes_and_ellipses():
    from mailr.intel.citations import find_markers
    from mailr.intel.highlight import locate

    t = 'x [[m:11726 "wall: "Itna zor parhai par" said"]] y [[m:5 "plain"]] [[c:3]]'
    assert find_markers(t) == [("m", 11726, 'wall: "Itna zor parhai par" said'), ("m", 5, "plain"), ("c", 3, None)]
    body = "Just kidding.\nJokes on me\nsomething else\nA joke that I'm the punch line\nbye"
    m = locate(body, "Jokes on me… A joke that I'm the punch line")
    assert m is not None and body[m.start:m.end].startswith("Jokes on me") and body[m.start:m.end].endswith("punch line")
    assert m.score < 100
    assert locate(body, "Jokes on me... not in the message at all") is None


def test_recording_operators_compile():
    """has:recording and recording:<text> read the Speakr recording links (integrations/speakr/mail.py)."""
    from mailr.core.search import compile_query

    sql = str(compile_query("has:recording recording:budget -has:recordings", 1, []).where.compile())
    assert sql.count("recording_links") == 3 and "sr.title ILIKE" in sql
