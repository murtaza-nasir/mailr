# ACT-SURFACES: api
from conftest import mail_date
from helpers.core_flow import _ingest
from sqlalchemy import select

from mailr.db import sessionmaker
from mailr.models import User


async def _ids(client, **params):
    r = await client.get("/api/v1/conversations", params=params)
    assert r.status_code == 200, r.text
    return [c["id"] for c in r.json()["items"]]


async def test_presets_match_the_old_inbox_types(client, user, raw):
    _, a = await _ingest(user, raw("Starred one", msgid="<l1@x.org>"))
    _, b = await _ingest(user, raw("Plain one", msgid="<l2@x.org>", date=mail_date(1)))
    await client.post("/api/v1/conversations/actions", json={"ids": [a], "action": "star"})

    r = (await client.get("/api/v1/layouts")).json()
    assert r["active"] == "default"
    ids = [x["id"] for x in r["layouts"]]
    assert ids[:6] == ["default", "important_first", "unread_first", "starred_first", "priority", "multiple"]
    starred = next(x for x in r["layouts"] if x["id"] == "starred_first")
    secs = starred["tabs"][0]["sections"]
    assert [s["id"] for s in secs] == ["starred", "everything"]
    assert secs[0]["view_all_q"] == "in:inbox is:starred"

    assert await _ids(client, layout="starred_first", tab="inbox", sec="starred") == [a]
    # "Everything else" leaves out what the Starred section already shows.
    assert await _ids(client, layout="starred_first", tab="inbox", sec="everything") == [b]
    assert (await client.get("/api/v1/conversations", params={"layout": "nope", "tab": "x", "sec": "y"})).status_code == 404


