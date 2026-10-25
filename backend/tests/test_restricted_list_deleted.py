"""A restricted token's list leaves out a conversation whose only message it may see is
deleted. Before, the token's semi-join had no deleted filter: the row showed and opened as a 404."""

from helpers.audit_fixes import _conn, _ingest, _token
from sqlalchemy import update

from mailr.db import session_scope
from mailr.models import Message


async def test_deleted_only_visible_message_is_not_listed(client, user, raw):
    normal = await _conn(user.id)
    uni = await _conn(user.id, "local_only")
    m1, c1, _ = await _ingest(user, raw("Room booking", msgid="<f34a@x.org>"), normal, roles=["inbox"])
    _, c2, _ = await _ingest(user, raw("Re: Room booking", frm="Dean <dean@uni.edu>", msgid="<f34b@x.org>",
                                       irt="<f34a@x.org>"), uni, roles=["inbox"])
    _, other, _ = await _ingest(user, raw("Lunch", msgid="<f34c@x.org>"), normal, roles=["inbox"])
    assert c1 == c2
    async with session_scope() as db:
        await db.execute(update(Message).where(Message.id == m1).values(deleted=True))
    tok = await _token(client, ["read"])
    ids = [c["id"] for c in (await tok.get("/api/v1/conversations", params={"view": "all"})).json()["items"]]
    assert other in ids and c1 not in ids
    # The signed-in user still sees the thread (its local-only reply is not deleted).
    ids = [c["id"] for c in (await client.get("/api/v1/conversations", params={"view": "all"})).json()["items"]]
    assert c1 in ids
