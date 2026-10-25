"""Attached emails (message/rfc822) are attachments, not body text.

The outer message's body, preview, passages and own words come only from its own parts. Each
attached email is listed as an attachment with a small summary, opens in the reader
(mail.open_attached_email), downloads as an .eml file, and is found by search at a lower rank.
"""
# ACT-SURFACES: api

import base64

from helpers.agent import _ingest, _setup_identity
from helpers.attached_emails import (
    ATTACHED,
    INNER_BODY,
    _as_stored_before,
    _email,
    _outer,
)
from sqlalchemy import select
from sqlalchemy.orm import undefer

from mailr.core import mime
from mailr.db import sessionmaker
from mailr.models import Memory, Message, MessagePassage


def _emails(p: mime.Parsed) -> list[dict]:
    return [a for a in p.attachments if a["type"] == "message/rfc822"]


# --- parsing --------------------------------------------------------------------------------


def test_forward_as_attachment_keeps_the_outer_body_clean():
    p = mime.parse(_outer([(ATTACHED, _email())]))
    assert p.body_text == "Please see the email below."
    assert "zanzibarite" not in p.snippet and "zanzibarite" not in p.text
    (a,) = _emails(p)
    assert a["filename"] == "Supplier quote.eml" and not a["inline"]
    assert a["email"]["subject"] == "Supplier quote" and a["email"]["from_email"] == "bob@inner.example"
    assert a["email"]["from_name"] == "Bob Inner" and a["email"]["date"].startswith("2026-10-05")
    assert "zanzibarite" in a["email"]["excerpt"]
    assert "zanzibarite" in p.attached_text


def test_nested_forward_lists_the_first_email_and_indexes_both():
    inner = _email(subject="Original", body="The innermost quokkaline detail.")
    middle_b = "mid0"
    middle = ("From: Carol <carol@example.org>\r\nSubject: Middle\r\nDate: Tue, 6 Oct 2026 10:00:00 +0000\r\n"
              f"MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=\"{middle_b}\"\r\n\r\n"
              f"--{middle_b}\r\nContent-Type: text/plain\r\n\r\nCarol's own words here.\r\n"
              f"--{middle_b}\r\n{ATTACHED}\r\n").encode() + inner + f"\r\n--{middle_b}--\r\n".encode()
    p = mime.parse(_outer([(ATTACHED, middle)]))
    assert p.body_text == "Please see the email below."
    (a,) = _emails(p)
    assert a["email"]["subject"] == "Middle" and a["email"]["attachments"] == 1
    assert "Carol's own words" in p.attached_text and "quokkaline" in p.attached_text


def test_attachment_only_forward_has_an_empty_body():
    p = mime.parse(_outer([(ATTACHED, _email())], body=None, subject="Fwd: quote"))
    assert p.body_text == "" and p.snippet == ""
    assert len(_emails(p)) == 1 and "zanzibarite" in p.attached_text


def test_digest_lists_every_email():
    raw = _outer([("", _email(subject=f"Issue {i}", body=f"Digest entry number {i} with platypodes."))
                  for i in range(3)], body=None, subject="Digest", ctype="multipart/digest")
    p = mime.parse(raw)
    assert p.body_text == ""
    assert [a["email"]["subject"] for a in _emails(p)] == ["Issue 0", "Issue 1", "Issue 2"]
    assert p.attached_text.count("platypodes") == 3


def test_broken_attached_email_degrades_to_a_plain_eml_file():
    for content in (b"", b"\x00\xff no headers at all", b"From: half"):
        p = mime.parse(_outer([(ATTACHED, content)]))
        assert p.body_text == "Please see the email below."
        (a,) = _emails(p)
        assert a["filename"].endswith(".eml") and "email" not in a
    # Base64 inside message/rfc822 is not allowed, but it is sent: decoded and read.
    enc = 'Content-Type: message/rfc822\r\nContent-Transfer-Encoding: base64\r\n'
    p = mime.parse(_outer([(enc, base64.encodebytes(_email()))]))
    assert p.body_text == "Please see the email below."
    assert _emails(p)[0]["email"]["subject"] == "Supplier quote"


def test_inline_attached_email_is_still_an_attachment():
    inline = "Content-Type: message/rfc822\r\nContent-Disposition: inline\r\n"
    p = mime.parse(_outer([(inline, _email())]))
    assert p.body_text == "Please see the email below."
    (a,) = _emails(p)
    assert a["inline"] is False and a["email"]["subject"] == "Supplier quote"


def test_get_part_downloads_the_attached_email_as_eml():
    raw = _outer([(ATTACHED, _email())])
    part = _emails(mime.parse(raw))[0]["part"]
    data, ctype, name = mime.get_part(raw, part)
    assert ctype == "message/rfc822" and name == "Supplier quote.eml"
    assert data.startswith(b"From: Bob Inner") and b"zanzibarite" in data


