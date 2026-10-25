"""Integrations: Speakr, push, public ids, People search, the Speakr webhook, and the server
halves of conference links and VAPID for UnifiedPush. Each test is an attack that must fail.
Needs the dev Stalwart (ingest)."""

import base64
import json
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from helpers.core_flow import _ingest
from httpx import ASGITransport, AsyncClient
from speakr_fake import BASE, connect, speakr  # noqa: F401
from sqlalchemy import select

from mailr.db import sessionmaker
from mailr.models import (
    AssistantOrder,
    AssistantRun,
    Connector,
    Conversation,
    PermissionRule,
    Person,
    PushTarget,
    RecordingLink,
    User,
)


def _tok_client(app, tok: str) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {tok}"})


async def _token(client, scopes, local_only=False) -> dict:
    r = await client.post("/api/v1/tokens", json={"name": f"t{uuid.uuid4().hex[:4]}", "scopes": scopes,
                                                  "local_only": local_only})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- Forged Speakr links


async def test_forged_speakr_link_never_binds_a_thread_to_recordings(client, user, raw, speakr):
    from mailr.assistant import errands
    from mailr.integrations.speakr import mail as smail
    from mailr.integrations.speakr.sync import sync_connector

    speakr.add(1, "Old")
    conn = await connect(client, speakr)
    r = await client.post("/api/v1/assistant/orders", json={
        "name": "Follow-ups", "trigger": "recording", "errand": "Draft the follow-up reply in the linked email."})
    assert r.status_code == 200, r.text
    oid = r.json()["id"]
    # Any sender mails links to the next recording ids, before they exist.
    body = "Hi! " + " ".join(f"{BASE}/recordings/{n}" for n in range(2, 6))
    mid, evil_cid = await _ingest(user, raw("Quick question", frm="Mallory <mallory@evil.example>", body=body),
                                  roles=["inbox"])
    await smail.mail_job({"message_id": mid}, None)
    async with sessionmaker()() as db:
        links = (await db.execute(select(RecordingLink).where(RecordingLink.connector_id == conn["id"],
                                                              RecordingLink.kind == "conversation"))).scalars().all()
        assert links == []  # ids that are not in the mirror are never linked
    # The user's real meeting is recorded later and completes.
    speakr.add(2, "Board meeting", meeting_date=datetime.now(UTC) - timedelta(hours=1))
    await sync_connector(conn["id"])
    async with sessionmaker()() as db:
        links = (await db.execute(select(RecordingLink).where(RecordingLink.connector_id == conn["id"],
                                                              RecordingLink.kind == "conversation"))).scalars().all()
        assert all(lk.state != "confirmed" for lk in links)  # at most a suggestion
        run = (await db.execute(select(AssistantRun).where(AssistantRun.order_id == oid))).scalars().one()
        assert run.conversation_id != evil_cid
        prompt = await errands._prompt(db, await db.get(User, user.id), await db.get(AssistantOrder, oid), run, [])
    assert f"conversation {evil_cid}" not in prompt


