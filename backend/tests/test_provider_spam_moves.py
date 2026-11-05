"""Provider spam lands in Spam, never in the Inbox (3 Nov 2026).

A Gmail mirror account collects new mail from All Mail within seconds (IDLE, 0.40.29). Gmail can
move a message it already delivered to its Spam folder afterwards. The message then left All Mail
(which takes no label away in mailr) and appeared in [Gmail]/Spam, whose copy added Spam beside
the Inbox: the list showed it in the Inbox while the reader treated it as spam ("Not spam",
images blocked). Now:

- a copy of a message in its own mirror account's Spam folder moves it to Spam: Spam replaces the
  Inbox; the user's labels stay on it, as on any upstream move to Spam, and label views, tabs and
  counts leave it out (test_spam_labels.py);
- the user's own "Not spam" stands against a copy the provider still holds in Spam;
- "Not spam" on Gmail moves the message out of [Gmail]/Spam, which tells Gmail.
"""

from __future__ import annotations

from types import SimpleNamespace

from helpers.connectors import create_connector, imap, make_upstream_account, msg, run_sync
from sqlalchemy import select, text

from mailr.connectors.base import WriteBack
from mailr.connectors.gmail import ALL_MAIL, GmailConnector
from mailr.connectors.runner import RunnerContext
from mailr.db import sessionmaker
from mailr.models import Connector, Label, LabelDecision, Message

GM = "[Gmail]/All Mail"
SPAM = "[Gmail]/Spam"


async def _gmail_mirror(user) -> int:
    async with sessionmaker()() as db:
        c = Connector(user_id=user.id, kind="gmail", name="gmail-mirror", mode="mirror", enabled=True,
                      config={"email": "x@gmail.example", "imap": {"host": "localhost", "port": 1, "tls": "none"}},
                      state={"initial_done": True})
        db.add(c)
        await db.commit()
        return c.id


def _kw(cid: int, folder: str, uid: int, roles: list[str], uv: int = 12) -> dict:
    return {"roles": roles, "label_names": [], "source": "connector", "merge_labels": True,
            "upstream": {"connector": cid, "folder": folder, "uidvalidity": uv, "uid": uid,
                         "gm_msgid": "1880000000000000001"}}


async def _roles(user) -> dict[str, int]:
    async with sessionmaker()() as db:
        rows = (await db.execute(select(Label).where(Label.user_id == user.id))).scalars()
        return {(lb.role or lb.name): lb.id for lb in rows}


async def _msg(mid: int) -> Message:
    async with sessionmaker()() as db:
        return await db.get(Message, mid)


async def _ids(client, **params) -> set[int]:
    r = await client.get("/api/v1/conversations", params=params)
    assert r.status_code == 200, r.text
    return {c["id"] for c in r.json()["items"]}


async def _act(client, cid, action, **extra):
    r = await client.post("/api/v1/conversations/actions", json={"ids": [cid], "action": action, **extra})
    assert r.status_code == 200, r.text
    return r.json()


