# ACT-SURFACES: api assistant
"""Text a model writes for an email (core/mdmail.py): its Markdown becomes email HTML in every
draft path (create_draft, update_draft edits, an open window, /agent/draft), plain text in fields
without rich rendering (event descriptions, label and filter names), and ordinary text is never
mangled. The text/plain part of a list reads cleanly."""

import json
from types import SimpleNamespace

import pytest
from helpers.agent import _drop_endpoint, _endpoint, _ingest, _setup_identity
from helpers.assistant_knowledge import fake  # noqa: F401
from sqlalchemy import select

from mailr.core import mdmail
from mailr.core.compose import plain_text
from mailr.db import sessionmaker
from mailr.models import Label, PendingAction, User

EXAMPLE = """Thank you for the productive discussion on our AI workshop planning and university strategy.
Here are the agreed decisions and next steps:
**Decisions:**
* We will consult Sam Okafor for his prior experience with AI 101-style workshops rather than starting from scratch.
* The Strategy Council meeting on October 21, 2026, will serve as the dedicated forum.
**Next Steps:**
* **Dana Whitfield:** Email Sam Okafor by the evening of October 21.
* **Theo:** Share workshop outlines with the Strategy Council."""


def test_the_meeting_follow_up_becomes_lists_and_bold():
    assert mdmail.to_html(EXAMPLE) == (
        "<div>Thank you for the productive discussion on our AI workshop planning and university strategy.<br>"
        "Here are the agreed decisions and next steps:<br><strong>Decisions:</strong></div>"
        "<ul><li>We will consult Sam Okafor for his prior experience with AI 101-style workshops rather than "
        "starting from scratch.</li><li>The Strategy Council meeting on October 21, 2026, will serve as the "
        "dedicated forum.</li></ul>"
        "<div><strong>Next Steps:</strong></div>"
        "<ul><li><strong>Dana Whitfield:</strong> Email Sam Okafor by the evening of October 21.</li>"
        "<li><strong>Theo:</strong> Share workshop outlines with the Strategy Council.</li></ul>")
    # The plain-text part of the sent message: "- " items, no stars, no run of blank lines.
    text = plain_text({"html": mdmail.to_html(EXAMPLE)})
    assert "*" not in text and "\n\n\n" not in text
    assert "  - Dana Whitfield: Email Sam Okafor by the evening of October 21." in text.splitlines()
    assert "Next Steps:" in text.splitlines()


def test_paragraphs_headings_nested_lists_links_and_quotes():
    assert mdmail.to_html("Hi Dana,\n\nSee you *Friday*.") == \
        "<div>Hi Dana,</div><div><br></div><div>See you <em>Friday</em>.</div>"
    assert mdmail.to_html("# Plan\n1. Book\n   - room _A_\n   - room B\n2. Invite\n\n3) Ship") == (
        "<div><strong>Plan</strong></div><ol><li>Book<ul><li>room <em>A</em></li><li>room B</li></ul></li>"
        "<li>Invite</li><li>Ship</li></ol>")
    assert mdmail.to_html("4. four\n5. five") == '<ol start="4"><li>four</li><li>five</li></ol>'
    assert mdmail.to_html("See [the agenda](https://x.org/a?b=1&c=2), https://y.org/p_q. Or <https://z.org>.") == (
        '<div>See <a href="https://x.org/a?b=1&amp;c=2">the agenda</a>, <a href="https://y.org/p_q">'
        'https://y.org/p_q</a>. Or <a href="https://z.org">https://z.org</a>.</div>')
    assert mdmail.to_html("Run `make **all**` now") == "<div>Run make **all** now</div>"
    assert mdmail.to_html("> On Monday you wrote:\n> **ship it**\n\nAgreed.") == (
        "<blockquote><div>On Monday you wrote:<br><strong>ship it</strong></div></blockquote><div>Agreed.</div>")
    assert mdmail.to_html("<script>alert(1)</script> & co") == "<div>&lt;script&gt;alert(1)&lt;/script&gt; &amp; co</div>"
    assert mdmail.to_html("[x](javascript:alert(1))") == "<div>[x](javascript:alert(1))</div>"


