"""Speakers ↔ people without a calendar: name matching (first names, nicknames, close spellings),
the ranking of candidates (the linked meeting, the mail around it, groups, strength), one candidate
for duplicate People rows of one person and two for namesakes at different organizations, and the
migration of the earlier keys to connector-wide name keys (speaker.map)."""

import importlib.util
import pathlib
from types import SimpleNamespace

from sqlalchemy import select, text

from mailr.db import engine, sessionmaker
from mailr.integrations.speakr.people import (
    Cand,
    name_match,
    rank_candidates,
    speaker_key,
    suggest,
    title_words,
)
from mailr.models import Connector, SpeakrSpeakerPerson

_MIG = pathlib.Path(__file__).parent.parent / "migrations" / "versions" / "d4b7f2a9c1e6_speakr_speaker_global.py"


def _migration():
    spec = importlib.util.spec_from_file_location("mig_speaker_global", _MIG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_name_match_kinds():
    assert name_match("Mateo", "Mateo Yilmaz") == ("first", 60)
    assert name_match("Mike", "Michael Brown")[0] == "nickname"
    assert name_match("Aiden", "Mohammad Aiden Khan")[0] == "word"
    assert name_match("Mateo", "Yilmaz, Mateo") == ("first", 60)  # "Last, First"
    assert name_match("Aiden", "Aidan Rahimov")[0] == "close"
    assert name_match("Mi", "Mia Wong") == ("", 0)  # short names need the exact word
    assert name_match("Mary Smith", "Mary Ann Smith")[0] == "full"
    assert name_match("Ross", "", "ross.geller@x.edu")[0] == "first"  # no name: the address
    assert speaker_key({"name": "Mi", "speaker_id": 7, "recording_id": 3}) == "name:mi"
    assert speaker_key({"label": "SPEAKER_02", "name": "", "recording_id": 3}) == "label:3:speaker_02"
    assert title_words("Restructuring the Information Systems and AI Curriculum") == [
        "curriculum", "information", "restructuring", "systems"]


def test_ranking_meeting_mail_groups_and_strength():
    pool = [
        Cand("mateo.a@big.com", "Mateo Arslan", rank=900.0),          # strongest, nothing else
        Cand("mateo.b@lakeside.example", "Mateo Boz", rank=5.0, event=True),   # in the linked meeting
        Cand("mateo.c@lakeside.example", "Mateo Can", rank=1.0, thread="from"),  # emailed about it
        Cand("mateo.d@x.org", "Mateo Demir", rank=50.0, groups=["Committee"]),
        Cand("other@x.org", "Ross Geller", rank=1000.0, event=True),     # no name match
    ]
    out = rank_candidates("Mateo", pool)
    assert [c["email"] for c in out] == ["mateo.b@lakeside.example", "mateo.c@lakeside.example", "mateo.d@x.org", "mateo.a@big.com"]
    assert out[0]["hints"] == ["in the linked meeting"] and out[0]["organization"] == "lakeside"
    assert out[1]["hints"] == ["emailed you about this"] and out[2]["hints"] == ["in your group Committee"]
    # With nothing but the mail, strength orders people with the same first name.
    weak = [Cand("a@x.org", "Ross Alpha", rank=2.0), Cand("b@y.org", "Ross Beta", rank=400.0),
            Cand("c@z.org", "Rossana Gamma", rank=900.0)]
    assert [c["email"] for c in rank_candidates("Ross", weak)] == ["b@y.org", "a@x.org"]
    # At equal strength, a nickname counts below the first name.
    nick = [Cand("mike@x.org", "Michael Brown", rank=5.0), Cand("mike2@x.org", "Mike Green", rank=5.0)]
    assert [c["email"] for c in rank_candidates("Mike", nick)] == ["mike2@x.org", "mike@x.org"]
    # Dismissed or taken addresses are left out.
    assert [c["email"] for c in rank_candidates("Mateo", pool, exclude={"mateo.b@lakeside.example"})][0] == "mateo.c@lakeside.example"


def test_duplicates_merge_and_namesakes_stay_apart():
    pool = [Cand("dana@lakeside.example", "Dana Smith", "Lakeside", rank=10.0), Cand("dana.smith@gmail.com", "Dana Smith", rank=3.0),
            Cand("dana@acme.example", "Dana Smith", "Acme", rank=1.0)]
    out = rank_candidates("Dana", pool)
    assert len(out) == 2
    assert out[0]["email"] == "dana@lakeside.example" and out[0]["emails"] == ["dana@lakeside.example", "dana.smith@gmail.com"]
    assert out[1]["email"] == "dana@acme.example" and out[1]["organization"] == "Acme"
    # The pure form for a guest list and correspondents.
    guests = [{"email": "evan@x.org", "name": "Evan Lee"}, {"email": "eve@x.org", "name": "Eve Lee"}]
    assert [s["email"] for s in suggest("evan", guests, [])] == ["evan@x.org"]
    assert suggest("SPEAKER_01", guests, []) == [] and suggest("", guests, []) == []
    # An unnamed label is offered the meeting's guests.
    assert [c["email"] for c in rank_candidates("SPEAKER_02", [Cand("g@x.org", "G", event=True), Cand("h@x.org", "H")],
                                                unnamed=True)] == ["g@x.org"]


def test_migration_plan():
    mod = _migration()
    rows = [  # newest first
        {"id": 1, "connector_id": 9, "speaker_key": "label:5:mi", "speaker_name": "Mi", "email": "mi@x.org",
         "state": "confirmed"},
        {"id": 2, "connector_id": 9, "speaker_key": "label:4:mi", "speaker_name": "Mi", "email": "other@x.org",
         "state": "confirmed"},
        {"id": 3, "connector_id": 9, "speaker_key": "label:3:mi", "speaker_name": "Mi", "email": "mi@x.org",
         "state": "confirmed"},
        {"id": 4, "connector_id": 9, "speaker_key": "id:11", "speaker_name": "Dana Smith", "email": "dana@x.org",
         "state": "confirmed"},
        {"id": 5, "connector_id": 9, "speaker_key": "label:5:speaker_02", "speaker_name": "SPEAKER_02",
         "email": "s@x.org", "state": "confirmed"},
        {"id": 6, "connector_id": 9, "speaker_key": "name:ross", "speaker_name": "Ross", "email": "ross@x.org",
         "state": "confirmed"},
    ]
    updates, deletes = mod.plan(rows)
    assert (1, "name:mi", "Mi") in updates
    assert (2, "rec:4:name:mi", "") in updates  # another person in an older recording: its override
    assert 3 in deletes  # the same mapping again
    assert (4, "name:dana smith", "Dana Smith") in updates
    assert not any(u[0] in (5, 6) for u in updates) and 5 not in deletes


async def test_migration_runs_on_the_table(user):
    async with sessionmaker()() as db:
        c = Connector(user_id=user.id, kind="speakr", name="S", config={}, data_class="local_only")
        db.add(c)
        await db.flush()
        cid = c.id
        for key, name, email in [("label:7:aiden", "Aiden", "aiden@x.org"), ("id:3", "Ross", "ross@x.org"),
                                 ("label:7:speaker_01", "SPEAKER_01", "s@x.org")]:
            db.add(SpeakrSpeakerPerson(user_id=user.id, connector_id=cid, speaker_key=key, speaker_name=name,
                                       email=email))
        await db.commit()
    mod = _migration()

    def run(sync_conn):
        mod.op = SimpleNamespace(get_bind=lambda: sync_conn)
        sync_conn.execute(text("SELECT 1"))
        mod.upgrade()

    async with engine().begin() as conn:
        await conn.run_sync(run)
    async with sessionmaker()() as db:
        keys = set((await db.execute(select(SpeakrSpeakerPerson.speaker_key).where(
            SpeakrSpeakerPerson.connector_id == cid))).scalars())
    assert keys == {"name:aiden", "name:ross", "label:7:speaker_01"}
