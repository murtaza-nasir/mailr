"""Sender pictures.

Sources, in order:
1. a picture the user set for that person (people card) or for one of their own identities;
2. only when the user opted in (setting `sender_pictures`, off by default because looking a
   sender up tells a third party whom you correspond with): the domain's BIMI logo, then
   Gravatar, then Libravatar (both by SHA-256 of the address, `d=404`). An address the user knows
   only from local-only mail is never looked up (`known_only_from_local`).

Lookups run on the server (the browser never contacts third parties) and are content fetches
(core/egress.py): through the image proxy's egress proxy when one is set (the BIMI record too, as
DNS over HTTPS, so no lookup of the sender's domain goes through the server's resolver),
otherwise pinned to checked public addresses, and not at all while MAILR_REQUIRE_EGRESS finds
no usable egress. They read at most MAX_BYTES and are cached on disk for a week (misses for a
day; a paused lookup caches nothing), shared by all users because the data is public. The UI
falls back to initials on 404.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, File, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.config import get_settings
from mailr.core.auth import Principal, current, scope
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.models import Identity

log = logging.getLogger("mailr.avatars")
router = APIRouter(tags=["avatars"])

HIT_TTL = 7 * 86400
MISS_TTL = 86400
MAX_BYTES = 256 * 1024
_EMAIL = re.compile(r"^[^@\s/\\]{1,200}@[a-z0-9.-]{1,200}\.[a-z]{2,}$")
_IMAGE_CT = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml"}
_SVG_BAD = re.compile(r"<\s*(script|foreignObject|iframe|object|embed|use)\b|\son\w+\s*=|javascript:|"
                      r"xlink:href\s*=\s*['\"](?!#)|href\s*=\s*['\"](?!#)", re.I)


def _root() -> Path:
    return Path(get_settings().data_dir) / "avatars"


def _key(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


def _norm(email: str) -> str:
    e = email.strip().lower()
    if not _EMAIL.match(e):
        raise ApiError(400, "common.not_an_email")
    return e


def _user_file(user_id: int, kind: str, key: str) -> Path:
    return _root() / f"user-{user_id}" / kind / key


def _read_meta(path: Path) -> dict | None:
    meta = path.with_suffix(".json")
    if not meta.exists():
        return None
    try:
        return json.loads(meta.read_text())
    except ValueError:
        return None


def _serve(data: bytes, ctype: str) -> Response:
    return Response(data, media_type=ctype, headers={
        "Cache-Control": "private, max-age=86400",
        "X-Content-Type-Options": "nosniff",
        # An SVG opened directly must not run anything.
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox",
    })


def _store(path: Path, data: bytes, ctype: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    path.with_suffix(".json").write_text(json.dumps({"type": ctype, "at": time.time()}))


def _sniff(data: bytes, declared: str) -> str | None:
    """Accept only real images (by magic bytes) and safe SVG."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    head = data[:512].lstrip().lower()
    if (declared == "image/svg+xml" or head.startswith(b"<?xml") or head.startswith(b"<svg")) and b"<svg" in data[:2048].lower():
        text = data.decode("utf-8", "replace")
        if _SVG_BAD.search(text):
            return None
        return "image/svg+xml"
    return None


FETCH_DEADLINE = 10.0


async def _fetch(url: str, egress: str = "") -> tuple[bytes, str] | None:
    """One picture: https and public-only on every hop, through the egress proxy (so the
    sender's server does not learn mailr's address) or pinned to the checked address
    (core/egress.py), at most MAX_BYTES read and FETCH_DEADLINE seconds in all. `egress` comes
    from egress.gate()."""
    import asyncio
    from urllib.parse import urljoin

    from mailr.core import egress as content
    from mailr.core.netguard import TooLarge, UnsafeTarget, fetch_capped

    try:
        await content.check_url(url, egress)
    except UnsafeTarget:
        return None
    try:
        async with asyncio.timeout(FETCH_DEADLINE):
            async with content.client(egress, timeout=6, headers={"User-Agent": "mailr avatar lookup"}) as c:
                r, body = await fetch_capped(c, "GET", url, limit=MAX_BYTES, deadline=FETCH_DEADLINE)
                hops = 0
                while r.is_redirect and hops < 3 and r.headers.get("location"):
                    url = urljoin(url, r.headers["location"])
                    await content.check_url(url, egress)
                    r, body = await fetch_capped(c, "GET", url, limit=MAX_BYTES, deadline=FETCH_DEADLINE)
                    hops += 1
    except (httpx.HTTPError, UnsafeTarget, TooLarge, TimeoutError):
        return None
    if r.status_code != 200:
        return None
    ctype = _sniff(body, r.headers.get("content-type", "").split(";")[0].strip().lower())
    return (body, ctype) if ctype else None


