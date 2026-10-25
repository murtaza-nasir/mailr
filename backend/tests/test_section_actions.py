# ACT-SURFACES: web.section
"""Actions on a whole inbox section (the section header's Mark all read, Archive all, Delete all and
the rest; web.section). The web sends the section itself as the list ({"layout", "tab", "sec"}), so
the server acts on exactly the conversations the section lists, not on the rows loaded on screen.
A section large enough to run as a job keeps what its Undo needs. ACT mail.archive, mail.delete,
mail.read, mail.unread, mail.label, mail.move, mail.snooze, mail.mute, mail.important."""

import types

from helpers.core_flow import _ingest
from sqlalchemy import select

from mailr.db import session_scope, sessionmaker
from mailr.models import Job, Message, User

SECTION = {"view": "inbox", "layout": "starred_first", "tab": "inbox", "sec": "everything"}


async def _ids(client, **params):
    r = await client.get("/api/v1/conversations", params=params)
    assert r.status_code == 200, r.text
    return [c["id"] for c in r.json()["items"]]


async def _setup(client, user, raw, n=4):
    """One starred conversation (the Starred section) and `n` others (Everything else)."""
    _, starred = await _ingest(user, raw("Keep me", msgid="<sa-star@x.org>"))
    await client.post("/api/v1/conversations/actions", json={"ids": [starred], "action": "star"})
    rest = []
    for i in range(n):
        _, c = await _ingest(user, raw(f"Section mail {i}", msgid=f"<sa{i}@x.org>",
                                       date=f"Mon, 21 Sep 2026 1{i}:00:00 +0000"))
        rest.append(c)
    assert set(await _ids(client, **SECTION)) == set(rest)
    return starred, rest


async def _run_job(job_id, user):
    async with session_scope() as db:
        j = await db.get(Job, job_id)
        payload = dict(j.payload)
        j.status = "running"
    import mailr.core.tasks as tasks

    await tasks.bulk_conversations(payload, types.SimpleNamespace(job_id=job_id, user_id=user.id))
    async with session_scope() as db:
        (await db.get(Job, job_id)).status = "done"


async def test_archive_all_acts_on_the_section_only_and_undoes(client, user, raw):
    starred, rest = await _setup(client, user, raw)
    r = await client.post("/api/v1/conversations/actions", json={"query": SECTION, "action": "archive"})
    assert r.status_code == 200, r.text
    assert r.json()["affected"] == len(rest) and r.json()["conversations"] == len(rest)  # one message each
    assert await _ids(client, **SECTION) == []
    # The other section is untouched.
    assert await _ids(client, layout="starred_first", tab="inbox", sec="starred") == [starred]
    undo = r.json()["undo"]
    assert undo["action"] == "restore_labels" and set(undo["ids"]) == set(rest)
    r = await client.post("/api/v1/conversations/actions", json={"ids": [], **undo})
    assert r.status_code == 200, r.text
    assert set(await _ids(client, **SECTION)) == set(rest)


async def test_mark_all_read_touches_only_the_unread_and_its_undo_restores_them(client, user, raw):
    _starred, rest = await _setup(client, user, raw)
    # One of the section's conversations was already read.
    await client.post("/api/v1/conversations/actions", json={"ids": [rest[0]], "action": "read"})
    r = await client.post("/api/v1/conversations/actions",
                          json={"query": {**SECTION, "q": "is:unread"}, "action": "read"})
    assert r.status_code == 200, r.text
    assert r.json()["affected"] == len(rest) - 1
    assert set(r.json()["undo"]["ids"]) == set(rest[1:])
    assert await _ids(client, **SECTION, q="is:unread") == []
    await client.post("/api/v1/conversations/actions", json={"ids": [], **r.json()["undo"]})
    # The undo marks unread only what was unread before.
    assert set(await _ids(client, **SECTION, q="is:unread")) == set(rest[1:])


