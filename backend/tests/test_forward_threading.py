# ACT-SURFACES: api
"""A forward stays in the conversation of the message it carries, as Gmail files one: the draft is
saved there, the message goes out with In-Reply-To and References to the original, so its sent
copy joins the same conversation, and the original is not marked answered.

Covers mail.forward."""

import email

from helpers.agent import _ingest, _seed_thread, _setup_identity, _token_client
from sqlalchemy import select

from mailr.db import sessionmaker
from mailr.models import Message, User


async def test_a_forward_stays_in_the_original_conversation(client, user, raw):
    from mailr.core.auth import ALL_SCOPES, Principal
    from mailr.core.drafts import build_for_payload, load_draft

    _, me = await _setup_identity(user)
    m1, conv = await _seed_thread(user, raw, me)
    tok = (await client.post("/api/v1/tokens", json={"name": "fwd", "scopes": ["read", "draft"]})).json()["token"]
    async with _token_client(client, tok) as a:
        r = await a.post("/api/v1/agent/tools/create_draft",
                         json={"reply_to_message_id": m1, "mode": "forward", "to": ["dana@example.org"],
                               "body": "For you."})
        assert r.status_code in (200, 202), r.text
        draft_id = r.json()["result"].get("draft_id")
    if draft_id is None:  # a token's draft to a new address may wait for approval
        aid = r.json()["result"]["approval_id"]
        assert (await client.post(f"/api/v1/approvals/{aid}/decide", json={"decision": "approve"})).status_code == 200
        from mailr.core import permissions

        await permissions.execute_job({"pending_id": aid}, None)
        async with sessionmaker()() as db:
            draft_id = (await db.execute(select(Message.id).where(Message.user_id == user.id, Message.draft.is_(True))
                                         .order_by(Message.id.desc()))).scalars().first()

    async with sessionmaker()() as db:
        p = Principal(user=await db.get(User, user.id), scopes=set(ALL_SCOPES) - {"admin"})
        d = await load_draft(db, p, int(draft_id))
        original = await db.get(Message, m1)
        assert d.conversation_id == conv  # filed with the original
        assert d.in_reply_to == original.message_id_hdr
        payload = d.upstream["draft_payload"]
        assert payload["forward_of_message_id"] == m1 and not payload.get("in_reply_to_message_id")
        raw_out, _ident, parent = await build_for_payload(db, p, payload, message_id="<fwd-test@me>")
        assert parent is not None and parent.id == m1
    headers = email.message_from_bytes(raw_out)
    assert headers["In-Reply-To"] == original.message_id_hdr
    assert original.message_id_hdr in (headers["References"] or "")
    assert headers["Subject"].startswith("Fwd:")

    # The sent copy, stored as the send job stores it, joins the same conversation.
    _, sent_conv = await _ingest(user.id, raw_out, source="send", roles=["sent"])
    assert sent_conv == conv
    async with sessionmaker()() as db:
        assert (await db.get(Message, m1)).answered is False  # a forward is not an answer
