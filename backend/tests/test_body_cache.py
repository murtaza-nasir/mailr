"""The rendered-body cache (core/bodycache.py) behind GET /messages/{id}/body.

A second open is served without rendering again; authorisation still runs first, so another
user gets 404 for a body the owner has cached; blocked and allowed images are separate entries;
and a change in the image proxy's cache for one of the message's images renders it again.
"""

from __future__ import annotations

import time
import uuid
from email.message import EmailMessage

import pytest
from conftest import mail_date
from httpx import ASGITransport, AsyncClient

from mailr.core import imageproxy, render
from mailr.core.bodycache import BodyCache, bodies

IMG = "https://cdn.example.org/body-cache/hero.png"


def _html(html: str) -> bytes:
    msg = EmailMessage()
    msg["From"] = "Shop <news@shop.example.org>"
    msg["To"] = "me@example.com"
    msg["Subject"] = "Body cache"
    msg["Message-ID"] = f"<{uuid.uuid4().hex}@example.org>"
    msg["Date"] = mail_date()
    msg.set_content("text version")
    msg.add_alternative(f"<html><body>{html}</body></html>", subtype="html")
    return msg.as_bytes()


async def _ingest(user, html: str) -> int:
    from mailr.core.ingest import IngestOptions, ingest_raw
    from mailr.core.store import user_store
    from mailr.db import sessionmaker
    from mailr.models import User

    async with sessionmaker()() as db:
        u = await db.get(User, user.id)
        store = await user_store(db, u)
        res = await ingest_raw(db, store, _html(html), IngestOptions(roles=["inbox"], source="import"))
        await db.commit()
        return res.message.id


@pytest.fixture
def renders(monkeypatch):
    """Counts render_body calls (the expensive part the cache saves)."""
    calls: list[bool] = []
    real = render.render_body

    def counting(raw, mid, allow=False, base=None, keep=False, deps=None):
        calls.append(allow)
        return real(raw, mid, allow, base, keep, deps)

    monkeypatch.setattr(render, "render_body", counting)
    bodies.clear()
    yield calls
    bodies.clear()


async def test_a_second_open_is_served_from_the_cache(client, user, renders):
    mid = await _ingest(user, f'<p>Hello <b>cache</b></p><img src="{IMG}" alt="Hero">')
    first = await client.get(f"/api/v1/messages/{mid}/body")
    assert first.status_code == 200 and len(renders) == 1
    second = await client.get(f"/api/v1/messages/{mid}/body")
    assert second.status_code == 200 and len(renders) == 1
    assert second.json() == first.json()
    # The route's own fields are worked out per request, on top of the cached render.
    assert second.json()["junk"] is False and "Hello" in second.json()["html"]


async def test_another_user_gets_404_for_a_cached_body(app, client, user, renders):
    from mailr.core.users import create_user
    from mailr.db import sessionmaker

    mid = await _ingest(user, "<p>Owner only: the cached secret</p>")
    for images in ("blocked", "allowed"):
        r = await client.get(f"/api/v1/messages/{mid}/body", params={"images": images})
        assert r.status_code == 200 and "cached secret" in r.text
    n = len(renders)
    async with sessionmaker()() as db:
        other = await create_user(db, f"o-{uuid.uuid4().hex[:8]}@example.com", "Other", password="test-password-123")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c2:
        r = await c2.post("/api/v1/auth/login", json={"email": other.email, "password": "test-password-123"})
        assert r.status_code == 200
        for images in ("blocked", "allowed"):
            r = await c2.get(f"/api/v1/messages/{mid}/body", params={"images": images})
            assert r.status_code == 404 and "cached secret" not in r.text
            r = await c2.get(f"/api/v1/messages/{mid}/body-highlight", params={"q": "secret", "images": images})
            assert r.status_code == 404 and "cached secret" not in r.text
    assert len(renders) == n  # refused before the cache was asked


