"""Sending through an IMAP account's SMTP server (connectors/imap.py `send`) against a local
SMTP server whose answers the test chooses: delivery, recipients the server refuses (some, all
for now, any for good), a refused message, a server that is not there, an account without a
server, and an address the outbound guard refuses. Temporary errors are retried by the outbox,
permanent ones are not, so each answer must land on the right side. A connection that drops after
the message was handed over (DATA) is neither: the message may be out, so it is uncertain."""

from __future__ import annotations

import asyncio
import socket
from types import SimpleNamespace

import pytest
from aiosmtpd.controller import Controller

from mailr.connectors.imap import (
    ImapConnector,
    SendPermanentError,
    SendTemporaryError,
    SendUncertainError,
)

RAW = (b"From: Me <me@example.com>\r\nTo: a@example.org\r\nSubject: Hello\r\n"
       b"Message-ID: <smtp-test@example.com>\r\n\r\nHi there\r\n")


class Handler:
    """RCPT: `bad...` gets 550, `later...` 450. DATA: 451 or 554 when the test sets `data`."""

    def __init__(self):
        self.data: str | None = None
        self.got: list[tuple[str, list[str], bytes]] = []

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        if address.startswith("bad"):
            return "550 5.1.1 no such user"
        if address.startswith("later"):
            return "450 4.2.0 mailbox busy, try later"
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server, session, envelope):
        if self.data:
            return self.data
        self.got.append((envelope.mail_from, list(envelope.rcpt_tos), envelope.content))
        return "250 2.0.0 OK queued as T3ST"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def smtp():
    h = Handler()
    port = _free_port()
    c = Controller(h, hostname="127.0.0.1", port=port)
    c.start()
    try:
        yield SimpleNamespace(handler=h, port=port)
    finally:
        c.stop()


def _conn(port: int | None, *, private_ok: bool | None = True, host: str = "127.0.0.1") -> ImapConnector:
    smtp = {"host": host, "port": port, "tls": "none"} if port else None
    row = SimpleNamespace(id=1, config={"imap": {"host": "imap.example.org", "username": "me@example.com"},
                                        "smtp": smtp, "tls_verify": False}, preset=None)
    c = ImapConnector(row, {})
    c.private_ok = private_ok
    return c


async def test_delivered(smtp):
    out = await _conn(smtp.port).send(RAW, "me@example.com", ["a@example.org", "b@example.org"])
    assert "OK queued as T3ST" in out and "refused" not in out
    (mail_from, rcpts, content), = smtp.handler.got
    assert mail_from == "me@example.com" and rcpts == ["a@example.org", "b@example.org"]
    assert b"Subject: Hello" in content and b"Hi there" in content


async def test_some_recipients_refused_the_rest_get_it(smtp):
    out = await _conn(smtp.port).send(RAW, "me@example.com", ["a@example.org", "bad@example.org"])
    assert "OK queued as T3ST" in out and "refused: bad@example.org (550)" in out
    assert smtp.handler.got[0][1] == ["a@example.org"]


@pytest.mark.parametrize("rcpts, error, says", [
    (["later@example.org"], SendTemporaryError, "450"),
    (["later1@example.org", "later2@example.org"], SendTemporaryError, "mailbox busy"),
    (["bad@example.org"], SendPermanentError, "recipients refused: bad@example.org: 550"),
    (["later@example.org", "bad@example.org"], SendPermanentError, "recipients refused"),  # one is final
])
async def test_all_recipients_refused(smtp, rcpts, error, says):
    with pytest.raises(error) as e:
        await _conn(smtp.port).send(RAW, "me@example.com", rcpts)
    assert type(e.value) is error and says in str(e.value)
    assert smtp.handler.got == []


@pytest.mark.parametrize("answer, error", [("451 4.3.0 queue full", SendTemporaryError),
                                           ("554 5.7.1 message refused as spam", SendPermanentError)])
async def test_the_message_refused(smtp, answer, error):
    smtp.handler.data = answer
    with pytest.raises(error) as e:
        await _conn(smtp.port).send(RAW, "me@example.com", ["a@example.org"])
    assert type(e.value) is error and answer.split()[0] in str(e.value)


async def test_a_server_that_is_not_there_is_temporary():
    port = _free_port()  # closed again: nothing listens there
    with pytest.raises(SendTemporaryError, match="could not reach 127.0.0.1"):
        await _conn(port).send(RAW, "me@example.com", ["a@example.org"])


async def test_no_outgoing_server_or_a_refused_address_is_permanent(smtp):
    with pytest.raises(SendPermanentError, match="no outgoing"):
        await _conn(None).send(RAW, "me@example.com", ["a@example.org"])
    # An address the outbound guard refuses (the cloud metadata service): never tried, no retry.
    with pytest.raises(SendPermanentError, match="169.254.169.254"):
        await _conn(25, host="169.254.169.254").send(RAW, "me@example.com", ["a@example.org"])
    assert smtp.handler.got == []


class _Dropping:
    """A bare SMTP server that hangs up at one step: "rcpt" (before anything was handed over),
    "data" (after the message's final dot, without the 250 reply) or "quit" (after the 250)."""

    def __init__(self, drop: str):
        self.drop = drop
        self.got = b""

    async def __call__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(b"220 test ESMTP\r\n")
        try:
            while line := await reader.readline():
                cmd = line[:4].upper()
                if cmd in (b"EHLO", b"HELO"):
                    writer.write(b"250 test\r\n")
                elif cmd == b"MAIL":
                    writer.write(b"250 OK\r\n")
                elif cmd == b"RCPT":
                    if self.drop == "rcpt":
                        return
                    writer.write(b"250 OK\r\n")
                elif cmd == b"DATA":
                    writer.write(b"354 go ahead\r\n")
                    await writer.drain()
                    while (chunk := await reader.readline()) != b".\r\n":
                        self.got += chunk
                    if self.drop == "data":
                        return
                    writer.write(b"250 OK queued as D1\r\n")
                    if self.drop == "quit":
                        await writer.drain()
                        return
                elif cmd == b"QUIT":
                    return
                else:
                    writer.write(b"250 OK\r\n")
                await writer.drain()
        finally:
            writer.close()


@pytest.mark.parametrize("drop, error", [("rcpt", SendTemporaryError), ("data", SendUncertainError)])
async def test_a_dropped_connection_before_data_is_temporary_after_it_uncertain(drop, error):
    srv = _Dropping(drop)
    server = await asyncio.start_server(srv, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        with pytest.raises(error) as e:
            await _conn(port).send(RAW, "me@example.com", ["a@example.org"])
        assert type(e.value) is error
        assert bool(srv.got) is (drop == "data")
    finally:
        server.close()


async def test_a_drop_after_the_server_accepted_counts_as_sent():
    srv = _Dropping("quit")
    server = await asyncio.start_server(srv, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        assert "queued as D1" in await _conn(port).send(RAW, "me@example.com", ["a@example.org"])
    finally:
        server.close()