async def test_gmail_spam_after_delivery_replaces_the_inbox(client, user, raw):
    cid = await _gmail_mirror(user)
    ctx = RunnerContext(cid, user.id, {})
    data = raw("Scheduled maintenance", frm="Updates <updates@example.net>")
    first = await ctx.ingest(data, **_kw(cid, GM, 501, ["inbox"]))
    mid, conv = first["message_id"], first["conversation_id"]
    clients = (await client.post("/api/v1/labels", json={"name": "Clients"})).json()["id"]
    await _act(client, conv, "add_labels", label_ids=[clients])
    roles = await _roles(user)
    assert set((await _msg(mid)).label_ids) == {roles["inbox"], clients}

    await ctx.vanished(GM, 12, [501], ALL_MAIL)
    again = await ctx.ingest(data, **_kw(cid, SPAM, 77, ["junk"], uv=9))
    assert again["duplicate"] and again["message_id"] == mid

    m = await _msg(mid)
    assert set(m.label_ids) == {roles["junk"], clients}  # not in the Inbox; the label stays
    assert not m.spam_kept_labels
    assert conv not in await _ids(client, view="inbox")
    assert conv in await _ids(client, view="spam")
    assert conv not in await _ids(client, view=f"label:{clients}")
    # The reader's spam handling (images blocked, "Not spam") follows the same label.
    body = (await client.get(f"/api/v1/messages/{mid}/body")).json()
    assert body["junk"] is True
    async with sessionmaker()() as db:
        d = (await db.execute(select(LabelDecision).where(
            LabelDecision.message_id == mid, LabelDecision.kind == "spam"))).scalars().all()
    assert [(x.source, x.source_ref, x.value) for x in d] == [("provider", "gmail", True)]
    assert SPAM in d[0].reason

    # Not spam: back in the Inbox with the label, and Gmail is asked to move it out of Spam.
    await _act(client, conv, "not_spam")
    m = await _msg(mid)
    assert set(m.label_ids) == {roles["inbox"], clients}
    assert conv in await _ids(client, view=f"label:{clients}")
    async with sessionmaker()() as db:
        jobs = [r[0] for r in (await db.execute(text(
            "SELECT payload FROM jobs WHERE kind='connector.write_back' AND user_id=:u AND status='queued' "
            "ORDER BY id"), {"u": user.id})).all()]
    assert any("junk" in (j["change"].get("remove_labels") or []) and j["connector_id"] == cid for j in jobs)

    # A pass that still sees the Spam copy (the write-back has not run) leaves the user's verdict.
    await ctx.ingest(data, **_kw(cid, SPAM, 78, ["junk"], uv=9))
    assert set((await _msg(mid)).label_ids) == {roles["inbox"], clients}


async def test_mail_already_in_spam_is_left_as_it_is(client, user, raw):
    """Reported spam in mailr: Gmail's Spam copy (from mailr's own write-back) changes nothing."""
    cid = await _gmail_mirror(user)
    ctx = RunnerContext(cid, user.id, {})
    data = raw("Win a prize", frm="Promo <promo@example.net>")
    first = await ctx.ingest(data, **_kw(cid, GM, 601, ["inbox"]))
    await _act(client, first["conversation_id"], "spam")
    await ctx.ingest(data, **_kw(cid, SPAM, 90, ["junk"], uv=9))
    roles = await _roles(user)
    assert (await _msg(first["message_id"])).label_ids == [roles["junk"]]
    async with sessionmaker()() as db:
        n = (await db.execute(select(LabelDecision).where(
            LabelDecision.message_id == first["message_id"], LabelDecision.source == "provider"))).scalars().all()
    assert n == []


async def test_a_copy_from_another_account_never_moves_mail_to_spam(client, user, raw):
    """Another account's Spam folder says nothing about this account's delivery."""
    store = await _gmail_mirror(user)
    other = await _gmail_mirror(user)
    data = raw("Team lunch", frm="Carol <carol@example.org>")
    first = await RunnerContext(store, user.id, {}).ingest(data, **_kw(store, GM, 1, ["inbox"]))
    await RunnerContext(other, user.id, {}).ingest(data, **_kw(other, SPAM, 2, ["junk"], uv=9))
    roles = await _roles(user)
    assert (await _msg(first["message_id"])).label_ids == [roles["inbox"]]


