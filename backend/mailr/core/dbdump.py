"""Logical database dumps: the nightly dump, the dump before migrations, and the restore drill.

Postgres alone holds notes and their history, tables, task details, settings, layouts, the
assistant's memory, chats, label decisions and approvals. The VM snapshots hold the database
only crash-consistently, inside the image. This module writes a `pg_dump` in the custom format
(compressed, restorable table by table with `pg_restore`) on a schedule of its own.

Where: `<MAILR_BACKUP_TARGET>/postgres/` when a backup target is set (next to the mail export; the
target must hold its marker file, core/backup.py), else `<data_dir>/backups/postgres/`.
`MAILR_DB_DUMP_DIR` names another folder.

Files: `mailr-<UTC time>-<reason>.dump` and beside it `<same>.json`, the manifest: when, which
reason, the schema revision, the size, the SHA-256 of the dump and the number of rows in every
table. The counts and the dump come from one snapshot (`pg_export_snapshot` and
`pg_dump --snapshot`), so a restore must give exactly these counts. A dump is written to a
`.partial` file, synced and renamed, so a file with the final name is always complete; it is
then read back with `pg_restore --list`.

When:
- nightly, at MAILR_DB_DUMP_HOUR (UTC), from the worker's maintenance lane (`db.dump`); also when
  the last good dump is more than 26 hours old (a missed night, a new install), at most every
  6 hours while dumps fail;
- before migrations, at start, when the schema is behind (`before_migrate`), into the same folder
  as the nightly dump (the data volume without a backup target); a failed dump stops the start,
  unless MAILR_DB_DUMP_BEFORE_MIGRATE=false. In the data volume, a dump that does not fit is
  skipped with a warning instead: a full disk would stop more than the start;
- by hand: `mailr db-dump`.

Kept: the newest MAILR_DB_DUMP_KEEP nightly dumps (14), pre-migration dumps for the same number of
days (the newest three always), manual dumps until removed by hand.

Restore: `mailr db-restore FILE --into NAME` loads a dump into a new database and compares its row
counts with the manifest; `mailr db-restore --check` does that with the newest dump in a scratch
database and drops it again (the drill). The live database is never a target: a restore goes to a
new database, which is renamed into place while the app and worker are stopped (admin guide,
Backups).

Trust: the SHA-256 of every dump this server writes is also kept in the database
(instance_settings db_dump_digests), since anyone who can write to the backup target can write a
manifest that matches their own file. A dump whose checksum differs from the recorded one is never
restored. The drill restores only dumps with a recorded checksum, and it restores them as a role
without superuser rights (`<database>_restore`, created when the app's role is a superuser), so SQL
in a dump cannot run programs on the database server; the extensions are created first, by the
app's role. A restore by hand of a dump with no record (another server's, or one older than the
record) still works: it is the admin's choice of file.

The binaries are `pg_dump` and `pg_restore` from the image (postgresql-client-17, the server's
major version), or from MAILR_PG_BIN_DIR. The password goes to them in the environment
(PGPASSWORD), never on the command line.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from mailr.config import get_settings
from mailr.core import private

log = logging.getLogger("mailr.dbdump")

STATE_KEY = "db_dump"
CHECK_KEY = "db_restore_check"
# {dump file name: its SHA-256} of the dumps this server wrote, the newest DIGESTS_MAX.
DIGESTS_KEY = "db_dump_digests"
DIGESTS_MAX = 200
STALE = timedelta(hours=26)
RETRY_EVERY = timedelta(hours=6)
NIGHTLY_GAP = timedelta(hours=20)
PREMIGRATE_KEEP_MIN = 3
NAME_RE = re.compile(r"^mailr-(\d{8}T\d{6}Z)-([a-z-]+)\.dump$")
CHUNK = 1024 * 1024


class DumpError(RuntimeError):
    """A dump or restore that did not complete. The message holds no secrets."""


# ---------------------------------------------------------------- connection and binaries


def _conninfo(database: str | None = None) -> tuple[dict[str, str], str]:
    """(environment for pg_dump/pg_restore, database name) from MAILR_DATABASE_URL."""
    from sqlalchemy.engine import make_url

    url = make_url(get_settings().database_url)
    env = {"PGHOST": url.host or "localhost", "PGPORT": str(url.port or 5432), "PGUSER": url.username or "",
           "PGPASSWORD": url.password or "", "PGDATABASE": database or url.database or "",
           "PGAPPNAME": "mailr-dbdump", "PGCONNECT_TIMEOUT": "10"}
    return env, url.database or ""


def _dsn(database: str | None = None) -> str:
    from sqlalchemy.engine import make_url

    url = make_url(get_settings().database_url)
    if database is not None:
        url = url.set(database=database)
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def _bin(name: str) -> str:
    d = get_settings().pg_bin_dir
    if d:
        return str(Path(d) / name)
    found = shutil.which(name)
    if not found:
        raise ToolMissing(f"{name} not found (install postgresql-client-17 or set MAILR_PG_BIN_DIR)")
    return found


def _env(database: str | None = None) -> dict[str, str]:
    extra, _ = _conninfo(database)
    return {**{k: v for k, v in os.environ.items() if not k.startswith("PG")}, **extra}


# ---------------------------------------------------------------- where


def mb(n: int | float | None) -> str:
    """A size in MB as `mailr db-dump`, its --list and the health check all show it: one decimal
    below 10 MB ("0.6"), whole numbers from there ("14"). A dump that is not empty never reads 0."""
    v = (n or 0) / 2**20
    return f"{v:.0f}" if v >= 9.95 else f"{max(v, 0.1) if n else 0.0:.1f}"  # 9.96 reads 10, not 10.0


def dump_dir() -> Path:
    """The folder dumps go to (created inside the target or the data folder when missing). A
    configured backup target that is not usable is an error: a dump on the local disk under an
    unmounted share's mount point would pass for a backup."""
    s = get_settings()
    if s.db_dump_dir:
        p = Path(s.db_dump_dir)
    elif s.backup_target:
        from mailr.core.backup import target_problem

        if (problem := target_problem()) is not None:
            raise DumpError(problem)
        p = Path(s.backup_target) / "postgres"
    else:
        p = Path(s.data_dir) / "backups" / "postgres"
    return private.mkdirs(p)


