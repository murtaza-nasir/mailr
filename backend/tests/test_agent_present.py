"""The presentation layer (agent/present.py): approval displays for
every preview kind, outcomes, step labels, the Markdown normaliser, and the golden fixtures that the
web (vitest) and Android (JUnit) renderers run. ACT approval.approve, ACT approval.deny

Regenerate the fixtures after a deliberate change:
    MAILR_WRITE_FIXTURES=1 uv run pytest tests/test_agent_present.py
"""

import json
import os
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from mailr.agent import present
from mailr.agent.present import Labels, build_display, describe_query, normalize_markdown, outcome

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "spec/fixtures/agent-blocks.schema.json"
BLOCKS = ROOT / "spec/fixtures/agent-blocks-fixtures.json"
MARKDOWN = ROOT / "spec/fixtures/agent-markdown-fixtures.json"
ANDROID = ROOT / "android/app/src/test/resources/design"
WRITE = os.environ.get("MAILR_WRITE_FIXTURES") == "1"
TZ = ZoneInfo("America/Chicago")

LABELS = Labels([
    SimpleNamespace(id=25, name="Shopping", role=None, color="@default/teal/3"),
    SimpleNamespace(id=26, name="Travel", role=None, color="#d0e8ff,#0b3d91"),
    SimpleNamespace(id=27, name="Finance", role=None, color=None),
    SimpleNamespace(id=28, name="Receipts", role=None, color="@vivid/orange/2"),
    SimpleNamespace(id=3, name="Trash", role="trash", color=None),
])

SENDERS = ["paypal.com", "amex.com", "chase.com", "venmo.com", "wellsfargo.com", "discover.com"]

