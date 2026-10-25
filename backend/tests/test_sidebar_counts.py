"""Every sidebar number equals the length of the list it opens (core/sidebar.py, GET /counts).

3 Oct 2026: the sidebar said Drafts 4 over an empty Drafts list; all four drafts were in Trash.
The mailbox here has mail in the Inbox, drafts in Trash and a live draft, Spam, a snoozed and a
muted conversation, a user label (one of its conversations in Trash), a conversation that waits
for a reply, local-only mail and a layout with tabs. For the signed-in user and for a restricted
token (no local-only mail), each view's total is the number of conversations its list pages
through, and its unread the number of those rows with unread mail; GET /labels and GET /me carry
the same numbers."""

import asyncio
from datetime import UTC, datetime, timedelta

from helpers.audit_fixes import _conn, _ingest, _token
from helpers.security_send_path import _ident
from sqlalchemy import update

from mailr.db import session_scope
from mailr.models import Conversation, Message


async def _list(c, view: str, q: str = "") -> list[dict]:
    items, cursor = [], None
    while True:
        params = {"view": view, "limit": 200, **({"q": q} if q else {}), **({"cursor": cursor} if cursor else {})}
        r = await c.get("/api/v1/conversations", params=params)
        assert r.status_code == 200, (view, r.text)
        body = r.json()
        items += body["items"]
        cursor = body["next_cursor"]
        if not cursor:
            return items


async def _seed(client, user, raw):
    normal = await _conn(user.id)
    local = await _conn(user.id, "local_only")
    ids = {}
    for i in range(4):
        _, ids[f"inbox{i}"], _ = await _ingest(user, raw(f"Inbox mail {i}", msgid=f"<sb-in{i}@x.org>"), normal)
    _, ids["local"], _ = await _ingest(user, raw("University record", msgid="<sb-local@x.org>"), local)
    for name in ("spam", "trash", "snooze", "mute", "labelled", "labelled_trash", "waiting"):
        _, ids[name], _ = await _ingest(user, raw(f"Mail {name}", msgid=f"<sb-{name}@x.org>"), normal)
    lab = (await client.post("/api/v1/labels", json={"name": "Projects"})).json()["id"]
    act = client.post
    assert (await act("/api/v1/conversations/actions", json={"ids": [ids["spam"]], "action": "spam"})).status_code == 200
    assert (await act("/api/v1/conversations/actions", json={"ids": [ids["trash"]], "action": "trash"})).status_code == 200
    until = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    assert (await act("/api/v1/conversations/actions", json={"ids": [ids["snooze"]], "action": "snooze",
                                                             "until": until})).status_code == 200
    assert (await act("/api/v1/conversations/actions", json={"ids": [ids["mute"]], "action": "mute"})).status_code == 200
    for k in ("labelled", "labelled_trash"):
        await act("/api/v1/conversations/actions", json={"ids": [ids[k]], "action": "add_labels", "label_ids": [lab]})
    await act("/api/v1/conversations/actions", json={"ids": [ids["labelled_trash"]], "action": "trash"})
    await act("/api/v1/conversations/actions", json={"ids": [ids["inbox0"]], "action": "read"})
    # Drafts: one live, two in Trash (the reported case).
    await _ident(user, known=())
    drafts = []
    for i in range(3):
        r = await client.post("/api/v1/drafts", json={"payload": {"to": [{"email": "a@else.org"}],
                                                                  "subject": f"Draft {i}", "text": "x"}})
        assert r.status_code == 200, r.text
        drafts.append(r.json()["draft"]["conversation_id"])
    assert (await act("/api/v1/conversations/actions", json={"ids": drafts[1:], "action": "trash"})).status_code == 200
    # A conversation waiting for a reply (follow-up reminder set).
    async with session_scope() as db:
        await db.execute(update(Conversation).where(Conversation.id == ids["waiting"]).values(
            follow_up_at=datetime.now(UTC) + timedelta(days=3), follow_up_since=datetime.now(UTC)))
        await db.execute(update(Message).where(Message.conversation_id == ids["waiting"]).values(sent_by_me=True))
    # Layout tabs: the Default layout with categories.
    await client.patch("/api/v1/me/settings", json={"inbox_type": "default"})
    return ids, lab


async def _check(c, label_id: int, who: str):
    counts = (await c.get("/api/v1/counts")).json()
    views = counts["views"]
    assert f"label:{label_id}" in views
    for view, n in views.items():
        if view == "scheduled":  # the outbox list, not conversations
            rows = (await c.get("/api/v1/outbox", params={"scheduled": "true"})).json()
            assert n == {"unread": 0, "total": len(rows)}, (who, n, len(rows))
            continue
        rows = await _list(c, view)
        assert n["total"] == len(rows), (who, view, n, len(rows))
        assert n["unread"] == sum(1 for r in rows if r["unread_count"] > 0), (who, view, n)
    waiting = await _list(c, "all", "is:follow_up")
    assert counts["waiting"] == len(waiting), (who, counts["waiting"], len(waiting))
    # GET /labels and GET /me carry the same numbers.
    labels = (await c.get("/api/v1/labels")).json()
    roles = {"inbox": "inbox", "sent": "sent", "drafts": "drafts", "junk": "spam", "trash": "trash", "archive": "archive"}
    for lb in labels:
        v = views[roles.get(lb["role"]) or f"label:{lb['id']}"]
        # Drafts has always shown its total as the number (unread = total there).
        unread = v["total"] if lb["role"] == "drafts" else v["unread"]
        assert (lb["unread"], lb["total"]) == (unread, v["total"]), (who, lb["name"], v)
    me = (await c.get("/api/v1/me")).json()["counts"]
    assert me["drafts"] == views["drafts"]["total"] and me["snoozed"] == views["snoozed"]["total"]
    assert me["scheduled"] == views["scheduled"]["total"] and me["waiting"] == counts["waiting"]
    return counts


