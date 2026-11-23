# ACT-SURFACES: api
"""Seeing suggestions (docs/design/memory-quality.md, step 3): "Remember …?" under the chat reply a
suggestion came from, keeping the answer; the count of waiting suggestions and the weekly review
card (from Monday morning, until dismissed that week); merging a suggestion into another note,
which then lists it as "Merged from" and is the only one used. Covers memory.chat_ask,
memory.review_count, memory.dismiss_review_card and memory.merge.

No real model is called: completions come from scripted fakes."""

import json
from datetime import datetime

from helpers.agent import _drop_endpoint, _endpoint
from helpers.assistant_knowledge import fake  # noqa: F401  (fixture)
from sqlalchemy import select

from mailr.db import sessionmaker
from mailr.models import ChatMessage, ChatSession, Memory


def _row(user_id, text, *, status="suggested", **kw):
    return Memory(user_id=user_id, kind="fact", text=text, source="extracted", status=status,
                  refs=kw.pop("refs", []), sensitive=False, pinned=False, use_count=0, **kw)


async def _chat(user_id, sid, text):
    async with sessionmaker()() as db:
        db.add(ChatSession(id=sid, user_id=user_id, title="", context={}, sensitive=False))
        await db.flush()
        row = ChatMessage(session_id=sid, role="user", content=text, status="done")
        db.add(row)
        await db.flush()
        db.add(ChatMessage(session_id=sid, role="assistant", content="Noted."))
        await db.commit()
        return row.id


def _ask(text):
    return json.dumps({"memories": [{"text": text, "kind": "preference", "about_name": "", "about_email": "",
                                     "about_kind": "self", "message_id": 0, "confidence": 0.9, "due": "",
                                     "action": "new", "update_id": 0}]})


def _remember(session):
    (turn,) = [t for t in session["turns"] if t["role"] == "assistant"]
    return turn["remember"]


async def test_a_chat_suggestion_is_asked_under_the_reply_and_keeps_the_answer(client, user, fake):
    from mailr.knowledge import tasks

    ep = await _endpoint(user.id, "local")
    try:
        rid = await _chat(user.id, "s-ask-1", "I'm vegetarian.")
        fake.replies = [_ask("You are vegetarian")]
        assert (await tasks.suggest_from_chat(user.id, "s-ask-1", rid))["suggested"] == 1
        s = (await client.get("/api/v1/agent/sessions/s-ask-1")).json()
        (item,) = _remember(s)
        assert item["text"] == "You are vegetarian" and item["state"] == "waiting"
        r = await client.post(f"/api/v1/assistant/memories/{item['id']}/accept")
        assert r.status_code == 200
        assert _remember((await client.get("/api/v1/agent/sessions/s-ask-1")).json())[0]["state"] == "kept"

        rid = await _chat(user.id, "s-ask-2", "I love early meetings.")
        fake.replies = [_ask("You like early meetings")]
        await tasks.suggest_from_chat(user.id, "s-ask-2", rid)
        (item,) = _remember((await client.get("/api/v1/agent/sessions/s-ask-2")).json())
        assert (await client.post(f"/api/v1/assistant/memories/{item['id']}/reject")).status_code == 200
        # Dismissed: the line says so, though the suggestion is gone.
        (item,) = _remember((await client.get("/api/v1/agent/sessions/s-ask-2")).json())
        assert item["state"] == "dropped" and item["text"] == "You like early meetings"
    finally:
        await _drop_endpoint(ep)


async def test_an_expired_chat_suggestion_leaves_the_line(client, user, fake):
    from mailr.knowledge import tasks

    ep = await _endpoint(user.id, "local")
    try:
        rid = await _chat(user.id, "s-ask-3", "I'm moving to Denver.")
        fake.replies = [_ask("You are moving to Denver")]
        await tasks.suggest_from_chat(user.id, "s-ask-3", rid)
        async with sessionmaker()() as db:
            for m in (await db.execute(select(Memory).where(Memory.user_id == user.id))).scalars():
                await db.delete(m)  # what drop_stale_suggestions does
            await db.commit()
        assert _remember((await client.get("/api/v1/agent/sessions/s-ask-3")).json()) == []
    finally:
        await _drop_endpoint(ep)


