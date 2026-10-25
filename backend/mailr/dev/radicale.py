"""Test users in the shared dev Radicale (deploy/dev/docker-compose.yml, :15232).

Every backend test that needs a calendar, and every e2e seed, makes a Radicale user of its own
(`u` or `e2e` and eight hex digits): users never collide, but they used to stay forever (about
57,000 collections, 2.6 GB by October 2026). The test session and the e2e seeder now delete the
users they made when they finish (`track`, `delete_users`). Users that older runs left match the
same patterns (`is_test_user`), so a cleanup can find them.

Only names that match `is_test_user` are ever deleted, and only on a local Radicale.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from urllib.parse import urlsplit

DEV_RADICALE = "http://localhost:15232/"

# `u` + 8 hex: backend tests (tests/test_calendar.py and others); `e2e` + 8 hex: mailr e2e-seed.
TEST_USER = re.compile(r"^(?:u[0-9a-f]{8}|e2e[0-9a-f]{8})$")
# The demo seed's calendar user for a test database (dev/demo.py cal_user): mailr_wt_<slug>__test
# and its pytest workers' copies (…__test_gw3). Never a checkout's own demo (…__demo) or mailr_demo.
TEST_DEMO_USER = re.compile(r"^alex\.rivera-mailr-wt-[a-z0-9-]+-test(?:-gw\d+)?$")


def is_test_user(name: str) -> bool:
    return bool(TEST_USER.fullmatch(name) or TEST_DEMO_USER.fullmatch(name))


def _local(url: str) -> bool:
    return (urlsplit(url).hostname or "") in ("localhost", "127.0.0.1", "::1")


def delete_users(names: Iterable[str], base: str = DEV_RADICALE) -> int:
    """Delete these test users (their principal and every collection under it) over WebDAV.
    Returns how many were deleted or already gone. Names that are not test users are skipped."""
    import httpx

    if not _local(base):
        raise ValueError(f"refusing to delete Radicale users on {base}: not a local development server")
    done = 0
    with httpx.Client(timeout=30) as c:
        for n in sorted(set(names)):
            if not is_test_user(n):
                continue
            try:
                r = c.request("DELETE", f"{base.rstrip('/')}/{n}/", auth=(n, "x"))
            except httpx.HTTPError:
                continue
            if r.status_code in (200, 204, 404):
                done += 1
    return done


def track() -> Callable[[], set[str]]:
    """Record the test users that caldav clients in this process sign in as on a local Radicale.
    Returns `stop`: it ends the recording and gives the names."""
    import caldav

    seen: set[str] = set()
    orig = caldav.DAVClient.__init__

    def init(self, *a, **kw):
        orig(self, *a, **kw)
        user = getattr(self, "username", None) or ""
        if is_test_user(user) and _local(str(getattr(self, "url", "") or "")):
            seen.add(user)

    caldav.DAVClient.__init__ = init

    def stop() -> set[str]:
        caldav.DAVClient.__init__ = orig
        return set(seen)

    return stop
