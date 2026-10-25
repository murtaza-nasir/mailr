"""Switching a mail account to local-only marks what was learned from its mail (outbound data
policy, gap 7): memories that reference its messages or conversations and entities linked to its
conversations become sensitive, contact suggestions from its messages become local-only, and cached
summaries of its conversations are deleted. Memories about other mail keep their class."""

import json
from pathlib import Path

from helpers.agent import _ingest, _setup_identity

from mailr.config import get_settings
from mailr.db import sessionmaker
from mailr.models import Entity, Memory, PersonSuggestion


async def test_switch_to_local_only_marks_memories_entities_and_summaries(client, user, raw):
    conn_id, me = await _setup_identity(user)
    mid, conv = await _ingest(user.id, raw("Grade appeal", frm="Student <s1@uni.example>", to=me,
                                           body="appeal details"), connector_id=conn_id)
    other_mid, other_conv = await _ingest(user.id, raw("Lunch", frm="Pat <pat@ex.example>", to=me, body="noon"))
    async with sessionmaker()() as db:
        ent = Entity(user_id=user.id, name="Student One", emails=["s1@uni.example"], conversation_ids=[conv],
                     source="extracted")
        plain_ent = Entity(user_id=user.id, name="Pat", emails=["pat@ex.example"], conversation_ids=[other_conv],
                           source="extracted")
        db.add_all([ent, plain_ent])
        await db.flush()
        by_msg = Memory(user_id=user.id, entity_id=ent.id, text="Appeals a grade", source="extracted",
                        refs=[{"message_id": mid}])
        by_conv = Memory(user_id=user.id, text="Hearing next week", source="extracted",
                         refs=[{"conversation_id": conv}])
        plain = Memory(user_id=user.id, entity_id=plain_ent.id, text="Likes noon lunches", source="extracted",
                       refs=[{"message_id": other_mid}])
        sug = PersonSuggestion(user_id=user.id, email="s1@uni.example", field="phone", value="555 0100",
                               norm="5550100", message_id=mid, local_only=False)
        db.add_all([by_msg, by_conv, plain, sug])
        await db.commit()
        ids = (by_msg.id, by_conv.id, plain.id, ent.id, plain_ent.id, sug.id)
    d = Path(get_settings().data_dir) / "cache" / "summaries" / str(user.id)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{conv}.json").write_text(json.dumps({"key": "x", "value": {}}))
    (d / f"{other_conv}.json").write_text(json.dumps({"key": "x", "value": {}}))

    r = await client.patch(f"/api/v1/connectors/{conn_id}", json={"data_class": "local_only"})
    assert r.status_code == 200, r.text
    async with sessionmaker()() as db:
        m1, m2, m3 = [await db.get(Memory, i) for i in ids[:3]]
        e1, e2 = await db.get(Entity, ids[3]), await db.get(Entity, ids[4])
        s = await db.get(PersonSuggestion, ids[5])
        assert m1.sensitive and m2.sensitive and not m3.sensitive
        assert e1.sensitive and not e2.sensitive
        assert s.local_only
    assert not (d / f"{conv}.json").exists() and (d / f"{other_conv}.json").exists()
