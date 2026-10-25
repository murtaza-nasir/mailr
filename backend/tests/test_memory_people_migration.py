"""Migration 7c2e9b4d1a60 on data shaped like the first install (7 Oct 2026): eight notes learned on
29 Sep, most hanging off a person entity that is the user, written "The user is …" and typed
relationship; two other people (one with a contact, one without); a cursor moved past mail that
produced nothing. Run twice: the second run changes nothing, and no note is ever lost."""

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from conftest import mail_date
from helpers.agent import _ingest, _setup_identity
from sqlalchemy import delete, select, update

from mailr.db import engine, sessionmaker
from mailr.models import AssistantProfile, Entity, Job, Memory, Person


def _migration():
    path = next((Path(__file__).resolve().parents[1] / "migrations" / "versions").glob("7c2e9b4d1a60_*.py"))
    spec = importlib.util.spec_from_file_location("memory_people_migration", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _reshape():
    mod = _migration()
    async with engine().begin() as conn:
        return await conn.run_sync(lambda c: mod.reshape(c))


async def _old_shape(user, raw):
    """Rows as the old code left them (no is_self, no person_id, old kinds, third person)."""
    _, me = await _setup_identity(user)
    first = await _ingest(user.id, raw("HOA", frm=f"Me <{me}>", to="Dan <dan@hoa.example>", body="About the pool",
                                       date=mail_date(1)), source="send", roles=["sent"])
    later = await _ingest(user.id, raw("Licence", frm=f"Me <{me}>", to="Joao <joao@lic.example>", body="Quote",
                                       date=mail_date(2)), source="send", roles=["sent"])
    async with sessionmaker()() as db:
        # The send made contacts for its recipients; João's is removed (a person whose contact is gone).
        await db.execute(delete(Person).where(Person.user_id == user.id, Person.email == "joao@lic.example"))
        await db.execute(update(Person).where(Person.user_id == user.id, Person.email == "dan@hoa.example")
                         .values(received_count=4, sent_count=3))
        db.add(Person(user_id=user.id, email="dan.old@hoa.example", name="Dan", received_count=1, sent_count=0))

        def ent(kind, name, emails=()):
            e = Entity(user_id=user.id, kind=kind, name=name, aliases=[], emails=list(emails), domains=[],
                       conversation_ids=[], summary="", sensitive=False, source="extracted")
            db.add(e)
            return e

        me_ent = ent("person", "Dr. Test User", [user.email.lower(), me])
        dan = ent("person", "Dan Murphy", ["dan.old@hoa.example", "dan@hoa.example"])
        joao = ent("person", "João Domingos", ["joao@lic.example"])
        thread = ent("thread", "Budget thread")
        sam = ent("person", "Sam")
        dup = ent("person", "Test User", [me])
        await db.flush()

        def note(e, text, kind, mid=None, source="extracted"):
            refs = [{"message_id": mid[0], "conversation_id": mid[1]}] if mid else []
            db.add(Memory(user_id=user.id, entity_id=e.id if e else None, text=text, kind=kind, source=source,
                          status="active", refs=refs, sensitive=False, pinned=False, use_count=0, confidence=0.9))

        note(me_ent, "The user is a professor at Example State.", "relationship", first)
        note(me_ent, "The user is an advisor for a student club.", "relationship", first)
        note(me_ent, "The user is a member of the Elm Street HOA.", "relationship", first)
        note(dan, "Dan Murphy is the contact person for the user's HOA.", "relationship", first)
        note(me_ent, "The user is the author of a paper on governance.", "commitment", first)
        note(me_ent, "The user is the maintainer of an open-source tool.", "relationship", first)
        note(joao, "The user is in discussions with João about a licence.", "commitment", first)
        note(me_ent, "The user is a member of the Northfield Analytics group.", "relationship", first)
        note(dup, "The user usually teaches on Tuesdays.", "habit")
        note(thread, "Budget is pending.", "status", source="user")
        note(sam, "Sam prefers calls.", "preference", source="user")
        prof = await db.get(AssistantProfile, user.id)
        if prof is None:
            prof = AssistantProfile(user_id=user.id, data={}, options={})
            db.add(prof)
        moved_past = datetime.now(UTC) - timedelta(days=1)
        prof.state = {"extract_at": moved_past.isoformat(), "extract_since": moved_past.isoformat(),
                      "extract_cursor": 6787, "skills_seeded": ["concise"]}
        await db.commit()
        return {"me": me_ent.id, "dan": dan.id, "joao": joao.id, "thread": thread.id, "sam": sam.id, "dup": dup.id,
                "first": first, "later": later, "own": me}


async def test_the_migration_reshapes_memory_like_the_first_install(client, user, raw):
    ids = await _old_shape(user, raw)
    async with sessionmaker()() as db:
        before = {m.id for m in (await db.execute(select(Memory).where(Memory.user_id == user.id))).scalars()}
    counts = await _reshape()
    assert counts["self"] >= 1 and counts["merged"] >= 1 and counts["cursors"] >= 1, counts

    async with sessionmaker()() as db:
        mems = {m.id: m for m in (await db.execute(select(Memory).where(Memory.user_id == user.id))).scalars()}
        ents = {e.id: e for e in (await db.execute(select(Entity).where(Entity.user_id == user.id))).scalars()}
        # Never deletes a note.
        assert set(mems) == before
        # The person that is the user became "you"; the duplicate merged into it, its note moved.
        me = ents[ids["me"]]
        assert me.is_self and me.kind == "person" and me.person_id is None
        assert ids["dup"] not in ents and "Test User" in me.aliases
        on_me = [m for m in mems.values() if m.entity_id == me.id]
        assert len(on_me) == 7
        # "The user is …" → "You are …", everywhere.
        assert all("the user" not in m.text.lower() for m in mems.values())
        assert {m.text for m in on_me} >= {"You are a professor at Example State.", "You usually teach on Tuesdays."}
        assert any(m.text == "Dan Murphy is the contact person for your HOA." for m in mems.values())
        assert any(m.text == "You are in discussions with João about a licence." and m.entity_id == ids["joao"]
                   for m in mems.values())
        rewritten = [m for m in mems.values() if m.source == "extracted"]
        assert all(m.embedding is None and m.embed_model is None for m in rewritten)
        # Kinds: relationship/status → fact, habit → routine; commitments stay.
        assert {m.kind for m in mems.values()} <= {"fact", "preference", "routine", "commitment"}
        assert sorted(m.kind for m in on_me).count("commitment") == 1
        assert ents[ids["thread"]].kind == "topic"
        # People link to their contact: Dan to the most written-to address; João gets a contact.
        dan_contact = await db.get(Person, ents[ids["dan"]].person_id)
        assert dan_contact.email == "dan@hoa.example"
        joao_contact = await db.get(Person, ents[ids["joao"]].person_id)
        assert joao_contact.email == "joao@lic.example" and joao_contact.name == "João Domingos"
        # A person without an address stays a contact without mail.
        assert ents[ids["sam"]].person_id is None
        # The cursor goes back to the newest message a kept note came from, and the reset is recorded.
        prof = await db.get(AssistantProfile, user.id)
        reset = prof.state["cursor_reset"]
        assert reset["to"] == prof.state["extract_since"] and reset["from"] != reset["to"]
        assert datetime.fromisoformat(prof.state["extract_since"]) < datetime.fromisoformat(reset["from"])
        assert prof.state["extract_cursor"] == 6787 and prof.state["skills_seeded"] == ["concise"]
        job = (await db.execute(select(Job).where(Job.kind == "kb.embed", Job.user_id == user.id,
                                                  Job.status == "queued"))).scalars().first()
        assert job is not None
        snapshot = {m.id: (m.text, m.kind, m.entity_id) for m in mems.values()}
        state = json.dumps(prof.state, sort_keys=True)

    # Again: nothing changes.
    again = await _reshape()
    assert again == {k: 0 for k in again}, again
    async with sessionmaker()() as db:
        mems = {m.id: (m.text, m.kind, m.entity_id)
                for m in (await db.execute(select(Memory).where(Memory.user_id == user.id))).scalars()}
        assert mems == snapshot
        assert json.dumps((await db.get(AssistantProfile, user.id)).state, sort_keys=True) == state


async def test_a_user_whose_passes_never_kept_a_note_reads_the_latest_mail_again(client, user):
    async with sessionmaker()() as db:
        db.add(AssistantProfile(user_id=user.id, data={}, options={},
                                state={"extract_since": datetime.now(UTC).isoformat()}))
        await db.commit()
    await _reshape()
    async with sessionmaker()() as db:
        st = (await db.get(AssistantProfile, user.id)).state
        assert "extract_since" not in st and st["cursor_reset"]["to"] is None
