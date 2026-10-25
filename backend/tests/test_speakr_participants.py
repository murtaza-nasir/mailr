# ACT-SURFACES: api
"""Recording participants by person (participant.merge; participants.py): each participant string
resolves to one identity: the per-recording override, the speaker mapping of the name, the user
(the user's own name, or the first name alone with nobody else of that first name in the
recording), the one correspondent in People with that full name, an alias, else the string. The
list's people, the Participants overview's counts, the `participant` filter and `participant:` in
the search use the identity; merges are aliases per connector, Separate undoes them, suggested
merges can be dismissed."""

from datetime import UTC, datetime, timedelta

from speakr_fake import connect, speakr  # noqa: F401

from mailr.db import sessionmaker
from mailr.integrations.speakr.participants import display
from mailr.models import Person, SpeakrSpeakerPerson


async def _setup(client, user, speakr):
    now = datetime.now(UTC)
    # The fixture's user is "Test User".
    speakr.add(1, "Strategy review", meeting_date=now - timedelta(days=1),
               participants="Test User, Ross Turner, Dana Smith, Evan")
    speakr.add(2, "Strategy follow-up", meeting_date=now - timedelta(days=2), participants="Test, Ross, Evan",
               segments=[(0, "Ross", "Hello."), (5, "Test", "Hi Ross.")])
    speakr.speakers[2] = [{"label": "SPEAKER_00", "identified_name": "Ross", "speaker_id": 7, "segment_count": 1},
                          {"label": "SPEAKER_01", "identified_name": "Test", "speaker_id": 8, "segment_count": 1}]
    speakr.add(3, "Two of a name", meeting_date=now - timedelta(days=3), participants="Test, Test Kowalski")
    speakr.add(4, "Old one", meeting_date=now - timedelta(days=4), participants="Dana  Smith")
    conn = await connect(client, speakr)
    cid = conn["id"]
    async with sessionmaker()() as db:
        db.add_all([
            Person(user_id=user.id, email="dana@guest.example", name="Dana Smith"),
            SpeakrSpeakerPerson(user_id=user.id, connector_id=cid, speaker_key="name:evan", speaker_name="Evan",
                                email="evan@guest.example", state="confirmed", source="user"),
            SpeakrSpeakerPerson(user_id=user.id, connector_id=cid, speaker_key="rec:2:name:evan", speaker_name="",
                                email="evan.other@guest.example", state="confirmed", source="user"),
        ])
        await db.commit()
    return cid


async def _people(client) -> dict[int, list[dict]]:
    items = (await client.get("/api/v1/speakr/recordings", params={"limit": 50, "annotate": "true"})).json()["items"]
    return {x["recording_id"]: x["people"] for x in items}


def _keys(people: list[dict]) -> list[str]:
    return [p["key"] for p in people]


async def test_resolution_order_and_me_first_name(client, user, speakr):
    await _setup(client, user, speakr)
    by = await _people(client)
    # Recording 1: the user's full name, a raw full name, People's full name, the mapping.
    assert _keys(by[1]) == ["me", "name:ross turner", "email:dana@guest.example", "email:evan@guest.example"]
    assert [p["how"] for p in by[1]] == ["me", "raw", "people", "mapped"]
    assert by[1][0]["label"] == "You" and by[1][0]["me"] is True
    # Recording 2: the first name alone is the user; Evan is someone else here (the override).
    assert _keys(by[2]) == ["me", "name:ross", "email:evan.other@guest.example"]
    assert [p["how"] for p in by[2]] == ["me_first", "raw", "override"]
    # Recording 3: another Test in the recording, so the first name alone is not the user.
    assert _keys(by[3]) == ["name:test", "name:test kowalski"]
    # Spaces and case do not matter; "Last, First" (in People or a name) is "First Last".
    assert _keys(by[4]) == ["email:dana@guest.example"] and by[4][0]["label"] == "Dana Smith"
    assert display("Smith, Dana") == "Dana Smith"


