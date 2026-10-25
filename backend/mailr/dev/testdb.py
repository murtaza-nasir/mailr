"""Private development databases, cloned from a template that is migrated to head.

Used by tests/conftest.py (one database per checkout and pytest worker) and by development scripts
that give each checkout its own database. Cloning a template takes a fraction of a second; migrating from scratch takes
longer and opens many more connections.

The template is `mailr_template`. Its comment records a hash of the migration files. When the
hash in this checkout differs, the template is rebuilt (under an advisory lock, so parallel
agents take turns). It never touches the shared `mailr` database or any database whose name
is not ours: see `_check_name`.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

TEMPLATE = "mailr_template"
LOCK = 727102  # pg_advisory_lock key for template builds and clones (bootstrap uses 727001)
BACKEND = Path(__file__).resolve().parents[2]
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def worktree_slug(checkout: Path | None = None) -> str:
    """The short name of a checkout: its folder name (or AGENT_ENV_SLUG), lowercase, [a-z0-9_].
    """
    s = os.environ.get("AGENT_ENV_SLUG") or (checkout or BACKEND.parent).name
    s = re.sub(r"^mailr[-_]?(wt[-_])?", "", s.lower()) or "main"
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")[:30].strip("_") or "main"


def _hosts() -> set[str]:
    extra = os.environ.get("MAILR_AGENT_DB_HOSTS", "")
    return LOCAL_HOSTS | {h.strip() for h in extra.split(",") if h.strip()}


def split_url(url: str) -> tuple[str, str]:
    """`postgresql+asyncpg://u:p@h:5432/db?x` -> ("postgresql+asyncpg://u:p@h:5432", "db")."""
    root, _, rest = url.rpartition("/")
    return root, rest.split("?")[0]


def with_db(url: str, name: str) -> str:
    return f"{split_url(url)[0]}/{name}"


def dsn(url: str, name: str | None = None) -> str:
    """An asyncpg DSN for `url`, optionally for another database on the same server."""
    if name:
        url = with_db(url, name)
    return url.replace("postgresql+asyncpg://", "postgresql://")


def check_server(url: str) -> None:
    host = urlsplit(url.replace("postgresql+asyncpg", "postgresql")).hostname or ""
    if host not in _hosts():
        raise SystemExit(f"refusing to manage databases on {host!r}: only a local development server "
                         "(set MAILR_AGENT_DB_HOSTS to allow another development host)")


def _check_name(name: str) -> None:
    """Only databases this tooling owns: per-checkout databases, test databases and the template."""
    ok = (name == TEMPLATE or name.startswith(("mailr_wt_", f"{TEMPLATE}_build_"))
          or ("test" in name and name not in ("mailr", "postgres")))
    if not ok or not re.fullmatch(r"[a-z0-9_]{1,63}", name):
        raise SystemExit(f"refusing to create or drop database {name!r}: not a per-checkout, test or template database")


def migrations_key() -> str:
    """A hash of everything that decides the migrated schema."""
    h = hashlib.sha256()
    mig = BACKEND / "migrations"
    for p in sorted([mig / "env.py", *mig.glob("versions/*.py")]):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


async def _admin(url: str):
    import asyncpg

    check_server(url)
    # The maintenance database: never the shared dev database itself.
    try:
        return await asyncpg.connect(dsn(url, "postgres"))
    except asyncpg.InvalidCatalogNameError:
        return await asyncpg.connect(dsn(url, "template1"))


async def _exists(conn, name: str) -> bool:
    return bool(await conn.fetchval("SELECT 1 FROM pg_database WHERE datname=$1", name))


async def _drop(conn, name: str) -> None:
    _check_name(name)
    if await _exists(conn, name):
        await conn.execute(f'ALTER DATABASE "{name}" IS_TEMPLATE false')
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _migrate(url: str) -> None:
    """Run the migrations of this checkout against `url` (a child process: settings are cached)."""
    env = {**os.environ, "MAILR_DATABASE_URL": url}
    r = subprocess.run([sys.executable, "-c", "import sys; from mailr.cli import main; sys.argv=['mailr','migrate']; main()"],
                       cwd=BACKEND, env=env, capture_output=True, text=True, check=False)
    if r.returncode:
        sys.stderr.write(r.stdout[-2000:] + r.stderr[-4000:])
        raise SystemExit(f"migrations failed on {split_url(url)[1]}")


async def _template_key(conn) -> str:
    row = await conn.fetchval(
        "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname=$1", TEMPLATE)
    m = re.search(r"key=(\w+)", row or "")
    return m[1] if m else ""


async def _ensure_template(conn, url: str) -> bool:
    """Rebuild the template when the migrations changed. Call with the lock held."""
    import asyncpg

    key = migrations_key()
    if await _exists(conn, TEMPLATE) and await _template_key(conn) == key:
        return False
    build = f"{TEMPLATE}_build_{os.getpid()}"
    await _drop(conn, build)
    await conn.execute(f'CREATE DATABASE "{build}"')
    try:
        c = await asyncpg.connect(dsn(url, build))
        try:
            await c.execute("CREATE EXTENSION IF NOT EXISTS vector")
        finally:
            await c.close()
        await asyncio.to_thread(_migrate, with_db(url, build))
        await _drop(conn, TEMPLATE)
        await conn.execute(f'ALTER DATABASE "{build}" RENAME TO "{TEMPLATE}"')
    except BaseException:
        await _drop(conn, build)
        raise
    # No connections to the template: a connected template cannot be cloned.
    await conn.execute(f'ALTER DATABASE "{TEMPLATE}" IS_TEMPLATE true ALLOW_CONNECTIONS false')
    await conn.execute(f"COMMENT ON DATABASE \"{TEMPLATE}\" IS 'mailr template key={key}'")
    return True


async def clone(url: str, name: str) -> bool:
    """(Re)create database `name` on the server of `url` as a copy of the template.
    Returns True when the template had to be rebuilt first."""
    _check_name(name)
    conn = await _admin(url)
    try:
        await conn.execute("SELECT pg_advisory_lock($1)", LOCK)
        try:
            rebuilt = await _ensure_template(conn, url)
            await _drop(conn, name)
            await conn.execute(f'CREATE DATABASE "{name}" TEMPLATE "{TEMPLATE}"')
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", LOCK)
    finally:
        await conn.close()
    return rebuilt


async def drop(url: str, *names: str) -> list[str]:
    conn = await _admin(url)
    gone = []
    try:
        for n in names:
            if await _exists(conn, n):
                await _drop(conn, n)
                gone.append(n)
    finally:
        await conn.close()
    return gone


async def matching(url: str, prefix: str) -> list[str]:
    conn = await _admin(url)
    try:
        rows = await conn.fetch("SELECT datname FROM pg_database WHERE datname LIKE $1 ORDER BY 1",
                                prefix.replace("_", r"\_") + "%")
    finally:
        await conn.close()
    return [r["datname"] for r in rows]


async def headroom(url: str) -> tuple[int, int]:
    """(max_connections, connections in use) on the server."""
    conn = await _admin(url)
    try:
        mx = int(await conn.fetchval("SHOW max_connections"))
        used = int(await conn.fetchval("SELECT count(*) FROM pg_stat_activity"))
    finally:
        await conn.close()
    return mx, used


def main() -> None:
    """`python -m mailr.dev.testdb clone|drop|list|template|headroom URL [NAME…]`."""
    cmd, url, *names = sys.argv[1:]
    if cmd == "clone":
        for n in names:
            if asyncio.run(clone(url, n)):
                print(f"rebuilt {TEMPLATE} (migrations changed)", file=sys.stderr)
    elif cmd == "drop":
        for n in asyncio.run(drop(url, *names)):
            print(f"dropped {n}")
    elif cmd == "list":
        for n in asyncio.run(matching(url, names[0])):
            print(n)
    elif cmd == "template":
        async def go():
            conn = await _admin(url)
            try:
                await conn.execute("SELECT pg_advisory_lock($1)", LOCK)
                return await _ensure_template(conn, url)
            finally:
                await conn.close()
        print("rebuilt" if asyncio.run(go()) else "current")
    elif cmd == "headroom":
        mx, used = asyncio.run(headroom(url))
        print(f"{used} {mx}")
    else:
        raise SystemExit(f"unknown command {cmd}")


if __name__ == "__main__":
    main()
