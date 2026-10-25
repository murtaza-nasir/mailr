"""The nightly database dump, the dump before migrations and the restore drill (core/dbdump.py)."""

from datetime import UTC, datetime, timedelta

import pytest
from helpers.pgtools import pg_bin_dir
from sqlalchemy import text

from mailr.config import get_settings
from mailr.core import dbdump, health
from mailr.db import session_scope

NOW = datetime(2026, 10, 14, 9, 5, tzinfo=UTC)


def _st(at=None, ok_at=None, ok=True):
    return {"at": at and at.isoformat(), "last_ok_at": ok_at and ok_at.isoformat(), "ok": ok}


def test_due_at_the_hour_once_a_night():
    assert dbdump.is_due({}, NOW, 9)  # never dumped
    assert dbdump.is_due(_st(NOW - timedelta(hours=24), NOW - timedelta(hours=24)), NOW, 9)
    # Ran 30 minutes ago: not again this hour.
    assert not dbdump.is_due(_st(NOW - timedelta(minutes=30), NOW - timedelta(minutes=30)), NOW, 9)
    # Another hour, last good dump 10 h ago: not due.
    assert not dbdump.is_due(_st(NOW - timedelta(hours=10), NOW - timedelta(hours=10)), NOW, 3)


def test_overdue_retries_every_six_hours():
    old = NOW - timedelta(hours=30)
    assert dbdump.is_due(_st(NOW - timedelta(hours=7), old, ok=False), NOW, 3)
    assert not dbdump.is_due(_st(NOW - timedelta(hours=2), old, ok=False), NOW, 3)


def _touch(d, stamp, reason):
    f = d / f"mailr-{stamp}-{reason}.dump"
    f.write_bytes(b"x")
    (d / (f.name + ".json")).write_text("{}")
    return f


def test_prune_keeps_the_newest_nightly_and_recent_premigrate(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "db_dump_keep", 3)
    for day in range(1, 7):
        _touch(tmp_path, f"202610{day:02d}T090000Z", "nightly")
    for day in (1, 2, 3, 4, 12):
        _touch(tmp_path, f"202610{day:02d}T120000Z", "pre-migrate")
    _touch(tmp_path, "20260101T000000Z", "manual")
    (tmp_path / ".mailr-x.dump.partial").write_bytes(b"x")
    removed = dbdump.prune(tmp_path, now=datetime(2026, 10, 14, tzinfo=UTC))
    left = sorted(f.name for f in dbdump.list_dumps(tmp_path))
    assert [n for n in left if "nightly" in n] == [f"mailr-202610{d:02d}T090000Z-nightly.dump" for d in (4, 5, 6)]
    # Pre-migration dumps: the newest three stay; of the older ones only those within 3 days.
    assert [n for n in left if "pre-migrate" in n] == [f"mailr-202610{d:02d}T120000Z-pre-migrate.dump"
                                                       for d in (3, 4, 12)]
    assert "mailr-20260101T000000Z-manual.dump" in left
    assert all(not (tmp_path / (n + ".json")).exists() for n in removed)


def test_one_size_format():
    assert [dbdump.mb(n) for n in (0, None, 1, 620_000, 5 * 2**20, int(9.96 * 2**20), 10 * 2**20, int(14.6 * 2**20))] \
        == ["0.0", "0.0", "0.1", "0.6", "5.0", "10", "10", "15"]


async def test_the_cli_prints_the_same_size(app, tmp_path, monkeypatch, capsys):
    import argparse

    from mailr import cli

    d = tmp_path / "dumps"
    d.mkdir()
    name = _fake_dump(d, NOW, "manual", ["abc"])
    (d / name).write_bytes(b"x" * 620_000)
    assert await cli._db_dump(argparse.Namespace(list=True, dir=str(d), reason="manual")) == 0
    assert f"{name}  0.6 MB  1 tables" in capsys.readouterr().out

    async def dump(reason, directory):
        return {"path": str(d / name), "bytes": 620_000, "tables": {"users": 1}, "seconds": 1.0}

    monkeypatch.setattr(dbdump, "dump", dump)
    assert await cli._db_dump(argparse.Namespace(list=False, dir=str(d), reason="manual")) == 0
    assert "  0.6 MB  1 tables, 1 rows" in capsys.readouterr().out