# (name, action, payload, preview, outcome result) — the approvals the fixtures cover.
SCENARIOS = [
    ("filter-create", "rules.write",
     {"op": "create", "rule": {"name": "Finance", "query": " OR ".join(f"from:{s}" for s in SENDERS),
                               "actions": {"add_labels": [27], "archive": True}}},
     {"title": "Create filter", "kind": "filter", "lines": ["Matches: from:paypal.com OR …", "Do: add_labels=[27]"]},
     {"rule": {"id": 9}}),
    ("filter-query-mix", "rules.write",
     {"op": "update", "rule": {"name": "", "query": 'subject:"order shipped" -label:Receipts has:attachment newer_than:7d',
                               "actions": {"add_labels": [25, 28], "mark_read": True, "important": False}}},
     {"title": "Change filter", "kind": "filter", "lines": []}, {}),
    ("order-choices", "orders.write",
     {"op": "create", "order": {"name": "Auto-categorize by content", "trigger": "message", "query": "",
                                "question": "Based on the content of this email, which category does it best fit?",
                                "choices": [{"name": "Shopping", "description": "Orders and receipts",
                                             "actions": {"add_labels": [25]}},
                                            {"name": "Travel", "description": "Flights and hotels",
                                             "actions": {"add_labels": [26], "star": True}}],
                                "actions": {}, "errand": "", "deliver": []}},
     {"title": "Create standing order: Auto-categorize by content", "kind": "order", "lines": []},
     {"order": {"id": 4}}),
    ("order-errand", "orders.write",
     {"op": "create", "order": {"name": "Remote jobs", "trigger": "message", "query": "from:jobs.example",
                                "question": "Is this a job-alert email listing job openings?", "choices": [],
                                "actions": {"notify": True},
                                "errand": "Use the /jobs skill: list the analyst jobs that allow remote work.",
                                "deliver": ["brief", "notify"]}},
     {"title": "Create standing order: Remote jobs", "kind": "order", "lines": []}, {}),
    ("mail-trash", "mail.trash",
     {"action": "trash", "args": {}, "message_ids": [11, 12, 13, 14, 15, 16]},
     {"title": "Move to Trash: 6 message(s)", "message_ids": [11, 12, 13, 14, 15, 16],
      "lines": ["Reddit: u/cwningen95 replied to your comment", "Reddit: u/koniboni replied to your comment",
                "Reddit: u/lasolady replied to your comment", "Reddit: u/Avawavaa replied to your comment",
                "Reddit: u/zeta replied to your comment", "…and 1 more"]},
     {"affected": 6}),
    ("mail-labels", "mail.label",
     {"action": "add_labels", "args": {"label_ids": [25, 26]}, "message_ids": [21, 22]},
     {"title": "Add labels: 2 message(s)", "message_ids": [21, 22],
      "lines": ["Labels: Shopping, Travel", "Levi's: Your order shipped", "Delta: Your trip to Austin"]},
     {"affected": 2}),
    ("send", "mail.send", {"outbox_id": 5},
     {"title": "Send “Re: Quarterly plan”", "lines": ["From me@example.com", "To alice@example.com"],
      "reason": "hard rule: first message to a new address",
      "email": {"from": "me@example.com", "to": [{"name": "Alice Chen", "email": "alice@example.com"}],
                "cc": [{"email": "bob@example.com"}], "bcc": [], "subject": "Re: Quarterly plan",
                "text": "Friday at 10 works for me. See you then!\n\nOn Tue, Alice wrote:\n> old", "attachments": []}},
     {}),
    # An agent's draft to someone outside the conversation. The card says a draft is saved (nothing is
    # sent) and why, in the user's terms.
    ("draft-outsider", "mail.draft", {"op": "save", "draft_id": None},
     {"title": "Save the draft “Re: Quote for the Bluefin landing page”", "kind": "draft",
      "reason": "hard rule: an agent that read email (or an API token) writes a draft to people outside the conversation",
      "outsiders": ["Marco Silva"], "by_name": "Ambrose",
      "email": {"from": "", "to": [{"name": "Marco Silva", "email": "marco@example.org"}], "cc": [], "bcc": [],
                "subject": "Re: Quote for the Bluefin landing page", "text": "Hi Marco, a reminder about the quote.",
                "attachments": []}},
     {}),
    ("event-create", "calendar.create", {"op": "calendar.create"},
     {"title": "Create event “Review with Alice”", "kind": "event",
      "event": {"summary": "Review with Alice", "start": "2026-09-30T19:00:00+00:00", "end": "2026-09-30T20:00:00+00:00",
                "location": "Room 204", "calendar": "Work", "attendees": [{"email": "alice@example.com", "name": "Alice"}]},
      "lines": []}, {}),
    ("task-create", "tasks.create", {},
     {"title": "Add task “Send the syllabus”", "kind": "task",
      "task": {"summary": "Send the syllabus", "due": "2026-10-03", "list_name": "Teaching"}, "lines": []}, {}),
    ("memory-update", "memory.write", {"op": "remember"},
     {"title": "Update a memory", "kind": "memory",
      "lines": ["Now: Alice leads the analytics team", "New: Alice leads the data science team", "about Alice Chen"]},
     {}),
    ("skill-create", "skills.write", {"op": "create_skill"},
     {"title": "Create skill //jobs", "kind": "skill",
      "lines": ["Remote analyst jobs", "Scan the job-alert email for analyst roles.",
                "Used for: chat, summarize"]}, {"skill": {}}),
    ("settings", "prefs.write", {"op": "settings", "changes": {"density": "compact", "undo_send_seconds": 10}},
     {"title": "Change settings", "kind": "settings", "lines": ['density: "compact"', "undo_send_seconds: 10"]}, {}),
    ("generic-lines", "speakr.share", {},
     {"title": "Create a public link to “Weekly sync”", "danger": True,
      "lines": ["Anyone with the link can open the recording and its summary", "Expires: never"]}, {}),
]

# A weekly series as calendar/describe.py event_snapshot reads it: the Tuesday 6 Oct occurrence.
SYNC = {"summary": "Weekly sync", "location": "Room 4", "all_day": False, "tz": "America/Chicago",
        "rrule": "FREQ=WEEKLY;BYDAY=TU", "recurring": True, "first": "2026-09-01T15:00:00+00:00", "calendar": "Work",
        "occurrence": "2026-10-06T15:00:00+00:00", "start": "2026-10-06T15:00:00+00:00",
        "end": "2026-10-06T16:00:00+00:00"}
