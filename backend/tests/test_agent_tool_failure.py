"""A tool that fails unexpectedly (a calendar server's error, a timeout, a bug) does not end the
assistant's turn: the model gets the failure as the tool's error and goes on (agent/loop.py), up to
MAX_TOOL_FAILURES per turn; the failed call's database writes are undone alone. A result over the size
cap keeps its head and its tail."""

from __future__ import annotations

from helpers.agent import (
    _drop_endpoint,
    _endpoint,
    _events,
    _seed_thread,
    _setup_identity,
    _text_turn,
    _tool_turn,
)

from mailr.agent import tools


async def test_an_unexpected_tool_error_goes_back_to_the_model(client, user, raw, fake_llm, monkeypatch):
    _, me = await _setup_identity(user)
    _m1, conv = await _seed_thread(user, raw, me)
    t = tools.TOOLS["search_conversations"]

    async def broken(ctx, args):
        raise TimeoutError("the calendar server did not answer")

    monkeypatch.setattr(t, "fn", broken)
    ep = await _endpoint(user.id, "local")
    try:
        fake_llm.script = [
            _tool_turn("search_conversations", {"q": "budget"}),
            _text_turn(f"The search failed, but here is the thread [c:{conv}]."),
        ]
        r = await client.post("/api/v1/agent/chat", json={"messages": [{"role": "user", "content": "Find the budget mail"}]})
        assert r.status_code == 200
        evs = _events(r.text)
        kinds = [e["type"] for e in evs]
        assert kinds[-1] == "done" and "error" not in kinds, kinds
        step = next(e for e in evs if e["type"] == "tool")
        assert step["ok"] is False and "did not answer" in step["error"]
        last = fake_llm.requests[-1]["body"]["messages"]
        assert any(m["role"] == "tool" and "failed unexpectedly" in m["content"] for m in last)
    finally:
        await _drop_endpoint(ep)


def test_a_long_tool_result_keeps_its_head_and_its_tail():
    """A thread's messages come oldest first: a result over the cap keeps the newest in view."""
    from mailr.agent.loop import _tool_message

    msgs = [{"id": i, "text": f"message {i} " + "x" * 400} for i in range(100)]
    content = _tool_message("c1", {"messages": msgs}, cap=6000)["content"]
    assert "message 0 " in content and "message 99 " in content and "characters left out" in content
    assert len(content) < 6200
