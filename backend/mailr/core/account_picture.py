"""The account picture: one picture per mailr user.

Where it comes from:
- an upload in Settings (`source: "upload"`), which always wins;
- the sign-in provider's `picture` claim (`source: "idp"`), fetched on the server at sign-in
  while the user has not uploaded one and has not removed the provider's picture.

Where it shows: everywhere the user appears (top bar, own messages, assistant chat, "me" in
participant lists). A sending address (identity) shows its own picture when it has one and the
account picture otherwise. Other users of the same instance see it only for the addresses of
that user's proven identities (`proven_identities`, `for_address`); never for any other address.

Storage: `<data_dir>/avatars/user-<id>/account/picture` (the re-encoded PNG) and
`picture.json` (metadata: type, at, source, idp_url, idp_fetched_at, idp_tried_at, declined).
The metadata outlives the picture: after "Remove" it keeps `declined` (so the provider's
picture does not come back at the next sign-in) and the last provider URL (so "Use my sign-in
picture" works without a new sign-in).

Fetching the provider's picture (`refresh_from_idp`):
- https only, at most MAX_FETCH bytes, image types only (sniffed), re-encoded like an upload;
- only from the issuer's own host and port, or a host the admin lists in
  MAILR_OIDC_PICTURE_HOSTS. A URL in the claim pointing anywhere else is ignored: the claim is
  data from the provider, and mailr does not fetch arbitrary hosts because a token says so;
- the issuer's host may be on the LAN (the admin configured it), so that fetch may reach private
  addresses; loopback and mailr's own services stay refused (netguard; list the host in
  MAILR_OUTBOUND_ALLOW for an issuer on the same machine). Hosts from MAILR_OIDC_PICTURE_HOSTS
  are third parties and must be public;
- connections are pinned to the checked addresses (netguard.guarded_client); redirects are
  followed by hand, at most three, each checked again;
- without credentials first; on 401/403 from the issuer's host, once more with the access
  token of the sign-in that is running (web sign-in only; the token is never sent to another
  host and never stored). Pocket ID serves `/api/users/<id>/profile-picture.png` publicly;
- again on a later sign-in only when the URL changed or the copy is a day old (a failed fetch
  is retried after a day too).
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from mailr.config import get_settings

log = logging.getLogger("mailr.account_picture")

MAX_UPLOAD = 2 * 1024 * 1024
MAX_FETCH = 2 * 1024 * 1024
MAX_PIXELS = 16_000_000  # refuse decompression bombs before decoding (about 16 MP)
SIDE = 256
REFRESH_AFTER = 86400
FETCH_DEADLINE = 10.0
RASTER = {"image/png", "image/jpeg", "image/gif", "image/webp"}


class BadPicture(ValueError):
    """Not a usable picture. The message is safe to show."""


# ---------------------------------------------------------------- storage


def _dir(user_id: int) -> Path:
    return Path(get_settings().data_dir) / "avatars" / f"user-{int(user_id)}" / "account"


def picture_path(user_id: int) -> Path:
    return _dir(user_id) / "picture"


def _meta_path(user_id: int) -> Path:
    return _dir(user_id) / "picture.json"


def read_meta(user_id: int) -> dict[str, Any]:
    try:
        m = json.loads(_meta_path(user_id).read_text())
        return m if isinstance(m, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_meta(user_id: int, meta: dict[str, Any]) -> None:
    p = _meta_path(user_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp-json")
    tmp.write_text(json.dumps(meta))
    tmp.replace(p)


def current(user_id: int) -> tuple[bytes, str] | None:
    """The stored picture (bytes, content type), or None."""
    m = read_meta(user_id)
    f = picture_path(user_id)
    if not m.get("source") or not m.get("type") or not f.exists():
        return None
    return f.read_bytes(), m["type"]


def info(user_id: int) -> dict[str, Any]:
    """What the clients need: whether there is a picture, where it came from, a version for
    cache-busting, and whether a provider picture is known ("Use my sign-in picture")."""
    m = read_meta(user_id)
    has = bool(m.get("source")) and picture_path(user_id).exists()
    return {"has": has, "source": m.get("source") if has else None,
            "version": int(m.get("at") or 0) if has else 0,
            "idp_available": bool(m.get("idp_url"))}


def store(user_id: int, data: bytes, ctype: str, source: str, **extra: Any) -> None:
    f = picture_path(user_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(f)
    meta = read_meta(user_id)
    meta.update({"type": ctype, "at": time.time(), "source": source, **extra})
    meta.pop("declined", None)
    _write_meta(user_id, meta)


def remove(user_id: int) -> None:
    """Remove the picture. A provider picture does not come back by itself afterwards."""
    picture_path(user_id).unlink(missing_ok=True)
    meta = read_meta(user_id)
    for k in ("type", "source", "at", "idp_fetched_url", "idp_fetched_at"):
        meta.pop(k, None)
    meta["declined"] = True
    _write_meta(user_id, meta)


# ---------------------------------------------------------------- validation


def sniff(data: bytes) -> str | None:
    """The raster type by magic bytes (no SVG: an account picture is a photo)."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def reencode(data: bytes) -> tuple[bytes, str]:
    """Check and re-encode a picture: a real PNG/JPEG/GIF/WebP, not too many pixels, turned
    upright from its EXIF orientation, scaled to at most SIDE×SIDE and written as a new PNG, so
    no EXIF, GPS or other metadata of the original survives. BadPicture otherwise."""
    if len(data) > MAX_UPLOAD:
        raise BadPicture("pictures are limited to 2 MB")
    if sniff(data) is None:
        raise BadPicture("use a PNG, JPEG, GIF or WebP picture")
    from PIL import Image, ImageOps

    try:
        im = Image.open(io.BytesIO(data))
        w, h = im.size
        if w * h > MAX_PIXELS or w < 1 or h < 1:
            raise BadPicture("the picture is too large")
        if im.format == "JPEG":
            # Decode a JPEG at a reduced scale (1/2 to 1/8) when it is much larger than SIDE:
            # less memory and time than decoding every pixel only to throw most away.
            im.draft("RGB", (SIDE * 2, SIDE * 2))
        im = ImageOps.exif_transpose(im)
        im.thumbnail((SIDE, SIDE))
        out = io.BytesIO()
        im.convert("RGBA").save(out, "PNG", optimize=True)
    except BadPicture:
        raise
    except Exception:  # noqa: BLE001
        raise BadPicture("the picture could not be read") from None
    return out.getvalue(), "image/png"


