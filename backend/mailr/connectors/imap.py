"""Generic IMAP/SMTP connector.

Sync model, per folder: state {uidvalidity, last_uid, modseq}. New mail = UIDs above
last_uid (or everything / SINCE <date> on first sync), fetched oldest-first in batches;
state is saved after every batch, and UIDs already stored (Message.upstream refs) are
skipped, so a crash resumes without re-importing. A UIDVALIDITY change resets the folder
cursor; Message-ID dedupe in ingest keeps that from creating duplicates, and upstream
deletion refuses refs whose UIDVALIDITY no longer matches.

Modes:
  store   read-only sync; upstream deletion happens only in the retention job.
  mirror  also pulls flag changes (CONDSTORE when available) and folder removals from
          upstream, and writes local changes back (flags, copy/move between folders).
  import  one full pass over history, then idle.

imapclient is synchronous; every call runs in a worker thread under a per-session lock.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import ssl
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from typing import Any

import aiosmtplib
import imapclient
from imapclient.exceptions import IMAPClientError, LoginError

from mailr.connectors.base import Capabilities, MailConnector, TestResult, WriteBack, register
from mailr.connectors.presets import PRESETS
from mailr.core import netguard
from mailr.core.netguard import UnsafeTarget, check_host, check_host_sync

log = logging.getLogger("mailr.imap")

BATCH = 50
MAX_MESSAGE_BYTES = 75 * 1024 * 1024
IDLE_REFRESH = 4 * 60  # default; many servers and NATs drop idle connections after ~5-10 min
IDLE_CHECK_SECONDS = 10  # shorter than the runner's stop timeout
POISON_ATTEMPTS = 3
# A message the server did not return when asked: asked again after 5 min, doubling to a day, and
# skipped only after MISSING_TRIES (about three weeks) of that.
NOT_RETURNED = "not returned by the server"
MISSING_FIRST_PAUSE = timedelta(minutes=5)
MISSING_MAX_PAUSE = timedelta(hours=24)
MISSING_TRIES = 30
# Mirror removals (`known - present`): a listing that would remove more than SWEEP_MAX messages, or
# at least SWEEP_MIN and more than SWEEP_SHARE of what mailr has from the folder, is held (see
# ImapConnector._guarded_gone). A provider that throttles or answers a SEARCH in part must never
# look like mass deletion.
SWEEP_MAX = 200
SWEEP_MIN = 20
SWEEP_SHARE = 0.2
SWEEP_CONFIRM_PASSES = 3
SWEEP_CONFIRM_AGE = timedelta(hours=6)
BACKFILL_SLICE = 90  # seconds of older-mail import per pass (new mail is checked between passes)
ROLE_FLAGS = {b"\\sent": "sent", b"\\junk": "junk", b"\\trash": "trash", b"\\drafts": "drafts",
              b"\\archive": "archive", b"\\all": "all", b"\\flagged": "virtual", b"\\important": "virtual"}
ROLE_NAMES = {
    "sent": {"sent", "sent items", "sent messages", "sent mail", "sent-mail", "gesendet", "envoyés"},
    "junk": {"junk", "spam", "junk mail", "junk e-mail", "junk email", "bulk", "bulk mail"},
    "trash": {"trash", "deleted items", "deleted messages", "bin", "deleted", "papierkorb"},
    "drafts": {"drafts", "draft", "entwürfe"},
    "archive": {"archive", "archives", "all mail"},
}
DEFAULT_EXCLUDED_ROLES = {"trash", "drafts", "all", "virtual"}


class SendTemporaryError(Exception):
    """The server refused for now (4xx). Retrying later may work."""


class SendPermanentError(Exception):
    """The server refused permanently (5xx). Retrying will not help."""

    permanent = True


class SendUncertainError(Exception):
    """The connection dropped or timed out after the message was handed over (DATA): it may have
    gone out. Sending it again blindly could deliver it twice."""

    uncertain = True


class _TrackedSMTP(aiosmtplib.SMTP):
    """An SMTP client that notes when DATA starts: a failure before it never delivered anything,
    one after it may have."""

    data_started = False
    data_done = False

    async def data(self, message, /, **kw):  # type: ignore[override]
        self.data_started = True
        resp = await super().data(message, **kw)
        self.data_done = True
        return resp


class AuthError(Exception):
    pass


Target = tuple[str, str]  # ("role", "inbox") | ("label", "Clients/Acme")


def _decode_name(name: Any) -> str:
    return name.decode() if isinstance(name, bytes) else str(name)


def folder_target(name: str, flags: tuple, delimiter: str | None, preset_folders: dict[str, str] | None = None) -> Target | None:
    """Map an IMAP folder to a mailr role or label. None = not selectable."""
    lflags = {f.lower() if isinstance(f, bytes) else str(f).lower().encode() for f in flags}
    if b"\\noselect" in lflags or b"\\nonexistent" in lflags:
        return None
    if name.upper() == "INBOX":
        return ("role", "inbox")
    for f in lflags:
        if f in ROLE_FLAGS:
            return ("role", ROLE_FLAGS[f])
    for role, folder in (preset_folders or {}).items():
        if folder and name.lower() == folder.lower():
            return ("role", role)
    leaf = name.split(delimiter)[-1] if delimiter else name
    for role, names in ROLE_NAMES.items():
        if name.lower() in names or (leaf.lower() in names and name.upper().startswith("INBOX")):
            return ("role", role)
    parts = name.split(delimiter) if delimiter else [name]
    if len(parts) > 1 and parts[0].upper() == "INBOX":
        parts = parts[1:]
    return ("label", "/".join(p.strip() for p in parts if p.strip()))


def sweep_verdict(known: int, gone: int, exists: int, listed: int) -> str | None:
    """Why a mirror listing must not be trusted to remove `gone` of `known` stored messages, or
    None. "empty_listing" / "short_listing": the server listed fewer UIDs than the folder's own
    EXISTS count says it holds (a throttled or partial answer). "large": the removal is bigger
    than SWEEP_MAX, or than SWEEP_SHARE of the folder (from SWEEP_MIN messages on)."""
    if exists > 0 and listed == 0:
        return "empty_listing"
    if listed < exists - exists // 100:
        return "short_listing"
    if gone > SWEEP_MAX or (gone >= SWEEP_MIN and gone > SWEEP_SHARE * known):
        return "large"
    return None


def keywords_from_flags(flags: tuple) -> dict[str, bool]:
    fl = {(f.decode() if isinstance(f, bytes) else str(f)).lower() for f in flags or ()}
    return {"seen": "\\seen" in fl, "flagged": "\\flagged" in fl, "answered": "\\answered" in fl,
            "draft": "\\draft" in fl, "important": "$important" in fl or "\\important" in fl}


def target_kwargs(t: Target) -> dict[str, Any]:
    if t[0] == "role":
        return {"roles": [t[1]], "label_names": []}
    return {"roles": [], "label_names": [t[1]]}


TEST_TIMEOUT = 25


def _is_loopback(host: str) -> bool:
    import ipaddress

    if host in ("localhost",) or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def tls_context(cfg: dict[str, Any], host: str) -> ssl.SSLContext:
    """Certificate checks are skipped only for a loopback host with tls_verify=false (the
    Proton Bridge preset, which serves a self-signed certificate on 127.0.0.1). A preset's
    flag never disables verification for a remote server."""
    ctx = ssl.create_default_context()
    if cfg.get("tls_verify") is False and _is_loopback((host or "").lower()):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _transient(e: BaseException) -> bool:
    """Errors worth retrying the whole batch for (outages), as opposed to one bad message."""
    from sqlalchemy.exc import DBAPIError, OperationalError

    from mailr.core.jmap import JMAPError

    if isinstance(e, DBAPIError) and not isinstance(e, OperationalError):
        # A data or constraint error (SQLSTATE class 22/23) belongs to this message; retrying the
        # batch would stop the whole account on it forever.
        orig = getattr(e, "orig", None)
        state = getattr(orig, "sqlstate", None) or getattr(getattr(orig, "__cause__", None), "sqlstate", None)
        if state and str(state)[:2] in ("22", "23"):
            return False
        return True
    if isinstance(e, (OSError, TimeoutError, OperationalError, IMAPClientError)):
        return True
    if isinstance(e, JMAPError):
        return e.kind in ("http", "upload", "download") or "serverUnavailable" in str(e) or "serverFail" in str(e)
    try:
        import httpx

        if isinstance(e, httpx.HTTPError):
            return True
    except ImportError:
        pass
    return False


class _PinnedIMAPClient(imapclient.IMAPClient):
    """IMAPClient whose socket goes to an address netguard checked (no DNS rebinding between
    the check and the connect); TLS still verifies the server's name."""

    def __init__(self, *a: Any, private_ok: bool | None = None, **kw: Any):
        self._private_ok = private_ok
        super().__init__(*a, **kw)

    def _create_IMAP4(self):
        from imapclient import imap4
        from imapclient import tls as itls

        po = self._private_ok
        connect_timeout = getattr(self._timeout, "connect", None)

        def sock_for(conn, timeout):
            return netguard.connect_pinned(conn.host, conn.port, timeout if timeout is not None else conn._timeout,
                                           private_ok=po)

        if self.ssl:
            class Tls(itls.IMAP4_TLS):
                def _create_socket(self, timeout=None):
                    return itls.wrap_socket(sock_for(self, timeout), self.ssl_context, self.host)

            return Tls(self.host, self.port, self.ssl_context, connect_timeout)

        class Plain(imap4.IMAP4WithTimeout):
            def _create_socket(self, timeout=None):
                return sock_for(self, timeout)

        return Plain(self.host, self.port, connect_timeout)


