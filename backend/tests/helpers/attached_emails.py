"""Shared helpers, from test_attached_emails.py."""

import uuid

from conftest import mail_date
from sqlalchemy.orm import undefer

from mailr.db import sessionmaker
from mailr.models import Message

INNER_BODY = "Quarterly zanzibarite numbers are attached to this forwarded note."


def _email(subject="Supplier quote", frm="Bob Inner <bob@inner.example>", body=INNER_BODY, extra="",
           html: str | None = None) -> bytes:
    head = (f"From: {frm}\r\nTo: Dana <dana@example.org>\r\nSubject: {subject}\r\n"
            f"Date: Mon, 5 Oct 2026 09:30:00 +0000\r\nMessage-ID: <{uuid.uuid4().hex}@inner.example>\r\n"
            f"MIME-Version: 1.0\r\n{extra}")
    if html is None:
        return (head + "Content-Type: text/plain; charset=utf-8\r\n\r\n" + body + "\r\n").encode()
    b = f"alt{uuid.uuid4().hex[:6]}"
    return (head + f"Content-Type: multipart/alternative; boundary=\"{b}\"\r\n\r\n"
            f"--{b}\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{body}\r\n"
            f"--{b}\r\nContent-Type: text/html; charset=utf-8\r\n\r\n{html}\r\n--{b}--\r\n").encode()


def _outer(parts: list[tuple[str, bytes]], body: str | None = "Please see the email below.",
           subject="Fwd: Supplier quote", frm="Alice <alice@example.org>", to="me@example.com",
           ctype="multipart/mixed") -> bytes:
    """parts: (headers of the part, its content)."""
    b = f"out{uuid.uuid4().hex[:8]}"
    out = (f"From: {frm}\r\nTo: {to}\r\nSubject: {subject}\r\nDate: {mail_date()}\r\n"
           f"Message-ID: <{uuid.uuid4().hex}@example.org>\r\nMIME-Version: 1.0\r\n"
           f"Content-Type: {ctype}; boundary=\"{b}\"\r\n\r\n").encode()
    if body is not None:
        out += f"--{b}\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{body}\r\n".encode()
    for head, content in parts:
        out += f"--{b}\r\n{head}\r\n".encode() + content + b"\r\n"
    return out + f"--{b}--\r\n".encode()


ATTACHED = 'Content-Type: message/rfc822\r\nContent-Disposition: attachment; filename="quote.eml"\r\n'


async def _as_stored_before(mid: int) -> None:
    """Make a message look as the old parser stored it: the attached email's text in the body,
    no attached-email entry, no attached text."""
    async with sessionmaker()() as db:
        m = await db.get(Message, mid, options=[undefer(Message.attached_text)])
        m.body_text = m.body_text + "\n" + INNER_BODY
        m.snippet = "Please see the email below. " + INNER_BODY
        m.attachments = [a for a in m.attachments if a["type"] != "message/rfc822"]
        m.attached_text = None
        await db.commit()