async def reencode_async(data: bytes) -> tuple[bytes, str]:
    """`reencode` in a worker thread: decoding a large picture must not stall the event loop."""
    import asyncio

    return await asyncio.to_thread(reencode, data)


# ---------------------------------------------------------------- the provider's picture


def _hosts_extra() -> set[str]:
    raw = getattr(get_settings(), "oidc_picture_hosts", "") or ""
    return {h.strip().lower().rstrip(".") for h in raw.split(",") if h.strip()}


def _origin(url: str) -> tuple[str, str, int]:
    u = urlsplit(url)
    try:
        port = u.port
    except ValueError:
        port = -1
    scheme = (u.scheme or "").lower()
    return scheme, (u.hostname or "").lower().rstrip("."), port or (443 if scheme == "https" else 80)


def picture_url_kind(url: str) -> str | None:
    """"issuer" when the URL is https on the issuer's own host and port, "listed" when its host
    is in MAILR_OIDC_PICTURE_HOSTS, None when mailr does not fetch it."""
    if not isinstance(url, str) or not url or len(url) > 2000:
        return None
    u = urlsplit(url)
    if u.username or u.password:
        return None
    scheme, host, port = _origin(url)
    if scheme != "https" or not host or port < 0:
        return None
    issuer = get_settings().oidc_issuer or ""
    if issuer:
        ischeme, ihost, iport = _origin(issuer)
        if ischeme == "http":
            iport = 443  # an http issuer's host is still the host; the picture must be https
        if host == ihost and port == iport:
            return "issuer"
    if host in _hosts_extra():
        return "listed"
    return None


