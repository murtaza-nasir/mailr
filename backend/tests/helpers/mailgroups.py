"""Shared helpers, from test_mailgroups.py."""

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

from sqlalchemy import select, update

from mailr.core.ingest import IngestOptions, ingest_raw
from mailr.core.store import user_store
from mailr.db import sessionmaker
from mailr.models import Conversation, Message, User

LIST = "List-Id: Weekly Digest <weekly.news.example>\r\nList-Unsubscribe: <https://news.example/u/1>, " \
       "<mailto:leave@news.example>\r\nList-Unsubscribe-Post: List-Unsubscribe=One-Click"


def _ago(days: float) -> str:
    return format_datetime(datetime.now(UTC) - timedelta(days=days))


async def _ingest(user, data, **kw):
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        res = await ingest_raw(db, await user_store(db, u), data, IngestOptions(**kw))
        await db.commit()
        return res.message.id, res.message.conversation_id


async def _set(model, oid, **values):
    async with sessionmaker()() as db:
        await db.execute(update(model).where(model.id == oid).values(**values))
        await db.commit()


async def _labels(cid):
    async with sessionmaker()() as db:
        return (await db.get(Conversation, cid)).label_ids


async def _role(user, role):
    from mailr.models import Label

    async with sessionmaker()() as db:
        return (await db.execute(select(Label.id).where(Label.user_id == user.id, Label.role == role))).scalar()


async def _cleanup_box(user, raw):
    """Archived mail from news@bulk.example: a plain one, a starred one, an important one, one the
    user replied to; and one in the Inbox."""
    inbox, archive = await _role(user, "inbox"), await _role(user, "archive")
    out = {}
    for name in ("plain", "starred", "important", "replied", "inbox"):
        out[name] = await _ingest(user, raw(f"Bulk {name}", frm="News <news@bulk.example>", to=user.email,
                                            msgid=f"<{name}-{user.id}@bulk.example>", date=_ago(400)))
    for name in ("plain", "starred", "important", "replied"):
        mid, cid = out[name]
        labels = [archive] if archive else []
        await _set(Message, mid, label_ids=labels)
        await _set(Conversation, cid, label_ids=labels)
    await _set(Conversation, out["starred"][1], starred=True)
    await _set(Conversation, out["important"][1], important=True)
    await _ingest(user, raw("Re: Bulk replied", frm=f"Me <{user.email}>", to="news@bulk.example",
                            irt=f"<replied-{user.id}@bulk.example>", date=_ago(399)), source="send", roles=["sent"])
    assert inbox in await _labels(out["inbox"][1])
    return out
