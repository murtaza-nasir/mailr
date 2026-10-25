# ACT-SURFACES: api
"""The account picture (core/account_picture.py, api/account_picture_routes.py): upload and
removal (settings.picture_upload, settings.picture_remove, settings.picture_use_sign_in), the
sign-in provider's picture, the fallback order, who sees which picture, token scopes, and the
publishing helpers (settings.picture_publish_check, settings.picture_bimi_check)."""

import io
import time
import uuid

import httpx
import pytest
from authlib.jose import JsonWebKey, jwt
from httpx import ASGITransport, AsyncClient
from PIL import Image

ISSUER = "https://id.example.test"
CLIENT = "mailr-android-test"


def png(color=(200, 30, 30), size=(64, 64), fmt="PNG", **save) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, fmt, **save)
    return out.getvalue()


def pixel(data: bytes) -> tuple:
    return Image.open(io.BytesIO(data)).convert("RGB").getpixel((5, 5))


async def _identity(user_id: int, email: str) -> int:
    from mailr.db import sessionmaker
    from mailr.models import Identity

    async with sessionmaker()() as db:
        i = Identity(user_id=user_id, email=email.lower(), name="")
        db.add(i)
        await db.commit()
        return i.id


async def _prove(user_id: int, email: str) -> None:
    """A mail account of the user that signs in as `email` and has synced: the identity is
    proven (account_picture.proven_identities)."""
    from datetime import UTC, datetime

    from mailr.db import sessionmaker
    from mailr.models import Connector

    async with sessionmaker()() as db:
        db.add(Connector(user_id=user_id, kind="imap", name="acct", config={"imap": {"username": email}},
                         enabled=True, last_sync_at=datetime.now(UTC)))
        await db.commit()


async def _correspondent(user_id: int, email: str) -> None:
    from mailr.db import sessionmaker
    from mailr.models import Person

    async with sessionmaker()() as db:
        db.add(Person(user_id=user_id, email=email.lower(), name=""))
        await db.commit()


async def _other_user(app, email_prefix="fam"):
    from mailr.core.users import create_user
    from mailr.db import sessionmaker

    async with sessionmaker()() as db:
        u = await create_user(db, f"{email_prefix}-{uuid.uuid4().hex[:8]}@example.com", "Family Member",
                              password="test-password-123")
    c = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    r = await c.post("/api/v1/auth/login", json={"email": u.email, "password": "test-password-123"})
    c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return u, c


@pytest.fixture
def issuer_settings(monkeypatch):
    from mailr.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "oidc_issuer", ISSUER)
    monkeypatch.setattr(s, "oidc_picture_hosts", "")
    return s


@pytest.fixture
def fake_net(monkeypatch):
    """The pinned client and the URL check replaced: requests go to `routes` (url → response
    factory) and every request and check is recorded."""
    from mailr.core import netguard

    seen = {"requests": [], "checks": []}
    routes: dict[str, callable] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["requests"].append(req)
        key = str(req.url)
        if key in routes:
            return routes[key](req)
        return httpx.Response(404)

    def client(**kw):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False,
                                 headers=kw.get("headers"))

    async def check_url(url, public_only=False, private_ok=None):
        seen["checks"].append((url, public_only, private_ok))

    monkeypatch.setattr(netguard, "guarded_client", client)
    monkeypatch.setattr(netguard, "check_url", check_url)
    return routes, seen


# ---------------------------------------------------------------- upload, limits, EXIF


async def test_upload_replace_remove_and_info(client, user):
    r = await client.put("/api/v1/me/picture", files={"file": ("me.png", png(), "image/png")})
    assert r.status_code == 200, r.text
    assert r.json()["has"] and r.json()["source"] == "upload"
    got = await client.get("/api/v1/me/picture")
    assert got.status_code == 200 and got.headers["content-type"] == "image/png"
    assert pixel(got.content) == (200, 30, 30)
    me = (await client.get("/api/v1/me")).json()
    assert me["picture"]["has"] and me["picture"]["version"] > 0
    # Replace (settings.picture_upload again), then remove (settings.picture_remove).
    await client.put("/api/v1/me/picture", files={"file": ("me.png", png((10, 200, 10)), "image/png")})
    assert pixel((await client.get("/api/v1/me/picture")).content) == (10, 200, 10)
    r = await client.delete("/api/v1/me/picture")
    assert r.status_code == 200 and not r.json()["has"]
    assert (await client.get("/api/v1/me/picture")).status_code == 404


