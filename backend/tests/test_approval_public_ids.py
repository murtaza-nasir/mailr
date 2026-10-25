"""Approvals (pending actions) get public ids like conversations and messages (core/publicid.py):
links, REST, push payloads, agent and MCP results and live events never show the sequential id.

Old numeric ids (links and notifications sent before the switch): the web link /approvals/<id>
moves to the public form for its owner; the API reads a numeric id while MAILR_PUBLIC_IDS_LEGACY
is on, and with it off only for an approval that is still pending and unexpired."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from mailr.core import publicid
from mailr.db import sessionmaker
from mailr.models import PendingAction


def _set(monkeypatch, name, value):
    from mailr.config import get_settings

    monkeypatch.setenv(name, value)
    get_settings.cache_clear()


@pytest.fixture
def pub(monkeypatch):
    from mailr.config import get_settings

    _set(monkeypatch, "MAILR_PUBLIC_IDS", "true")
    _set(monkeypatch, "MAILR_PUBLIC_IDS_LEGACY", "false")
    yield publicid
    monkeypatch.undo()
    get_settings.cache_clear()


async def _pending(user_id: int, *, hours: float = 1, status: str = "pending") -> PendingAction:
    async with sessionmaker()() as db:
        pa = PendingAction(user_id=user_id, subject="token:1", actor="Test agent", action="mail.trash",
                           preview={"title": "Move 1 message to Trash"}, status=status,
                           expires_at=datetime.now(UTC) + timedelta(hours=hours))
        db.add(pa)
        await db.commit()
        await db.refresh(pa)
        return pa


async def _other_user():
    import uuid

    from mailr.core.users import create_user

    async with sessionmaker()() as db:
        return await create_user(db, f"o-{uuid.uuid4().hex[:8]}@example.com", "Other", password="test-password-123")


def test_codec_has_an_approval_kind(pub):
    for n in (1, 20, 21, 2**40 - 1):
        a = pub.encode("approval", n)
        assert 2**52 <= a < 2**53 and pub.decode("approval", a) == n
        assert a not in (pub.encode("conversation", n), pub.encode("message", n))
        with pytest.raises(pub.InvalidId):
            pub.decode("conversation", a)
        assert pub.from_token("approval", pub.token("approval", n)) == n
    assert pub.token("approval", 20).startswith("a")
    with pytest.raises(pub.InvalidId):
        pub.from_token("approval", pub.token("conversation", 20))  # a conversation's form
    for bad in ("", "a", "abc!", "a" + "z" * 20, "9" * 30, "²"):
        with pytest.raises(pub.InvalidId):
            pub.from_token("approval", bad)


def test_out_and_into_by_key(pub):
    o = pub.out({"approval": {"id": pub.ApprovalId(7)}, "approval_id": 7, "pending_id": 7, "approval_ids": [7, 8],
                 "focused_approval": {"id": 7}, "id": 7})
    a7 = pub.encode("approval", 7)
    assert o["approval"]["id"] == o["approval_id"] == o["pending_id"] == o["focused_approval"]["id"] == a7
    assert o["approval_ids"] == [a7, pub.encode("approval", 8)]
    assert o["id"] == 7  # a bare "id" is left alone
    back = pub.into({"resume_approval_id": a7, "args": {"approval_id": a7},
                     "context": {"focused_approval": {"id": a7}}})
    assert back == {"resume_approval_id": 7, "args": {"approval_id": 7}, "context": {"focused_approval": {"id": 7}}}
    # A stale or made-up approval on the screen is dropped, not a 404 for the turn.
    assert pub.into({"context": {"focused_approval": {"id": 7}}}) == {"context": {}}
    assert pub.into({"context": {"focused_approval": {"id": 2**52 + 3}}}) == {"context": {}}
    with pytest.raises(pub.InvalidId):
        pub.into({"resume_approval_id": 7})  # legacy off


async def test_approval_links_and_results_use_public_ids(pub, user, monkeypatch):
    from mailr.agent import tools
    from mailr.config import get_settings
    from mailr.core import permissions, push

    a, b = await _pending(user.id), await _pending(user.id)
    url = permissions.approval_url(a.id)
    tok = url.rsplit("/", 1)[1]
    assert url == f"{get_settings().base_url.rstrip('/')}/approvals/{tok}"
    assert tok.startswith("a") and not url.endswith(f"/approvals/{a.id}") and len(tok) >= 9
    assert pub.from_token("approval", tok) == a.id
    # Neighbours are not neighbours.
    pa_, pb_ = pub.encode("approval", a.id), pub.encode("approval", b.id)
    assert abs(pb_ - pa_) > 1000

    # The 202 body of a REST route, and an agent tool's result as the HTTP edge sends it.
    out = permissions.pending_response(a)
    assert out["approval_id"] == out["approval"]["id"] == pa_ and out["approval_url"] == url
    res = pub.out(tools.pending_result(a).data)
    assert res["approval_id"] == pa_ and res["approval_url"] == url

    # Push: the payload every device gets (fan_out encodes) and the link it opens.
    sent: list[dict] = []

    async def fan_out(db, uid, event, title, body, data, priority="default"):
        sent.append(pub.out(data))
        return 1

    async def web(db, u, level, payload):
        sent.append(pub.out(payload))

    monkeypatch.setattr(push, "fan_out", fan_out)
    monkeypatch.setattr(push, "_web", web)
    await push.push_approval({"pending_id": a.id}, None)
    assert len(sent) == 2 and all(d["pending_id"] == pa_ and d["click"] == url for d in sent)


async def test_api_reads_public_ids_and_refuses_other_users(pub, client, user, monkeypatch):
    from mailr.core import permissions

    events: list[dict] = []

    async def notify(db, uid, ev):
        events.append(ev)

    monkeypatch.setattr(permissions, "notify", notify)
    pa = await _pending(user.id)
    pid, tok = pub.encode("approval", pa.id), pub.token("approval", pa.id)
    listed = (await client.get("/api/v1/approvals")).json()
    assert pid in [c["id"] for c in listed] and pa.id not in [c["id"] for c in listed]
    for ref in (pid, tok):
        r = await client.get(f"/api/v1/approvals/{ref}")
        assert r.status_code == 200 and r.json()["id"] == pid, r.text
    # A made-up public id, a conversation's id and nonsense are 404s.
    for ref in (2**52 + 12345, pub.encode("conversation", pa.id), "nonsense", "a!", "9" * 40):
        assert (await client.get(f"/api/v1/approvals/{ref}")).status_code == 404

    # Another user's approval is a 404 in every form, and cannot be decided.
    other = await _other_user()
    theirs = await _pending(other.id)
    for ref in (pub.encode("approval", theirs.id), pub.token("approval", theirs.id), theirs.id):
        assert (await client.get(f"/api/v1/approvals/{ref}")).status_code == 404
        r = await client.post(f"/api/v1/approvals/{ref}/decide", json={"decision": "deny"})
        assert r.status_code == 404
    async with sessionmaker()() as db:
        assert (await db.get(PendingAction, theirs.id)).status == "pending"

    # Deciding by public id; the live event names the public id too.
    r = await client.post(f"/api/v1/approvals/{pid}/decide", json={"decision": "deny"})
    assert r.status_code == 200 and r.json()["id"] == pid and r.json()["status"] == "denied", r.text
    assert [e["id"] for e in events if e["type"] == "approval"][-1] == pid
    # approval.decided (approval.sync) names it by its public id as well.
    assert [e["ids"] for e in events if e["type"] == "approval.decided"][-1] == [pid]


async def test_old_numeric_ids(pub, app, client, user, monkeypatch):
    """With legacy ids refused (production), an old numeric id still names a pending approval, so a
    notification sent before the switch can be decided; once decided or expired it is a 404. With
    legacy ids allowed it reads like any old id."""
    pending = await _pending(user.id)
    assert (await client.get(f"/api/v1/approvals/{pending.id}")).json()["id"] == pub.encode("approval", pending.id)
    r = await client.post(f"/api/v1/approvals/{pending.id}/decide", json={"decision": "deny"})
    assert r.status_code == 200 and r.json()["status"] == "denied", r.text
    # Decided now: the numeric form is gone, the public one stays.
    assert (await client.get(f"/api/v1/approvals/{pending.id}")).status_code == 404
    assert (await client.get(f"/api/v1/approvals/{pub.encode('approval', pending.id)}")).status_code == 200
    expired = await _pending(user.id, hours=-1)
    assert (await client.get(f"/api/v1/approvals/{expired.id}")).status_code == 404

    _set(monkeypatch, "MAILR_PUBLIC_IDS_LEGACY", "true")
    assert (await client.get(f"/api/v1/approvals/{pending.id}")).status_code == 200
    assert (await client.get(f"/api/v1/approvals/{expired.id}")).status_code == 200
    _set(monkeypatch, "MAILR_PUBLIC_IDS_LEGACY", "false")

    # Old web links /approvals/<internal> move to the public form, for the owner only.
    from starlette.requests import Request

    from mailr.main import _legacy_approval_link

    def req(cookie):
        return Request({"type": "http", "method": "GET", "path": f"/approvals/{pending.id}", "query_string": b"",
                        "headers": [(b"cookie", f"mailr_session={cookie}".encode())] if cookie else []})

    sess = client.cookies.get("mailr_session")
    want = f"/approvals/{pub.token('approval', pending.id)}"
    assert await _legacy_approval_link(f"approvals/{pending.id}", req(sess)) == want
    assert await _legacy_approval_link(f"approvals/{pending.id}", req(None)) is None
    assert await _legacy_approval_link(f"approvals/{pending.id}", req("someone-else")) is None
    assert await _legacy_approval_link(f"approvals/{pub.encode('approval', pending.id)}", req(sess)) is None
    other = await _other_user()
    theirs = await _pending(other.id)
    assert await _legacy_approval_link(f"approvals/{theirs.id}", req(sess)) is None
    # Through the app: the page load redirects.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           cookies={"mailr_session": sess}) as c:
        r = await c.get(f"/approvals/{pending.id}", follow_redirects=False)
        if r.status_code == 302:  # the built web app is present
            assert r.headers["location"] == want
        # A pending approval of another user is not readable by its numeric id either.
        assert (await client.get(f"/api/v1/approvals/{theirs.id}")).status_code == 404


async def test_public_ids_off_keeps_plain_numbers(user, client):
    from mailr.core import permissions

    pa = await _pending(user.id)
    assert permissions.approval_url(pa.id).endswith(f"/approvals/{pa.id}")
    assert (await client.get(f"/api/v1/approvals/{pa.id}")).json()["id"] == pa.id
    assert (await client.get("/api/v1/approvals/aBcDeFgHiJ")).status_code == 404
