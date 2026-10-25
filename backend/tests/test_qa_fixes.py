"""Regression tests for the frontend-QA round (search negation, label colours, scheduled sends,
bulk actions on a whole list, mirror-mode placement and folder ops, Gmail filter import,
plain-text conversion, References, avatars)."""

import types
import uuid
from datetime import UTC, datetime, timedelta

from helpers.connectors import (
    create_connector,
    imap,
    make_upstream_account,
    msg,
    run_jobs,
    run_sync,
)
from sqlalchemy import select

from mailr.core.ingest import IngestOptions, ingest_raw
from mailr.core.store import user_store
from mailr.db import session_scope, sessionmaker
from mailr.models import Connector, Identity, Job, Label, Message, User


async def _ingest(user, data, **kw):
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        store = await user_store(db, u)
        res = await ingest_raw(db, store, data, IngestOptions(**kw))
        await db.commit()
        return res.message.id, res.message.conversation_id


async def test_negated_raw_operators(client, user, raw):
    lb = (await client.post("/api/v1/labels", json={"name": "Projects"})).json()
    _, c1 = await _ingest(user, raw("Labelled one"))
    _, c2 = await _ingest(user, raw("Plain one"))
    await client.post("/api/v1/conversations/actions", json={"ids": [c1], "action": "add_labels", "label_ids": [lb["id"]]})
    for q, want in (("has:userlabels", {c1}), ("has:nouserlabels", {c2}), ("-has:userlabels", {c2}),
                    ("-filename:pdf one", {c1, c2}), ("-(has:userlabels) one", {c2})):
        r = await client.get("/api/v1/conversations", params={"view": "all", "q": q})
        assert r.status_code == 200, (q, r.text)
        assert {c["id"] for c in r.json()["items"]} == want, q


async def _sending_identity(user):
    async with session_scope() as db:
        c = Connector(user_id=user.id, kind="imap", name="acct", mode="mirror", config={"smtp": {"host": "x"}},
                      state={}, enabled=False)
        db.add(c)
        await db.flush()
        db.add(Identity(user_id=user.id, email=f"me-{uuid.uuid4().hex[:6]}@example.com", name="Me",
                        connector_id=c.id, is_default=True))


async def test_scheduled_new_message_listed_and_send_now(client, user):
    await _sending_identity(user)
    later = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    r = await client.post("/api/v1/send", json={"payload": {"to": [{"email": "x@y.org", "name": ""}],
                                                            "subject": "Tomorrow", "text": "hi"}, "send_at": later})
    assert r.status_code == 200, r.text
    oid = r.json()["outbox"]["id"]
    sched = (await client.get("/api/v1/outbox", params={"scheduled": "true"})).json()
    assert [o["id"] for o in sched] == [oid] and sched[0]["subject"] == "Tomorrow"
    assert (await client.get("/api/v1/me")).json()["counts"]["scheduled"] == 1
    r = await client.post(f"/api/v1/outbox/{oid}/send-now")
    assert r.status_code == 200, r.text
    assert datetime.fromisoformat(r.json()["outbox"]["send_at"]) <= datetime.now(UTC) + timedelta(seconds=2)
    assert (await client.get("/api/v1/outbox", params={"scheduled": "true"})).json() == []
    async with session_scope() as db:
        jobs = (await db.execute(select(Job).where(Job.kind == "outbox.send", Job.status == "queued"))).scalars().all()
        assert any(j.payload.get("outbox_id") == oid and j.run_at <= datetime.now(UTC) + timedelta(seconds=2) for j in jobs)
    # Cancelled rows are no longer "scheduled"; a second send-now is a 409.
    await client.post(f"/api/v1/outbox/{oid}/cancel")
    assert (await client.post(f"/api/v1/outbox/{oid}/send-now")).status_code == 409


