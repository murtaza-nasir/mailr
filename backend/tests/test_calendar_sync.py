# ACT-SURFACES: api
"""Calendar mirror sync against the dev Radicale (:15232): the server's capabilities, the first
sync, incremental sync after changes made by another client, an invalid sync token, a
collection removed upstream; conflicts (409), delta and restricted tokens."""

import uuid
from datetime import UTC, datetime, timedelta

import caldav
import httpx
import pytest
from dev_services import RADICALE
from dev_services import radicale_up as _radicale_up
from helpers.calendar_sync import _connect
from sqlalchemy import select, text

from mailr.db import sessionmaker
from mailr.models import CalCollection, CalObject, Connector

pytestmark = pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")


def _ics(uid: str, start: datetime, summary: str, extra: str = "") -> str:
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Other client//EN\r\nBEGIN:VEVENT\r\n"
            f"UID:{uid}\r\nDTSTAMP:20260920T100000Z\r\nDTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}\r\n"
            f"DTEND:{(start + timedelta(hours=1)).strftime('%Y%m%dT%H%M%SZ')}\r\nSUMMARY:{summary}\r\n{extra}"
            "END:VEVENT\r\nEND:VCALENDAR\r\n")


def _day(days: int, hour: int) -> datetime:
    return (datetime.now(UTC) + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)


async def _changes(user_id: int, entity: str | None = None) -> list[tuple[str, int, str]]:
    async with sessionmaker()() as db:
        q = "SELECT entity, entity_id, op FROM change_log WHERE user_id=:u"
        if entity:
            q += " AND entity=:e"
        return [tuple(r) for r in (await db.execute(text(q + " ORDER BY id"), {"u": user_id, "e": entity})).all()]


async def _agenda(client, start: datetime, days: int = 1) -> list[dict]:
    r = await client.get("/api/v1/calendar/agenda", params={
        "from": start.date().isoformat(), "to": (start + timedelta(days=days)).date().isoformat()})
    assert r.status_code == 200, r.text
    return r.json()["events"]


async def test_radicale_supports_sync_collection_and_conditional_writes():
    """The design depends on these (RFC 6578 sync-collection, strong ETags, If-Match and
    If-None-Match with 412); checked first, so a server surprise shows up at once."""
    from mailr.calendar.dav import DavClient, InvalidSyncToken, PreconditionFailed

    username = f"u{uuid.uuid4().hex[:8]}"
    caldav.DAVClient(url=RADICALE, username=username, password="x").principal().make_calendar(name="Probe")
    async with DavClient(RADICALE, username, "x") as d:
        [coll] = await d.collections(await d.home())
        assert coll.sync_token and coll.ctag
        changed, removed, token, truncated = await d.sync_collection(coll.href, None)
        assert changed == [] and removed == [] and token and not truncated
        href = coll.href + "probe@mailr.ics"
        etag = await d.put(href, _ics("probe@mailr", _day(1, 9), "Probe"), if_none_match=True)
        assert etag and etag.startswith('"')
        with pytest.raises(PreconditionFailed):
            await d.put(href, _ics("probe@mailr", _day(1, 9), "Again"), if_none_match=True)
        with pytest.raises(PreconditionFailed):
            await d.put(href, _ics("probe@mailr", _day(1, 9), "Stale"), if_match='"not-the-etag"')
        etag2 = await d.put(href, _ics("probe@mailr", _day(1, 9), "Fresh"), if_match=etag)
        assert etag2 and etag2 != etag
        changed, removed, token2, _ = await d.sync_collection(coll.href, token)
        assert changed == [(d.abs(href), etag2)] and removed == [] and token2 != token
        await d.delete(href, if_match=etag2)
        changed, removed, _, _ = await d.sync_collection(coll.href, token2)
        assert removed == [d.abs(href)]
        with pytest.raises(InvalidSyncToken):
            await d.sync_collection(coll.href, "http://radicale.org/ns/sync/not-a-token")
        assert "calendar-access" in await d.options(coll.href)