# A card for a change to a recurring event must say which occurrence and how
# much of the series, before and after, and name calendars as the user knows them.
SCENARIOS += [
    ("event-update-occurrence", "calendar.update",
     {"uid": "sync-1", "changes": {"start": "2026-10-06T19:00:00+00:00", "location": "Room 5"}, "scope": "this",
      "occurrence": "2026-10-06T15:00:00Z"},
     {"title": "Change one occurrence of “Weekly sync”", "kind": "event", "scope": "this", "before": SYNC,
      "event": {"uid": "sync-1", "start": "2026-10-06T19:00:00+00:00", "location": "Room 5"}, "lines": ["Weekly sync"]},
     {}),
    ("event-update-series", "calendar.update",
     {"uid": "sync-1", "changes": {"summary": "Team sync", "recurrence": "FREQ=WEEKLY;BYDAY=MO,TH"}, "scope": "all"},
     {"title": "Change “Weekly sync”", "kind": "event", "scope": "all", "before": {**SYNC, "occurrence": None},
      "event": {"uid": "sync-1", "summary": "Team sync", "recurrence": "FREQ=WEEKLY;BYDAY=MO,TH"}, "lines": []},
     {}),
    ("event-delete-following", "calendar.delete", {"uid": "sync-1", "scope": "following"},
     {"title": "Delete “Weekly sync”", "kind": "event", "scope": "following", "danger": True, "before": SYNC,
      "event": {"uid": "sync-1"}, "lines": ["Weekly sync"]}, {}),
    ("event-move-calendar", "calendar.update",
     {"uid": "dentist-1", "changes": {"calendar": "/dav/cal/alice@mailr.internal/family/"}, "scope": "all"},
     {"title": "Change “Dentist”", "kind": "event", "scope": "all",
      "before": {"summary": "Dentist", "location": "", "all_day": False, "tz": "America/Los_Angeles", "rrule": None,
                 "recurring": False, "first": "2026-10-08T16:00:00+00:00", "calendar": "Work",
                 "start": "2026-10-08T16:00:00+00:00", "end": "2026-10-08T17:00:00+00:00"},
      "event": {"uid": "dentist-1", "calendar": "/dav/cal/alice@mailr.internal/family/",
                "calendar_name": "Family (the Family group)"}, "lines": []}, {}),
    ("event-create-repeating", "calendar.create", {"op": "calendar.create"},
     {"title": "Create event “Standup”", "kind": "event",
      "event": {"summary": "Standup", "start": "2026-10-05T14:15:00+00:00", "end": "2026-10-05T14:30:00+00:00",
                "recurrence": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", "calendar": "/dav/cal/alice@mailr.internal/default/",
                "calendar_name": "Work"}, "lines": []}, {}),
]

STATUSES = ["pending", "executed", "denied", "expired", "failed"]

STEPS = [
    ("search_conversations", {"q": "from:paypal.com newer_than:30d"}),
    ("search_conversations", {"view": "inbox"}),
    ("search_semantic", {"q": "conference travel reimbursement"}),
    ("use_skill", {"name": "/jobs"}),
    ("manage_filter", {"action": "create", "name": "Finance"}),
    ("manage_order", {"action": "create", "name": "Remote jobs"}),
    ("modify_conversations", {"action": "trash", "conversation_ids": [1, 2, 3, 4, 5, 6]}),
    ("list_labels", {}),
    ("get_brief", {}),
    ("create_event", {}),
    ("get_transcript", {}),
    ("some_new_tool", {}),
]


@pytest.fixture(autouse=True)
def _fixed_now(monkeypatch):
    """Card dates leave out the year of the current year: the fixtures are made on 1 Oct 2026."""
    from datetime import UTC, datetime

    from mailr.agent import when

    monkeypatch.setattr(when, "now", lambda: datetime(2026, 10, 1, 15, 0, tzinfo=UTC))


def _expect(d: dict) -> list[str]:
    """Strings every renderer must show for a display: title, summary, field labels and the words
    of each value (label names, people, conditions, list items, the start of a quote, both sides of
    a diff, option names)."""
    out = [d["title"]] + ([d["summary"]] if d["summary"] else []) + ([d["reason"]] if d["reason"] else [])
    for f in d["fields"]:
        if f["label"]:
            out.append(f["label"])
        v = f["value"]
        t = v["t"]
        if t == "labels":
            out += [i["name"] for i in v["items"]]
        elif t == "people":
            out += [i["name"] or i["email"] for i in v["items"]]
        elif t == "query":
            out += [c["text"] for c in v["conditions"]] or [v["text"]]
        elif t == "list":
            out += v["items"]
        elif t == "quote":
            out.append(v["text"].split("\n")[0][:40])
        elif t == "diff":
            out += [v["before"], v["after"]]
        elif t == "choices":
            out += [i["name"] for i in v["items"]]
        else:
            out.append(v["text"])
    return [s for s in dict.fromkeys(out) if s]


