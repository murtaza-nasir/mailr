"""Passage index, hybrid search, citations, highlighting, and the embedding guard."""

import json
import uuid

import pytest
from embedfake import CALLS, embedding_endpoint
from helpers.agent import (
    FakeStream,
    _endpoint,
    _events,
    _ingest,
    _setup_identity,
    _text_turn,
    _tool_turn,
)
from helpers.passages import LONG, _process
from sqlalchemy import func, select

from mailr.db import sessionmaker
from mailr.intel import citations, embed, highlight, passages
from mailr.models import ChatSession, Message, MessagePassage


class _P:
    def __init__(self, uid, token=None):
        self.user_id = uid
        self.token = token


def test_chunker_offsets_and_caps():
    chunks = passages.chunk(LONG)
    assert len(chunks) >= 3
    for c in chunks:
        assert LONG[c.start:c.end] == c.text and c.text.strip() == c.text
        assert len(c.text) <= passages.HARD_MAX
    # the long paragraph is split with overlap, in order
    assert all(a.start < b.start for a, b in zip(chunks, chunks[1:], strict=False))
    assert passages.chunk("") == [] and passages.chunk("   \n\n ") == []
    many = "\n\n".join(f"Paragraph {i} " + "word " * 150 for i in range(60))
    assert len(passages.chunk(many)) == passages.MAX_PASSAGES


def test_locate_and_mark():
    t = "Dear all,\n\nThe OEA/DSA  nominations close on Sep 30,\nwhile papers are due “Oct 4”."
    m = highlight.locate(t, 'nominations close on sep 30, while papers are due "Oct 4"')
    assert m and m.score == 100 and t[m.start:m.end].startswith("nominations")
    assert highlight.locate(t, "nominations closes on Sept 30") is not None  # fuzzy, near-verbatim
    assert highlight.locate(t, "the budget was approved yesterday") is None
    html = ('<div><p>The OEA/DSA <b>nominations</b> close on Sep 30,</p><p>while papers are due Oct 4.</p>'
            '<style>.nominations{}</style></div>')
    out, found = highlight.mark_html(html, "nominations close on Sep 30, while papers")
    assert found and out.count('class="mailr-hl"') == 3 and 'id="mailr-hl-1"' in out
    assert "<style>.nominations{}</style>" in out  # styles untouched
    same, found = highlight.mark_html(html, "absent phrase that is not there")
    assert not found and same == html


async def test_passages_embed_dedupe_search_and_highlight(monkeypatch, client, user, raw):
    async with embedding_endpoint(monkeypatch):
        to = user.email
        sig = "\n\nBest regards,\nDr. Carol Diaz\nDepartment of Economics"
        m1, c1 = await _ingest(user.id, raw("PADS 2027 call", frm="Carol Diaz <carol@uni.example>", to=to, body=LONG + sig))
        m2, c2 = await _ingest(user.id, raw("Lunch", frm="Bob <bob@example.org>", to=to,
                                            body="Want to get lunch on Friday? The new ramen place opened." + sig))
        CALLS.clear()
        await _process(m1)
        await _process(m2)
        async with sessionmaker()() as db:
            n = (await db.execute(select(func.count()).select_from(MessagePassage).where(
                MessagePassage.user_id == user.id))).scalar_one()
            emb = (await db.execute(select(func.count()).select_from(MessagePassage).where(
                MessagePassage.user_id == user.id, MessagePassage.embedding.is_not(None)))).scalar_one()
            msg = await db.get(Message, m1)
            assert msg.embedding is not None and len(msg.embedding) == 512
        assert n >= 4 and emb == n
        assert sum(len(c) for c in CALLS) <= n  # one input per distinct passage

        # Hybrid search finds the paragraph (keyword) and the conversation (meaning).
        r = (await client.get("/api/v1/passages/search", params={"q": "awards nominations close"})).json()
        top = r["items"][0]
        assert top["message_id"] == m1 and "Nominations" in top["text"] and "<mark>" in top["highlight"]
        assert top["url"].endswith(f"/mail/all/{c1}?m={m1}&hl=" + top["url"].split("&hl=")[1])
        s = (await client.get("/api/v1/search/semantic", params={"q": "ramen lunch"})).json()
        assert s["items"][0]["id"] == c2 and s["semantic"] is True

        # Highlighted body.
        b = (await client.get(f"/api/v1/messages/{m1}/body-highlight",
                              params={"q": "close on September 30, while paper submissions"})).json()
        assert b["highlighted"] and 'id="mailr-hl-1"' in b["html"]

        # A new message whose passage text is identical to an existing one reuses its vector.
        CALLS.clear()
        m3, _ = await _ingest(user.id, raw("PADS 2027 call", frm="Carol Diaz <carol@uni.example>", to=to,
                                           body=LONG + sig, msgid=f"<{uuid.uuid4().hex}@x>"))
        await _process(m3)
        assert sum(len(c) for c in CALLS) == 0