async def test_every_hop_passes_the_outbound_guard(monkeypatch):
    """A redirect to a refused address is never followed."""
    from mailr.calendar import dav
    from mailr.core.netguard import UnsafeTarget

    seen = []

    async def guard(url, public_only=False, private_ok=None):
        seen.append(url)
        if "169.254" in url:
            raise UnsafeTarget("metadata")

    def handler(request):
        return httpx.Response(302, headers={"Location": "https://169.254.169.254/latest/"})

    monkeypatch.setattr(dav.netguard, "check_url", guard)
    c = dav.DavClient("https://cal.example.org/dav/", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(dav.DavError, match="metadata"):  # the guard's refusal, as a DAV error
        await c.request("PROPFIND", "https://cal.example.org/dav/")
    assert seen == ["https://cal.example.org/dav/", "https://169.254.169.254/latest/"]


async def test_first_sync_reads_existing_data_with_one_change_per_collection(client, user):
    username = f"u{uuid.uuid4().hex[:8]}"
    pr = caldav.DAVClient(url=RADICALE, username=username, password="x").principal()
    cal = pr.make_calendar(name="Work", supported_calendar_component_set=["VEVENT", "VTODO"])
    start = _day(2, 9)
    for i in range(5):
        cal.save_event(_ics(f"pre-{i}-{uuid.uuid4().hex[:6]}", start + timedelta(hours=i), f"Meeting {i}"))
    cal.save_todo(summary="Existing task")
    r = await client.post("/api/v1/calendar/connect", json={"url": RADICALE, "username": username, "password": "x"})
    assert r.status_code == 200, r.text
    evs = await _agenda(client, start)
    assert sorted(e["summary"] for e in evs) == [f"Meeting {i}" for i in range(5)]
    assert all(e["etag"] and e["id"] and e["calendar_id"] for e in evs)
    assert [t["summary"] for t in (await client.get("/api/v1/tasks")).json()["items"]] == ["Existing task"]
    ch = await _changes(user.id)
    # The first sync records the collection, not one row per object.
    assert {c[0] for c in ch} == {"calendar"}
    async with sessionmaker()() as db:
        coll = (await db.execute(select(CalCollection).where(CalCollection.user_id == user.id))).scalar_one()
        assert coll.sync_token and coll.sync_supported and coll.last_full_sync_at


async def test_incremental_sync_after_changes_by_another_client(client, user):
    from mailr.calendar import sync

    _, cals, cid = await _connect(client)
    cal = cals["Personal"]
    start = _day(3, 10)
    await _agenda(client, start)  # first sync
    before = len(await _changes(user.id))
    uid = f"ext-{uuid.uuid4().hex[:8]}"
    ev = cal.save_event(_ics(uid, start, "Planning"))
    res = await sync.sync_connector(cid)
    assert res.changed == 1 and not res.busy
    async with sessionmaker()() as db:
        obj = (await db.execute(select(CalObject).where(CalObject.uid == uid))).scalar_one()
        assert obj.summary == "Planning" and obj.etag
        oid = obj.id
    assert (await _changes(user.id))[before:] == [("event", oid, "upsert")]
    # Modify, then delete, from the other client.
    ev.data = _ics(uid, start + timedelta(hours=2), "Planning (moved)")
    ev.save()
    res = await sync.sync_connector(cid)
    assert res.changed == 1
    [e] = [x for x in await _agenda(client, start) if x["uid"] == uid]
    assert e["summary"] == "Planning (moved)" and datetime.fromisoformat(e["start"]) == start + timedelta(hours=2)
    ev.delete()
    res = await sync.sync_connector(cid)
    assert res.deleted == 1
    assert ("event", oid, "delete") in await _changes(user.id)
    assert [x for x in await _agenda(client, start) if x["uid"] == uid] == []
    # Nothing changed: no new rows and no changes recorded.
    n = len(await _changes(user.id))
    res = await sync.sync_connector(cid)
    assert res.changed == res.deleted == 0 and len(await _changes(user.id)) == n


async def test_reads_see_another_clients_change_without_the_worker(client, user):
    _, cals, _ = await _connect(client)
    start = _day(4, 11)
    assert await _agenda(client, start) == []
    cals["Personal"].save_event(_ics(f"ext-{uuid.uuid4().hex[:8]}", start, "Dentist"))
    assert [e["summary"] for e in await _agenda(client, start)] == ["Dentist"]


async def test_invalid_sync_token_falls_back_to_a_full_resync(client, user):
    from mailr.calendar import sync

    _, cals, cid = await _connect(client)
    start = _day(5, 9)
    await _agenda(client, start)
    async with sessionmaker()() as db:
        await db.execute(text("UPDATE cal_collections SET sync_token='http://radicale.org/ns/sync/bogus' "
                              "WHERE connector_id=:c"), {"c": cid})
        await db.commit()
    cals["Personal"].save_event(_ics(f"tok-{uuid.uuid4().hex[:8]}", start, "After the bad token"))
    res = await sync.sync_connector(cid)
    assert res.error is None and res.changed == 1
    async with sessionmaker()() as db:
        coll = (await db.execute(select(CalCollection).where(CalCollection.connector_id == cid))).scalar_one()
        assert coll.sync_token and "bogus" not in coll.sync_token and coll.sync_status == "idle"
    assert [e["summary"] for e in await _agenda(client, start)] == ["After the bad token"]


async def test_collection_removed_upstream(client, user):
    from mailr.calendar import sync

    _, cals, cid = await _connect(client, "Keep", "Drop")
    start = _day(6, 9)
    cals["Drop"].save_event(_ics(f"drop-{uuid.uuid4().hex[:8]}", start, "Goes away"))
    cals["Keep"].save_event(_ics(f"keep-{uuid.uuid4().hex[:8]}", start, "Stays"))
    assert sorted(e["summary"] for e in await _agenda(client, start)) == ["Goes away", "Stays"]
    async with sessionmaker()() as db:
        drop = (await db.execute(select(CalCollection).where(CalCollection.connector_id == cid,
                                                             CalCollection.displayname == "Drop"))).scalar_one()
    cals["Drop"].delete()
    await sync.sync_connector(cid)
    assert [e["summary"] for e in await _agenda(client, start)] == ["Stays"]
    names = [c["name"] for c in (await client.get("/api/v1/calendar/calendars")).json()]
    assert names == ["Keep"]
    ch = await _changes(user.id)
    assert ("calendar", drop.id, "delete") in ch and any(c[0] == "event" and c[2] == "delete" for c in ch)


async def test_unchanged_poll_is_one_propfind(client, user, monkeypatch):
    """With nothing new on the server, a sync is the discovery PROPFIND and nothing else."""
    from mailr.calendar import dav, sync

    _, _, cid = await _connect(client)
    await _agenda(client, _day(1, 9))
    calls = []
    orig = dav.DavClient.request

    async def counting(self, method, url, **kw):
        calls.append(method)
        return await orig(self, method, url, **kw)

    monkeypatch.setattr(dav.DavClient, "request", counting)
    await sync.sync_connector(cid)
    assert calls == ["PROPFIND"]


async def test_sign_in_failure_pauses_the_account(client, user, monkeypatch):
    """A 401 marks the account (sign-in failed), stops polling it, and reads keep serving the mirror."""
    from mailr.calendar import sync

    _, cals, cid = await _connect(client)
    start = _day(2, 13)
    cals["Personal"].save_event(_ics(f"a-{uuid.uuid4().hex[:8]}", start, "Kept"))
    assert len(await _agenda(client, start)) == 1
    real = sync.dav_client

    def refusing(conn, user):
        c = real(conn, user)
        c.http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401)))
        return c

    monkeypatch.setattr(sync, "dav_client", refusing)
    res = await sync.sync_connector(cid)
    assert res.error
    async with sessionmaker()() as db:
        c = await db.get(Connector, cid)
        assert c.status == "error" and "Sign-in failed" in c.status_detail and c.state["caldav"]["auth_failed"]
    st = (await client.get("/api/v1/calendar/sync-status")).json()
    assert st["connectors"][0]["error_kind"] == "auth"
    assert [e["summary"] for e in await _agenda(client, start)] == ["Kept"]
    jobs_before = await _jobs(user.id)
    await sync.due_syncs()
    assert await _jobs(user.id) == jobs_before