async def _blocks() -> dict:
    approvals = []
    for name, action, payload, preview, result in SCENARIOS:
        d = await build_display(action, payload, preview, LABELS, TZ)
        outs = [outcome(action, st, payload, result if st == "executed" else
                        ({"error": "the server said no"} if st == "failed" else None), d) for st in STATUSES]
        approvals.append({"name": name, "action": action, "display": d, "outcomes": outs, "expect": _expect(d)})
    steps = []
    for name, args in STEPS:
        m = await present.step_msg(None, 0, name, args)
        steps.append({"name": name, "args": args, "label": present.en(m),
                      **({"label_msg": m} if present.i18n.is_msg(m) else {}), "icon": present.step_icon(name)})
    return {"version": 1, "approvals": approvals, "steps": steps}


# Markdown samples: what the model wrote, and what each surface must show. `layout` is
# tableLayout() at 360 dp and 800 dp: each column counts
# min(40, its longest cell in characters) × 7 + 16; a table that fits shows as a table, one that
# does not shows as cards with three or more columns, else as a scrolling table.
MD_SAMPLES = [
    {"name": "categories-table",
     "md": "# Categories & matching rules\n\nI set up these categories:\n"
           "| Category | Match rule | Label |\n|---|---|---|\n"
           "| Shopping | `from:levi.com OR from:gap.com OR from:nike.com` | Shopping |\n"
           "| Travel | `from:delta.com OR from:united.com OR from:marriott.com` | Travel |\n"
           "| Finance | `from:paypal.com OR from:amex.com OR from:chase.com` | Finance |\n\n"
           "Want me to create the filters?",
     "expect": {"headings": [3], "tables": [{"cols": 3, "rows": 3, "layout": {"360": "cards", "800": "table"}}],
                "text": ["Categories & matching rules", "Shopping", "from:levi.com OR from:gap.com OR from:nike.com",
                         "Want me to create the filters?"]}},
    {"name": "sections",
     "md": "## Summary\nTwo invoices are due this week.\n\n### Details\n- **Amex**: $1,204 due Friday\n- **Chase**: $88 due "
           "Monday\n\n#### Fine print\nLate fees apply.\n",
     "expect": {"headings": [3, 4], "tables": [], "text": ["Summary", "Details", "Amex", "Fine print"]}},
    {"name": "short-table",
     "md": "Times that work:\n\n| Day | Time |\n|:---|---:|\n| Tue | 10:00 |\n| Thu | 14:30 |",
     "expect": {"headings": [], "tables": [{"cols": 2, "rows": 2, "layout": {"360": "table", "800": "table"}}],
                "text": ["Times that work:", "Tue", "14:30"]}},
    {"name": "wide-table",
     "md": "| Sender | Subject | Date | Amount | Status |\n|---|---|---|---|---|\n"
           "| American Express | Your statement is ready to view online | 28 Sep 2026 | $1,204.18 | Due Friday |\n"
           "| Chase | Payment reminder for the account ending 4411 | 29 Sep 2026 | $88.00 | Paid |",
     "expect": {"headings": [], "tables": [{"cols": 5, "rows": 2, "layout": {"360": "cards", "800": "table"}}],
                "text": ["American Express", "Payment reminder for the account ending 4411", "Due Friday"]}},
    {"name": "two-col-long",
     "md": "| Rule | What it does |\n|---|---|\n"
           "| Finance | Labels mail from PayPal, American Express, Chase, Venmo and Discover as Finance and skips the inbox |",
     "expect": {"headings": [], "tables": [{"cols": 2, "rows": 1, "layout": {"360": "scroll", "800": "table"}}],
                "text": ["Finance", "skips the inbox"]}},
    {"name": "citations-list",
     "md": "Three things need a reply:\n\n1. The PADS review request [1](mailr-cite:1)\n2. Alice's question about "
           "Friday [2](mailr-cite:2)\n3. The parking permit renewal",
     "expect": {"headings": [], "tables": [], "text": ["Three things need a reply:", "PADS review request",
                                                        "parking permit renewal"]}},
    {"name": "code-and-setext",
     "md": "Filters\n=======\n\nThe query I used:\n\n```\n# not a heading\nfrom:paypal.com OR from:amex.com\n```\n\n---\n",
     "expect": {"headings": [3], "tables": [], "text": ["Filters", "# not a heading", "from:paypal.com OR from:amex.com"]}},
    {"name": "br-in-cells",
     "md": "Here:\n| Label | Senders |\n|---|---|\n| Shopping | levi.com<br>gap.com |",
     "expect": {"headings": [], "tables": [{"cols": 2, "rows": 1, "layout": {"360": "table", "800": "table"}}],
                "text": ["Here:", "levi.com; gap.com"]}},
]


