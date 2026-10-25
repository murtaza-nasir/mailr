"""Outbound services: one registry, one locality rule, one way out (docs/design/outbound-data-policy.md).

SERVICES. Every outbound service that can receive user data is a `Service`: a `kind` (chat,
classify, embed, transcribe, websearch, reader, speakr_models, error_tracker, avatar, or account
for a person's own account at a service such as Speakr), a name, a base URL, a declared
`locality` (local | hosted) and the `source` it comes from (a model endpoint row, a web search
provider row, a Speakr connector, the environment, the Sentry DSN).

LOCALITY. `local(x)` and `allowed(local_only, service)` are the only places in the code that
compare a locality. A service declared `local` is local only while every address its host resolves
to is private or loopback, and that is checked when each connection is made (`client()` pins the
connection to the checked addresses), not only when an admin saves the service. A host that
resolves to a public address at connect time is refused for that connection: fail closed. Entries
of MAILR_OUTBOUND_ALLOW make an address local only when they are IP addresses or CIDRs; a host
name on that list is never local by its name.

DATA CLASSES. Data is normal or local-only. The effective class of an outbound call is local-only
when any of these holds:
1. the caller declares it (`sensitive=` in intel/llm.py, `local_only=` here);
2. the taint scope of the request or job is local-only (below);
3. for model kinds, the household rule `local_all` or the person's "local models only" switch
   (intel/locality.py; intel/llm.py applies it).
A local-only call may only reach a local service.

TAINT SCOPE. A ContextVar holds a mutable `Scope`. The ORM `load` and `refresh` events of
local-only rows mark it (install_listeners): Message (local_only, or a local-only connector,
which covers drafts), Note and DataTable (local_only), TableRow (of a local-only table),
Memory and Entity (sensitive), SpeakrRecording and CalObject (of a local-only connector).
`mark_local_only()` marks it by hand, for selects of single columns that build prompts. Every
API request (ScopeMiddleware) and every job (core/jobs.run_one) runs in a scope of its own. A batch
job opens `item_scope(row)` for each item, so one local-only item does not taint the rest of the
batch: the item scope starts clean and holds only the item's own class and what is loaded inside
it. Agent chat sessions keep their taint in `session.sensitive` (agent/loop.py).

ONE WAY OUT. `client(service, ...)` is the httpx client for every model and service call: it
refuses a local-only call to a hosted service, connects only to checked addresses (pinned, with
`trust_env=False`, so no proxy from the environment), checks a local service's addresses again on
every connection, and checks the taint scope again when each request is sent. A lint test
(tests/test_outbound_lint.py) fails when another module opens its own HTTP client.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import socket
import time
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from mailr.core import netguard

log = logging.getLogger("mailr.outbound")

LOCAL = "local"
HOSTED = "hosted"
LOCALITIES = (LOCAL, HOSTED)

KINDS = ("chat", "classify", "embed", "transcribe", "websearch", "reader", "speakr_models", "error_tracker",
         "avatar", "account")
# The household rule and the person's switch apply to these (intel/locality.py).
MODEL_KINDS = frozenset({"chat", "classify", "embed", "transcribe", "speakr_models"})
# The data policy applies to these. `account` is a person's own account at a service (the Speakr
# API): the data there is theirs already, and what may be written to it is decided by its caller.
POLICY_KINDS = frozenset(set(KINDS) - {"account"})


class Refused(Exception):
    """A local-only call to a hosted service (or to a local one that failed its address check).
    The message is safe to show."""


class NotLocal(netguard.UnsafeTarget):
    """A service declared local resolves to a public address (or to no address at all)."""


@dataclass(frozen=True)
class Service:
    kind: str
    name: str
    base_url: str
    locality: str
    source: str = ""
    # Set up by the server's environment (MAILR_ASR_URL, MAILR_BROWSER_URL): an address the operator
    # chose, which may be one of the stack's own names; only the locality check applies to it.
    trusted: bool = False

    @property
    def host(self) -> str:
        return (urlsplit(self.base_url).hostname or "").lower()


# ---------------------------------------------------------------- the one comparison


def local(x: Any) -> bool:
    """Is this service (a Service, a ModelEndpoint, a Resolved model or a locality string) declared
    local? Whether its addresses are local is checked when it is connected to."""
    if isinstance(x, str):
        return x == LOCAL
    ep = getattr(x, "endpoint", None)
    if ep is not None and not hasattr(x, "locality"):
        x = ep
    return getattr(x, "locality", None) == LOCAL


def allowed(effective_local_only: bool, service: Any) -> bool:
    """The policy: local-only data goes to local services only; normal data goes anywhere."""
    return not effective_local_only or local(service)


def effective(declared: bool, *, kind: str | None = None) -> bool:
    """The effective class of a call: the caller's declaration or the taint scope. (The household
    rule and the person's switch are added by intel/llm.py, which knows the user.)"""
    if kind is not None and kind not in POLICY_KINDS:
        return bool(declared)
    return bool(declared) or tainted()