async def test_counts_by_identity_and_merge_separate(client, user, speakr):
    cid = await _setup(client, user, speakr)
    ov = (await client.get("/api/v1/speakr/participants")).json()
    people = {p["key"]: p for p in ov["people"]}
    # You first, once per recording, merging the full name and the first name.
    assert ov["people"][0]["key"] == "me" and people["me"]["count"] == 2
    assert set(people["me"]["names"]) == {"Test User", "Test"}
    assert people["email:dana@guest.example"]["count"] == 2
    assert set(people["email:dana@guest.example"]["names"]) == {"Dana Smith"}

    # Suggested: Ross into Ross Turner (never in the same recording, same folder and meeting people).
    sug = [s for s in ov["suggestions"] if s["name"] == "ross"]
    assert sug and sug[0]["into"] == "name:ross turner" and sug[0]["into_label"] == "Ross Turner"
    assert not ov["aliases"]

    # ACT participant.merge (the suggestion's Merge)
    r = await client.post("/api/v1/speakr/participants/merge",
                          json={"connector_id": cid, "name": "Ross", "into": "name:ross turner"})
    assert r.status_code == 200, r.text
    assert r.json()["merged"] == ["Ross"]
    by = await _people(client)
    assert by[2][1]["key"] == "name:ross turner" and by[2][1]["how"] == "alias"
    assert by[2][1]["label"] == "Ross Turner"
    ov = (await client.get("/api/v1/speakr/participants")).json()
    ross = next(p for p in ov["people"] if p["key"] == "name:ross turner")
    assert ross["count"] == 2 and set(ross["names"]) == {"Ross Turner", "Ross"}
    assert not [s for s in ov["suggestions"] if s["name"] == "ross"]

    # The filter by the merged person finds the recordings of both strings; so does participant: in q.
    ids = lambda r: sorted(x["recording_id"] for x in r.json()["items"])
    assert ids(await client.get("/api/v1/speakr/recordings", params={"participant": "Ross Turner"})) == [1, 2]
    assert ids(await client.get("/api/v1/speakr/recordings", params={"participant": "Ross"})) == [1, 2]
    assert ids(await client.get("/api/v1/speakr/recordings", params={"q": 'participant:"Ross Turner"'})) == [1, 2]
    assert ids(await client.get("/api/v1/speakr/recordings", params={"participant": "me"})) == [1, 2]
    assert ids(await client.get("/api/v1/speakr/recordings", params={"participant": "dana@guest.example"})) == [1, 4]

    # An alias of a mapped name gets that name's person.
    async with sessionmaker()() as db:
        db.add(SpeakrSpeakerPerson(user_id=user.id, connector_id=cid, speaker_key="name:ross turner",
                                   speaker_name="Ross Turner", email="ross@guest.example", state="confirmed"))
        await db.commit()
    by = await _people(client)
    assert by[2][1]["key"] == "email:ross@guest.example" and by[1][1]["key"] == "email:ross@guest.example"
    # ...in the recording's People tab too.
    spk = (await client.get(f"/api/v1/speakr/recordings/{cid}/2/people")).json()["speakers"]
    ross_sp = next(s for s in spk if s["name"] == "Ross")
    assert ross_sp["person"]["email"] == "ross@guest.example" and ross_sp["via_alias"]
    me_sp = next(s for s in spk if s["name"] == "Test")
    assert me_sp["me"] is True
    # The person's recordings include the merged name's.
    pr = (await client.get("/api/v1/speakr/people/recordings", params={"email": "ross@guest.example"})).json()
    assert pr["count"] == 2

    # ACT participant.merge (Separate)
    r = await client.post("/api/v1/speakr/participants/separate", json={"connector_id": cid, "name": "Ross"})
    assert r.status_code == 200 and r.json()["separated"] == 1
    by = await _people(client)
    assert by[2][1]["key"] == "name:ross"


async def test_merge_group_into_you_separate_and_dismiss(client, user, speakr):
    cid = await _setup(client, user, speakr)
    # Recording 3's "Test" is not the user by the first-name rule; the user says it is.
    r = await client.post("/api/v1/speakr/participants/merge", json={"key": "name:test", "into": "me"})
    assert r.status_code == 200 and r.json()["merged"] == ["Test"]
    by = await _people(client)
    assert _keys(by[3]) == ["me", "name:test kowalski"]
    assert by[3][0]["how"] == "alias"
    # You cannot be merged into someone else.
    r = await client.post("/api/v1/speakr/participants/merge", json={"key": "me", "into": "name:ross"})
    assert r.status_code == 400
    # A mapped name is not changed by a merge of its group; it is named as skipped.
    r = await client.post("/api/v1/speakr/participants/merge",
                          json={"key": "email:evan@guest.example", "into": "name:ross"})
    assert r.json()["merged"] == [] and r.json()["skipped"] == ["Evan"]

    # Separate on the user's first name: "Test" stands on its own in every recording.
    r = await client.post("/api/v1/speakr/participants/separate", json={"name": "Test"})
    assert r.status_code == 200
    by = await _people(client)
    assert _keys(by[2])[0] == "name:test" and _keys(by[3])[0] == "name:test"
    # A merge into You brings it back.
    await client.post("/api/v1/speakr/participants/merge", json={"connector_id": cid, "name": "Test", "into": "me"})
    by = await _people(client)
    assert _keys(by[2])[0] == "me" and _keys(by[3])[0] == "me"

    # A dismissed suggestion is not offered again.
    ov = (await client.get("/api/v1/speakr/participants")).json()
    assert [s for s in ov["suggestions"] if s["name"] == "ross"]
    r = await client.post("/api/v1/speakr/participants/dismiss",
                          json={"connector_id": cid, "name": "ross", "into": "name:ross turner"})
    assert r.status_code == 200
    ov = (await client.get("/api/v1/speakr/participants")).json()
    assert not [s for s in ov["suggestions"] if s["name"] == "ross"]
    # Rename in Speakr needs write-back, which is off.
    r = await client.post("/api/v1/speakr/participants/rename",
                          json={"connector_id": cid, "name": "Ross", "to": "Ross Turner"})
    assert r.status_code == 403