@pytest.mark.parametrize("text", [
    "5 * 3 = 15, and 2*3*4 = 24",
    "Price*",
    "*",
    "* ",
    "Write to sam_okafor@northwind.example or jane.doe+x@a-b.org",
    "Set my_var_name and __init__ and snake_case_thing",
    "The file is https://example.org/a_b_c/d_e?x=1_2",
    "Thanks,\n-- \nDana Whitfield",
    "Best,\n- Dana",
    "#1 priority, 2026. Done",
    "a ** b ** c and a _ b _ c",
])
def test_ordinary_text_is_not_mangled(text):
    html = mdmail.to_html(text)
    assert "<strong>" not in html and "<em>" not in html and "<ul>" not in html and "<ol>" not in html
    assert mdmail.to_text(text) == text.strip()


def test_quoted_reply_text_stays_quoted():
    body = "> Can you make Friday?\n\nYes, Friday works."
    assert mdmail.to_html(body).startswith("<blockquote><div>Can you make Friday?</div></blockquote>")
    assert mdmail.to_text(body) == body


def test_plain_text_mode():
    assert mdmail.to_text(EXAMPLE).splitlines()[2:] == [
        "Decisions:",
        "• We will consult Sam Okafor for his prior experience with AI 101-style workshops rather than starting "
        "from scratch.",
        "• The Strategy Council meeting on October 21, 2026, will serve as the dedicated forum.",
        "Next Steps:",
        "• Dana Whitfield: Email Sam Okafor by the evening of October 21.",
        "• Theo: Share workshop outlines with the Strategy Council."]
    assert mdmail.to_text("## Agenda\n1. **Budget**\n   - see [sheet](https://x.org/s)\n\n_Thanks_ `all`") == (
        "Agenda\n1. Budget\n  • see sheet (https://x.org/s)\n\nThanks all")
    assert mdmail.to_text("Mail [me](mailto:a@b.org) at https://x.org") == "Mail me (a@b.org) at https://x.org"
    assert mdmail.inline_text("**Budget** review with `Dana`") == "Budget review with Dana"
    assert mdmail.fragment_html(" **next steps** ") == " <strong>next steps</strong> "
    assert mdmail.fragment_html("a\n* one\n* two") == "<div>a</div><ul><li>one</li><li>two</li></ul>"


async def _principal(user_id):
    from mailr.core.auth import ALL_SCOPES, Principal

    db = sessionmaker()()
    u = await db.get(User, user_id)
    return db, Principal(user=u, scopes=set(ALL_SCOPES) - {"admin"})


async def test_agent_draft_tools_write_markdown_as_html(client, user, raw):
    from mailr.agent.tools import ToolCtx, run_tool

    _, me = await _setup_identity(user)
    m1, _ = await _ingest(user.id, raw("Workshop", frm="Theo <pierre@acme.example>", to=me, body="Notes?"))
    db, p = await _principal(user.id)
    async with db:
        ctx = ToolCtx(db=db, p=p, assistant=True)
        d = await run_tool(ctx, "create_draft", {"reply_to_message_id": m1, "body": EXAMPLE})
        did = d.data["draft_id"]
        # An edit's new text: its Markdown too becomes formatting, in place.
        await run_tool(ctx, "update_draft", {"draft_id": did, "edits": [
            {"find": "Thank you", "replace": "**Many** thanks"}]})
        # An open compose window that was never saved gets the new text as HTML as well.
        ui = {"compose": [{"id": "w1", "draft_id": None, "focused": True, "mode": "new", "to": [], "cc": [],
                           "bcc": [], "subject": "", "body": "Hi"}]}
        w = await run_tool(ToolCtx(db=db, p=p, assistant=True, ui=ui), "update_draft",
                           {"window": "w1", "body": "Hi\n\n* **Theo:** outlines"})
        ch = w.events[0]["changes"]
        assert ch["body_text"] == "Hi\n\n* **Theo:** outlines"
        assert ch["body_html"] == "<div>Hi</div><ul><li><strong>Theo:</strong> outlines</li></ul>"
        w = await run_tool(ToolCtx(db=db, p=p, assistant=True, ui=ui), "update_draft",
                           {"window": "w1", "edits": [{"find": "outlines", "replace": "_outlines_"}]})
        assert w.events[0]["changes"]["edits"][0]["replace_html"] == "<em>outlines</em>"
    html = (await client.get(f"/api/v1/drafts/{did}")).json()["payload"]["html"]
    assert "<ul><li><strong>Dana Whitfield:</strong> Email Sam Okafor" in html
    assert "<strong>Next Steps:</strong>" in html and "<strong>Many</strong> thanks" in html
    assert "**" not in html and "* " not in html


