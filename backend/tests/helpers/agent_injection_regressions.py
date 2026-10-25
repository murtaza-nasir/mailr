"""Shared helpers, from test_agent_injection_regressions.py."""

from __future__ import annotations

import uuid

from conftest import mail_date

from helpers.trusted_forwards import (
    GOOD_AR,
    WORK,
)


def _raw(frm_header: str, to: str, body: str, *, ar: str = GOOD_AR, subject: str = "Fw: Ops Review",
         extra: str = "", msgid: str | None = None) -> bytes:
    """A message with a From header written exactly as given (no normalisation by EmailMessage)."""
    return (f"{ar}\r\nFrom: {frm_header}\r\n{extra}To: {to}\r\nSubject: {subject}\r\n"
            f"Message-ID: {msgid or f'<{uuid.uuid4().hex}@{WORK}>'}\r\nDate: {mail_date(13)}\r\nMIME-Version: 1.0\r\n"
            f"Content-Type: text/plain; charset=utf-8\r\n\r\n{body}\r\n").encode()
