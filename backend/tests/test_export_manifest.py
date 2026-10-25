"""The mail export's manifest across batches (core/backup.py): each batch reads only the lines
added since the last one, and the backup target's copy gets the new lines appended instead of the
whole file copied again (a first export of 500,000 messages copied about 25 GB of manifest)."""

import shutil

from helpers.audit_fixes import _ingest

from mailr.config import get_settings
from mailr.core import backup


def test_the_blob_ids_are_read_from_where_the_last_read_stopped(tmp_path):
    f = tmp_path / ".exported"
    f.write_text("b1\tInbox/cur/1.1.mailr:2,S\nold-format-line\n")
    assert backup._exported_blobs(f) == {"b1"}
    with f.open("a") as fh:
        fh.write("b2\tInbox/cur/2.2.mailr:2,\nb3\tArch")  # the last line is still being written
    assert backup._exported_blobs(f) == {"b1", "b2"}
    with f.open("a") as fh:
        fh.write("ive/cur/3.3.mailr:2,\n")
    assert backup._exported_blobs(f) == {"b1", "b2", "b3"}
    assert backup._exported_blobs(f) == set(backup.exported_manifest_from(f))
    backup._write_atomic(f, "b3\tArchive/cur/3.3.mailr:2,\n")  # a rewrite (Delete forever): a new file
    assert backup._exported_blobs(f) == {"b3"}
    f.unlink()
    assert backup._exported_blobs(f) == set()


async def test_batches_append_to_the_targets_manifest(user, raw, tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "data_dir", str(tmp_path / "data"))
    nas = tmp_path / "nas"
    nas.mkdir()
    (nas / backup.TARGET_MARKER).write_text("")
    monkeypatch.setattr(s, "backup_target", str(nas))
    monkeypatch.setattr(backup, "BATCH", 2)
    for i in range(5):
        await _ingest(user, raw(f"Export batch {i}"))
    copies = []
    real = shutil.copy2

    def copy2(src, dst, *a, **kw):
        if str(dst).endswith(".exported"):
            copies.append(str(dst))
        return real(src, dst, *a, **kw)

    monkeypatch.setattr(backup.shutil, "copy2", copy2)
    written, caught_up = await backup.export_until_caught_up(user.id)
    assert written >= 5 and caught_up
    base, tgt = backup.export_root(user.id), backup.target_root(user.id)
    assert (tgt / ".exported").read_text() == (base / ".exported").read_text()
    assert copies == []  # appended batch by batch, never copied whole
    # A target that missed a batch gets the whole file once, then appends again.
    (tgt / ".exported").write_text("")
    await _ingest(user, raw("Export batch late"))
    await backup.export_until_caught_up(user.id)
    assert (tgt / ".exported").read_text() == (base / ".exported").read_text() and len(copies) == 1