async def test_size_and_type_limits(client):
    big = b"\x89PNG\r\n\x1a\n" + b"0" * (2 * 1024 * 1024 + 10)
    r = await client.put("/api/v1/me/picture", files={"file": ("big.png", big, "image/png")})
    assert r.status_code == 413
    r = await client.put("/api/v1/me/picture", files={"file": ("x.png", b"not an image at all", "image/png")})
    assert r.status_code == 400
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>'
    r = await client.put("/api/v1/me/picture", files={"file": ("x.svg", svg, "image/svg+xml")})
    assert r.status_code == 400
    truncated = png()[:60]
    r = await client.put("/api/v1/me/picture", files={"file": ("t.png", truncated, "image/png")})
    assert r.status_code == 400


async def test_upload_is_reencoded_without_exif(client):
    exif = Image.Exif()
    exif[0x010F] = "CameraMaker"
    exif.get_ifd(0x8825)[2] = (51.0, 30.0, 0.0)  # GPS latitude
    jpeg = png((0, 0, 255), size=(1000, 600), fmt="JPEG", exif=exif.tobytes())
    assert b"CameraMaker" in jpeg and Image.open(io.BytesIO(jpeg)).getexif().get_ifd(0x8825)
    r = await client.put("/api/v1/me/picture", files={"file": ("p.jpg", jpeg, "image/jpeg")})
    assert r.status_code == 200, r.text
    out = (await client.get("/api/v1/me/picture")).content
    assert out.startswith(b"\x89PNG") and b"CameraMaker" not in out and b"Exif" not in out
    im = Image.open(io.BytesIO(out))
    assert max(im.size) == 256 and not im.getexif()


# ---------------------------------------------------------------- the provider's picture


def test_picture_url_kind_only_issuer_host(issuer_settings, monkeypatch):
    from mailr.core.account_picture import picture_url_kind

    assert picture_url_kind(ISSUER + "/api/users/42/profile-picture.png") == "issuer"
    assert picture_url_kind("https://ID.example.test:443/api/users/42/profile-picture.png") == "issuer"
    assert picture_url_kind("http://id.example.test/api/users/42/profile-picture.png") is None
    assert picture_url_kind("https://id.example.test:8443/p.png") is None
    assert picture_url_kind("https://evil.example/p.png") is None
    assert picture_url_kind("https://id.example.test.evil.example/p.png") is None
    assert picture_url_kind("https://user:pw@id.example.test/p.png") is None
    assert picture_url_kind("data:image/png;base64,AAAA") is None
    monkeypatch.setattr(issuer_settings, "oidc_picture_hosts", "lh3.googleusercontent.com")
    assert picture_url_kind("https://lh3.googleusercontent.com/a/x") == "listed"


async def test_idp_picture_fetched_only_from_issuer_host(user, issuer_settings, fake_net):
    from mailr.core import account_picture as ap

    routes, seen = fake_net
    routes["https://evil.example/p.png"] = lambda req: httpx.Response(200, content=png(), headers={"content-type": "image/png"})
    assert await ap.refresh_from_idp(user.id, {"picture": "https://evil.example/p.png"}) == "refused"
    assert seen["requests"] == []
    url = ISSUER + "/api/users/1/profile-picture.png"
    routes[url] = lambda req: httpx.Response(200, content=png((1, 2, 3)), headers={"content-type": "image/png"})
    assert await ap.refresh_from_idp(user.id, {"picture": url}) == "stored"
    # The issuer's host may be on the LAN: the check allows private addresses for it.
    assert seen["checks"][-1] == (url, False, True)
    assert ap.info(user.id)["source"] == "idp" and pixel(ap.current(user.id)[0]) == (1, 2, 3)
    # A redirect to another host is not followed.
    url2 = ISSUER + "/api/users/2/profile-picture.png"
    routes[url2] = lambda req: httpx.Response(302, headers={"location": "https://evil.example/p.png"})
    n = len(seen["requests"])
    assert await ap.fetch_idp_picture(url2) is None
    assert len(seen["requests"]) == n + 1


