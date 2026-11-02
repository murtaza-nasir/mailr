"""Test accounts in the shared dev mail stores (deploy/dev/docker-compose.yml).

Backend tests, e2e seeds and the e2e servers create Stalwart accounts in the dev store
(:18080) and in the fake upstream (:18091). None were destroyed: by October 2026 the dev store
held about 200 leftover accounts with about 19,000 blobs each (6.7 GB), and Stalwart used a CPU
core around the clock. Now:

- An account made in a test run is marked: its `externalId` is `mailr-test:<run>`. The mark is
  set only with MAILR_DEV and MAILR_TEST_STORE_RUN (`mark`), so a production account never has it.
- Every marked account is noted when it is made (`note_created`): in this process's list, and in
  the file named by MAILR_TEST_STORE_LEDGER, when set, so that accounts made by child processes
  can be found too. The noted accounts are destroyed at the end of the pytest session, of
  `tools/agent-env e2e` and in `tools/agent-env down` (`release`, `release_ledger`, `release_db`).
- With `sweep` (tools/dev-store-sweep), what crashed runs left is destroyed: only test accounts
  (marked, or named like the test accounts of earlier versions), only accounts older than
  MIN_AGE_HOURS, never an admin or a service account.

A store that is not the dev store or the fake upstream is refused before any request (`check_target`).
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from collections.abc import Iterable
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

DEV_STORE = "http://localhost:18080"
UPSTREAM = "http://localhost:18091"
UPSTREAM_AUTH = ("up-svc@upstream.test", "up-svc-pass")
# The exact URLs of the two dev stores of deploy/dev/docker-compose.yml.
DEV_URLS = {f"http://{h}:{p}" for h in ("localhost", "127.0.0.1") for p in (18080, 18091)}
LOOPBACK = {"localhost", "127.0.0.1", "::1"}

MARK_ENV = "MAILR_TEST_STORE_RUN"
LEDGER_ENV = "MAILR_TEST_STORE_LEDGER"
MARK_PREFIX = "mailr-test:"
MIN_AGE_HOURS = 6.0
MGMT = ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"]

# Accounts made before the mark existed (or by a checkout without it). Dev store: mailr's account
# names are `<address base>-<user id>-<6 hex>` (core/users._local_part), and test addresses end in
# a random hex tag of 6 or more digits (t-1a2b3c4d@example.com, zed-6cf9f7@elsewhere.example);
# tests/test_stalwart_dav.py names its own `p0t-<name>-<6 hex>`. Upstream: tests/helpers/connectors
# (`alice` + 16 hex) and the e2e seed (`rosa` + 6 hex).
DEV_TEST_NAME = re.compile(r"^(?:(?:[a-z0-9]+-)*[0-9a-f]{6,}-\d+-[0-9a-f]{6}|p0t-[a-z]+-[0-9a-f]{6})$")
UPSTREAM_TEST_NAME = re.compile(r"^(?:alice[0-9a-f]{16}|rosa[0-9a-f]{6})$")
# Provisioned accounts (deploy/stalwart/provision*.json and deploy/dev/docker-compose.yml): never touched.
PROVISIONED = {"mailr-svc", "up-svc", "admin"}


class RefusedTarget(ValueError):
    """The store is not one of the dev stores."""


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes")


def check_target(url: str, dev: bool | None = None) -> str:
    """The store URL without a trailing slash, when it is a dev store: one of the two exact dev URLs,
    or a loopback URL while MAILR_DEV is true. Anything else raises RefusedTarget, before any request."""
    u = (url or "").strip().rstrip("/")
    if u in DEV_URLS:
        return u
    parts = urlsplit(u)
    if dev is None:
        dev = _truthy(os.environ.get("MAILR_DEV"))
    if dev and parts.scheme in ("http", "https") and (parts.hostname or "") in LOOPBACK:
        return u
    raise RefusedTarget(f"refusing to touch the accounts of {url!r}: not the dev store "
                        f"({DEV_STORE}, {UPSTREAM}, or a localhost URL with MAILR_DEV=true)")


def is_upstream(url: str) -> bool:
    return (urlsplit(url).port or 0) == 18091


# ---------------------------------------------------------------- marking and noting


def run_tag() -> str | None:
    """This process's test run, when accounts it makes are test accounts (MAILR_DEV and MAILR_TEST_STORE_RUN)."""
    tag = os.environ.get(MARK_ENV, "").strip()
    if not tag:
        return None
    from mailr.config import get_settings

    try:
        dev = get_settings().dev
    except Exception:  # noqa: BLE001  no settings: not a dev process
        return None
    return tag if dev else None