def test_health_evaluation():
    now = datetime.now(UTC)
    assert health.evaluate_db_dump({}, now, False)[0] is True
    assert health.evaluate_db_dump({}, now, True)[0] is False
    ok = {"ok": True, "last_ok_at": (now - timedelta(hours=3)).isoformat(), "last_ok_bytes": 5 * 2**20}
    r = health.evaluate_db_dump(ok, now, True)
    assert r[0] is True and str(r[1]) == "last dump 3 h ago (5.0 MB)"
    # A small dump: one decimal, as `mailr db-dump` and its --list print it (it read "0 MB" here).
    r = health.evaluate_db_dump({**ok, "last_ok_bytes": 620_000}, now, True)
    assert str(r[1]) == "last dump 3 h ago (0.6 MB)"
    assert health.evaluate_db_dump({**ok, "ok": False}, now, True)[0] is False
    stale = {**ok, "last_ok_at": (now - timedelta(hours=30)).isoformat()}
    assert health.evaluate_db_dump(stale, now, True)[0] is False


async def test_dump_fails_cleanly_on_an_unmounted_target(app, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "backup_target", str(tmp_path / "nas"))
    monkeypatch.setattr(get_settings(), "db_dump_dir", "")
    (tmp_path / "nas").mkdir()  # no marker file: not mounted
    rec = await dbdump.run_and_record("nightly")
    assert rec["ok"] is False and "not mounted" in rec["error"]
    assert not (tmp_path / "nas" / "postgres").exists()
    st = await dbdump.state()
    assert st["ok"] is False
    async with session_scope() as db:
        await db.execute(text("DELETE FROM instance_settings WHERE key = 'db_dump'"))


async def test_before_migrate_only_with_a_schema_behind(app, monkeypatch):
    monkeypatch.setattr(get_settings(), "backup_target", "")
    monkeypatch.setattr(get_settings(), "db_dump_dir", "")
    assert await dbdump.before_migrate() is None
    assert await dbdump.pending_migrations() is False


async def test_before_migrate_dumps_into_the_data_volume_without_a_target(app, tmp_path, monkeypatch):
    """A default install (no backup target) still gets its dump before migrations, in the data
    volume; one that does not fit there is skipped with a warning, and the start goes on."""
    monkeypatch.setattr(get_settings(), "backup_target", "")
    monkeypatch.setattr(get_settings(), "db_dump_dir", "")
    monkeypatch.setattr(get_settings(), "data_dir", str(tmp_path / "data"))
    monkeypatch.setattr(get_settings(), "db_dump_fresh_minutes", 0)
    dumped: list = []

    async def behind():
        return True

    async def fake_dump(reason="manual", directory=None):
        d = dbdump.dump_dir()
        dbdump._check_space(d)
        dumped.append((reason, d))
        return {"file": "x", "reason": reason}

    monkeypatch.setattr(dbdump, "pending_migrations", behind)
    monkeypatch.setattr(dbdump, "dump", fake_dump)
    await dbdump.before_migrate()
    assert dumped == [("pre-migrate", tmp_path / "data" / "backups" / "postgres")]

    import shutil
    from collections import namedtuple

    full = namedtuple("usage", "total used free")(1000, 990, 10)
    monkeypatch.setattr(shutil, "disk_usage", lambda p: full)
    assert await dbdump.before_migrate() is None and len(dumped) == 1
    # With a dump folder of its own, too little space stops the start as any failed dump does.
    monkeypatch.setattr(get_settings(), "db_dump_dir", str(tmp_path / "dumps"))
    with pytest.raises(RuntimeError, match="not enough free space"):
        await dbdump.before_migrate()


