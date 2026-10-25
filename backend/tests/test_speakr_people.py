# ACT-SURFACES: api web.people
"""Speakers ↔ people (speaker.map): the user's own speaker
is recognised and the user's addresses are never candidates; first-name speakers get ranked
candidates from the linked event's guests, the mail around the meeting and strong correspondents;
a confirmed named speaker applies to every recording with that name, with "Not them in this
recording" and another person for one recording; unnamed labels stay in their recording; get_person
and the person card (recording.view, web.people) list recordings with the person. Needs the dev
Radicale and Stalwart."""

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import pytest
from dev_services import radicale_up as _radicale_up
from helpers.calendar import _connect
from helpers.core_flow import _ingest
from helpers.speakr_matching import _event
from helpers.speakr_tools import _ctx
from speakr_fake import connect, speakr  # noqa: F401

from mailr.agent.tools import run_tool
from mailr.core.people import decay
from mailr.db import sessionmaker
from mailr.models import Identity, Person

pytestmark = pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")


def _spk(*names):
    return [{"label": f"SPEAKER_{i:02d}", "identified_name": n, "speaker_id": None, "segment_count": 3}
            for i, n in enumerate(names)]


async def _people(client, cid, rid):
    d = (await client.get(f"/api/v1/speakr/recordings/{cid}/{rid}/people")).json()
    return d, {s["name"] or s["label"]: s for s in d["speakers"]}


