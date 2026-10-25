# ACT-SURFACES: api
"""Labels picked on the phone's "Send to self" card (Android 0.5.9): POST /send carries
`label_ids`; the sent copy gets them when it is stored (mail.send_to_self_labels), next to the
labels of the user's filters ("Notes to self" → Later). Only the user's own labels count, a
replay changes nothing, and the inbox copy that arrived first gets them too.
A share is still a note to self from another app (mail.send_to_self)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from mailr.db import session_scope, sessionmaker
from mailr.models import Connector, Identity, Label, Message, Outbox, User

ME = "me@example.com"


async def _ident(user) -> None:
    async with session_scope() as db:
        c = Connector(user_id=user.id, kind="imap", name="acct", mode="mirror", config={"smtp": {"host": "x"}},
                      state={}, enabled=False)
        db.add(c)
        await db.flush()
        db.add(Identity(user_id=user.id, email=ME, name="Me", connector_id=c.id, is_default=True))


async def _later_rule(client) -> int:
    later = (await client.post("/api/v1/labels", json={"name": "Later"})).json()["id"]
    r = await client.post("/api/v1/rules", json={"name": "Notes to self", "query": "from:me to:me",
                                                  "actions": {"archive": True, "add_labels": [later]}})
    assert r.status_code in (200, 201), r.text
    return later


async def _label(client, name: str) -> int:
    r = await client.post("/api/v1/labels", json={"name": name})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _foreign_label() -> int:
    from mailr.core.users import create_user

    async with sessionmaker()() as db:
        other = await create_user(db, f"o-{uuid.uuid4().hex[:8]}@example.com", "Other", password="test-password-123")
    async with session_scope() as db:
        lb = Label(user_id=other.id, name="Theirs")
        db.add(lb)
        await db.flush()
        return lb.id


async def _roles(user) -> dict[str, int]:
    async with sessionmaker()() as db:
        return {lb.role: lb.id for lb in (await db.execute(
            select(Label).where(Label.user_id == user.id, Label.role.is_not(None)))).scalars()}


def _share(label_ids, subject="Recipe: lentil soup") -> dict:
    return {"payload": {"to": [{"email": ME}], "subject": subject, "text": "https://example.org/soup",
                        "headers": {"X-Mailr-Origin": "android-share"}, "label_ids": label_ids}}


async def _store(user, oid: int, data: bytes | None = None) -> bytes:
    """What the worker does after delivery: the .eml on disk, then store_sent_job. Pass the bytes
    of an earlier call to replay the same message (a rebuild gets a new Date and boundary)."""
    from mailr.core import sendgate
    from mailr.core.drafts import build_for_payload

    async with sessionmaker()() as db:
        ob = await db.get(Outbox, oid)
        u = await db.get(User, user.id)
        if not ob.mime_message_id:
            ob.mime_message_id = f"<{uuid.uuid4().hex}@example.com>"
        if data is None:
            data, _, _ = await build_for_payload(db, sendgate._SystemPrincipal(u), ob.payload,
                                                 message_id=ob.mime_message_id)
        (sendgate._outbox_dir() / f"{oid}.eml").write_bytes(data)
        ob.status = "sent"
        await db.commit()
    await sendgate.store_sent_job({"outbox_id": oid}, None)
    return data


async def _sent_copy(oid: int) -> Message:
    async with sessionmaker()() as db:
        ob = await db.get(Outbox, oid)
        return (await db.execute(select(Message).where(Message.message_id_hdr == ob.mime_message_id))).scalar_one()


async def test_send_to_self_labels_only_the_users_own_labels_are_kept(client, user):
    await _ident(user)
    recipes = await _label(client, "Recipes")
    cooking = await _label(client, "Home/Cooking")
    foreign = await _foreign_label()
    roles = await _roles(user)
    r = await client.post("/api/v1/send", json=_share([recipes, foreign, roles["trash"], "x", True, recipes, cooking]))
    assert r.status_code == 200, r.text
    async with sessionmaker()() as db:
        ob = await db.get(Outbox, r.json()["outbox"]["id"])
        assert ob.payload["label_ids"] == [recipes, cooking]
    # No labels: no key at all, so a plain send's payload is unchanged.
    r = await client.post("/api/v1/send", json=_share([foreign]))
    async with sessionmaker()() as db:
        assert "label_ids" not in (await db.get(Outbox, r.json()["outbox"]["id"])).payload


async def test_send_to_self_labels_go_on_the_note_and_the_later_filter_still_applies(client, user):
    await _ident(user)
    later = await _later_rule(client)
    recipes = await _label(client, "Recipes")
    roles = await _roles(user)
    oid = (await client.post("/api/v1/send", json=_share([recipes]))).json()["outbox"]["id"]
    await _store(user, oid)
    m = await _sent_copy(oid)
    assert m.origin == "android-share" and m.sent_by_me
    assert {recipes, later, roles["sent"]} <= set(m.label_ids)
    assert roles["inbox"] not in m.label_ids  # the filter archives it, as before


async def test_send_to_self_labels_replay_is_idempotent(client, user):
    """The phone retries with the same Idempotency-Key; the worker may store the copy twice."""
    await _ident(user)
    later = await _later_rule(client)
    recipes = await _label(client, "Recipes")
    key = f"self-{uuid.uuid4().hex}"
    first = await client.post("/api/v1/send", json=_share([recipes]), headers={"Idempotency-Key": key})
    again = await client.post("/api/v1/send", json=_share([recipes]), headers={"Idempotency-Key": key})
    assert first.status_code == again.status_code == 200
    oid = first.json()["outbox"]["id"]
    assert again.json()["outbox"]["id"] == oid
    async with sessionmaker()() as db:
        assert len((await db.execute(select(Outbox).where(Outbox.user_id == user.id))).scalars().all()) == 1
    data = await _store(user, oid)
    before = sorted((await _sent_copy(oid)).label_ids)
    await _store(user, oid, data)  # the worker retries with the same file
    m = await _sent_copy(oid)
    assert sorted(m.label_ids) == before and recipes in before and later in before


async def test_send_to_self_labels_a_deleted_label_is_skipped_and_the_inbox_copy_gets_them(client, user, raw):
    from helpers.core_flow import _ingest

    await _ident(user)
    later = await _later_rule(client)
    keep = await _label(client, "Recipes")
    gone = await _label(client, "Gone soon")
    oid = (await client.post("/api/v1/send", json=_share([keep, gone]))).json()["outbox"]["id"]
    async with sessionmaker()() as db:
        ob = await db.get(Outbox, oid)
        ob.mime_message_id = f"<{uuid.uuid4().hex}@example.com>"
        msgid = ob.mime_message_id
        await db.commit()
    assert (await client.delete(f"/api/v1/labels/{gone}")).status_code in (200, 204)
    # The inbox copy of the note arrives before the worker stores the sent copy.
    from mailr.core import sendgate
    from mailr.core.drafts import build_for_payload

    async with sessionmaker()() as db:
        ob = await db.get(Outbox, oid)
        u = await db.get(User, user.id)
        data, _, _ = await build_for_payload(db, sendgate._SystemPrincipal(u), ob.payload, message_id=msgid)
    mid, _ = await _ingest(user, data, roles=["inbox"], source="connector", run_rules=True)
    await _store(user, oid, data)  # the worker stores the bytes it sent (a rebuild has a new Date)
    async with sessionmaker()() as db:
        m = await db.get(Message, mid)
    assert keep in m.label_ids and gone not in m.label_ids and later in m.label_ids
    assert m.sent_by_me and m.origin == "android-share"
    assert datetime.now(UTC) > m.received_at
