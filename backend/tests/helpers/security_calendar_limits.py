"""Shared helpers, from test_security_calendar_limits.py."""

import asyncio
import time
import uuid

GOOD_AR = "mx.example.com; spf=pass; dkim=pass header.d=acme.example; dmarc=pass header.from=acme.example"


def _mail(ics: str, frm: str, to: str, *, method: str, auth_header: str | None, extra_headers: str = "") -> bytes:
    ar = f"Authentication-Results: {auth_header}\r\n" if auth_header is not None else ""
    msg = (f"From: {frm}\r\n{extra_headers}To: {to}\r\nSubject: {method.title()}\r\n"
           f"Message-ID: <{uuid.uuid4().hex}@x.example>\r\nDate: Thu, 01 Oct 2026 12:00:00 +0000\r\n{ar}"
           'MIME-Version: 1.0\r\nContent-Type: multipart/alternative; boundary="b1"\r\n\r\n'
           "--b1\r\nContent-Type: text/plain\r\n\r\nx\r\n"
           f"--b1\r\nContent-Type: text/calendar; charset=utf-8; method={method}\r\n\r\n{ics}--b1--\r\n")
    return msg.encode()


class _LagProbe:
    """The longest gap between 50 ms ticks of the event loop while the block runs."""

    def __init__(self):
        self.lag, self._stop, self._task = 0.0, False, None

    async def _tick(self):
        while not self._stop:
            t = time.monotonic()
            await asyncio.sleep(0.05)
            self.lag = max(self.lag, time.monotonic() - t - 0.05)

    async def __aenter__(self):
        self._task = asyncio.create_task(self._tick())
        await asyncio.sleep(0)
        return self

    async def __aexit__(self, *a):
        self._stop = True
        await self._task