class ImapClosed(RuntimeError):
    """An IMAP call on a session that is not connected (closed, or never opened)."""


class ImapSession:
    """One authenticated IMAP connection; all calls serialized through a lock.

    The blocking IMAP calls run on a thread of the session's own, retired with the session.
    A TLS failure leaves an entry in OpenSSL's per-thread error queue, and Python's ssl
    module then reports it on the next TLS read on that thread: on a shared pool, one
    provider's dead connection made healthy connections of other accounts fail with
    BrokenPipeError (test_imap_thread_isolation.py)."""

    def __init__(self, cfg: dict[str, Any], secrets: dict[str, Any], private_ok: bool | None = None):
        self.cfg = cfg
        self.secrets = secrets
        self.private_ok = private_ok
        self.client: imapclient.IMAPClient | None = None
        self.caps: set[str] = set()
        self._lock = asyncio.Lock()
        self.selected: str | None = None
        self.selected_rw = False
        self._thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix="imap")

    async def _run(self, fn: Callable[..., Any], *a: Any, **kw: Any) -> Any:
        return await asyncio.get_running_loop().run_in_executor(self._thread, lambda: fn(*a, **kw))

    def _ssl_context(self) -> ssl.SSLContext:
        return tls_context(self.cfg, (self.cfg.get("imap") or {}).get("host", ""))

    def _connect_sync(self) -> None:
        imap = self.cfg["imap"]
        tls = imap.get("tls", "ssl")
        check_host_sync(imap["host"], int(imap.get("port") or 0) or None, private_ok=self.private_ok)
        c = _PinnedIMAPClient(imap["host"], port=int(imap.get("port") or (993 if tls == "ssl" else 143)),
                              ssl=(tls == "ssl"), ssl_context=self._ssl_context(), timeout=90,
                              private_ok=self.private_ok)
        c.normalise_times = False
        if tls == "starttls":
            c.starttls(self._ssl_context())
        user = imap.get("username") or self.cfg.get("username") or self.cfg.get("email")
        try:
            if self.secrets.get("oauth", {}).get("access_token"):
                # XOAUTH2 hook: a token refresher fills secrets["oauth"]["access_token"].
                c.oauth2_login(user, self.secrets["oauth"]["access_token"])
            else:
                c.login(user, self.secrets.get("password", ""))
        except LoginError as e:
            try:
                c.shutdown()
            except Exception:  # noqa: BLE001, S110
                pass
            raise AuthError(f"sign-in failed for {user}: {e}") from e
        self.caps = {x.decode().upper() if isinstance(x, bytes) else str(x).upper() for x in c.capabilities()}
        if "ENABLE" in self.caps and "CONDSTORE" in self.caps:
            try:
                c.enable("CONDSTORE")
            except Exception:  # noqa: BLE001, S110
                pass
        self.client = c
        self.selected = None

    async def connect(self) -> None:
        async with self._lock:
            await self._run(self._connect_sync)

    @property
    def conn(self) -> imapclient.IMAPClient:
        """The open connection; a closed session fails with ImapClosed instead of a None error."""
        if self.client is None:
            raise ImapClosed("the IMAP session is not connected")
        return self.client

    async def call(self, fn: Callable[..., Any], *a: Any, **kw: Any) -> Any:
        async with self._lock:
            return await self._run(fn, *a, **kw)

    async def select(self, folder: str, readonly: bool = True) -> dict:
        def _sel():
            res = self.conn.select_folder(folder, readonly=readonly)
            self.selected, self.selected_rw = folder, not readonly
            return res
        return await self.call(_sel)

    async def close(self, hard: bool = False) -> None:
        """Log out politely, or (hard=True, or when another call is still using the
        connection, e.g. a blocked IDLE) close the socket at once. Closing the socket is what
        unblocks a thread stuck reading it; a LOGOUT sent mid-IDLE would never be answered."""
        if self.client is None:
            self._thread.shutdown(wait=False)
            return
        c, self.client = self.client, None
        try:
            if hard or self._lock.locked():
                try:
                    c.shutdown()  # socket close from this thread; safe while another thread reads
                except Exception:  # noqa: BLE001, S110
                    pass
                return
            try:
                await asyncio.wait_for(self._run(c.logout), timeout=10)
            except (Exception, TimeoutError):  # noqa: BLE001
                try:
                    c.shutdown()
                except Exception:  # noqa: BLE001, S110
                    pass
        finally:
            # The thread ends once its last call returns (a blocked read ends with the socket).
            self._thread.shutdown(wait=False)

    def has(self, cap: str) -> bool:
        return cap.upper() in self.caps