async def test_citations_verified_unverified_invisible(monkeypatch, client, user, raw):
    to = user.email
    m1, c1 = await _ingest(user.id, raw("Award deadlines", frm="Carol <carol@uni.example>", to=to, body=LONG))
    async with sessionmaker()() as db:
        text, cites = await citations.resolve(db, _P(user.id), (
            f'Nominations close Sep 30 [[m:{m1} "Nominations for the OEA and DSA awards close on September 30"]]. '
            f'Budget approved [[m:{m1} "the budget was approved unanimously"]]. See [[c:{c1}]] and [[m:999999]].'))
    assert [c["verified"] for c in cites] == [True, False, True]
    assert "[1](mailr-cite:1)" in text and "[3](mailr-cite:3)" in text and "999999" not in text
    v = cites[0]
    assert v["start"] is not None and "&hl=" in v["url"] and f"m={m1}" in v["url"]
    # A token that may not see the message (local-only connector, non-local token) gets no citation.
    conn_id, _ = await _setup_identity(user, data_class="local_only")
    m2, _ = await _ingest(user.id, raw("Grades", frm="s@uni.example", to=to, body="PRIVATE grade discussion"),
                          connector_id=conn_id)
    r = await client.post("/api/v1/tokens", json={"name": "hosted-agent", "scopes": ["read"]})
    from mailr.models import ApiToken

    async with sessionmaker()() as db:
        tok = await db.get(ApiToken, r.json()["item"]["id"])
        text, cites = await citations.resolve(db, _P(user.id, tok), f'x [[m:{m2} "PRIVATE grade"]] y')
    assert cites == [] and "PRIVATE" not in text


async def test_embedding_guard_for_local_only(monkeypatch, user, raw):
    """With only a hosted embedding endpoint, local-only mail is never embedded."""
    async with embedding_endpoint(monkeypatch, locality="hosted"):
        conn_id, me = await _setup_identity(user, data_class="local_only")
        mid, _ = await _ingest(user.id, raw("Student record", frm="s@uni.example", to=me, body="CONFIDENTIAL-SSN"),
                               connector_id=conn_id)
        nid, _ = await _ingest(user.id, raw("Newsletter", frm="n@news.example", to=me, body="Weekly roundup"))
        CALLS.clear()
        await _process(mid)
        await _process(nid)
        assert all("CONFIDENTIAL" not in t for batch in CALLS for t in batch)
        async with sessionmaker()() as db:
            pm = (await db.execute(select(MessagePassage).where(MessagePassage.message_id == mid))).scalars().all()
            pn = (await db.execute(select(MessagePassage).where(MessagePassage.message_id == nid))).scalars().all()
        assert pm and all(p.embedding is None for p in pm)
        assert pn and all(p.embedding is not None for p in pn)
        async with sessionmaker()() as db:
            assert await embed.embed_query(db, "grades", sensitive=True) is None


