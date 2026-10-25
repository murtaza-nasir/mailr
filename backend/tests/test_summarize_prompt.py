"""Summaries: look-alike emails are separate emails (two PayPal orders differing only in the
transaction id were summarised as one)."""

from conftest import mail_date
from helpers.core_flow import _ingest

from mailr.db import sessionmaker
from mailr.intel import llm, summarize
from mailr.models import User


async def test_look_alike_messages_are_all_sent_and_counted_separately(user, raw, monkeypatch):
    seen = {}

    async def fake_chat(db, u, messages, **kw):
        seen["system"], seen["user"] = messages[0]["content"], messages[1]["content"]
        return {"content": '{"summary": ["Two orders."], "action_items": [], "dates": []}'}

    monkeypatch.setattr(llm, "chat", fake_chat)
    body = "You placed a $833.80 USD order with American Airlines. Transaction ID {}"
    m1, cid = await _ingest(user, raw("American Airlines: $833.80 USD", frm="PayPal <service@paypal.example>",
                                      msgid="<pp1@x.org>", body=body.format("1AB23")))
    await _ingest(user, raw("American Airlines: $833.80 USD", frm="PayPal <service@paypal.example>", msgid="<pp2@x.org>",
                            irt="<pp1@x.org>", date=mail_date(0, "10:11"), body=body.format("9XY87")))
    async with sessionmaker()() as db:
        out = await summarize.summarize(db, await db.get(User, user.id), cid, force=True)
    assert out["message_count"] == 2
    assert "1AB23" in seen["user"] and "9XY87" in seen["user"]
    assert "separate email" in seen["system"] and "Do not mention duplicate" not in seen["system"]