@register
class ImapConnector(MailConnector):
    kind = "imap"
    capabilities = Capabilities(push=True, send=True, write_back=True, delete=True, labels=False)

    def __init__(self, row, secrets):
        super().__init__(row, secrets)
        cfg = dict(row.config or {})
        preset = PRESETS.get(row.preset or "") or {}
        cfg.setdefault("imap", dict(preset.get("imap", {})))
        cfg.setdefault("smtp", dict(preset.get("smtp", {})) if preset.get("smtp") else None)
        if "tls_verify" not in cfg and preset.get("tls_verify") is False:
            cfg["tls_verify"] = False
        self.cfg = cfg
        self.preset_folders = preset.get("folders", {})
        self.session: ImapSession | None = None
        self._folders: list[tuple[str, tuple, str | None]] | None = None

    # ---------------- connection helpers ----------------

    async def _open(self) -> ImapSession:
        if self.session is None or self.session.client is None:
            s = ImapSession(self.cfg, self.secrets, self.private_ok)
            await s.connect()
            self.session = s
            self._folders = None
        return self.session

    async def _close(self, hard: bool = False) -> None:
        if self.session is not None:
            session, self.session = self.session, None
            await session.close(hard=hard)

    async def folders(self) -> list[tuple[str, tuple, str | None]]:
        if self._folders is None:
            s = await self._open()
            raw = await s.call(s.conn.list_folders)
            self._folders = [(_decode_name(n), tuple(f), (d.decode() if isinstance(d, bytes) else d)) for f, d, n in raw]
        return self._folders

    async def folder_plan(self) -> list[tuple[str, Target]]:
        """Folders to sync with their mailr target, INBOX first."""
        inc = [x.lower() for x in (self.cfg.get("folders") or {}).get("include") or []]
        exc = [x.lower() for x in (self.cfg.get("folders") or {}).get("exclude") or []]
        # Folders the user opted into although their role is off by default (Trash, Drafts).
        also = [x.lower() for x in (self.cfg.get("folders") or {}).get("also") or []]
        plan = []
        for name, flags, delim in await self.folders():
            t = folder_target(name, flags, delim, self.preset_folders)
            if t is None:
                continue
            if inc:
                if name.lower() not in inc:
                    continue
            elif t[0] == "role" and t[1] in DEFAULT_EXCLUDED_ROLES and name.lower() not in also:
                continue
            if name.lower() in exc:
                continue
            plan.append((name, t))
        plan.sort(key=lambda x: (x[1] != ("role", "inbox"), x[0].lower()))
        return plan

    async def folder_for(self, target: Target, create: bool = True) -> str | None:
        for name, flags, delim in await self.folders():
            if folder_target(name, flags, delim, self.preset_folders) == target:
                return name
        if target[0] == "role":
            name = self.preset_folders.get(target[1]) or {"archive": "Archive", "junk": "Junk", "trash": "Trash",
                                                          "sent": "Sent", "drafts": "Drafts"}.get(target[1])
        else:
            delim = next((d for _, _, d in await self.folders() if d), "/")
            name = target[1].replace("/", delim)
        if not name or not create:
            return None
        s = await self._open()
        try:
            await s.call(s.conn.create_folder, name)
        except IMAPClientError as e:
            if "exist" not in str(e).lower():
                raise
        self._folders = None
        return name

    # ---------------- contract ----------------

    async def test(self) -> TestResult:
        async def probe():
            await self._open()
            return await self.folder_plan(), await self.folders(), sorted(self.session.caps)

        try:
            plan, all_f, caps = await asyncio.wait_for(probe(), timeout=TEST_TIMEOUT)
        except TimeoutError:
            host = self.cfg["imap"].get("host")
            return TestResult(ok=False, detail=f"{host} did not answer within {TEST_TIMEOUT} seconds. "
                                               "Check the server name, port, and security setting.")
        except (AuthError, UnsafeTarget) as e:
            return TestResult(ok=False, detail=str(e))
        except (OSError, IMAPClientError, ssl.SSLError) as e:
            return TestResult(ok=False, detail=f"could not connect to {self.cfg['imap'].get('host')}: {e}")
        finally:
            await self._close()
        smtp_detail = ""
        if self.cfg.get("smtp") and self.cfg["smtp"].get("host"):
            ok, smtp_detail = await self._smtp_check()
            if not ok:
                return TestResult(ok=False, detail=f"IMAP works, but sending failed: {smtp_detail}",
                                  extensions=caps)
        planned = {n for n, _ in plan}
        folders = [{"name": n, "target": list(t) if t else None, "sync": n in planned}
                   for n, f, d in all_f for t in [folder_target(n, f, d, self.preset_folders)]]
        email = self.cfg.get("email") or self.cfg["imap"].get("username")
        return TestResult(ok=True, detail=smtp_detail or "Receiving works; no outgoing server set.",
                          folders=folders, identities=[email] if email and "@" in email else [],
                          extensions=caps)

    async def sync_once(self, ctx) -> dict[str, int]:
        """One full pass over all planned folders (initial import or catch-up). Used by run()
        and directly by tests."""
        await self._open()
        totals = {"new": 0, "duplicates": 0}
        plan = await self.folder_plan()
        state = ctx.state
        state.setdefault("folders", {})
        if state.get("initial_done") and not state.get("initial_done_at"):
            # Accounts whose first sync finished before this was recorded: mail that arrives
            # from now on proves mail flow for arming upstream deletion.
            state["initial_done_at"] = datetime.now(UTC).isoformat()
        self._history = self.row.mode == "import" or not state.get("initial_done")
        self._first_pass = not state.get("initial_done")
        pass_started = datetime.now(UTC)
        completed = True
        for folder, target in plan:
            if ctx.stopping.is_set():
                completed = False
                break
            r = await self._sync_folder(ctx, folder, target)
            totals["new"] += r["new"]
            totals["duplicates"] += r["duplicates"]
        self._history = False
        if completed:
            planned = {f for f, _t in plan}
            for f in [f for f in state.get("sync_warnings") or {} if f not in planned]:
                state["sync_warnings"].pop(f, None)  # a folder no longer synced holds nothing back
            if not state.get("initial_done"):
                state["initial_done_at"] = datetime.now(UTC).isoformat()
            state["initial_done"] = True
            if self.row.mode == "mirror" and hasattr(ctx, "finalize_orphans"):
                await ctx.finalize_orphans(pass_started)
        await ctx.save_state(state)
        if completed and self.backfill_pending(state) and not ctx.stopping.is_set():
            totals["backfilled"] = await self.backfill(ctx, plan)
        return totals

    def backfill_pending(self, state: dict[str, Any]) -> bool:
        """Recent mail first (config `since`), then the older mail (config `backfill`): true while
        the older mail is still to come."""
        return bool(self.cfg.get("backfill") and self.cfg.get("since")
                    and not (state.get("backfill") or {}).get("done"))

    async def backfill(self, ctx, plan: list[tuple[str, Target]]) -> int:
        """Import the mail from before `since` (back to `backfill_since`, or all of it), newest
        first, after the recent mail is in. Like the first import it runs no filters and sends no
        notifications. Progress is in state["backfill"] {total, imported, done}; it survives a
        restart (mail already stored is skipped). Each call works for at most BACKFILL_SLICE seconds;
        run() calls again at once while older mail remains, checking for new mail in between."""
        state = ctx.state
        bf = state.setdefault("backfill", {})
        bf.setdefault("started_at", datetime.now(UTC).isoformat())
        folders = bf.setdefault("folders", {})
        s = await self._open()
        before = _to_date(self.cfg["since"])
        oldest = self.cfg.get("backfill_since")
        override = {"notify": False, "run_rules": bool(self.cfg.get("apply_filters_to_history"))}
        deadline = asyncio.get_running_loop().time() + BACKFILL_SLICE
        work: list[tuple[str, Target, int, list[int]]] = []
        for folder, target in plan:
            sel = await s.select(folder, readonly=True)
            uv = int(sel.get(b"UIDVALIDITY", 0))
            if int(sel.get(b"EXISTS", 0)) == 0:
                continue
            crit: list[Any] = ["BEFORE", before] + (["SINCE", _to_date(oldest)] if oldest else [])
            uids = await s.call(s.conn.search, crit)
            if not uids:
                continue
            known = await ctx.known_upstream(folder, uv, min(uids))
            gave_up = {int(u) for u in (folders.get(folder) or {}).get("skipped", {})}
            todo = sorted((u for u in uids if u not in known and u not in gave_up), reverse=True)  # newest first
            if todo:
                work.append((folder, target, uv, todo))
        remaining = sum(len(t) for *_, t in work)
        bf["total"] = int(bf.get("imported") or 0) + remaining
        bf.setdefault("imported", 0)
        await ctx.save_state(state)
        items = await self._fetch_items()
        new = 0
        self._history = True
        try:
            for folder, target, uv, todo in work:
                await s.select(folder, readonly=True)
                await self._before_folder(folder, target)
                ffs = folders.setdefault(folder, {})
                for i in range(0, len(todo), BATCH):
                    if ctx.stopping.is_set() or asyncio.get_running_loop().time() > deadline:
                        # A slice at a time, so new mail is still picked up between slices.
                        return new
                    chunk = todo[i:i + BATCH]
                    result = {"new": 0, "duplicates": 0}
                    await self._ingest_batch(ctx, s, folder, target, uv, chunk, items, ffs, result, override)
                    new += result["new"]
                    bf["imported"] = int(bf.get("imported") or 0) + len(chunk)
                    await ctx.save_state(state)
                    await ctx.set_status("syncing", f"Importing older mail: {bf['imported']:,} of {bf['total']:,}")
        finally:
            self._history = False
        bf["done"] = True
        bf["done_at"] = datetime.now(UTC).isoformat()
        bf["imported"] = bf.get("total", bf.get("imported", 0))
        await ctx.save_state(state)
        return new

    async def _before_folder(self, folder: str, target: Target) -> None:
        """Hook for subclasses (Gmail precomputes categories)."""
        return

    async def _fetch_items(self) -> list[bytes]:
        return [b"BODY.PEEK[]", b"FLAGS", b"INTERNALDATE", b"RFC822.SIZE"]

    def _ingest_kwargs(self, folder: str, target: Target, uidvalidity: int, uid: int, data: dict) -> dict[str, Any] | None:
        kw = target_kwargs(target)
        kw.update({
            "keywords": keywords_from_flags(data.get(b"FLAGS", ())),
            "received_at": _aware(data.get(b"INTERNALDATE")),
            "upstream": {"connector": self.row.id, "folder": folder, "uidvalidity": int(uidvalidity), "uid": int(uid)},
            "merge_labels": self.row.mode == "mirror",
        })
        if getattr(self, "_history", False):
            # Mail that was already there when the account was added (or an import): no
            # filters (unless the user asked), no forwards, no push notifications.
            kw.update({"source": "import", "notify": False,
                       "run_rules": bool(self.cfg.get("apply_filters_to_history"))})
        return kw

    async def _sync_folder(self, ctx, folder: str, target: Target) -> dict[str, int]:
        s = await self._open()
        sel = await s.select(folder, readonly=True)
        selected_at = datetime.now(UTC)
        uv = int(sel.get(b"UIDVALIDITY", 0))
        modseq = sel.get(b"HIGHESTMODSEQ")
        exists = int(sel.get(b"EXISTS", 0))
        fs = ctx.state["folders"].setdefault(folder, {})
        if fs.get("uidvalidity") not in (None, uv):
            log.warning("connector %s: UIDVALIDITY of %s changed %s → %s; re-scanning (dedupe prevents duplicates)",
                        self.row.id, folder, fs.get("uidvalidity"), uv)
            if hasattr(ctx, "forget_uidvalidity"):
                await ctx.forget_uidvalidity(folder, int(fs["uidvalidity"]))
            fs.clear()
        first = not fs.get("uidvalidity")
        fs["uidvalidity"] = uv
        last_uid = int(fs.get("last_uid") or 0)
        result = {"new": 0, "duplicates": 0}

        if exists == 0:
            uids: list[int] = []
        elif last_uid == 0:
            since = self.cfg.get("since")
            crit: list[Any] = ["SINCE", _to_date(since)] if since else ["ALL"]
            uids = sorted(await s.call(s.conn.search, crit))
        else:
            uids = sorted(u for u in await s.call(s.conn.search, ["UID", f"{last_uid + 1}:*"]) if u > last_uid)

        failed: dict[str, dict] = fs.setdefault("failed", {})
        now = datetime.now(UTC)
        gave_up = fs.get("skipped") or {}
        for u in [u for u, why in gave_up.items() if why == NOT_RETURNED and u not in failed]:
            # Skipped for good by an older version after three passes without an answer: tried
            # again, with the pause and limit above.
            gave_up.pop(u)
            failed[u] = {"attempts": 0, "missing": POISON_ATTEMPTS, "error": NOT_RETURNED}
        retry = [int(u) for u, f in failed.items() if f.get("attempts", 0) < POISON_ATTEMPTS
                 and (not f.get("retry_at") or datetime.fromisoformat(f["retry_at"]) <= now)]
        if retry:
            uids = sorted(set(uids) | set(retry))
        if uids:
            known = await ctx.known_upstream(folder, uv, min(uids))
            todo = [u for u in uids if u not in known]
            skipped_max = max((u for u in uids if u in known), default=0)
            stopped = False
            total = len(todo)
            items = await self._fetch_items()
            await self._before_folder(folder, target)
            skipped: dict[str, str] = fs.setdefault("skipped", {})
            for i in range(0, total, BATCH):
                if ctx.stopping.is_set():
                    stopped = True
                    break
                chunk = todo[i:i + BATCH]
                await self._ingest_batch(ctx, s, folder, target, uv, chunk, items, fs, result)
                fs["last_uid"] = max(fs.get("last_uid") or 0, max(chunk))
                await ctx.save_state(ctx.state)
                if first or total > BATCH:
                    await ctx.set_status("syncing", f"Importing {folder}: {min(i + BATCH, total):,} of {total:,}")
            if not stopped and skipped_max > int(fs.get("last_uid") or 0):
                fs["last_uid"] = skipped_max
            for u in list(failed):
                if failed[u].get("attempts", 0) >= POISON_ATTEMPTS:
                    skipped[u] = failed.pop(u).get("error", "failed")
            result["skipped"] = len(skipped)
        if self.row.mode == "mirror" and not first:
            await self._pull_remote_changes(ctx, folder, target, uv, fs)
        if modseq is not None:
            fs["modseq"] = int(modseq)
        # Upstream changes reported by the next pass happened after this moment (apply_remote).
        fs["pulled_at"] = selected_at.isoformat()
        await ctx.save_state(ctx.state)
        return result

    async def _ingest_batch(self, ctx, s, folder: str, target: Target, uv: int, chunk: list[int], items: list[bytes],
                            fs: dict, result: dict[str, int], override: dict[str, Any] | None = None) -> None:
        """Fetch and store one batch of UIDs. A message the server does not return, or that cannot
        be stored, is retried on later passes and skipped after POISON_ATTEMPTS. `override` is
        merged into each message's ingest options (the backfill turns notifications off)."""
        failed: dict[str, dict] = fs.setdefault("failed", {})
        skipped: dict[str, str] = fs.setdefault("skipped", {})
        sizes = await s.call(s.conn.fetch, chunk, [b"RFC822.SIZE"])
        ok_uids = [u for u in chunk if int(sizes.get(u, {}).get(b"RFC822.SIZE", 0)) <= MAX_MESSAGE_BYTES]
        for big in set(chunk) - set(ok_uids):
            log.warning("connector %s: skipping %s UID %s (larger than %s bytes)", self.row.id, folder, big,
                        MAX_MESSAGE_BYTES)
            skipped[str(big)] = f"larger than {MAX_MESSAGE_BYTES // (1024 * 1024)} MB"
        data = await s.call(s.conn.fetch, ok_uids, items) if ok_uids else {}
        now = datetime.now(UTC)
        for gone in set(ok_uids) - set(data):
            # The server did not return it (e.g. Gmail throttling). That says nothing about the
            # message, so it is not an attempt towards POISON_ATTEMPTS: it is asked for again with
            # a growing pause (up to a day), and given up only after MISSING_TRIES.
            f = failed.setdefault(str(gone), {"attempts": 0})
            f["missing"] = int(f.get("missing") or 0) + 1
            f["error"] = NOT_RETURNED
            f["retry_at"] = (now + min(MISSING_MAX_PAUSE, MISSING_FIRST_PAUSE * 2 ** (f["missing"] - 1))).isoformat()
            if f["missing"] >= MISSING_TRIES:
                skipped[str(gone)] = failed.pop(str(gone))["error"]
        for uid in sorted(data):
            raw = data[uid].get(b"BODY[]")
            if not raw:
                continue
            kw = self._ingest_kwargs(folder, target, uv, uid, data[uid])
            if kw is None:
                continue
            if override:
                kw.update(override)
            try:
                res = await ctx.ingest(raw, **kw)
            except Exception as e:
                if _transient(e):
                    raise  # network / database / store outage: retry the whole batch later
                # A message mailr cannot parse or store must not block the account.
                f = failed.setdefault(str(uid), {"attempts": 0})
                f["attempts"] += 1
                f["error"] = f"{type(e).__name__}: {e}"[:300]
                log.warning("connector %s: %s UID %s failed (%s/%s): %s", self.row.id, folder, uid,
                            f["attempts"], POISON_ATTEMPTS, e)
                if f["attempts"] >= POISON_ATTEMPTS:
                    skipped[str(uid)] = f["error"]
                continue
            failed.pop(str(uid), None)
            result["duplicates" if res.get("duplicate") else "new"] += 1

    async def _pull_remote_changes(self, ctx, folder: str, target: Target, uv: int, fs: dict) -> None:
        """Mirror mode: apply upstream flag changes and removals to mailr."""
        s = await self._open()
        last_uid = int(fs.get("last_uid") or 0)
        if last_uid == 0:
            return
        listing = await self._list_present(s, folder, uv, last_uid)
        if listing is None:
            return  # the folder went away or its UIDVALIDITY changed: the next pass rescans it
        present, exists, listed = listing
        if not present:
            changed = {}
        elif s.has("CONDSTORE") and fs.get("modseq"):
            changed = await s.call(s.conn.fetch, sorted(present), [b"FLAGS"],
                                   modifiers=[f"CHANGEDSINCE {fs['modseq']}"])
        else:
            recent = sorted(present)[-2000:]
            changed = await s.call(s.conn.fetch, recent, [b"FLAGS"])
        if changed:
            await ctx.apply_remote(folder, uv, {int(u): keywords_from_flags(d.get(b"FLAGS", ())) for u, d in changed.items()},
                                   since=pulled_since(fs))
        known = await ctx.known_upstream(folder, uv)
        gone = await self._guarded_gone(ctx, s, folder, uv, fs, known, present, exists, listed, last_uid)
        if gone:
            await ctx.vanished(folder, uv, gone, target)

    async def _list_present(self, s, folder: str, uv: int, last_uid: int) -> tuple[set[int], int, int] | None:
        """The folder's UIDs up to `last_uid`, its EXISTS count and the number of UIDs the server
        listed, from a fresh SELECT (the one at the start of the pass is old by now). None when
        the folder's UIDVALIDITY changed meanwhile."""
        sel = await s.select(folder, readonly=True)
        if int(sel.get(b"UIDVALIDITY", 0)) != int(uv):
            return None
        exists = int(sel.get(b"EXISTS", 0))
        listed = {int(u) for u in await s.call(s.conn.search, ["ALL"])} if exists else set()
        return {u for u in listed if u <= last_uid}, exists, len(listed)

    async def _guarded_gone(self, ctx, s, folder: str, uv: int, fs: dict, known: set[int], present: set[int],
                            exists: int, listed: int, last_uid: int) -> list[int]:
        """The stored UIDs that really left the folder upstream: `known - present`, unless the
        listing is suspect (sweep_verdict). A suspect listing is read again; a UID either listing
        shows stays. Still suspect: nothing is removed, the account shows a warning
        (state["sync_warnings"], accounthealth "deletions_held"), and a large removal that every
        pass agrees on is applied only after SWEEP_CONFIRM_PASSES passes over SWEEP_CONFIRM_AGE.
        A listing that contradicts the folder's own count is never applied."""
        gone = known - present
        verdict = sweep_verdict(len(known), len(gone), exists, listed) if gone else None
        if verdict:
            again = await self._list_present(s, folder, uv, last_uid)
            if again is None:
                return []
            present2, exists, listed = again
            gone -= present2
            verdict = sweep_verdict(len(known), len(gone), exists, listed) if gone else None
        warnings: dict[str, Any] = ctx.state.setdefault("sync_warnings", {})
        if not verdict:
            fs.pop("held_gone", None)
            warnings.pop(folder, None)
            return sorted(gone)
        now = datetime.now(UTC)
        sig = hashlib.sha256(",".join(map(str, sorted(gone))).encode()).hexdigest()[:16]
        held = fs.get("held_gone") or {}
        if verdict == "large" and held.get("sig") == sig and held.get("verdict") == "large":
            held["passes"] = int(held.get("passes") or 1) + 1
        else:
            held = {"sig": sig, "verdict": verdict, "since": now.isoformat(), "passes": 1}
        held["count"] = len(gone)
        fs["held_gone"] = held
        if (verdict == "large" and held["passes"] >= SWEEP_CONFIRM_PASSES
                and now - datetime.fromisoformat(held["since"]) >= SWEEP_CONFIRM_AGE):
            log.warning("connector %s: %s: %d messages gone upstream on %d passes since %s; applying",
                        self.row.id, folder, len(gone), held["passes"], held["since"])
            fs.pop("held_gone", None)
            warnings.pop(folder, None)
            return sorted(gone)
        log.warning("connector %s: %s: not removing %d of %d messages (%s: %d listed, EXISTS %d); held",
                    self.row.id, folder, len(gone), len(known), verdict, listed, exists)
        warnings[folder] = {"code": "deletions_held", "reason": verdict, "count": len(gone),
                            "since": held["since"]}
        return []

    async def run(self, ctx) -> None:
        backoff = 5
        poll = max(1, int(self.cfg.get("poll_minutes") or 5)) * 60
        if self.row.mode == "import" and ctx.state.get("initial_done") and not self.backfill_pending(ctx.state):
            # A finished import stays finished: no new mail is pulled after a worker restart.
            # "Import again" (POST /connectors/{id}/sync) sets import_again for
            # one more pass.
            if not ctx.state.pop("import_again", False):
                await ctx.set_status("idle", "Import finished")
                await _wait(ctx.stopping, 3600 * 24 * 365)
                return
            await ctx.save_state(ctx.state)
        while not ctx.stopping.is_set():
            try:
                await ctx.set_status("syncing", "Connecting…")
                await self._open()
                totals = await self.sync_once(ctx)
                detail = f"Up to date ({totals['new']:,} new)" if totals["new"] else "Up to date"
                await ctx.set_status("idle", detail, synced=True)
                backoff = 5
                if self.row.mode == "import" and not self.backfill_pending(ctx.state):
                    await ctx.set_status("idle", "Import finished", synced=True)
                    await _wait(ctx.stopping, 3600 * 24 * 365)
                    return
                if self.backfill_pending(ctx.state):
                    # Older mail is still coming in: the next slice right away.
                    await _wait(ctx.stopping, 1)
                    continue
                await self._idle_with_reconnect(ctx, poll)
            except AuthError as e:
                await ctx.set_status("error", str(e))
                await self._close()
                await _wait(ctx.stopping, 1800)
            except asyncio.CancelledError:
                await self._close(hard=True)
                raise
            except Exception as e:  # noqa: BLE001  network drops, server errors
                log.warning("connector %s: %s: %s", self.row.id, type(e).__name__, e)
                await ctx.set_status("error", f"{type(e).__name__}: {e}"[:500] + f" (retrying in {backoff}s)")
                await self._close()
                await _wait(ctx.stopping, backoff)
                backoff = min(backoff * 2, 300)
        await self._close()

    async def _idle_with_reconnect(self, ctx, poll: int) -> None:
        """Providers and NATs drop idle IMAP connections; that is normal, not an error.
        Reconnect at once, catch up the inbox only (cheap), and resume IDLE. Repeated drops
        in quick succession, or a failed reconnect, go to run()'s error handling."""
        drops: list[float] = []
        while not ctx.stopping.is_set():
            try:
                await self._idle_loop(ctx, poll)
                return
            except (OSError, IMAPClientError, ssl.SSLError, EOFError, TimeoutError) as e:
                now = time.monotonic()
                drops = [t for t in drops if now - t < 120] + [now]
                if len(drops) > 5:
                    raise
                log.info("connector %s: connection dropped (%s); reconnecting", self.row.id, type(e).__name__)
                await self._close()
                if ctx.stopping.is_set():
                    return
                await asyncio.sleep(1)
                await self._open()
                plan = dict(await self.folder_plan())
                inbox = next((f for f, t in plan.items() if t == ("role", "inbox")), None)
                if inbox:
                    await self._sync_folder(ctx, inbox, plan[inbox])

    async def _idle_loop(self, ctx, poll: int) -> None:
        s = await self._open()
        plan = dict(await self.folder_plan())
        inbox = next((f for f, t in plan.items() if t == ("role", "inbox")), None)
        last_full = time.monotonic()
        synced_next = None  # the inbox's UIDNEXT when it was last synced here
        while not ctx.stopping.is_set():
            if inbox and s.has("IDLE"):
                sel = await s.select(inbox, readonly=True)
                # Mail that arrived since the last sync (before this loop, or while the last
                # push was being synced) raises no IDLE notice; it would wait for the refresh.
                uidnext = sel.get(b"UIDNEXT")
                if uidnext is None or uidnext != synced_next:
                    await self._sync_folder(ctx, inbox, plan[inbox])
                    synced_next = uidnext
                    sel = await s.select(inbox, readonly=True)
                    if sel.get(b"UIDNEXT") != synced_next:
                        continue  # more arrived during that sync
                await s.call(s.conn.idle)
                started = time.monotonic()
                got = []
                try:
                    while not ctx.stopping.is_set():
                        got = await s.call(s.conn.idle_check, IDLE_CHECK_SECONDS)
                        refresh = int(self.cfg.get("idle_refresh_minutes") or 0) * 60 or IDLE_REFRESH
                        if got or time.monotonic() - started > refresh or time.monotonic() - last_full > poll:
                            break
                except asyncio.CancelledError:
                    # Stopping mid-IDLE: drop the connection instead of a DONE/LOGOUT exchange
                    # that would race the thread still reading the socket.
                    await self._close(hard=True)
                    raise
                await s.call(s.conn.idle_done)
                if got and any(b"EXISTS" in r or b"FETCH" in r or b"EXPUNGE" in r for r in got if isinstance(r, tuple)):
                    await self._sync_folder(ctx, inbox, plan[inbox])
                    await ctx.set_status("idle", "Up to date", synced=True)
            else:
                await _wait(ctx.stopping, min(poll, 120))
                if inbox:
                    await self._sync_folder(ctx, inbox, plan[inbox])
            if time.monotonic() - last_full > poll:
                self._folders = None
                plan = dict(await self.folder_plan())
                pass_started = datetime.now(UTC)
                skipped = 0
                for f, t in plan.items():
                    skipped += (await self._sync_folder(ctx, f, t)).get("skipped", 0)
                if self.row.mode == "mirror" and hasattr(ctx, "finalize_orphans"):
                    await ctx.finalize_orphans(pass_started)
                last_full = time.monotonic()
                await ctx.set_status("idle", "Up to date" + (f" ({skipped} message(s) could not be imported; "
                                                             "see the account details)" if skipped else ""),
                                     synced=True)

    # ---------------- sending ----------------

    def _smtp_params(self) -> dict[str, Any]:
        smtp = self.cfg.get("smtp") or {}
        if not smtp.get("host"):
            raise SendPermanentError("this account has no outgoing (SMTP) server configured")
        tls = smtp.get("tls", "ssl")
        user = smtp.get("username") or self.cfg["imap"].get("username") or self.cfg.get("email")
        pw = self.secrets.get("smtp_password") or self.secrets.get("password")
        tls_ctx = tls_context(self.cfg, smtp["host"])
        params: dict[str, Any] = {
            "hostname": smtp["host"], "port": int(smtp.get("port") or (465 if tls == "ssl" else 587)),
            "use_tls": tls == "ssl", "start_tls": tls == "starttls", "timeout": 60, "tls_context": tls_ctx,
        }
        if tls == "none":
            params["start_tls"] = False
        if smtp.get("auth", True) and pw:
            params["username"], params["password"] = user, pw
        return params

    async def _pinned_smtp(self, params: dict[str, Any], timeout: float) -> dict[str, Any]:
        """The SMTP parameters with a socket already connected to a checked address (the
        hostname stays for TLS and STARTTLS)."""
        sock = await asyncio.to_thread(netguard.connect_pinned, params["hostname"], params["port"], timeout,
                                       private_ok=self.private_ok)
        sock.setblocking(False)
        return {**params, "sock": sock, "port": None}

    async def _smtp_check(self) -> tuple[bool, str]:
        try:
            params = self._smtp_params()
            await check_host(params["hostname"], params["port"], private_ok=self.private_ok)
            params = await self._pinned_smtp(params, 15)
            client = aiosmtplib.SMTP(**{**{k: v for k, v in params.items() if k not in ("username", "password")},
                                        "timeout": 15})
            await client.connect()
            if params.get("username"):
                await client.login(params["username"], params["password"])
            await client.quit()
            return True, "Sending works."
        except UnsafeTarget as e:
            return False, str(e)
        except aiosmtplib.SMTPAuthenticationError as e:
            return False, f"SMTP sign-in failed: {e.message}"
        except (aiosmtplib.SMTPException, OSError) as e:
            return False, f"could not reach the SMTP server: {e}"
        except SendPermanentError as e:
            return False, str(e)

    async def send(self, raw: bytes, mail_from: str, rcpt_to: list[str]) -> str:
        params = self._smtp_params()
        try:
            await check_host(params["hostname"], params["port"], private_ok=self.private_ok)
        except UnsafeTarget as e:
            raise SendPermanentError(str(e)) from e
        client: _TrackedSMTP | None = None
        errors: dict[str, Any] = {}
        response = ""
        try:
            try:
                pinned = await self._pinned_smtp(params, 60)
            except UnsafeTarget as e:
                raise SendPermanentError(str(e)) from e
            client = _TrackedSMTP(**pinned)
            async with client:
                errors, response = await client.sendmail(mail_from, rcpt_to, raw)
        except aiosmtplib.SMTPRecipientsRefused as e:
            codes = [r.code for r in e.recipients]
            detail = "; ".join(f"{r.recipient}: {r.code} {r.message}" for r in e.recipients)
            if all(400 <= c < 500 for c in codes):
                raise SendTemporaryError(detail) from e
            raise SendPermanentError(f"recipients refused: {detail}") from e
        except aiosmtplib.SMTPResponseException as e:
            if 400 <= e.code < 500:
                raise SendTemporaryError(f"{e.code} {e.message}") from e
            raise SendPermanentError(f"{e.code} {e.message}") from e
        except (aiosmtplib.SMTPConnectError, aiosmtplib.SMTPServerDisconnected, aiosmtplib.SMTPTimeoutError, OSError) as e:
            if client is not None and client.data_done:
                log.info("smtp %s: the message was accepted; the connection ended at QUIT: %s", params["hostname"], e)
            elif client is not None and client.data_started:
                raise SendUncertainError(f"the connection to {params['hostname']} ended after the message was "
                                         f"handed over: {e}") from e
            else:
                raise SendTemporaryError(f"could not reach {params['hostname']}: {e}") from e
        if errors:
            # Some recipients were refused; the message went to the rest.
            bad = ", ".join(f"{k} ({v[0]})" for k, v in errors.items())
            return f"{response} (refused: {bad})"
        return response

    # ---------------- sent mail at the provider (connectors/sentcopy.py) ----------------

    async def sent_folder(self) -> str:
        """The provider's Sent folder, found as for sync: SPECIAL-USE \\Sent, the preset's name, then
        the usual names (folder_target). "Sent" (or the preset's name) is made when there is none."""
        name = await self.folder_for(("role", "sent"))
        assert name is not None  # a role always has a name to create
        return name

    async def message_ids(self, folder: str, uids: list[int] | None = None) -> tuple[int, dict[str, int]]:
        """UIDVALIDITY and {Message-ID: UID} of a folder (all of it, or these UIDs), read in batches of
        headers only. A message without a Message-ID is left out; of two with one id, the newer wins."""
        from mailr.core.mime import parse_msgids

        s = await self._open()
        sel = await s.select(folder, readonly=True)
        uv = int(sel.get(b"UIDVALIDITY", 0))
        if uids is None:
            uids = sorted(await s.call(s.conn.search, ["ALL"])) if int(sel.get(b"EXISTS", 0)) else []
        out: dict[str, int] = {}
        for i in range(0, len(uids), 500):
            got = await s.call(s.conn.fetch, uids[i:i + 500], [b"BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)]"])
            for u, d in sorted(got.items()):
                v = next((x for k, x in d.items() if isinstance(k, bytes) and k.startswith(b"BODY[HEADER")), b"") or b""
                ids = parse_msgids(v.decode("utf-8", "replace"))
                if len(ids) == 1:
                    out[ids[0]] = int(u)
        return uv, out

    async def find_message_id(self, folder: str, message_id: str, size: int) -> tuple[int, int | None]:
        """UIDVALIDITY and the UID of the message in `folder` with exactly this Message-ID, or None.
        SEARCH HEADER is a substring match and some servers find nothing with it (the dev
        Stalwart): its hits, else the messages of exactly `size` bytes, are only candidates, and
        the parsed Message-ID decides."""
        from mailr.core.mime import parse_msgids

        want = parse_msgids(message_id)
        s = await self._open()
        sel = await s.select(folder, readonly=True)
        uv = int(sel.get(b"UIDVALIDITY", 0))
        if len(want) != 1 or not int(sel.get(b"EXISTS", 0)):
            return uv, None
        try:
            found = await s.call(s.conn.search, ["HEADER", "Message-ID", want[0]])
        except Exception:  # noqa: BLE001  some servers reject HEADER search
            found = []
        if not found:
            try:
                found = await s.call(s.conn.search, ["LARGER", size - 1, "SMALLER", size + 1])
            except Exception:  # noqa: BLE001
                found = []
        if not found:
            return uv, None
        _uv, ids = await self.message_ids(folder, sorted(int(u) for u in found)[-50:])
        return uv, ids.get(want[0])

    async def append_sent(self, folder: str, raw: bytes, message_id: str, when: datetime | None = None,
                          check: bool = True) -> dict[str, Any]:
        """Put a sent message in `folder`, flagged \\Seen, unless a copy with its Message-ID is
        already there (an earlier attempt, or the provider's own copy; `check` False: the caller
        looked). Returns {folder, uidvalidity, uid, existed}; uid is None only when the server
        neither reports APPENDUID nor finds the copy afterwards."""
        if check:
            uv, uid = await self.find_message_id(folder, message_id, len(raw))
            if uid is not None:
                return {"folder": folder, "uidvalidity": uv, "uid": uid, "existed": True}
        s = await self._open()
        resp = await s.call(s.conn.append, folder, raw, flags=(b"\\Seen",), msg_time=when)
        m = re.search(rb"APPENDUID (\d+) (\d+)", resp if isinstance(resp, bytes) else str(resp).encode())
        if m:
            return {"folder": folder, "uidvalidity": int(m.group(1)), "uid": int(m.group(2)), "existed": False}
        uv, uid = await self.find_message_id(folder, message_id, len(raw))
        return {"folder": folder, "uidvalidity": uv, "uid": uid, "existed": False}

    # ---------------- mirror write-back ----------------

    FLAG_BATCH = 500

    async def _batch_flags(self, changes: list[WriteBack], ref_ok) -> list[dict[str, Any]] | None:
        """Flag-only write-backs (read/unread, star) of many messages: one SELECT per folder and one
        UID STORE per FLAG_BATCH messages, instead of a round trip per message. None when some change
        does more than flags (the per-message path handles those)."""
        if not changes or any(ch.add_labels or ch.remove_labels or ch.delete for ch in changes):
            return None
        s = await self._open()
        groups: dict[tuple, list[int]] = {}
        for ch in changes:
            ops = tuple(op for op in (("\\Seen", ch.seen), ("\\Flagged", ch.flagged)) if op[1] is not None)
            for r in all_refs(ch.upstream):
                if str(r.get("connector")) == str(self.row.id) and ref_ok(r) and ops:
                    groups.setdefault((r["folder"], r["uidvalidity"], ops), []).append(int(r["uid"]))
        for (folder, uv, ops), uids in groups.items():
            if not await self._select_ref({"folder": folder, "uidvalidity": uv}):
                continue
            for i in range(0, len(uids), self.FLAG_BATCH):
                chunk = uids[i:i + self.FLAG_BATCH]
                for flag, on in ops:
                    await s.call(s.conn.add_flags if on else s.conn.remove_flags, chunk, [flag.encode()])
        return [ch.upstream for ch in changes]

    async def write_back(self, changes: list[WriteBack]) -> list[dict[str, Any]]:
        """Apply local changes upstream. Returns the updated Message.upstream for each change
        (refs change when messages are copied or moved).

        A message can have several provider copies: label folders mailr wrote, a copy a provider
        filter put in a second folder, and for mail to oneself the delivered copy (INBOX) beside
        the Sent copy (the provider's, or mailr's own, connectors/sentcopy.py). A copy is expunged
        only when the user deleted the message, or when another copy still files it (_filed): a
        copy in Trash or Spam counts only for a move there, and for received mail
        (WriteBack.received) a copy in Sent or Drafts never counts, so taking the Inbox or a label
        off the delivered copy moves it to Archive and the Sent copy stays where it is.
        (23 Oct 2026: archiving a reply to oneself expunged its delivered copy, leaving only the
        Sent copy.)"""
        s = await self._open()
        out = []
        try:
            fast = await self._batch_flags(changes, lambda r: True)
            if fast is not None:
                return fast
            special = await self._special_folders()
            for ch in changes:

                refs = all_refs(ch.upstream)
                refs = [r for r in refs if str(r.get("connector")) == str(self.row.id)]
                if not refs:
                    out.append(ch.upstream)
                    continue
                written = set((ch.upstream or {}).get("label_copies") or [])
                # Flags go to every upstream copy.
                flag_ops = []
                if ch.seen is not None:
                    flag_ops.append(("\\Seen", ch.seen))
                if ch.flagged is not None:
                    flag_ops.append(("\\Flagged", ch.flagged))
                for r in refs:
                    if not flag_ops:
                        break
                    if not await self._select_ref(r):
                        continue
                    for flag, on in flag_ops:
                        fn = s.conn.add_flags if on else s.conn.remove_flags
                        await s.call(fn, [r["uid"]], [flag.encode()])
                if ch.delete:
                    trash = await self.folder_for(("role", "trash"))
                    if any(r["folder"] == trash for r in refs):
                        await self._expunge(refs)
                        refs = []
                    else:
                        new = await self._copy(_source(refs, ch, special), trash, ch.message_id) if trash else None
                        if new is None:
                            # Never delete the original if the Trash copy did not happen.
                            out.append(refs_to_upstream(refs))
                            continue
                        await self._expunge(refs)
                        refs = [new]
                else:
                    for lbl in ch.add_labels:
                        target = ("role", lbl) if lbl in ("inbox", "sent", "junk", "trash", "archive", "drafts") else ("label", lbl)
                        dest = await self.folder_for(target)
                        if dest and not any(r["folder"] == dest for r in refs):
                            new = await self._copy(_source(refs, ch, special), dest, ch.message_id)
                            if new:
                                refs.append(new)
                                if target[0] == "label":
                                    written.add(dest)  # a label folder mailr wrote this message to
                    for lbl in ch.remove_labels:
                        target = ("role", lbl) if lbl in ("inbox", "sent", "junk", "trash", "archive", "drafts") else ("label", lbl)
                        src = await self.folder_for(target, create=False)
                        victims = [r for r in refs if r["folder"] == src]
                        if not victims and src and target[0] == "label" and src in written \
                                and src not in await self._role_folders():
                            # The copy mailr wrote may be untracked (imapclient returns no COPYUID,
                            # so it is known only after the next sync). Report spam relies on this:
                            # upstream keeps only the Junk copy. Guarded: _untracked_label_copy.
                            found = await self._untracked_label_copy(refs, src, ch)
                            if found is not None:
                                victims, refs = [found], [*refs, found]
                                written.discard(src)
                        if not victims:
                            continue
                        rest = [r for r in refs if r not in victims]
                        # Another copy still files the message: this one goes. Taking Sent or
                        # Drafts off counts any other copy, as before.
                        if rest if target in (("role", "sent"), ("role", "drafts")) else _filed(rest, ch, special):
                            await self._expunge(victims)
                            refs = rest
                        elif (target == ("role", "inbox") or target[0] == "label") \
                                and not {"trash", "junk"} & set(ch.add_labels):
                            # The last copy that files it (Archive, or a label taken off an archived
                            # message; for mail to oneself, the delivered copy beside the Sent copy):
                            # it moves to the upstream archive folder, never away.
                            arch = await self.folder_for(("role", "archive"))
                            if arch and arch != src:
                                new = await self._copy(victims[0], arch, ch.message_id)
                                if new is not None:
                                    await self._expunge(victims)
                                    refs = [*rest, new]
                up = refs_to_upstream(refs)
                if written or "label_copies" in (ch.upstream or {}):
                    up["label_copies"] = sorted(written)
                out.append(up)
        finally:
            await self._close()
        return out

    # Most candidates an untracked label copy is looked for among; more is ambiguous.
    UNTRACKED_MAX = 20
    _ROLES_NEVER_SEARCHED = ("inbox", "sent", "drafts", "archive", "junk", "trash")

    async def _special_folders(self) -> dict[str, str]:
        """{role: upstream folder} of Sent, Drafts, Trash and Junk: the folders whose copies may not
        file a message (_filed)."""
        out = {}
        for role in ("sent", "drafts", "trash", "junk"):
            f = await self.folder_for(("role", role), create=False)
            if f:
                out[role] = f
        return out

    async def _role_folders(self) -> set[str]:
        """The upstream folders of the system roles, plus INBOX itself: the untracked-copy
        fallback never looks in them."""
        out = {"INBOX"}
        for role in self._ROLES_NEVER_SEARCHED:
            f = await self.folder_for(("role", role), create=False)
            if f:
                out.add(f)
        return out

    async def _untracked_label_copy(self, refs: list[dict], folder: str, ch: WriteBack) -> dict | None:
        """The one copy of ch's message in the label folder `folder` that mailr wrote but holds no
        uid for, or None. Guards (a wrong expunge on a real server is not acceptable):

        - the caller asks only for a label folder mailr itself wrote this message to (the
          message's `label_copies`), never for INBOX, Sent, Drafts, Archive, Junk or Trash;
        - a candidate counts only when the content hash of its bytes equals the stored message's
          hash (core/mime.content_hash: Message-ID, Date, From, Subject, Content-Type and body);
          without a stored hash nothing counts;
        - exactly one match is returned. None, two or more (or more candidates than UNTRACKED_MAX
          to check) leave the folder as it is, with a warning that names no content.
        """
        from mailr.core.mime import content_hash

        def leave(why: str) -> None:
            log.warning("connector %s: message %s: label copy left upstream (%s)", self.row.id, ch.mailr_id, why)

        if not ch.content_hash:
            leave("the message has no content hash")
            return None
        s = await self._open()
        found: list[int] = []
        try:
            from mailr.core.mime import parse_msgids

            want = parse_msgids(ch.message_id or "")
            if len(want) == 1:
                await s.select(folder, readonly=True)
                found = [int(u) for u in await s.call(s.conn.search, ["HEADER", "Message-ID", want[0]])]
        except Exception:  # noqa: BLE001  some servers reject HEADER search
            found = []
        if not found:
            # Some servers find no Message-ID by HEADER search (the dev Stalwart): every message of
            # the same size as a tracked copy is a candidate; the hash decides.
            src = next((r for r in refs if r.get("uid") is not None and r.get("uidvalidity") is not None), None)
            found = await self._same_size(src, folder, limit=self.UNTRACKED_MAX + 1) if src else []
        if not found:
            leave("no candidate")
            return None
        if len(found) > self.UNTRACKED_MAX:
            leave(f"more than {self.UNTRACKED_MAX} candidates")
            return None
        sel = await s.select(folder, readonly=True)
        uv = int(sel.get(b"UIDVALIDITY", 0))
        got = await s.call(s.conn.fetch, sorted(found), [b"BODY.PEEK[]"])
        matches = [int(u) for u, d in got.items()
                   if (d or {}).get(b"BODY[]") is not None and content_hash(d[b"BODY[]"]) == ch.content_hash]
        if len(matches) != 1:
            leave(f"{len(matches)} copies match")
            return None
        return {"connector": self.row.id, "folder": folder, "uidvalidity": uv, "uid": matches[0]}

    # ---------------- folder operations (mirror mode label rename / delete) ----------------

    async def rename_folder(self, old_label: str, new_label: str) -> tuple[str, str] | None:
        """RENAME the upstream folder backing a label. Returns (old folder, new folder)."""
        src = await self.folder_for(("label", old_label), create=False)
        if not src:
            return None
        delim = next((d for n, _, d in await self.folders() if n == src and d), None) or \
            next((d for _, _, d in await self.folders() if d), "/")
        dst = new_label.replace("/", delim)
        s = await self._open()
        await s.call(s.conn.rename_folder, src, dst)
        self._folders = None
        return src, dst

    async def folder_uids(self, label: str) -> tuple[str, int, list[int]] | None:
        folder = await self.folder_for(("label", label), create=False)
        if not folder:
            return None
        s = await self._open()
        sel = await s.select(folder, readonly=True)
        uids = await s.call(s.conn.search, ["ALL"]) if int(sel.get(b"EXISTS", 0)) else []
        return folder, int(sel.get(b"UIDVALIDITY", 0)), sorted(int(u) for u in uids)

    async def live_uids(self, folder: str, uidvalidity: int) -> set[int] | None:
        """UIDs present in a folder, or None if it is gone or its UIDVALIDITY changed."""
        s = await self._open()
        try:
            sel = await s.select(folder, readonly=True)
        except IMAPClientError:
            return None
        if int(sel.get(b"UIDVALIDITY", 0)) != int(uidvalidity):
            return None
        if not int(sel.get(b"EXISTS", 0)):
            return set()
        return {int(u) for u in await s.call(s.conn.search, ["ALL"])}

    async def delete_folder(self, folder: str) -> None:
        s = await self._open()
        try:
            await s.call(s.conn.select_folder, "INBOX", readonly=True)
        except Exception:  # noqa: BLE001, S110
            pass
        await s.call(s.conn.delete_folder, folder)
        self._folders = None

    async def _select_ref(self, r: dict) -> bool:
        s = await self._open()
        # SELECT once per folder: on a large Gmail All Mail each SELECT takes a while, and a
        # write-back of thousands of messages used to select it once per message (30 Sep 2026).
        cached = getattr(self, "_selected_uv", None)
        if cached and s.selected == r["folder"] and getattr(s, "selected_rw", False) and cached[0] == r["folder"]:
            uv = cached[1]
        else:
            sel = await s.select(r["folder"], readonly=False)
            uv = int(sel.get(b"UIDVALIDITY", 0))
            self._selected_uv = (r["folder"], uv)
        if uv != int(r["uidvalidity"]):
            log.warning("connector %s: %s UIDVALIDITY changed; skipping write-back", self.row.id, r["folder"])
            return False
        return True

    async def _earlier_copy(self, r: dict, dest: str, message_id: str) -> dict | None:
        """A copy of r that an earlier attempt already put in dest, or None. SEARCH HEADER is a
        substring match (RFC 3501), so a hit is only a candidate: it counts when its parsed
        Message-ID is exactly this one, its size matches and its bytes hash the same as the
        source's. A message that merely contains the id is never taken for the copy."""
        from mailr.core.mime import content_hash, parse_msgids

        want = parse_msgids(message_id)
        if len(want) != 1:
            return None
        s = await self._open()
        sel = await s.select(dest, readonly=True)
        try:
            found = await s.call(s.conn.search, ["HEADER", "Message-ID", want[0]])
        except Exception:  # noqa: BLE001  some servers reject HEADER search
            found = []
        if not found:
            # Some servers find no Message-ID by HEADER search (the dev Stalwart): the messages of
            # the source's exact size are the candidates then.
            found = await self._same_size(r, dest)
            if not found:
                return None
            sel = await s.select(dest, readonly=True)
        dest_uv = int(sel.get(b"UIDVALIDITY", 0))
        head = await s.call(s.conn.fetch, list(found)[:20], [b"BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)]", b"RFC822.SIZE"])
        cands: dict[int, int] = {}
        for u, d in head.items():
            v = next((x for k, x in d.items() if isinstance(k, bytes) and k.startswith(b"BODY[HEADER")), b"") or b""
            if parse_msgids(v.decode("utf-8", "replace")) == want:
                cands[int(u)] = int(d.get(b"RFC822.SIZE", -1))
        if not cands:
            return None
        if not await self._select_ref(r):
            return None
        src = (await s.call(s.conn.fetch, [r["uid"]], [b"RFC822.SIZE", b"BODY.PEEK[]"])).get(int(r["uid"])) or {}
        src_body = src.get(b"BODY[]")
        if src_body is None:
            return None
        same_size = [u for u, n in cands.items() if n == int(src.get(b"RFC822.SIZE", -2))]
        if not same_size:
            return None
        want_hash = content_hash(src_body)
        await s.select(dest, readonly=True)
        got = await s.call(s.conn.fetch, same_size, [b"BODY.PEEK[]"])
        for u in sorted(same_size, reverse=True):
            body = (got.get(u) or {}).get(b"BODY[]")
            if body is not None and content_hash(body) == want_hash:
                return {"connector": self.row.id, "folder": dest, "uidvalidity": dest_uv, "uid": int(u)}
        return None

    async def _same_size(self, r: dict, dest: str, limit: int = 50) -> list[int]:
        """UIDs in dest of messages exactly as large as r (at most `limit`, newest first)."""
        s = await self._open()
        if not await self._select_ref(r):
            return []
        size = int(((await s.call(s.conn.fetch, [r["uid"]], [b"RFC822.SIZE"])).get(int(r["uid"])) or {})
                   .get(b"RFC822.SIZE", 0))
        if size <= 0:
            return []
        await s.select(dest, readonly=True)
        try:
            found = await s.call(s.conn.search, ["LARGER", size - 1, "SMALLER", size + 1])
        except Exception:  # noqa: BLE001
            return []
        return sorted((int(u) for u in found), reverse=True)[:limit]

    async def _copy(self, r: dict, dest: str, message_id: str | None = None) -> dict | None:
        s = await self._open()
        if message_id:
            # Idempotent retry: if an earlier attempt already copied it, reuse that copy.
            earlier = await self._earlier_copy(r, dest, message_id)
            if earlier is not None:
                return earlier
        if not await self._select_ref(r):
            return None
        resp = await s.call(s.conn.copy, [r["uid"]], dest)
        new_uid = None
        # imapclient returns the COPYUID response text when UIDPLUS is supported.
        m = re.search(rb"COPYUID (\d+) \S+ (\d+)", resp if isinstance(resp, bytes) else str(resp).encode())
        dest_uv = None
        if m:
            dest_uv, new_uid = int(m.group(1)), int(m.group(2))
        if new_uid is None:
            return {"connector": self.row.id, "folder": dest, "uidvalidity": None, "uid": None}
        return {"connector": self.row.id, "folder": dest, "uidvalidity": dest_uv, "uid": new_uid}

    async def _expunge(self, refs: list[dict]) -> list[dict]:
        """Delete these refs upstream. Returns the refs actually deleted."""
        s = await self._open()
        done = []
        by_folder: dict[tuple[str, int], list[int]] = {}
        for r in refs:
            if r.get("uid") is None:
                continue
            by_folder.setdefault((r["folder"], int(r["uidvalidity"])), []).append(int(r["uid"]))
        for (folder, uv), uids in by_folder.items():
            sel = await s.select(folder, readonly=False)
            if int(sel.get(b"UIDVALIDITY", 0)) != uv:
                log.warning("connector %s: not deleting from %s: UIDVALIDITY changed (%s → %s)", self.row.id,
                            folder, uv, sel.get(b"UIDVALIDITY"))
                continue
            if not s.has("UIDPLUS"):
                # Plain EXPUNGE removes EVERY \\Deleted message in the folder; only do it when
                # nothing else is marked (another client's pending deletions are not ours).
                others = set(await s.call(s.conn.search, ["DELETED"])) - set(uids)
                if others:
                    log.warning("connector %s: not expunging %s: server lacks UIDPLUS and %d other message(s) "
                                "are marked deleted", self.row.id, folder, len(others))
                    continue
            for i in range(0, len(uids), 200):
                chunk = uids[i:i + 200]
                await s.call(s.conn.add_flags, chunk, [b"\\Deleted"])
                if s.has("UIDPLUS"):
                    await s.call(s.conn.uid_expunge, chunk)
                else:
                    await s.call(s.conn.expunge)
            done += [{"connector": self.row.id, "folder": folder, "uidvalidity": uv, "uid": u} for u in uids]
        return done

    async def ref_hashes(self, refs: list[dict]) -> dict[tuple[str, int, int], str]:
        """content_hash of the upstream bytes behind each ref (folder, uidvalidity, uid); a ref
        whose folder changed UIDVALIDITY or whose message is gone is left out. The connection
        stays open for delete_refs."""
        from mailr.core.mime import content_hash

        s = await self._open()
        out: dict[tuple[str, int, int], str] = {}
        by_folder: dict[tuple[str, int], list[int]] = {}
        for r in refs:
            if str(r.get("connector")) == str(self.row.id) and r.get("uid") is not None and r.get("uidvalidity") is not None:
                by_folder.setdefault((r["folder"], int(r["uidvalidity"])), []).append(int(r["uid"]))
        for (folder, uv), uids in by_folder.items():
            try:
                sel = await s.select(folder, readonly=True)
            except IMAPClientError:
                continue
            if int(sel.get(b"UIDVALIDITY", 0)) != uv:
                continue
            for i in range(0, len(uids), 20):
                got = await s.call(s.conn.fetch, uids[i:i + 20], [b"BODY.PEEK[]"])
                for u, d in got.items():
                    body = d.get(b"BODY[]")
                    if body is not None:
                        out[(folder, uv, int(u))] = content_hash(body)
        return out

    async def delete_refs(self, refs: list[dict]) -> list[dict]:
        try:
            await self._open()
            return await self._expunge([r for r in refs if str(r.get("connector")) == str(self.row.id)])
        finally:
            await self._close()

    async def delete_upstream(self, upstream_refs: list[dict[str, Any]]) -> int:
        return len(await self.delete_refs(upstream_refs))