async def _jobs(user_id: int) -> int:
    async with sessionmaker()() as db:
        return (await db.execute(text("SELECT count(*) FROM jobs WHERE user_id=:u AND kind='calendar.sync'"),
                                 {"u": user_id})).scalar_one()


# ---------------- writes: ETags and conflicts ---------------------


async def _set_user_tz(user_id: int, name: str) -> None:
    async with sessionmaker()() as db:
        from mailr.models import User

        u = await db.get(User, user_id)
        u.settings = {**(u.settings or {}), "timezone": name}
        await db.commit()


async def _server_event(cals, uid):
    return cals["Personal"].event_by_uid(uid)


async def test_stale_etag_gets_409_with_the_server_copy(client, user):
    _, cals, _ = await _connect(client)
    start = _day(2, 9)
    ev = (await client.post("/api/v1/calendar/events", json={"summary": "Review", "start": start.isoformat()})).json()
    assert ev["etag"] and ev["occurrence"] == start.isoformat() and ev["tz"] == "UTC"
    other = await _server_event(cals, ev["uid"])
    other.icalendar_component["summary"] = "Review (changed elsewhere)"
    other.save()
    r = await client.patch(f"/api/v1/calendar/events/{ev['uid']}", json={"location": "Room 1", "etag": ev["etag"]})
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["error"] == "conflict" and body["kind"] == "event"
    assert body["server"]["summary"] == "Review (changed elsewhere)" and body["server"]["etag"] != ev["etag"]
    # The header form, with the fresh version, goes through.
    r = await client.patch(f"/api/v1/calendar/events/{ev['uid']}", json={"location": "Room 1"},
                           headers={"If-Match": body["server"]["etag"]})
    assert r.status_code == 200, r.text
    assert r.json()["location"] == "Room 1" and r.json()["summary"] == "Review (changed elsewhere)"
    # Tasks and deletes use the same rule.
    t = (await client.post("/api/v1/tasks", json={"summary": "Send it"})).json()
    ok = (await client.patch(f"/api/v1/tasks/{t['uid']}", json={"priority": 1, "etag": t["etag"]})).json()
    assert ok["priority"] == 1 and ok["etag"] != t["etag"]
    r = await client.patch(f"/api/v1/tasks/{t['uid']}", json={"priority": 5, "etag": t["etag"]})
    assert r.status_code == 409 and r.json()["server"]["priority"] == 1
    r = await client.delete(f"/api/v1/tasks/{t['uid']}", params={"etag": t["etag"]})
    assert r.status_code == 409
    assert (await client.delete(f"/api/v1/tasks/{t['uid']}", params={"etag": ok["etag"]})).status_code == 200