def test_reader_body_leaves_the_attached_email_out():
    from mailr.core.render import render_body

    html_inner = _email(html="<p>An <b>inner</b> wombatine paragraph.</p>")
    out = render_body(_outer([(ATTACHED, html_inner)], body=None), 1)
    assert "wombatine" not in (out.get("html") or "") and "wombatine" not in (out.get("text") or "")


# --- API --------------------------------------------------------------------------------------


async def test_reader_lists_opens_and_downloads_the_attached_email(client, user):
    inner = _email(html='<p>Inner <img src="https://img.example/pic.png" width="300" height="200"> text.</p>'
                        "<script>alert(1)</script>")
    mid, cid = await _ingest(user.id, _outer([(ATTACHED, inner)]), roles=["inbox"])
    conv = (await client.get(f"/api/v1/conversations/{cid}")).json()
    (a,) = conv["messages"][0]["attachments"]
    assert a["type"] == "message/rfc822" and a["email"]["subject"] == "Supplier quote"
    # ACT mail.open_attached_email: headers, a sanitised body with remote images blocked.
    r = await client.get(f"/api/v1/messages/{mid}/attachments/{a['part']}/email")
    assert r.status_code == 200
    e = r.json()
    assert e["subject"] == "Supplier quote" and e["from"]["email"] == "bob@inner.example"
    assert "<script" not in e["html"] and "img.example" not in e["html"].split("data-mailr-src")[0]
    assert e["remote_images"] is True
    dl = await client.get(f"/api/v1/messages/{mid}/attachments/{a['part']}", params={"download": 1})
    assert dl.status_code == 200 and b"Subject: Supplier quote" in dl.content
    assert "Supplier%20quote.eml" in dl.headers["content-disposition"]
    # A part that is not an attached email is not opened as one.
    assert (await client.get(f"/api/v1/messages/{mid}/attachments/1/email")).status_code == 404
    assert (await client.get(f"/api/v1/messages/{mid}/attachments/999/email")).status_code == 404
    async with sessionmaker()() as db:
        m = await db.get(Message, mid, options=[undefer(Message.attached_text)])
        assert "zanzibarite" not in m.body_text and m.has_attachments
        assert "zanzibarite" in (m.attached_text or "")


async def test_nested_attached_email_files_download_from_the_outer_message(client, user):
    pdf = ('Content-Type: application/pdf\r\nContent-Disposition: attachment; filename="q.pdf"\r\n'
           'Content-Transfer-Encoding: base64\r\n\r\naGVsbG8=\r\n')
    b = "mid1"
    middle = ("From: Carol <carol@example.org>\r\nSubject: With file\r\nDate: Tue, 6 Oct 2026 10:00:00 +0000\r\n"
              f"MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=\"{b}\"\r\n\r\n"
              f"--{b}\r\nContent-Type: text/plain\r\n\r\nFile inside.\r\n--{b}\r\n{pdf}--{b}--\r\n").encode()
    mid, cid = await _ingest(user.id, _outer([(ATTACHED, middle)]), roles=["inbox"])
    conv = (await client.get(f"/api/v1/conversations/{cid}")).json()
    (a,) = conv["messages"][0]["attachments"]
    e = (await client.get(f"/api/v1/messages/{mid}/attachments/{a['part']}/email")).json()
    (f,) = e["attachments"]
    assert f["filename"] == "q.pdf" and f["part"] is not None
    r = await client.get(f"/api/v1/messages/{mid}/attachments/{f['part']}")
    assert r.status_code == 200 and r.content == b"hello"


async def test_search_finds_attached_text_lower_and_says_so(client, user):
    from mailr.intel import passages

    own, own_c = await _ingest(user.id, _outer([], body="Our zanzibarite order shipped today.",
                                               subject="Order update"), roles=["inbox"])
    fwd, fwd_c = await _ingest(user.id, _outer([(ATTACHED, _email())], subject="Fwd: quote"), roles=["inbox"])
    async with sessionmaker()() as db:
        for mid in (own, fwd):
            await passages.build_for_message(db, await db.get(Message, mid))
        await db.commit()
    items = (await client.get("/api/v1/conversations", params={"view": "all", "q": "zanzibarite"})).json()["items"]
    by = {c["id"]: c for c in items}
    assert set(by) == {own_c, fwd_c}
    assert by[fwd_c].get("match_in_attached") is True and "match_in_attached" not in by[own_c]
    # A word in the outer text too: no hint.
    items = (await client.get("/api/v1/conversations", params={"view": "all", "q": "quote"})).json()["items"]
    assert all("match_in_attached" not in c for c in items)
    hits = (await client.get("/api/v1/passages/search", params={"q": "zanzibarite"})).json()["items"]
    assert [h["message_id"] for h in hits][:2] == [own, fwd]
    assert hits[0]["score"] > hits[1]["score"]


