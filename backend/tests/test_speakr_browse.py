# ACT-SURFACES: web.recordings
"""The Recordings app's list (web.recordings; recording.search): GET /speakr/recordings pages through
the mirror with offset and tells whether more may follow; with annotate, each recording carries its
event link (a confirmed one before a suggestion; dismissed links never) and its participants with
the address mailr knows: a per-recording override, else the connector-wide speaker mapping, else
the one correspondent in People with that full name ("Last, First" too); an address marked "Not
them in this recording" is skipped for that recording only."""

from datetime import UTC, datetime, timedelta

from speakr_fake import connect, speakr  # noqa: F401

from mailr.db import sessionmaker
from mailr.models import Person, RecordingLink, SpeakrSpeakerPerson


async def test_recordings_list_pages_and_annotates(client, user, speakr):
    now = datetime.now(UTC)
    for i in range(1, 6):
        speakr.add(i, f"Meeting {i}", meeting_date=now - timedelta(days=i),
                   participants="Dana Smith, Evan, Mara Lopez, Rita Gomez" if i == 1 else "Evan")
    conn = await connect(client, speakr)
    cid = conn["id"]

    page1 = (await client.get("/api/v1/speakr/recordings", params={"limit": 2})).json()
    page2 = (await client.get("/api/v1/speakr/recordings", params={"limit": 2, "offset": 2})).json()
    page3 = (await client.get("/api/v1/speakr/recordings", params={"limit": 2, "offset": 4})).json()
    assert [x["recording_id"] for x in page1["items"]] == [1, 2] and page1["more"] is True
    assert [x["recording_id"] for x in page2["items"]] == [3, 4] and page2["more"] is True
    assert [x["recording_id"] for x in page3["items"]] == [5] and page3["more"] is False
    assert "event_link" not in page1["items"][0]

    async with sessionmaker()() as db:
        db.add_all([
            Person(user_id=user.id, email="dana@guest.example", name="Dana Smith"),
            # Two people named Mara Lopez: no address is guessed.
            Person(user_id=user.id, email="mara1@guest.example", name="Mara Lopez"),
            Person(user_id=user.id, email="mara2@guest.example", name="Mara Lopez"),
            # People names in "Last, First" order match too.
            Person(user_id=user.id, email="rita@guest.example", name="Gomez, Rita"),
            SpeakrSpeakerPerson(user_id=user.id, connector_id=cid, speaker_key="name:evan", speaker_name="Evan",
                                email="evan@guest.example", state="confirmed", source="user"),
            # Evan in recording 2 is someone else; in recording 3 he is "Not them" (no address).
            SpeakrSpeakerPerson(user_id=user.id, connector_id=cid, speaker_key="rec:2:name:evan", speaker_name="",
                                email="evan.other@guest.example", state="confirmed", source="user"),
            SpeakrSpeakerPerson(user_id=user.id, connector_id=cid, speaker_key="rec:3:name:evan", speaker_name="",
                                email="evan@guest.example", state="dismissed", source="user"),
            RecordingLink(user_id=user.id, connector_id=cid, recording_id=1, kind="event", target="ev-a",
                          occurrence="", state="suggested", score=0.9, source="auto"),
            RecordingLink(user_id=user.id, connector_id=cid, recording_id=1, kind="event", target="ev-b",
                          occurrence="", state="confirmed", score=0.4, source="user"),
            RecordingLink(user_id=user.id, connector_id=cid, recording_id=2, kind="event", target="ev-c",
                          occurrence="", state="dismissed", score=0.9, source="auto"),
        ])
        await db.commit()

    items = (await client.get("/api/v1/speakr/recordings", params={"limit": 50, "annotate": "true"})).json()["items"]
    by = {x["recording_id"]: x for x in items}
    # The name and address of each participant (participants.py adds the identity; test_speakr_participants.py).
    for x in items:
        x["people"] = [{"name": q["name"], "email": q["email"]} for q in x["people"]]
    assert by[1]["event_link"]["target"] == "ev-b" and by[1]["event_link"]["state"] == "confirmed"
    assert by[2]["event_link"] is None
    assert by[1]["people"] == [{"name": "Dana Smith", "email": "dana@guest.example"},
                               {"name": "Evan", "email": "evan@guest.example"},
                               {"name": "Mara Lopez", "email": None},
                               {"name": "Rita Gomez", "email": "rita@guest.example"}]
    assert by[2]["people"] == [{"name": "Evan", "email": "evan.other@guest.example"}]
    assert by[3]["people"] == [{"name": "Evan", "email": None}]
    assert by[4]["people"] == [{"name": "Evan", "email": "evan@guest.example"}]

    # Search with annotate: the summary index, the matching line and the annotations together.
    hit = (await client.get("/api/v1/speakr/recordings", params={"q": "budget", "annotate": "true"})).json()["items"]
    assert hit and all("budget" in x["summary_match"].lower() for x in hit) and "people" in hit[0]