def endpoint_service(ep: Any, kind: str) -> Service:
    """A model endpoint row as a Service for one task."""
    return Service(kind=kind, name=ep.name, base_url=ep.base_url, locality=ep.locality, source="model_endpoint",
                   trusted=bool(getattr(ep, "from_env", False)))


# ---------------------------------------------------------------- the taint scope


@dataclass
class Scope:
    local_only: bool = False
    why: str = ""
    children: list[Scope] = field(default_factory=list, repr=False)


_SCOPE: ContextVar[Scope | None] = ContextVar("mailr_outbound_scope", default=None)


def tainted() -> bool:
    s = _SCOPE.get()
    return bool(s is not None and s.local_only)


def mark_local_only(why: str = "") -> None:
    """The current request or job has read local-only data: every later outbound call in it is
    local-only. With no scope open (a script), one is opened for the rest of this context."""
    s = _SCOPE.get()
    if s is None:
        s = Scope()
        _SCOPE.set(s)
    if not s.local_only:
        s.local_only, s.why = True, why


@contextlib.contextmanager
def scope() -> Iterator[Scope]:
    """A clean scope for one request or one job."""
    s = Scope()
    token = _SCOPE.set(s)
    try:
        yield s
    finally:
        _SCOPE.reset(token)


@contextlib.contextmanager
def item_scope(*rows: Any, local_only: bool = False) -> Iterator[Scope]:
    """A clean scope for one item of a batch: it holds the item's own class (`rows`, or
    `local_only`), and what is loaded inside it, and nothing of the other items. Use it only around
    code that works on this item alone."""
    s = Scope()
    token = _SCOPE.set(s)
    try:
        if local_only or any(row_local_only(r) for r in rows if r is not None):
            s.local_only, s.why = True, "item"
        yield s
    finally:
        _SCOPE.reset(token)


def exception_tainted(exc: BaseException | None) -> bool:
    """Was this exception raised in a local-only scope (ScopeMiddleware notes it on the way out)?"""
    return bool(exc is not None and getattr(exc, "_mailr_local_only", False))


def note_exception(exc: BaseException) -> None:
    if tainted():
        with contextlib.suppress(AttributeError, TypeError):
            exc._mailr_local_only = True  # type: ignore[attr-defined]


# Ids of local-only connectors, tables and the collections of local-only connectors, for rows whose
# class is their parent's. Kept up to date by the parents' own load and write events in this
# process, and read again from the database at most every CLASSES_TTL seconds when a scope opens
# (refresh_classes), for changes another process made.
CLASSES_TTL = 10.0
_LOCAL_CONNECTORS: set[int] = set()
_LOCAL_TABLES: set[int] = set()
_classes_at = 0.0
_classes_lock: asyncio.Lock | None = None


async def refresh_classes(force: bool = False) -> None:
    global _classes_at, _classes_lock
    if not force and time.monotonic() - _classes_at < CLASSES_TTL:
        return
    if _classes_lock is None:
        _classes_lock = asyncio.Lock()
    async with _classes_lock:
        if not force and time.monotonic() - _classes_at < CLASSES_TTL:
            return
        from sqlalchemy import select

        from mailr.db import sessionmaker
        from mailr.models import Connector
        from mailr.tables.models import DataTable

        try:
            async with sessionmaker()() as db:
                conns = set((await db.execute(select(Connector.id).where(
                    Connector.data_class == "local_only"))).scalars())
                tables = set((await db.execute(select(DataTable.id).where(DataTable.local_only.is_(True)))).scalars())
        except Exception as e:  # noqa: BLE001 - the sets stay as they were; the rows' own flags still count
            log.info("could not read the local-only connectors: %s", type(e).__name__)
            return
        _LOCAL_CONNECTORS.clear()
        _LOCAL_CONNECTORS.update(conns)
        _LOCAL_TABLES.clear()
        _LOCAL_TABLES.update(tables)
        _classes_at = time.monotonic()


def forget_classes() -> None:
    """Read the sets again when the next scope opens (tests, an account switched to local-only)."""
    global _classes_at
    _classes_at = 0.0