async def test_a_deleted_message_is_not_served_from_the_cache(client, user, renders):
    from mailr.db import sessionmaker
    from mailr.models import Message

    mid = await _ingest(user, "<p>Soon gone</p>")
    assert (await client.get(f"/api/v1/messages/{mid}/body")).status_code == 200
    async with sessionmaker()() as db:
        (await db.get(Message, mid)).deleted = True
        await db.commit()
    assert (await client.get(f"/api/v1/messages/{mid}/body")).status_code == 404


async def test_images_mode_is_part_of_the_key(client, user, renders):
    mid = await _ingest(user, f'<p>Pictures</p><img src="{IMG}" alt="Hero">')
    blocked = (await client.get(f"/api/v1/messages/{mid}/body", params={"images": "blocked"})).json()
    allowed = (await client.get(f"/api/v1/messages/{mid}/body", params={"images": "allowed"})).json()
    assert renders == [False, True]
    h = imageproxy.url_hash(IMG)
    assert f"/images/{h}" not in blocked["html"] and blocked["remote_images"]
    assert f"/images/{h}" in allowed["html"] and allowed["proxied"]
    # Each mode now comes from its own entry, unchanged.
    assert (await client.get(f"/api/v1/messages/{mid}/body", params={"images": "blocked"})).json() == blocked
    assert (await client.get(f"/api/v1/messages/{mid}/body", params={"images": "allowed"})).json() == allowed
    assert renders == [False, True]


async def test_a_highlight_never_reaches_the_cached_body(client, user, renders):
    mid = await _ingest(user, "<p>Find the quoted words here</p>")
    hl = (await client.get(f"/api/v1/messages/{mid}/body-highlight", params={"q": "quoted words"})).json()
    assert hl["highlighted"] and "mailr-hl" in hl["html"]
    plain = (await client.get(f"/api/v1/messages/{mid}/body")).json()
    assert "mailr-hl" not in plain["html"] and len(renders) == 1


async def test_a_changed_image_state_renders_again(client, user, renders, monkeypatch):
    mid = await _ingest(user, f'<p>Logo below</p><img src="{IMG}" alt="Brand logo">')
    h = imageproxy.url_hash(IMG)
    first = (await client.get(f"/api/v1/messages/{mid}/body", params={"images": "allowed"})).json()
    assert f"/images/{h}" in first["html"]
    # The proxy then fails to fetch the image: the next open shows its alt text instead.
    imageproxy._store_entry(h, imageproxy.Entry(status="failed", at=time.time()))
    try:
        again = (await client.get(f"/api/v1/messages/{mid}/body", params={"images": "allowed"})).json()
        assert len(renders) == 2 and f"/images/{h}" not in again["html"] and "Brand logo" in again["html"]
        # Unchanged since: from the cache.
        await client.get(f"/api/v1/messages/{mid}/body", params={"images": "allowed"})
        assert len(renders) == 2
    finally:
        imageproxy._entry_path(h).unlink(missing_ok=True)


def test_the_cache_is_bounded_and_ages_out(monkeypatch):
    c = BodyCache(max_bytes=16 * 1024, max_age=60)
    out = {"html": "x" * 900}
    for i in range(20):
        c.put(c.key(i, "b", False, None), out, {})
    assert c.size <= 16 * 1024 and c.get(c.key(0, "b", False, None)) is None
    assert c.get(c.key(19, "b", False, None)) == out
    # A copy: changing what was served leaves the entry alone.
    c.get(c.key(19, "b", False, None))["html"] = "changed"
    assert c.get(c.key(19, "b", False, None)) == out
    # Too large for the cache: not kept.
    c.put(c.key(99, "b", False, None), {"html": "y" * 4096}, {})
    assert c.get(c.key(99, "b", False, None)) is None
    # Old entries are dropped.
    now = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: now + 61)
    assert c.get(c.key(19, "b", False, None)) is None
    # The render version and the blob are in the key.
    assert c.key(1, "a", False, None) != c.key(1, "b", False, None)
    assert c.key(1, "a", False, None)[0] == render.RENDER_VERSION
