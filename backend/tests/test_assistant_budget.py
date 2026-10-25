"""The assistant's prompt budget (MAILR_ASSISTANT_PROMPT_TOKENS): the oldest earlier turns give
way first, this question's tool results are cut down for the answer, and a question that does
not fit, or an endpoint that refuses a prompt as too long, gets the reason instead of "The model
did not respond"."""

from helpers.agent import _drop_endpoint, _endpoint, _text_turn, _tool_turn
from helpers.assistant_knowledge import _all_events, fake  # noqa: F401

from mailr.agent import loop
from mailr.config import get_settings


def _turn(i, size=4000):
    return [{"role": "user", "content": f"question {i} " + "x" * size},
            {"role": "assistant", "content": None, "tool_calls": [{"id": f"t{i}", "type": "function",
                                                                    "function": {"name": "f", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": f"t{i}", "content": "y" * size},
            {"role": "assistant", "content": f"answer {i}"}]


def test_fit_history_drops_whole_old_turns_first():
    history = _turn(1) + _turn(2) + _turn(3)
    convo = [{"role": "system", "content": "s"}, *history, {"role": "user", "content": "now"}]
    budget = loop.prompt_tokens([convo[0], *_turn(3), convo[-1]]) + 10
    dropped = loop.fit_history(convo, len(history), budget)
    assert dropped == 8
    assert convo[1]["content"].startswith("question 3") and convo[-1]["content"] == "now"
    # Nothing to drop: the system prompt and the question stay whatever the budget.
    assert loop.fit_history(convo, 0, 10) == 0 and len(convo) == 6


def test_shrink_results_cuts_this_turns_results_or_says_it_cannot():
    convo = [{"role": "system", "content": "s"}, {"role": "user", "content": "q"},
             {"role": "tool", "tool_call_id": "a", "content": "head " + "z" * 30_000 + " newest"}]
    assert loop.shrink_results(convo, 1, 3_000)
    assert loop.prompt_tokens(convo) <= 3_000 and convo[2]["content"].endswith("newest")
    assert not loop.shrink_results([{"role": "system", "content": "s" * 40_000}], 1, 1_000)


def test_context_overflow_errors_are_recognised():
    assert loop.context_overflow(RuntimeError("400: This model's maximum context length is 32768 tokens"))
    assert loop.context_overflow(RuntimeError("prompt is too long: 210000 tokens > 200000 maximum"))
    assert not loop.context_overflow(RuntimeError("connection refused"))


async def test_a_question_over_the_budget_gets_the_reason(client, user, fake, monkeypatch):
    ep = await _endpoint(user.id, "local")
    try:
        monkeypatch.setattr(get_settings(), "assistant_prompt_tokens", 4_000)
        evs = _all_events((await client.post("/api/v1/agent/chat", json={"message": "w" * 20_000})).text)
        assert evs[-1]["type"] == "error" and evs[-1]["code"] == "context_budget"
        assert "start a new chat" in evs[-1]["message"] and fake.requests == []
    finally:
        await _drop_endpoint(ep)


async def test_an_endpoints_context_error_is_named(client, user, monkeypatch):
    from mailr.intel import llm

    ep = await _endpoint(user.id, "local")

    async def too_long(*a, **kw):
        raise RuntimeError("400 Bad Request: maximum context length is 8192 tokens")
        yield  # pragma: no cover

    monkeypatch.setattr(llm, "chat_stream", too_long)
    try:
        evs = _all_events((await client.post("/api/v1/agent/chat", json={"message": "hi"})).text)
        assert evs[-1]["type"] == "error" and evs[-1]["code"] == "context_budget"
    finally:
        await _drop_endpoint(ep)


async def test_big_results_end_the_search_and_the_answer_still_fits(client, user, fake, monkeypatch):
    """A tool result that takes the prompt over the budget ends the tool calls; the answer is
    written from shortened results."""
    ep = await _endpoint(user.id, "local")
    try:
        real = loop.run_tool

        async def big(ctx, name, args):
            res = await real(ctx, name, args)
            res.data = {"labels": ["label " + "q" * 50 for _ in range(400)]}
            return res

        monkeypatch.setattr(loop, "run_tool", big)
        first = await client.post("/api/v1/agent/chat", json={"message": "hi"})
        base = loop.prompt_tokens(fake.requests[0]["body"]["messages"], fake.requests[0]["body"].get("tools"))
        fake.requests.clear()
        monkeypatch.setattr(get_settings(), "assistant_prompt_tokens", base + 3_000)
        fake.script = [_tool_turn("list_labels", {}, "a"), _text_turn("Here is what I found.")]
        evs = _all_events((await client.post("/api/v1/agent/chat", json={"message": "labels?"})).text)
        assert first.status_code == 200
        final = next(e for e in evs if e["type"] == "final")
        assert final["stop_reason"] == "size" and final["content"].startswith("Here is what I found")
        last = fake.requests[-1]["body"]
        assert loop.prompt_tokens(last["messages"]) <= base + 3_000 and not last.get("tools")
    finally:
        await _drop_endpoint(ep)