async def test_authenticated_speakr_mail_links_and_writes_no_mail_refs(client, user, raw, speakr):
    """A notification that authenticates for the Speakr host's domain makes a confirmed link; the
    reference written back to Speakr never carries a link made from mail."""
    from mailr.integrations.speakr import mail as smail
    from mailr.integrations.speakr import writes

    speakr.modern()
    speakr.add(5, "Weekly sync")
    conn = await connect(client, speakr)
    good = raw("Transcription ready: Weekly sync", frm="Speakr <noreply@speakr.test>", body=f"{BASE}/recordings/5",
               extra="Authentication-Results: mx; dkim=pass header.d=speakr.test; dmarc=pass")
    mid, cid = await _ingest(user, good, roles=["inbox"])
    await smail.mail_job({"message_id": mid}, None)
    unauth = raw("Transcription ready: Weekly sync", frm="Speakr <noreply@speakr.test>", body=f"{BASE}/recordings/5")
    umid, ucid = await _ingest(user, unauth, roles=["inbox"])
    await smail.mail_job({"message_id": umid}, None)
    async with sessionmaker()() as db:
        rows = {lk.target: lk.state for lk in (await db.execute(select(RecordingLink).where(
            RecordingLink.connector_id == conn["id"], RecordingLink.kind == "conversation"))).scalars()}
        assert rows == {str(cid): "confirmed", str(ucid): "suggested"}
        c = await db.get(Connector, conn["id"])
        c.config = {**(c.config or {}), "write_back": True}
        await db.flush()
        assert await writes.sync_refs(db, c, 5) is True
        # The user confirms the other link by hand: that one is theirs and is written.
        from mailr.core.auth import ALL_SCOPES, Principal
        from mailr.integrations.speakr import service

        p = Principal(user=await db.get(User, user.id), scopes=set(ALL_SCOPES) - {"admin"})
        await service.set_link(db, p, rid=5, cid=conn["id"], kind="conversation", target=str(ucid))
        await db.commit()
    from mailr.integrations.speakr.writes import conversation_ref

    refs = [r["ref"] for r in speakr.refs.get(5, []) if r.get("kind") == "conversation"]
    assert refs == [conversation_ref(ucid)]  # the user's own confirmation, never the mail-made link


# ---------------------------------------------------------------- Local-only content in pushes


@pytest.fixture
def posted(monkeypatch):
    """Every POST core/push.py makes through netguard, answered 200."""
    from mailr.core import netguard

    calls: list[dict] = []

    async def fetch_capped(client, method, url, *, limit, deadline=30.0, content=None, headers=None, **kw):
        calls.append({"url": url, "content": content or b"", "headers": dict(headers or {})})
        return httpx.Response(200), b""

    monkeypatch.setattr(netguard, "fetch_capped", fetch_capped)
    return calls


def _blob(calls) -> bytes:
    return b" ".join(c["content"] + json.dumps(c["headers"]).encode() for c in calls)


async def test_follow_up_and_assistant_pushes_carry_no_local_only_text(client, user, raw, posted):
    from helpers.agent import _ingest as agent_ingest
    from helpers.agent import _setup_identity

    from mailr.assistant import notify
    from mailr.core.tasks import followup_check

    async with sessionmaker()() as db:
        db.add(PushTarget(user_id=user.id, kind="ntfy", endpoint=f"http://ntfy.invalid/t{uuid.uuid4().hex[:8]}",
                          events=[]))
        db.add(PushTarget(user_id=user.id, kind="unifiedpush", endpoint=f"http://ntfy.invalid/u{uuid.uuid4().hex[:8]}",
                          events=[]))
        await db.commit()
    conn_id, me = await _setup_identity(user, data_class="local_only")
    _mid, cid = await agent_ingest(user.id, raw("SECRET-SUBJECT grades", frm="Student Name <s2@uni.example>", to=me,
                                               body="b"), connector_id=conn_id)
    at = datetime.now(UTC) - timedelta(minutes=1)
    async with sessionmaker()() as db:
        (await db.get(Conversation, cid)).follow_up_at = at
        await db.commit()
    await followup_check({"conversation_id": cid, "at": at.isoformat()}, None)
    assert len(posted) == 2, "the follow-up nudge still goes out"
    # The assistant's notify without the private flag: the conversation is enough.
    async with sessionmaker()() as db:
        await notify.send(db, await db.get(User, user.id), title="Ambrose · Student Name",
                          body="Student Name: SECRET-SUBJECT", data={"conversation_id": cid}, force=True)
        # A Speakr order on a local-only account names only the connector.
        from mailr.core import push

        await push.fan_out(db, user.id, "assistant", "Ambrose · order", "New recording: SECRET-SUBJECT",
                           {"connector_id": conn_id})
    assert len(posted) == 6
    blob = _blob(posted)
    assert b"SECRET-SUBJECT" not in blob and b"Student Name" not in blob and b"uni.example" not in blob


