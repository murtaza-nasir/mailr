"""Delete forever removes the message from everything mailr controls (core/purge.py): the row keeps
only a tombstone, what was made from the message goes, and its file leaves the mail export and the
backup target with its manifest line (core/backup.py remove_exported). Messages that vanished from
the store, and rows deleted before the purge existed, are scrubbed by the daily sweep, which holds
back when most of a user's mail looks deleted and never removes their export files: after a
restore of the mail store from an older snapshot, those files are the only copy left."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from helpers.connectors import run_jobs
from helpers.core_flow import _ingest
from sqlalchemy import select, text
from sqlalchemy.orm import undefer

from mailr.config import get_settings
from mailr.core import backup, purge
from mailr.db import session_scope
from mailr.models import (
    ChatMessage,
    ChatSession,
    Job,
    LabelDecision,
    Memory,
    Message,
    MessagePassage,
)

API = "/api/v1"
JOB = purge.JOB


@pytest.fixture
def export_dirs(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "data_dir", str(tmp_path / "data"))
    nas = tmp_path / "nas"
    nas.mkdir()
    (nas / backup.TARGET_MARKER).touch()
    monkeypatch.setattr(s, "backup_target", str(nas))
    monkeypatch.setattr(s, "mail_export", True)
    return nas


async def _delete_forever(client, conv_id: int, msg_id: int | None = None) -> None:
    if msg_id is None:
        r = await client.post(f"{API}/conversations/actions", json={"ids": [conv_id], "action": "delete_forever"})
    else:
        r = await client.post(f"{API}/messages/actions", json={"ids": [msg_id], "action": "delete_forever"})
    assert r.status_code == 200, r.text


async def _row(mid: int) -> Message:
    async with session_scope() as db:
        return (await db.execute(select(Message).options(undefer(Message.attached_text))
                                 .where(Message.id == mid))).scalar_one()


async def _export(uid: int) -> None:
    from mailr.core.tasks import export_user

    await export_user({"user_id": uid}, None)


def _files(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.glob("*/cur/*")}


async def test_delete_forever_leaves_a_tombstone_and_removes_what_was_made_from_it(client, user, raw, export_dirs):
    first, conv = await _ingest(user, raw("Harbour lease", body="The quay rent rises to 900 in May.",
                                          msgid="<lease-1@harbour.example>"))
    reply, conv2 = await _ingest(user, raw("Re: Harbour lease", frm="Dana Brook <dana@harbour.example>",
                                           msgid="<lease-2@harbour.example>", irt="<lease-1@harbour.example>"))
    assert conv == conv2
    sid = f"s-{uuid.uuid4().hex[:12]}"
    async with session_scope() as db:
        db.add(MessagePassage(user_id=user.id, message_id=first, conversation_id=conv, idx=0, start=0, end=20,
                              text="The quay rent rises", content_hash="x"))
        db.add(LabelDecision(user_id=user.id, message_id=first, kind="category", target="updates", value=True,
                             source="classifier", reason="mentions the quay rent", status="applied"))
        db.add(Memory(user_id=user.id, text="Quay rent is 900 from May", source="extracted", status="suggested",
                      refs=[{"message_id": first, "conversation_id": conv}]))
        db.add(Memory(user_id=user.id, text="Dana runs the harbour office", source="user", status="active",
                      refs=[{"message_id": first, "conversation_id": conv}, {"message_id": reply}]))
        db.add(ChatSession(id=sid, user_id=user.id, title="Lease"))
        await db.flush()
        db.add(ChatMessage(session_id=sid, role="assistant", content="The rent rises [1](mailr-cite:1).",
                           citations=[{"kind": "message", "message_id": first, "conversation_id": conv,
                                       "subject": "Harbour lease", "from": "Alice", "quote": "rises to 900",
                                       "url": "https://mail.example/mail/all/x?m=y&hl=rises%20to%20900"},
                                      {"kind": "message", "message_id": reply, "subject": "Re: Harbour lease",
                                       "quote": "kept", "url": None}]))
    summaries = Path(get_settings().data_dir) / "cache" / "summaries" / str(user.id)
    summaries.mkdir(parents=True)
    (summaries / f"{conv}.json").write_text("{}")
    replies = Path(get_settings().data_dir) / "cache" / "replies" / str(user.id)
    replies.mkdir(parents=True)
    (replies / f"{first}.json").write_text("{}")
    before = await _row(first)

    await _delete_forever(client, conv, first)
    async with session_scope() as db:
        job = (await db.execute(select(Job).where(Job.kind == JOB, Job.user_id == user.id))).scalar_one()
    assert job.payload == {"user_id": user.id, "message_ids": [first], "export": True}
    assert await run_jobs(JOB, user.id) == 1

    m = await _row(first)
    assert m.deleted and m.intel_state == "purged"
    assert (m.subject, m.norm_subject, m.snippet, m.body_text, m.from_name, m.from_email) == ("", "", "", "", "", "")
    assert m.to == [] and m.cc == [] and m.attachments == [] and m.attached_text is None and m.embedding is None
    # The tombstone: what threading, write-back and the export need.
    assert m.message_id_hdr == "<lease-1@harbour.example>" and m.blob_id == before.blob_id
    assert m.content_hash == before.content_hash and m.conversation_id == conv
    async with session_scope() as db:
        tsv = (await db.execute(text("SELECT tsv::text FROM messages WHERE id = :i"), {"i": first})).scalar_one()
        assert "quay" not in tsv and "harbour" not in tsv
        assert (await db.execute(select(MessagePassage).where(MessagePassage.message_id == first))).first() is None
        d = (await db.execute(select(LabelDecision).where(LabelDecision.message_id == first))).scalar_one()
        assert d.reason == "" and d.target == "updates"
        mems = {x.text: x for x in (await db.execute(select(Memory).where(Memory.user_id == user.id))).scalars()}
        assert "Quay rent is 900 from May" not in mems
        assert mems["Dana runs the harbour office"].refs == [{"message_id": reply}]
        cites = (await db.execute(select(ChatMessage.citations).where(ChatMessage.session_id == sid))).scalar_one()
    assert cites[0]["subject"] is None and cites[0]["quote"] is None and cites[0]["from"] is None
    assert "hl=" not in cites[0]["url"] and cites[0]["message_id"] == first
    assert cites[1]["quote"] == "kept"
    assert not (summaries / f"{conv}.json").exists() and not (replies / f"{first}.json").exists()

    # The other message is untouched, and a later reply to the deleted one still joins the conversation.
    assert (await _row(reply)).subject == "Re: Harbour lease"
    late, conv3 = await _ingest(user, raw("Re: Harbour lease", msgid="<lease-3@harbour.example>",
                                          irt="<lease-1@harbour.example>"))
    assert conv3 == conv

    # Again: nothing left to do, nothing fails.
    await purge.purge_job({"user_id": user.id, "message_ids": [first], "export": True}, None)
    assert (await _row(first)).intel_state == "purged"
    # A message that is not deleted is never scrubbed.
    await purge.purge_job({"user_id": user.id, "message_ids": [reply], "export": True}, None)
    assert (await _row(reply)).body_text


async def test_a_message_reported_as_spam_keeps_its_sender_address(client, user, raw):
    mid, conv = await _ingest(user, raw("Win a boat", frm="Prize Desk <desk@prizes.example>"))
    r = await client.post(f"{API}/conversations/actions", json={"ids": [conv], "action": "spam"})
    assert r.status_code == 200, r.text
    await _delete_forever(client, conv)
    await run_jobs(JOB, user.id)
    m = await _row(mid)
    # Spam sender learning counts reports whose mail is deleted (intel/spam.py spam_marks).
    assert m.from_email == "desk@prizes.example" and m.from_name == "" and m.subject == ""


async def test_the_export_file_leaves_the_export_the_manifest_and_the_backup_target(client, user, raw, export_dirs):
    gone, conv = await _ingest(user, raw("Old invoice", body="Invoice 4471"))
    kept, _c = await _ingest(user, raw("Field trip", body="Bring boots"))
    await _export(user.id)
    base, tgt = backup.export_root(user.id), backup.target_root(user.id)
    manifest = backup.exported_manifest(user.id)
    blob_gone, blob_kept = (await _row(gone)).blob_id, (await _row(kept)).blob_id
    assert set(manifest) == {blob_gone, blob_kept}
    assert _files(base) == _files(tgt) == set(manifest.values())

    await _delete_forever(client, conv)
    await run_jobs(JOB, user.id)
    assert set(backup.exported_manifest(user.id)) == {blob_kept}
    assert _files(base) == _files(tgt) == {manifest[blob_kept]}
    assert blob_gone not in (tgt / ".exported").read_text()
    assert not (base / backup.PURGED).exists()

    # Idempotent, and the next export does not write it again.
    await purge.purge_job({"user_id": user.id, "message_ids": [gone], "export": True}, None)
    await _export(user.id)
    assert set(backup.exported_manifest(user.id)) == {blob_kept}
    assert _files(base) == {manifest[blob_kept]}


async def test_an_unmounted_target_gets_the_removal_when_it_is_back(client, user, raw, export_dirs):
    mid, conv = await _ingest(user, raw("Locker code", body="Code 2291"))
    await _export(user.id)
    rel = backup.exported_manifest(user.id)[(await _row(mid)).blob_id]
    (export_dirs / backup.TARGET_MARKER).unlink()  # the share is not mounted

    await _delete_forever(client, conv)
    await run_jobs(JOB, user.id)
    base, tgt = backup.export_root(user.id), backup.target_root(user.id)
    assert not (base / rel).exists() and backup.exported_manifest(user.id) == {}
    assert (tgt / rel).exists()
    assert rel in (base / backup.PURGED).read_text()

    (export_dirs / backup.TARGET_MARKER).touch()
    await _export(user.id)  # each export run applies what the target missed
    assert not (tgt / rel).exists()
    assert (tgt / ".exported").read_text() == ""
    assert not (base / backup.PURGED).exists()


async def test_a_file_shared_with_a_live_message_stays(client, user, raw, export_dirs):
    a, conv = await _ingest(user, raw("Shared bytes"))
    b, _c = await _ingest(user, raw("Other"))
    await _export(user.id)
    blob = (await _row(a)).blob_id
    async with session_scope() as db:  # the same bytes stored twice
        await db.execute(text("UPDATE messages SET blob_id = :b WHERE id = :i"), {"b": blob, "i": b})
    await _delete_forever(client, conv)
    await run_jobs(JOB, user.id)
    assert blob in backup.exported_manifest(user.id)
    assert (await _row(a)).intel_state == "purged"


def test_remove_exported_touches_only_the_listed_file_of_that_blob(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "data_dir", str(tmp_path / "data"))
    monkeypatch.setattr(get_settings(), "backup_target", "")
    base = backup.export_root(5)
    (base / "Inbox" / "cur").mkdir(parents=True)
    for n in ("1.1.mailr:2,S", "2.2.mailr:2,", "3.3.mailr:2,"):
        (base / "Inbox" / "cur" / n).write_bytes(b"x")
    outside = tmp_path / "data" / "exports" / "keep.txt"
    outside.write_text("not mail")
    (base / ".exported").write_text("b1\tInbox/cur/1.1.mailr:2,S\nb2\tInbox/cur/2.2.mailr:2,\n"
                                    "b3\t../keep.txt\nlegacy\n")
    out = backup.remove_exported(5, {"b1", "b3", "legacy", "unknown"})
    assert out["removed"] == 1
    assert not (base / "Inbox" / "cur" / "1.1.mailr:2,S").exists()
    assert (base / "Inbox" / "cur" / "2.2.mailr:2,").exists()
    assert (base / "Inbox" / "cur" / "3.3.mailr:2,").exists()  # in no line of a removed blob
    assert outside.exists()  # a path outside the export is never removed
    assert (base / ".exported").read_text() == "b2\tInbox/cur/2.2.mailr:2,\n"
    assert backup.remove_exported(5, {"b1"})["removed"] == 0
    assert backup.remove_exported(6, {"b1"}) == {"removed": 0, "target_removed": 0, "target_pending": 0}


async def test_the_sweep_cleans_mail_deleted_before_and_holds_back_on_a_mass_deletion(user, raw, export_dirs,
                                                                                     monkeypatch, capsys):
    from mailr import cli

    old, _c = await _ingest(user, raw("Deleted long ago", body="Pin 5512"))
    live, _c2 = await _ingest(user, raw("Still here"))
    await _export(user.id)
    blob = (await _row(old)).blob_id
    async with session_scope() as db:  # how Delete forever left a row before the purge existed
        await db.execute(text("UPDATE messages SET deleted = true, jmap_id = NULL WHERE id = :i"), {"i": old})

    # Half of the mail looks deleted: the automatic run keeps the row's text, and never the file.
    monkeypatch.setattr(purge, "MASS_MIN", 0)
    r = await purge.sweep_user(user.id)
    assert r["rows"] == 1 and r["files"] == 0 and r["files_kept"] == 1
    assert r["held_rows"] is True and r["held_files"] is False
    assert (await _row(old)).body_text
    assert blob in backup.exported_manifest(user.id)

    # The command purges the text, after a dry run that changes nothing; the file stays.
    assert await cli._purge_deleted(user.email, True) == 0
    assert "1 deleted messages would be scrubbed, 1 export files of deleted mail stay" in capsys.readouterr().out
    assert (await _row(old)).body_text and blob in backup.exported_manifest(user.id)
    assert await cli._purge_deleted(user.email, False) == 0
    assert "1 export files of deleted mail stay" in capsys.readouterr().out
    assert (await _row(old)).body_text == ""
    assert blob in backup.exported_manifest(user.id)
    # Only when asked does it remove the files too.
    assert await cli._purge_deleted(user.email, False, exports=True) == 0
    assert "1 export files were removed" in capsys.readouterr().out
    assert set(backup.exported_manifest(user.id)) == {(await _row(live)).blob_id}

    # Below the share the daily run purges by itself.
    monkeypatch.setattr(purge, "MASS_MIN", 1000)
    assert await purge.sweep_user(user.id) == {"rows": 0, "files": 0, "files_kept": 0, "held": False,
                                               "held_rows": False, "held_files": False, "dry_run": False}

    # The scheduler queues one sweep per user, once, when the user's time of day came (SPREAD).
    from mailr.core import tasks

    async with session_scope() as db:
        await db.execute(text("INSERT INTO schedule_runs (name, last_run_at) VALUES (:n, now() - interval '25 hours') "
                              "ON CONFLICT (name) DO UPDATE SET last_run_at = excluded.last_run_at"),
                         {"n": f"purge:{user.id}"})
    await tasks._spread_daily()
    await tasks._spread_daily()
    async with session_scope() as db:
        n = (await db.execute(text("SELECT count(*) FROM jobs WHERE kind = :k AND user_id = :u AND status = 'queued'"),
                              {"k": JOB, "u": user.id})).scalar_one()
    assert n == 1


async def test_a_small_vanish_from_the_store_is_scrubbed_and_keeps_its_export_file(user, raw, export_dirs):
    """One message in thirteen vanishes from the store, as after a restore of last night's snapshot:
    the sweep scrubs its row, and its export file and the copy on the backup target stay."""
    from mailr.core.storesync import _UserSync

    mid, _c = await _ingest(user, raw("Removed in another app", body="Gate 7"))
    await _export(user.id)
    for i in range(12):  # a small vanish: one message of thirteen
        await _ingest(user, raw(f"Kept {i}"))
    m = await _row(mid)
    async with session_scope() as db:
        await _UserSync(user.id)._apply_destroyed(db, [m.jmap_id])
    # Only marked: nothing is queued (no purge, no deletion at a provider), the text and the file
    # stay until the sweep.
    async with session_scope() as db:
        assert (await db.execute(select(Job).where(Job.kind.in_((JOB, "connector.delete_forever")),
                                                   Job.user_id == user.id))).first() is None
    gone = await _row(mid)
    assert gone.deleted and gone.body_text and m.blob_id in backup.exported_manifest(user.id)
    rel = backup.exported_manifest(user.id)[m.blob_id]
    r = await purge.sweep_user(user.id)
    assert r["held"] is False and r["rows"] == 1 and r["files"] == 0 and r["files_kept"] == 1
    assert (await _row(mid)).body_text == ""
    assert backup.exported_manifest(user.id)[m.blob_id] == rel
    assert (backup.export_root(user.id) / rel).is_file() and (backup.target_root(user.id) / rel).is_file()
    assert not (backup.export_root(user.id) / backup.PURGED).exists()
    # The next export does not count it as missing, and nothing removes it later.
    await _export(user.id)
    await purge.sweep_user(user.id)
    assert (backup.export_root(user.id) / rel).is_file() and (backup.target_root(user.id) / rel).is_file()


async def test_an_export_run_that_read_a_message_before_delete_forever_leaves_its_file_out(
        client, user, raw, export_dirs, monkeypatch):
    """The export reads its rows, Delete forever and its purge run while it writes the files: the
    purge finds no manifest line yet, and the export leaves the file out when it finishes."""
    mid, conv = await _ingest(user, raw("Door code", body="Code 4410"))
    kept, _c = await _ingest(user, raw("Picnic"))
    real = backup._finish_batch

    def deleted_meanwhile(base, written, tgt):
        backup.remove_exported(user.id, set(), message_ids=[mid])  # what the purge job does
        real(base, written, tgt)

    monkeypatch.setattr(backup, "_finish_batch", deleted_meanwhile)
    await _export(user.id)
    base, tgt = backup.export_root(user.id), backup.target_root(user.id)
    assert set(backup.exported_manifest(user.id)) == {(await _row(kept)).blob_id}
    assert all(f".{mid}.mailr" not in f for f in _files(base) | _files(tgt))


async def test_a_mass_vanish_keeps_the_text_and_reports_the_hold(user, admin_client, export_dirs, capsys):
    """2000 of 2100 messages vanish from the store at once (a store that lost its data): the sweep
    scrubs nothing, logs and records the hold, and Admin → Diagnostics shows it."""
    from mailr import cli

    async with session_scope() as db:
        await db.execute(text(
            "INSERT INTO messages (user_id, subject, body_text, deleted, intel_state, upstream, \"references\", "
            "label_ids, \"to\", cc, bcc, reply_to, delivered_to, attachments, auth_results, snippet, norm_subject, "
            "from_email, from_name, size, seen, flagged, answered, draft, important, has_attachments, sent_by_me, "
            "local_only) SELECT :u, 'Ledger ' || g, 'Line ' || g, g <= 2000, 'done', '{}', '{}', '{}', '[]', '[]', "
            "'[]', '[]', '{}', '[]', '{}', '', '', 'clerk@ledger.example', '', 0, false, false, false, false, false, "
            "false, false, false FROM generate_series(1, 2100) g"), {"u": user.id})
    r = await purge.sweep_user(user.id)
    assert r["rows"] == 2000 and r["held_rows"] is True
    async with session_scope() as db:
        left = (await db.execute(text("SELECT count(*) FROM messages WHERE user_id = :u AND deleted "
                                      "AND body_text <> '' AND intel_state <> 'purged'"), {"u": user.id})).scalar_one()
    assert left == 2000
    d = (await admin_client.get(f"{API}/admin/diagnostics")).json()
    held = [h for h in d["backup"]["purge_held"] if h["user"] == user.email]
    assert held and held[0]["rows"] == 2000 and held[0]["files"] == 0

    # The command purges anyway, and the hold is cleared on the next sweep.
    assert await cli._purge_deleted(user.email, False) == 0
    assert "2000 deleted messages were scrubbed" in capsys.readouterr().out
    assert (await purge.sweep_user(user.id))["held"] is False
    d = (await admin_client.get(f"{API}/admin/diagnostics")).json()
    assert not [h for h in d["backup"]["purge_held"] if h["user"] == user.email]


async def test_old_tombstones_do_not_dilute_the_mass_guard(user, export_dirs):
    """30,000 messages deleted and scrubbed long ago, 100 live: 2000 vanishing at once is still a mass
    vanish."""
    async with session_scope() as db:
        await db.execute(text(
            "INSERT INTO messages (user_id, subject, body_text, deleted, intel_state, upstream, \"references\", "
            "label_ids, \"to\", cc, bcc, reply_to, delivered_to, attachments, auth_results, snippet, norm_subject, "
            "from_email, from_name, size, seen, flagged, answered, draft, important, has_attachments, sent_by_me, "
            "local_only) SELECT :u, CASE WHEN g <= 30000 THEN '' ELSE 'Note ' || g END, "
            "CASE WHEN g <= 30000 THEN '' ELSE 'Text ' || g END, g <= 32000, "
            "CASE WHEN g <= 30000 THEN 'purged' ELSE 'done' END, '{}', '{}', '{}', '[]', '[]', '[]', '[]', '{}', "
            "'[]', '{}', '', '', 'clerk@ledger.example', '', 0, false, false, false, false, false, false, false, "
            "false FROM generate_series(1, 32100) g"), {"u": user.id})
    r = await purge.sweep_user(user.id, dry_run=True)
    assert r["rows"] == 2000 and r["held_rows"] is True


async def test_a_discarded_draft_is_scrubbed(client, user):
    from helpers.agent import _setup_identity

    await _setup_identity(user)
    r = await client.post(f"{API}/drafts", json={"payload": {
        "identity_id": None, "to": [{"email": "dana@harbour.example"}], "subject": "Unsent plan",
        "html": "<p>Draft words</p>"}})
    assert r.status_code == 200, r.text
    did = r.json()["draft"]["id"]
    r = await client.delete(f"{API}/drafts/{did}")
    assert r.status_code == 200, r.text
    async with session_scope() as db:
        job = (await db.execute(select(Job).where(Job.kind == JOB, Job.user_id == user.id))).scalar_one()
    assert job.payload["export"] is False
    await run_jobs(JOB, user.id)
    async with session_scope() as db:
        m = (await db.execute(select(Message).where(Message.id == job.payload["message_ids"][0]))).scalar_one()
    assert m.deleted and m.subject == "" and m.intel_state == "purged"


async def test_removing_a_user_removes_their_mail_export(app, admin_client, export_dirs, monkeypatch):
    from mailr.api import admin_routes
    from mailr.core.users import create_user
    from mailr.db import sessionmaker

    async def no_store(u):
        return None

    monkeypatch.setattr(admin_routes, "delete_store_account", no_store)
    async with sessionmaker()() as db:
        u = await create_user(db, f"leaver-{uuid.uuid4().hex[:6]}@example.com", "Leaver", password="leaver-pass-123")
    base, tgt = backup.export_root(u.id), backup.target_root(u.id)
    for root in (base, tgt):
        (root / "Inbox" / "cur").mkdir(parents=True)
        (root / "Inbox" / "cur" / "1.1.mailr:2,").write_bytes(b"x")
    r = await admin_client.delete(f"{API}/admin/users/{u.id}", params={"confirm": u.email})
    assert r.status_code == 200, r.text
    async with session_scope() as db:
        job = (await db.execute(select(Job).where(Job.kind == "mail.purge_user_export",
                                                  Job.payload["user_id"].as_integer() == u.id))).scalar_one()
    await purge.purge_user_export(job.payload, None)
    assert not base.exists() and not tgt.exists()
    assert (export_dirs / backup.TARGET_MARKER).exists()


async def test_delete_forever_in_a_store_account_deletes_the_provider_copy(client, user):
    """Inside the retention window, unarmed: Delete forever still deletes the copy at the provider,
    so a Resync from scratch does not bring the message back."""
    from helpers.connectors import (
        create_connector,
        imap,
        make_upstream_account,
        msg,
        msg_count,
        run_sync,
    )

    email, pw = make_upstream_account()
    c = imap(email, pw)
    c.append("INBOX", msg("Gym invoice", msgid="<gym@club.example>", body="Invoice 88"))
    c.append("INBOX", msg("Club news", msgid="<news@club.example>"))
    c.logout()
    conn = await create_connector(client, email, pw)
    assert conn["mode"] == "store" and not conn["delete_upstream"]
    await run_sync(conn["id"])
    async with session_scope() as db:
        m = (await db.execute(select(Message).where(Message.user_id == user.id,
                                                    Message.message_id_hdr == "<gym@club.example>"))).scalar_one()
    await _delete_forever(client, m.conversation_id)
    assert await run_jobs("connector.delete_forever", user.id) == 1
    c = imap(email, pw)
    c.select_folder("INBOX")
    assert len(c.search(["ALL"])) == 1
    c.logout()
    assert (await _row(m.id)).upstream_deleted_at is not None

    r = await client.post(f"{API}/connectors/{conn['id']}/resync")
    assert r.status_code == 200, r.text
    await run_sync(conn["id"])
    assert await msg_count(user.id) == 1
    async with session_scope() as db:
        back = (await db.execute(select(Message).where(Message.user_id == user.id, Message.deleted.is_(False),
                                                       Message.message_id_hdr == "<gym@club.example>"))).first()
    assert back is None
