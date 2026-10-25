# ACT-SURFACES: api
"""What has not left yet: GET /outbox?active=true lists sends that are on their
way, waiting for approval, failed this week or sent a moment ago, with the draft each came from;
POST /outbox/{id}/retry puts a failed send back in the queue (ACT draft.retry_send); a user's send of
a draft withdraws Ambrose's request to send the same draft, so it never goes out twice."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from mailr.db import session_scope, sessionmaker
from mailr.models import Connector, Identity, Job, Outbox, PendingAction

API = "/api/v1"


async def _ident(user) -> int:
    async with session_scope() as db:
        c = Connector(user_id=user.id, kind="imap", name="acct", mode="mirror", config={"smtp": {"host": "x"}},
                      state={}, enabled=False)
        db.add(c)
        await db.flush()
        i = Identity(user_id=user.id, email="me@example.com", name="Me", connector_id=c.id, is_default=True)
        db.add(i)
        await db.flush()
        return i.id


async def _send(client, ident: int, **extra) -> dict:
    r = await client.post(f"{API}/send", json={"payload": {
        "identity_id": ident, "to": [{"email": "marco@example.org", "name": "Marco"}], "subject": "Quote",
        "html": "<p>Hi</p>"}, **extra})
    assert r.status_code in (200, 202), r.text
    return r.json()["outbox"]


async def _set(oid: int, **fields) -> None:
    async with session_scope() as db:
        row = await db.get(Outbox, oid)
        for k, v in fields.items():
            setattr(row, k, v)


async def test_active_outbox_lists_what_has_not_left(client, user):
    ident = await _ident(user)
    queued = await _send(client, ident)
    later = await _send(client, ident, send_at=(datetime.now(UTC) + timedelta(hours=3)).isoformat())
    failed = await _send(client, ident)
    await _set(failed["id"], status="failed", error="SMTPRecipientsRefused: 550 5.1.1 no such user")
    sent = await _send(client, ident)
    await _set(sent["id"], status="sent", sent_at=datetime.now(UTC), sent_message_id="<abc@example.com>")
    old = await _send(client, ident)
    await _set(old["id"], status="sent", sent_at=datetime.now(UTC) - timedelta(minutes=10))

    rows = (await client.get(f"{API}/outbox", params={"active": "true"})).json()
    by = {o["id"]: o for o in rows}
    assert queued["id"] in by and by[queued["id"]]["status"] == "queued"
    assert failed["id"] in by and "550" in by[failed["id"]]["error"]
    assert by[sent["id"]]["sent_message_id"] == "<abc@example.com>"
    # Scheduled for later is the Scheduled view's; a send that left ten minutes ago is history.
    assert later["id"] not in by and old["id"] not in by
    assert all("draft_id" in o for o in rows)


async def test_retry_puts_a_failed_send_back_in_the_queue(client, user):
    """ACT draft.retry_send"""
    ident = await _ident(user)
    ob = await _send(client, ident)
    r = await client.post(f"{API}/outbox/{ob['id']}/retry")
    assert r.status_code == 409  # only a failed send can be retried
    await _set(ob["id"], status="failed", error="TimeoutError: timed out", attempts=4)
    r = await client.post(f"{API}/outbox/{ob['id']}/retry")
    assert r.status_code == 200, r.text
    assert r.json()["outbox"]["status"] == "queued" and r.json()["outbox"]["error"] == ""
    async with sessionmaker()() as db:
        jobs = (await db.execute(select(Job).where(Job.kind == "outbox.send"))).scalars().all()
        assert any(j.payload.get("outbox_id") == ob["id"] and j.status == "queued" for j in jobs)


async def test_users_send_withdraws_ambroses_request_for_the_same_draft(client, user):
    ident = await _ident(user)
    r = await client.post(f"{API}/drafts", json={"payload": {
        "identity_id": ident, "to": [{"email": "marco@example.org"}], "subject": "Re: Quote", "html": "<p>Confirmed</p>"}})
    assert r.status_code == 200, r.text
    draft_id = r.json()["draft"]["id"] if "draft" in r.json() else r.json()["id"]
    # Ambrose's request to send that draft, waiting for approval.
    async with session_scope() as db:
        ob = Outbox(user_id=user.id, identity_id=ident, subject="assistant", draft_jmap_id=str(draft_id),
                    payload={"subject": "Re: Quote"}, recipients=["marco@example.org"], send_at=datetime.now(UTC),
                    status="awaiting_approval")
        db.add(ob)
        await db.flush()
        pa = PendingAction(user_id=user.id, subject="assistant", action="mail.send", payload={"outbox_id": ob.id},
                           expires_at=datetime.now(UTC) + timedelta(days=1))
        db.add(pa)
        await db.flush()
        asked, pid = ob.id, pa.id
    rows = (await client.get(f"{API}/outbox", params={"active": "true"})).json()
    assert next(o for o in rows if o["id"] == asked)["draft_id"] == int(draft_id)
    mine = await _send(client, ident, draft_id=int(draft_id))
    assert mine["status"] == "queued"
    async with sessionmaker()() as db:
        assert (await db.get(Outbox, asked)).status == "cancelled"
        assert (await db.get(PendingAction, pid)).status == "denied"
