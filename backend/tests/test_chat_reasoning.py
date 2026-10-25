"""The model's reasoning never reaches the assistant's answer: reasoning in its own field
(`reasoning_content`, llama.cpp's and vLLM's parsers), in <think> tags inside the content
(--reasoning-format none), or before a lone </think> (a chat template that opens the block
itself, as Qwen3's does). intel/llm.py ThinkSplitter, agent/loop.py stream_round."""

from __future__ import annotations

from helpers.agent import _drop_endpoint, _endpoint, _events, fake_llm  # noqa: F401
from sqlalchemy import select

from mailr.db import sessionmaker
from mailr.intel import llm
from mailr.models import ChatMessage

THOUGHT = "The user wants me to run review_mail on the screen scope."


def _pieces(*texts, finish="stop"):
    return [*({"choices": [{"delta": {"content": t}}]} for t in texts),
            {"choices": [{"delta": {}, "finish_reason": finish}]}]


def _split_all(pieces):
    sp = llm.ThinkSplitter()
    out = []
    for p in pieces:
        out += sp.feed(p)
    return out + sp.flush()


def test_splitter_tags_across_pieces():
    ev = _split_all(["<thi", "nk>Let me ", "think.</th", "ink>\n\nHello ", "<b>there</b>"])
    assert "".join(t for k, t in ev if k == "content") == "Hello <b>there</b>"
    assert "".join(t for k, t in ev if k == "reasoning") == "Let me think."
    # A lone closing tag: what came before was reasoning.
    ev = _split_all([THOUGHT[:20], THOUGHT[20:], "</think>", "\n\nDone."])
    assert [k for k, _ in ev] == ["content", "content", "retract", "content"]
    assert llm.strip_think(THOUGHT + "</think>\n\nDone.") == "Done."
    assert llm.strip_think("<think>\nhm\n</think>\nAnswer < 3 and a <b>tag</b>") == "Answer < 3 and a <b>tag</b>"
    assert llm.strip_think("Plain answer.") == "Plain answer."
    # An unfinished block at the end stays reasoning.
    assert llm.strip_think("Hi<think>never closed") == "Hi"


async def _turn(client, user_id, fake, script):
    ep = await _endpoint(user_id, "local")
    try:
        fake.script = script
        r = await client.post("/api/v1/agent/chat", json={"message": "anything urgent on screen?"})
        evs = _events(r.text)
        assert not [e for e in evs if e["type"] == "error"], evs
        sid = next(e for e in evs if e["type"] == "session")["session_id"]
        async with sessionmaker()() as db:
            stored = list((await db.execute(select(ChatMessage).where(
                ChatMessage.session_id == sid, ChatMessage.role == "assistant").order_by(ChatMessage.id))).scalars())
        return evs, stored
    finally:
        await _drop_endpoint(ep)


async def test_reasoning_field_is_not_the_answer(client, user, fake_llm):
    script = [[{"choices": [{"delta": {"reasoning_content": THOUGHT}}]},
               {"choices": [{"delta": {"reasoning_content": " Then answer."}}]},
               *_pieces("Nothing urgent.")]]
    evs, stored = await _turn(client, user.id, fake_llm, script)
    shown = "".join(e.get("text", "") for e in evs if e["type"] == "delta")
    final = next(e for e in evs if e["type"] == "final")
    assert shown == "Nothing urgent." and final["content"] == "Nothing urgent."
    assert stored[-1].content == "Nothing urgent."


async def test_think_tags_in_content_are_hidden_while_streaming(client, user, fake_llm):
    script = [_pieces("<think>", THOUGHT[:15], THOUGHT[15:], "</thi", "nk>\n\n", "Nothing ", "urgent.")]
    evs, stored = await _turn(client, user.id, fake_llm, script)
    shown = "".join(e.get("text", "") for e in evs if e["type"] == "delta")
    assert shown == "Nothing urgent." and "review_mail" not in shown
    assert next(e for e in evs if e["type"] == "final")["content"] == "Nothing urgent."
    assert stored[-1].content == "Nothing urgent."


async def test_a_lone_closing_tag_retracts_the_reasoning_before_a_tool_call(client, user, fake_llm):
    tool = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {
        "name": "list_labels", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}]}
    script = [[{"choices": [{"delta": {"content": THOUGHT}}]}, {"choices": [{"delta": {"content": "\n</think>\n\n"}}]},
               tool],
              _pieces(THOUGHT, "</think>", "\n\nNothing urgent.")]
    evs, stored = await _turn(client, user.id, fake_llm, script)
    # The reasoning streamed before its closing tag is cleared on screen, segment by segment.
    segs = [e for e in evs if e["type"] == "segment"]
    assert segs and segs[0]["content"] == ""
    final = next(e for e in evs if e["type"] == "final")
    assert final["content"] == "Nothing urgent."
    assert all("review_mail" not in (m.content or "") for m in stored)
    assert stored[-1].content == "Nothing urgent."
    # The reasoning is not replayed to the model either.
    replay = fake_llm.requests[-1]["body"]["messages"]
    assert not any("review_mail" in str(m.get("content") or "") for m in replay if m["role"] == "assistant")