async def test_own_words_leave_the_attached_email_out(client, user):
    """The assistant's writing samples (agent/context._my_replies) read body_text: a message the
    user sent with an email attached shows only what the user wrote."""
    from mailr.agent.context import _my_replies
    from mailr.core.auth import Principal

    _, me = await _setup_identity(user)
    mid, _ = await _ingest(user.id, _outer([(ATTACHED, _email())], body="Forwarding this for your records.",
                                           frm=f"Me <{me}>", to="Dana <dana@example.org>"),
                           source="send", roles=["sent"])
    async with sessionmaker()() as db:
        m = await db.get(Message, mid)
        assert m.own_words
        p = Principal(user=user)
        got = await _my_replies(db, p, ["dana@example.org"])
    assert got and got[0]["message_id"] == mid
    assert "Forwarding this" in str(got[0]["body"]) and "zanzibarite" not in str(got[0]["body"])


# --- mailr reparse-attached-emails --------------------------------------------------------------


async def test_reparse_dry_run_counts_and_the_real_run_repairs(user, tmp_path, monkeypatch):
    from mailr.config import get_settings
    from mailr.core import reparse
    from mailr.intel import passages

    monkeypatch.setattr(get_settings(), "data_dir", str(tmp_path))
    _, me = await _setup_identity(user)
    plain, _ = await _ingest(user.id, _outer([], body="Nothing attached here."), roles=["inbox"])
    fwd, _ = await _ingest(user.id, _outer([(ATTACHED, _email())]), roles=["inbox"])
    sent, _ = await _ingest(user.id, _outer([(ATTACHED, _email())], body="Sending you this.", frm=f"Me <{me}>",
                                            to="Dana <dana@example.org>"), source="send", roles=["sent"])
    current, _ = await _ingest(user.id, _outer([(ATTACHED, _email())]), roles=["inbox"])
    for mid in (fwd, sent):
        await _as_stored_before(mid)
    async with sessionmaker()() as db:
        for mid in (fwd, sent):
            await passages.build_for_message(db, await db.get(Message, mid), rebuild=True)
        db.add(Memory(user_id=user.id, text="You track zanzibarite numbers.", source="extracted", status="suggested",
                      refs=[{"message_id": sent}]))
        db.add(Memory(user_id=user.id, text="You like quarterly numbers.", source="extracted", status="active",
                      refs=[{"message_id": sent}]))
        await db.commit()

    dry = await reparse.attached_emails(user.id, batch=2, dry_run=True, rate=0)
    assert dry["seen"] == 4 and dry["with_attached"] == 3 and dry["changed"] == 2
    assert sum(dry["accounts"].values()) == 2
    async with sessionmaker()() as db:
        assert INNER_BODY in (await db.get(Message, fwd)).body_text  # a dry run changes nothing

    st = await reparse.attached_emails(user.id, batch=2, dry_run=False, rate=0)
    assert st["changed"] == 2 and st["memories_withdrawn"] == 1
    async with sessionmaker()() as db:
        for mid in (fwd, sent):
            m = await db.get(Message, mid, options=[undefer(Message.attached_text)])
            assert INNER_BODY not in m.body_text and INNER_BODY not in m.snippet
            assert "zanzibarite" in (m.attached_text or "")
            assert [a["type"] for a in m.attachments] == ["message/rfc822"] and m.has_attachments
            texts = (await db.execute(select(MessagePassage.text).where(MessagePassage.message_id == mid))).scalars()
            assert all("zanzibarite" not in t for t in texts)
        mems = list((await db.execute(select(Memory.status).where(Memory.user_id == user.id))).scalars())
        assert mems == ["active"]  # the suggestion is withdrawn; what the user accepted stays
        assert (await db.get(Message, plain)).body_text == "Nothing attached here."
        assert (await db.get(Message, current, options=[undefer(Message.attached_text)])).attached_text
    # Idempotent: a second run finds nothing to change.
    again = await reparse.attached_emails(user.id, dry_run=False, rate=0)
    assert again["changed"] == 0 and again["with_attached"] == 3


async def test_reparse_resumes_from_the_saved_cursor(user, tmp_path, monkeypatch):
    from mailr.config import get_settings
    from mailr.core import reparse

    monkeypatch.setattr(get_settings(), "data_dir", str(tmp_path))
    ids = [(await _ingest(user.id, _outer([(ATTACHED, _email())]), roles=["inbox"]))[0] for _ in range(3)]
    for mid in ids:
        await _as_stored_before(mid)
    seen: list[int] = []

    class Stop(Exception):
        pass

    def stop_after_first(st):
        seen.append(st["seen"])
        raise Stop

    try:
        await reparse.attached_emails(user.id, batch=1, dry_run=True, rate=0, progress=stop_after_first)
    except Stop:
        pass
    st = await reparse.attached_emails(user.id, batch=1, dry_run=True, rate=0)
    assert seen == [1] and st["resumed_from"] == ids[0] and st["seen"] == 3 and st["changed"] == 3
    # The finished run starts again from the first message.
    assert (await reparse.attached_emails(user.id, batch=5, dry_run=True, rate=0))["resumed_from"] == 0