def row_local_only(obj: Any) -> bool:
    """The data class of one loaded row (the rules in the module docstring). A row loaded without
    the column that decides it (a load_only query) counts as local-only."""
    name = type(obj).__name__
    d = obj.__dict__

    def flag(key: str) -> bool:
        return bool(d[key]) if key in d else True

    def parent_in(key: str, ids: set[int]) -> bool:
        return d[key] in ids if key in d else True

    if name == "Message":
        return flag("local_only") or parent_in("connector_id", _LOCAL_CONNECTORS)
    if name in ("Note", "DataTable"):
        return flag("local_only")
    if name == "TableRow":
        return parent_in("table_id", _LOCAL_TABLES)
    if name in ("Memory", "Entity"):
        return flag("sensitive")
    if name in ("SpeakrRecording", "CalObject"):
        return parent_in("connector_id", _LOCAL_CONNECTORS)
    return False


_installed = False


def install_listeners() -> None:
    """The ORM events that raise the taint scope (idempotent; mailr.db calls it)."""
    global _installed
    if _installed:
        return
    from sqlalchemy import event

    from mailr.models import CalObject, Connector, Entity, Memory, Message, SpeakrRecording
    from mailr.notes.models import Note
    from mailr.tables.models import DataTable, TableRow

    def on_load(target: Any, *_a: Any) -> None:
        if row_local_only(target):
            mark_local_only(type(target).__name__)

    for cls in (Message, Note, DataTable, TableRow, Memory, Entity, SpeakrRecording, CalObject):
        event.listen(cls, "load", on_load)
        event.listen(cls, "refresh", on_load)

    def on_connector(target: Any, *_a: Any) -> None:
        cid, dc = target.__dict__.get("id"), target.__dict__.get("data_class")
        if cid is None or dc is None:
            return
        if dc == "local_only":
            _LOCAL_CONNECTORS.add(cid)
        else:
            _LOCAL_CONNECTORS.discard(cid)

    for ev in ("load", "refresh"):
        event.listen(Connector, ev, on_connector)
    for ev in ("after_insert", "after_update"):
        event.listen(Connector, ev, lambda _m, _c, target: on_connector(target))

    def on_table(target: Any, *_a: Any) -> None:
        tid = target.__dict__.get("id")
        if tid is None or "local_only" not in target.__dict__:
            return
        if target.__dict__["local_only"]:
            _LOCAL_TABLES.add(tid)
        else:
            _LOCAL_TABLES.discard(tid)

    for ev in ("load", "refresh"):
        event.listen(DataTable, ev, on_table)
    for ev in ("after_insert", "after_update"):
        event.listen(DataTable, ev, lambda _m, _c, target: on_table(target))
    _installed = True