async def test_ordinary_mail_keeps_its_text(client, user, raw, posted):
    from helpers.agent import _ingest as agent_ingest

    from mailr.core import push

    async with sessionmaker()() as db:
        db.add(PushTarget(user_id=user.id, kind="ntfy", endpoint=f"http://ntfy.invalid/t{uuid.uuid4().hex[:8]}",
                          events=[]))
        await db.commit()
    _mid, cid = await agent_ingest(user.id, raw("Lunch plan", frm="Bob <bob@x.example>", body="b"))
    async with sessionmaker()() as db:
        await push.fan_out(db, user.id, "follow_up", "No reply from Bob", "Lunch plan", {"conversation_id": cid})
    assert posted[0]["content"] == b"Lunch plan" and posted[0]["headers"]["Title"] == "No reply from Bob"


# ---------------------------------------------------------------- Speakr gates


async def test_connector_deny_holds_without_connector_id(client, user, speakr, app):
    from helpers.speakr_record import URL, _form

    conn = await connect(client, speakr)
    tok = await _token(client, ["read", "recordings"], local_only=True)
    async with sessionmaker()() as db:
        subj = f"token:{tok['item']['id']}"
        db.add(PermissionRule(user_id=user.id, subject=subj, action="speakr.upload", effect="allow", constraints={}))
        db.add(PermissionRule(user_id=user.id, subject=subj, action="speakr.upload", effect="deny",
                              constraints={"connectors": [conn["id"]]}))
        await db.commit()
    async with _tok_client(app, tok["token"]) as t:
        r = await t.post(URL, **_form())
        assert r.status_code == 403, r.text
        r = await t.post("/api/v1/speakr/upload", json={"message_id": 1, "part": 2})
        assert r.status_code == 403, r.text
    assert speakr.uploads == []


async def test_speaker_write_back_needs_speakr_write(client, user, speakr, app):
    from helpers.speakr_writeback import _switch

    speakr.modern()
    speakr.add(1, participants="Dana, Evan")
    speakr.speakers[1] = [{"label": "SPEAKER_00", "identified_name": "Dana", "segment_count": 2}]
    conn = await connect(client, speakr)
    cid = conn["id"]
    await _switch(client, cid, write_back=True)
    d = (await client.get(f"/api/v1/speakr/recordings/{cid}/1/people")).json()
    dana = next(s for s in d["speakers"] if s["name"] == "Dana")
    body = {"key": dana["key"], "speaker_name": "Dana", "email": "dana@example.org", "speaker_id": 1,
            "recording_id": 1}
    tok = await _token(client, ["read", "recordings"], local_only=True)
    async with _tok_client(app, tok["token"]) as t:
        r = await t.post(f"/api/v1/speakr/speakers/{cid}/person", json=body)
    assert r.status_code == 202, r.text
    assert r.json()["approval"]["action"] == "speakr.write"
    assert not [w for w in speakr.writes if "/speakers/" in w[1]]
    # Approved: the mapping is made and the address is written.
    from mailr.core import permissions

    pid = r.json()["approval"]["id"]
    async with sessionmaker()() as db:
        await permissions.decide(db, await db.get(User, user.id), pid, "approve", "once")
        await db.commit()
    await permissions.execute_job({"pending_id": pid}, None)
    assert speakr.profiles[0]["email"] == "dana@example.org"


# ---------------------------------------------------------------- Public ids in JSON bodies


@pytest.fixture
def pub(monkeypatch):
    from mailr.config import get_settings
    from mailr.core import publicid

    monkeypatch.setenv("MAILR_PUBLIC_IDS", "true")
    monkeypatch.setenv("MAILR_PUBLIC_IDS_LEGACY", "false")
    get_settings.cache_clear()
    yield publicid
    monkeypatch.undo()
    get_settings.cache_clear()


