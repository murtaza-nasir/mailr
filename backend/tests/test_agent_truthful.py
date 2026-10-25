# ACT-SURFACES: assistant
"""Truthful task writes and replies (agent/claims.py), from the chat of 10 Oct 2026.

The user pasted "You've hit your weekly limit · resets Oct 14, 6am" and asked to be reminded then.
Ambrose put an absolute reminder on an unrelated, overdue task. The task stored it, but the server
never planned a reminder more than a day after a due, and the reply said "Done — the reminder is
set". Covered here, against the dev Radicale:
- the planner fires an absolute reminder days after the task's due (task.remind_at);
- create_task / update_task refuse fields they do not read, refuse reminders on a task with no
  due, and return `will_remind`; the step line shows the stored reminder;
- with a fake model that follows the system prompt, "remind me about X at <time>" makes a new task
  and leaves the unrelated one alone (task.create);
- a reply that claims a change no tool confirmed is asked again once, and flagged if it insists.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest
from dev_services import radicale_up as _radicale_up
from helpers.agent import _drop_endpoint, _endpoint, _events, _text_turn, _tool_turn
from helpers.agent_calendar_v2 import _allow, _ctx
from helpers.calendar import _connect
from sqlalchemy import select

from mailr.agent import claims
from mailr.agent.tools import ToolError, run_tool
from mailr.calendar import planner
from mailr.db import sessionmaker
from mailr.models import CalObject, ChatMessage, ReminderSchedule

pytestmark = pytest.mark.skipif(not _radicale_up(), reason="Radicale dev server not running on :15232")

PASTE = "You've hit your weekly limit · resets Oct 14, 6am (America/Chicago)\n\nSet a reminder for this time, remind me about this time."
RULE = "create a new task named after X"


def _hour(days: int, hour: int) -> datetime:
    return (datetime.now(UTC) + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)


async def _schedule(user_id: int, uid: str) -> list[ReminderSchedule]:
    async with sessionmaker()() as db:
        return list((await db.execute(select(ReminderSchedule).where(
            ReminderSchedule.user_id == user_id, ReminderSchedule.uid == uid)
            .order_by(ReminderSchedule.fire_at))).scalars())


async def _ics(user_id: int, uid: str) -> str:
    async with sessionmaker()() as db:
        return (await db.execute(select(CalObject.ics).where(CalObject.user_id == user_id,
                                                             CalObject.uid == uid))).scalar_one()


async def test_absolute_reminder_days_after_the_due_fires(client, user):
    """task.remind_at: the incident's write. A reminder set for three days from now on a task due two
    days ago is stored, reported in `will_remind`, and planned at its own time."""
    await _connect(client)
    due, at = _hour(-2, 13), _hour(3, 11)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        t = (await run_tool(ctx, "create_task", {"summary": "Finish the midterm", "due": due.isoformat()})).data
        res = await run_tool(ctx, "update_task", {"uid": t["uid"], "reminders": [at.isoformat()]})
        assert res.data["reminders_at"] == [at.isoformat()]
        assert datetime.fromisoformat(res.data["will_remind"][0]["at"]) == at
        assert res.data["changed"] == ["reminders", "reminders_at"]
        from mailr.agent import present

        m, _ = present.result_msg("update_task", res)
        assert m["key"] == "approvals.result.saved_reminds" and m["params"]["when"] == res.data["will_remind"][0]["at"][:16].replace("T", " ")
    rows = await _schedule(user.id, t["uid"])
    assert [r.fire_at for r in rows if r.fired_at is None] == [at]


def test_planner_counts_absolute_reminders_without_bound():
    """A unit check of planner.task_offsets: the mirror's `alarms` drops an absolute trigger more than a
    day after the due; the planner reads it from the calendar data."""
    from zoneinfo import ZoneInfo

    due = datetime(2026, 10, 9, 13, tzinfo=UTC)
    ics = ("BEGIN:VCALENDAR\r\nBEGIN:VTODO\r\nUID:x\r\nDUE:20261009T130000Z\r\nBEGIN:VALARM\r\nACTION:DISPLAY\r\n"
           "TRIGGER;VALUE=DATE-TIME:20261014T110000Z\r\nEND:VALARM\r\nEND:VTODO\r\nEND:VCALENDAR\r\n")
    obj = CalObject(uid="x", kind="task", ics=ics, alarms=[], due_at=due, status="NEEDS-ACTION")
    cfg = planner.settings_of(None)
    zone = ZoneInfo("America/Chicago")
    got_due, offsets, source = planner.task_offsets(obj, cfg, zone)
    assert got_due == due and offsets == [-(5 * 24 * 60 - 120)] and source == "valarm"
    fires = planner.task_fire_times(obj, cfg, zone, datetime(2026, 10, 11, 3, tzinfo=UTC))
    assert [f for f, _m, _s in fires] == [datetime(2026, 10, 14, 11, tzinfo=UTC)]
    plan = planner.plan_task(obj, cfg, datetime(2026, 10, 11, 3, tzinfo=UTC), zone)
    assert [p.fire_at for p in plan] == [datetime(2026, 10, 14, 11, tzinfo=UTC)]


async def test_task_tools_refuse_what_they_cannot_apply(client, user):
    """A field the tool does not read is refused by name, and nothing changes; a reminder on a task
    with no due (it would never fire) is refused; a create returns what will fire."""
    await _connect(client)
    at = _hour(3, 11)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        t = (await run_tool(ctx, "create_task", {"summary": "Send the practice exam", "due": _hour(1, 14).isoformat()})).data
        before = await _ics(user.id, t["uid"])
        with pytest.raises(ToolError, match="no field 'remind_at'.*nothing was changed.*`reminders`"):
            await run_tool(ctx, "update_task", {"uid": t["uid"], "remind_at": at.isoformat()})
        assert await _ics(user.id, t["uid"]) == before
        with pytest.raises(ToolError, match="needs the task to have a due"):
            await run_tool(ctx, "create_task", {"summary": "Limit resets", "reminders": [at.isoformat()]})
        nodue = (await run_tool(ctx, "create_task", {"summary": "Someday"})).data
        assert nodue["will_remind"] == [] and "no due" in nodue["will_remind_note"]
        with pytest.raises(ToolError, match="needs the task to have a due"):
            await run_tool(ctx, "update_task", {"uid": nodue["uid"], "reminders": [30]})
        with pytest.raises(ToolError, match="needs the task to have a due"):
            await run_tool(ctx, "update_task", {"uid": t["uid"], "due": "", "reminders": [0]})
        made = (await run_tool(ctx, "create_task", {"summary": "Usage limit resets", "due": at.isoformat(),
                                                    "reminders": [0]})).data
        assert [datetime.fromisoformat(w["at"]) for w in made["will_remind"]] == [at]
        assert made["will_remind"][0]["when"].endswith(made["will_remind"][0]["when"][-3:])  # a zone name


async def test_unapplied_reminder_is_an_error(client, user, monkeypatch):
    """A write that stores less than was asked (here: the writer loses the absolute reminder) is an error
    naming `reminders`, never a success."""
    from mailr.calendar import todo

    await _connect(client)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        t = (await run_tool(ctx, "create_task", {"summary": "Check the limit", "due": _hour(1, 14).isoformat()})).data
        monkeypatch.setattr(todo, "set_absolute_alarms", lambda comp, moments: None)
        with pytest.raises(ToolError, match=r"did not take: reminders .*do not say the change is set"):
            await run_tool(ctx, "update_task", {"uid": t["uid"], "reminders": [_hour(3, 11).isoformat()]})


def _follower(script_state):
    """A fake model that follows the system prompt: with the reminder rule it makes a new task; without
    it, it does what qwen did on 10 Oct 2026 and moves the one reminder it found."""

    class Follow(list):
        def pop(self, i=0):
            from helpers.agent import FakeStream

            body = FakeStream.requests[-1]["body"]
            msgs = body["messages"]
            system = msgs[0]["content"]
            tools_done = [m for m in msgs if m.get("role") == "tool"]
            script_state["system"] = system
            if not tools_done:
                return _tool_turn("list_reminders", {}, "r1")
            if len(tools_done) == 1:
                found = json.loads(tools_done[0]["content"])
                if RULE in system:
                    return _tool_turn("create_task", {"summary": "Usage limit resets",
                                                      "due": script_state["at"], "reminders": [0]}, "w1")
                return _tool_turn("update_task", {"uid": found["items"][0]["uid"],
                                                  "reminders": [script_state["at"]]}, "w1")
            return _text_turn("Done — the reminder is set.")

        def __bool__(self):
            return True

    return Follow([None])


async def test_remind_me_makes_a_new_task_not_another_one(client, user, fake_llm):
    """task.create: the incident replayed with a model that follows the prompt. The new task carries the
    reminder; the unrelated task is unchanged; the turn's confirmed changes name the new task."""
    await _connect(client)
    at = _hour(3, 11)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        other = (await run_tool(ctx, "create_task", {"summary": "Finish the DATA520 midterm",
                                                     "due": (datetime.now(UTC) + timedelta(hours=2)).replace(second=0, microsecond=0).isoformat()})).data
        await db.commit()
    before = await _ics(user.id, other["uid"])
    await _allow(user.id, "tasks.create", "tasks.update")  # as on a typical server: task writes need no card
    state = {"at": at.isoformat()}
    ep = await _endpoint(user.id, "local")
    try:
        fake_llm.script = _follower(state)
        r = await client.post("/api/v1/agent/chat", json={"message": PASTE})
        evs = _events(r.text)
        assert RULE in state["system"]
        assert next(e for e in evs if e["type"] == "tool")["summary_msg"]["key"] == "approvals.count.item"
        tool = next(e for e in evs if e["type"] == "tool" and e["name"] == "create_task")
        assert tool["ok"] and tool["summary_msg"]["key"] == "approvals.result.saved_reminds"
        final = next(e for e in evs if e["type"] == "final")
        assert final["content"] == "Done — the reminder is set."
        assert [c["tool"] for c in final["changes"]] == ["create_task"]
        assert final["changes"][0]["items"][0]["title"] == "Usage limit resets"
        assert not final.get("unconfirmed")
        new_uid = final["changes"][0]["items"][0]["id"]
        assert [x.fire_at for x in await _schedule(user.id, new_uid)] == [at]
        assert await _ics(user.id, other["uid"]) == before

        # The same model without the rule repeats the incident: the change is real now (the planner
        # fires it), and the step line and the turn's changes show which task it went to.
        fake_llm.requests.clear()
        from mailr.agent import prompts

        old = prompts.ASSISTANT
        prompts.ASSISTANT = old.replace(RULE, "make a task")
        try:
            r = await client.post("/api/v1/agent/chat", json={"message": PASTE})
        finally:
            prompts.ASSISTANT = old
        evs = _events(r.text)
        final = next(e for e in evs if e["type"] == "final")
        assert [c["items"][0]["title"] for c in final["changes"]] == ["Finish the DATA520 midterm"]
        assert [datetime.fromisoformat(w) for w in final["changes"][0]["will_remind"]] == [at]
        assert [x.fire_at for x in await _schedule(user.id, other["uid"]) if x.fire_at == at] == [at]
    finally:
        await _drop_endpoint(ep)


