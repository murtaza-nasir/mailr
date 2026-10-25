"""The image proxy: remote images in mail are fetched by the server and served from mailr's origin.

Why: a remote image tells its sender when, where and on what device a message was opened.
When the proxy is on (instance setting `image_proxy`, default on), no request from the reader
goes to a sender's server. The images of new mail are fetched when it arrives (the
`images.prefetch` job), so every message "opens" at arrival and an open pixel learns nothing
about reading. Older mail is fetched the first time it is opened, in the serving route. Mail in
Spam is never prefetched: a fetch would confirm that the address is live.

Fetching (`fetch`):
- https only (http:// is upgraded, as it always was for display), public addresses only, checked by
  netguard before every hop and pinned to the checked address when connecting; redirects are followed by hand, at most MAX_REDIRECTS, and each
  target is checked again; no cookies, no referrer, a generic User-Agent;
- optionally through an egress HTTP proxy (instance setting `image_proxy_egress`, for example a
  VPN container's proxy), so senders see the VPN's address and not the server's. Through the
  proxy the image's host name is never resolved locally, and with MAILR_REQUIRE_EGRESS nothing
  is fetched without a usable proxy (core/egress.py, which builds every client and checks every
  hop here);
- limits: MAX_BYTES per image, MAX_IMAGES and MAX_MESSAGE_BYTES per message, TIMEOUT per
  request, MAX_PIXELS and MAX_SIDE for the decoded size;
- types: PNG, JPEG, GIF, WebP and AVIF, decided by the file's magic bytes (the server's
  Content-Type must not contradict it). SVG and everything else is refused.

The cache lives under <data dir>/imgcache. Each image is stored once in `blobs/`, named by the
SHA-256 of its bytes (the same logo in a thousand newsletters is stored once). In `urls/`, the
hash of each URL maps to its blob, type and size, or to the reason the fetch failed. Entries
expire (OK_TTL, FAIL_TTL), and `prune` deletes blobs, least recently served first, until the
cache fits its budget.

Serving: GET /api/v1/messages/{id}/images/{url hash} (api/mail_read.py) gives an image only for
a hash that the caller's own message references.

The cache is shared, but its answer time is not: an entry lists the users it was fetched for
(`users`, at most MAX_ENTRY_USERS tags), and the first time another user asks for the same URL it
is fetched again for them (`fetch(..., user_id=)`), so a quick answer never tells one user that
someone else's mail showed that image. The blob is still stored once.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import struct
import tempfile
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from mailr.config import get_settings
from mailr.core import egress as egress_mod
from mailr.core import netguard
from mailr.core.jobs import Retry, handler

log = logging.getLogger("mailr.imageproxy")

MAX_BYTES = 5 * 1024 * 1024
MAX_MESSAGE_BYTES = 25 * 1024 * 1024
MAX_IMAGES = 60
MAX_REDIRECTS = 4
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
MAX_SIDE = 16384
MAX_PIXELS = 50_000_000
OK_TTL = 30 * 86400
FAIL_TTL = 3600
REFUSED_TTL = 7 * 86400
CACHE_BUDGET = 2 * 1024 * 1024 * 1024
UNUSED_TTL = 90 * 86400
PRUNE_EVERY = 3600
CONCURRENCY = 6
MAX_ENTRY_USERS = 200
USER_AGENT = "Mozilla/5.0 (compatible; mailr-image-proxy)"
ACCEPT = "image/avif,image/webp,image/png,image/jpeg,image/gif;q=0.9"
ALLOWED_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp", "image/avif")
# Tests serve images from a local plain-http server; production never sets this.
ALLOW_HTTP = False

HASH_RE = re.compile(r"^[0-9a-f]{32}$")


class Refused(Exception):
    """The image is not fetched or not kept. The message is safe to show to users."""


# ---------------- URLs ----------------

def normalize(url: str) -> str | None:
    """The URL the proxy fetches: https (http:// and protocol-relative are upgraded), or None."""
    u = (url or "").strip()
    if u.startswith("//"):
        u = "https:" + u
    elif u.lower().startswith("http://") and not ALLOW_HTTP:
        u = "https://" + u[7:]
    if not re.match(r"(?i)https?://[^/\s]", u) or len(u) > 4096:
        return None
    return u


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8", "surrogatepass")).hexdigest()[:32]


def proxy_path(public_mid: int | str, h: str) -> str:
    return f"/api/v1/messages/{public_mid}/images/{h}"


# ---------------- cache ----------------

def _root() -> Path:
    return Path(get_settings().data_dir) / "imgcache"


def _blob_path(sha: str) -> Path:
    return _root() / "blobs" / sha[:2] / sha


def _entry_path(h: str) -> Path:
    return _root() / "urls" / h[:2] / f"{h}.json"


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@dataclass
class Entry:
    status: str  # ok | failed | refused
    sha: str = ""
    type: str = ""
    size: int = 0
    width: int | None = None
    height: int | None = None
    at: float = 0.0
    error: str = ""
    # Tags of the users it was fetched for (user_tag; "*": every user, the seeded demo images).
    users: list[str] = field(default_factory=list)

    def fetched_for(self, user_id: int | None) -> bool:
        return user_id is None or "*" in self.users or user_tag(user_id) in self.users

    def fresh(self) -> bool:
        ttl = {"ok": OK_TTL, "failed": FAIL_TTL}.get(self.status, REFUSED_TTL)
        if time.time() - self.at > ttl:
            return False
        return self.status != "ok" or _blob_path(self.sha).is_file()


def user_tag(user_id: int) -> str:
    return hashlib.sha256(f"mailr-image-user|{user_id}".encode()).hexdigest()[:12]


def lookup_for(h: str, user_id: int) -> Entry | None:
    """lookup(), for a user it was fetched for; None for anyone else (see the module docstring)."""
    e = lookup(h)
    return e if e is not None and e.fetched_for(user_id) else None


def lookup(h: str) -> Entry | None:
    """The cached result for a URL hash, if it is still fresh."""
    try:
        e = Entry(**json.loads(_entry_path(h).read_text()))
    except (OSError, ValueError, TypeError):
        return None
    return e if e.fresh() else None


def _store_entry(h: str, e: Entry) -> None:
    _write_atomic(_entry_path(h), json.dumps(e.__dict__).encode())


def read_blob(e: Entry) -> bytes | None:
    p = _blob_path(e.sha)
    try:
        data = p.read_bytes()
    except OSError:
        return None
    # Recency for the size budget, written at most once an hour per blob.
    try:
        if time.time() - p.stat().st_mtime > 3600:
            os.utime(p)
    except OSError:
        pass
    return data


def prune(budget: int = CACHE_BUDGET) -> int:
    """Delete blobs unused for UNUSED_TTL, then the least recently served ones until the cache
    fits the budget. Returns the number of blobs removed."""
    blobs = []
    for p in (_root() / "blobs").glob("*/*"):
        try:
            st = p.stat()
        except OSError:
            continue
        blobs.append((st.st_mtime, st.st_size, p))
    blobs.sort()
    total = sum(b[1] for b in blobs)
    removed = 0
    now = time.time()
    for mtime, size, p in blobs:
        if total <= budget and now - mtime < UNUSED_TTL:
            break
        try:
            p.unlink()
            total -= size
            removed += 1
        except OSError:
            pass
    for p in (_root() / "urls").glob("*/*.json"):
        try:
            if now - p.stat().st_mtime > max(OK_TTL, REFUSED_TTL):
                p.unlink()
        except OSError:
            pass
    return removed


def _maybe_prune() -> None:
    marker = _root() / ".pruned"
    try:
        if marker.is_file() and time.time() - marker.stat().st_mtime < PRUNE_EVERY:
            return
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except OSError:
        return
    prune()


# ---------------- image checks ----------------

def sniff(data: bytes) -> str | None:
    """The image type by magic bytes, for the allowed types only."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp":
        head = data[8:64]
        if b"avif" in head or b"avis" in head:
            return "image/avif"
    return None


def dimensions(data: bytes, ctype: str) -> tuple[int | None, int | None]:
    try:
        if ctype == "image/png" and len(data) >= 24:
            return struct.unpack(">II", data[16:24])
        if ctype == "image/gif" and len(data) >= 10:
            return struct.unpack("<HH", data[6:10])
        if ctype == "image/webp" and len(data) >= 30:
            chunk = data[12:16]
            if chunk == b"VP8X":
                w = int.from_bytes(data[24:27], "little") + 1
                h = int.from_bytes(data[27:30], "little") + 1
                return w, h
            if chunk == b"VP8 ":
                w, h = struct.unpack("<HH", data[26:30])
                return w & 0x3FFF, h & 0x3FFF
            if chunk == b"VP8L":
                b = data[21:25]
                bits = int.from_bytes(b, "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if ctype == "image/jpeg":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                seg = struct.unpack(">H", data[i + 2:i + 4])[0]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return w, h
                i += 2 + seg
        if ctype == "image/avif":
            j = data.find(b"ispe")
            if j > 0 and len(data) >= j + 16:
                return struct.unpack(">II", data[j + 8:j + 16])
    except struct.error:
        pass
    return None, None


def check_image(data: bytes, header_type: str) -> tuple[str, int | None, int | None]:
    """(type, width, height) for an acceptable image; raises Refused otherwise."""
    declared = (header_type or "").split(";", 1)[0].strip().lower()
    if "svg" in declared or "xml" in declared or "html" in declared or declared.startswith("text/"):
        raise Refused(f"not an image ({declared})")
    ctype = sniff(data)
    if ctype is None:
        raise Refused("not a PNG, JPEG, GIF, WebP or AVIF image")
    if declared.startswith("image/") and declared not in ALLOWED_TYPES and declared != "image/jpg":
        raise Refused(f"unsupported image type {declared}")
    w, h = dimensions(data, ctype)
    if w is not None and h is not None and (w > MAX_SIDE or h > MAX_SIDE or w * h > MAX_PIXELS):
        raise Refused(f"image too large ({w}×{h})")
    return ctype, w, h


# ---------------- fetching ----------------

_SEM: asyncio.Semaphore | None = None
_LOCKS: dict[str, asyncio.Lock] = {}


def _sem() -> asyncio.Semaphore:
    global _SEM
    if _SEM is None:
        _SEM = asyncio.Semaphore(CONCURRENCY)
    return _SEM


async def _guard(url: str, egress: str = "") -> None:
    """Every hop: https and a public address (http only when tests allow it). Through the egress
    proxy the host is not resolved here (core/egress.py)."""
    await egress_mod.check_url(url, egress, allow_http=ALLOW_HTTP)


def _client(egress: str) -> httpx.AsyncClient:
    # Through the egress proxy, or pinned to the addresses the guard checked (no DNS rebinding
    # between check and connect). Never direct while the egress is required.
    return egress_mod.client(
        egress, public_only=not ALLOW_HTTP, timeout=TIMEOUT, follow_redirects=False,
        headers={"User-Agent": USER_AGENT, "Accept": ACCEPT}, cookies=None,
    )


async def _download(url: str, egress: str) -> tuple[bytes, str]:
    async with _client(egress) as c:
        for _ in range(MAX_REDIRECTS + 1):
            try:
                await _guard(url, egress)
            except netguard.UnsafeTarget as e:
                raise Refused(str(e)) from None
            async with c.stream("GET", url) as r:
                if r.status_code in (301, 302, 303, 307, 308):
                    loc = r.headers.get("location")
                    nxt = normalize(urljoin(url, loc)) if loc else None
                    if not nxt:
                        raise Refused("redirect without a usable target")
                    url = nxt
                    continue
                if r.status_code != 200:
                    raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
                ctype = r.headers.get("content-type", "")
                declared = ctype.split(";", 1)[0].strip().lower()
                if "svg" in declared or "html" in declared or declared.startswith("text/"):
                    raise Refused(f"not an image ({declared})")
                try:
                    if int(r.headers.get("content-length") or 0) > MAX_BYTES:
                        raise Refused("image larger than the size limit")
                except ValueError:
                    pass
                buf = bytearray()
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    if len(buf) > MAX_BYTES:
                        raise Refused("image larger than the size limit")
                return bytes(buf), ctype
        raise Refused("too many redirects")


async def fetch(url: str, *, egress: str | None = None, refresh: bool = False, user_id: int | None = None) -> Entry:
    """Fetch one image into the cache (or return the cached result). With `user_id`, a cached
    result counts only when it was fetched for that user; otherwise the image is fetched again and
    the user added to the entry. Raises egress.EgressUnavailable, and caches nothing, when fetches
    are paused."""
    h = url_hash(url)
    if not refresh and (e := lookup(h)) is not None and e.fetched_for(user_id):
        return e
    egress = await egress_mod.gate(egress)
    lock = _LOCKS.setdefault(h, asyncio.Lock())
    async with lock:
        before = lookup(h)
        if not refresh and before is not None and before.fetched_for(user_id):
            return before
        try:
            async with _sem():
                data, header_type = await _download(url, egress)
            ctype, w, hgt = check_image(data, header_type)
            sha = hashlib.sha256(data).hexdigest()
            if not _blob_path(sha).is_file():
                _write_atomic(_blob_path(sha), data)
            e = Entry("ok", sha=sha, type=ctype, size=len(data), width=w, height=hgt, at=time.time())
        except Refused as ex:
            e = Entry("refused", at=time.time(), error=str(ex)[:300])
        except (httpx.HTTPError, OSError, ValueError) as ex:
            e = Entry("failed", at=time.time(), error=f"{type(ex).__name__}: {ex}"[:300])
        finally:
            _LOCKS.pop(h, None)
        users = list(before.users) if before is not None else []
        if user_id is not None and user_tag(user_id) not in users:
            users.append(user_tag(user_id))
        e.users = users[-MAX_ENTRY_USERS:]
        try:
            _store_entry(h, e)
        except OSError as ex:
            log.warning("image cache write failed: %s", ex)
        return e


def put(url: str, data: bytes) -> Entry:
    """Store an image for a URL as if it had been fetched (the e2e seed uses it; no network)."""
    ctype, w, h = check_image(data, "")
    sha = hashlib.sha256(data).hexdigest()
    if not _blob_path(sha).is_file():
        _write_atomic(_blob_path(sha), data)
    e = Entry("ok", sha=sha, type=ctype, size=len(data), width=w, height=h, at=time.time(), users=["*"])
    _store_entry(url_hash(url), e)
    return e


async def fetch_all(urls: list[str], egress: str | None, user_id: int | None = None) -> dict[str, Entry]:
    """Fetch a message's images (for `user_id`, see fetch), within the per-message limits."""
    out: dict[str, Entry] = {}
    total = 0
    egress = await egress_mod.gate(egress)
    for url in urls[:MAX_IMAGES]:
        if total > MAX_MESSAGE_BYTES:
            break
        e = await fetch(url, egress=egress, user_id=user_id)
        out[url] = e
        total += e.size
    _maybe_prune()
    return out


# ---------------- message references ----------------

_REFS: OrderedDict[tuple[int, int], dict[str, str]] = OrderedDict()
_REFS_MAX = 512


def message_refs(raw: bytes, attached: bool = False) -> dict[str, str]:
    """{url hash: url} for the images a message body shows through the proxy (trackers and
    images past MAX_IMAGES excluded). render.py builds the same list while it rewrites.
    `attached` adds the images of the emails attached to it (message/rfc822), which the reader
    shows when one of them is opened; prefetch leaves those alone."""
    from mailr.core.render import collect_image_urls

    refs = {url_hash(u): u for u in collect_image_urls(raw)}
    if attached and b"essage/" in raw:
        refs.update(_attached_refs(raw))
    return refs


def _attached_refs(raw: bytes) -> dict[str, str]:
    import email
    import email.policy

    from mailr.core import mime
    from mailr.core.render import collect_image_urls

    if mime.too_deep(raw):
        return {}
    out: dict[str, str] = {}
    try:
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        for idx, part in enumerate(msg.walk()):
            if idx >= mime.MAX_PARTS or len(out) >= 4 * MAX_IMAGES:
                break
            if mime.is_attached_email(part):
                for u in collect_image_urls(mime.attached_email_bytes(part)):
                    out[url_hash(u)] = u
    except Exception:  # noqa: BLE001  an attached email that cannot be read shows no images
        return out
    return out


def cached_refs(user_id: int, mid: int, raw_loader) -> Any:
    """Memoised message_refs for the serving route (raw_loader is an async callable)."""
    async def get() -> dict[str, str]:
        key = (user_id, mid)
        if key in _REFS:
            _REFS.move_to_end(key)
            return _REFS[key]
        raw = await raw_loader()
        refs = await asyncio.to_thread(message_refs, raw, True)
        _REFS[key] = refs
        while len(_REFS) > _REFS_MAX:
            _REFS.popitem(last=False)
        return refs
    return get()


# ---------------- settings ----------------

async def instance_config(db) -> tuple[bool, str]:
    """(proxy on, egress proxy URL or "")."""
    from mailr.api.admin_routes import get_instance_setting

    on = await get_instance_setting(db, "image_proxy", True)
    env = egress_from_env()
    egress = env or open_egress(await get_instance_setting(db, "image_proxy_egress", "") or "")
    # With the egress required the proxy is always on: with it off, the reader's browser would
    # load images straight from the sender's servers.
    return bool(on) or egress_mod.required(), egress


def egress_from_env() -> str:
    """MAILR_IMAGE_PROXY_EGRESS, checked like the admin setting; an invalid value fails closed."""
    from mailr.config import get_settings

    raw = (get_settings().image_proxy_egress or "").strip()
    if not raw:
        return ""
    try:
        return check_egress(raw)
    except ValueError:
        log.warning("MAILR_IMAGE_PROXY_EGRESS is not a usable proxy URL; image fetches are paused")
        return "http://egress-invalid.invalid:1"


def seal_egress(url: str) -> str:
    """The egress URL as stored: encrypted with the instance key (it may carry a password)."""
    import base64

    from mailr.core.crypto import EGRESS_CONTEXT, encrypt_instance

    return "enc:" + base64.b64encode(encrypt_instance(url, context=EGRESS_CONTEXT)).decode() if url else ""


def open_egress(stored: str) -> str:
    import base64

    from mailr.core.crypto import EGRESS_CONTEXT, decrypt_instance

    if not stored.startswith("enc:"):
        return stored
    try:
        return str(decrypt_instance(base64.b64decode(stored[4:]), context=EGRESS_CONTEXT) or "")
    except Exception:  # noqa: BLE001 - an unreadable value means no egress proxy, and fetches fail closed
        log.warning("image_proxy_egress cannot be decrypted; image fetches are paused")
        return "http://egress-unreadable.invalid:1"


def user_mode(user) -> str:
    """The user's "Load images" setting: always (through the proxy) | ask | never."""
    from mailr.api.auth_routes import DEFAULT_SETTINGS

    v = (user.settings or {}).get("images", DEFAULT_SETTINGS["images"])
    return v if v in ("always", "ask", "never") else "ask"


async def is_junk(db, m) -> bool:
    from sqlalchemy import select

    from mailr.models import Label

    junk = (await db.execute(select(Label.id).where(Label.user_id == m.user_id, Label.role == "junk"))).scalar()
    return junk is not None and junk in (m.label_ids or [])


def mask_egress(url: str) -> str:
    """The egress URL for display: a password is never sent back."""
    u = urlsplit(url or "")
    if u.password:
        return url.replace(f":{u.password}@", ":***@", 1)
    return url


def check_egress(url: str) -> str:
    """Validate an egress proxy URL from the admin form."""
    url = (url or "").strip()
    if not url:
        return ""
    u = urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname or (u.path not in ("", "/")) or u.query:
        raise ValueError("the egress proxy must look like http://host:port")
    # The stack's own VPN container (`egress`) is one of the internal names user URLs never
    # reach; as the admin's egress proxy it is exactly what is meant. Anything else is checked
    # like an admin-set server (private addresses allowed, loopback and metadata refused).
    if u.hostname.lower() not in netguard._infra_hosts():
        netguard.check_host_sync(u.hostname, u.port, private_ok=True)
    return url


# ---------------- the arrival job ----------------

PREFETCH_DELAY_S = 45
SPAM_WAIT_S = 30 * 60
PAUSED_WAIT_S = 24 * 3600


async def maybe_enqueue_prefetch(db, user, m) -> None:
    """Queue the arrival fetch for a new message (core/ingest.py calls this for live, non-junk
    inbound mail). The job checks again when it runs, after the spam checks had their turn."""
    from datetime import UTC, datetime, timedelta

    from mailr.core.jobs import enqueue

    if user_mode(user) != "always" or m.draft or m.sent_by_me:
        return
    on, _ = await instance_config(db)
    if not on:
        return
    await enqueue(db, "images.prefetch", {"message_id": m.id}, user_id=user.id,
                  run_at=datetime.now(UTC) + timedelta(seconds=PREFETCH_DELAY_S),
                  dedupe_key=f"images.prefetch:{m.id}", max_attempts=3)


@handler("images.prefetch")
async def prefetch_job(payload: dict[str, Any], ctx) -> None:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import text

    from mailr.core.store import user_store
    from mailr.db import session_scope
    from mailr.models import Message, User

    async with session_scope() as db:
        m = await db.get(Message, payload["message_id"])
        if m is None or m.deleted:
            return
        user = await db.get(User, m.user_id)
        on, egress = await instance_config(db)
        if not on or user_mode(user) != "always":
            return
        # Never for Spam: the fetch itself would tell the sender the address is live.
        if await is_junk(db, m):
            return
        # The spam classifier may still move it to Spam: wait for it (for a while).
        pending = (await db.execute(text(
            "SELECT 1 FROM jobs WHERE status IN ('queued','running') AND dedupe_key = ANY(:k) LIMIT 1"),
            {"k": [f"intel.message:{m.id}", f"spam.classify:{m.id}"]})).first()
        when = m.received_at or m.date
        if pending and when and when > datetime.now(UTC) - timedelta(seconds=SPAM_WAIT_S):
            raise Retry(delay=60)
        store = await user_store(db, user)
        raw = await store.get_raw(m)
    urls = list(dict.fromkeys((await asyncio.to_thread(message_refs, raw)).values()))
    if urls:
        try:
            await fetch_all(urls, egress or None, user_id=m.user_id)
        except egress_mod.EgressUnavailable as e:
            # Paused (core/egress.py): never a direct fetch. A VPN that is down or leaking gets
            # another try while the message is new; with no egress configured, the images wait
            # for the reader (and the paused notice there).
            if e.state in ("down", "leak") and when and when > datetime.now(UTC) - timedelta(seconds=PAUSED_WAIT_S):
                raise Retry(delay=300) from None
