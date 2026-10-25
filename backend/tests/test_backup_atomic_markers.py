import os
import threading

from mailr.core import backup


def test_last_run_is_never_read_empty_while_it_is_rewritten(tmp_path):
    """The readiness check reads .last_run on the backup target while an export rewrites it. A
    plain write_text truncates first, so a read could find an empty file and report no copy."""
    p = tmp_path / backup.LAST_RUN
    backup._write_atomic(p, "2026-10-03T23:00:00+00:00")
    stop = threading.Event()
    seen_none = []

    def reader():
        while not stop.is_set():
            if backup.last_run(tmp_path) is None:
                seen_none.append(1)

    t = threading.Thread(target=reader)
    t.start()
    for i in range(2000):
        backup._write_atomic(p, f"2026-10-03T23:{i % 60:02d}:00+00:00")
    stop.set()
    t.join()
    assert not seen_none
    assert not [f for f in os.listdir(tmp_path) if ".tmp-" in f]
