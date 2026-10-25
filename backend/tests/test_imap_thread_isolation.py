"""A provider resetting one account's IMAP connection must not break another account's.

A TLS failure leaves an entry in OpenSSL's per-thread error queue, and Python's ssl module
reports it on the next TLS read on that thread. With the IMAP calls of every account on one
shared thread pool, a provider's resets made healthy Gmail connections fail with
BrokenPipeError. Each ImapSession now runs on a thread of its own."""

from __future__ import annotations

import asyncio
import socket
import struct

from helpers.connectors import make_upstream_account

from mailr.connectors.imap import ImapSession

UP_IMAPS = 12993  # the dev upstream's IMAP over TLS (deploy/dev/docker-compose.yml)


class _Proxy:
    """TCP pass-through to the upstream; reset() answers the client's next write with a
    reset, as a provider's servers did."""

    def __init__(self):
        self.doomed = False
        self.client: asyncio.StreamWriter | None = None

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def _handle(self, cr, cw):
        ur, uw = await asyncio.open_connection("localhost", UP_IMAPS)
        self.client = cw

        async def pipe(r, w, upstream):
            try:
                while data := await r.read(65536):
                    if upstream and self.doomed:
                        cw.get_extra_info("socket").setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                                                               struct.pack("ii", 1, 0))
                        cw.transport.abort()
                        uw.transport.abort()
                        return
                    w.write(data)
                    await w.drain()
            except (ConnectionError, OSError):
                pass
            finally:
                w.close()

        await asyncio.gather(pipe(cr, uw, True), pipe(ur, cw, False))

    def close(self):
        self.server.close()


def _session(port: int, email: str, pw: str) -> ImapSession:
    cfg = {"email": email, "tls_verify": False, "imap": {"host": "127.0.0.1", "port": port, "tls": "ssl"}}
    return ImapSession(cfg, {"password": pw}, private_ok=True)


async def test_a_reset_connection_does_not_break_another_account():
    email, pw = make_upstream_account()
    pa, pb = _Proxy(), _Proxy()
    a, b = _session(await pa.start(), email, pw), _session(await pb.start(), email, pw)
    try:
        await a.connect()
        await b.connect()
        await a.select("INBOX")
        await b.select("INBOX")
        await a.call(a.conn.idle)

        pa.doomed = True
        failures = []
        for fn in (a.conn.idle_done, a.conn.logout):  # the reset, then the polite logout that follows
            try:
                await a.call(fn)
            except OSError as e:
                failures.append(type(e).__name__)
            except Exception as e:  # noqa: BLE001  imaplib wraps some socket errors as abort
                failures.append(type(e).__name__)
        assert failures, "the reset should have failed account A's calls"
        await a.close(hard=True)

        # Account B's connection is healthy and must keep working.
        for _ in range(10):
            assert await b.call(b.conn.list_folders)
            await b.call(b.conn.noop)
            await b.select("INBOX")
    finally:
        await b.close()
        pa.close()
        pb.close()


async def test_closing_a_session_retires_its_thread():
    email, pw = make_upstream_account()
    p = _Proxy()
    s = _session(await p.start(), email, pw)
    try:
        await s.connect()
        await s.select("INBOX")
        await s.close()
        assert s._thread._shutdown
    finally:
        p.close()
