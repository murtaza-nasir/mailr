"""List rows: attachment chips. The same file sent twice in a thread is one chip with a count."""

import uuid

from conftest import mail_date
from helpers.core_flow import _ingest


def _mail(subject, msgid, files, irt=None, date=mail_date()):
    b = "b0"
    parts = [f"--{b}\r\nContent-Type: text/plain\r\n\r\nSee attached.\r\n"]
    for name, ctype, data in files:
        parts.append(f"--{b}\r\nContent-Type: {ctype}; name=\"{name}\"\r\nContent-Disposition: attachment; "
                     f"filename=\"{name}\"\r\nContent-Transfer-Encoding: base64\r\n\r\n{data}\r\n")
    thread = f"In-Reply-To: {irt}\r\nReferences: {irt}\r\n" if irt else ""
    head = (f"From: Jordan Lee <jordan@example.org>\r\nTo: me@example.com\r\nSubject: {subject}\r\n"
            f"Message-ID: {msgid}\r\n{thread}Date: {date}\r\nMIME-Version: 1.0\r\n"
            f"Content-Type: multipart/mixed; boundary=\"{b}\"\r\n\r\n")
    return (head + "".join(parts) + f"--{b}--\r\n").encode()


async def test_same_file_twice_is_one_chip_with_a_count(client, user):
    first, second = f"<{uuid.uuid4().hex}@x.org>", f"<{uuid.uuid4().hex}@x.org>"
    await _ingest(user, _mail("Planning", first, [("invite.ics", "text/calendar", "aGVsbG8="),
                                                  ("agenda.pdf", "application/pdf", "aGVsbG8=")]), roles=["inbox"])
    # The same name with other bytes (another size) is another file.
    await _ingest(user, _mail("Re: Planning", second, [("invite.ics", "text/calendar", "aGVsbG8="),
                                                       ("agenda.pdf", "application/pdf", "aGVsbG8gYWdhaW4=")],
                              irt=first, date=mail_date(0, "11:00")), roles=["inbox"])
    items = (await client.get("/api/v1/conversations", params={"view": "inbox"})).json()["items"]
    row = next(c for c in items if c["subject"] == "Planning")
    assert row["message_count"] == 2
    assert [(a["filename"], a["n"]) for a in row["attachments_preview"]] == [
        ("invite.ics", 2), ("agenda.pdf", 1), ("agenda.pdf", 1)]