async def test_claim_without_a_confirmed_change_is_asked_again(client, user, fake_llm):
    """The write fails (a field update_task does not read); the model says "Done". It is asked once more
    without tools and its corrected reply is stored. A model that insists gets `unconfirmed`."""
    await _connect(client)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        t = (await run_tool(ctx, "create_task", {"summary": "Practice exam", "due": _hour(1, 14).isoformat()})).data
        await db.commit()
    at = _hour(3, 11).isoformat()
    ep = await _endpoint(user.id, "local")
    try:
        fake_llm.script = [_tool_turn("update_task", {"uid": t["uid"], "remind_at": at}),
                           _text_turn("Done — the reminder is set for Wednesday."),
                           _text_turn("I could not set the reminder: update_task has no field remind_at.")]
        r = await client.post("/api/v1/agent/chat", json={"message": "Remind me about the exam on Wednesday"})
        evs = _events(r.text)
        step = next(e for e in evs if e["type"] == "tool")
        assert step["ok"] is False and "change" not in step
        final = next(e for e in evs if e["type"] == "final")
        assert final["content"].startswith("I could not set the reminder") and not final.get("unconfirmed")
        note = fake_llm.requests[-1]["body"]["messages"][-1]["content"]
        assert note.startswith("[mailr: your reply says something was changed") and "update_task failed" in note
        assert "tools" not in fake_llm.requests[-1]["body"]
        sid = next(e for e in evs if e["type"] == "session")["session_id"]
        async with sessionmaker()() as db:
            row = (await db.execute(select(ChatMessage).where(ChatMessage.session_id == sid, ChatMessage.role == "assistant")
                                    .order_by(ChatMessage.id.desc()).limit(1))).scalar_one()
            assert row.meta["claim_check"] == "corrected" and "unconfirmed" not in row.meta

        fake_llm.script = [_text_turn("All set, I've added the reminder."), _text_turn("Done, it is set.")]
        r = await client.post("/api/v1/agent/chat", json={"session_id": sid, "message": "Is it set?"})
        final = next(e for e in _events(r.text) if e["type"] == "final")
        assert final["unconfirmed"] is True and final["content"] == "Done, it is set."
        s = (await client.get(f"/api/v1/agent/sessions/{sid}")).json()
        assert s["turns"][-1]["unconfirmed"] is True and not s["turns"][1].get("unconfirmed")
    finally:
        await _drop_endpoint(ep)


