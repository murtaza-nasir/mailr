"""Shared helpers, from test_speakr_upload.py."""

import uuid
from email.message import EmailMessage

from helpers.core_flow import _ingest

AUDIO = b"ID3\x03\x00\x00\x00" + b"\x00" * 2048


def _with_attachments(subject="Interview recording"):
    m = EmailMessage()
    m["From"] = "Dana <dana@example.org>"
    m["To"] = "me@example.com"
    m["Subject"] = subject
    m["Message-ID"] = f"<{uuid.uuid4().hex}@example.org>"
    m["Date"] = "Mon, 21 Sep 2026 10:00:00 +0000"
    m.set_content("The interview is attached.")
    m.add_attachment(AUDIO, maintype="audio", subtype="mpeg", filename="interview.mp3")
    m.add_attachment(b"%PDF-1.4 x", maintype="application", subtype="pdf", filename="notes.pdf")
    m.add_attachment(AUDIO, maintype="application", subtype="octet-stream", filename="voice memo.m4a")
    return m.as_bytes()


async def _message(client, user):
    mid, cid = await _ingest(user, _with_attachments(), roles=["inbox"])
    msg = (await client.get(f"/api/v1/messages/{mid}")).json()
    parts = {a["filename"]: a["part"] for a in msg["attachments"]}
    return mid, cid, parts