class ScopeMiddleware:
    """Every API request runs in a taint scope of its own (pure ASGI, so the endpoint, its
    dependencies and a streamed body share it). An exception that leaves a local-only request is
    marked, so the error tracker keeps only its type (core/errors.py)."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope_: dict[str, Any], receive: Any, send: Any) -> None:
        if scope_.get("type") not in ("http", "websocket"):
            await self.app(scope_, receive, send)
            return
        await refresh_classes()
        with scope():
            try:
                await self.app(scope_, receive, send)
            except BaseException as e:
                note_exception(e)
                raise


# ---------------------------------------------------------------- connect-time locality


def _allow_nets() -> list[ipaddress._BaseNetwork]:
    return netguard._allow_list()[1]


def _is_local_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0].strip("[]"))
    except ValueError:
        return False  # a name, not an address: never local by its name
    if any(addr in n for n in _allow_nets()):
        return True
    if isinstance(addr, ipaddress.IPv6Address) and (inner := netguard._inner_v4(addr)) is not None:
        addr = inner
    return addr.is_loopback or not netguard._is_public(addr)


def verify_local(host: str, ips: list[str]) -> None:
    """Every address must be private or loopback (or in an allow-list CIDR). NotLocal otherwise."""
    if not ips:
        raise NotLocal(f"{host} does not resolve to any address")
    for ip in ips:
        if not _is_local_ip(ip):
            raise NotLocal(f"{host} resolves to a public address at connect time, so it is not used as a "
                           "local service")


def _lookup(host: str, port: int | None) -> list[str]:
    host = (host or "").strip().lower().rstrip(".").strip("[]")
    try:
        ipaddress.ip_address(host)
        return [host]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, port or 0, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError) as e:
        raise netguard.UnsafeTarget(f"cannot resolve {host}: {e}") from None
    out: list[str] = []
    for info in infos:
        ip = str(info[4][0]).split("%", 1)[0]
        if ip not in out:
            out.append(ip)
    return out


# The last connect-time refusal per (kind, name): an admin notice on Admin → Models.
_REFUSALS: dict[tuple[str, str], tuple[float, str]] = {}


def refusals() -> dict[tuple[str, str], tuple[float, str]]:
    return dict(_REFUSALS)


def _resolver(service: Service, private_ok: bool | None, public_only: bool, own=None):
    def resolve(host: str, port: int) -> list[str]:
        if own is not None:
            ips = own(host, port)
        elif service.trusted:
            ips = _lookup(host, port)
        else:
            ips = netguard.resolve_checked(host, port, public_only=public_only, private_ok=private_ok)
        if local(service):
            try:
                verify_local(host, ips)
            except NotLocal as e:
                _REFUSALS[(service.kind, service.name)] = (time.time(), str(e))
                log.warning("%s service %r refused at connect time: %s", service.kind, service.name, e)
                raise
            _REFUSALS.pop((service.kind, service.name), None)
        return ips
    return resolve


async def check(service: Service, *, private_ok: bool | None = None) -> str | None:
    """Run the connect-time check now: None when it passes, else the reason (Admin → Models)."""
    u = urlsplit(service.base_url)
    if not u.hostname:
        return "the URL has no host"
    port = u.port or (443 if u.scheme == "https" else 80)
    try:
        await asyncio.to_thread(_resolver(service, _private_ok(private_ok), False), u.hostname, port)
    except netguard.UnsafeTarget as e:
        return str(e)
    return None


def _private_ok(private_ok: bool | None) -> bool:
    # Services an admin set up may use the LAN unless the server refuses private hosts for everyone.
    return netguard.private_mode() != "none" if private_ok is None else private_ok


def client(service: Service, *, local_only: bool = False, private_ok: bool | None = None,
           public_only: bool = False, transport: httpx.AsyncBaseTransport | None = None,
           verify: Any = True, resolve: Any = None, **kw: Any) -> httpx.AsyncClient:
    """The HTTP client for one call to `service`. Refuses (Refused) a local-only call (declared, or
    a tainted scope) to a hosted service; checks the taint again when each request is sent; pins
    every connection to checked addresses; a local service must resolve to private or loopback
    addresses on every connection. `resolve(host, port)` replaces the address check (it returns the
    addresses to connect to, or raises netguard.UnsafeTarget); `transport` replaces the network
    (tests only)."""
    if service.kind not in KINDS:
        raise ValueError(f"unknown service kind {service.kind!r}")
    policy = service.kind in POLICY_KINDS
    if policy and not allowed(effective(local_only, kind=service.kind), service):
        raise Refused(f"local-only data is not sent to the hosted service {service.name!r}")

    async def on_request(request: httpx.Request) -> None:
        if policy and not allowed(local_only or tainted(), service):
            raise Refused(f"local-only data is not sent to the hosted service {service.name!r}")

    hooks = kw.pop("event_hooks", None) or {}
    hooks = {**hooks, "request": [on_request, *(hooks.get("request") or [])]}
    kw.setdefault("follow_redirects", False)
    if transport is None:
        transport = netguard.pinned_transport(verify=verify, resolve=_resolver(service, _private_ok(private_ok),
                                                                              public_only, resolve))
    return httpx.AsyncClient(transport=transport, trust_env=False, event_hooks=hooks, **kw)


def proxied_client(service: Service, proxy: str, *, local_only: bool = False, verify: Any = True,
                   **kw: Any) -> httpx.AsyncClient:
    """client() for a hosted service reached through the admin's egress proxy (core/egress.py): the
    proxy resolves names, so there is no pinning and no local service goes this way."""
    if local(service):
        raise ValueError("a local service is never reached through the egress proxy")
    if service.kind in POLICY_KINDS and not allowed(effective(local_only, kind=service.kind), service):
        raise Refused(f"local-only data is not sent to the hosted service {service.name!r}")

    async def on_request(request: httpx.Request) -> None:
        if service.kind in POLICY_KINDS and (local_only or tainted()):
            raise Refused(f"local-only data is not sent to the hosted service {service.name!r}")

    kw.setdefault("follow_redirects", False)
    return httpx.AsyncClient(proxy=proxy, trust_env=False, verify=verify, event_hooks={"request": [on_request]}, **kw)


def host_is_local_now(url: str) -> bool:
    """A blocking check that every address of the URL's host is private or loopback (no SSRF
    policy; for labels such as the Sentry DSN host and Speakr's own models)."""
    host = (urlsplit(url or "").hostname or "").lower()
    if not host:
        return False
    try:
        verify_local(host, _lookup(host, None))
    except netguard.UnsafeTarget:
        return False
    return True
