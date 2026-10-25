"""Rate limits shared by every API worker.

Counted events live in the `rate_events` table (one row each), so a limit holds across uvicorn
workers and restarts, and its size is bounded: rows older than the longest window are deleted
whenever new ones are written. Bucket names are hashed before they are stored, so the table
never holds an email address or an IP.

Buckets in use:
- sign-in failures per IP (spraying many addresses), per IP and address, and per address from
  anywhere (slow guessing from many IPs); native ID-token failures per IP; sign-ups per IP
  (api/auth_routes.py, api/native_auth.py). The per-address bucket does not apply to a client the
  account signed in from before (a browser with the sign-in cookie, or an IP with a live session
  of the account): it has a bucket of its own, so strangers who fill the per-address bucket do
  not lock the owner out of their own devices;
- model-backed calls per API token (`token_model_call`).

Per-IP buckets count an IPv6 client by its /64 (`client_net`). Sign-in counts an attempt before
the password is checked (`attempt`, then `forgive` on success), so parallel guesses cannot pass a
limit together.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# The longest window any bucket uses: older rows are pruned.
MAX_WINDOW = timedelta(hours=1)


@dataclass(frozen=True)
class Limit:
    name: str
    count: int
    window: timedelta


# Sign-in (local passwords).
LOGIN_PER_IP = Limit("login-ip", 30, timedelta(minutes=15))
LOGIN_PER_IP_ACCOUNT = Limit("login-ip-acct", 10, timedelta(minutes=15))
LOGIN_PER_ACCOUNT = Limit("login-acct", 50, timedelta(minutes=15))
LOGIN_PER_KNOWN_CLIENT = Limit("login-known-acct", 10, timedelta(minutes=15))
# Native sign-in with an ID token, and self-service sign-up.
NATIVE_PER_IP = Limit("native-ip", 30, timedelta(minutes=15))
SIGNUP_PER_IP = Limit("signup-ip", 10, timedelta(hours=1))
# Requests an API token makes that call a model (chat, drafting, summaries, translations, search
# embeddings, order previews): MAILR_TOKEN_MODEL_CALLS_PER_MINUTE / _PER_HOUR.
def token_model_limits() -> tuple[Limit, Limit]:
    from mailr.config import get_settings

    s = get_settings()
    return (Limit("token-model-min", max(1, int(s.token_model_calls_per_minute)), timedelta(minutes=1)),
            Limit("token-model-hour", max(1, int(s.token_model_calls_per_hour)), timedelta(hours=1)))


def client_net(ip: str) -> str:
    """The part of a client address a per-IP bucket counts: an IPv6 address by its /64 (one host
    usually has the whole /64, so single addresses would give it endless buckets), an IPv4 address
    (also one mapped into IPv6) as it is. Anything else unchanged."""
    import ipaddress

    try:
        a = ipaddress.ip_address(str(ip).strip())
    except ValueError:
        return str(ip)
    if isinstance(a, ipaddress.IPv6Address):
        if a.ipv4_mapped is not None:
            return str(a.ipv4_mapped)
        return str(ipaddress.IPv6Network(f"{a}/64", strict=False))
    return str(a)


def _key(limit: Limit, *parts: object) -> str:
    raw = "\x00".join([limit.name, *(client_net(str(p).strip().lower()) for p in parts)])
    return limit.name[:20] + ":" + hashlib.sha256(raw.encode()).hexdigest()[:56]


async def _count(db: AsyncSession, key: str, window: timedelta) -> int:
    return int((await db.execute(text(
        "SELECT count(*) FROM rate_events WHERE key = :k AND created_at > :since"),
        {"k": key, "since": datetime.now(UTC) - window})).scalar_one())


async def exceeded(db: AsyncSession, buckets: list[tuple[Limit, tuple[object, ...]]]) -> Limit | None:
    """The first bucket at or over its limit, or None."""
    for limit, parts in buckets:
        if await _count(db, _key(limit, *parts), limit.window) >= limit.count:
            return limit
    return None


async def check(db: AsyncSession, buckets: list[tuple[Limit, tuple[object, ...]]],
                message: str = "too many attempts, try again later") -> None:
    """Raise 429 when any bucket is full."""
    if await exceeded(db, buckets) is not None:
        raise HTTPException(429, message)


async def record(db: AsyncSession, buckets: list[tuple[Limit, tuple[object, ...]]]) -> None:
    """Count one event in each bucket (committed by the caller) and prune expired rows."""
    for limit, parts in buckets:
        await db.execute(text("INSERT INTO rate_events (key) VALUES (:k)"), {"k": _key(limit, *parts)})
    await db.execute(text("DELETE FROM rate_events WHERE created_at < :old"),
                     {"old": datetime.now(UTC) - MAX_WINDOW})


async def record_now(buckets: list[tuple[Limit, tuple[object, ...]]]) -> None:
    """record() in its own transaction: the request's own transaction may roll back (a failed
    sign-in raises), and the failure must still count."""
    from mailr.db import session_scope

    async with session_scope() as db:
        await record(db, buckets)


async def attempt(db: AsyncSession, checked: Sequence[tuple[Limit, tuple[object, ...]]],
                  counted: Sequence[tuple[Limit, tuple[object, ...]]],
                  message: str = "too many attempts, try again later") -> list[int]:
    """Check the buckets and count the attempt in one step, before the attempt is made (a password
    verified, a token checked). Parallel attempts then cannot all pass a check that none of them
    has counted in yet: the buckets' keys are locked (advisory locks, in a fixed order) from the
    count to the insert. Raises 429 when a checked bucket is full; otherwise returns the counted
    rows, which `forgive` removes when the attempt succeeds (only failures count).

    Commits `db` (the request's session, so no second connection is held while it waits): the
    count stays even when the request fails later. Call it before the request changes anything."""
    try:
        for k in sorted({_key(limit, *parts) for limit, parts in (*checked, *counted)}):
            await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": k})
        for limit, parts in checked:
            if await _count(db, _key(limit, *parts), limit.window) >= limit.count:
                raise HTTPException(429, message)
        ids = [int((await db.execute(text("INSERT INTO rate_events (key) VALUES (:k) RETURNING id"),
                                     {"k": _key(limit, *parts)})).scalar_one()) for limit, parts in counted]
        await db.execute(text("DELETE FROM rate_events WHERE created_at < :old"),
                         {"old": datetime.now(UTC) - MAX_WINDOW})
    except BaseException:
        await db.rollback()
        raise
    await db.commit()
    return ids


async def forgive(db: AsyncSession, ids: list[int]) -> None:
    """Take back the counts of an attempt that succeeded (`attempt`). Commits `db`."""
    if ids:
        await db.execute(text("DELETE FROM rate_events WHERE id = ANY(:ids)"), {"ids": ids})
        await db.commit()


def login_buckets(ip: str, email: str, known: str | None = None
                  ) -> tuple[list[tuple[Limit, tuple[object, ...]]], list[tuple[Limit, tuple[object, ...]]]]:
    """(checked before the password, counted on a failure). Per IP and per IP and address come
    first for everyone. A known client (`known`: its key, see the module docstring) is checked
    against its own bucket instead of the per-address one; its failures still count there too."""
    first = [(LOGIN_PER_IP, (ip,)), (LOGIN_PER_IP_ACCOUNT, (ip, email))]
    if known:
        own = (LOGIN_PER_KNOWN_CLIENT, (known, email))
        return first + [own], first + [own, (LOGIN_PER_ACCOUNT, (email,))]
    every = first + [(LOGIN_PER_ACCOUNT, (email,))]
    return every, every


async def token_model_call(token_id: int) -> None:
    """A request of an API token that calls a model counts once against the token's
    per-minute and per-hour limits; 429 when either is full (intel/llm.charge_model_budget). The
    signed-in user, the user's own assistant and background jobs are not limited here."""
    from mailr.db import session_scope

    per_min, per_hour = token_model_limits()
    buckets = [(per_min, (token_id,)), (per_hour, (token_id,))]
    async with session_scope() as db:
        await check(db, buckets, "this token made too many requests that use a model; try again later")
        await record(db, buckets)
