"""Authentication and authorisation.

Two kinds of caller:
- A signed-in person (session cookie). Has every scope over their own data, plus admin
  if their role is admin. Unsafe methods require the X-CSRF-Token header.
- An API token (Authorization: Bearer mlr_...). Limited to its scopes, connectors, and
  local_only flag. Every call made with a token is audited.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from mailr.config import get_settings
from mailr.core.crypto import random_token, sha256_hex
from mailr.db import get_db
from mailr.i18n import ApiError
from mailr.models import ApiToken, Connector, Session, User

SESSION_COOKIE = "mailr_session"
# `profile`: the user's name, address and account picture, nothing else (integrations such as Speakr).
# `notes`: notes, checklists and tables, read and write.
# `web`: search the web and read web pages through mailr (web_search, read_page). A token's agent
# then has an outbound channel, so new tokens do not get it unless it is ticked; tokens made before 11 Oct 2026 with `read` got it in a migration.
ALL_SCOPES = ("read", "label", "draft", "send", "delete", "rules", "calendar", "memory", "recordings", "notes", "profile",
              "web", "admin", "tls")
TOKEN_PREFIX = "mlr_"

def _hasher() -> PasswordHasher:
    """Argon2 with the library's defaults. The test suite (MAILR_DEV with
    MAILR_TEST_CHEAP_PASSWORD_HASHES) uses the cheapest parameters: thousands of test users each
    hash and verify a password, and the defaults cost tens of milliseconds every time."""
    s = get_settings()
    if s.dev and s.test_cheap_password_hashes:
        return PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    return PasswordHasher()


_ph = _hasher()


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


_DUMMY_HASH: str | None = None


def dummy_hash() -> str:
    """A fixed Argon2 hash with the same parameters as real ones. Sign-in verifies against it when
    the address has no usable account, so a failure costs the same either way (no timing oracle
    for which addresses exist)."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = _ph.hash(random_token(24))
    return _DUMMY_HASH