def _markdown() -> dict:
    return {"version": 1, "samples": [{**s, "normalized": normalize_markdown(s["md"])} for s in MD_SAMPLES]}


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1) + "\n"


async def test_fixtures_match_present_py():
    blocks, md = _dump(await _blocks()), _dump(_markdown())
    if WRITE:
        ANDROID.mkdir(parents=True, exist_ok=True)
        for path, text in ((BLOCKS, blocks), (MARKDOWN, md), (ANDROID / BLOCKS.name, blocks),
                           (ANDROID / MARKDOWN.name, md)):
            path.write_text(text)
    assert BLOCKS.read_text() == blocks, "agent-blocks-fixtures.json is stale: MAILR_WRITE_FIXTURES=1"
    assert MARKDOWN.read_text() == md, "agent-markdown-fixtures.json is stale: MAILR_WRITE_FIXTURES=1"
    assert (ANDROID / BLOCKS.name).read_text() == blocks, "the Android copy differs"
    assert (ANDROID / MARKDOWN.name).read_text() == md, "the Android copy differs"


def test_fixtures_validate_against_the_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA.read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(json.loads(BLOCKS.read_text()), schema)


def _field(d, label):
    return next(f["value"] for f in d["fields"] if f["label"] == label)


async def test_filter_shows_label_names_and_readable_query():
    _, action, payload, preview, _ = SCENARIOS[0]
    d = await build_display(action, payload, preview, LABELS, TZ)
    assert d["title"] == "Create a filter" and d["icon"] == "filter"
    q = _field(d, "Matches")
    assert q["t"] == "query" and q["text"] == "From any of: paypal.com, amex.com, chase.com, … (6)"
    assert q["conditions"][0]["values"] == SENDERS
    labels = _field(d, "Add label")
    assert labels == {"t": "labels", "text": "Finance", "items": [{"name": "Finance", "color": None}]}
    assert _field(d, "Also")["text"] == "Skip the Inbox"
    assert d["summary"] == "Mail from 6 senders gets the label Finance."
    assert "add_labels" not in d["text"] and "[27]" not in d["text"]
    assert present.lines_of(d)[2].startswith("Matches: From any of:")


async def test_filter_query_conditions_and_colours():
    _, action, payload, preview, _ = SCENARIOS[1]
    d = await build_display(action, payload, preview, LABELS, TZ)
    conds = [c["text"] for c in _field(d, "Matches")["conditions"]]
    assert conds == ["Subject contains “order shipped”", "Does not have the label Receipts", "Has an attachment",
                     "Newer than 7 days"]
    assert _field(d, "Matches")["conditions"][1]["labels"] == [{"name": "Receipts", "color": "@vivid/orange/2"}]
    assert [i["color"] for i in _field(d, "Add labels")["items"]] == ["@default/teal/3", "@vivid/orange/2"]
    assert _field(d, "Also")["items"] == ["Mark as read", "Never mark as important"]