async def test_write_without_a_version_is_applied_again_on_the_fresh_copy(client, user, monkeypatch):
    """The mirror is behind (no sync on read here): the PUT gets 412, the change is applied to the
    server's copy, and the other client's change survives."""
    from mailr.calendar import mirror, writer

    _, cals, _ = await _connect(client)
    start = _day(3, 9)
    ev = (await client.post("/api/v1/calendar/events", json={"summary": "Sync", "start": start.isoformat()})).json()
    monkeypatch.setattr(mirror, "read_refresh_seconds", lambda: 1e9)
    refreshed = []
    real_refresh = writer.refresh

    async def counting(db, obj, client=None):
        refreshed.append(obj.uid)
        return await real_refresh(db, obj, client)

    monkeypatch.setattr(writer, "refresh", counting)
    other = await _server_event(cals, ev["uid"])
    other.icalendar_component["location"] = "Lab"
    other.save()
    r = await client.patch(f"/api/v1/calendar/events/{ev['uid']}", json={"summary": "Sync (renamed)"})
    assert r.status_code == 200, r.text
    assert r.json()["summary"] == "Sync (renamed)" and r.json()["location"] == "Lab"
    assert refreshed == [ev["uid"]]  # one 412, one fresh copy, applied again
    # With a version that was current in the (stale) mirror, the client is told instead.
    other = await _server_event(cals, ev["uid"])
    other.icalendar_component["location"] = "Lab 2"
    other.save()
    r = await client.patch(f"/api/v1/calendar/events/{ev['uid']}", json={"summary": "Again",
                                                                         "etag": r.json()["etag"]})
    assert r.status_code == 409 and r.json()["server"]["location"] == "Lab 2"


async def test_create_with_an_existing_uid_is_409(client, user):
    await _connect(client)
    uid = f"{uuid.uuid4()}@mailr-android"
    body = {"uid": uid, "summary": "Once", "start": _day(1, 9).isoformat()}
    assert (await client.post("/api/v1/calendar/events", json=body)).status_code == 200
    r = await client.post("/api/v1/calendar/events", json=body)
    assert r.status_code == 409 and r.json()["error"] == "exists" and r.json()["server"]["uid"] == uid