async def _bimi(domain: str, egress: str = "") -> str | None:
    """The logo URL from the domain's BIMI record (default selector), if any. Through the egress
    proxy (DNS over HTTPS) when one is in use."""
    from mailr.core.egress import txt_records

    for d in (domain, ".".join(domain.split(".")[-2:])):
        for txt in await txt_records(f"default._bimi.{d}", egress):
            if not txt.lower().startswith("v=bimi1"):
                continue
            m = re.search(r"(?:^|;)\s*l=([^;\s]+)", txt)
            if m and m.group(1).lower().startswith("https://"):
                return m.group(1)
    return None


async def lookup_remote(email: str, egress: str = "", *, may_fetch=None) -> tuple[bytes, str] | None:
    """The picture from BIMI, Gravatar or Libravatar, from the disk cache when it is fresh.
    `may_fetch`: an async check made before any request leaves (a cache hit sends nothing)."""
    key = _key(email)
    cache = _root() / "remote" / key
    meta = _read_meta(cache)
    if meta is not None:
        fresh = time.time() - meta.get("at", 0) < (HIT_TTL if meta.get("type") else MISS_TTL)
        if fresh:
            return (cache.read_bytes(), meta["type"]) if meta.get("type") and cache.exists() else None
    if may_fetch is not None and not await may_fetch():
        return None  # no lookup, and no cached miss
    from mailr.core import egress as content

    try:
        egress = await content.gate(egress)
    except content.EgressUnavailable:
        return None  # paused: no lookup, and no cached miss
    found = None
    domain = email.split("@", 1)[1]
    url = await _bimi(domain, egress)
    if url:
        found = await _fetch(url, egress)
    if found is None:
        found = await _fetch(f"https://gravatar.com/avatar/{key}?s=128&d=404", egress)
    if found is None:
        found = await _fetch(f"https://seccdn.libravatar.org/avatar/{key}?s=128&d=404", egress)
    if found is None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.with_suffix(".json").write_text(json.dumps({"type": None, "at": time.time()}))
        cache.unlink(missing_ok=True)
        return None
    _store(cache, found[0], found[1])
    return found


async def known_only_from_local(db: AsyncSession, user_id: int, email: str) -> bool:
    """Does this user know the address only from local-only mail? Then a remote lookup (BIMI for
    its domain, a Gravatar or Libravatar hash) would tell an outside service about a correspondent
    of a local-only account, so none is made. An address in no mail at all (typed in a draft) is
    not known from local-only mail."""
    from sqlalchemy import exists, or_

    from mailr.models import Message

    def seen(local: bool, recipients: bool):
        who = or_(Message.to.contains([{"email": email}]), Message.cc.contains([{"email": email}])) if recipients \
            else Message.from_email == email
        return exists().where(Message.user_id == user_id, Message.local_only.is_(local), Message.deleted.is_(False), who)

    # The sender checks first: they use ix_msg_from; the recipient checks read more rows.
    if (await db.execute(select(seen(False, False)))).scalar():
        return False
    if not (await db.execute(select(seen(True, False)))).scalar() and \
            not (await db.execute(select(seen(True, True)))).scalar():
        return False
    return not (await db.execute(select(seen(False, True)))).scalar()