@pytest.mark.parametrize("ctype", ["Application/JSON", "application/vnd.x+json", "APPLICATION/JSON; charset=UTF-8"])
async def test_internal_ids_refused_whatever_the_json_content_type(client, user, raw, pub, ctype):
    _mid, cid = await _ingest(user, raw("Hello", body="x"), roles=["inbox"])
    h = {k: v for k, v in client.headers.items() if k.lower() != "content-type"}
    if ctype:
        h["content-type"] = ctype
    r = await client.post("/api/v1/conversations/actions",
                          content=json.dumps({"ids": [cid], "action": "read"}).encode(), headers=h)
    assert r.status_code == 404, r.text
    # The public id works with the same content type.
    r = await client.post("/api/v1/conversations/actions",
                          content=json.dumps({"ids": [pub.encode("conversation", cid)], "action": "read"}).encode(),
                          headers=h)
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------- People search oracle


async def test_hidden_fields_are_not_searchable_by_hosted_tokens(client, user, raw, app):
    _mid, _ = await _ingest(user, raw("Hi", frm="Bob <bob@x.org>", body="b"))
    async with sessionmaker()() as db:
        p = (await db.execute(select(Person).where(Person.user_id == user.id, Person.email == "bob@x.org"))).scalar_one()
        p.name = "Robert Hiddensurname"
        p.organization = "Secretorg"
        hidden = {"source": "carddav", "connector_id": 999, "href": "/c", "local_only": True}
        p.provenance = {**(p.provenance or {}), "name": hidden, "organization": hidden}
        p.addresses = [{"text": "12 Hidden Lane", "type": "home", "source": "carddav", "local_only": True},
                       {"text": "1 Open Road", "type": "work", "source": "mail"}]
        await db.commit()
    hosted = await _token(client, ["read"])
    local = await _token(client, ["read"], local_only=True)
    async with _tok_client(app, hosted["token"]) as t:
        assert (await t.get("/api/v1/people", params={"q": "hiddensurname"})).json() == []
        assert (await t.get("/api/v1/people", params={"q": "secretorg"})).json() == []
        assert [x["email"] for x in (await t.get("/api/v1/people", params={"q": "bob@x"})).json()] == ["bob@x.org"]
        assert (await t.get("/api/v1/contacts/places", params={"q": "hiddensurname"})).json() == []
        assert (await t.get("/api/v1/contacts/places", params={"q": "hidden lane"})).json() == []
        open_ = (await t.get("/api/v1/contacts/places", params={"q": "open road"})).json()
        assert [x["text"] for x in open_] == ["1 Open Road"] and open_[0]["name"] == ""
    async with _tok_client(app, local["token"]) as t:
        assert [x["email"] for x in (await t.get("/api/v1/people", params={"q": "hiddensurname"})).json()] == ["bob@x.org"]
        assert "12 Hidden Lane" in [x["text"] for x in (await t.get("/api/v1/contacts/places",
                                                                  params={"q": "hidden lane"})).json()]


# ---------------------------------------------------------------- Speakr webhook

SECRET = "whsec_test_0123456789"


async def test_replay_with_a_fresh_header_id_is_a_duplicate(client, user, speakr, app):
    from mailr.integrations.speakr.webhook import sign

    speakr.add(1, "Weekly sync")
    conn = await connect(client, speakr)
    r = await client.patch(f"/api/v1/connectors/{conn['id']}", json={"secrets": {"webhook_secret": SECRET}})
    path = r.json()["speakr"]["webhook_path"]
    body = {"id": uuid.uuid4().hex, "type": "recording.summary.completed", "timestamp": int(time.time()) - 3600 * 20,
            "data": {"recording_id": 1}}
    raw = json.dumps(body).encode()
    sig = sign(SECRET, raw)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        r1 = await anon.post(path, content=raw, headers={"Speakr-Signature": sig, "Speakr-Delivery-Id": body["id"]})
        assert r1.json() == {"ok": True, "queued": True}
        for _ in range(3):
            r = await anon.post(path, content=raw, headers={"Speakr-Signature": sig,
                                                            "Speakr-Delivery-Id": uuid.uuid4().hex})
            assert r.status_code == 200 and r.json() == {"ok": True, "duplicate": True, "queued": False}
        # No header at all: the signed body still names the delivery.
        r = await anon.post(path, content=raw, headers={"Speakr-Signature": sig})
        assert r.json()["duplicate"] is True