async def test_the_count_and_the_weekly_card(client, user, monkeypatch):
    from mailr.knowledge import tasks

    r = await client.get("/api/v1/assistant/memories/counts")
    assert r.json()["suggested"] == 0 and r.json()["review_card"] is False
    async with sessionmaker()() as db:
        db.add_all([_row(user.id, "You review for a conference"), _row(user.id, "Sam is your accountant")])
        await db.commit()
    r = (await client.get("/api/v1/assistant/memories/counts")).json()
    assert r["suggested"] == 2 and r["review_card"] is True
    assert (await client.post("/api/v1/assistant/memories/review-card/dismiss")).json()["week"] == r["week"]
    assert (await client.get("/api/v1/assistant/memories/counts")).json()["review_card"] is False
    # The week turns on Monday morning: Sunday night is still the old week, Monday 06:00 the new one.
    assert tasks.review_week(datetime(2026, 11, 23, 5, 59)) == "2026-W47"
    assert tasks.review_week(datetime(2026, 11, 23, 6, 0)) == "2026-W48"
    assert tasks.review_card_due({"review_dismissed": "2026-W47"}, datetime(2026, 11, 23, 7, 0), 2) is True
    assert tasks.review_card_due({"review_dismissed": "2026-W48"}, datetime(2026, 11, 29, 22, 0), 2) is False


async def test_a_merge_leaves_one_note_with_its_history(client, user):
    async with sessionmaker()() as db:
        saved = _row(user.id, "You review for the Plains Conference", status="active",
                     refs=[{"message_id": 1, "conversation_id": 1}])
        sugg = _row(user.id, "You agreed to review a paper for PDC 2027", refs=[{"message_id": 2, "conversation_id": 2}])
        other = _row(user.id, "You teach on Tuesdays", status="active")
        db.add_all([saved, sugg, other])
        await db.commit()
        ids = saved.id, sugg.id, other.id
    r = await client.post(f"/api/v1/assistant/memories/{ids[1]}/merge", json={"into": ids[0]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["id"] == ids[0] and out["text"] == "You review for the Plains Conference"
    assert [h["text"] for h in out["merged_from"]] == ["You agreed to review a paper for PDC 2027"]
    assert {"message_id": 2, "conversation_id": 2} in out["refs"]
    # The merged note is not listed on its own, not suggested, not counted, and not used.
    listed = (await client.get("/api/v1/assistant/memories", params={"status": "all"})).json()
    assert ids[1] not in [m["id"] for m in listed["items"]]
    assert (await client.get("/api/v1/assistant/memories/counts")).json()["suggested"] == 0
    async with sessionmaker()() as db:
        assert (await db.get(Memory, ids[1])).status == "merged"
    # A saved note goes only into another saved note; never into itself.
    async with sessionmaker()() as db:
        s2 = _row(user.id, "You might like jazz")
        db.add(s2)
        await db.commit()
        s2_id = s2.id
    r = await client.post(f"/api/v1/assistant/memories/{ids[2]}/merge", json={"into": s2_id})
    assert r.status_code == 400
    assert (await client.post(f"/api/v1/assistant/memories/{ids[2]}/merge", json={"into": ids[2]})).status_code == 400
    assert (await client.post(f"/api/v1/assistant/memories/{ids[1]}/merge", json={"into": ids[2]})).status_code == 404


def test_topics_group_other_wordings_of_a_topic_and_keep_people_apart():
    """The review card and To review group by topic: one conference under three wordings is one
    group, named by its shortest; people match only by exact name."""
    from mailr.knowledge.memory import topics
    from mailr.models import Entity, Memory

    def sug(i, name=None, kind="topic", self_=False):
        about = {"self": True} if self_ else {"name": name, "kind": kind} if name else None
        return Memory(id=i, text=f"n{i}", refs=[{"about": about}] if about else [])

    rows = [sug(1, "2027 PDC Conference"), sug(2, "PDC 2027 AI Applications track"),
            sug(3, "PDC 2027"), sug(4, "John Perry", "person"), sug(5, "John", "person"), sug(6, self_=True),
            sug(7), Memory(id=8, text="n8", entity_id=50, refs=[])]
    ents = {50: Entity(id=50, name="PDC", kind="project", is_self=False)}
    t = topics(rows, ents)
    assert {t[i]["key"] for i in (1, 2, 3, 8)} == {"topic:pdc"}
    assert t[1]["name"] == "PDC"  # the shortest wording names the group
    assert t[4]["key"] != t[5]["key"]
    assert t[6] == {"key": "self", "name": "", "self": True}
    assert t[7]["key"] == "other"