async def test_idp_picture_uses_access_token_only_when_needed(user, issuer_settings, fake_net):
    from mailr.core import account_picture as ap

    routes, seen = fake_net
    url = ISSUER + "/api/users/3/profile-picture.png"

    def needs_auth(req):
        if req.headers.get("authorization") != "Bearer at-123":
            return httpx.Response(401)
        return httpx.Response(200, content=png(), headers={"content-type": "image/png"})

    routes[url] = needs_auth
    assert await ap.fetch_idp_picture(url) is None
    got = await ap.fetch_idp_picture(url, "at-123")
    assert got is not None
    assert [r.headers.get("authorization") for r in seen["requests"]] == [None, None, "Bearer at-123"]


async def test_idp_picture_size_and_type(user, issuer_settings, fake_net):
    from mailr.core import account_picture as ap

    routes, _ = fake_net
    url = ISSUER + "/big.png"
    routes[url] = lambda req: httpx.Response(200, content=b"\x89PNG\r\n\x1a\n" + b"0" * (3 * 1024 * 1024),
                                             headers={"content-type": "image/png"})
    assert await ap.fetch_idp_picture(url) is None
    url = ISSUER + "/page"
    routes[url] = lambda req: httpx.Response(200, content=b"<html></html>", headers={"content-type": "text/html"})
    assert await ap.fetch_idp_picture(url) is None
    url = ISSUER + "/logo.svg"
    routes[url] = lambda req: httpx.Response(200, content=b"<svg></svg>", headers={"content-type": "image/svg+xml"})
    assert await ap.fetch_idp_picture(url) is None


async def test_upload_wins_over_idp_picture(client, user, issuer_settings, fake_net):
    from mailr.core import account_picture as ap

    routes, seen = fake_net
    url = ISSUER + "/api/users/4/profile-picture.png"
    routes[url] = lambda req: httpx.Response(200, content=png((9, 9, 9)), headers={"content-type": "image/png"})
    await client.put("/api/v1/me/picture", files={"file": ("me.png", png((200, 30, 30)), "image/png")})
    assert await ap.refresh_from_idp(user.id, {"picture": url}) == "kept"
    assert seen["requests"] == []
    assert ap.info(user.id)["source"] == "upload" and ap.info(user.id)["idp_available"]
    # "Use my sign-in picture" (settings.picture_use_sign_in) replaces it on request only.
    r = await client.post("/api/v1/me/picture/use-sign-in")
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "idp" and pixel(ap.current(user.id)[0]) == (9, 9, 9)
    # After "Remove" the provider's picture does not come back at the next sign-in.
    await client.delete("/api/v1/me/picture")
    assert await ap.refresh_from_idp(user.id, {"picture": url}) == "declined"
    assert ap.current(user.id) is None


async def test_idp_refetch_only_when_url_changes_or_daily(user, issuer_settings, fake_net):
    from mailr.core import account_picture as ap

    routes, seen = fake_net
    a, b = ISSUER + "/a.png", ISSUER + "/b.png"
    for u in (a, b):
        routes[u] = lambda req: httpx.Response(200, content=png(), headers={"content-type": "image/png"})
    assert await ap.refresh_from_idp(user.id, {"picture": a}) == "stored"
    assert await ap.refresh_from_idp(user.id, {"picture": a}) == "fresh"
    assert len(seen["requests"]) == 1
    assert await ap.refresh_from_idp(user.id, {"picture": b}) == "stored"
    meta = ap.read_meta(user.id)
    meta["idp_fetched_at"] = time.time() - ap.REFRESH_AFTER - 5
    ap._write_meta(user.id, meta)
    assert await ap.refresh_from_idp(user.id, {"picture": b}) == "stored"
    assert len(seen["requests"]) == 3


