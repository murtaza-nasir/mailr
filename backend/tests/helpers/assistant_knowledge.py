"""Shared helpers, from test_assistant_knowledge.py."""

import copy
import json

import pytest

from helpers.agent import (
    FakeStream,
)


class FakeLLM(FakeStream):
    """Scripted streams (FakeStream) plus scripted non-streaming completions (llm.chat)."""

    posts: list[dict] = []
    replies: list[str] = []

    async def post(self, url, headers=None, json=None):
        FakeLLM.posts.append({"url": url, "body": copy.deepcopy(json)})
        content = FakeLLM.replies.pop(0) if FakeLLM.replies else '{"subject": "", "body": "ok"}'

        class R:
            status_code = 200
            text = ""

            def json(self):
                return {"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]}

        return R()


class _Handle:
    """What a test scripts: stream turns (script/requests) and completions (replies/posts)."""

    script = property(lambda self: FakeStream.script, lambda self, v: setattr(FakeStream, "script", v))
    requests = property(lambda self: FakeStream.requests)
    replies = property(lambda self: FakeLLM.replies, lambda self, v: setattr(FakeLLM, "replies", v))
    posts = property(lambda self: FakeLLM.posts)


@pytest.fixture
def fake(monkeypatch):
    from mailr.intel import llm

    monkeypatch.setattr(llm, "_http", lambda r, task, **kw: FakeLLM(**kw))
    FakeStream.requests, FakeStream.script = [], []
    FakeLLM.posts, FakeLLM.replies = [], []
    return _Handle()


def _all_events(text_body):
    """The SSE stream as a list of {"type": event name, **data} (nothing dropped)."""
    out = []
    for block in text_body.replace("\r\n", "\n").split("\n\n"):
        name, data = "message", None
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
        if data is not None and name not in ("tool_call", "tool_result"):
            out.append({"type": "delta" if name == "token" else name,
                        **({"message": data.get("detail")} if name == "error" else {}), **data})
    return out