async def test_chat_sessions_sticky_sensitivity_and_citations(client, user, raw, fake_llm):
    conn_id, me = await _setup_identity(user, data_class="local_only")
    m1, conv = await _ingest(user.id, raw("Exam appeal", frm="Student <s@uni.example>", to=me,
                                          body="I would like to appeal my exam grade before Friday."),
                             connector_id=conn_id)
    ep = await _endpoint(user.id, "local")
    try:
        fake_llm.script = [
            _tool_turn("get_conversation", {"conversation_id": conv}),
            _text_turn(f'The student wants to appeal [[m:{m1} "appeal my exam grade before Friday"]].'),
        ]
        r = await client.post("/api/v1/agent/chat", json={"message": "What does the student want?"})
        evs = _events(r.text)
        sid = next(e for e in evs if e["type"] == "session")["session_id"]
        start = next(e for e in evs if e["type"] == "tool_start")
        assert start["label"] == "Reading “Exam appeal”"
        tool = next(e for e in evs if e["type"] == "tool")
        assert tool["summary"] == "1 message" and tool["ok"]
        final = next(e for e in evs if e["type"] == "final")
        assert final["citations"][0]["verified"] and "[1](mailr-cite:1)" in final["content"]
        done = evs[-1]
        assert done["type"] == "done" and done["sensitive"] is True
        async with sessionmaker()() as db:
            assert (await db.get(ChatSession, sid)).sensitive is True
        # Next turn in the same session: history comes from the server and the call is sensitive
        # from the start (the router is asked for a local endpoint).
        fake_llm.script = [_text_turn("Nothing else.")]
        r2 = await client.post("/api/v1/agent/chat", json={"message": "Anything else?", "session_id": sid})
        evs2 = _events(r2.text)
        assert evs2[-1]["type"] == "done" and evs2[-1]["sensitive"] is True
        sent = fake_llm.requests[-1]["body"]["messages"]
        assert any(m["role"] == "tool" for m in sent) and sent[-1]["content"] == "Anything else?"
        # The session replays for the UI with citations.
        s = (await client.get(f"/api/v1/agent/sessions/{sid}")).json()
        assert [t["role"] for t in s["turns"]] == ["user", "assistant", "user", "assistant"]
        assert s["turns"][1]["citations"][0]["message_id"] == m1
        # Another user's session id is refused.
        r3 = await client.post("/api/v1/agent/chat", json={"message": "hi", "session_id": "nope"})
        assert _events(r3.text)[-1]["type"] == "error"
    finally:
        from helpers.agent import _drop_endpoint

        await _drop_endpoint(ep)


_ = (FakeStream, json, pytest)


async def test_a_conversation_id_cited_as_a_message_finds_the_right_email(user, raw):
    """The 1 Oct chat: a search returned conversation N (Amex); the model wrote [[m:N "Card wasn't
    present…"]]; message N was an unrelated 2013 SMS. The citation must land on the Amex email, and
    a stray message id the model never saw must not become a link."""
    import json

    to = user.email
    amex, amex_conv = await _ingest(user.id, raw("Your Additional Card may not have been present", frm="Amex <a@amex.example>",
                                                 to=to, body="A purchase was made, but it appears the Card wasn't present for a purchase."))
    sms, sms_conv = await _ingest(user.id, raw("SMS with 0321", frm="me@example.org", to=to, body="ok see you at 5"))
    # The search result the model read lists conversations only.
    known = json.dumps({"items": [{"conversation_id": amex_conv, "subject": "Your Additional Card…"}]})
    async with sessionmaker()() as db:
        text, cites = await citations.resolve(db, _P(user.id),
                                              f'A possible fraud alert [[m:{amex_conv} "Card wasn\'t present for a purchase"]].', known)
    assert len(cites) == 1 and cites[0]["message_id"] == amex and cites[0]["verified"], cites
    # The same number as a message id that the model never saw, with a quote it does not hold: no link.
    async with sessionmaker()() as db:
        text, cites = await citations.resolve(db, _P(user.id), f'See [[m:{sms} "Card wasn\'t present"]].', known)
    assert cites == [] and "mailr-cite" not in text
    # A message the model did read still cites normally, even with a loose quote.
    async with sessionmaker()() as db:
        text, cites = await citations.resolve(db, _P(user.id), f'[[m:{sms} "see you at five"]]',
                                              json.dumps({"message_id": sms}))
    assert len(cites) == 1 and cites[0]["message_id"] == sms and sms_conv


async def test_model_change_reembeds_and_updates_message_vectors(monkeypatch, user, raw):
    """Pending passages come from two queries (no vector first, then another model's), and
    the message vectors are written in one executemany."""
    from sqlalchemy import update

    async with embedding_endpoint(monkeypatch):
        m1, _ = await _ingest(user.id, raw("Budget review", body=LONG))
        await _process(m1)
        async with sessionmaker()() as db:
            await db.execute(update(MessagePassage).where(MessagePassage.message_id == m1).values(embed_model="old-model"))
            await db.execute(update(Message).where(Message.id == m1).values(embedding=[0.0] * 512))
            await db.commit()
        async with sessionmaker()() as db:
            got = await embed.embed_pending_passages(db, user.id)
            await db.commit()
            assert got["pending"] >= 1
            models = set((await db.execute(select(MessagePassage.embed_model).where(
                MessagePassage.message_id == m1))).scalars())
            vec = (await db.execute(select(Message.embedding).where(Message.id == m1))).scalar_one()
        assert models and "old-model" not in models
        assert any(abs(x) > 0 for x in vec)