async def test_bulk_action_by_query(client, user, raw, monkeypatch):
    ids = [(await _ingest(user, raw(f"Bulk {i}")))[1] for i in range(5)]
    # Small lists run immediately and are undoable.
    r = await client.post("/api/v1/conversations/actions",
                          json={"query": {"view": "inbox", "q": "subject:bulk"}, "action": "read"})
    assert r.status_code == 200 and r.json()["affected"] == 5
    # Large lists become a job over a snapshot of the matching ids.
    import mailr.api.mail_write as mw

    monkeypatch.setattr(mw, "BULK_INLINE_MAX", 2)
    r = await client.post("/api/v1/conversations/actions",
                          json={"query": {"view": "inbox", "q": "subject:bulk"}, "action": "archive"})
    assert r.status_code == 202, r.text
    job, total = r.json()["job"], r.json()["total"]
    assert total == 5
    import mailr.core.tasks as tasks

    async with session_scope() as db:
        j = await db.get(Job, job)
        payload = dict(j.payload)
        j.status = "running"
    await tasks.bulk_conversations(payload, types.SimpleNamespace(job_id=job, user_id=user.id))
    inbox = (await client.get("/api/v1/conversations", params={"view": "inbox", "q": "subject:bulk"})).json()
    assert inbox["items"] == []
    st = (await client.get(f"/api/v1/bulk/{job}")).json()
    assert st["done"] == 5 and st["total"] == 5
    assert set(ids)


async def test_gmail_filter_import_forward_and_category(client, user):
    xml = """<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns='http://www.w3.org/2005/Atom' xmlns:apps='http://schemas.google.com/apps/2006'>
 <entry><apps:property name='from' value='shop@store.example'/>
  <apps:property name='smartLabelToApply' value='^smartlabel_receipt'/></entry>
 <entry><apps:property name='to' value='team@example.org'/>
  <apps:property name='forwardTo' value='Me@Elsewhere.org'/></entry>
</feed>"""
    r = await client.post("/api/v1/rules/import-gmail", files={"file": ("mailFilters.xml", xml, "application/xml")})
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 2
    assert r.json()["forwarding_needs_verification"] == ["me@elsewhere.org"]
    rules = (await client.get("/api/v1/rules")).json()
    by_q = {x["query"]: x for x in rules}
    assert by_q["from:shop@store.example"]["actions"] == {"category": "purchases"}
    assert "forward_to" not in by_q["to:team@example.org"]["actions"]
    assert "me@elsewhere.org" in by_q["to:team@example.org"]["name"]


def test_plain_text_has_no_markdown_escapes():
    from mailr.core.compose import build_message

    m = build_message({"subject": "x", "html": "<div>Hi</div><div>-- <br>Rowan<br>1. item [a]</div>",
                       "to": [{"email": "a@b.org", "name": ""}]}, from_addr={"email": "me@x.org", "name": "Me"})
    text = m.get_body(("plain",)).get_content()
    assert "\\" not in text
    assert "\n-- \nRowan\n" in text


def test_references_include_parent():
    import email
    import email.policy

    from mailr.core.compose import build_message

    refs = [f"<r{i}.{'a' * 30}@example.org>" for i in range(8)]
    m = build_message({"subject": "Re: x", "text": "hi", "to": [{"email": "a@b.org", "name": ""}]},
                      from_addr={"email": "me@x.org", "name": "Me"}, in_reply_to="<parent@example.org>",
                      references=refs)
    parsed = email.message_from_bytes(m.as_bytes(), policy=email.policy.default)
    assert str(parsed["References"]).split()[-1] == "<parent@example.org>"
    assert str(parsed["In-Reply-To"]) == "<parent@example.org>"