async def test_delete_of_an_object_already_gone_succeeds(client, user, monkeypatch):
    from mailr.calendar import mirror

    _, cals, _ = await _connect(client)
    ev = (await client.post("/api/v1/calendar/events", json={"summary": "Gone", "start": _day(1, 9).isoformat()})).json()
    monkeypatch.setattr(mirror, "read_refresh_seconds", lambda: 1e9)
    (await _server_event(cals, ev["uid"])).delete()
    assert (await client.delete(f"/api/v1/calendar/events/{ev['uid']}")).status_code == 200
    assert (await client.get(f"/api/v1/calendar/events/{ev['uid']}")).status_code == 404


# ---------------- time zones on write and read ----------------


async def test_writes_use_the_users_zone_and_floating_reads_follow_it(client, user):
    _, cals, _ = await _connect(client)
    await _set_user_tz(user.id, "Europe/London")
    r = await client.post("/api/v1/calendar/events", json={"summary": "Weekly", "start": "2026-10-12T09:00:00",
                                                           "end": "2026-10-12T09:30:00", "recurrence": "FREQ=WEEKLY;COUNT=4"})
    assert r.status_code == 200, r.text
    ev = r.json()
    assert ev["tz"] == "Europe/London" and ev["start"] == "2026-10-12T08:00:00+00:00"
    raw = (await _server_event(cals, ev["uid"])).data
    assert "DTSTART;TZID=Europe/London:20261012T090000" in raw and "BEGIN:VTIMEZONE" in raw
    evs = [e for e in (await client.get("/api/v1/calendar/agenda", params={"from": "2026-10-10", "to": "2026-11-05"})
                       ).json()["events"] if e["uid"] == ev["uid"]]
    assert [e["occurrence"] for e in evs] == ["2026-10-12T08:00:00+00:00", "2026-10-19T08:00:00+00:00",
                                              "2026-10-26T09:00:00+00:00", "2026-11-02T09:00:00+00:00"]
    # A floating event from another client is read in the user's zone.
    cals["Personal"].save_event("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:x\r\nBEGIN:VEVENT\r\nUID:float-1\r\n"
                                "DTSTAMP:20260101T000000Z\r\nDTSTART:20261020T140000\r\nDTEND:20261020T150000\r\n"
                                "SUMMARY:Floating\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    [f] = [e for e in (await client.get("/api/v1/calendar/agenda", params={"from": "2026-10-20", "to": "2026-10-21"})
                       ).json()["events"] if e["uid"] == "float-1"]
    assert f["start"] == "2026-10-20T13:00:00+00:00" and f["tz"] == "Europe/London"
    # Moving one occurrence keeps the series zone; the override is matched by its occurrence string.
    r = await client.patch(f"/api/v1/calendar/events/{ev['uid']}", json={
        "start": "2026-10-26T11:00:00", "end": "2026-10-26T11:30:00", "scope": "this",
        "occurrence": "2026-10-26T09:00:00+00:00"})
    assert r.status_code == 200, r.text
    assert r.json()["occurrence"] == "2026-10-26T09:00:00+00:00" and r.json()["start"] == "2026-10-26T11:00:00+00:00"
    raw = (await _server_event(cals, ev["uid"])).data
    assert "RECURRENCE-ID;TZID=Europe/London:20261026T090000" in raw


# ---------------- delta, sync routes and restricted tokens ----------------


