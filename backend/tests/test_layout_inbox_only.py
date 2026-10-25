# ACT-SURFACES: api
"""Layout sections with "Only mail in the Inbox" (layout.inbox_only, `inbox_only`, core/layouts.py):
a label section beside Inbox sections drops a conversation once it
is archived, in its list, the tab counts, the sidebar count, the tabs it claims from and Show in
tab. Migration b7d4e1a9c3f6 turns it on for label sections in inbox-like tabs."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from helpers.core_flow import _ingest

from mailr.db import sessionmaker
from mailr.models import User

API = "/api/v1"


async def _setup(client, user, raw, *, inbox_only: bool = True):
    fin = (await client.post(f"{API}/labels", json={"name": "Financial"})).json()["id"]
    gh = (await client.post(f"{API}/labels", json={"name": "Github"})).json()["id"]
    _, bill = await _ingest(user, raw("Your monthly statement", frm="Bank <bank@bank.example>", msgid="<io1@x.org>"))
    _, rcpt = await _ingest(user, raw("Payment received", frm="Bank <bank@bank.example>", msgid="<io2@x.org>"))
    _, pr = await _ingest(user, raw("PR merged", frm="Forge <noreply@forge.example>", msgid="<io3@x.org>"))
    _, lunch = await _ingest(user, raw("Lunch?", frm="Bob <bob@example.org>", msgid="<io4@x.org>"))
    for cid, lid in ((bill, fin), (rcpt, fin), (pr, gh)):
        r = await client.post(f"{API}/conversations/actions", json={"ids": [cid], "action": "add_labels", "label_ids": [lid]})
        assert r.status_code == 200, r.text
    lay = {"id": "one", "name": "One page", "tabs": [
        {"id": "inbox", "name": "Inbox", "indicators": ["unread", "all"], "sections": [
            {"id": "fin", "title": "Financial", "base": f"label:{fin}", "limit": 10, "inbox_only": inbox_only},
            {"id": "rest", "title": "Everything else", "base": "inbox", "limit": None, "exclude_above": True}]},
        {"id": "everything", "name": "All mail", "indicators": ["all"], "sections": [
            {"id": "all", "title": "All mail", "base": "all"}]},
        {"id": "gh", "name": "Github", "claims": True, "indicators": ["all"], "sections": [
            {"id": "gh", "title": "Github", "base": f"label:{gh}", "inbox_only": inbox_only}]},
    ]}
    r = await client.put(f"{API}/layouts", json={"layouts": [lay], "active": "one"})
    assert r.status_code == 200, r.text
    return {"fin": fin, "gh": gh, "bill": bill, "rcpt": rcpt, "pr": pr, "lunch": lunch}


async def _ids(client, tab, sec):
    r = await client.get(f"{API}/conversations", params={"layout": "one", "tab": tab, "sec": sec})
    assert r.status_code == 200, r.text
    return {c["id"] for c in r.json()["items"]}


async def _counts(client):
    r = await client.get(f"{API}/layouts/one/counts", params={"detail": "true"})
    assert r.status_code == 200, r.text
    return r.json()["tabs"]


async def _archive(client, cid):
    r = await client.post(f"{API}/conversations/actions", json={"ids": [cid], "action": "archive"})
    assert r.status_code == 200 and r.json()["affected"] == 1, r.text


async def test_inbox_only_section_drops_archived_mail(client, user, raw):
    s = await _setup(client, user, raw)
    assert await _ids(client, "inbox", "fin") == {s["bill"], s["rcpt"]}
    assert (await _counts(client))["inbox"]["all"] == 3  # two bills and lunch (Github is claimed)

    await _archive(client, s["bill"])
    assert await _ids(client, "inbox", "fin") == {s["rcpt"]}
    assert await _ids(client, "inbox", "rest") == {s["lunch"]}
    counts = await _counts(client)
    assert counts["inbox"]["all"] == 2 and counts["inbox"]["unread"] == 2
    assert (await client.get(f"{API}/inbox/count")).json()["count"] == 2
    # Its "View all" search says the same.
    lays = (await client.get(f"{API}/layouts")).json()["layouts"]
    fin = next(x for x in lays if x["id"] == "one")["tabs"][0]["sections"][0]
    assert fin["inbox_only"] is True and fin["view_all_q"] == "label:Financial in:inbox"
    # The preview in the editor counts the same.
    r = await client.post(f"{API}/layouts/preview", json={"section": {"base": f"label:{s['fin']}", "inbox_only": True}})
    assert r.json()["total"] == 1
    r = await client.post(f"{API}/layouts/preview", json={"section": {"base": f"label:{s['fin']}", "inbox_only": False}})
    assert r.json()["total"] == 2


async def test_without_inbox_only_archived_mail_stays(client, user, raw):
    s = await _setup(client, user, raw, inbox_only=False)
    await _archive(client, s["bill"])
    assert await _ids(client, "inbox", "fin") == {s["bill"], s["rcpt"]}
    assert (await _counts(client))["inbox"]["all"] == 3


async def test_inbox_only_claims_take_only_inbox_mail(client, user, raw):
    s = await _setup(client, user, raw)
    # In the Inbox the Github tab claims the PR: no other tab lists it.
    assert s["pr"] not in await _ids(client, "everything", "all")
    assert await _ids(client, "gh", "gh") == {s["pr"]}
    # Archived, it is no longer Github's, so the All mail tab shows it again.
    await _archive(client, s["pr"])
    assert await _ids(client, "gh", "gh") == set()
    assert s["pr"] in await _ids(client, "everything", "all")
    counts = await _counts(client)
    assert counts["gh"]["all"] == 0 and counts["everything"]["all"] == 4


async def test_show_in_tab_puts_mail_back_in_the_inbox(client, user, raw):
    s = await _setup(client, user, raw)
    await _archive(client, s["pr"])
    r = await client.post(f"{API}/conversations/actions", json={"ids": [s["pr"]], "action": "show_in_tab", "tab": "gh"})
    assert r.status_code == 200, r.text
    assert await _ids(client, "gh", "gh") == {s["pr"]}


async def test_validate_keeps_inbox_only_off_an_inbox_base(client, user, raw):
    lab = (await client.post(f"{API}/labels", json={"name": "Later"})).json()["id"]
    lay = {"id": "v", "name": "V", "tabs": [{"name": "T", "sections": [
        {"title": "A", "base": "inbox", "inbox_only": True},
        {"title": "B", "base": f"label:{lab}", "inbox_only": True},
        {"title": "C", "base": "starred"}]}]}
    r = await client.put(f"{API}/layouts", json={"layouts": [lay]})
    assert r.status_code == 200, r.text
    secs = next(x for x in r.json()["layouts"] if x["id"] == "v")["tabs"][0]["sections"]
    assert [x["inbox_only"] for x in secs] == [False, True, False]


def _migration():
    path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "b7d4e1a9c3f6_layout_inbox_only_sections.py"
    spec = importlib.util.spec_from_file_location("mig_inbox_only", path)
    assert spec and spec.loader
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    return mig


def _owner_layouts():
    """Two layouts as a user set them up (ids as in the settings)."""
    def sec(id, base, **kw):
        return {"id": id, "title": id, "base": base, "limit": None, "exclude_above": True, **kw}
    return [
        {"id": "priority-later", "name": "Priority", "tabs": [
            {"id": "inbox", "name": "Inbox", "sections": [
                sec("important", "inbox", filter="important"), sec("unread", "inbox", filter="unread"), sec("rest", "inbox")]},
            {"id": "financial", "name": "Financial", "claims": True, "sections": [sec("financial", "label:28")]},
            {"id": "later", "name": "Later", "sections": [sec("later", "label:19")]},
            {"id": "github", "name": "Github", "claims": True, "sections": [sec("github", "label:20")]},
            {"id": "purchases", "name": "Purchases", "claims": True, "sections": [sec("p", "inbox", category="purchases")]},
            {"id": "promotions", "name": "Promotions", "claims": True, "sections": [sec("p", "inbox", category="promotions")]},
        ]},
        {"id": "single-page", "name": "My Personalized View (One Page)", "tabs": [
            {"id": "inbox", "name": "Inbox", "sections": [
                sec("important", "inbox", filter="important"), sec("financial", "label:28"),
                sec("purchases", "inbox", category="purchases"), sec("promotions", "inbox", category="promotions"),
                sec("rest", "inbox")]},
            {"id": "later", "name": "Later", "sections": [sec("later", "label:19")]},
            {"id": "github", "name": "Github Mail", "claims": True, "sections": [sec("github", "label:20")]},
        ]},
    ]


def test_migration_rule():
    mig = _migration()
    new, n = mig.migrate_inbox_only(_owner_layouts())
    assert n == 4  # Financial in both layouts, Github in both; Later stays off
    on = {(lay["id"], t["id"], s["id"]) for lay in new for t in lay["tabs"] for s in t["sections"] if s.get("inbox_only")}
    assert on == {("priority-later", "financial", "financial"), ("priority-later", "github", "github"),
                  ("single-page", "inbox", "financial"), ("single-page", "github", "github")}
    later = [s for lay in new for t in lay["tabs"] if t["id"] == "later" for s in t["sections"]]
    assert later and all("inbox_only" not in s for s in later)
    # Again: nothing changes. A section the user turned off stays off.
    again, n2 = mig.migrate_inbox_only(new)
    assert n2 == 0 and again == new
    new[1]["tabs"][0]["sections"][1]["inbox_only"] = False
    _, n3 = mig.migrate_inbox_only(new)
    assert n3 == 0
    assert mig.migrate_inbox_only(None) == (None, 0)


async def test_migration_runs_on_the_database(user):
    mig = _migration()
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        u.settings = {**(u.settings or {}), "inbox_layouts": _owner_layouts()}
        await db.commit()
    async with sessionmaker()() as db:
        conn = await db.connection()
        first = await conn.run_sync(mig.mark_inbox_only)
        second = await conn.run_sync(mig.mark_inbox_only)
        await db.commit()
    # Other test users may have layouts too; this user's four sections are among the first run's.
    assert first >= 4 and second == 0
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        lays = u.settings["inbox_layouts"]
        assert lays[1]["tabs"][0]["sections"][1]["inbox_only"] is True
        assert "inbox_only" not in lays[1]["tabs"][1]["sections"][0]