async def test_a_large_section_runs_as_a_job_with_an_undo(client, user, raw, monkeypatch):
    import mailr.api.mail_write as mw

    starred, rest = await _setup(client, user, raw, n=5)
    monkeypatch.setattr(mw, "BULK_INLINE_MAX", 2)
    r = await client.post("/api/v1/conversations/actions", json={"query": SECTION, "action": "trash"})
    assert r.status_code == 202, r.text
    job = r.json()["job"]
    assert r.json()["total"] == len(rest)
    await _run_job(job, user)
    assert await _ids(client, **SECTION) == []
    assert set(await _ids(client, view="trash")) == set(rest)
    # User actions: every touched message has its local change time.
    async with sessionmaker()() as db:
        stamps = (await db.execute(select(Message.local_change_at).where(Message.conversation_id.in_(rest)))).scalars().all()
    assert stamps and all(s is not None for s in stamps)

    st = (await client.get(f"/api/v1/bulk/{job}")).json()
    assert st["status"] == "done"
    assert st["undo"] == {"action": "restore_labels", "job": job, "ids": []}
    r = await client.post("/api/v1/conversations/actions", json=st["undo"])
    assert r.status_code == 202, r.text
    undo_job = r.json()["job"]
    await _run_job(undo_job, user)
    assert set(await _ids(client, **SECTION)) == set(rest)
    assert await _ids(client, view="trash") == []
    assert await _ids(client, layout="starred_first", tab="inbox", sec="starred") == [starred]
    # Used once; the undo job itself offers no undo.
    assert (await client.get(f"/api/v1/bulk/{job}")).json()["undo"] is None
    assert (await client.post("/api/v1/conversations/actions", json=st["undo"])).status_code == 404
    assert (await client.get(f"/api/v1/bulk/{undo_job}")).json()["undo"] is None


async def test_job_undo_of_mark_read_and_a_mismatched_action(client, user, raw, monkeypatch):
    import mailr.api.mail_write as mw

    _starred, rest = await _setup(client, user, raw, n=3)
    monkeypatch.setattr(mw, "BULK_INLINE_MAX", 1)
    r = await client.post("/api/v1/conversations/actions",
                          json={"query": {**SECTION, "q": "is:unread"}, "action": "read"})
    job = r.json()["job"]
    await _run_job(job, user)
    assert await _ids(client, **SECTION, q="is:unread") == []
    st = (await client.get(f"/api/v1/bulk/{job}")).json()
    assert st["undo"] == {"action": "unread", "job": job, "ids": []}
    # The undo must be the reverse the job kept.
    assert (await client.post("/api/v1/conversations/actions",
                              json={"action": "trash", "job": job, "ids": []})).status_code == 404
    r = await client.post("/api/v1/conversations/actions", json=st["undo"])
    await _run_job(r.json()["job"], user)
    assert set(await _ids(client, **SECTION, q="is:unread")) == set(rest)


async def test_another_users_job_cannot_be_undone(client, user, raw, monkeypatch):
    import mailr.api.mail_write as mw
    from mailr.core.users import create_user

    _starred, _rest = await _setup(client, user, raw, n=3)
    monkeypatch.setattr(mw, "BULK_INLINE_MAX", 1)
    r = await client.post("/api/v1/conversations/actions", json={"query": SECTION, "action": "archive"})
    job = r.json()["job"]
    await _run_job(job, user)
    async with sessionmaker()() as db:
        other = await create_user(db, f"other-{job}@example.com", "Other", password="test-password-123")
        j = await db.get(Job, job)
        j.user_id = other.id
        await db.commit()
    assert (await client.post("/api/v1/conversations/actions",
                              json={"action": "restore_labels", "job": job, "ids": []})).status_code == 404
    assert (await client.get(f"/api/v1/bulk/{job}")).status_code == 404
    async with sessionmaker()() as db:
        assert await db.get(User, user.id) is not None


async def test_an_unknown_section_is_refused(client, user, raw):
    await _setup(client, user, raw, n=1)
    r = await client.post("/api/v1/conversations/actions",
                          json={"query": {**SECTION, "sec": "nope"}, "action": "archive"})
    assert r.status_code == 404
