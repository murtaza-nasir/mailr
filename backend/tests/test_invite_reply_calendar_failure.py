"""An invitation answer whose calendar copy fails (a calendar server's HTTP 500, as Davis gave when it
tried to mail the reply itself with no mail server): the reply still goes out, the failure is logged,
and the answer carries a reason the apps show."""

import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from dev_services import radicale_up
from helpers.calendar import _connect, _invite

from mailr.calendar import dav
from mailr.core.ingest import IngestOptions, ingest_raw
from mailr.core.store import user_store
from mailr.db import sessionmaker
from mailr.models import Connector, Identity, User

pytestmark = pytest.mark.skipif(not radicale_up(), reason="Radicale dev server not running on :15232")


async def test_a_calendar_server_error_is_logged_and_explained(client, user, monkeypatch, caplog):
    await _connect(client)
    async with sessionmaker()() as db:
        c = Connector(user_id=user.id, kind="imap", name="out", mode="mirror", enabled=False, status="paused", config={})
        db.add(c)
        await db.flush()
        me = f"me-{c.id}@example.com"
        db.add(Identity(user_id=user.id, connector_id=c.id, email=me, name="Me", is_default=True, receives=[]))
        await db.commit()
    uid = f"inv-{uuid.uuid4().hex[:10]}"
    start = (datetime.now(UTC) + timedelta(days=3)).replace(hour=14, minute=0, second=0, microsecond=0)
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        mid = (await ingest_raw(db, await user_store(db, u), _invite(uid, me, start), IngestOptions())).message.id
        await db.commit()

    from mailr.calendar import client as cal

    async def failing(*a, **kw):
        raise dav.ServerError("PUT https://cal.example.com/dav/calendars/me/default/x.ics: HTTP 500", 500)

    monkeypatch.setattr(cal, "set_partstat", failing)
    with caplog.at_level(logging.WARNING, logger="mailr.calendar"):
        r = await client.post("/api/v1/calendar/invite-reply", json={"message_id": mid, "response": "accepted"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["calendar"].startswith("error") and out["outbox"]["status"] == "queued"
    assert out["calendar_reason"] == "your calendar server answered with an error (HTTP 500)"
    assert any("calendar update failed" in rec.getMessage() for rec in caplog.records)
    assert (await client.post(f"/api/v1/outbox/{out['outbox']['id']}/cancel")).status_code == 200

