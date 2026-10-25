"""The send gate's refusals (core/sendgate.py): what a token may not do, the checks on the
sending identity, the recipients, the content and the send time, and the outbox actions (retry,
cancel, send now, the legacy approve/reject) on messages that are not the caller's or not in
the right state. Each refusal leaves no outbox row and no job behind."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from mailr.core.auth import ALL_SCOPES, Principal
from mailr.core.sendgate import MAX_RECIPIENTS, SendGate
from mailr.db import sessionmaker
from mailr.models import ApiToken, Connector, Identity, Job, Outbox, PendingAction, User

pytestmark = pytest.mark.db_user

MSG = {"to": [{"email": "alice@acme.example"}], "subject": "Hello", "text": "Hi"}


async def _setup(user_id: int) -> dict:
    """Two accounts (one local-only), an identity on each and one without an account, and three
    tokens: one that may send, one without the send scope, one limited to the second account."""
    async with sessionmaker()() as db:
        main = Connector(user_id=user_id, kind="imap", name="Main")
        local = Connector(user_id=user_id, kind="imap", name="Local", data_class="local_only")
        db.add_all([main, local])
        await db.flush()
        ident = Identity(user_id=user_id, email="me@example.com", connector_id=main.id, is_default=True)
        on_local = Identity(user_id=user_id, email="me@local.example", connector_id=local.id)
        bare = Identity(user_id=user_id, email="me@nowhere.example")
        db.add_all([ident, on_local, bare])
        toks = {}
        for name, scopes, conns in (("sender", ["send", "read"], []), ("reader", ["read", "draft"], []),
                                    ("limited", ["send"], [local.id])):
            t = ApiToken(user_id=user_id, name=name, prefix="mlr_x", token_hash=f"{name}-{user_id}-{datetime.now(UTC).timestamp()}",
                         scopes=scopes, connector_ids=conns)
            db.add(t)
            toks[name] = t
        await db.commit()
        return {"main": main.id, "local": local.id, "ident": ident.id, "on_local": on_local.id, "bare": bare.id,
                **{f"tok_{k}": t.id for k, t in toks.items()}}


async def _principal(db, user_id: int, token_id: int | None = None) -> Principal:
    u = await db.get(User, user_id)
    if token_id is None:
        return Principal(user=u, scopes=set(ALL_SCOPES))
    t = await db.get(ApiToken, token_id)
    return Principal(user=u, token=t, scopes=set(t.scopes))


async def _counts(user_id: int) -> tuple[int, int]:
    async with sessionmaker()() as db:
        ob = (await db.execute(select(func.count()).select_from(Outbox).where(Outbox.user_id == user_id))).scalar_one()
        jobs = (await db.execute(select(func.count()).select_from(Job).where(
            Job.user_id == user_id, Job.kind == "outbox.send"))).scalar_one()
        return ob, jobs


async def _refused(user_id: int, payload: dict, *, token: int | None = None, status: int, says: str, **kw) -> None:
    before = await _counts(user_id)
    async with sessionmaker()() as db:
        p = await _principal(db, user_id, token)
        with pytest.raises(HTTPException) as e:
            await SendGate(db, p).submit(payload, **kw)
        await db.rollback()
    assert e.value.status_code == status and says in str(e.value.detail), (e.value.status_code, e.value.detail)
    assert await _counts(user_id) == before


async def test_submit_refusals(user):
    s = await _setup(user.id)
    uid = user.id
    # The same message goes out when nothing is wrong with it (so each refusal below is its own).
    async with sessionmaker()() as db:
        ob = await SendGate(db, await _principal(db, uid)).submit({**MSG, "identity_id": s["ident"]})
        await db.commit()
        assert ob.status == "queued" and ob.recipients == ["alice@acme.example"]

    await _refused(uid, MSG, token=s["tok_reader"], status=403, says="this token cannot send")
    await _refused(uid, {**MSG, "identity_id": s["bare"]}, status=400, says="has no outgoing mail server")
    await _refused(uid, {**MSG, "identity_id": s["ident"]}, token=s["tok_limited"], status=403,
                   says="may not use that account")
    # A token that is not local-only may not send from a local-only account.
    await _refused(uid, {**MSG, "identity_id": s["on_local"]}, token=s["tok_sender"], status=403,
                   says="may not use that account")
    await _refused(uid, {**MSG, "to": []}, status=400, says="add at least one recipient")
    many = [{"email": f"r{i}@acme.example"} for i in range(MAX_RECIPIENTS + 1)]
    await _refused(uid, {**MSG, "to": many[:60], "cc": many[60:]}, status=400, says=f"at most {MAX_RECIPIENTS}")
    await _refused(uid, {"to": MSG["to"], "subject": "", "text": ""}, status=400, says="the message is empty")
    await _refused(uid, MSG, status=400, says="send time is in the past",
                   send_at=datetime.now(UTC) - timedelta(minutes=5))
    # A send time a few seconds ago (a slow client) is still now.
    async with sessionmaker()() as db:
        ob = await SendGate(db, await _principal(db, uid)).submit(MSG, send_at=datetime.now(UTC) - timedelta(seconds=20))
        await db.commit()
        assert ob.status == "queued"


async def test_another_users_identity_is_unknown(user):
    from conftest import make_db_user

    other = await make_db_user()
    s = await _setup(other.id)
    await _refused(user.id, {**MSG, "identity_id": s["ident"]}, status=400, says="unknown sending identity")
    # Without a usable identity of the user's own, none is borrowed.
    await _refused(user.id, MSG, status=400, says="unknown sending identity")


async def _outbox(user_id: int, ident: int, *, token: int | None, status: str) -> int:
    async with sessionmaker()() as db:
        ob = Outbox(user_id=user_id, identity_id=ident, token_id=token, subject=f"token:{token}" if token else "user",
                    payload=MSG, recipients=["alice@acme.example"], send_at=datetime.now(UTC) + timedelta(hours=1),
                    status=status)
        db.add(ob)
        await db.commit()
        return ob.id


async def _act(user_id: int, token: int | None, method: str, *args):
    async with sessionmaker()() as db:
        try:
            ob = await getattr(SendGate(db, await _principal(db, user_id, token)), method)(*args)
            await db.commit()
            return ob.status
        except HTTPException as e:
            await db.rollback()
            return e.status_code, str(e.detail)


async def _status(oid: int) -> str:
    async with sessionmaker()() as db:
        return (await db.get(Outbox, oid)).status


async def test_a_token_acts_only_on_its_own_messages(user):
    s = await _setup(user.id)
    mine, other = s["tok_sender"], s["tok_limited"]
    failed = await _outbox(user.id, s["ident"], token=mine, status="failed")
    assert await _act(user.id, other, "retry", failed) == (403, "not this token's message")
    assert await _status(failed) == "failed"
    assert await _act(user.id, mine, "retry", failed) == "queued"

    queued = await _outbox(user.id, s["ident"], token=mine, status="queued")
    assert await _act(user.id, other, "cancel", queued) == (403, "not this token's message")
    assert await _status(queued) == "queued"
    # Releasing early is the user's alone, even for the token's own message.
    assert await _act(user.id, mine, "send_now", queued) == (403, "only the user can release a scheduled message early")
    assert await _status(queued) == "queued"
    assert await _act(user.id, mine, "cancel", queued) == "cancelled"


async def test_outbox_actions_refuse_the_wrong_state(user):
    s = await _setup(user.id)
    sent = await _outbox(user.id, s["ident"], token=None, status="sent")
    assert (await _act(user.id, None, "retry", sent))[0] == 409
    assert await _act(user.id, None, "cancel", sent) == (409, "too late to cancel: the message was already sent")
    assert (await _act(user.id, None, "send_now", sent))[0] == 409
    assert (await _act(user.id, None, "decide", sent, True))[0] == 409
    assert await _status(sent) == "sent"
    # Someone else's message does not exist for this user.
    from conftest import make_db_user

    other = await make_db_user()
    theirs = await _outbox(other.id, (await _setup(other.id))["ident"], token=None, status="queued")
    for method, args in (("retry", ()), ("cancel", ()), ("send_now", ()), ("decide", (True,))):
        assert (await _act(user.id, None, method, theirs, *args))[0] == 404, method
    assert await _status(theirs) == "queued"


async def test_send_now_pulls_the_queued_job_forward(user):
    s = await _setup(user.id)
    async with sessionmaker()() as db:
        ob = await SendGate(db, await _principal(db, user.id)).submit(MSG, send_at=datetime.now(UTC) + timedelta(hours=2))
        await db.commit()
        oid = ob.id
    assert await _act(user.id, None, "send_now", oid) == "queued"
    async with sessionmaker()() as db:
        jobs = (await db.execute(select(Job).where(Job.kind == "outbox.send", Job.user_id == user.id,
                                                   Job.payload["outbox_id"].astext == str(oid)))).scalars().all()
        assert len(jobs) == 1 and jobs[0].run_at <= datetime.now(UTC)
        assert (await db.get(Outbox, oid)).send_at <= datetime.now(UTC)
    _ = s


async def test_legacy_decide_without_a_pending_action(user):
    """/outbox/{id}/approve|reject on a message awaiting approval whose approval request is gone:
    approve releases it once (queued, one job), reject marks it rejected; neither sends twice."""
    s = await _setup(user.id)
    a = await _outbox(user.id, s["ident"], token=s["tok_sender"], status="awaiting_approval")
    b = await _outbox(user.id, s["ident"], token=s["tok_sender"], status="awaiting_approval")
    async with sessionmaker()() as db:
        assert (await db.execute(select(func.count()).select_from(PendingAction).where(
            PendingAction.user_id == user.id))).scalar_one() == 0
    assert await _act(user.id, None, "decide", a, True) == "queued"
    assert await _act(user.id, None, "decide", b, False) == "rejected"
    async with sessionmaker()() as db:
        assert (await db.get(Outbox, a)).decided_by == "user"
        jobs = (await db.execute(select(Job.payload).where(Job.kind == "outbox.send", Job.user_id == user.id))).scalars().all()
        assert [j["outbox_id"] for j in jobs] == [a]
    # Deciding again finds it no longer awaiting approval.
    assert (await _act(user.id, None, "decide", a, True))[0] == 409