async def test_avatars_upload_opt_in_and_guard(client, user, monkeypatch):
    import mailr.api.avatar_routes as av

    png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
           b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")
    # Off by default: no remote lookups, 404.
    called = []

    async def fake_lookup(email, egress="", **kw):
        called.append(email)
        return png, "image/png"

    monkeypatch.setattr(av, "lookup_remote", fake_lookup)
    assert (await client.get("/api/v1/avatars/alice@example.org")).status_code == 404
    assert called == []
    # A picture the user set wins, and needs no opt-in.
    r = await client.put("/api/v1/people/alice@example.org/avatar", files={"file": ("a.png", png, "image/png")})
    assert r.status_code == 200, r.text
    r = await client.get("/api/v1/avatars/alice@example.org")
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    assert "sandbox" in r.headers["content-security-policy"]
    # Uploads must be real images; SVG uploads are refused.
    bad = await client.put("/api/v1/people/bob@example.org/avatar",
                           files={"file": ("x.svg", b"<svg onload='x()'/>", "image/svg+xml")})
    assert bad.status_code == 400
    # Opt in: remote lookup is used.
    await client.patch("/api/v1/me/settings", json={"sender_pictures": True})
    r = await client.get("/api/v1/avatars/carol@example.org")
    assert r.status_code == 200 and called == ["carol@example.org"]
    # Unsafe SVG content and internal targets are never fetched/served.
    assert av._sniff(b"<svg xmlns='http://www.w3.org/2000/svg'><script>x</script></svg>", "image/svg+xml") is None
    assert av._sniff(b"<svg xmlns='http://www.w3.org/2000/svg'><circle r='4'/></svg>", "image/svg+xml") == "image/svg+xml"
    monkeypatch.delenv("MAILR_OUTBOUND_ALLOW", raising=False)
    from mailr.config import get_settings

    get_settings.cache_clear()
    try:
        assert await av._fetch("http://169.254.169.254/latest/meta-data") is None
    finally:
        get_settings.cache_clear()


# ---------------- mirror mode ----------------


async def _upstream_folders(email, pw, message_id):
    c = imap(email, pw)
    try:
        out = []
        for _, _, name in c.list_folders():
            sel = c.select_folder(name, readonly=True)
            if not sel.get(b"EXISTS"):
                continue
            # FETCH, not SEARCH HEADER: Stalwart indexes headers for search asynchronously.
            data = c.fetch(c.search(["ALL"]), [b"BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)]"])
            if any(message_id.encode() in d.get(b"BODY[HEADER.FIELDS (MESSAGE-ID)]", b"") for d in data.values()):
                out.append(name)
        return sorted(out)
    finally:
        c.logout()


async def test_mirror_archive_undo_out_of_order(client, user):
    """Archive then Undo, with the write-back jobs running in the opposite order: upstream must
    still end up matching mailr (Inbox), and the next sync must not re-add Archive."""
    email, pw = make_upstream_account()
    mid = f"<undo-{uuid.uuid4().hex}@x.org>"
    c = imap(email, pw)
    c.append("INBOX", msg("Undo me", msgid=mid))
    c.logout()
    conn = await create_connector(client, email, pw, mode="mirror")
    await run_sync(conn["id"])
    cid = (await client.get("/api/v1/conversations", params={"view": "inbox"})).json()["items"][0]["id"]
    r = await client.post("/api/v1/conversations/actions", json={"ids": [cid], "action": "archive"})
    undo = r.json()["undo"]
    await client.post("/api/v1/conversations/actions",
                      json={"ids": undo["ids"], "action": undo["action"], "labels": undo["labels"]})
    # Run the queued write-backs newest first (the race QA hit).
    import mailr.connectors.tasks  # noqa: F401
    from mailr.core.jobs import _HANDLERS

    async with session_scope() as db:
        jobs = (await db.execute(select(Job).where(Job.kind == "connector.write_back", Job.user_id == user.id,
                                                   Job.status == "queued").order_by(Job.id.desc()))).scalars().all()
        payloads = [j.payload for j in jobs]
        for j in jobs:
            j.status = "done"
    assert len(payloads) == 2
    for pl in payloads:
        await _HANDLERS["connector.write_back"](pl, None)
    assert await _upstream_folders(email, pw, mid) == ["INBOX"]
    await run_sync(conn["id"])
    labels = {lb["id"]: lb for lb in (await client.get("/api/v1/labels")).json()}
    conv = (await client.get(f"/api/v1/conversations/{cid}")).json()["conversation"]
    assert sorted(labels[i]["role"] or labels[i]["name"] for i in conv["label_ids"]) == ["inbox"]


