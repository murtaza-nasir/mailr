"""VIP senders (Settings → General): their mail is always important and `is:vip` finds it."""

from helpers.core_flow import _ingest

from mailr.db import sessionmaker
from mailr.intel.tasks import process_message
from mailr.models import Message, User


async def _triage(user, mid):
    async with sessionmaker()() as db:
        m, u = await db.get(Message, mid), await db.get(User, user.id)
        await process_message(db, m, u, allow_llm=False)
        await db.commit()
    async with sessionmaker()() as db:
        return (await db.get(Message, mid)).important


async def test_vip_mail_is_important_and_searchable(client, user, raw):
    r = await client.patch("/api/v1/me/settings", json={"vip": [" Dana@Example.org ", "dana@example.org"]})
    assert r.status_code == 200 and r.json()["vip"] == ["dana@example.org"]
    assert (await client.patch("/api/v1/me/settings", json={"vip": ["not an address"]})).status_code == 400
    assert (await client.patch("/api/v1/me/settings", json={"vip": "dana@example.org"})).status_code == 400

    vip, _ = await _ingest(user, raw("Quick question", frm="Dana <dana@example.org>"), roles=["inbox"], source="connector")
    other, _ = await _ingest(user, raw("Weekly digest", frm="News <news@example.org>"), roles=["inbox"], source="connector")
    assert await _triage(user, vip) is True
    assert await _triage(user, vip) is True  # a second pass keeps it
    assert await _triage(user, other) is False
    got = (await client.get("/api/v1/conversations", params={"view": "all", "q": "is:vip"})).json()["items"]
    assert [c["subject"] for c in got] == ["Quick question"]
