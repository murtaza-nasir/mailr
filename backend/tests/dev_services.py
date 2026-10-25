"""The shared dev services the suite talks to (deploy/dev/docker-compose.yml), checked once per
process.

Radicale (:15232) carries several hundred calendar tests. They used to check it with a 2 s request
at import, in every module and on every worker, and skipped when it did not answer in time: a slow
or stopped Radicale gave a green run with hundreds of skips. Now conftest stops the run when
Radicale does not answer (pytest_sessionstart), unless MAILR_TEST_WITHOUT_RADICALE=1 says this
machine has none; then the calendar tests skip as before.

The Stalwart calendar tests (the `*_stalwart.py` files: the built-in calendars, sharing, group
calendars, phone access, backups) follow the same rule. conftest points them at the dev Stalwart
of the compose file (MAILR_STALWART_URL and its service account) unless MAILR_TEST_STALWART_URL and
MAILR_TEST_STALWART_ADMIN name another server, and stops the run when that server does not
answer. MAILR_TEST_WITHOUT_STALWART_DAV=1 skips them instead."""

from __future__ import annotations

import functools
import os

import httpx

RADICALE = "http://localhost:15232/"
WITHOUT_RADICALE = "MAILR_TEST_WITHOUT_RADICALE"


@functools.cache
def radicale_up() -> bool:
    """Radicale answers below 500. A refused connection is final; a timeout is tried again."""
    for _ in range(3):
        try:
            return httpx.get(RADICALE, timeout=5).status_code < 500
        except httpx.ConnectError:
            return False
        except httpx.HTTPError:
            continue
    return False


def radicale_optional() -> bool:
    return os.environ.get(WITHOUT_RADICALE, "").lower() in ("1", "true", "yes")


def require_radicale() -> str | None:
    """The reason to stop the run, or None."""
    if radicale_up() or radicale_optional():
        return None
    return (f"the dev Radicale at {RADICALE} does not answer, and several hundred calendar tests need it. "
            "Start the dev services (deploy/dev/docker-compose.yml: from a second checkout, "
            "`docker compose up -d --no-recreate`), or set "
            f"{WITHOUT_RADICALE}=1 on a machine without Radicale to skip those tests.")


WITHOUT_STALWART_DAV = "MAILR_TEST_WITHOUT_STALWART_DAV"
# The dev Stalwart's container (deploy/dev/docker-compose.yml, project mailr-dev) and its IMAPS port.
DEV_STALWART_CONTAINER = "mailr-dev-stalwart-1"
DEV_STALWART_IMAPS = "127.0.0.1:11993"


def stalwart_dav_optional() -> bool:
    return os.environ.get(WITHOUT_STALWART_DAV, "").lower() in ("1", "true", "yes")


def stalwart_dav_defaults(env: dict[str, str | None], store_url: str, admin_user: str, admin_password: str) -> None:
    """Point the Stalwart calendar tests at the suite's own store server (the dev Stalwart) unless
    the environment names another one or opts out. Sets os.environ, before any test module reads it."""
    if stalwart_dav_optional():
        return
    if env.get("MAILR_TEST_STALWART_URL"):  # another server, named in the environment or backend/.env
        for k in ("MAILR_TEST_STALWART_URL", "MAILR_TEST_STALWART_ADMIN", "MAILR_TEST_STALWART_IMAPS",
                  "MAILR_TEST_STALWART_CONTAINER"):
            if env.get(k):
                os.environ.setdefault(k, str(env[k]))
        return
    import shutil

    os.environ["MAILR_TEST_STALWART_URL"] = store_url
    os.environ["MAILR_TEST_STALWART_ADMIN"] = f"{admin_user}:{admin_password}"
    if store_url.rstrip("/") in ("http://localhost:18080", "http://127.0.0.1:18080"):
        # The compose file's own server: its IMAPS port and its container (the gateway test).
        os.environ.setdefault("MAILR_TEST_STALWART_IMAPS", DEV_STALWART_IMAPS)
        if shutil.which("docker"):
            os.environ.setdefault("MAILR_TEST_STALWART_CONTAINER", DEV_STALWART_CONTAINER)


@functools.cache
def stalwart_dav_up() -> str | None:
    """None when the Stalwart of the calendar tests answers its service account, else why not."""
    url = os.environ.get("MAILR_TEST_STALWART_URL", "").rstrip("/")
    admin = os.environ.get("MAILR_TEST_STALWART_ADMIN", "")
    if not url or ":" not in admin:
        return "MAILR_TEST_STALWART_URL or MAILR_TEST_STALWART_ADMIN is not set"
    login, pw = admin.split(":", 1)
    for _ in range(3):
        try:
            r = httpx.get(url + "/jmap/session", auth=(login, pw), timeout=5)
            return None if r.status_code == 200 else f"{url} answered {r.status_code} to the service account"
        except httpx.ConnectError as e:
            return f"{url} refused the connection ({e})"
        except httpx.HTTPError:
            continue
    return f"{url} did not answer in time"


def require_stalwart_dav() -> str | None:
    """The reason to stop the run, or None."""
    if stalwart_dav_optional():
        return None
    why = stalwart_dav_up()
    if why is None:
        return None
    return (f"the Stalwart of the calendar tests (*_stalwart.py) is not usable: {why}. Start the dev services "
            "(deploy/dev/docker-compose.yml), point MAILR_TEST_STALWART_URL and MAILR_TEST_STALWART_ADMIN at "
            f"another throwaway Stalwart, or set {WITHOUT_STALWART_DAV}=1 to skip those tests.")


def stalwart_dav_skip_reason() -> str:
    return (f"{WITHOUT_STALWART_DAV} is set" if stalwart_dav_optional()
            else "set MAILR_TEST_STALWART_URL and MAILR_TEST_STALWART_ADMIN")