async def test_native_sign_in_stores_idp_picture(app, issuer_settings, fake_net, monkeypatch):
    from helpers.native_auth import SEED, _nonce, _own_origin

    from mailr.api import native_auth

    key = JsonWebKey.generate_key("RSA", 2048, is_private=True, options={"kid": "k1"})
    monkeypatch.setattr(issuer_settings, "oidc_native_client_id", CLIENT)
    monkeypatch.setattr(issuer_settings, "oidc_auto_create", True)

    async def discovery():
        return {"issuer": ISSUER, "jwks_uri": ISSUER + "/jwks"}

    async def fetch_jwks(uri, refresh=False):
        return {"keys": [key.as_dict(is_private=False)]}

    monkeypatch.setattr(native_auth, "_discovery", discovery)
    monkeypatch.setattr(native_auth, "_jwks", fetch_jwks)
    routes, _ = fake_net
    url = ISSUER + "/api/users/77/profile-picture.png"
    routes[url] = lambda req: httpx.Response(200, content=png((4, 5, 6)), headers={"content-type": "image/png"})
    now = int(time.time())
    token = jwt.encode({"alg": "RS256", "kid": "k1"}, {
        "iss": ISSUER, "aud": CLIENT, "sub": f"sub-{uuid.uuid4().hex[:8]}", "email": f"n-{uuid.uuid4().hex[:8]}@example.com",
        "email_verified": True, "name": "Native User", "iat": now, "exp": now + 300, "picture": url,
        "nonce": _nonce(_own_origin(), SEED)}, key).decode()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/v1/auth/oidc/native", json={"id_token": token, "nonce_seed": SEED})
        assert r.status_code == 200, r.text
        c.cookies.set("mailr_session", r.json()["session_token"])
        me = (await c.get("/api/v1/me")).json()
        assert me["picture"]["source"] == "idp"
        pic = await c.get("/api/v1/me/picture")
        assert pixel(pic.content) == (4, 5, 6)


# ---------------------------------------------------------------- fallback and visibility


async def test_fallback_identity_then_account_then_initials(client, user):
    addr = f"me-{uuid.uuid4().hex[:6]}@example.net"
    iid = await _identity(user.id, addr)
    assert (await client.get(f"/api/v1/avatars/{addr}")).status_code == 404  # initials
    await client.put("/api/v1/me/picture", files={"file": ("a.png", png((50, 50, 50)), "image/png")})
    r = await client.get(f"/api/v1/avatars/{addr}")
    assert r.status_code == 200 and pixel(r.content) == (50, 50, 50)
    await client.put(f"/api/v1/identities/{iid}/avatar", files={"file": ("i.png", png((0, 120, 0)), "image/png")})
    r = await client.get(f"/api/v1/avatars/{addr}")
    assert pixel(r.content) == (0, 120, 0)


async def test_family_sees_pictures_only_for_identity_addresses(app, client, user):
    from mailr.core.account_picture import address_key
    from mailr.db import sessionmaker
    from mailr.models import User

    other, oc = await _other_user(app)
    try:
        theirs = f"fam-{uuid.uuid4().hex[:6]}@example.org"
        await _identity(other.id, theirs)
        await oc.put("/api/v1/me/picture", files={"file": ("o.png", png((7, 70, 140)), "image/png")})
        # An identity alone is only a claim: no picture for others until it is proven.
        assert (await client.get(f"/api/v1/avatars/{theirs}")).status_code == 404
        await _prove(other.id, theirs)
        await _correspondent(user.id, theirs)
        r = await client.get(f"/api/v1/avatars/{theirs}")
        assert r.status_code == 200 and pixel(r.content) == (7, 70, 140)
        keys = (await client.get("/api/v1/pictures/local")).json()["keys"]
        assert address_key(theirs) in keys and theirs not in str(keys)
        # Their sign-in address is not one of their identities here: no picture for it, nor
        # for any other address.
        assert (await client.get(f"/api/v1/avatars/{other.email}")).status_code == 404
        assert (await client.get("/api/v1/avatars/stranger@example.org")).status_code == 404
        # A disabled user's picture is not shown.
        async with sessionmaker()() as db:
            (await db.get(User, other.id)).disabled = True
            await db.commit()
        assert (await client.get(f"/api/v1/avatars/{theirs}")).status_code == 404
    finally:
        await oc.aclose()