async def fetch_idp_picture(url: str, access_token: str | None = None) -> tuple[bytes, str] | None:
    """Fetch and re-encode the provider's picture, under the rules in the module doc. None when
    the URL is not allowed or the fetch fails in any way."""
    import asyncio

    from mailr.core.netguard import TooLarge, UnsafeTarget, check_url, fetch_capped, guarded_client

    kind = picture_url_kind(url)
    if kind is None:
        log.info("sign-in picture not fetched: not on the issuer's host or an allowed host")
        return None
    headers = {"User-Agent": "mailr account picture", "Accept": "image/png,image/jpeg,image/webp,image/gif"}

    async def get(u: str, k: str, token: str | None) -> tuple[httpx.Response, bytes]:
        # One pinned client per hop: the issuer's host may be private, a listed host must be public.
        public_only = k == "listed"
        await check_url(u, public_only=public_only, private_ok=not public_only)
        extra = {"Authorization": f"Bearer {token}"} if token else {}
        async with guarded_client(public_only=public_only, private_ok=not public_only, timeout=6,
                                  headers=headers) as c:
            return await fetch_capped(c, "GET", u, limit=MAX_FETCH, deadline=FETCH_DEADLINE, headers=extra)

    try:
        async with asyncio.timeout(FETCH_DEADLINE):
            r, body = await get(url, kind, None)
            if r.status_code in (401, 403) and access_token and kind == "issuer":
                r, body = await get(url, kind, access_token)
            hops = 0
            while r.is_redirect and r.headers.get("location") and hops < 3:
                url = urljoin(url, r.headers["location"])
                kind = picture_url_kind(url)
                if kind is None:
                    return None
                r, body = await get(url, kind, None)
                hops += 1
    except (httpx.HTTPError, UnsafeTarget, TooLarge, TimeoutError) as e:
        log.info("sign-in picture fetch failed: %s", type(e).__name__)
        return None
    if r.status_code != 200 or r.is_redirect:
        return None
    declared = r.headers.get("content-type", "").split(";")[0].strip().lower()
    if declared and declared not in RASTER and declared != "application/octet-stream":
        return None
    try:
        return reencode(body)
    except BadPicture:
        return None


async def userinfo_picture(access_token: str) -> str | None:
    """The `picture` claim from the provider's userinfo endpoint (web sign-in, when the ID token
    has none). The endpoint comes from the issuer's discovery document."""
    from mailr.api.auth_routes import _discovery
    from mailr.core.netguard import TooLarge, fetch_capped

    try:
        d = await _discovery()
        endpoint = d.get("userinfo_endpoint")
        if not endpoint or not str(endpoint).startswith(("https://", "http://")):
            return None
        async with httpx.AsyncClient(timeout=5, trust_env=False, follow_redirects=False) as c:
            r, body = await fetch_capped(c, "GET", endpoint, limit=64 * 1024, deadline=6,
                                         headers={"Authorization": f"Bearer {access_token}"})
        if r.status_code != 200:
            return None
        pic = json.loads(body).get("picture")
    except (httpx.HTTPError, TooLarge, TimeoutError, ValueError, AttributeError):
        return None
    except Exception:  # noqa: BLE001
        return None
    return pic if isinstance(pic, str) and pic else None


async def refresh_from_idp(user_id: int, claims: dict[str, Any], access_token: str | None = None,
                           *, force: bool = False) -> str:
    """Bring the account picture in line with the provider's after a sign-in. Returns what
    happened (for tests and logs): kept | declined | fresh | none | refused | failed | stored.
    Never raises: a picture never breaks a sign-in."""
    try:
        return await _refresh(user_id, claims, access_token, force=force)
    except Exception:
        log.exception("sign-in picture refresh failed")
        return "failed"


