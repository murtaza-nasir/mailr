"""Backup files are readable by mailr's account only: files 0600 and folders 0700 in the database
dumps, the Maildir export, and the calendar and notes exports, on the server and on the backup
target, whatever the umask (core/private.py). The process umask 027 left them group-readable."""

import os
import stat
from pathlib import Path

import pytest
from helpers.audit_fixes import _ingest
from helpers.pgtools import pg_bin_dir

from mailr.config import get_settings
from mailr.core import backup, dbdump, private


@pytest.fixture
def loose_umask():
    old = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(old)


def _modes(root: Path) -> dict[str, int]:
    """Every file and folder below `root` (and root itself), by path, with its permission bits."""
    out = {str(root): stat.S_IMODE(root.stat().st_mode)}
    for dirpath, dirs, files in os.walk(root):
        for n in dirs + files:
            p = Path(dirpath) / n
            out[str(p)] = stat.S_IMODE(p.stat().st_mode)
    return out


def _all_private(root: Path) -> None:
    modes = _modes(root)
    loose = {p: oct(m) for p, m in modes.items()
             if m != (private.DIR_MODE if Path(p).is_dir() else private.FILE_MODE)}
    assert len(modes) > 1 and not loose, loose


def test_the_helpers(tmp_path, loose_umask):
    d = private.mkdirs(tmp_path / "a" / "b")
    assert stat.S_IMODE((tmp_path / "a").stat().st_mode) == 0o700 and stat.S_IMODE(d.stat().st_mode) == 0o700
    private.write_text(d / "x.json", "{}")
    private.write_bytes(d / "y.bin", b"1")
    with private.open_new(d / "z.log", "a") as f:
        f.write("one\n")
    with private.open_new(d / "z.log", "a") as f:
        f.write("two\n")
    assert (d / "z.log").read_text() == "one\ntwo\n"
    _all_private(tmp_path / "a")
    # A folder and a file an older release left group-readable are closed on the next write.
    old = tmp_path / "old"
    old.mkdir(mode=0o755)
    (old / "f").write_text("x")
    os.chmod(old / "f", 0o644)
    private.mkdirs(old)
    private.write_text(old / "f", "y")
    _all_private(old)
    # parents=False makes only the last folder.
    with pytest.raises(FileNotFoundError):
        private.mkdirs(tmp_path / "missing" / "c", parents=False)


async def test_the_mail_export_and_its_copy_on_the_target(user, raw, tmp_path, monkeypatch, loose_umask):
    s = get_settings()
    monkeypatch.setattr(s, "data_dir", str(tmp_path / "data"))
    nas = tmp_path / "nas"
    nas.mkdir()
    (nas / backup.TARGET_MARKER).write_text("")
    monkeypatch.setattr(s, "backup_target", str(nas))
    await _ingest(user, raw("Quarterly figures", msgid="<modes-1@example.org>"))
    assert await backup.export_user(user.id) >= 1
    _all_private(backup.export_root(user.id))
    _all_private(backup.target_root(user.id))


async def test_the_database_dump(app, tmp_path, monkeypatch, loose_umask):
    monkeypatch.setattr(get_settings(), "pg_bin_dir", pg_bin_dir(tmp_path))
    monkeypatch.setattr(get_settings(), "db_dump_dir", str(tmp_path / "dumps"))
    m = await dbdump.dump("manual")
    _all_private(tmp_path / "dumps")
    assert (tmp_path / "dumps" / m["file"]).is_file()


def test_calendar_snapshots_and_their_copy(tmp_path, loose_umask):
    from mailr.calendar import backup as cal

    base, tgt = tmp_path / "exports" / "user-1", tmp_path / "nas" / "calendars" / "user-1"
    name = cal.store_snapshot(base, {"collections": [1], "address_books": []},
                              {"calendars/01-work.ics": "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"})
    assert name
    private.mkdirs(tgt)
    assert cal.copy_to_target(base, tgt) == name
    cal._mark(base, tgt)
    _all_private(tmp_path / "exports")
    _all_private(tgt)


@pytest.mark.db_user
async def test_the_notes_export(app, user, client, tmp_path, monkeypatch, loose_umask):
    from mailr.notes import backup as notes

    s = get_settings()
    monkeypatch.setattr(s, "data_dir", str(tmp_path / "data"))
    nas = tmp_path / "nas"
    nas.mkdir()
    (nas / backup.TARGET_MARKER).write_text("")
    monkeypatch.setattr(s, "backup_target", str(nas))
    r = await client.post("/api/v1/notes", json={"kind": "text", "title": "Packing", "body": "Tent, stove."})
    assert r.status_code == 200, r.text
    out = await notes.export_user(user.id)
    assert out["snapshot"] and out["copied"] == out["snapshot"]
    _all_private(notes.export_dir(user.id))
    _all_private(notes.target_dir(user.id))
