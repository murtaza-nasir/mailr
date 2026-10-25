"""The Android apps this server hands out: "mailr" and "notes" (the separate Notes app).

An admin publishes a release APK with `mailr app-publish --app <app>` (the APK on standard input)
or from Settings → Admin → Phone apps. The server reads the version from the APK itself
(core/apk.py), checks that its package is the one configured for that app
(MAILR_ANDROID_PACKAGE_MAILR, MAILR_ANDROID_PACKAGE_NOTES), and keeps the version it replaces as
the fallback. Phones download the current one at /api/v1/app/<app>.apk; the setup guide and the
Get the app page show its version, size and SHA-256.

Files, in <data_dir>/app/<app>/:

    current.apk, current.json    the version phones get
    previous.apk, previous.json  the one before it (served when current.apk is missing;
                                 `app-publish --rollback` makes it current again)

The .json holds {app, package, version_code, version_name, size, sha256, published_at, by}.
<data_dir>/app/mailr.apk, from before this module (copied in by hand), is still served as the
mailr app until a version is published; the first publish keeps it as the previous version.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mailr.config import get_settings
from mailr.core import apk

log = logging.getLogger("mailr.phone_apps")

APPS: dict[str, str] = {"mailr": "Mailr", "notes": "Mailr Notes"}
MAX_BYTES = 300 * 1024 * 1024
RELEASES = "https://github.com/murtaza-nasir/mailr/releases/latest"


class PublishError(ValueError):
    """The APK was refused: not an APK, the wrong package, or an older version."""


def expected_package(app: str) -> str:
    s = get_settings()
    return {"mailr": s.android_package_mailr, "notes": s.android_package_notes}[app]


def root() -> Path:
    return Path(get_settings().data_dir) / "app"


def _dir(app: str) -> Path:
    if app not in APPS:
        raise PublishError(f"unknown app {app!r}; use {' or '.join(APPS)}")
    return root() / app


def _legacy() -> Path:
    return root() / "mailr.apk"


def _sha256_file(f: Path) -> str:
    h = hashlib.sha256()
    with f.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_meta(f: Path) -> dict[str, Any] | None:
    try:
        v = json.loads(f.read_text())
    except (OSError, ValueError):
        return None
    return v if isinstance(v, dict) else None


_legacy_cache: dict[tuple[int, int], dict[str, Any]] = {}


def _legacy_meta(f: Path) -> dict[str, Any] | None:
    """The hand-copied <data_dir>/app/mailr.apk, read once per change of size or time."""
    st = f.stat()
    key = (st.st_size, st.st_mtime_ns)
    if key not in _legacy_cache:
        _legacy_cache.clear()
        try:
            info = apk.read(f.read_bytes())
            meta = {"package": info.package, "version_code": info.version_code, "version_name": info.version_name}
        except apk.ApkError:
            meta = {"package": "", "version_code": None, "version_name": ""}
        _legacy_cache[key] = {"app": "mailr", **meta, "size": st.st_size, "sha256": _sha256_file(f),
                              "published_at": datetime.fromtimestamp(st.st_mtime, UTC).isoformat(), "by": "file"}
    return _legacy_cache[key]


def served(app: str) -> tuple[Path, dict[str, Any]] | None:
    """The APK phones get for this app and its details, or None when this server holds none."""
    d = _dir(app)
    for name in ("current", "previous"):
        f, m = d / f"{name}.apk", d / f"{name}.json"
        if f.is_file():
            meta = _read_meta(m)
            if meta is None:  # the details went missing: read them from the file again
                meta = {"app": app, "size": f.stat().st_size, "sha256": _sha256_file(f), "published_at": None,
                        "by": None, "package": "", "version_code": None, "version_name": ""}
            return f, meta
    if app == "mailr" and _legacy().is_file():
        meta = _legacy_meta(_legacy())
        return (_legacy(), meta) if meta else None
    return None


def previous(app: str) -> dict[str, Any] | None:
    d = _dir(app)
    return _read_meta(d / "previous.json") if (d / "previous.apk").is_file() else None


def download_name(app: str, meta: dict[str, Any]) -> str:
    v = str(meta.get("version_name") or "").strip()
    safe = "".join(c for c in v if c.isalnum() or c in ".-_")[:40]
    return f"{'mailr' if app == 'mailr' else 'mailr-notes'}{'-' + safe if safe else ''}.apk"


def url(app: str) -> str:
    """Where phones download this app: MAILR_ANDROID_APK_URL for mailr when set, else the APK this
    server holds, else the latest release."""
    s = get_settings()
    if app == "mailr" and s.android_apk_url:
        return s.android_apk_url
    if served(app):
        return s.base_url.rstrip("/") + f"/api/v1/app/{app}.apk"
    return RELEASES


def release(app: str) -> dict[str, Any]:
    """What the setup guide, the Get the app page and Admin → Phone apps show for one app."""
    s = get_settings()
    hit = served(app)
    meta = hit[1] if hit else {}
    external = app == "mailr" and bool(s.android_apk_url)
    return {"app": app, "name": APPS[app], "package": expected_package(app), "url": url(app),
            "on_server": hit is not None and not external,
            "version_name": meta.get("version_name") or None if hit and not external else None,
            "version_code": meta.get("version_code") if hit and not external else None,
            "size": meta.get("size") if hit and not external else None,
            "sha256": meta.get("sha256") if hit and not external else None,
            "published_at": meta.get("published_at") if hit and not external else None}


def releases() -> list[dict[str, Any]]:
    return [release(a) for a in APPS]


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        os.chmod(tmp, 0o640)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


@contextlib.contextmanager
def _lock(app: str):
    """One publish or rollback of an app at a time (the CLI and the admin page may overlap)."""
    d = _dir(app)
    d.mkdir(parents=True, exist_ok=True)
    with (d / ".lock").open("a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def check(app: str, data: bytes) -> apk.ApkInfo:
    """Validate an upload for `app`. PublishError with a plain reason."""
    _dir(app)
    if not data:
        raise PublishError("no APK received (empty input)")
    if len(data) > MAX_BYTES:
        raise PublishError(f"the APK is larger than {MAX_BYTES // (1024 * 1024)} MB")
    try:
        info = apk.read(data)
    except apk.ApkError as e:
        raise PublishError(str(e)) from None
    want = expected_package(app)
    if info.package != want:
        raise PublishError(f"this APK is {info.package}, not the {APPS[app]} app ({want})")
    return info


def publish(app: str, data: bytes, *, by: str, allow_older: bool = False) -> dict[str, Any]:
    """Make `data` the version phones get. The version it replaces becomes the previous one.
    Returns {"release": …, "replaced": meta | None, "unchanged": bool}. PublishError."""
    info = check(app, data)
    with _lock(app):
        return _publish(app, data, info, by=by, allow_older=allow_older)


def _publish(app: str, data: bytes, info: apk.ApkInfo, *, by: str, allow_older: bool) -> dict[str, Any]:
    d = _dir(app)
    sha = hashlib.sha256(data).hexdigest()
    hit = served(app)
    cur = hit[1] if hit else None
    if cur and cur.get("sha256") == sha:
        return {"release": release(app), "replaced": None, "unchanged": True}
    if cur and isinstance(cur.get("version_code"), int) and info.version_code < cur["version_code"] and not allow_older:
        raise PublishError(f"version {info.version_name} ({info.version_code}) is older than the one served, "
                           f"{cur.get('version_name')} ({cur['version_code']}); allow an older version to go back")
    d.mkdir(parents=True, exist_ok=True)
    meta = {"app": app, "package": info.package, "version_code": info.version_code,
            "version_name": info.version_name, "size": len(data), "sha256": sha,
            "published_at": datetime.now(UTC).isoformat(), "by": by}
    replaced = None
    if hit:
        src, old = hit
        if src != d / "previous.apk":
            os.replace(src, d / "previous.apk")
            _write_atomic(d / "previous.json", json.dumps(old, indent=1).encode())
            (d / "current.json").unlink(missing_ok=True)
            replaced = old
    _write_atomic(d / "current.apk", data)
    _write_atomic(d / "current.json", json.dumps(meta, indent=1).encode())
    log.info("published %s %s (%s), sha256 %s, by %s", app, info.version_name, info.version_code, sha, by)
    return {"release": release(app), "replaced": replaced, "unchanged": False}


def rollback(app: str, *, by: str) -> dict[str, Any]:
    """Swap the current and the previous version. PublishError when there is no previous one."""
    with _lock(app):
        return _rollback(app, by=by)


def _rollback(app: str, *, by: str) -> dict[str, Any]:
    d = _dir(app)
    prev, cur = d / "previous.apk", d / "current.apk"
    if not prev.is_file():
        raise PublishError(f"there is no previous {APPS[app]} version to go back to")
    pmeta = _read_meta(d / "previous.json") or {}
    cmeta = _read_meta(d / "current.json") if cur.is_file() else None
    tmp = d / ".swap.apk"
    if cur.is_file():
        os.replace(cur, tmp)
    os.replace(prev, cur)
    _write_atomic(d / "current.json", json.dumps({**pmeta, "by": by, "published_at": datetime.now(UTC).isoformat()},
                                                 indent=1).encode())
    if tmp.is_file():
        os.replace(tmp, prev)
        _write_atomic(d / "previous.json", json.dumps(cmeta or {}, indent=1).encode())
    else:
        (d / "previous.json").unlink(missing_ok=True)
    log.info("rolled %s back to %s, by %s", app, pmeta.get("version_name"), by)
    return {"release": release(app), "replaced": cmeta, "unchanged": False}