def list_dumps(directory: Path | None = None) -> list[Path]:
    """Complete dumps, oldest first (by the time in the name)."""
    d = directory or dump_dir()
    return sorted((f for f in d.glob("mailr-*.dump") if NAME_RE.match(f.name)), key=lambda f: NAME_RE.match(f.name)[1])


def _manifest_path(f: Path) -> Path:
    return f.with_name(f.name + ".json")


def read_manifest(f: Path) -> dict[str, Any] | None:
    try:
        return json.loads(_manifest_path(f).read_text())
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- counting rows


async def _tables(conn) -> list[str]:
    """Every ordinary table outside the system schemas, as a quoted name."""
    rows = await conn.fetch(
        "SELECT n.nspname, c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition "
        "AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg_toast%' "
        "AND n.nspname NOT LIKE 'pg_temp%' ORDER BY 1, 2")
    return [(r["relname"] if r["nspname"] == "public" else f'{r["nspname"]}.{r["relname"]}') for r in rows]


def _quote(name: str) -> str:
    return ".".join('"' + part.replace('"', '""') + '"' for part in name.split("."))


async def count_rows(conn) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in await _tables(conn):
        out[t] = int(await conn.fetchval(f"SELECT count(*) FROM {_quote(t)}"))
    return out


async def _revision(conn) -> list[str]:
    try:
        return sorted(r[0] for r in await conn.fetch("SELECT version_num FROM alembic_version"))
    except Exception:  # noqa: BLE001  no alembic_version: an empty database
        return []


# ---------------------------------------------------------------- dump


