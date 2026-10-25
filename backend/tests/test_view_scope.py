# ACT-SURFACES: assistant
"""The whole list on screen for the assistant: the screen names
the list and its total, list_view pages through all of it, and archive / modify_conversations take a
scope with exceptions, gated as one card and run as a background job with Undo.

Covers agent.list_view."""

import pytest
from helpers.core_flow import _ingest
from sqlalchemy import select

from mailr.db import sessionmaker
from mailr.models import Conversation, PendingAction, User


async def _ctx(db, user_id, ui, untrusted=False):
    from mailr.agent import screen
    from mailr.agent.tools import ToolCtx
    from mailr.core.auth import ALL_SCOPES, Principal

    p = Principal(user=await db.get(User, user_id), scopes=set(ALL_SCOPES) - {"admin"})
    return ToolCtx(db=db, p=p, assistant=True, chat_session_id="vs1", ui=screen.sanitize(ui), untrusted=untrusted)


async def _five(user, raw, tag):
    ids = []
    for i in range(5):
        _, cid = await _ingest(user, raw(f"{tag} offer {i}", frm=f"Shop {i} <deals{i}@shop.example>"))
        ids.append(cid)
    return ids


async def test_the_screen_names_the_whole_list_and_list_view_pages_through_it(client, user, raw):
    from mailr.agent import screen
    from mailr.agent.tools import run_tool

    ids = await _five(user, raw, "Promo")
    ui = {"view": "inbox", "list_conversation_ids": ids[-2:],
          "list": {"view": "inbox", "q": "subject:promo", "total": 5, "name": "Inbox", "bogus": 1}}
    clean = screen.sanitize(ui)
    assert clean["list"] == {"view": "inbox", "q": "subject:promo", "total": 5, "name": "Inbox"}
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id, ui)
        text = await screen.describe_mail(db, ctx.p, ctx.ui)
        first = await run_tool(ctx, "list_view", {"limit": 3})
        second = await run_tool(ctx, "list_view", {"limit": 3, "cursor": first.data["next_cursor"]})
    assert "the first 2 of 5 conversations in the list" in text and "5 conversations in all" in text
    assert "Promo" not in text  # still by id only
    assert first.data["total"] == 5 and first.data["rows"]["untrusted"] is True
    got = [r["conversation_id"] for r in first.data["rows"]["content"] + second.data["rows"]["content"]]
    assert sorted(got) == sorted(ids) and second.data["next_cursor"] is None
    assert first.data["rows"]["content"][0]["from"].startswith("Shop")


async def test_archive_this_view_except_some_runs_one_background_job(client, user, raw):
    from mailr.agent.tools import run_tool

    ids = await _five(user, raw, "Sale")
    keep = ids[:2]
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id, {"view": "inbox", "list": {"view": "inbox", "q": "subject:sale", "total": 5}})
        res = await run_tool(ctx, "archive", {"scope": "screen", "except_ids": keep})
    assert res.data["conversations"] == 3 and res.data["left_alone"] == 2 and res.data["running_in_background"]
    import types

    from mailr.core import tasks
    from mailr.models import Job

    async with sessionmaker()() as db:
        job = (await db.execute(select(Job).where(Job.id == res.data["job"]))).scalar_one()
    await tasks.bulk_conversations(job.payload, types.SimpleNamespace(job_id=job.id, user_id=user.id))
    async with sessionmaker()() as db:
        convs = {c.id: c for c in (await db.execute(select(Conversation).where(Conversation.id.in_(ids)))).scalars()}
        from mailr.models import Label

        inbox = (await db.execute(select(Label.id).where(Label.user_id == user.id, Label.role == "inbox"))).scalar()
    for cid in ids:
        assert (inbox in (convs[cid].label_ids or [])) == (cid in keep), cid


async def test_trash_by_search_asks_once_for_the_exact_set(client, user, raw):
    from mailr.agent.tools import run_tool

    ids = await _five(user, raw, "Clearance")
    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id, {}, untrusted=True)
        res = await run_tool(ctx, "modify_conversations", {"action": "trash", "except_ids": [ids[0]],
                                                           "scope": {"view": "inbox", "q": "subject:clearance"}})
        assert res.data["status"] == "pending_approval"
        pa = await db.get(PendingAction, res.data["approval_id"])
    assert pa.payload["op"] == "cleanup" and pa.payload["action"] == "trash"
    assert sorted(pa.payload["conversation_ids"]) == sorted(ids[1:])
    assert pa.preview["title"] == "Move to Trash 4 conversations"


async def test_scope_errors(client, user, raw):
    from mailr.agent.tools import ToolError, run_tool

    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id, {})
        with pytest.raises(ToolError, match="no list is on screen"):
            await run_tool(ctx, "archive", {"scope": "screen"})
        with pytest.raises(ToolError, match="needs a search"):
            await run_tool(ctx, "archive", {"scope": {"view": "all"}})
        with pytest.raises(ToolError, match="not both"):
            await run_tool(ctx, "archive", {"scope": "screen", "conversation_ids": [1]})
        with pytest.raises(ToolError, match="no conversations"):
            await run_tool(ctx, "archive", {"scope": {"view": "inbox", "q": "subject:nothing-matches-this"}})