async def test_webhook_refuses_a_large_body_before_reading_or_lookup(app):
    """Fixed by area 4 (d521937): the cap applies before the connector lookup."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        r = await anon.post("/api/v1/integrations/speakr/no-such-hook/webhook", content=b"{}",
                            headers={"content-length": str(10 * 1024 * 1024)})
        assert r.status_code == 413
        r = await anon.post("/api/v1/integrations/speakr/no-such-hook/webhook", content=b"x" * (70 * 1024))
        assert r.status_code == 413


# ---------------------------------------------------------------- VAPID for UnifiedPush


def _phone_keys():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    return {"p256dh": b64(pub), "auth": b64(uuid.uuid4().bytes)}


async def test_unifiedpush_with_the_server_key_is_signed(client, user, posted):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    from mailr.core import push

    listing = (await client.get("/api/v1/push/targets")).json()
    key = listing["vapid_public_key"]
    assert key and key == (await client.get("/api/v1/push/webpush/key")).json()["public_key"]
    ep_signed = f"http://ntfy.invalid/up{uuid.uuid4().hex[:8]}"
    r = await client.post("/api/v1/push/targets", json={"endpoint": ep_signed, "keys": _phone_keys(), "vapid": True})
    assert r.status_code == 200, r.text
    assert r.json()["vapid"] is True
    # VAPID without the phone's keys is refused (the connector always sends both).
    r = await client.post("/api/v1/push/targets", json={"endpoint": "http://ntfy.invalid/x", "vapid": True})
    assert r.status_code == 400
    ep_plain = f"http://ntfy.invalid/up{uuid.uuid4().hex[:8]}"
    assert (await client.post("/api/v1/push/targets", json={"endpoint": ep_plain, "keys": _phone_keys()})).status_code == 200
    async with sessionmaker()() as db:
        assert await push.fan_out(db, user.id, "send_failed", "Message not sent", "x", {"outbox_id": 1}) == 2
    by = {c["url"]: c["headers"] for c in posted}
    assert "Authorization" not in by[ep_plain]
    auth = by[ep_signed]["Authorization"]
    assert auth.startswith("vapid t=") and f"k={key}" in auth
    # The JWT verifies with the advertised key and names the push server.
    jwt = auth.split("t=", 1)[1].split(",", 1)[0].strip()
    h, p, s = jwt.split(".")
    pad = lambda x: base64.urlsafe_b64decode(x + "=" * (-len(x) % 4))
    claims = json.loads(pad(p))
    assert claims["aud"] == "http://ntfy.invalid" and claims["exp"] > time.time()
    sig = pad(s)
    pubkey = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), pad(key))
    pubkey.verify(encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")),
                  f"{h}.{p}".encode(), ec.ECDSA(hashes.SHA256()))


# ---------------------------------------------------------------- Conference links (server)


def test_join_url_is_anchored_to_provider_hosts():
    from mailr.calendar.planner import join_url

    for bad in ("https://zoom.us.evil.example/j/1", "http://evil.example/zoom.us", "https://phish.example/?meet.google.com",
                "http://zoom.us/j/1", "https://evilzoom.us/j/1", "https://user@zoom.us.evil.example/j/1"):
        assert join_url(bad, None, None) is None, bad
        assert join_url(None, f"Room 3 {bad}", f"Join: {bad}") is None, bad
    assert join_url("https://us02web.zoom.us/j/123?pwd=x", None, None) == "https://us02web.zoom.us/j/123?pwd=x"
    assert join_url(None, "https://meet.google.com/abc-defg-hij", None) == "https://meet.google.com/abc-defg-hij"
    assert join_url("https://example.org/page", "x", "see https://teams.microsoft.com/l/meetup-join/1 now") == \
        "https://teams.microsoft.com/l/meetup-join/1"