async def test_help_me_write_turns_markdown_into_html(client, user, fake):
    await _setup_identity(user)
    ep = await _endpoint(user.id, "local")
    try:
        fake.replies = [json.dumps({"subject": "Workshop follow-up", "body": EXAMPLE})]
        r = await client.post("/api/v1/agent/draft", json={"mode": "new", "instructions": "follow up with Theo",
                                                           "to": ["pierre@acme.example"]})
        assert r.status_code == 200, r.text
        html = r.json()["payload"]["html"]
        assert "<li><strong>Theo:</strong> Share workshop outlines" in html and "**" not in html
        # The drafting prompt allows simple formatting and rules out the rest.
        system = fake.posts[-1]["body"]["messages"][0]["content"]
        assert "bullet and numbered lists" in system and "no tables, headings or code blocks" in system
        # An edit's word diff is of the text the user reads, without the marks.
        fake.replies = [json.dumps({"subject": "Hi", "body": "Hi Bob,\n\n**See** you Friday."})]
        r = await client.post("/api/v1/agent/draft", json={
            "mode": "new", "instructions": "make it Friday",
            "current": {"subject": "Hi", "to": [{"email": "bob@example.org"}],
                        "html": "<p>Hi Bob,</p><p>See you Monday.</p>"}})
        out = r.json()
        assert out["edit"] and {"old": "Monday.", "new": "Friday."} in out["changes"]
        assert "<strong>See</strong> you Friday." in out["payload"]["html"]
    finally:
        await _drop_endpoint(ep)


async def test_event_text_label_and_filter_names_are_plain(client, user):
    from mailr.agent.calendar_common import _event_fields
    from mailr.agent.tools import ToolCtx, run_tool

    out = await _event_fields(SimpleNamespace(known=None), {
        "summary": "**Budget** review", "location": "Room `4`",
        "description": "## Agenda\n* **Numbers** for Q3\n* See [sheet](https://x.org/s)"})
    assert out == {"summary": "Budget review", "location": "Room 4",
                   "description": "Agenda\n• Numbers for Q3\n• See sheet (https://x.org/s)"}
    await _setup_identity(user)
    db, p = await _principal(user.id)
    async with db:
        ctx = ToolCtx(db=db, p=p, assistant=True, chat_session_id="s-md")
        r = await run_tool(ctx, "manage_label", {"action": "create", "label": "**Clients**"})
        assert r.pending is None
        assert (await db.execute(select(Label).where(Label.user_id == user.id, Label.name == "Clients"))).scalar_one()
        r = await run_tool(ctx, "manage_filter", {"action": "create", "name": "**Finance** mail",
                                                  "query": "from:bank@x.example", "archive": True})
        pa = await db.get(PendingAction, r.data["approval_id"])
        assert "Finance mail" in json.dumps(pa.payload) and "**" not in json.dumps(pa.payload)
