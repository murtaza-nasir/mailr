"""Sender pictures and local-only mail (outbound data policy, gap 13): with remote lookups on, an
address known only from local-only mail gets no BIMI, Gravatar or Libravatar lookup; the same
address in ordinary mail does, and so does an address in no mail at all."""

import uuid

from helpers.agent import _ingest, _setup_identity


async def test_no_remote_picture_lookup_for_local_only_correspondents(client, user, raw, monkeypatch):
    import mailr.api.avatar_routes as av

    called: list[str] = []

    async def fake_lookup(email, egress="", may_fetch=None):
        # The real lookup asks may_fetch before any request leaves (a cache hit sends nothing).
        if may_fetch is not None and not await may_fetch():
            return None
        called.append(email)
        return None

    monkeypatch.setattr(av, "lookup_remote", fake_lookup)
    await client.patch("/api/v1/me/settings", json={"sender_pictures": True})
    conn_id, me = await _setup_identity(user, data_class="local_only")
    secret = f"student-{uuid.uuid4().hex[:6]}@uni.example"
    await _ingest(user.id, raw("Appeal", frm=f"Student <{secret}>", to=me, body="x"), connector_id=conn_id)
    assert (await client.get(f"/api/v1/avatars/{secret}")).status_code == 404
    assert called == []
    stranger = f"nobody-{uuid.uuid4().hex[:6]}@else.example"
    await client.get(f"/api/v1/avatars/{stranger}")
    assert called == [stranger]
    # Once the address writes from an ordinary account, the lookup is allowed.
    await _ingest(user.id, raw("Hello", frm=f"Student <{secret}>", to=me, body="y"))
    await client.get(f"/api/v1/avatars/{secret}")
    assert called == [stranger, secret]