def test_describe_query_forms():
    assert describe_query("from:(a.com OR b.com)")["text"] == "From any of: a.com, b.com"
    assert describe_query("from:a@x.example,b@y.com")["text"] == "From any of: a@x.example, b@y.com"
    assert describe_query("invoice receipt")["text"] == "Contains all of: “invoice”, “receipt”"
    assert describe_query("category:promotions -from:me is:unread")["text"] == \
        "In the Promotions category; Not from me; Is unread"
    assert describe_query("label:shopping", LABELS)["conditions"][0]["labels"][0]["name"] == "Shopping"
    assert describe_query("after:2026/9/1 larger:5M")["text"] == "After 1 Sep 2026; Larger than 5 MB"
    assert describe_query("(from:a.com subject:x) OR to:me")["text"].startswith("Any of:")
    bad = describe_query('from:"unclosed')
    assert bad["raw"] == 'from:"unclosed'


async def test_order_with_choices_names_each_option():
    _, action, payload, preview, _ = SCENARIOS[2]
    d = await build_display(action, payload, preview, LABELS, TZ)
    assert d["title"] == "Create a standing order: Auto-categorize by content"
    opts = _field(d, "Options")
    assert [i["name"] for i in opts["items"]] == ["Shopping", "Travel"]
    assert opts["items"][1]["then"][0]["value"]["items"] == [{"name": "Travel", "color": "#d0e8ff,#0b3d91"}]
    assert "add_labels" not in d["text"]
    assert d["summary"] == "Sorts each new email into one of 2 options."


async def test_mail_trash_counts_and_outcome():
    _, action, payload, preview, result = SCENARIOS[4]
    d = await build_display(action, payload, preview, LABELS, TZ)
    assert d["title"] == "Move 6 messages to Trash" and d["icon"] == "trash"
    msgs = _field(d, "Messages")
    assert msgs["items"][0] == "Reddit — u/cwningen95 replied to your comment" and msgs["more"] == 1
    o = outcome(action, "executed", payload, result, d)
    assert o["state"] == "executed" and o["text"] == "Approved · moved 6 messages to Trash"
    assert o["msg"]["key"] == "approvals.outcome.executed"
    assert o["msg"]["params"]["done"]["key"] == "approvals.mail.trash.done"
    assert outcome(action, "denied")["text"] == "Denied"
    assert outcome(action, "expired")["text"] == "Expired"
    assert outcome(action, "failed", payload, {"error": "boom"})["text"] == "Approved · failed: boom"


async def test_send_event_task_memory_skill_settings():
    disp = {}
    for name, action, payload, preview, _ in SCENARIOS:
        disp[name] = await build_display(action, payload, preview, LABELS, TZ)
    send = disp["send"]
    assert send["reason"] == "First message to a new address"
    assert _field(send, "To")["items"] == [{"name": "Alice Chen", "email": "alice@example.com"}]
    # The whole body, quoted history included: a fake "On … wrote:" line must not hide a tail.
    assert "old" in next(f for f in send["fields"] if f["value"]["t"] == "quote")["value"]["text"]
    ev = disp["event-create"]
    assert _field(ev, "When")["text"] == "Wed 30 Sep, 14:00–15:00"  # Chicago time
    assert _field(ev, "Guests")["items"][0]["name"] == "Alice"
    assert _field(disp["task-create"], "Due")["text"] == "Sat 3 Oct (all day)"
    note = _field(disp["memory-update"], "Note")
    assert note["t"] == "diff" and note["before"].endswith("analytics team")
    assert disp["skill-create"]["title"] == "Create skill /jobs"
    assert _field(disp["skill-create"], "Used for")["text"] == "chat, summarize"
    assert _field(disp["settings"], "Density")["text"] == "compact"
    gen = disp["generic-lines"]
    assert gen["danger"] and _field(gen, "Expires")["text"] == "never"


async def test_check_stores_display_and_readable_lines(user):
    from mailr.core import permissions
    from mailr.db import sessionmaker
    from mailr.models import Label

    async with sessionmaker()() as db:
        lb = Label(user_id=user.id, name="Shopping", color="@default/teal/3")
        db.add(lb)
        await db.flush()
        u = await db.get(type(user), user.id)
        actor = permissions.Actor.assistant(u, "s-present")
        dec = await permissions.check(db, actor, "rules.write", target="Create filter",
                                      payload={"op": "create", "rule": {"name": "", "query": "from:levi.com OR from:gap.com",
                                                                        "actions": {"add_labels": [lb.id]}}},
                                      preview={"title": "Create filter", "kind": "filter",
                                               "lines": ["Matches: from:levi.com OR from:gap.com",
                                                         f"Do: add_labels=[{lb.id}]"]})
        pa = dec.action
        d = pa.preview["display"]
        assert d["title"] == "Create a filter"
        assert pa.preview["lines"] == ["Mail from 2 senders gets the label Shopping.",
                                       "Matches: From any of: levi.com, gap.com", "Add label: Shopping"]
        card = await present.approval_card(db, pa)
        assert card["outcome"]["text"] == "Waiting for approval" and card["display"] == d
        await db.rollback()


