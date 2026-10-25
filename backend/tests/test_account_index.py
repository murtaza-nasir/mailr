# ACT-SURFACES: api
"""The account index (8 Oct 2026): Conversation.account_ids, kept by threading.refresh and rebuilt
for the whole mailbox when the user's accounts or addresses change (core/mailaccounts.py).

/counts with "Show each account separately" on reads it in two grouped queries; an account's folder
list reads it too. While it is not current (a new send-as address, after the upgrade), lists use
the sub-query and give the same answer, and the per-account numbers wait for the rebuild: inline
for a small mailbox, else the job `mail.account_index`. A restricted token never uses it.
"""

from helpers.audit_fixes import _ingest, _token
from helpers.mail_accounts import _ids, _two_accounts
from sqlalchemy import func, select

from mailr.core import mailaccounts
from mailr.db import session_scope
from mailr.models import Conversation, Identity, Job, User


async def _counts(client):
    r = await client.get("/api/v1/counts")
    assert r.status_code == 200, r.text
    return r.json()["views"]


async def _account_ids(cid):
    async with session_scope() as db:
        return (await db.get(Conversation, cid)).account_ids


async def _stamp(uid):
    async with session_scope() as db:
        return (await db.get(User, uid)).account_index


async def test_refresh_keeps_the_index_and_lists_count_alike(client, user, raw):
    a, b, ids = await _two_accounts(user, raw)
    assert await _account_ids(ids["a1"]) == [a]
    assert await _account_ids(ids["b_sent"]) == [b]          # sent from the account's address
    assert await _account_ids(ids["none"]) == []
    assert (await client.patch("/api/v1/me/settings", json={"account_folders": True})).status_code == 200
    views = await _counts(client)
    assert await _stamp(user.id) is not None                  # the first /counts made it current
    assert views[f"acct:{a}:inbox"]["total"] == 2 and views[f"acct:{b}:inbox"]["total"] == 1
    for acct in (a, b):
        assert views[f"acct:{acct}:inbox"]["total"] == len(await _ids(client, "", f"acct:{acct}:inbox"))


async def test_a_new_address_rebuilds_the_index(client, user, raw):
    a, b, ids = await _two_accounts(user, raw)
    assert (await client.patch("/api/v1/me/settings", json={"account_folders": True})).status_code == 200
    # A note to self from the login address, which the second account does not have yet.
    _, alias, _ = await _ingest(user, raw("Note to self", frm=f"Me <{user.email}>", to=user.email),
                                roles=["sent", "inbox"])
    await _counts(client)
    assert alias not in await _ids(client, "", f"acct:{b}:inbox")
    before = (await _counts(client))[f"acct:{b}:inbox"]["total"]
    async with session_scope() as db:
        db.add(Identity(user_id=user.id, email=user.email, name="Me", connector_id=b, is_default=False))
    # The hash no longer matches: the list falls back to the sub-query at once ...
    assert alias in await _ids(client, "", f"acct:{b}:inbox")
    # ... and the next /counts rebuilds the index (a small mailbox: inline) and counts it.
    views = await _counts(client)
    assert views[f"acct:{b}:inbox"]["total"] == before + 1
    assert await _account_ids(alias) == [b]


async def test_a_large_mailbox_rebuilds_in_a_job_and_waits_for_it(client, user, raw, monkeypatch):
    a, b, ids = await _two_accounts(user, raw)
    assert (await client.patch("/api/v1/me/settings", json={"account_folders": True})).status_code == 200
    monkeypatch.setattr(mailaccounts, "INDEX_INLINE_MAX", 0)
    views = await _counts(client)
    # Not current yet: no per-account numbers (the folders show without them), a job queued, and
    # the lists still answer through the sub-query.
    assert not [v for v in views if v.startswith("acct:")]
    async with session_scope() as db:
        jobs = list((await db.execute(select(Job).where(Job.kind == "mail.account_index",
                                                        Job.user_id == user.id))).scalars())
    assert len(jobs) == 1
    assert await _ids(client, "", f"acct:{a}:inbox") == {ids["a1"], ids["a2"]}
    from mailr.core.tasks import account_index

    await account_index(jobs[0].payload, None)
    views = await _counts(client)
    assert views[f"acct:{a}:inbox"]["total"] == 2 and views[f"acct:{b}:inbox"]["total"] == 1


async def test_a_restricted_token_gets_no_account_numbers(client, user, raw):
    a, _b, ids = await _two_accounts(user, raw)
    assert (await client.patch("/api/v1/me/settings", json={"account_folders": True})).status_code == 200
    assert f"acct:{a}:inbox" in await _counts(client)
    t = await _token(client, ["read"])
    views = await _counts(t)
    assert not [v for v in views if v.startswith("acct:")]
    # Its account folder list still answers, from the messages it may see.
    assert await _ids(t, "", f"acct:{a}:inbox") == {ids["a1"], ids["a2"]}


async def test_two_session_checks_at_once_do_not_wait_on_each_other(client, user, raw, monkeypatch):
    """The page asks for the session twice as it starts (React's strict mode in development), each
    with the browser's zone. The first writes the zone and goes on to the numbers, whose first
    account index rebuild updates the user's row; the second, which locked that row to write the
    same zone, found it written and must not keep the lock while it waits for those numbers."""
    import asyncio

    from mailr.calendar import tz as tzmod

    await _two_accounts(user, raw)
    assert (await client.patch("/api/v1/me/settings", json={"account_folders": True})).status_code == 200
    async with session_scope() as db:
        u = await db.get(User, user.id)
        u.account_index = None  # not current: the next numbers rebuild it
    # Both requests have read the user (without the zone) before either writes it.
    arrived = asyncio.Barrier(2)
    note = tzmod.note_device_zone

    async def together(*a, **k):
        await arrived.wait()
        return await note(*a, **k)

    monkeypatch.setattr(tzmod, "note_device_zone", together)
    zone = {"X-Mailr-Zone": "Pacific/Auckland"}
    got = await asyncio.wait_for(asyncio.gather(*(client.get("/api/v1/auth/session", headers=zone) for _ in range(2))), 20)
    assert [r.status_code for r in got] == [200, 200]
    assert await _stamp(user.id) is not None


async def test_the_job_commits_each_batch(client, user, raw, monkeypatch):
    """The job's rebuild commits batch by batch: the rows of one batch are not held locked until
    the whole mailbox is done. The hash is stored last."""
    from sqlalchemy.ext.asyncio import AsyncSession

    from mailr.core.tasks import account_index

    await _two_accounts(user, raw)
    async with session_scope() as db:
        u = await db.get(User, user.id)
        u.account_index = None
    monkeypatch.setattr(mailaccounts, "INDEX_BATCH", 1)
    commits = []
    real = AsyncSession.commit

    async def commit(self):
        commits.append(1)
        return await real(self)

    monkeypatch.setattr(AsyncSession, "commit", commit)
    await account_index({"user_id": user.id}, None)
    async with session_scope() as db:
        n = (await db.execute(select(func.count()).select_from(Conversation).where(
            Conversation.user_id == user.id))).scalar_one()
    assert len(commits) >= n >= 3
    assert await _stamp(user.id) is not None