async def test_restore_drill(app, tmp_path, monkeypatch):
    """Dump the test database, restore it into a fresh database, compare the row count of every
    table with the dump's manifest, and refuse the live database as a target."""
    monkeypatch.setattr(get_settings(), "pg_bin_dir", pg_bin_dir(tmp_path))
    monkeypatch.setattr(get_settings(), "db_dump_dir", str(tmp_path / "dumps"))
    async with session_scope() as db:
        await db.execute(text("INSERT INTO instance_settings (key, value) VALUES ('drill-marker', '{\"n\": 1}') "
                              "ON CONFLICT (key) DO NOTHING"))
    m = await dbdump.dump("manual")
    assert m["bytes"] > 0 and m["tables"]["users"] >= 0 and m["tables"]["instance_settings"] >= 1
    assert m["revision"] and len(m["sha256"]) == 64
    f = dbdump.list_dumps(tmp_path / "dumps")[-1]
    assert f.name == m["file"] and dbdump.read_manifest(f)["tables"] == m["tables"]

    out = await dbdump.restore_check(f)
    assert out["ok"] is True, out["mismatches"]
    assert out["tables"] == m["tables"] and out["revision"] == m["revision"]
    st = await dbdump._get_state(dbdump.CHECK_KEY)
    assert st["ok"] is True and st["mismatches"] == 0

    # A manifest that disagrees is reported table by table.
    import json

    man = json.loads((f.parent / (f.name + ".json")).read_text())
    man["tables"]["instance_settings"] += 1
    man.pop("sha256")
    (f.parent / (f.name + ".json")).write_text(json.dumps(man))
    bad = await dbdump.restore_check(f)
    assert bad["ok"] is False and set(bad["mismatches"]) == {"instance_settings"}

    _, live = dbdump._conninfo()
    with pytest.raises(dbdump.DumpError):
        await dbdump.restore(f, live)
    with pytest.raises(dbdump.DumpError):
        await dbdump.drop_database(live)
    async with session_scope() as db:
        await db.execute(text("DELETE FROM instance_settings WHERE key IN ('drill-marker', 'db_restore_check')"))
    await _drop_restore_role()


async def _drop_restore_role():
    """The drill's role is per database: the test databases' ones go with them."""
    import asyncpg

    _, live = dbdump._conninfo()
    conn = await asyncpg.connect(dbdump._dsn("postgres"))
    try:
        await conn.execute(f"DROP ROLE IF EXISTS {dbdump._quote(live + '_restore')}")
    finally:
        await conn.close()


def _fake_dump(d, when, reason, revision, tables=None):
    import json

    name = f"mailr-{when.strftime('%Y%m%dT%H%M%SZ')}-{reason}.dump"
    (d / name).write_bytes(b"x")
    (d / (name + ".json")).write_text(json.dumps({
        "created_at": when.replace(microsecond=0).isoformat(), "reason": reason, "file": name,
        "revision": revision, "tables": tables if tables is not None else {"users": 1}}))
    return name


async def test_a_recent_dump_before_the_deploy_stands_in_for_the_one_before_migrations(app, tmp_path, monkeypatch):
    """The release dumps while the old version still serves (`--reason pre-deploy`); the start then
    migrates without a second dump. An old dump, or one at another revision, does not count, and
    then the start still dumps first (14 Oct 2026: the dump at start took ~2 min of downtime)."""
    from datetime import UTC, datetime, timedelta

    import asyncpg

    d = tmp_path / "dumps"
    d.mkdir()
    monkeypatch.setattr(get_settings(), "db_dump_dir", str(d))
    monkeypatch.setattr(get_settings(), "db_dump_fresh_minutes", 120)
    conn = await asyncpg.connect(dbdump._dsn())
    try:
        current = await dbdump._revision(conn)
    finally:
        await conn.close()
    now = datetime.now(UTC)

    # Too old, and a recent one at another revision: neither counts.
    _fake_dump(d, now - timedelta(hours=5), "pre-deploy", current)
    _fake_dump(d, now - timedelta(minutes=10), "pre-deploy", ["0000aaaa"])
    assert await dbdump.fresh_dump(120) is None

    dumped: list[str] = []

    async def fake_dump(reason="manual", directory=None):
        dumped.append(reason)
        return {"file": "x", "reason": reason}

    async def behind():
        return True

    monkeypatch.setattr(dbdump, "dump", fake_dump)
    monkeypatch.setattr(dbdump, "pending_migrations", behind)
    await dbdump.before_migrate()
    assert dumped == ["pre-migrate"]  # no fresh dump: the start dumps first, as before

    # A fresh one at the current revision: the start migrates without dumping again.
    name = _fake_dump(d, now - timedelta(minutes=3), "pre-deploy", current)
    assert (await dbdump.fresh_dump(120))["file"] == name
    dumped.clear()
    m = await dbdump.before_migrate()
    assert dumped == [] and m["file"] == name
    # Switched off: always dump.
    monkeypatch.setattr(get_settings(), "db_dump_fresh_minutes", 0)
    await dbdump.before_migrate()
    assert dumped == ["pre-migrate"]


