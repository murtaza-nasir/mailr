"""push.touches_local_only names more than mail (outbound data policy, gap 14): a notification
whose data names a local-only note or table, a calendar item of a local-only account, or a
recording of a local-only Speakr account is private even when the caller forgot `private=`.
The same kinds of item without the mark stay ordinary."""

import uuid

from mailr.core import push
from mailr.db import sessionmaker
from mailr.models import CalCollection, CalObject, Connector, SpeakrRecording


async def _cal(db, user_id, data_class):
    c = Connector(user_id=user_id, kind="caldav", name=f"cal-{data_class}", mode="mirror", data_class=data_class,
                  enabled=False, status="paused", config={})
    db.add(c)
    await db.flush()
    col = CalCollection(user_id=user_id, connector_id=c.id, href=f"/cal/{uuid.uuid4().hex}/")
    db.add(col)
    await db.flush()
    uid = f"{uuid.uuid4().hex}@test"
    obj = CalObject(user_id=user_id, connector_id=c.id, collection_id=col.id, href=f"{col.href}{uid}.ics",
                    uid=uid, kind="event")
    db.add(obj)
    s = Connector(user_id=user_id, kind="speakr", name=f"sp-{data_class}", data_class=data_class, config={})
    db.add(s)
    await db.flush()
    rec = SpeakrRecording(user_id=user_id, connector_id=s.id, recording_id=4000 + s.id, title="R", content_hash="")
    db.add(rec)
    await db.flush()
    return obj, s, rec


async def test_notes_tables_calendar_and_recordings_make_a_push_private(client, user):
    async def note(local):
        r = await client.post("/api/v1/notes", json={"kind": "text", "title": "N", "body": "b", "local_only": local})
        assert r.status_code == 200, r.text
        return r.json()["note"]["id"]

    async def table(local):
        r = await client.post("/api/v1/tables", json={"name": f"T{uuid.uuid4().hex[:4]}", "columns": [{"name": "A"}],
                                                      "local_only": local})
        assert r.status_code == 200, r.text
        return r.json()["table"]["id"]

    n_lo, n_ok, t_lo, t_ok = await note(True), await note(False), await table(True), await table(False)
    async with sessionmaker()() as db:
        lo_obj, lo_sp, lo_rec = await _cal(db, user.id, "local_only")
        ok_obj, ok_sp, ok_rec = await _cal(db, user.id, "normal")
        await db.commit()
        t = push.touches_local_only
        assert await t(db, user.id, {"note_id": n_lo}) and not await t(db, user.id, {"note_id": n_ok})
        assert await t(db, user.id, {"item": {"kind": "note", "uid": n_lo}})
        assert await t(db, user.id, {"table_id": t_lo}) and not await t(db, user.id, {"table_id": t_ok})
        assert await t(db, user.id, {"object_id": lo_obj.id}) and not await t(db, user.id, {"object_id": ok_obj.id})
        assert await t(db, user.id, {"item": {"kind": "event", "uid": lo_obj.uid}})
        assert not await t(db, user.id, {"item": {"kind": "event", "uid": ok_obj.uid}})
        assert await t(db, user.id, {"recording_id": lo_rec.recording_id})
        assert not await t(db, user.id, {"recording_id": ok_rec.recording_id, "connector_id": ok_sp.id})