async def test_token_scopes(app, client, user):
    await client.put("/api/v1/me/picture", files={"file": ("a.png", png(), "image/png")})

    async def token(scopes):
        r = await client.post("/api/v1/tokens", json={"name": "t", "scopes": scopes})
        assert r.status_code == 200, r.text
        return r.json()["token"]

    profile, calendar = await token(["profile"]), await token(["calendar"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        h = {"Authorization": f"Bearer {profile}"}
        assert (await c.get("/api/v1/users/me/avatar", headers=h)).status_code == 200
        assert (await c.get("/api/v1/me/picture", headers=h)).status_code == 200
        assert (await c.get(f"/api/v1/avatars/{user.email}", headers=h)).status_code == 403
        assert (await c.get("/api/v1/pictures/local", headers=h)).status_code == 403
        assert (await c.put("/api/v1/me/picture", headers=h, files={"file": ("a.png", png(), "image/png")})).status_code == 403
        assert (await c.delete("/api/v1/me/picture", headers=h)).status_code == 403
        h = {"Authorization": f"Bearer {calendar}"}
        assert (await c.get("/api/v1/users/me/avatar", headers=h)).status_code == 403


# ---------------------------------------------------------------- BIMI / DMARC


def test_bimi_dmarc_parser_fixtures():
    from mailr.core.picture_checks import evaluate, org_domain, parse_tags, pick

    assert parse_tags("v=BIMI1; l=https://example.com/l.svg; a=https://example.com/vmc.pem") == {
        "v": "BIMI1", "l": "https://example.com/l.svg", "a": "https://example.com/vmc.pem"}
    assert pick(["v=spf1 -all", "v=DMARC1; p=reject"], "DMARC1") == "v=DMARC1; p=reject"
    assert pick(["v=DMARC1; p=reject", "v=DMARC1; p=none"], "DMARC1") is None  # two records: invalid
    assert org_domain("mail.example.co.uk") == "example.co.uk" and org_domain("a.b.example.com") == "example.com"
    good_logo = {"type": "image/svg+xml", "size": 4000, "tiny_ps": True}
    ok = {r["key"]: r["met"] for r in evaluate(
        "example.com", "v=BIMI1; l=https://example.com/l.svg; a=https://example.com/vmc.pem",
        "v=DMARC1; p=reject; rua=mailto:d@example.com", False, good_logo)}
    assert ok == {"record": True, "dmarc": True, "logo_https": True, "logo_svg": True, "certificate": True}
    r = {x["key"]: x for x in evaluate("example.com", "v=BIMI1; l=http://example.com/l.svg;",
                                       "v=DMARC1; p=quarantine; pct=50", False, None)}
    assert r["dmarc"]["met"] is False and "pct=50" in r["dmarc"]["detail"]
    assert r["logo_https"]["met"] is False and r["certificate"]["met"] is False and r["certificate"]["next"]
    r = {x["key"]: x for x in evaluate("example.com", None, "v=DMARC1; p=none", False, None)}
    assert r["record"]["met"] is False and "default._bimi.example.com" in r["record"]["next"]
    assert r["dmarc"]["met"] is False and r["logo_svg"]["met"] is None
    # A subdomain under the organisation's record: sp= decides.
    r = {x["key"]: x["met"] for x in evaluate("news.example.com", "v=BIMI1; l=https://x.example/l.svg; a=",
                                              "v=DMARC1; p=reject; sp=none", True, good_logo)}
    assert r["dmarc"] is False and r["certificate"] is False
    bad_logo = {"type": "image/png", "size": 50_000, "tiny_ps": False}
    r = {x["key"]: x for x in evaluate("example.com", "v=BIMI1; l=https://example.com/l.png",
                                       "v=DMARC1; p=reject", False, bad_logo)}
    assert r["logo_svg"]["met"] is False and "image/png" in r["logo_svg"]["detail"]


async def _make_admin(user_id: int) -> None:
    from mailr.db import sessionmaker
    from mailr.models import User

    async with sessionmaker()() as db:
        (await db.get(User, user_id)).role = "admin"
        await db.commit()


async def test_bimi_check_is_admin_only(client, user, monkeypatch):
    """settings.picture_bimi_check: a user who is not an admin gets 403, before any lookup."""
    from mailr.core import egress

    async def txt(name, eg, timeout=3.0):
        raise AssertionError("no lookup for a non-admin")

    monkeypatch.setattr(egress, "txt_records", txt)
    own = await _identity(user.id, f"me{uuid.uuid4().hex[:6]}@brand.example")
    r = await client.post("/api/v1/me/picture/bimi-check", json={"identity_id": own})
    assert r.status_code == 403 and "admin" in r.json()["detail"]


async def test_bimi_check_shared_domain_and_lookup(app, client, user, monkeypatch):
    from mailr.core import egress, picture_checks

    await _make_admin(user.id)
    gmail = await _identity(user.id, f"x{uuid.uuid4().hex[:6]}@gmail.com")
    r = await client.post("/api/v1/me/picture/bimi-check", json={"identity_id": gmail})
    assert r.status_code == 200 and r.json()["applicable"] is False and "don't control" in r.json()["reason"]
    own = await _identity(user.id, f"me{uuid.uuid4().hex[:6]}@brand.example")
    asked = []

    async def txt(name, eg, timeout=3.0):
        asked.append((name, eg))
        return {"default._bimi.brand.example": ["v=BIMI1; l=https://brand.example/l.svg; a=https://brand.example/v.pem"],
                "_dmarc.brand.example": ["v=DMARC1; p=quarantine"]}.get(name, [])

    async def logo(url, eg):
        return {"type": "image/svg+xml", "size": 2048, "tiny_ps": True}

    monkeypatch.setattr(egress, "txt_records", txt)
    monkeypatch.setattr(picture_checks, "_logo", logo)
    r = await client.post("/api/v1/me/picture/bimi-check", json={"identity_id": own})
    assert r.status_code == 200, r.text
    assert r.json()["ready"] is True and ("_dmarc.brand.example", "") in asked
    other, oc = await _other_user(app)
    await _make_admin(other.id)
    try:
        assert (await oc.post("/api/v1/me/picture/bimi-check", json={"identity_id": own})).status_code == 404
    finally:
        await oc.aclose()


async def test_gravatar_check_goes_through_the_egress(app, client, user, monkeypatch):
    from mailr.core import egress, picture_checks

    calls = {"clients": [], "checks": []}
    found = picture_checks.hashlib.sha256(user.email.lower().encode()).hexdigest()

    def handler(req):
        if req.url.host == "gravatar.com" and found in str(req.url):
            return httpx.Response(200, content=png(), headers={"content-type": "image/png"})
        return httpx.Response(404)

    def fake_client(eg, **kw):
        calls["clients"].append(eg)
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)

    async def fake_check(url, eg, **kw):
        calls["checks"].append((url, eg))

    async def fake_configured(db=None):
        return "http://egress.example:8888"

    monkeypatch.setattr(egress, "client", fake_client)
    monkeypatch.setattr(egress, "check_url", fake_check)
    monkeypatch.setattr(egress, "configured", fake_configured)
    await _identity(user.id, user.email)
    r = await client.post("/api/v1/me/picture/publish-check")
    assert r.status_code == 200, r.text
    item = next(i for i in r.json()["items"] if i["email"] == user.email.lower())
    assert item["gravatar"] == "found" and item["libravatar"] == "not_found"
    assert calls["clients"] and set(calls["clients"]) == {"http://egress.example:8888"}
    assert all(eg == "http://egress.example:8888" for _, eg in calls["checks"])
    # identity_ids limits the check: the Gmail address is not sent to Gravatar or Libravatar.
    gmail_addr = f"x{uuid.uuid4().hex[:6]}@gmail.com"
    await _identity(user.id, gmail_addr)
    own = await _identity(user.id, f"me{uuid.uuid4().hex[:6]}@brand.example")
    calls["checks"].clear()
    r = await client.post("/api/v1/me/picture/publish-check", json={"identity_ids": [own]})
    assert r.status_code == 200, r.text
    assert [i["identity_id"] for i in r.json()["items"]] == [own]
    gmail_hash = picture_checks.hashlib.sha256(gmail_addr.encode()).hexdigest()
    assert calls["checks"] and not any(gmail_hash in u for u, _ in calls["checks"])
    # An API token cannot run it (it tells third parties about the addresses).
    tok = (await client.post("/api/v1/tokens", json={"name": "t", "scopes": ["read"]})).json()["token"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/v1/me/picture/publish-check", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 403


def test_provider_detection():
    from types import SimpleNamespace

    from mailr.core.picture_checks import provider_for

    assert provider_for("a@gmail.com", None) == "google"
    assert provider_for("a@company.example", SimpleNamespace(kind="gmail", preset=None, config={})) == "google"
    assert provider_for("a@company.example", SimpleNamespace(kind="m365", preset=None, config={})) == "microsoft"
    assert provider_for("a@company.example", SimpleNamespace(kind="imap", preset="", config={"imap": {"host": "outlook.office365.com"}})) == "microsoft"
    assert provider_for("a@hotmail.com", None) == "microsoft"
    assert provider_for("a@company.example", SimpleNamespace(kind="imap", preset="purelymail", config={"imap": {"host": "imap.purelymail.com"}})) is None


async def _rows_read(fn) -> int:
    """How many rows the database handed back while `fn` ran (every SELECT's result)."""
    from sqlalchemy import event

    from mailr.db import engine

    n = 0

    def count(conn, cursor, statement, parameters, context, executemany):
        nonlocal n
        if statement.lstrip().upper().startswith(("SELECT", "WITH")) and cursor.rowcount and cursor.rowcount > 0:
            n += cursor.rowcount

    sync = engine().sync_engine
    event.listen(sync, "after_cursor_execute", count)
    try:
        await fn()
    finally:
        event.remove(sync, "after_cursor_execute", count)
    return n


async def test_proven_identities_cost_follows_the_viewer_not_the_instance(user):
    """A page load (local_map) and an avatar (for_address) read the identities the viewer can
    ask about, not every identity of the instance (they used to: 7,600 e2e users made /health
    take seconds)."""
    from conftest import make_db_user

    from mailr.core import account_picture as ap
    from mailr.db import sessionmaker

    tag = uuid.uuid4().hex[:6]
    # The crowd only needs user rows: no store accounts (make_db_user), no model access.
    others = [await make_db_user(f"crowd-{tag}-{i}@example.com", "Crowd", password="", model_access=False)
              for i in range(120)]
    for i, o in enumerate(others):
        await _identity(o.id, f"crowd-{tag}-{i}@crowd{tag}.example")
        await _prove(o.id, f"crowd-{tag}-{i}@crowd{tag}.example")
    friend = f"crowd-{tag}-3@crowd{tag}.example"
    await _correspondent(user.id, friend)

    async def page_load():
        async with sessionmaker()() as db:
            await ap.local_map(db, user.id)

    async def avatar():
        async with sessionmaker()() as db:
            await ap.for_address(db, friend, user.id)

    assert await _rows_read(page_load) < 30
    assert await _rows_read(avatar) < 10


async def test_proven_identities_filters_match_the_full_list(app, user):
    """`emails` and `known_to` give exactly the rows of the full list for those addresses, so
    for_address and local_map decide as before: proven by a synced login, by an alias on the
    login's own domain (not on a shared provider), by a verified sign-in address; not a bare
    claim, not a disabled user; two owners of one address both listed, oldest first."""
    from mailr.core import account_picture as ap
    from mailr.db import sessionmaker
    from mailr.models import User

    tag = uuid.uuid4().hex[:6]
    a, ac = await _other_user(app, f"eqa-{tag}")
    b, bc = await _other_user(app, f"eqb-{tag}")
    c, cc = await _other_user(app, f"eqc-{tag}")
    try:
        login, alias = f"login-{tag}@own{tag}.example", f"alias-{tag}@own{tag}.example"
        shared_login, shared_alias = f"x-{tag}@gmail.com", f"y-{tag}@gmail.com"
        claim, both = f"claim-{tag}@else{tag}.example", f"both-{tag}@own{tag}.example"
        for addr in (login, alias, both):
            await _identity(a.id, addr)
        await _prove(a.id, login)
        await _identity(b.id, shared_alias)
        await _prove(b.id, shared_login)
        await _identity(b.id, claim)
        await _identity(b.id, both)
        await _prove(b.id, f"other-{tag}@own{tag}.example")
        await _identity(c.id, c.email)  # the verified sign-in address
        gone = f"gone-{tag}@own{tag}.example"
        await _identity(c.id, gone)
        await _prove(c.id, gone)
        async with sessionmaker()() as db:
            (await db.get(User, c.id)).disabled = True
            await db.commit()
        mine = f"me-{tag}@mine{tag}.example"
        await _identity(user.id, mine)
        for addr in (login, alias, shared_alias, claim, both, gone):
            await _correspondent(user.id, addr)
        addrs = [login, alias, shared_alias, claim, both, c.email, gone, mine, "nobody@example.org"]

        async with sessionmaker()() as db:
            full = await ap.proven_identities(db, user.id)
            for addr in addrs:
                assert await ap.proven_identities(db, user.id, emails=[addr.upper()]) == [r for r in full if r[2] == addr]
            known = {mine, login, alias, shared_alias, claim, both, gone}
            assert await ap.proven_identities(db, user.id, known_to=user.id) == [r for r in full if r[2] in known]
            got = {r[2] for r in await ap.proven_identities(db, user.id, known_to=user.id)}
        assert got == {mine, login, alias, both}
        assert [r[1] for r in full if r[2] == both] == [a.id, b.id]
    finally:
        for x in (ac, bc, cc):
            await x.aclose()