def test_dumps_before_a_deploy_are_pruned_like_those_before_migrations(tmp_path, monkeypatch):
    from datetime import UTC, datetime, timedelta

    monkeypatch.setattr(get_settings(), "db_dump_keep", 14)
    now = datetime.now(UTC)
    for i in range(5):
        _fake_dump(tmp_path, now - timedelta(days=30 + i), "pre-deploy", ["a"])
    _fake_dump(tmp_path, now - timedelta(days=1), "pre-deploy", ["a"])
    removed = dbdump.prune(tmp_path, now)
    left = [f.name for f in dbdump.list_dumps(tmp_path)]
    assert len(left) == 3 and len(removed) == 3  # the newest three stay; older than 14 days go


async def test_the_drill_restores_only_dumps_this_server_wrote(app, tmp_path, monkeypatch):
    """A dump planted on the backup target, with a manifest that matches it, is never the drill's
    pick, and a dump changed after it was written is refused. The drill restores as a role without
    superuser rights."""
    import json
    import shutil

    import asyncpg

    monkeypatch.setattr(get_settings(), "pg_bin_dir", pg_bin_dir(tmp_path))
    d = tmp_path / "dumps"
    monkeypatch.setattr(get_settings(), "db_dump_dir", str(d))
    m = await dbdump.dump("manual")
    f = d / m["file"]
    assert (await dbdump.recorded_digests())[f.name] == m["sha256"]

    planted = d / "mailr-29991231T000000Z-manual.dump"
    shutil.copy(f, planted)
    with planted.open("ab") as fh:
        fh.write(b"\0")
    man = {**json.loads((d / (f.name + ".json")).read_text()), "file": planted.name,
           "sha256": dbdump._sha256(planted)}
    (d / (planted.name + ".json")).write_text(json.dumps(man))
    with pytest.raises(dbdump.DumpError, match="no checksum recorded"):
        await dbdump.restore_check(planted)
    out = await dbdump.restore_check(keep=True)
    try:
        assert out["file"] == f.name and out["unknown"] == [planted.name] and out["ok"] is True
        conn = await asyncpg.connect(dbdump._dsn(out["database"]))
        try:
            owner = await conn.fetchval("SELECT tableowner FROM pg_tables WHERE tablename = 'users'")
            superuser = await conn.fetchval("SELECT rolsuper FROM pg_roles WHERE rolname = $1", owner)
        finally:
            await conn.close()
        assert owner.endswith("_restore") and superuser is False
    finally:
        await dbdump.drop_database(out["database"])

    # The recorded file, changed on the target together with its manifest: refused.
    with f.open("ab") as fh:
        fh.write(b"\0")
    man = json.loads((d / (f.name + ".json")).read_text())
    man["sha256"] = dbdump._sha256(f)
    (d / (f.name + ".json")).write_text(json.dumps(man))
    with pytest.raises(dbdump.DumpError, match="recorded when it was written"):
        await dbdump.restore(f, "mailr_restore_tampered")
    await _drop_restore_role()


async def test_before_migrate_without_the_client_tools(app, monkeypatch):
    """A source checkout without pg_dump migrates in development mode with a warning; a production
    start stops instead of migrating without a dump."""
    monkeypatch.setattr(get_settings(), "db_dump_fresh_minutes", 0)
    monkeypatch.setattr(get_settings(), "pg_bin_dir", "")

    async def behind():
        return True

    monkeypatch.setattr(dbdump, "pending_migrations", behind)
    monkeypatch.setattr(dbdump.shutil, "which", lambda name: None)
    monkeypatch.setattr(get_settings(), "dev", True)
    assert await dbdump.before_migrate() is None
    monkeypatch.setattr(get_settings(), "dev", False)
    with pytest.raises(RuntimeError, match="client tools"):
        await dbdump.before_migrate()