@router.get("/avatars/{email}")
async def avatar(email: str, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    e = _norm(email)
    key = _key(e)
    for kind in ("people",):
        f = _user_file(p.user_id, kind, key)
        m = _read_meta(f)
        if m and f.exists():
            return _serve(f.read_bytes(), m["type"])
    ident = (await db.execute(select(Identity).where(Identity.user_id == p.user_id, Identity.email == e))).scalars().first()
    if ident is not None:
        f = _user_file(p.user_id, "identities", str(ident.id))
        m = _read_meta(f)
        if m and f.exists():
            return _serve(f.read_bytes(), m["type"])
    # An address of a user of this instance (yours included): that identity's picture, else
    # that user's account picture (core/account_picture.py). Never for any other address.
    from mailr.core.account_picture import for_address

    local = await for_address(db, e, viewer_id=p.user_id)
    if local is not None:
        return _serve(*local)
    if not (p.user.settings or {}).get("sender_pictures", False):
        raise ApiError(404, "pictures.no_picture")
    from mailr.core.imageproxy import instance_config

    async def may_fetch() -> bool:
        return not await known_only_from_local(db, p.user_id, e)

    _, egress = await instance_config(db)
    found = await lookup_remote(e, egress, may_fetch=may_fetch)
    if found is None:
        raise ApiError(404, "pictures.no_picture")
    return _serve(*found)


async def _read_upload(file: UploadFile) -> tuple[bytes, str]:
    data = await file.read(MAX_BYTES * 8 + 1)
    if len(data) > MAX_BYTES * 8:
        raise ApiError(413, "pictures.limit_2mb")
    ctype = _sniff(data, (file.content_type or "").lower())
    if ctype is None or ctype == "image/svg+xml":
        raise ApiError(400, "pictures.wrong_type")
    import asyncio

    from mailr.core.account_picture import MAX_PIXELS

    try:
        return await asyncio.to_thread(_shrink, data, ctype, MAX_PIXELS)
    except _TooLarge:
        raise ApiError(400, "pictures.too_large") from None


class _TooLarge(Exception):
    pass


def _shrink(data: bytes, ctype: str, max_pixels: int | None = None) -> tuple[bytes, str]:
    """Store at most 256×256 (Pillow if available; otherwise as uploaded). Runs in a worker
    thread; _TooLarge when the picture has more than `max_pixels` pixels."""
    try:
        from PIL import Image
    except ImportError:
        return data, ctype
    try:
        im = Image.open(io.BytesIO(data))
        w, h = im.size
        if max_pixels and w * h > max_pixels:
            raise _TooLarge
        if im.format == "JPEG":
            im.draft("RGB", (512, 512))
        im.thumbnail((256, 256))
        out = io.BytesIO()
        im.convert("RGBA").save(out, "PNG", optimize=True)
        return out.getvalue(), "image/png"
    except _TooLarge:
        raise
    except Exception:  # noqa: BLE001
        return data, ctype


@router.put("/people/{email}/avatar")
async def set_person_avatar(email: str, file: UploadFile = File(...), p: Principal = Depends(current)):
    if p.is_token:
        raise ApiError(403, "pictures.set_by_user")
    data, ctype = await _read_upload(file)
    _store(_user_file(p.user_id, "people", _key(_norm(email))), data, ctype)
    return {"ok": True}


@router.delete("/people/{email}/avatar")
async def delete_person_avatar(email: str, p: Principal = Depends(current)):
    if p.is_token:
        raise ApiError(403, "pictures.removed_by_user")
    f = _user_file(p.user_id, "people", _key(_norm(email)))
    f.unlink(missing_ok=True)
    f.with_suffix(".json").unlink(missing_ok=True)
    return {"ok": True}


async def _own_identity(db: AsyncSession, p: Principal, iid: int) -> Identity:
    i = await db.get(Identity, iid)
    if not i or i.user_id != p.user_id:
        raise ApiError(404, "identities.identity_not_found")
    return i


@router.put("/identities/{iid}/avatar")
async def set_identity_avatar(iid: int, file: UploadFile = File(...), p: Principal = Depends(current),
                              db: AsyncSession = Depends(get_db)):
    if p.is_token:
        raise ApiError(403, "pictures.set_by_user")
    await _own_identity(db, p, iid)
    data, ctype = await _read_upload(file)
    _store(_user_file(p.user_id, "identities", str(iid)), data, ctype)
    return {"ok": True}


@router.get("/identities/{iid}/avatar")
async def get_identity_avatar(iid: int, p: Principal = Depends(scope("read")), db: AsyncSession = Depends(get_db)):
    await _own_identity(db, p, iid)
    f = _user_file(p.user_id, "identities", str(iid))
    m = _read_meta(f)
    if not m or not f.exists():
        raise ApiError(404, "pictures.no_picture")
    return _serve(f.read_bytes(), m["type"])


@router.delete("/identities/{iid}/avatar")
async def delete_identity_avatar(iid: int, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    if p.is_token:
        raise ApiError(403, "pictures.removed_by_user")
    await _own_identity(db, p, iid)
    f = _user_file(p.user_id, "identities", str(iid))
    f.unlink(missing_ok=True)
    f.with_suffix(".json").unlink(missing_ok=True)
    return {"ok": True}