def mark() -> dict[str, str]:
    """Extra properties for an `x:Account/set` create: the test mark, or nothing outside a test run."""
    tag = run_tag()
    return {"externalId": f"{MARK_PREFIX}{tag}"[:120]} if tag else {}


_noted: list[tuple[str, str, str]] = []
_lock = threading.Lock()


def note_created(url: str, account_id: str, name: str) -> None:
    """Remember a test account just made (url, id, name): in this process, and in the ledger file
    when MAILR_TEST_STORE_LEDGER is set. Nothing happens outside a test run."""
    if not run_tag() or not account_id:
        return
    entry = (url.rstrip("/"), str(account_id), name)
    with _lock:
        _noted.append(entry)
    ledger = os.environ.get(LEDGER_ENV, "").strip()
    if ledger:
        line = json.dumps({"url": entry[0], "id": entry[1], "name": name}) + "\n"
        try:
            with open(ledger, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError as e:
            print(f"store_accounts: could not write {ledger}: {e}", file=sys.stderr)


def noted() -> list[tuple[str, str, str]]:
    with _lock:
        return list(_noted)


def read_ledger(path: str) -> list[tuple[str, str, str]]:
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                    out.append((str(d["url"]).rstrip("/"), str(d["id"]), str(d["name"])))
                except (ValueError, KeyError, TypeError):
                    continue
    except FileNotFoundError:
        pass
    return out


# ---------------------------------------------------------------- the stores


def auth_for(url: str) -> tuple[str, str]:
    """The management login of a dev store: the fake upstream's own, else the service account of
    the settings (backend/.env), which conftest points at the dev store."""
    if is_upstream(url):
        return UPSTREAM_AUTH
    from mailr.config import get_settings

    st = get_settings()
    return st.stalwart_admin_user, st.stalwart_admin_password


class Store:
    """A small synchronous management client for one dev store."""

    def __init__(self, url: str, auth: tuple[str, str] | None = None, dev: bool | None = None, timeout: float = 120):
        import httpx

        self.url = check_target(url, dev)
        self.auth = auth or auth_for(self.url)
        self._http = httpx.Client(auth=self.auth, timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def call(self, method: str, args: dict[str, Any]) -> dict[str, Any]:
        r = self._http.post(f"{self.url}/jmap/", json={"using": MGMT, "methodCalls": [[method, args, "0"]]})
        r.raise_for_status()
        name, out, _ = r.json()["methodResponses"][0]
        if name == "error":
            raise RuntimeError(f"{method}: {out}")
        return out

    def ids(self) -> list[str]:
        # Without a sort, Stalwart 0.16.23 pages in no stable order (pages repeat, accounts go
        # missing), and one page holds at most 5,000 ids. Sorted by id, new accounts come last.
        out: list[str] = []
        while True:
            got = self.call("x:Account/query", {"sort": [{"property": "id"}], "position": len(out),
                                                "limit": 1000}).get("ids") or []
            out += got
            if len(got) < 1000:
                return list(dict.fromkeys(out))

    def get(self, ids: Iterable[str]) -> list[dict[str, Any]]:
        ids = list(dict.fromkeys(ids))
        out: list[dict[str, Any]] = []
        for i in range(0, len(ids), 500):
            out += self.call("x:Account/get", {"ids": ids[i:i + 500], "properties": [
                "name", "createdAt", "externalId", "roles", "emailAddress", "aliases"]}).get("list") or []
        return out

    def accounts(self) -> list[dict[str, Any]]:
        return self.get(self.ids())

    def destroy(self, ids: list[str]) -> int:
        """Destroy these accounts, 100 per call. A group with members is refused (objectIsLinked)
        while a member exists, so refused ids are tried again after the others, up to three rounds."""
        done = 0
        todo = list(dict.fromkeys(ids))
        for _ in range(3):
            linked: list[str] = []
            for i in range(0, len(todo), 100):
                r = self.call("x:Account/set", {"destroy": todo[i:i + 100]})
                done += len(r.get("destroyed") or [])
                linked += [k for k, v in (r.get("notDestroyed") or {}).items() if (v or {}).get("type") == "objectIsLinked"]
            if not linked or len(linked) == len(todo):
                break
            todo = linked
        return done

    def service_name(self) -> str:
        return self.auth[0].split("@", 1)[0].lower()


def protected(store: Store, acct: dict[str, Any]) -> bool:
    """Never destroyed: admins, the service account this client signs in as, provisioned accounts."""
    name = (acct.get("name") or "").lower()
    role = (acct.get("roles") or {}).get("@type")
    return role == "Admin" or name in PROVISIONED or name == store.service_name()


def is_test_account(store: Store, acct: dict[str, Any]) -> bool:
    if protected(store, acct):
        return False
    if str(acct.get("externalId") or "").startswith(MARK_PREFIX):
        return True
    pattern = UPSTREAM_TEST_NAME if is_upstream(store.url) else DEV_TEST_NAME
    return bool(pattern.fullmatch(acct.get("name") or ""))


def _created(acct: dict[str, Any]) -> float | None:
    try:
        return datetime.fromisoformat(str(acct["createdAt"]).replace("Z", "+00:00")).timestamp()
    except (KeyError, ValueError):
        return None


# ---------------------------------------------------------------- per-run release


def release(entries: Iterable[tuple[str, str, str]]) -> dict[str, int]:
    """Destroy the noted accounts (url, id, name). An id is destroyed only while it still carries the
    noted name, so an account that was destroyed already and whose id went to another account is
    left alone. Returns {url: destroyed}."""
    by_url: dict[str, dict[str, str]] = {}
    for url, aid, name in entries:
        by_url.setdefault(url.rstrip("/"), {})[aid] = name
    out: dict[str, int] = {}
    failed: list[str] = []
    for url, want in by_url.items():
        try:
            with Store(url) as st:
                ids = [a["id"] for a in st.get(want) if a.get("name") == want.get(a["id"]) and not protected(st, a)]
                out[url] = st.destroy(ids) if ids else 0
        except Exception as e:  # noqa: BLE001  the other stores still get theirs
            failed.append(f"{url}: {type(e).__name__}: {str(e)[:200]}")
    if failed:
        raise RuntimeError(f"destroyed {sum(out.values())}; failed: " + "; ".join(failed))
    return out


def release_noted() -> dict[str, int]:
    entries = noted()
    out = release(entries) if entries else {}
    with _lock:
        _noted.clear()
    return out


def release_ledger(path: str) -> dict[str, int]:
    out = release(read_ledger(path))
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    return out


def _base(email: str) -> str:
    # The same rule as core/users._local_part.
    return re.sub(r"[^a-z0-9]+", "-", email.split("@")[0].lower()).strip("-")[:30] or "user"


async def _db_refs(db_url: str) -> list[tuple[str, str, set[str]]]:
    """(account id, name prefix or "", logins) of every store account a mailr database refers to."""
    import asyncpg

    conn = await asyncpg.connect(db_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        refs = [(r["stalwart_account_id"], f"{_base(r['email'])}-{r['id']}-",
                 {(r["stalwart_login"] or "").lower()} - {""})
                for r in await conn.fetch("SELECT id, email, stalwart_login, stalwart_account_id FROM users "
                                          "WHERE stalwart_account_id IS NOT NULL")]
        if await conn.fetchval("SELECT to_regclass('calendar_groups') IS NOT NULL"):
            for r in await conn.fetch("SELECT store_group_id, store_group_login, owner_id, owner_login FROM calendar_groups"):
                for aid, login in ((r["store_group_id"], r["store_group_login"]), (r["owner_id"], r["owner_login"])):
                    if aid and login:
                        refs.append((aid, "", {login.lower()}))
    finally:
        await conn.close()
    return refs


def release_db(db_url: str, since: float | None = None, dry_run: bool = False) -> int:
    """release_db_async, for callers outside an event loop."""
    import asyncio

    return asyncio.run(release_db_async(db_url, since, dry_run))


async def release_db_async(db_url: str, since: float | None = None, dry_run: bool = False) -> int:
    """Destroy the dev store accounts that the users and calendar groups of one mailr database hold
    (a worktree's databases before they are dropped, an e2e run's database). An account is destroyed
    only when it is still the one the row names (its name starts with the user's `<base>-<id>-`, or
    its address is the row's login), it is not protected, and (with `since`) it was made after
    that moment. Returns how many were destroyed (or would be, with dry_run)."""
    from mailr.config import get_settings

    st = get_settings()
    refs = await _db_refs(db_url)
    if not refs:
        return 0
    with Store(st.stalwart_url, (st.stalwart_admin_user, st.stalwart_admin_password), dev=st.dev) as store:
        found = {a["id"]: a for a in store.get(r[0] for r in refs)}
        doomed = []
        for aid, prefix, logins in refs:
            a = found.get(aid)
            if not a or protected(store, a):
                continue
            name = (a.get("name") or "").lower()
            mine = (prefix and name.startswith(prefix)) or (a.get("emailAddress") or "").lower() in logins
            made = _created(a)
            if mine and (since is None or (made is not None and made >= int(since))):
                doomed.append(aid)
        doomed = list(dict.fromkeys(doomed))
        if dry_run or not doomed:
            return len(doomed)
        return store.destroy(doomed)


# ---------------------------------------------------------------- the sweep


def sweep(url: str, min_age_hours: float = MIN_AGE_HOURS, limit: int = 0, dry_run: bool = False,
          now: float | None = None, dev: bool | None = None) -> tuple[int, int, int]:
    """Destroy the test accounts that are older than min_age_hours in one dev store (at most `limit`
    of the oldest, 0 for all). Returns (accounts, test accounts, destroyed or due with dry_run)."""
    now = now or time.time()
    with Store(url, dev=dev) as st:
        accts = st.accounts()
        tests = [a for a in accts if is_test_account(st, a)]
        due = sorted(((_created(a), a["id"]) for a in tests
                      if (_created(a) or now) <= now - min_age_hours * 3600), key=lambda t: t[0] or 0)
        ids = [aid for _, aid in due]
        if limit:
            ids = ids[:limit]
        if dry_run or not ids:
            return len(accts), len(tests), len(ids)
        return len(accts), len(tests), st.destroy(ids)


def main() -> None:
    """python -m mailr.dev.store_accounts sweep [--dry-run] [--min-age H] [--limit N] [--store dev|upstream|all]
    python -m mailr.dev.store_accounts release-ledger FILE
    python -m mailr.dev.store_accounts release-db DB_URL [--since EPOCH] [--dry-run]
    python -m mailr.dev.store_accounts count"""
    args = sys.argv[1:]
    if not args:
        print(main.__doc__, file=sys.stderr)
        sys.exit(2)

    def opt(name: str, default: str) -> str:
        return args[args.index(name) + 1] if name in args[:-1] else default

    cmd = args[0]
    try:
        if cmd in ("sweep", "count"):
            from mailr.config import get_settings

            dev = get_settings().dev
            which = opt("--store", "all")
            urls = ([get_settings().stalwart_url] if which in ("dev", "all") else []) + \
                   ([UPSTREAM] if which in ("upstream", "all") else [])
            for url in urls:
                try:
                    total, tests, n = sweep(url, float(opt("--min-age", str(MIN_AGE_HOURS))), int(opt("--limit", "0")),
                                            dry_run=cmd == "count" or "--dry-run" in args, dev=dev)
                except RefusedTarget:
                    raise
                except Exception as e:  # noqa: BLE001  a store that is not running: nothing to sweep
                    print(f"dev-store-sweep: {url}: skipped ({type(e).__name__}: {str(e)[:200]})", file=sys.stderr)
                    continue
                verb = "would destroy" if cmd == "count" or "--dry-run" in args else "destroyed"
                print(f"dev-store-sweep: {url}: {total} accounts, {tests} test accounts, {verb} {n} older than "
                      f"{opt('--min-age', str(MIN_AGE_HOURS))} h", file=sys.stderr)
        elif cmd == "release-ledger":
            for url, n in release_ledger(args[1]).items():
                print(f"store accounts: destroyed {n} on {url}", file=sys.stderr)
        elif cmd == "release-db":
            since = opt("--since", "")
            n = release_db(args[1], float(since) if since else None, dry_run="--dry-run" in args)
            print(f"store accounts: {'would destroy' if '--dry-run' in args else 'destroyed'} {n} of "
                  f"{args[1].rpartition('/')[2]}", file=sys.stderr)
        else:
            print(main.__doc__, file=sys.stderr)
            sys.exit(2)
    except RefusedTarget as e:
        print(f"store accounts: {e}", file=sys.stderr)
        sys.exit(3)
    except Exception as e:  # noqa: BLE001  one line for the caller; the sweep finds what is left
        print(f"store accounts: {cmd} failed: {type(e).__name__}: {str(e)[:300]}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
