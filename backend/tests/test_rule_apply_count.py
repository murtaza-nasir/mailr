"""A rule's "apply to existing mail" answers how many messages it matches, counted in the database
(core/rules.py count_matching): the matching ids are no longer loaded just to be counted."""

from helpers.core_flow import _ingest

from mailr.core import rules as rulesmod
from mailr.db import sessionmaker


async def test_apply_counts_the_matching_mail(client, user, raw):
    for i in range(3):
        await _ingest(user, raw(f"Invoice {i}", body="due"))
    await _ingest(user, raw("Lunch", body="noon"))
    r = await client.post("/api/v1/rules", json={"query": "subject:invoice", "actions": {"mark_read": True}})
    assert r.status_code in (200, 201), r.text
    rid = r.json()["id"]
    async with sessionmaker()() as db:
        assert await rulesmod.count_matching(db, user.id, "subject:invoice") == 3
        assert len(await rulesmod.matching_message_ids(db, user.id, "subject:invoice")) == 3
    r = await client.post(f"/api/v1/rules/{rid}/apply")
    assert r.status_code in (200, 202), r.text
    assert r.json()["affected"] == 3