def _fsync(p: Path) -> None:
    fd = os.open(p, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fp:
        while chunk := fp.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


async def _run_to_file(args: list[str], env: dict[str, str], out: Path) -> None:
    """Run a client program with its standard output written to `out`. Standard output, not
    `-f`, so the program may run anywhere that sees the server (a container, in the tests)."""
    with private.open_new(out) as fp:
        proc = await asyncio.create_subprocess_exec(*args, stdout=fp, stderr=asyncio.subprocess.PIPE, env=env)
        _, err = await proc.communicate()
    if proc.returncode != 0:
        raise DumpError(f"{Path(args[0]).name} failed (exit {proc.returncode}): {_first_line(err)}")


async def _run_from_file(args: list[str], env: dict[str, str], src: Path) -> bytes:
    with src.open("rb") as fp:
        proc = await asyncio.create_subprocess_exec(*args, stdin=fp, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE, env=env)
        out, err = await proc.communicate()
    if proc.returncode != 0:
        raise DumpError(f"{Path(args[0]).name} failed (exit {proc.returncode}): {_first_line(err)}")
    return out


def _first_line(err: bytes) -> str:
    """The first error line of a client program, without anything that looks like a password."""
    lines = [x.strip() for x in err.decode("utf-8", "replace").splitlines() if x.strip()]
    pick = next((x for x in lines if "error" in x.lower()), lines[0] if lines else "no output")
    return re.sub(r"(password[= :]+)\S+", r"\1***", pick, flags=re.IGNORECASE)[:300]


def _stamp(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


async def dump(reason: str = "manual", directory: Path | None = None) -> dict[str, Any]:
    """Write one dump and its manifest; returns the manifest (with `file`, the path)."""
    import asyncpg

    if not re.fullmatch(r"[a-z-]+", reason):
        raise ValueError("reason: lower-case letters and dashes")
    d = private.mkdirs(directory or dump_dir())
    _check_space(d)
    started = datetime.now(UTC)
    name = f"mailr-{_stamp(started)}-{reason}.dump"
    final = d / name
    partial = d / f".{name}.partial"
    env = _env()
    conn = await asyncpg.connect(_dsn())
    try:
        # One snapshot for the counts and the dump: the manifest is exact even while mail arrives.
        tr = conn.transaction(isolation="repeatable_read", readonly=True)
        await tr.start()
        try:
            snapshot = await conn.fetchval("SELECT pg_export_snapshot()")
            server = await conn.fetchval("SHOW server_version")
            revision = await _revision(conn)
            counts = await count_rows(conn)
            await _run_to_file([_bin("pg_dump"), "--format=custom", "--compress=6", f"--snapshot={snapshot}",
                                "--no-password"], env, partial)
        finally:
            await tr.rollback()
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    finally:
        await conn.close()
    try:
        await asyncio.to_thread(_fsync, partial)
        # Read it back: the table of contents of a complete custom-format file.
        toc = await _run_from_file([_bin("pg_restore"), "--list"], env, partial)
        if b"TABLE DATA" not in toc and counts:
            raise DumpError("the dump has no table data")
        digest = await asyncio.to_thread(_sha256, partial)
        size = partial.stat().st_size
        os.replace(partial, final)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    manifest = {"created_at": started.replace(microsecond=0).isoformat(), "reason": reason, "file": name,
                "bytes": size, "sha256": digest, "seconds": round((datetime.now(UTC) - started).total_seconds(), 1),
                "server_version": server, "revision": revision, "tables": counts}
    _write_json(_manifest_path(final), manifest)
    await asyncio.to_thread(_fsync_dir, d)
    await _remember_digest(name, digest)
    log.info("database dump written: %s (%d bytes, %d tables, %.0f s)", name, size, len(counts), manifest["seconds"])
    prune(d)
    return {**manifest, "path": str(final)}


def _write_json(p: Path, data: dict[str, Any]) -> None:
    tmp = p.with_name(f".{p.name}.tmp")
    private.write_text(tmp, json.dumps(data, indent=1, sort_keys=True))
    _fsync(tmp)
    os.replace(tmp, p)


def _fsync_dir(d: Path) -> None:
    try:
        fd = os.open(d, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


class NoSpace(DumpError):
    """The folder has too little free space for the dump."""


class ToolMissing(DumpError):
    """pg_dump or pg_restore is not installed (the image has them; a source checkout may not)."""


def _check_space(d: Path) -> None:
    """Refuse a dump that would not fit: one and a half times the newest dump must be free, and
    afterwards more than MAILR_DISK_FAIL_PERCENT of the disk."""
    dumps = list_dumps(d)
    need = int(dumps[-1].stat().st_size * 1.5) if dumps else 0
    du = shutil.disk_usage(d)
    floor = du.total * get_settings().disk_fail_percent / 100
    if du.free < need or du.free - need < floor:
        raise NoSpace(f"not enough free space for a dump: {mb(du.free)} MB free, about {mb(need)} MB needed")


def prune(directory: Path | None = None, now: datetime | None = None) -> list[str]:
    """Remove nightly dumps beyond MAILR_DB_DUMP_KEEP, dumps taken before migrations or deploys
    older than that many days (the newest three stay), and partial files older than a day. Manual
    dumps stay."""
    d = directory or dump_dir()
    keep = max(1, get_settings().db_dump_keep)
    now = now or datetime.now(UTC)
    removed: list[str] = []
    dumps = list_dumps(d)
    nightly = [f for f in dumps if NAME_RE.match(f.name)[2] == "nightly"]
    # Dumps taken before a deploy (`--reason pre-deploy`) are kept like those before migrations.
    pre = [f for f in dumps if NAME_RE.match(f.name)[2] in ("pre-migrate", "pre-deploy")]
    old = nightly[:-keep] if len(nightly) > keep else []
    for f in pre[:-PREMIGRATE_KEEP_MIN]:
        t = datetime.strptime(NAME_RE.match(f.name)[1], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        if now - t > timedelta(days=keep):
            old.append(f)
    for f in old:
        f.unlink(missing_ok=True)
        _manifest_path(f).unlink(missing_ok=True)
        removed.append(f.name)
    for f in d.glob(".mailr-*.partial"):
        try:
            if now.timestamp() - f.stat().st_mtime > 86400:
                f.unlink()
        except OSError:
            pass
    if removed:
        log.info("removed %d old database dump(s)", len(removed))
    return removed


# ---------------------------------------------------------------- state, schedule


async def _remember_digest(name: str, digest: str) -> None:
    """Keep a dump's checksum in the database, where the backup target cannot change it."""
    try:
        known = await _get_state(DIGESTS_KEY)
        known[name] = digest
        await _set_state(dict(sorted(known.items())[-DIGESTS_MAX:]), DIGESTS_KEY)
    except Exception as e:  # noqa: BLE001  the dump is written; the drill then refuses it
        log.warning("could not record the dump's checksum: %s", type(e).__name__)


async def recorded_digests() -> dict[str, str]:
    try:
        return {k: v for k, v in (await _get_state(DIGESTS_KEY)).items() if isinstance(v, str)}
    except Exception:  # noqa: BLE001  no database to ask (a restore after losing it)
        return {}


async def _get_state(key: str = STATE_KEY) -> dict[str, Any]:
    from sqlalchemy import text

    from mailr.db import session_scope

    async with session_scope() as db:
        v = (await db.execute(text("SELECT value FROM instance_settings WHERE key = :k"), {"k": key})).scalar()
    return v if isinstance(v, dict) else {}


async def _set_state(value: dict[str, Any], key: str = STATE_KEY) -> None:
    from sqlalchemy import bindparam, text
    from sqlalchemy.dialects.postgresql import JSONB

    from mailr.db import session_scope

    async with session_scope() as db:
        await db.execute(text(
            "INSERT INTO instance_settings (key, value, updated_at) VALUES (:k, :v, now()) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = now()").bindparams(
            bindparam("v", type_=JSONB)), {"k": key, "v": value})


async def state() -> dict[str, Any]:
    """The last nightly dump's record: at, ok, reason, file, bytes, seconds, error; last_ok_at."""
    return await _get_state()


async def run_and_record(reason: str = "nightly") -> dict[str, Any]:
    """A dump, with its outcome recorded for the readiness check and the backup heartbeat."""
    prev = await _get_state()
    now = datetime.now(UTC).replace(microsecond=0)
    rec: dict[str, Any] = {"at": now.isoformat(), "reason": reason, "last_ok_at": prev.get("last_ok_at"),
                           "last_ok_bytes": prev.get("last_ok_bytes")}
    try:
        m = await dump(reason)
    except Exception as e:  # noqa: BLE001
        msg = str(e) if isinstance(e, DumpError) else type(e).__name__
        log.error("database dump failed: %s", msg)
        rec.update(ok=False, error=msg[:300])
        await _set_state(rec)
        return rec
    await _record_ok(rec, m, now)
    return rec


async def _record_ok(rec: dict[str, Any], m: dict[str, Any], now: datetime) -> None:
    rec.update(ok=True, file=m["file"], bytes=m["bytes"], seconds=m["seconds"], last_ok_at=now.isoformat(),
               last_ok_bytes=m["bytes"])
    await _set_state(rec)
    from mailr.core import health

    health.after_export()


def is_due(st: dict[str, Any], now: datetime, hour: int) -> bool:
    """Whether a nightly dump is due (module docstring, "When")."""
    at = _parse(st.get("at"))
    ok_at = _parse(st.get("last_ok_at"))
    if at is not None and now - at < timedelta(hours=1):
        return False
    if now.hour == hour and (ok_at is None or now - ok_at > NIGHTLY_GAP):
        return True
    if ok_at is None or now - ok_at > STALE:
        return at is None or now - at > RETRY_EVERY
    return False


def _parse(v: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(v) if v else None
    except (TypeError, ValueError):
        return None


async def enqueue_if_due() -> bool:
    s = get_settings()
    if not s.db_dump:
        return False
    if not is_due(await _get_state(), datetime.now(UTC), s.db_dump_hour % 24):
        return False
    from mailr.core.jobs import enqueue
    from mailr.db import session_scope

    async with session_scope() as db:
        await enqueue(db, "db.dump", {"reason": "nightly"}, dedupe_key="db.dump", max_attempts=1)
    return True


# ---------------------------------------------------------------- before migrations


async def pending_migrations() -> bool | None:
    """True when the database is behind the code's migrations, False when current, None for an
    empty database (a new install: nothing to keep)."""
    import asyncpg
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    ini = Path(__file__).resolve().parents[2] / "alembic.ini"  # noqa: ASYNC240
    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(ini.parent / "migrations"))
    script = ScriptDirectory.from_config(cfg)
    heads = set(script.get_heads())
    conn = await asyncpg.connect(_dsn())
    try:
        if not await conn.fetchval("SELECT to_regclass('public.alembic_version') IS NOT NULL"):
            return None
        current = set(await _revision(conn))
    finally:
        await conn.close()
    if not current:
        return None
    unknown = current - {r.revision for r in script.walk_revisions()}
    if unknown:
        # An older image on a newer database (a rollback): nothing to migrate and no dump to take;
        # the start stops here instead of dumping on every restart.
        raise RuntimeError(f"the database is at revision {', '.join(sorted(unknown))}, which this version of "
                           "Mailr does not know: run the newer version again, or restore the dump taken "
                           "before its migrations and then start this one")
    return current != heads


async def fresh_dump(max_minutes: int) -> dict[str, Any] | None:
    """The manifest of the newest complete dump written in the last `max_minutes` at the
    database's current revision, if there is one (a dump taken just before a deploy)."""
    import asyncpg

    if max_minutes <= 0:
        return None
    conn = await asyncpg.connect(_dsn())
    try:
        current = await _revision(conn)
    finally:
        await conn.close()
    now = datetime.now(UTC)
    for f in reversed(list_dumps()):
        m = read_manifest(f)
        if not m or not m.get("created_at"):
            continue
        try:
            age = (now - datetime.fromisoformat(m["created_at"])).total_seconds() / 60
        except ValueError:
            continue
        if age > max_minutes:
            break  # newest first: the rest are older
        if sorted(m.get("revision") or []) == current and m.get("tables"):
            return m
    return None


async def before_migrate() -> dict[str, Any] | None:
    """A dump before migrations change the schema, into dump_dir(). A failed dump stops the start:
    MAILR_DB_DUMP_BEFORE_MIGRATE=false lets a start go on without one. Without a backup target or
    MAILR_DB_DUMP_DIR, a dump that does not fit in the data volume is skipped with a warning."""
    s = get_settings()
    if not s.db_dump_before_migrate:
        return None
    if not await pending_migrations():
        return None
    fresh = await fresh_dump(s.db_dump_fresh_minutes)
    if fresh is not None:
        # Taken just before the deploy while the old version still served (tools: the release
        # takes `mailr db-dump --reason pre-deploy` first), so the start needs no second dump.
        log.warning("the database schema is behind; a dump from %s at this revision exists: migrating",
                    fresh.get("created_at"))
        return fresh
    log.warning("the database schema is behind: writing a dump before the migrations run")
    try:
        m = await dump("pre-migrate")
    except NoSpace as e:
        if s.backup_target or s.db_dump_dir:
            raise RuntimeError(f"the dump before the migrations failed ({e}); nothing was migrated. Free space on "
                               "the dump folder, or set MAILR_DB_DUMP_BEFORE_MIGRATE=false to start without a "
                               "dump") from None
        log.warning("no dump before the migrations: %s in the data volume. Migrating without one", e)
        return None
    except ToolMissing as e:
        if not s.dev:
            raise RuntimeError(f"the dump before the migrations failed ({e}); nothing was migrated. Install the "
                               "PostgreSQL client tools, or set MAILR_DB_DUMP_BEFORE_MIGRATE=false to start "
                               "without a dump") from None
        log.warning("no dump before the migrations: %s. Migrating without one (development mode)", e)
        return None
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"the dump before the migrations failed ({e}); nothing was migrated. Fix the backup "
                           "target, or set MAILR_DB_DUMP_BEFORE_MIGRATE=false to start without a dump") from None
    log.warning("dump before the migrations: %s", m["file"])
    # A good dump counts for the readiness check like a nightly one (a very old schema without
    # instance_settings skips the record).
    try:
        now = datetime.now(UTC).replace(microsecond=0)
        await _record_ok({"at": now.isoformat(), "reason": "pre-migrate"}, m, now)
    except Exception as e:  # noqa: BLE001  the dump is written; the record is a convenience
        log.warning("could not record the dump before the migrations: %s", type(e).__name__)
    return m


# ---------------------------------------------------------------- restore


async def _create_database(name: str, owner: str | None = None) -> None:
    import asyncpg

    conn = await asyncpg.connect(_dsn("postgres"))
    try:
        if await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name):
            raise DumpError(f"database {name} exists already")
        await conn.execute(f"CREATE DATABASE {_quote(name)}" + (f" OWNER {_quote(owner)}" if owner else ""))
    finally:
        await conn.close()


async def _restricted_role() -> str | None:
    """A role without superuser rights for the drill's restore, or None when the app's own role
    has none (the restore then runs as it). Created on first use."""
    import asyncpg

    _, live = _conninfo()
    name = f"{live}_restore"[:63]
    conn = await asyncpg.connect(_dsn("postgres"))
    try:
        if not await conn.fetchval("SELECT rolsuper FROM pg_roles WHERE rolname = current_user"):
            return None
        if not await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", name):
            await conn.execute(f"CREATE ROLE {_quote(name)} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT")
    finally:
        await conn.close()
    return name


async def _create_extensions(into: str) -> None:
    """The live database's extensions in a new database, created by the app's role: a restricted
    role may not create most of them (vector), and the dump's own CREATE EXTENSION IF NOT EXISTS
    then finds them."""
    import asyncpg

    conn = await asyncpg.connect(_dsn())
    try:
        names = [r[0] for r in await conn.fetch("SELECT extname FROM pg_extension WHERE extname <> 'plpgsql'")]
    finally:
        await conn.close()
    conn = await asyncpg.connect(_dsn(into))
    try:
        for n in names:
            await conn.execute(f"CREATE EXTENSION IF NOT EXISTS {_quote(n)}")
    finally:
        await conn.close()


async def drop_database(name: str) -> None:
    _, live = _conninfo()
    if name == live:
        raise DumpError("refusing to drop the live database")
    import asyncpg

    conn = await asyncpg.connect(_dsn("postgres"))
    try:
        await conn.execute(f"DROP DATABASE IF EXISTS {_quote(name)} WITH (FORCE)")
    finally:
        await conn.close()


def _valid_name(name: str) -> str:
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name):
        raise DumpError("database name: lower-case letters, digits and _")
    return name


async def restore(file: Path, into: str, *, drill: bool = False) -> dict[str, Any]:
    """Load `file` into a new database `into` and compare its row counts with the manifest.
    Returns {database, revision, tables, mismatches, missing_manifest}. Never the live database.
    A checksum recorded for the file must match. `drill`: only a dump with a recorded checksum,
    restored as a role without superuser rights where the app's role has them."""
    import asyncpg

    _, live = _conninfo()
    into = _valid_name(into)
    if into == live:
        raise DumpError("refusing to restore into the live database: restore into a new database and rename it")
    if not file.is_file():  # noqa: ASYNC240
        raise DumpError(f"no such dump: {file.name}")
    manifest = read_manifest(file)
    digest = await asyncio.to_thread(_sha256, file)
    if manifest and manifest.get("sha256") and digest != manifest["sha256"]:
        raise DumpError(f"{file.name} does not match the checksum in its manifest")
    recorded = (await recorded_digests()).get(file.name)
    if recorded is not None and recorded != digest:
        raise DumpError(f"{file.name} does not match the checksum recorded when it was written")
    if drill and recorded is None:
        raise DumpError(f"{file.name} has no checksum recorded by this server: the drill restores only dumps it "
                        "wrote (restore another by hand with --into once you trust it)")
    role = await _restricted_role() if drill else None
    await _create_database(into, owner=role)
    args = [_bin("pg_restore"), "--dbname", into, "--no-owner", "--exit-on-error", "--no-password"]
    if role:
        await _create_extensions(into)
        # As the restricted role: no COPY ... FROM PROGRAM, no other superuser statement.
        args += [f"--role={role}", "--no-privileges", "--no-comments"]
    await _run_from_file(args, _env(into), file)
    conn = await asyncpg.connect(_dsn(into))
    try:
        counts = await count_rows(conn)
        revision = await _revision(conn)
    finally:
        await conn.close()
    mismatches: dict[str, tuple[int | None, int | None]] = {}
    if manifest:
        want = manifest.get("tables") or {}
        for t in sorted(set(want) | set(counts)):
            if want.get(t) != counts.get(t):
                mismatches[t] = (want.get(t), counts.get(t))
    return {"database": into, "file": file.name, "revision": revision, "tables": counts,
            "mismatches": mismatches, "missing_manifest": manifest is None}


async def restore_check(file: Path | None = None, keep: bool = False) -> dict[str, Any]:
    """The restore drill: the newest dump (or `file`) into `<database>_restore_check`, compared
    with its manifest, then dropped. The outcome is recorded (instance_settings db_restore_check)."""
    _, live = _conninfo()
    scratch = f"{live}_restore_check"[:63]
    unknown: list[str] = []
    if file is None:
        dumps = list_dumps()
        if not dumps:
            raise DumpError("no dump to check")
        known = await recorded_digests()
        # The newest dump this server wrote; newer files nobody recorded are named, not restored.
        mine = [f for f in dumps if f.name in known]
        file = mine[-1] if mine else dumps[-1]
        unknown = [f.name for f in dumps if f.name not in known and f.name > file.name]
    await drop_database(scratch)
    started = datetime.now(UTC)
    try:
        out = await restore(file, scratch, drill=True)
    finally:
        if not keep:
            await drop_database(scratch)
    ok = not out["mismatches"] and not out["missing_manifest"]
    rec = {"at": started.replace(microsecond=0).isoformat(), "ok": ok, "file": out["file"],
           "tables": len(out["tables"]), "rows": sum(out["tables"].values()), "mismatches": len(out["mismatches"]),
           "seconds": round((datetime.now(UTC) - started).total_seconds(), 1)}
    try:
        await _set_state(rec, CHECK_KEY)
    except Exception as e:  # noqa: BLE001  the drill still reports on standard output
        log.warning("could not record the restore check: %s", type(e).__name__)
    return {**out, "ok": ok, "seconds": rec["seconds"], "unknown": unknown}