async def test_custom_layout_round_trip(client, user, raw):
    _, note = await _ingest(user, raw("Look later", frm=user.email, to=user.email,
                                      extra=f"Authentication-Results: mx; dkim=pass header.d={user.email.split('@')[1]}"))
    _, other = await _ingest(user, raw("Hello", frm="Bob <bob@example.org>"))
    layout = {"name": "Mine", "tabs": [
        {"name": "Inbox", "icon": "inbox", "sections": [
            {"title": "Unread", "base": "inbox", "filter": "unread", "limit": 5},
            {"title": "Rest", "base": "inbox"},
        ]},
        {"name": "Later", "icon": "bookmark", "badge": "unread", "sections": [
            {"title": "Notes to self", "base": "all", "q": "from:me to:me"},
        ]},
    ]}
    r = await client.put("/api/v1/layouts", json={"layouts": [layout], "active": "mine"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["active"] == "mine"
    mine = next(x for x in body["layouts"] if x["id"] == "mine")
    assert [t["id"] for t in mine["tabs"]] == ["inbox", "later"]
    assert [s["id"] for s in mine["tabs"][0]["sections"]] == ["unread", "rest"]
    assert (await client.get("/api/v1/me")).json()["settings"]["inbox_type"] == "layout:mine"

    assert await _ids(client, layout="mine", tab="later", sec="notes-to-self") == [note]
    unread = await _ids(client, layout="mine", tab="inbox", sec="unread")
    assert other in unread
    assert await _ids(client, layout="mine", tab="inbox", sec="rest") == [note]  # read, so not in Unread
    counts = (await client.get("/api/v1/layouts/mine/counts")).json()
    assert counts == {"inbox": 1, "later": 0}  # Bob's mail is unread; mail from me is read

    prev = (await client.post("/api/v1/layouts/preview", json={
        "section": {"title": "x", "base": "all", "q": "from:bob@example.org"}})).json()
    assert prev["total"] == 1 and prev["unread"] == 1

    bad = await client.put("/api/v1/layouts", json={"layouts": [{"name": "B", "tabs": [{"name": "T", "sections": [
        {"base": "label:999999"}]}]}]})
    assert bad.status_code == 400 and "label" in bad.json()["detail"]
    assert (await client.patch("/api/v1/me/settings", json={"inbox_layouts": []})).status_code == 400

    # Deleting the active layout falls back to the default inbox.
    r = (await client.put("/api/v1/layouts", json={"layouts": []})).json()
    assert r["active"] == "default"


async def test_imap_username_is_the_users_address(client, user):
    info = (await client.get("/api/v1/me/imap")).json()
    assert info["username"] == user.email


async def test_archive_from_a_label_view_removes_that_label(client, user, raw):
    _, c = await _ingest(user, raw("Read on laptop", msgid="<later1@x.org>"))
    lab = (await client.post("/api/v1/labels", json={"name": "Later"})).json()
    await client.post("/api/v1/conversations/actions", json={"ids": [c], "action": "add_labels", "label_ids": [lab["id"]]})
    r = await client.post("/api/v1/conversations/actions", json={"ids": [c], "action": "archive", "from_label_id": lab["id"]})
    assert r.status_code == 200, r.text
    conv = (await client.get(f"/api/v1/conversations/{c}")).json()["conversation"]
    labels = {x["id"]: x for x in (await client.get("/api/v1/labels")).json()}
    names = {labels[i]["role"] or labels[i]["name"] for i in conv["label_ids"] if i in labels}
    assert "Later" not in names and "inbox" not in names
    # Undo restores both.
    await client.post("/api/v1/conversations/actions", json=r.json()["undo"])
    conv = (await client.get(f"/api/v1/conversations/{c}")).json()["conversation"]
    names = {labels[i]["role"] or labels[i]["name"] for i in conv["label_ids"] if i in labels}
    assert {"Later", "inbox"} <= names


async def test_filters_apply_to_notes_to_self_sent_from_mailr(client, user, raw):
    lab = (await client.post("/api/v1/labels", json={"name": "Later"})).json()
    r = await client.post("/api/v1/rules", json={"query": "from:me to:me",
                                                  "actions": {"add_labels": [lab["id"]], "archive": True}})
    assert r.status_code in (200, 201), r.text
    # What the send path stores for a message the user sent (source="send", rules on).
    mid, _ = await _ingest(user, raw("Look later", frm=user.email, to=user.email, msgid="<self1@x.org>"),
                           source="send", roles=["sent"], keywords={"seen": True})
    other, _ = await _ingest(user, raw("To Bob", frm=user.email, to="bob@example.org", msgid="<out1@x.org>"),
                             source="send", roles=["sent"], keywords={"seen": True})
    labels = {x["id"]: x for x in (await client.get("/api/v1/labels")).json()}

    async def names(message_id):
        from mailr.db import sessionmaker
        from mailr.models import Message

        async with sessionmaker()() as db:
            m = await db.get(Message, message_id)
            return {labels[i]["role"] or labels[i]["name"] for i in m.label_ids if i in labels}

    assert {"Later", "sent"} <= await names(mid) and "inbox" not in await names(mid)
    assert "Later" not in await names(other)


async def test_archive_is_configurable(client, user, raw):
    later = (await client.post("/api/v1/labels", json={"name": "Later2"})).json()
    todo = (await client.post("/api/v1/labels", json={"name": "Todo2"})).json()
    keep = (await client.post("/api/v1/labels", json={"name": "Keep2"})).json()

    async def names(cid):
        conv = (await client.get(f"/api/v1/conversations/{cid}")).json()["conversation"]
        lbs = {x["id"]: x for x in (await client.get("/api/v1/labels")).json()}
        return {lbs[i]["role"] or lbs[i]["name"] for i in conv["label_ids"] if i in lbs}

    async def fresh(n):
        _, c = await _ingest(user, raw(f"Msg {n}", msgid=f"<arch{n}@x.org>"))
        await client.post("/api/v1/conversations/actions",
                          json={"ids": [c], "action": "add_labels", "label_ids": [later["id"], todo["id"], keep["id"]]})
        return c

    # Gmail-style: only Inbox, even from a label's list.
    r = await client.patch("/api/v1/me/settings", json={"archive_removes_current_label": False, "archive_also_removes": []})
    assert r.status_code == 200
    c = await fresh(1)
    await client.post("/api/v1/conversations/actions", json={"ids": [c], "action": "archive", "from_label_id": later["id"]})
    assert {"Later2", "Todo2", "Keep2"} <= await names(c) and "inbox" not in await names(c)

    # Always also remove Todo2; the current list's label (Later2) too.
    await client.patch("/api/v1/me/settings", json={"archive_removes_current_label": True,
                                                    "archive_also_removes": [todo["id"]]})
    c = await fresh(2)
    await client.post("/api/v1/conversations/actions", json={"ids": [c], "action": "archive", "from_label_id": later["id"]})
    got = await names(c)
    assert "Keep2" in got and not ({"Later2", "Todo2", "inbox"} & got)

    # From the inbox (no current label): Inbox + the always-removed label only.
    c = await fresh(3)
    await client.post("/api/v1/conversations/actions", json={"ids": [c], "action": "archive"})
    got = await names(c)
    assert {"Later2", "Keep2"} <= got and not ({"Todo2", "inbox"} & got)


async def test_select_all_with_exclusions(client, user, raw):
    cs = []
    for i in range(4):
        _, c = await _ingest(user, raw(f"Bulk {i}", msgid=f"<bulk{i}@x.org>"))
        cs.append(c)
    r = await client.post("/api/v1/conversations/actions", json={
        "query": {"view": "inbox", "q": "subject:bulk", "exclude_ids": [cs[1]]}, "action": "star"})
    assert r.status_code == 200, r.text
    starred = {c["id"]: c["starred"] for c in (await client.get("/api/v1/conversations", params={"view": "inbox", "q": "subject:bulk"})).json()["items"]}
    assert starred[cs[1]] is False and all(starred[c] for c in cs if c != cs[1])


async def test_a_claiming_tab_keeps_its_mail_out_of_the_inbox_tab(client, user, raw):
    """A "GitHub" tab on label:github with `claims` takes GitHub mail out of the Inbox tab (and its
    unread badge) without archiving it; turning claims off puts it back."""
    gh = (await client.post("/api/v1/labels", json={"name": "github"})).json()["id"]
    _, issue = await _ingest(user, raw("[speakr] Feature request", frm="Gil <notifications@github.com>"))
    _, other = await _ingest(user, raw("Lunch?", frm="Bob <bob@example.org>"))
    await client.post("/api/v1/conversations/actions", json={"ids": [issue], "action": "add_labels", "label_ids": [gh]})

    def layout(claims):
        return {"name": "GH", "tabs": [
            {"name": "Inbox", "icon": "inbox", "badge": "unread", "sections": [{"title": "All", "base": "inbox"}]},
            {"name": "Github", "icon": "info", "claims": claims, "sections": [{"title": "All", "base": f"label:{gh}"}]},
        ]}

    r = await client.put("/api/v1/layouts", json={"layouts": [layout(True)], "active": "gh"})
    assert r.status_code == 200, r.text
    assert next(x for x in r.json()["layouts"] if x["id"] == "gh")["tabs"][1]["claims"] is True
    assert await _ids(client, layout="gh", tab="inbox", sec="all") == [other]
    assert await _ids(client, layout="gh", tab="github", sec="all") == [issue]
    assert (await client.get("/api/v1/layouts/gh/counts")).json() == {"inbox": 1, "github": 1}
    conv = next(c for c in (await client.get("/api/v1/conversations", params={"view": "all"})).json()["items"] if c["id"] == issue)
    assert any(lb["role"] == "inbox" and lb["id"] in conv["label_ids"]
               for lb in (await client.get("/api/v1/labels")).json())  # still in the Inbox, not archived

    await client.put("/api/v1/layouts", json={"layouts": [layout(False)], "active": "gh"})
    assert set(await _ids(client, layout="gh", tab="inbox", sec="all")) == {issue, other}


async def test_tab_counts_all_or_unread_and_the_old_boolean(client, user, raw):
    """A tab counts unread conversations ("unread"), every conversation it lists ("all") or
    nothing ("none"); a claiming tab's mail stays out of the other tabs' counts either way.
    Layouts saved with the old boolean read as unread (true) and none (false)."""
    gh = (await client.post("/api/v1/labels", json={"name": "github"})).json()["id"]
    _, issue = await _ingest(user, raw("[speakr] Issue", frm="Gil <notifications@github.com>", msgid="<tc1@x.org>"))
    _, pr = await _ingest(user, raw("[speakr] PR", frm="Gil <notifications@github.com>", msgid="<tc2@x.org>"))
    _, lunch = await _ingest(user, raw("Lunch?", frm="Bob <bob@example.org>", msgid="<tc3@x.org>"))
    _, memo = await _ingest(user, raw("Memo", frm="Ann <ann@example.org>", msgid="<tc4@x.org>"))
    await client.post("/api/v1/conversations/actions", json={"ids": [issue, pr], "action": "add_labels", "label_ids": [gh]})
    await client.post("/api/v1/conversations/actions", json={"ids": [pr, memo], "action": "read"})

    lay = {"name": "Counts", "tabs": [
        {"name": "Inbox", "icon": "inbox", "badge": "all", "sections": [{"title": "All", "base": "inbox"}]},
        {"name": "New", "icon": "mail", "badge": "unread", "sections": [{"title": "All", "base": "inbox"}]},
        {"name": "Quiet", "icon": "flag", "badge": "none", "sections": [{"title": "All", "base": "inbox"}]},
        {"name": "Github", "icon": "info", "badge": "all", "claims": True, "sections": [{"title": "All", "base": f"label:{gh}"}]},
    ]}
    r = await client.put("/api/v1/layouts", json={"layouts": [lay], "active": "counts"})
    assert r.status_code == 200, r.text
    tabs = next(x for x in r.json()["layouts"] if x["id"] == "counts")["tabs"]
    assert [t["badge"] for t in tabs] == ["all", "unread", "none", "all"]
    # Inbox lists Lunch and Memo (the GitHub tab claims its two), read or not; New counts Lunch only.
    assert (await client.get("/api/v1/layouts/counts/counts")).json() == {"inbox": 2, "new": 1, "github": 2}

    # An unknown choice is stored as "unread"; a boolean sent by an old client maps too.
    lay["tabs"][0]["badge"], lay["tabs"][1]["badge"], lay["tabs"][2]["badge"] = "bogus", False, True
    tabs = next(x for x in (await client.put("/api/v1/layouts", json={"layouts": [lay]})).json()["layouts"]
                if x["id"] == "counts")["tabs"]
    assert [t["badge"] for t in tabs] == ["unread", "none", "unread", "all"]

    # A layout stored before indicators existed holds only a badge, sometimes a boolean; it reads
    # (and counts) as before.
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        stored = [dict(x, tabs=[dict({k: v for k, v in t.items() if k != "indicators"}, badge=b)
                                for t, b in zip(x["tabs"], [True, False, True, False], strict=True)])
                  for x in u.settings["inbox_layouts"]]
        u.settings = {**u.settings, "inbox_layouts": stored}
        await db.commit()
    tabs = next(x for x in (await client.get("/api/v1/layouts")).json()["layouts"] if x["id"] == "counts")["tabs"]
    assert [t["badge"] for t in tabs] == ["unread", "none", "unread", "none"]
    assert [t["indicators"] for t in tabs] == [["unread"], [], ["unread"], []]
    assert (await client.get("/api/v1/layouts/counts/counts")).json() == {"inbox": 1, "quiet": 1}


async def test_tab_indicators_count_unread_all_and_due_in_one_go(client, user, raw):
    """A tab shows any of unread, all and due. Due counts the conversations whose newest message
    carries a deadline (not rejected, from a week ago on) and names the earliest; a claiming
    tab's deadlines stay off the other tabs; "Not a deadline" takes one off."""
    from datetime import UTC, datetime, timedelta

    from mailr.models import LabelDecision

    today = datetime.now(UTC).date()
    day = lambda n: (today + timedelta(days=n)).isoformat()
    fin = (await client.post("/api/v1/labels", json={"name": "financial"})).json()["id"]
    # (A display name like "Wells Fargo" on another domain is brand impersonation: Spam, whose
    # deadlines count nowhere.)
    bill_m, bill = await _ingest(user, raw("Card payment due", frm="Card Services <alerts@card.example>", msgid="<d1@x.org>"))
    tax_m, tax = await _ingest(user, raw("Tax form", frm="IRS <irs@example.gov>", msgid="<d2@x.org>"))
    old_m, old = await _ingest(user, raw("Old invoice", frm="Acme <billing@acme.example>", msgid="<d3@x.org>"))
    no_m, no = await _ingest(user, raw("Sale ends", frm="Shop <deals@shop.example>", msgid="<d4@x.org>"))
    lunch_m, lunch = await _ingest(user, raw("Lunch Friday?", frm="Bob <bob@example.org>", msgid="<d5@x.org>"))
    await client.post("/api/v1/conversations/actions", json={"ids": [bill, tax, old, no], "action": "add_labels", "label_ids": [fin]})
    async with sessionmaker()() as db:
        for mid, when, status in ((bill_m, day(3), "applied"), (tax_m, day(9), "applied"), (old_m, day(-30), "applied"),
                                  (no_m, day(1), "rejected"), (lunch_m, day(2), "applied")):
            db.add(LabelDecision(user_id=user.id, message_id=mid, kind="smart", target="deadline", value=True,
                                 source="llm", source_ref=when, status=status))
        await db.commit()

    lay = {"name": "Money", "tabs": [
        {"name": "Inbox", "icon": "inbox", "indicators": ["unread", "due"], "sections": [{"title": "All", "base": "inbox"}]},
        {"name": "Financial", "icon": "briefcase", "indicators": ["due", "all"], "claims": True,
         "sections": [{"title": "All", "base": f"label:{fin}"}]},
        {"name": "Quiet", "icon": "flag", "indicators": [], "sections": [{"title": "All", "base": "inbox"}]},
    ]}
    r = await client.put("/api/v1/layouts", json={"layouts": [lay], "active": "money"})
    assert r.status_code == 200, r.text
    tabs = next(x for x in r.json()["layouts"] if x["id"] == "money")["tabs"]
    assert [(t["indicators"], t["badge"]) for t in tabs] == [(["unread", "due"], "unread"), (["all", "due"], "all"), ([], "none")]

    got = (await client.get("/api/v1/layouts/money/counts", params={"detail": "true"})).json()["tabs"]
    assert set(got) == {"inbox", "financial"}
    # Inbox keeps Lunch only (Financial claims the rest): one unread, one due in two days.
    assert got["inbox"]["unread"] == 1 and got["inbox"]["due_count"] == 1
    assert got["inbox"]["earliest_due"] == day(2) and got["inbox"]["earliest_due_from"] == "Bob"
    fin_tab = got["financial"]
    # The bill and the tax form; the rejected one and the month-old one do not count.
    assert fin_tab["all"] == 4 and fin_tab["due_count"] == 2 and "unread" not in fin_tab
    assert fin_tab["earliest_due"] == day(3) and fin_tab["earliest_due_from"] == "Card Services"
    assert fin_tab["earliest_due_subject"] == "Card payment due"
    ids = await _ids(client, layout="money", tab="financial", sec="all")
    assert fin_tab["earliest_due_cid"] in ids
    # Older clients still get one number per tab, for the badge.
    assert (await client.get("/api/v1/layouts/money/counts")).json() == {"inbox": 1, "financial": 4}

    # "Not a deadline" on the bill: the tax form is the earliest now.
    async with sessionmaker()() as db:
        d = (await db.execute(select(LabelDecision).where(LabelDecision.message_id == bill_m, LabelDecision.target == "deadline"))).scalar_one()
        d.status = "rejected"
        await db.commit()
    # Written here directly: the "Not a deadline" route records a change, which the cached counts
    # follow (api/layout_routes.py); this write does not.
    from mailr.api import layout_routes

    layout_routes._TAB_COUNTS.clear()
    fin_tab = (await client.get("/api/v1/layouts/money/counts", params={"detail": "true"})).json()["tabs"]["financial"]
    assert fin_tab["due_count"] == 1 and fin_tab["earliest_due"] == day(9) and fin_tab["earliest_due_from"] == "IRS"

    # A deadline a few days past still shows (as overdue on the client).
    async with sessionmaker()() as db:
        d = (await db.execute(select(LabelDecision).where(LabelDecision.message_id == old_m, LabelDecision.target == "deadline"))).scalar_one()
        d.source_ref = day(-2)
        await db.commit()
    layout_routes._TAB_COUNTS.clear()
    fin_tab = (await client.get("/api/v1/layouts/money/counts", params={"detail": "true"})).json()["tabs"]["financial"]
    assert fin_tab["due_count"] == 2 and fin_tab["earliest_due"] == day(-2)


async def test_due_indicator_counts_overdue_only_within_the_window(client, user, raw, monkeypatch):
    """MAILR_OVERDUE_DAYS (default 7) is how long a passed deadline still counts on its tab; an
    older one drops, so a forgotten deadline does not keep the tab red. 0 keeps only today on."""
    from datetime import UTC, datetime, timedelta

    from mailr.config import get_settings
    from mailr.models import LabelDecision

    today = datetime.now(UTC).date()
    day = lambda n: (today + timedelta(days=n)).isoformat()
    mids = []
    for i, when in enumerate((-10, -7, -3, 1)):
        mid, _ = await _ingest(user, raw(f"Deadline {i}", frm="Acme <billing@acme.example>", msgid=f"<ow{i}@x.org>"))
        mids.append((mid, when))
    async with sessionmaker()() as db:
        for mid, when in mids:
            db.add(LabelDecision(user_id=user.id, message_id=mid, kind="smart", target="deadline", value=True,
                                 source="llm", source_ref=day(when), status="applied"))
        await db.commit()
    lay = {"name": "Due", "tabs": [{"name": "Inbox", "icon": "inbox", "indicators": ["due"],
                                    "sections": [{"title": "All", "base": "inbox"}]}]}
    assert (await client.put("/api/v1/layouts", json={"layouts": [lay], "active": "due"})).status_code == 200

    async def due() -> tuple[int, str | None]:
        t = (await client.get("/api/v1/layouts/due/counts", params={"detail": "true"})).json()["tabs"]["inbox"]
        return t["due_count"], t["earliest_due"]

    assert get_settings().overdue_days == 7
    # Seven days past is the last day that counts; ten days past has dropped.
    assert await due() == (3, day(-7))
    monkeypatch.setattr(get_settings(), "overdue_days", 2)
    assert await due() == (1, day(1))
    monkeypatch.setattr(get_settings(), "overdue_days", 14)
    assert await due() == (4, day(-10))
    monkeypatch.setattr(get_settings(), "overdue_days", 0)
    assert await due() == (1, day(1))


async def test_layout_tab_detail_is_stored_and_off_by_default(client):
    """layout.tab_detail: "Show more on tabs when there is room" is kept per layout; a layout
    without it, and every preset, reads as off."""
    tabs = [{"name": "Inbox", "icon": "inbox", "indicators": ["unread", "all"], "sections": [{"title": "All", "base": "inbox"}]}]
    r = await client.put("/api/v1/layouts", json={"layouts": [
        {"name": "Wide", "tab_detail": True, "tabs": tabs},
        {"name": "Plain", "tabs": tabs},
        {"name": "Odd", "tab_detail": "yes", "tabs": tabs},
    ], "active": "wide"})
    assert r.status_code == 200, r.text
    got = {x["id"]: x.get("tab_detail") for x in r.json()["layouts"]}
    assert got["wide"] is True and got["plain"] is False and got["odd"] is True
    assert not any(x.get("tab_detail") for x in r.json()["layouts"] if x.get("preset"))
    again = {x["id"]: x.get("tab_detail") for x in (await client.get("/api/v1/layouts")).json()["layouts"]}
    assert again["wide"] is True and again["plain"] is False


async def test_layout_hide_empty_is_stored_and_off_by_default(client):
    """layout.hide_empty: "Hide empty sections" is kept per layout; a layout without it reads as off."""
    tabs = [{"name": "Inbox", "icon": "inbox", "sections": [{"title": "All", "base": "inbox"}]}]
    r = await client.put("/api/v1/layouts", json={"layouts": [
        {"name": "Tidy", "hide_empty": True, "tabs": tabs}, {"name": "Plain", "tabs": tabs}], "active": "tidy"})
    assert r.status_code == 200, r.text
    got = {x["id"]: x.get("hide_empty") for x in (await client.get("/api/v1/layouts")).json()["layouts"]}
    assert got["tidy"] is True and got["plain"] is False


async def test_archive_of_a_conversation_in_trash_changes_nothing(client, user, raw):
    # ACT mail.archive. 3 Oct 2026: a stale web selection archived a conversation already in Trash; the answer said
    # "affected 1" as if it had worked. It changes nothing and says so.
    _, c = await _ingest(user, raw("Already binned", msgid="<binned1@x.org>"))
    assert (await client.post("/api/v1/conversations/actions", json={"ids": [c], "action": "trash"})).status_code == 200
    labels = {x["id"]: x for x in (await client.get("/api/v1/labels")).json()}
    before = (await client.get(f"/api/v1/conversations/{c}")).json()["conversation"]["label_ids"]
    r = await client.post("/api/v1/conversations/actions", json={"ids": [c], "action": "archive"})
    assert r.status_code == 200, r.text
    assert r.json()["affected"] == 0 and r.json()["undo"] is None
    after = (await client.get(f"/api/v1/conversations/{c}")).json()["conversation"]["label_ids"]
    assert after == before and "trash" in {labels[i]["role"] for i in after if i in labels}
    # An inbox conversation still archives (affected counts its messages).
    _, d = await _ingest(user, raw("Still in inbox", msgid="<inbox9@x.org>"))
    r = await client.post("/api/v1/conversations/actions", json={"ids": [d], "action": "archive"})
    assert r.json()["affected"] == 1 and r.json()["undo"]["action"] == "restore_labels"