# ---------------- helpers ----------------


def _filed(refs: list[dict], ch: WriteBack, special: dict[str, str]) -> list[dict]:
    """The copies that keep the message where Mailr has it: none in Trash or Spam (unless the change
    puts it there), and for received mail (WriteBack.received) none in Sent or Drafts. `special`
    is ImapConnector._special_folders."""
    binning = {"trash", "junk"} & set(ch.add_labels)
    skip = {f for role, f in special.items()
            if (role in ("trash", "junk") and role not in binning) or (role in ("sent", "drafts") and ch.received)}
    return [r for r in refs if r["folder"] not in skip]


def _source(refs: list[dict], ch: WriteBack, special: dict[str, str]) -> dict:
    """The copy a new copy is made from: the delivered one when there is one. Only a copy whose
    UID is known can be copied (one mailr just made has none until the next sync)."""
    known = [r for r in refs if r.get("uid") is not None and r.get("uidvalidity") is not None]
    return (_filed(known, ch, special) or known or refs)[0]


def pulled_since(fs: dict[str, Any]) -> datetime | None:
    """When the previous pass over a folder selected it: the changes a pass reports are newer."""
    try:
        return datetime.fromisoformat(fs["pulled_at"]) if fs.get("pulled_at") else None
    except (TypeError, ValueError):
        return None


def all_refs(upstream: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Primary ref plus copies recorded when the same message was seen in other folders."""
    up = upstream or {}
    out = []
    keys = ("connector", "folder", "uidvalidity", "uid", "hash", "hv", "match")
    if up.get("folder") is not None and up.get("uid") is not None:
        out.append({k: up[k] for k in keys if k in up})
    for c in up.get("copies", []) or []:
        if c.get("folder") is not None and c.get("uid") is not None:
            out.append({k: c[k] for k in keys if k in c})
    return out


def refs_to_upstream(refs: list[dict[str, Any]], keep: dict[str, Any] | None = None) -> dict[str, Any]:
    extra = {k: v for k, v in (keep or {}).items()
             if k not in ("connector", "folder", "uidvalidity", "uid", "copies", "hash", "hv", "match")}
    if not refs:
        return {**extra, "removed": True}
    return {**extra, **refs[0], "copies": refs[1:]}


def _aware(d: Any) -> datetime | None:
    if not isinstance(d, datetime):
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _to_date(v: Any) -> date:
    if isinstance(v, date):
        return v
    return datetime.fromisoformat(str(v)[:10]).date()


async def _wait(ev: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(ev.wait(), timeout=seconds)
    except TimeoutError:
        pass