async def _two_tabs(client, user, raw):
    """A layout with two tabs: Main (Starred above Everything else) and News, which claims its mail."""
    ids = {}
    for name in ("star1", "star2", "plain1", "plain2", "news"):
        _, ids[name] = await _ingest(user, raw(f"Tabbed {name}", frm=f"{name} <{name}@tabs.example>"))
    async with sessionmaker()() as db:
        from sqlalchemy import update

        await db.execute(update(Conversation).where(Conversation.id.in_([ids["star1"], ids["star2"]])).values(starred=True))
        await db.commit()
    r = await client.put("/api/v1/layouts", json={"active": "two", "layouts": [{"id": "two", "name": "Two", "tabs": [
        {"id": "main", "name": "Main", "icon": "inbox", "sections": [
            {"id": "starred", "title": "Starred", "base": "inbox", "filter": "starred", "limit": 10, "q": "subject:tabbed"},
            {"id": "rest", "title": "Everything else", "base": "inbox", "limit": None, "exclude_above": True,
             "q": "subject:tabbed"}]},
        {"id": "news", "name": "News", "icon": "tag", "claims": True, "sections": [
            {"id": "all", "title": "News", "base": "inbox", "q": "subject:news"}]},
    ]}]})
    assert r.status_code == 200, r.text
    return ids


async def test_the_screen_scope_is_the_whole_tab_or_the_section_shown_alone(client, user, raw):
    """"This view" on an inbox tab is every section of the tab (the union of the sections, as the tab shows
    them), or the one section shown alone; "my inbox" is the Inbox across all tabs. Each scope has an
    unambiguous name (bulk review scope, 20 Oct 2026)."""
    from mailr.agent import screen, view_tools

    ids = await _two_tabs(client, user, raw)
    tab_ids = sorted(ids[k] for k in ("star1", "star2", "plain1", "plain2"))
    # What each section lists, and what the whole tab lists (the list endpoint with layout and tab alone).
    per_sec = set()
    for sec in ("starred", "rest"):
        got = (await client.get("/api/v1/conversations", params={"layout": "two", "tab": "main", "sec": sec})).json()
        per_sec |= {c["id"] for c in got["items"]}
    whole = (await client.get("/api/v1/conversations", params={"layout": "two", "tab": "main"})).json()
    assert sorted(c["id"] for c in whole["items"]) == sorted(per_sec) == tab_ids and whole["total"] == 4
    assert (await client.get("/api/v1/conversations", params={"layout": "two", "tab": "nope"})).status_code == 404

    async with sessionmaker()() as db:
        ctx = await _ctx(db, user.id, {"view": "inbox", "list_conversation_ids": tab_ids[:2], "list": {
            "view": "inbox", "layout": "two", "tab": "main", "total": 4, "name": "Main tab (all sections)"}})
        got, lq = await view_tools.resolve_scope(ctx, "screen")
        assert sorted(got) == tab_ids and "sec" not in lq
        assert await view_tools.scope_name(db, ctx.p, lq) == "Main tab (all sections)"
        line = await screen.describe_mail(db, ctx.p, ctx.ui)
        assert "'Main tab (all sections)': 4 conversations in all" in line
        assert "2 sections (Starred, Everything else)" in line and "means the whole tab, every section" in line
        assert "across all 2 tabs" in line

        # "Show only this section": the screen is that section.
        ctx = await _ctx(db, user.id, {"view": "inbox", "list": {
            "view": "inbox", "layout": "two", "tab": "main", "sec": "starred", "total": 2}})
        got, lq = await view_tools.resolve_scope(ctx, "screen")
        assert sorted(got) == sorted([ids["star1"], ids["star2"]])
        assert await view_tools.scope_name(db, ctx.p, lq) == "Main: Starred"
        assert "shown alone" in await screen.describe_mail(db, ctx.p, ctx.ui)

        # The Inbox folder (no layout) is every tab, the claimed News mail included.
        got, lq = await view_tools.resolve_scope(ctx, {"view": "inbox", "q": "subject:tabbed"})
        assert sorted(got) == sorted(ids.values())
        assert await view_tools.scope_name(db, ctx.p, lq) == 'Inbox (all tabs) matching "subject:tabbed"'
        # A tab of one section is named by the tab alone.
        assert view_tools.describe_query({"view": "inbox", "layout": "two", "tab": "news"},
                                         {"inbox_type": "layout:two", "inbox_layouts": (await db.get(User, user.id)).settings["inbox_layouts"]}) == "News tab"


def test_the_inbox_without_tabs_is_just_inbox():
    from mailr.agent import (
        tools,  # noqa: F401  (registers the tools before view_tools)
        view_tools,
    )

    assert view_tools.describe_query({"view": "inbox"}, {"inbox_type": "simple"}) == "Inbox"
    assert view_tools.describe_query({"view": "inbox"}, {"inbox_type": "default", "categories": ["primary", "promotions"]}) \
        == "Inbox (all tabs)"
    assert view_tools.describe_query({"view": "label:3", "name": "Receipts"}) == "Receipts"
