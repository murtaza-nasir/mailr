"""Shared helpers, from test_people_groups.py."""

import uuid
from datetime import UTC, datetime

from mailr.core import people
from mailr.db import sessionmaker
from mailr.models import Message

NOW = datetime.now(UTC)


async def _msg(user_id, *, sent, frm="", to=(), cc=(), when=None, subject="Hello", list_id=None, msgid=None,
               irt=None, body=""):
    """A message row straight into the database (no mail store needed), with People updated as ingest does."""
    async with sessionmaker()() as db:
        m = Message(user_id=user_id, sent_by_me=sent, from_email=frm, from_name="",
                    to=[{"email": e, "name": ""} for e in to], cc=[{"email": e, "name": ""} for e in cc], bcc=[],
                    date=when or NOW, subject=subject, list_id=list_id, label_ids=[], references=[], attachments=[],
                    message_id_hdr=msgid or f"<{uuid.uuid4().hex}@t>", in_reply_to=irt, body_text=body)
        db.add(m)
        await db.flush()
        await people.record(db, m)
        await db.commit()
        return m.id
