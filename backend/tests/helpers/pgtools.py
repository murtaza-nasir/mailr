"""pg_dump and pg_restore for the database dump tests (core/dbdump.py).

The image has them (postgresql-client-17). A development machine may not: then each one is a small
script that runs it in the server's own image (pgvector/pgvector:pg17) with the host network, the
connection in the environment (PGHOST, PGPASSWORD, …; never on the command line) and the dump on
standard input or output, which is how dbdump calls them anyway. Without either, the tests skip.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

IMAGE = "pgvector/pgvector:pg17@sha256:cf134a767f474095eeba57e0117be8e568e011a63f33fbf252f14c9b760f8e6f"
ENV = ("PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE", "PGAPPNAME", "PGCONNECT_TIMEOUT")


def _local_major() -> int:
    """The major version of the pg_dump on PATH (0 when there is none or it cannot say)."""
    exe = shutil.which("pg_dump")
    if not exe or not shutil.which("pg_restore"):
        return 0
    out = subprocess.run([exe, "--version"], capture_output=True, text=True).stdout
    digits = "".join(ch if ch.isdigit() else " " for ch in out).split()
    return int(digits[0]) if digits else 0


def pg_bin_dir(tmp: Path) -> str:
    """"" when tools of at least the server's version are on PATH, else a folder of wrapper scripts;
    skips without either. An older pg_dump refuses a newer server ("server version mismatch"), as
    the GitHub runner's 16 did against the CI's Postgres 17."""
    if _local_major() >= 17:
        return ""
    if not shutil.which("docker"):
        pytest.skip("no pg_dump and no docker")
    have = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode == 0
    if not have:
        pytest.skip(f"no pg_dump, and the image {IMAGE.split('@')[0]} is not pulled")
    d = tmp / "pgbin"
    d.mkdir(exist_ok=True)
    envs = " ".join(f"-e {k}" for k in ENV)
    for name in ("pg_dump", "pg_restore"):
        f = d / name
        f.write_text(f'#!/bin/sh\nexec docker run --rm -i --network host {envs} {IMAGE} {name} "$@"\n')
        f.chmod(0o755)
    return str(d)
