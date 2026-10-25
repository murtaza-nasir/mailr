# ACT-SURFACES: assistant
"""The citation marker grammar (spec/cases/citation-cases.yaml) and the streaming resolver.

The cases: an answer showed raw `[[event:2ad60b43-…@2026-05-20T13:00:00+00:00 "Chinatown
walk (until ~11 AM)"]]` (the model added a title, which the parser did not accept), and markers showed
as raw text while the answer streamed."""

import json
import random
from pathlib import Path

import pytest

from mailr.intel import citations
from mailr.intel import citegrammar as cg

CASES = Path(__file__).resolve().parents[2] / "spec" / "cases" / "citation-cases.yaml"
UID = "2ad60b43-fa20-4375-955f-8a5e03b55a29"
OWNER = (f'The only near match is a May travel event whose description mentions "until 11 AM" '
         f'[[event:{UID}@2026-05-20T13:00:00+00:00 "Chinatown walk (until ~11 AM)"]], but that\'s not a '
         f'"snooze until" item.')


def _cases() -> list[dict]:
    out = []
    for line in CASES.read_text().splitlines():
        if line.startswith("  - "):
            out.append(json.loads(line[4:]))
    return out


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["id"])
def test_citation_cases(case):
    got = [{"kind": m.kind, "id": m.id, "at": m.at, "quote": m.quote} for m in cg.scan(case["in"])]
    assert got == case["markers"]
    assert cg.clean(case["in"]) == case["clean"]
    assert cg.clean(case["in"], streaming=True) == case["stream"]
    assert "[[" not in case["clean"] or "not-a-marker" in case["id"] or "newline" in case["id"]


class _Stub(citations.Resolver):
    """A resolver without a database: every event, task and message is visible."""

    def __init__(self, offset: int = 0):
        super().__init__(None, None, None, offset)

    async def number(self, mk: cg.Marker) -> int:
        key = (mk.kind, mk.id, mk.at or "")
        if key not in self.index:
            self._add(key, {"kind": mk.kind, "uid": mk.id, "title": mk.quote})
        return self.index[key]


async def test_owner_answer_resolves_the_titled_event():
    r = _Stub()
    out = await r.resolve(OWNER)
    assert '"until 11 AM" [1](mailr-cite:1), but' in out
    assert "[[" not in out and len(r.cites) == 1 and r.cites[0]["uid"] == UID


async def test_stream_shows_links_as_soon_as_each_marker_closes():
    text = (f"Lunch [[event:{UID}@2026-10-05T17:00:00Z]] and the memo [[m:12 \"budget\"]]. "
            f"Again [[ Event : {UID} @ 2026-10-05T17:00:00Z ]], a list [[1,2]] and [[note: \"plain\"]] end [[m:1")
    want = ("Lunch [1](mailr-cite:1) and the memo [2](mailr-cite:2). Again [1](mailr-cite:1), "
            "a list [[1,2]] and plain end")
    rng = random.Random(7)
    for _ in range(25):
        r = _Stub()
        st = citations.Streamer(r)
        shown, new = "", []
        i = 0
        while i < len(text):
            j = min(len(text), i + rng.randint(1, 9))
            piece, cites = await st.feed(text[i:j])
            # Nothing that is still a raw marker is ever shown.
            assert "[[event" not in piece and "[[m" not in piece and "[[ E" not in piece
            shown += piece
            new += cites
            i = j
        shown += st.rest()
        # A space before the open marker at the end may already be out; the final text replaces it.
        assert shown.rstrip() == want
        assert [c["n"] for c in new] == [1, 2]
        # The final text, from the same resolver, keeps the numbers the stream showed.
        assert await r.resolve(text) == want


async def test_stream_numbers_follow_the_segment_offset():
    r = _Stub(offset=3)
    st = citations.Streamer(r)
    piece, cites = await st.feed("See [[task:t1]].")
    assert piece == "See [4](mailr-cite:4)." and cites[0]["n"] == 4


async def test_stream_passes_ordinary_brackets_through():
    st = citations.Streamer(_Stub())
    a, _ = await st.feed("Read [the guide](https://x.example) and arr")
    b, _ = await st.feed("ay[0] now [")
    c, _ = await st.feed("tbd]")
    assert a + b + c + st.rest() == "Read [the guide](https://x.example) and array[0] now [tbd]"


async def test_chat_stream_sends_each_citation_before_its_link(client, user, raw, fake_llm):
    """The SSE stream: a `cite` event, then the token with the numbered link; never a raw marker.
    The final answer keeps the number the stream showed."""
    from helpers.agent import _drop_endpoint, _endpoint, _events, _ingest, _setup_identity

    _, me = await _setup_identity(user)
    _, conv = await _ingest(user.id, raw("Q4 budget review", frm="Alice <alice@acme.example>", to=me,
                                         body="Send the numbers by Friday."))
    ep = await _endpoint(user.id, "local")
    try:
        pieces = ["Alice asked for the numbers [", f"[c:{conv}", ' "Q4 budget review"', "]] by Friday", "."]
        fake_llm.script = [[{"choices": [{"delta": {"content": x}}]} for x in pieces]
                           + [{"choices": [{"delta": {}, "finish_reason": "stop"}]}]]
        r = await client.post("/api/v1/agent/chat", json={"message": "What does Alice want?"})
        evs = _events(r.text)
        shown = [e for e in evs if e["type"] in ("delta", "cite")]
        assert all("[[" not in e.get("text", "") for e in shown)
        i_cite = next(i for i, e in enumerate(shown) if e["type"] == "cite")
        i_link = next(i for i, e in enumerate(shown) if "[1](mailr-cite:1)" in e.get("text", ""))
        assert i_cite < i_link and shown[i_cite]["citation"]["conversation_id"] == conv
        assert "".join(e["text"] for e in shown if e["type"] == "delta") == \
            "Alice asked for the numbers [1](mailr-cite:1) by Friday."
        final = next(e for e in evs if e["type"] == "final")
        assert final["content"] == "Alice asked for the numbers [1](mailr-cite:1) by Friday."
        assert [c["n"] for c in final["citations"]] == [1]
    finally:
        await _drop_endpoint(ep)