async def test_old_preview_gets_a_display_when_read(user):
    from datetime import UTC, datetime, timedelta

    from mailr.db import sessionmaker
    from mailr.models import PendingAction

    async with sessionmaker()() as db:
        pa = PendingAction(user_id=user.id, subject="assistant", actor="Ambrose", action="mail.trash", target="",
                           payload={"action": "trash", "args": {}, "message_ids": [1, 2]},
                           preview={"title": "Move to Trash: 2 message(s)", "lines": ["A: x", "B: y"],
                                    "message_ids": [1, 2]},
                           status="executed", result={"affected": 2}, expires_at=datetime.now(UTC) + timedelta(hours=1))
        db.add(pa)
        await db.flush()
        card = await present.approval_card(db, pa)
        assert card["display"]["title"] == "Move 2 messages to Trash"
        assert card["outcome"]["text"] == "Approved · moved 2 messages to Trash"
        await db.rollback()


async def test_step_labels_and_icons():
    blocks = await _blocks()
    by = {(s["name"], json.dumps(s["args"])): s for s in blocks["steps"]}
    assert by[("use_skill", '{"name": "/jobs"}')]["label"] == "Using the /jobs skill"
    assert by[("modify_conversations", json.dumps(STEPS[6][1]))]["label"] == "Moving 6 conversations to Trash"
    assert by[("manage_filter", json.dumps(STEPS[4][1]))]["icon"] == "label"
    assert by[("get_transcript", "{}")]["icon"] == "recording"
    assert by[("some_new_tool", "{}")] == {"name": "some_new_tool", "args": {}, "label": "Some new tool",
                                           "icon": "other"}


def test_normalize_markdown():
    n = normalize_markdown
    assert n("# Title\n\ntext\n\n## Sub\n\n### Deep") == "### Title\n\ntext\n\n#### Sub\n\n**Deep**"
    assert n("Intro:\n| a | b |\n|---|---|\n| 1 | 2 |") == "Intro:\n\n| a | b |\n|---|---|\n| 1 | 2 |"
    assert n("```\n# keep\n```") == "```\n# keep\n```"
    assert n("Title\n=====\nbody") == "### Title\nbody"
    assert n("line one\nline two\n---\nafter") == "line one\nline two\n\n---\nafter"
    assert n("a\n\n\n\nb\n\n---\n") == "a\n\nb"
    assert n("| x | y<br>z |\n|---|---|") == "| x | y; z |\n|---|---|"
    assert n("") == "" and n(None) == ""
    for s in MD_SAMPLES:  # idempotent
        assert n(n(s["md"])) == n(s["md"])


async def test_event_cards_say_which_occurrence_before_and_after():
    """The card for a change to a recurring event once did not say
    which occurrence it changed. Times are in the user's zone with the weekday; a second zone shows
    when the event keeps another clock."""
    disp = {name: await build_display(action, payload, preview, LABELS, TZ, clock="12h")
            for name, action, payload, preview, _ in SCENARIOS}
    one = disp["event-update-occurrence"]
    assert one["title"] == "Change one occurrence of “Weekly sync”"
    assert _field(one, "Applies to")["text"] == "Tue 6 Oct, 10:00–11:00 AM (this occurrence only)"
    t = _field(one, "Time")
    assert (t["before"], t["after"]) == ("Tue 6 Oct, 10:00–11:00 AM", "2:00–3:00 PM")  # same day; length kept
    assert (_field(one, "Where")["before"], _field(one, "Where")["after"]) == ("Room 4", "Room 5")
    series = disp["event-update-series"]
    assert _field(series, "Applies to")["text"] == "The whole series (weekly on Tuesday)"
    assert (_field(series, "Title")["before"], _field(series, "Title")["after"]) == ("Weekly sync", "Team sync")
    assert _field(series, "Repeats")["after"] == "Weekly on Mon and Thu"
    gone = disp["event-delete-following"]
    assert gone["danger"] and _field(gone, "Applies to")["text"] == "This and following occurrences, from Tue 6 Oct"
    moved = disp["event-move-calendar"]
    assert _field(moved, "When")["text"] == "Thu 8 Oct, 11:00 AM–12:00 PM CDT (9:00–10:00 AM PDT)"
    assert (_field(moved, "Calendar")["before"], _field(moved, "Calendar")["after"]) == ("Work", "Family (the Family group)")
    rep = disp["event-create-repeating"]
    assert _field(rep, "Repeats")["text"] == "Every weekday (Mon–Fri)"
    assert _field(rep, "Calendar")["text"] == "Work"
    assert (await build_display(*SCENARIOS[-1][1:4], LABELS, TZ))["fields"][0]["value"]["text"] == "Mon 5 Oct, 09:15–09:30"


