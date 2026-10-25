"""approval_touches_local_only (core/visibility.py) reads the card's own rows first, then every id the
payload names, a chunk at a time: a cleanup plan may name up to 10,000 conversations. Plans are
marked sensitive when built if any of their conversations is local-only."""
import uuid

from helpers.agent import _ingest, _setup_identity

from mailr.agent.tools import ToolCtx, run_tool
from mailr.core.auth import ALL_SCOPES, Principal
from mailr.core.visibility import any_local_only, approval_touches_local_only
from mailr.db import sessionmaker
from mailr.models import PendingAction, User


async def _two(user, raw):
    lid, me_l = await _setup_identity(user, data_class="local_only")
    _, me = await _setup_identity(user)
    _, normal = await _ingest(user.id, raw("Newsletter", frm="News <n@shop.example>", to=me))
    _, local = await _ingest(user.id, raw("Salary review", frm="News <n@shop.example>", to=me_l), connector_id=lid)
    return normal, local


def _card(user_id, payload, preview):
    return PendingAction(user_id=user_id, action="mail.label", subject="assistant", actor="Ambrose",
                         payload=payload, preview=preview, ctx={})


async def test_local_only_id_after_2000_others_is_seen(client, user, raw):
    normal, local = await _two(user, raw)
    async with sessionmaker()() as db:
        late = _card(user.id, {"op": "cleanup", "conversation_ids": [normal] * 4500 + [local]}, {"lines": ["x"]})
        assert await approval_touches_local_only(db, user.id, late) is True
        clean = _card(user.id, {"op": "cleanup", "conversation_ids": [normal] * 4500}, {"lines": ["x"]})
        assert await approval_touches_local_only(db, user.id, clean) is False


async def test_card_rows_are_checked_before_the_payload(client, user, raw):
    normal, local = await _two(user, raw)
    async with sessionmaker()() as db:
        pa = _card(user.id, {"op": "cleanup", "conversation_ids": [normal] * 2500},
                   {"lines": ["Salary review"], "mail_refs": {"conversation_ids": [local]}})
        assert await approval_touches_local_only(db, user.id, pa) is True


async def test_any_local_only_reads_every_chunk(client, user, raw):
    normal, local = await _two(user, raw)
    async with sessionmaker()() as db:
        assert await any_local_only(db, user.id, conversation_ids=list(range(10**9, 10**9 + 6000)) + [local])
        assert not await any_local_only(db, user.id, conversation_ids=[normal] * 6000)
        assert not await any_local_only(db, user.id)


async def test_bulk_plan_with_local_only_mail_is_marked_sensitive_when_built(client, user, raw):
    normal, local = await _two(user, raw)
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        ctx = ToolCtx(db=db, p=Principal(user=u, scopes=set(ALL_SCOPES) - {"admin"}), assistant=True,
                      chat_session_id=f"s-{uuid.uuid4().hex[:6]}", untrusted=True)
        res = await run_tool(ctx, "archive", {"scope": {"view": "inbox"}})
        assert res.pending is not None, res.data
        pa = await db.get(PendingAction, res.data["approval_id"])
        assert local in pa.payload["conversation_ids"]
        assert pa.payload["sensitive"] is True