async def test_imap_mirror_move_to_junk_upstream_leaves_the_inbox(client, user):
    """The same on a plain IMAP mirror account, through a real sync: the provider moved the
    message from INBOX to its Junk folder."""
    email, pw = make_upstream_account()
    c = imap(email, pw)
    c.append("INBOX", msg("Quarterly update", msgid="<provider-spam-1@example.org>"))
    c.logout()
    conn = await create_connector(client, email, pw, mode="mirror")
    await run_sync(conn["id"])
    conv = next(iter(await _ids(client, view="inbox")))
    clients = (await client.post("/api/v1/labels", json={"name": "Clients"})).json()["id"]
    await _act(client, conv, "add_labels", label_ids=[clients])
    async with sessionmaker()() as db:  # the write-back of the label is not under test
        await db.execute(text("UPDATE jobs SET status='done' WHERE user_id=:u AND status='queued'"), {"u": user.id})
        await db.commit()

    c = imap(email, pw)
    junk = next(f[2] for f in c.list_folders() if f[2].lower() in ("junk", "junk mail", "spam"))
    c.select_folder("INBOX")
    c.move(c.search(["ALL"]), junk)
    c.logout()
    await run_sync(conn["id"])
    roles = await _roles(user)
    async with sessionmaker()() as db:
        m = (await db.execute(select(Message).where(Message.conversation_id == conv))).scalar_one()
    assert set(m.label_ids) == {roles["junk"], clients}
    assert conv in await _ids(client, view="spam") and conv not in await _ids(client, view="inbox")
    assert conv not in await _ids(client, view=f"label:{clients}")


def _fake_gmail(monkeypatch, hits):
    calls: list[tuple[str, tuple]] = []

    class FakeClient:
        def search(self, criteria):
            calls.append(("search", (criteria,)))
            return hits.get(criteria[0], [])

        def __getattr__(self, name):
            return lambda *a: calls.append((name, a))

    class FakeSession:
        client = FakeClient()

        async def call(self, fn, *a, **kw):
            return fn(*a, **kw)

        async def select(self, folder, readonly=True):
            calls.append(("select", (folder,)))
            return {b"UIDVALIDITY": 1}

        def has(self, cap):
            return cap in ("MOVE", "UIDPLUS")

    conn = GmailConnector.__new__(GmailConnector)
    conn.row = SimpleNamespace(id=7, preset="gmail", config={"email": "a@gmail.example"}, mode="mirror", kind="gmail")
    session = FakeSession()

    async def _open():
        return session

    async def _close(hard=False):
        return None

    async def folders():
        return [("INBOX", (b"\\HasNoChildren",), "/"), (GM, (b"\\All",), "/"), (SPAM, (b"\\Junk",), "/"),
                ("[Gmail]/Trash", (b"\\Trash",), "/")]

    async def folder_plan():
        return [(GM, ALL_MAIL), (SPAM, ("role", "junk"))]

    async def _select_ref(r):
        return True

    for name, fn in (("_open", _open), ("_close", _close), ("folders", folders), ("folder_plan", folder_plan),
                     ("_select_ref", _select_ref)):
        monkeypatch.setattr(conn, name, fn, raising=False)
    return conn, calls


async def test_not_spam_moves_the_message_out_of_gmails_spam(monkeypatch):
    """Its only Gmail copy is in [Gmail]/Spam: Not spam finds it there by its Gmail id and moves it
    to the Inbox. Before, the write-back looked for an All Mail copy only and told Gmail nothing."""
    conn, calls = _fake_gmail(monkeypatch, {"X-GM-MSGID": [12129]})
    up = {"connector": 7, "folder": SPAM, "uidvalidity": 9, "uid": 12129, "gm_msgid": "1880000000000000001"}
    out = await conn.write_back([WriteBack(upstream=up, add_labels=["inbox", "Clients"], remove_labels=["junk"],
                                           message_id="<a@example.org>")])
    assert ("select", (SPAM,)) in calls
    assert ("move", ([12129], "INBOX")) in calls
    assert out[0].get("removed") is True and not out[0].get("folder")  # the Spam ref is gone


async def test_not_spam_of_mail_gmail_does_not_hold_in_spam_moves_nothing(monkeypatch):
    conn, calls = _fake_gmail(monkeypatch, {})
    up = {"connector": 7, "folder": GM, "uidvalidity": 12, "uid": 42}
    await conn.write_back([WriteBack(upstream=up, add_labels=["inbox"], remove_labels=["junk"],
                                     message_id="<b@example.org>")])
    assert not any(n == "move" for n, _ in calls)
    assert ("add_gmail_labels", ([42], ["\\Inbox"])) in calls