async def test_event_cards_never_show_internal_identifiers():
    """No collection URL, store address, raw RULE, ISO time or UTC on a calendar card. A
    calendar given only as a URL is left out rather than shown."""
    for name, action, payload, preview, _ in SCENARIOS:
        if not action.startswith("calendar."):
            continue
        for clock in ("12h", "24h"):
            text = (await build_display(action, payload, preview, LABELS, TZ, clock=clock))["text"]
            for bad in ("/dav/", "@mailr.internal", "FREQ=", "+00:00", "T15:00", "UTC", "sync-1", "dentist-1"):
                assert bad not in text, f"{name}: {bad!r} in {text!r}"
    bare = await build_display("calendar.create", {}, {"title": "Create event “X”", "kind": "event", "event": {
        "summary": "X", "start": "2026-10-05", "calendar": "/dav/cal/alice@mailr.internal/default/"}}, LABELS, TZ)
    assert "Calendar" not in [f["label"] for f in bare["fields"]]
    # An older stored preview (scope only in its lines) still says the scope in words.
    old = await build_display("calendar.delete", {}, {"title": "Delete event “X”", "kind": "event", "event": {"uid": "x"},
                                                      "lines": ["X", "scope: following"]}, LABELS, TZ)
    assert _field(old, "Applies to")["text"] == "This and following"


def _msgs(obj):
    """Every msg ({"key", "params"}) in a fixture, nested ones too."""
    if isinstance(obj, dict):
        if isinstance(obj.get("key"), str) and isinstance(obj.get("params"), dict):
            yield obj
        for v in obj.values():
            yield from _msgs(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _msgs(v)


async def test_every_msg_in_the_fixtures_is_in_the_catalogue_and_renders_to_the_english():
    """The words of a card are keys the apps render: each
    key exists, and the English of each msg is the text beside it."""
    from mailr import i18n

    blocks = await _blocks()
    found = list(_msgs(blocks))
    assert len(found) > 80
    assert not [m["key"] for m in found if not i18n.has(m["key"])]
    for a in blocks["approvals"]:
        d = a["display"]
        assert i18n.render(d["title_msg"]) == d["title"] if "title_msg" in d else True
        for f in d["fields"]:
            if "label_msg" in f:
                assert i18n.render(f["label_msg"]) == f["label"]
        for o in a["outcomes"]:
            assert i18n.render(o["msg"]) == o["text"]


async def test_every_step_and_mail_verb_has_its_keys():
    from mailr import i18n

    for name in present.STEP_FIXED:
        assert i18n.has(f"approvals.step.fixed.{name}"), name
    for action in [*present.MAIL_VERB, "other"]:
        for form in ("ask", "done", "ing"):
            assert i18n.has(f"approvals.mail.{action}.{form}"), (action, form)
    for args in ({"action": a} for a in ("create", "update", "delete", "apply", "activate", "save", "rename", "color",
                                          "pause", "resume", "list", "x")):
        for tool in ("manage_lists", "manage_calendars", "manage_filter", "manage_label", "manage_layout", "manage_order"):
            for named in ({}, {"name": "N", "label": "N"}):
                m = await present.step_msg(None, 0, tool, {**args, **named})
                assert i18n.has(m["key"]), m