async def _refresh(user_id: int, claims: dict[str, Any], access_token: str | None, *, force: bool) -> str:
    url = claims.get("picture")
    if not isinstance(url, str) or not url:
        url = await userinfo_picture(access_token) if access_token else None
    meta = read_meta(user_id)
    if not url:
        return "none"
    if meta.get("idp_url") != url:
        meta["idp_url"] = url
        _write_meta(user_id, meta)
    if not force:
        if meta.get("source") == "upload":
            return "kept"
        if meta.get("declined"):
            return "declined"
        now = time.time()
        if (meta.get("source") == "idp" and meta.get("idp_fetched_url") == url
                and now - float(meta.get("idp_fetched_at") or 0) < REFRESH_AFTER and picture_path(user_id).exists()):
            return "fresh"
        if meta.get("idp_tried_url") == url and now - float(meta.get("idp_tried_at") or 0) < REFRESH_AFTER:
            return "fresh"  # failed within the day: do not retry on every sign-in
    if picture_url_kind(url) is None:
        return "refused"
    meta.update({"idp_tried_url": url, "idp_tried_at": time.time()})
    _write_meta(user_id, meta)
    got = await fetch_idp_picture(url, access_token)
    if got is None:
        return "failed"
    meta = read_meta(user_id)
    if meta.get("source") == "upload" and not force:
        return "kept"  # the user uploaded one while the fetch ran
    store(user_id, got[0], got[1], "idp", idp_fetched_url=url, idp_fetched_at=time.time())
    meta = read_meta(user_id)
    meta.pop("idp_tried_url", None)
    meta.pop("idp_tried_at", None)  # the day's wait is for failed fetches only
    _write_meta(user_id, meta)
    return "stored"


# ---------------------------------------------------------------- who sees which picture


def _identity_file(user_id: int, iid: int) -> Path:
    return Path(get_settings().data_dir) / "avatars" / f"user-{int(user_id)}" / "identities" / str(int(iid))