async def test_mirror_label_rename_and_delete_propagate(client, user):
    email, pw = make_upstream_account()
    only_mid = f"<only-{uuid.uuid4().hex}@x.org>"
    both_mid = f"<both-{uuid.uuid4().hex}@x.org>"
    c = imap(email, pw)
    c.create_folder("Work")
    c.append("Work", msg("Only in Work", msgid=only_mid))
    c.append("INBOX", msg("In both", msgid=both_mid))
    c.append("Work", msg("In both", msgid=both_mid))
    c.logout()
    conn = await create_connector(client, email, pw, mode="mirror")
    await run_sync(conn["id"])
    work = next(lb for lb in (await client.get("/api/v1/labels")).json() if lb["name"] == "Work")

    r = await client.patch(f"/api/v1/labels/{work['id']}", json={"name": "Team"})
    assert r.status_code == 200, r.text
    assert await run_jobs("connector.folder_op", user.id) == 1
    c = imap(email, pw)
    names = [f[2] for f in c.list_folders()]
    c.logout()
    assert "Team" in names and "Work" not in names
    await run_sync(conn["id"])
    names_local = [lb["name"] for lb in (await client.get("/api/v1/labels")).json()]
    assert "Team" in names_local and "Work" not in names_local

    team = next(lb for lb in (await client.get("/api/v1/labels")).json() if lb["name"] == "Team")
    assert (await client.delete(f"/api/v1/labels/{team['id']}")).status_code == 200
    assert await run_jobs("connector.folder_op", user.id) == 1
    c = imap(email, pw)
    names = [f[2] for f in c.list_folders()]
    c.logout()
    assert "Team" not in names
    # The message that lived only in the label is rescued upstream (mailr keeps it in Archive).
    assert await _upstream_folders(email, pw, only_mid) == ["Archive"]
    assert await _upstream_folders(email, pw, both_mid) == ["INBOX"]
    await run_sync(conn["id"])
    names_local = [lb["name"] for lb in (await client.get("/api/v1/labels")).json()]
    assert "Team" not in names_local
    async with session_scope() as db:
        n = (await db.execute(select(Message).where(Message.user_id == user.id, Message.deleted.is_(False)))).scalars().all()
        assert len(n) == 2
    assert Label


async def test_move_to_inbox_unsnoozes_and_mute_undo_restores(client, user, raw):
    _, c = await _ingest(user, raw("Snooze then inbox"))
    await client.post("/api/v1/conversations/actions", json={"ids": [c], "action": "snooze",
                                                           "until": "2099-01-01T08:00:00Z"})
    await client.post("/api/v1/conversations/actions", json={"ids": [c], "action": "inbox"})
    inbox = (await client.get("/api/v1/conversations", params={"view": "inbox"})).json()["items"]
    assert c in [x["id"] for x in inbox]
    assert (await client.get("/api/v1/conversations", params={"view": "snoozed"})).json()["items"] == []

    r = await client.post("/api/v1/conversations/actions", json={"ids": [c], "action": "mute"})
    undo = r.json()["undo"]
    assert undo["action"] == "unmute" and undo["labels"]
    await client.post("/api/v1/conversations/actions", json={"ids": undo["ids"], "action": "unmute",
                                                           "labels": undo["labels"]})
    inbox = (await client.get("/api/v1/conversations", params={"view": "inbox"})).json()["items"]
    conv = next(x for x in inbox if x["id"] == c)
    assert conv["muted"] is False


def test_calendar_alternative_recorded_as_hidden_attachment():
    from mailr.core.mime import parse

    raw = (b"From: a@x.org\r\nTo: b@y.org\r\nSubject: Meet\r\nMIME-Version: 1.0\r\n"
           b"Content-Type: multipart/alternative; boundary=BB\r\n\r\n--BB\r\nContent-Type: text/plain\r\n\r\nhi\r\n"
           b"--BB\r\nContent-Type: text/calendar; method=REQUEST\r\n\r\nBEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\nEND:VCALENDAR\r\n"
           b"--BB--\r\n")
    p = parse(raw)
    cal = [a for a in p.attachments if a["type"] == "text/calendar"]
    assert cal and cal[0]["inline"] and cal[0]["filename"] == "invite.ics"
    assert not [a for a in p.attachments if not a["inline"]]