async def test_delta_cursor_tombstones_and_reset(client, user):
    _, cals, _ = await _connect(client)
    start = _day(2, 9)
    a = (await client.post("/api/v1/calendar/events", json={"summary": "A", "start": start.isoformat(),
                                                           "recurrence": "FREQ=DAILY;COUNT=3"})).json()
    t = (await client.post("/api/v1/tasks", json={"summary": "T"})).json()
    d = (await client.get("/api/v1/calendar/delta")).json()
    assert not d["reset"] and not d["more"] and [c["name"] for c in d["collections"]] == ["Personal"]
    kinds = {o["uid"]: o["kind"] for o in d["objects"]}
    assert kinds == {a["uid"]: "event", t["uid"]: "task"}
    assert len(d["instances"][a["uid"]]) == 3 and d["instances"][a["uid"]][0]["occurrence"] == start.isoformat()
    cur = d["cursor"]
    assert (await client.get("/api/v1/calendar/delta", params={"cursor": cur})).json()["objects"] == []
    # A change from another client, and a delete.
    other = await _server_event(cals, a["uid"])
    other.icalendar_component["summary"] = "A2"
    other.save()
    await client.delete(f"/api/v1/tasks/{t['uid']}")
    d2 = (await client.get("/api/v1/calendar/delta", params={"cursor": cur})).json()
    assert [o["summary"] for o in d2["objects"]] == ["A2"]
    assert d2["deleted"] == [{"id": t["id"], "uid": t["uid"], "kind": "task"}]
    assert d2["cursor"] > cur
    # Paging.
    p1 = (await client.get("/api/v1/calendar/delta", params={"limit": 1})).json()
    assert p1["more"] and len(p1["collections"]) + len(p1["objects"]) + len(p1["deleted"]) == 1
    # A cursor older than the purge horizon must start again.
    async with sessionmaker()() as db:
        from mailr.models import User

        u = await db.get(User, user.id)
        u.settings = {**(u.settings or {}), "calendar_purged_rev": d2["cursor"] + 1}
        await db.commit()
    assert (await client.get("/api/v1/calendar/delta", params={"cursor": cur})).json()["reset"] is True


async def test_sync_routes(client, user):
    _, cals, cid = await _connect(client)
    st = (await client.get("/api/v1/calendar/sync-status")).json()
    assert st["connectors"][0]["id"] == cid and st["connectors"][0]["error_kind"] is None
    cals["Personal"].save_event(_ics(f"s-{uuid.uuid4().hex[:6]}", _day(1, 8), "Pulled"))
    r = await client.post("/api/v1/calendar/sync", json={"wait": True})
    assert r.status_code == 200 and r.json()["queued"] is False and r.json()["state"] == "idle"
    assert r.json()["synced"] >= 1
    st = (await client.get("/api/v1/calendar/sync-status")).json()
    assert st["state"] == "idle" and st["connectors"][0]["collections"][0]["state"] == "idle"
    assert (await client.post("/api/v1/calendar/sync", json={})).json() == {"queued": True}
    ag = (await client.get("/api/v1/calendar/agenda")).json()
    assert ag["sync"]["state"] == "idle"


async def test_restricted_token_sees_only_its_connectors_in_delta_and_changes(client, user):
    from helpers.security_authz import _caldav, _token

    from mailr.calendar import sync

    cid_a, cal_a = await _caldav(user.id)
    cid_b, cal_b = await _caldav(user.id, "local_only")
    start = _day(1, 10)
    await client.get("/api/v1/calendar/agenda")  # first sync of both
    since = await _max_change(user.id)
    cal_a.save_event(_ics(f"a-{uuid.uuid4().hex[:6]}", start, "Team sync"))
    cal_b.save_event(_ics(f"b-{uuid.uuid4().hex[:6]}", start, "Student record meeting"))
    await sync.sync_connector(cid_a)
    await sync.sync_connector(cid_b)
    tok = await _token(client, ["read", "calendar"])
    d = (await tok.get("/api/v1/calendar/delta")).json()
    assert [o["summary"] for o in d["objects"]] == ["Team sync"]
    assert {c["connector_id"] for c in d["collections"]} == {cid_a}
    ch = (await tok.get("/api/v1/changes", params={"since": since})).json()["changes"]
    async with sessionmaker()() as db:
        ids = dict((await db.execute(select(CalObject.summary, CalObject.id).where(CalObject.user_id == user.id))).all())
    events = {c["id"] for c in ch if c["entity"] == "event"}
    assert ids["Team sync"] in events and ids["Student record meeting"] not in events
    mine = (await client.get("/api/v1/changes", params={"since": since})).json()["changes"]
    assert ids["Student record meeting"] in {c["id"] for c in mine if c["entity"] == "event"}
    await tok.aclose()


async def _max_change(user_id: int) -> int:
    async with sessionmaker()() as db:
        return (await db.execute(text("SELECT coalesce(max(id), 0) FROM change_log WHERE user_id=:u"),
                                 {"u": user_id})).scalar_one()