def identity_picture(user_id: int, iid: int) -> tuple[bytes, str] | None:
    f = _identity_file(user_id, iid)
    try:
        m = json.loads(f.with_suffix(".json").read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(m, dict) or not m.get("type") or not f.exists():
        return None
    return f.read_bytes(), m["type"]


def identity_version(user_id: int, iid: int) -> int:
    f = _identity_file(user_id, iid)
    try:
        m = json.loads(f.with_suffix(".json").read_text())
        return int(m.get("at") or 0) if f.exists() else 0
    except (OSError, ValueError, AttributeError):
        return 0


def _login_of(c) -> str:
    """The address a mail connector signs in with (its IMAP or SMTP user name), lower case, or ''."""
    cfg = c.config or {}
    for part in (cfg.get("imap") or {}, cfg.get("smtp") or {}):
        u = str(part.get("username") or "").strip().lower()
        if "@" in u:
            return u
    return ""


def _domain(email: str) -> str:
    return email.rpartition("@")[2].strip().lower()


async def proven_identities(db, viewer_id: int | None = None, *, emails: Iterable[str] | None = None,
                            known_to: int | None = None) -> list[tuple[int, int, str]]:
    """(identity id, user id, address) for every identity of an enabled user whose address the
    user has shown to be theirs, oldest identity first. `emails` limits it to those addresses and
    `known_to` to the addresses that user knows (their own identities and their correspondents):
    the work then grows with the caller, not with the instance (a page load used to read every
    identity of every user). The rules below are applied the same way either way. An identity is only a claim
    (POST /identities takes any address), so another user's picture is shown for an address
    only when one of these holds:
    - the address is the user's own sign-in address (set by the admin or the sign-in provider);
    - the user has a mail account (connector) that has synced at least once and signs in as
      that address, or as another address on the same domain when the domain is not a shared
      mailbox provider (an alias on a domain where the user holds a mailbox).
    The viewer's own identities always count for the viewer (they see only their own claim)."""
    from sqlalchemy import func, select

    from mailr.core.picture_checks import SHARED_DOMAINS
    from mailr.models import Connector, Identity, Person, User

    q = (select(Identity.id, Identity.user_id, Identity.email, User.email, User.email_verified)
         .join(User, User.id == Identity.user_id).where(User.disabled.is_(False)))
    if emails is not None:
        wanted = sorted({e.strip().lower() for e in emails if e})
        if not wanted:
            return []
        q = q.where(func.lower(Identity.email).in_(wanted))
    if known_to is not None:
        # ix_identities_lower_email answers each address from the user's own lists.
        own = (select(func.lower(Identity.email)).join(User, User.id == Identity.user_id)
               .where(Identity.user_id == known_to, User.disabled.is_(False)))
        people = select(func.lower(Person.email)).where(Person.user_id == known_to)
        q = q.where(func.lower(Identity.email).in_(own.union(people).scalar_subquery()))
    rows = (await db.execute(q.order_by(Identity.id))).all()
    users = {uid for _, uid, _, _, _ in rows}
    logins: dict[int, set[str]] = {}
    if users:
        for c in (await db.execute(select(Connector).where(
                Connector.user_id.in_(users), Connector.enabled.is_(True),
                Connector.last_sync_at.is_not(None)))).scalars():
            login = _login_of(c)
            if login:
                logins.setdefault(c.user_id, set()).add(login)
    out: list[tuple[int, int, str]] = []
    for iid, uid, email, user_email, verified in rows:
        e = (email or "").strip().lower()
        mine = logins.get(uid, set())
        proven = (
            uid == viewer_id
            or (verified and e == (user_email or "").strip().lower())
            or e in mine
            or (_domain(e) not in SHARED_DOMAINS and _domain(e) in {_domain(x) for x in mine})
        )
        if proven:
            out.append((iid, uid, e))
    return out


async def for_address(db, email: str, viewer_id: int | None = None) -> tuple[bytes, str] | None:
    """The picture of an address that is a proven identity of a user of this instance
    (`proven_identities`): that identity's own picture, else that user's account picture. The
    first proven owner decides; a later claim to the same address is never consulted. Any other
    address: None (a picture is never shown for an arbitrary address). Disabled users' pictures
    are not shown. The viewer's own identity comes first for the viewer."""
    e = email.strip().lower()
    rows = [r for r in await proven_identities(db, viewer_id, emails=[e]) if r[2] == e]
    rows.sort(key=lambda r: (r[1] != viewer_id, r[0]))
    if not rows:
        return None
    iid, uid, _ = rows[0]
    return identity_picture(uid, iid) or current(uid)


def address_key(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


async def local_map(db, viewer_id: int) -> dict[str, int]:
    """sha256(address) → version for the proven identity addresses of this instance's users
    that have a picture (their own or the account's), limited to addresses the viewer already
    knows: the viewer's own identities and the viewer's correspondents (people). Clients hash
    the addresses they show and ask /avatars/{email} only for those, so a list does not ask
    about every sender. The hashes are unsalted (the client computes them), so the list
    names only addresses that are already in the viewer's own mail; it tells nothing about the
    rest of the instance."""
    from sqlalchemy import func, select

    from mailr.models import Person

    proven = await proven_identities(db, viewer_id, known_to=viewer_id)
    if not proven:
        return {}
    wanted = {r[2] for r in proven}
    known = {r[2] for r in proven if r[1] == viewer_id}
    known |= set((await db.execute(select(func.lower(Person.email)).where(
        Person.user_id == viewer_id, func.lower(Person.email).in_(wanted)))).scalars())
    out: dict[str, int] = {}
    accounts: dict[int, int] = {}
    seen: set[str] = set()
    for iid, uid, email in sorted(proven, key=lambda r: (r[1] != viewer_id, r[0])):
        if email not in known or email in seen:
            continue
        seen.add(email)  # the first proven owner decides, as in for_address
        v = identity_version(uid, iid)
        if not v:
            if uid not in accounts:
                inf = info(uid)
                accounts[uid] = inf["version"] if inf["has"] else 0
            v = accounts[uid]
        if v:
            out[address_key(email)] = v
    return out