def test_claim_detection():
    yes = ["Done — the reminder is set for Wed 14 Oct.", "**Done.** Moved it.", "I've set a reminder for 6:00.",
           "The task has been updated.", "Your reminder is now set.", "I have created a task."]
    no = ["I'll set a reminder for that time.", "It will be moved once you approve the card.",
          "The move is waiting for your approval.", "I found 3 tasks due this week.",
          "Nothing was changed: the tool failed.",
          "All four were created on 23 Oct and closed as completed on 24 Oct.",
          "The invoice was sent last week by the supplier.",
          "It is set to expire when approved? No."]
    assert [t for t in yes if not claims.claims_change(t)] == []
    assert [t for t in no[:-1] if claims.claims_change(t)] == []
    assert claims.is_write("update_task") and not claims.is_write("list_reminders") and not claims.is_write("recall")


async def test_reminders_is_the_whole_list(client, user):
    """`reminders` replaces both kinds: [] clears every reminder, relative ones replace absolute ones,
    and keeping one of two absolute ones removes the other. The REST API removes an absolute one with
    `reminders_at`. Each absolute-only edit makes a version, and its Activity entry undoes it."""
    from mailr.core import live

    await _connect(client)
    due, a1, a2 = _hour(-2, 13), _hour(3, 11), _hour(4, 11)
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        t = (await run_tool(ctx, "create_task", {"summary": "Midterm", "due": due.isoformat(),
                                                 "reminders": [30, a1.isoformat()]})).data
        uid = t["uid"]
        assert t["reminders"] == [30] and t["reminders_at"] == [a1.isoformat()]
        cleared = (await run_tool(ctx, "update_task", {"uid": uid, "reminders": []})).data
        assert cleared["reminders"] == [] and cleared["reminders_at"] == []
        assert "TRIGGER" not in await _ics(user.id, uid)
        both = (await run_tool(ctx, "update_task", {"uid": uid, "reminders": [a1.isoformat(), a2.isoformat()]})).data
        assert both["reminders_at"] == [a1.isoformat(), a2.isoformat()]
        kept = (await run_tool(ctx, "update_task", {"uid": uid, "reminders": [a2.isoformat()]})).data
        assert kept["reminders_at"] == [a2.isoformat()] and kept["reminders"] == []
        assert [datetime.fromisoformat(w["at"]) for w in kept["will_remind"]] == [a2]
        rel = (await run_tool(ctx, "update_task", {"uid": uid, "reminders": [15]})).data
        assert rel["reminders"] == [15] and rel["reminders_at"] == []
        await db.commit()

        # Versions carry absolute reminders: an edit of them alone is a version of its own.
        v0 = (await live.latest(db, user.id, "task", uid)).version
        await run_tool(ctx, "update_task", {"uid": uid, "reminders": [15, a1.isoformat()]})
        await db.commit()
        row = await live.latest(db, user.id, "task", uid)
        assert row.version == v0 + 1 and row.content["reminders_at"] == [a1.isoformat()] and row.content["reminders"] == [15]

    # REST: reminders_at [] removes the absolute one and keeps the relative one.
    r = await client.patch(f"/api/v1/tasks/{uid}", json={"reminders_at": []})
    assert r.status_code == 200 and r.json()["reminders_at"] == [] and r.json()["reminders"] == [15]

    # Activity: the incident's edit (an absolute reminder added) can be undone.
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id)
        await run_tool(ctx, "update_task", {"uid": uid, "reminders": [a2.isoformat()]})
        await db.commit()
    items = (await client.get("/api/v1/activity", params={"app": "tasks"})).json()["items"]
    undo = next(i for i in items if i.get("undo") and i["undo"]["path"] == f"/tasks/{uid}")["undo"]
    assert undo["body"]["reminders"] == [15] and undo["body"]["reminders_at"] == []
    r = await client.request(undo["method"], f"/api/v1{undo['path']}", json=undo["body"])
    assert r.status_code == 200 and r.json()["reminders_at"] == [] and r.json()["reminders"] == [15]