async def test_every_sidebar_number_is_its_list(client, user, raw):
    ids, lab = await _seed(client, user, raw)
    counts = await _check(client, lab, "session")
    v = counts["views"]
    # The reported case: drafts in Trash are not drafts.
    assert v["drafts"]["total"] == 1
    assert v["trash"]["total"] >= 4 and v["spam"]["total"] == 1 and v["snoozed"]["total"] == 1
    assert v[f"label:{lab}"]["total"] == 1  # its other conversation is in Trash
    inbox = {r["id"] for r in await _list(client, "inbox")}
    assert ids["snooze"] not in inbox and ids["mute"] not in inbox and ids["local"] in inbox
    assert counts["waiting"] == 1

    token = await _token(client, ["read"])  # not local-only: the university mail is not its
    tc = await _check(token, lab, "token")
    assert tc["views"]["inbox"]["total"] == v["inbox"]["total"] - 1


async def test_counts_follow_a_change_and_are_cached(client, user, raw):
    ids, _lab = await _seed(client, user, raw)
    before = (await client.get("/api/v1/counts")).json()
    again = (await client.get("/api/v1/counts")).json()
    assert again == before  # same change cursor: the cached answer
    await client.post("/api/v1/conversations/actions", json={"ids": [ids["inbox1"]], "action": "trash"})
    after = (await client.get("/api/v1/counts")).json()
    assert after["cursor"] > before["cursor"]
    assert after["views"]["inbox"]["total"] == before["views"]["inbox"]["total"] - 1
    assert after["views"]["trash"]["total"] == before["views"]["trash"]["total"] + 1


async def test_a_change_pushes_one_counts_event(client, user, raw):
    """The live stream gets a `counts` event after a change; a burst of changes sends one."""
    from mailr.core import events

    ids, _lab = await _seed(client, user, raw)
    for i in (1, 2, 3):
        await client.post("/api/v1/conversations/actions", json={"ids": [ids[f"inbox{i}"]], "action": "archive"})
    await asyncio.sleep(1.5)  # the listener's own notifications for these (when it runs) pass first
    q = events.hub.subscribe(user.id)
    try:
        for _ in (1, 2, 3):
            events.hub._dispatch(None, 0, "mailr_changes", f'{{"u": {user.id}, "type": "changes", "entity": "conversation"}}')
        got = []
        deadline = asyncio.get_running_loop().time() + 5
        while asyncio.get_running_loop().time() < deadline:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=0.5)
            except TimeoutError:
                if got:
                    break
                continue
            if ev.get("type") == "counts":
                got.append(ev)
        assert len(got) == 1, got
        expect = (await client.get("/api/v1/counts")).json()
        assert got[0]["views"] == expect["views"]
    finally:
        events.hub.unsubscribe(user.id, q)


async def test_a_steady_stream_of_changes_is_counted_at_a_pace_set_by_the_cost(monkeypatch):
    """An import's changes arrive every few tenths of a second: the numbers are computed at most
    once per COUNTS_SPACING times their cost, not once per COUNTS_DELAY, and the last change is
    still followed by a count."""
    from mailr.core import events

    monkeypatch.setattr(events, "COUNTS_DELAY", 0.05)
    monkeypatch.setattr(events, "COUNTS_SPACING", 4.0)
    computed: list[float] = []
    loop = asyncio.get_running_loop()

    async def slow_counts(uid):
        computed.append(loop.time())
        await asyncio.sleep(0.1)  # a 100 ms recount: at most one per 0.4 s
        return {"views": {"inbox": {"unread": len(computed), "total": 0}}}

    monkeypatch.setattr(events, "sidebar_counts_for", slow_counts)
    hub = events.Hub()
    uid = 987654321
    q = hub.subscribe(uid)
    change = f'{{"u": {uid}, "type": "changes", "entity": "conversation"}}'
    t0 = loop.time()
    while loop.time() - t0 < 2.0:
        hub._dispatch(None, 0, "mailr_changes", change)
        await asyncio.sleep(0.03)
    last_change = loop.time()
    await asyncio.sleep(1.0)
    # Without the spacing: one recount per 0.15 s (delay + cost), about 13 in 2 s.
    assert 3 <= len(computed) <= 7, computed
    assert all(b - a >= 0.39 for a, b in zip(computed, computed[1:], strict=False)), computed
    assert computed[-1] >= last_change - 0.05  # the burst's end was counted
    got = []
    while not q.empty():
        got.append(q.get_nowait())
    assert [e["type"] for e in got if e["type"] == "counts"] and got[-1]["views"]["inbox"]["unread"] == len(computed)