# ACT speaker.map
async def test_me_first_names_and_ranking(client, user, raw, speakr):
    await _connect(client)
    t = (datetime.now(UTC) - timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
    title = "Restructuring the Information Systems Curriculum"
    await _event(client, title, t, attendees=[{"email": "mateo@lakeside.example", "name": "Mateo Yilmaz"},
                                              {"email": "ross@lakeside.example", "name": ""}])
    speakr.add(1, title, meeting_date=t, duration=3000, participants="Test User, Mateo, Aiden, Ross, Mike")
    speakr.speakers[1] = _spk("Test User", "Mateo", "Aiden", "Ross", "Mike", "SPEAKER_05")
    today = decay(datetime.now(UTC))
    async with sessionmaker()() as db:
        db.add_all([
            # The user's own addresses, one of them not an identity: never candidates.
            Identity(user_id=user.id, email="tu@second.example", name="Test User"),
            Person(user_id=user.id, email="tu@second.example", name="Test User"),
            Person(user_id=user.id, email="old@third.example", name="Test User"),
            # Aiden: strong correspondents and a weak one; the mail around the meeting names the weak one.
            # Strength is stored on a fixed time base: a message today adds
            # decay(now), which grows as the calendar moves (about 9e5 in October 2026).
            Person(user_id=user.id, email="aiden.k@x.example", name="Aiden Karimov", strength=2.2 * today,
                   sent_count=3, received_count=4, organization="Xco"),
            Person(user_id=user.id, email="aiden.r@y.example", name="Aiden Rahimov", strength=4.4 * today,
                   sent_count=0, received_count=9),
            Person(user_id=user.id, email="aiden.t@z.example", name="Aiden Tursunov", strength=1.0),
            Person(user_id=user.id, email="aiden.r2@gmail.com", name="Aiden Rahimov", strength=2.0),
            Person(user_id=user.id, email="aiden.q@q.example", name="Aiden Qodirov", strength=0.5),
            Person(user_id=user.id, email="michael@x.example", name="Michael Brown", strength=50.0),
        ])
        await db.commit()
    await _ingest(user, raw("Re: Information Systems curriculum restructuring", frm="Aiden Tursunov <aiden.t@z.example>",
                            date=format_datetime(t - timedelta(days=1))), roles=["inbox"])
    await _ingest(user, raw("Systems news", frm="Aiden Qodirov <aiden.q@q.example>",
                            date=format_datetime(t)), roles=["inbox"])  # one shared word: not about this
    conn = await connect(client, speakr)
    cid = conn["id"]

    d, by = await _people(client, cid, 1)
    me = by["Test User"]
    assert me["me"] is True and me["person"]["me"] is True and me["suggestions"] == []
    every = [c for s in d["speakers"] for c in s["suggestions"]]
    assert not any(e in c["emails"] for c in every for e in ("tu@second.example", "old@third.example"))
    # Mateo: the guest of the linked meeting.
    assert by["Mateo"]["suggestions"][0]["email"] == "mateo@lakeside.example"
    assert "in the linked meeting" in by["Mateo"]["suggestions"][0]["hints"]
    # Ross: a guest without a name, by the address.
    assert by["Ross"]["suggestions"][0]["email"] == "ross@lakeside.example"
    # Aiden: the one who emailed about it, then by strength (two-way raised); Rahimov's two
    # addresses are one candidate; three are shown and the rest counted.
    ak = by["Aiden"]["suggestions"]
    assert [c["email"] for c in ak] == ["aiden.t@z.example", "aiden.r@y.example", "aiden.k@x.example"]
    assert ak[0]["hints"] == ["emailed you about this"] and by["Aiden"]["more"] == 1
    assert ak[1]["emails"] == ["aiden.r@y.example", "aiden.r2@gmail.com"]
    assert ak[2]["organization"] == "Xco"
    # Mike: a nickname.
    assert by["Mike"]["suggestions"][0]["email"] == "michael@x.example"
    # An unnamed label: the meeting's guests, per recording.
    lab = by["SPEAKER_05"]
    assert lab["key"] == "label:1:speaker_05" and lab["applies"] == "recording"
    assert {c["email"] for c in lab["suggestions"]} == {"mateo@lakeside.example", "ross@lakeside.example"}


# ACT speaker.map
async def test_global_mapping_and_recording_override(client, user, speakr):
    t = datetime.now(UTC) - timedelta(days=5)
    for rid in (1, 2, 3):
        speakr.add(rid, f"Meeting {rid}", meeting_date=t + timedelta(days=rid), participants="Mi, Evan")
        speakr.speakers[rid] = _spk("Mi", "SPEAKER_01")
    async with sessionmaker()() as db:
        db.add_all([Person(user_id=user.id, email="mi@x.example", name="Mi Zhang", strength=10.0),
                    Person(user_id=user.id, email="mi.other@y.example", name="Mi Park", strength=1.0)])
        await db.commit()
    conn = await connect(client, speakr)
    cid = conn["id"]
    _, by = await _people(client, cid, 1)
    mi = by["Mi"]
    assert mi["key"] == "name:mi" and mi["map_key"] == "name:mi" and mi["applies"] == "all"
    assert [c["email"] for c in mi["suggestions"]] == ["mi@x.example", "mi.other@y.example"]

    # Confirming in recording 1 applies to recordings 2 and 3.
    r = await client.post(f"/api/v1/speakr/speakers/{cid}/person",
                          json={"key": mi["map_key"], "speaker_name": "Mi", "email": "mi@x.example", "name": "Mi Zhang"})
    assert r.status_code == 200 and r.json()["applies"] == "all"
    for rid in (1, 2, 3):
        _, by = await _people(client, cid, rid)
        assert by["Mi"]["person"]["email"] == "mi@x.example" and by["Mi"]["applies"] == "all"
    pr = (await client.get("/api/v1/speakr/people/recordings", params={"email": "mi@x.example"})).json()
    assert pr["count"] == 3

    # Not them in recording 2 only, and someone else there.
    _, by = await _people(client, cid, 2)
    ov = by["Mi"]["override_key"]
    assert ov == "rec:2:name:mi"
    r = await client.request("DELETE", f"/api/v1/speakr/speakers/{cid}/person", params={"key": ov, "email": "mi@x.example"})
    assert r.status_code == 200
    _, by = await _people(client, cid, 2)
    assert by["Mi"]["person"] is None and by["Mi"]["map_key"] == ov and by["Mi"]["applies"] == "recording"
    assert [c["email"] for c in by["Mi"]["suggestions"]] == ["mi.other@y.example"]
    await client.post(f"/api/v1/speakr/speakers/{cid}/person",
                      json={"key": ov, "speaker_name": "Mi", "email": "mi.other@y.example", "name": "Mi Park"})
    _, by = await _people(client, cid, 2)
    assert by["Mi"]["person"]["email"] == "mi.other@y.example" and by["Mi"]["applies"] == "recording"
    _, by = await _people(client, cid, 3)
    assert by["Mi"]["person"]["email"] == "mi@x.example"
    pr = (await client.get("/api/v1/speakr/people/recordings", params={"email": "mi@x.example"})).json()
    assert {x["recording_id"] for x in pr["items"]} == {1, 3}
    pr = (await client.get("/api/v1/speakr/people/recordings", params={"email": "mi.other@y.example"})).json()
    assert [x["recording_id"] for x in pr["items"]] == [2]

    # An unnamed label stays in its recording.
    _, by = await _people(client, cid, 1)
    await client.post(f"/api/v1/speakr/speakers/{cid}/person",
                      json={"key": by["SPEAKER_01"]["map_key"], "speaker_name": "SPEAKER_01", "email": "evan@x.example"})
    _, by1 = await _people(client, cid, 1)
    _, by3 = await _people(client, cid, 3)
    assert by1["SPEAKER_01"]["person"]["email"] == "evan@x.example" and by3["SPEAKER_01"]["person"] is None

    # Not them in any recording: the global mapping is dismissed and not offered again.
    await client.request("DELETE", f"/api/v1/speakr/speakers/{cid}/person", params={"key": "name:mi", "email": "mi@x.example"})
    _, by = await _people(client, cid, 3)
    assert by["Mi"]["person"] is None and "mi@x.example" not in [c["email"] for c in by["Mi"]["suggestions"]]


# ACT speaker.map
async def test_speaker_map_and_person_recordings(client, user, raw, speakr):
    await _connect(client)
    t = (datetime.now(UTC) - timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
    await _event(client, "Budget review", t, attendees=[{"email": "dana@guest.example", "name": "Dana Smith"},
                                                       {"email": "evan@guest.example", "name": "Evan Lee"}])
    speakr.add(1, "Budget review", meeting_date=t, duration=3000, participants="Dana Smith, Evan")
    speakr.speakers[1] = [{"label": "SPEAKER_00", "identified_name": "Dana Smith", "speaker_id": 11, "segment_count": 4},
                          {"label": "SPEAKER_01", "identified_name": "Evan", "speaker_id": 12, "segment_count": 3},
                          {"label": "SPEAKER_02", "identified_name": "", "speaker_id": None, "segment_count": 1}]
    conn = await connect(client, speakr)
    await _ingest(user, raw("Numbers", frm="Dana Smith <dana@guest.example>"), roles=["inbox"])

    d, _ = await _people(client, conn["id"], 1)
    by = {s["label"]: s for s in d["speakers"]}
    assert by["SPEAKER_00"]["key"] == "name:dana smith" and by["SPEAKER_02"]["key"] == "label:1:speaker_02"
    assert [s["email"] for s in by["SPEAKER_00"]["suggestions"]] == ["dana@guest.example"]
    assert [s["email"] for s in by["SPEAKER_01"]["suggestions"]] == ["evan@guest.example"]
    assert by["SPEAKER_00"]["person"] is None
    assert {g["email"] for g in d["guests"]} == {"dana@guest.example", "evan@guest.example"}

    r = await client.post(f"/api/v1/speakr/speakers/{conn['id']}/person",
                          json={"key": "name:dana smith", "speaker_name": "Dana Smith", "email": "dana@guest.example",
                                "name": "Dana Smith"})
    assert r.status_code == 200 and r.json()["state"] == "confirmed"
    # Evan's suggestion is removed: it does not come back.
    r = await client.request("DELETE", f"/api/v1/speakr/speakers/{conn['id']}/person",
                             params={"key": "name:evan", "email": "evan@guest.example"})
    assert r.status_code == 200
    d, _ = await _people(client, conn["id"], 1)
    by = {s["label"]: s for s in d["speakers"]}
    assert by["SPEAKER_00"]["person"]["email"] == "dana@guest.example"
    assert by["SPEAKER_00"]["person"]["name"] == "Dana Smith"
    assert by["SPEAKER_00"]["suggestions"] == [] and by["SPEAKER_01"]["suggestions"] == []
    # One person per speaker: a new choice replaces the old one.
    await client.post(f"/api/v1/speakr/speakers/{conn['id']}/person",
                      json={"key": "name:dana smith", "speaker_name": "Dana Smith", "email": "dana2@guest.example"})
    d, _ = await _people(client, conn["id"], 1)
    assert {s["label"]: s for s in d["speakers"]}["SPEAKER_00"]["person"]["email"] == "dana2@guest.example"
    await client.post(f"/api/v1/speakr/speakers/{conn['id']}/person",
                      json={"key": "name:dana smith", "speaker_name": "Dana Smith", "email": "dana@guest.example"})

    pr = (await client.get("/api/v1/speakr/people/recordings", params={"email": "dana@guest.example"})).json()
    assert pr["count"] == 1 and pr["items"][0]["recording_id"] == 1
    assert (await client.get("/api/v1/speakr/people/recordings", params={"email": "zoe@x.org"})).json()["count"] == 0
    async with sessionmaker()() as db:
        res = await run_tool(await _ctx(db, user.id), "get_person", {"email": "dana@guest.example"})
        recs = res.data["recent_recordings"]
        assert recs["count"] == 1 and recs["items"][0]["title"] == {"untrusted": True, "content": "Budget review"}
        assert res.sensitive  # local-only recordings keep the chat on local models