def verify_password(pw: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return _ph.verify(hashed, pw)
    except VerificationError:
        return False
    except Exception:  # noqa: BLE001
        return False


_HASHING = None
# Password hashes computed at once, off the event loop (about 40 ms each with the defaults).
HASHING_AT_ONCE = 4


async def run_hashing(fn, *args):
    """`fn(*args)` (verify_password, hash_password) in a worker thread, at most HASHING_AT_ONCE at
    a time: an Argon2 run on the event loop would stall every other request meanwhile."""
    import anyio

    global _HASHING
    if _HASHING is None:
        _HASHING = anyio.CapacityLimiter(HASHING_AT_ONCE)
    return await anyio.to_thread.run_sync(fn, *args, limiter=_HASHING)


@dataclass
class Principal:
    user: User
    token: ApiToken | None = None
    session: Session | None = None
    scopes: set[str] = field(default_factory=set)

    @property
    def user_id(self) -> int:
        return self.user.id

    @property
    def is_token(self) -> bool:
        return self.token is not None

    @property
    def local_only(self) -> bool:
        """True when everything this caller reads stays on local infrastructure."""
        return self.token is None or self.token.local_only

    @property
    def actor(self) -> str:
        return f"token:{self.token.name}" if self.token else "user"

    def has(self, scope: str) -> bool:
        return scope in self.scopes

    def require(self, scope: str) -> None:
        if scope not in self.scopes:
            raise HTTPException(403, f"missing scope: {scope}")

    def can_see_connector(self, c: Connector) -> bool:
        if c.user_id != self.user.id:
            return False
        if self.token is None:
            return True
        if self.token.connector_ids and c.id not in self.token.connector_ids:
            return False
        if c.data_class == "local_only" and not self.token.local_only:
            return False
        return True


# ---------- sessions ----------


async def create_session(
    db: AsyncSession, user: User, user_agent: str = "", ip: str = "", device: str = "web"
) -> tuple[str, Session]:
    raw = random_token(32)
    s = Session(
        id=sha256_hex(raw),
        user_id=user.id,
        csrf=random_token(24),
        user_agent=user_agent[:400],
        ip=ip[:64],
        device=(device or "web").strip()[:40] or "web",  # a label the client chose (X-Mailr-Device)
        expires_at=datetime.now(UTC) + timedelta(days=get_settings().session_days),
    )
    db.add(s)
    user.last_login_at = datetime.now(UTC)
    await db.commit()
    return raw, s


async def _session_principal(db: AsyncSession, raw: str) -> Principal | None:
    s = await db.get(Session, sha256_hex(raw))
    if not s or s.expires_at < datetime.now(UTC):
        return None
    user = await db.get(User, s.user_id)
    if not user or user.disabled:
        return None
    now = datetime.now(UTC)
    if (now - s.last_seen_at).total_seconds() > 300:
        s.last_seen_at = now
        await db.commit()
    scopes = set(ALL_SCOPES) if user.role == "admin" else set(ALL_SCOPES) - {"admin"}
    return Principal(user=user, session=s, scopes=scopes)


# ---------- API tokens ----------


async def create_token(
    db: AsyncSession,
    user: User,
    name: str,
    scopes: list[str],
    connector_ids: list[int] | None = None,
    local_only: bool = False,
    send_policy: dict | None = None,
    expires_at: datetime | None = None,
) -> tuple[str, ApiToken]:
    bad = set(scopes) - set(ALL_SCOPES)
    if bad:
        raise HTTPException(400, f"unknown scopes: {sorted(bad)}")
    if {"admin", "tls"} & set(scopes) and user.role != "admin":
        raise HTTPException(403, "only admins can mint admin or tls tokens")
    raw = TOKEN_PREFIX + random_token(30)
    t = ApiToken(
        user_id=user.id,
        name=name,
        prefix=raw[:8],
        token_hash=sha256_hex(raw),
        scopes=sorted(set(scopes)),
        connector_ids=connector_ids or [],
        local_only=local_only,
        send_policy=send_policy or {"mode": "approve_all"},
        expires_at=expires_at,
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return raw, t


async def _token_principal(db: AsyncSession, raw: str) -> Principal | None:
    t = (
        await db.execute(select(ApiToken).where(ApiToken.token_hash == sha256_hex(raw)))
    ).scalar_one_or_none()
    if not t or t.revoked or (t.expires_at and t.expires_at < datetime.now(UTC)):
        return None
    user = await db.get(User, t.user_id)
    if not user or user.disabled:
        return None
    # A token inherits its owner's access policy (core/access.py): with Agents & API tokens off,
    # no token of the owner works. Model calls it causes resolve as the owner (intel/llm.resolve).
    from mailr.core import access

    acc = await access.for_user(db, user)
    if not acc.feature("api_tokens"):
        await access.audit_refusal(user.id, f"token:{t.id}", "API tokens are off in the access policy")
        raise HTTPException(403, access.NO_FEATURE_MESSAGES["api_tokens"])
    # The local-only flag counts only while the owner's policy allows local-only agents. Without it
    # the token works as an ordinary one and cannot read local-only accounts. The stored flag stays
    # (set_committed_value does not mark the row dirty), so turning the switch back on restores it.
    if t.local_only and not acc.feature("local_only_tokens"):
        set_committed_value(t, "local_only", False)
    await db.execute(
        update(ApiToken).where(ApiToken.id == t.id).values(last_used_at=datetime.now(UTC))
    )
    await db.commit()
    return Principal(user=user, token=t, scopes=set(t.scopes))


# ---------- dependencies ----------

_SAFE = {"GET", "HEAD", "OPTIONS"}


async def resolve_principal(request: Request, db: AsyncSession) -> Principal | None:
    """The caller of a request from its headers alone (the body is never read): an API token, or a
    session with its CSRF token on unsafe methods. None when signed out; HTTPException for a bad
    token or a failed CSRF check. The upload gate (core/uploadgate.py) calls it before a body is
    read, the dependencies below for every route."""
    authz = request.headers.get("authorization", "")
    if authz.lower().startswith("bearer "):
        p = await _token_principal(db, authz[7:].strip())
        if p is None:
            raise HTTPException(401, "invalid or expired token")
        return p
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    p = await _session_principal(db, raw)
    if p is None:
        return None
    if request.method not in _SAFE:
        sent = request.headers.get("x-csrf-token", "")
        if not sent or not secrets.compare_digest(sent.encode(), (p.session.csrf or "").encode()):
            raise HTTPException(403, "CSRF check failed")
    return p


async def optional_principal(
    request: Request, db: AsyncSession = Depends(get_db)
) -> Principal | None:
    p = await resolve_principal(request, db)
    if p is None:
        return None
    request.state.principal = p
    if p.token is not None:
        # Model calls this request causes count against the token's budget (intel/llm.py).
        from mailr.intel.llm import MODEL_BUDGET

        MODEL_BUDGET.set({"token_id": p.token.id, "charged": False})
    _author(p, request)
    return p


def _author(p: Principal, request: Request | None = None) -> None:
    """Who writes in this request, for the live events of shared notes and tables (core/live.py),
    and the languages their browser accepts, for texts rendered for them (i18n.user_language)."""
    from mailr import i18n
    from mailr.core import live

    live.author_var.set({"id": p.user.id, "name": p.user.name or p.user.email})
    from mailr.core import errors

    errors.set_user(p.user.id)  # the id only, on error reports (core/errors.py)
    if request is not None:
        # For the access line (core/slowlog.py), read after the response: the user row may be
        # detached and expired by then.
        request.state.user_id = p.user.id
    from mailr.core.visibility import viewer_var

    viewer_var.set(p)
    i18n.set_request_language(p.user.id, request.headers.get("accept-language") if request is not None else None)


async def current(p: Principal | None = Depends(optional_principal)) -> Principal:
    if p is None:
        raise HTTPException(401, "not signed in")
    return p


def scope(name: str):
    async def dep(p: Principal = Depends(current)) -> Principal:
        p.require(name)
        return p

    return dep


async def admin(p: Principal = Depends(current)) -> Principal:
    """Administration needs a signed-in admin. API tokens cannot administer: a leaked or
    prompt-injected token must not be able to create admins or re-point model endpoints
    (which would route every user's mail to another server)."""
    if p.user.role != "admin" or not p.has("admin"):
        raise ApiError(403, "auth.admin_only")
    if p.is_token:
        raise HTTPException(403, "administration needs a signed-in admin, not an API token")
    return p


async def tls_admin(p: Principal = Depends(current)) -> Principal:
    """Installing the IMAP TLS certificate: a signed-in admin, or an admin's token with the
    narrow `tls` scope (the certificate-push job). `admin`-scoped tokens are still accepted here
    so an existing push job keeps working until its token is replaced."""
    if p.user.role != "admin":
        raise ApiError(403, "auth.admin_only")
    if p.is_token and not (p.has("tls") or p.has("admin")):
        raise HTTPException(403, "this token needs the tls scope")
    return p
