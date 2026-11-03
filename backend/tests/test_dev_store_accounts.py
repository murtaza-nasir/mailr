"""Test accounts in the shared dev mail stores (mailr/dev/store_accounts.py): the target guard, the
test mark, what the sweep may destroy, and the per-run release against the real dev store."""

import asyncio
import os
import time
import uuid

import httpx
import pytest

from mailr.dev import store_accounts as sa


@pytest.mark.parametrize("url", ["https://mail.example.com", "http://mail-store:8080", "http://10.0.0.5:18080",
                                 "http://stalwart:8080", "https://localhost.evil.example:18080", ""])
def test_a_store_that_is_not_the_dev_store_is_refused(url, monkeypatch):
    def no_request(*_a, **_kw):
        raise AssertionError("a request was made")

    monkeypatch.setattr(httpx.Client, "send", no_request)
    for dev in (True, False):
        with pytest.raises(sa.RefusedTarget):
            sa.check_target(url, dev=dev)
        with pytest.raises(sa.RefusedTarget):
            sa.Store(url, ("u", "p"), dev=dev)
        with pytest.raises(sa.RefusedTarget):
            sa.sweep(url, dev=dev)
    with pytest.raises(RuntimeError, match="RefusedTarget"):
        sa.release([(url, "a", "name")])


def test_only_the_dev_urls_or_a_loopback_url_in_dev_mode_are_accepted():
    for url in ("http://localhost:18080", "http://127.0.0.1:18080/", "http://localhost:18091"):
        assert sa.check_target(url, dev=False) == url.rstrip("/")
    assert sa.check_target("http://127.0.0.1:18995", dev=True) == "http://127.0.0.1:18995"
    with pytest.raises(sa.RefusedTarget):
        sa.check_target("http://127.0.0.1:18995", dev=False)


def test_the_sweep_command_refuses_a_production_store(monkeypatch, capsys):
    from mailr.config import get_settings

    monkeypatch.setenv("MAILR_STALWART_URL", "https://mail.example.com")
    get_settings.cache_clear()
    monkeypatch.setattr("sys.argv", ["store_accounts", "sweep", "--store", "dev"])
    try:
        with pytest.raises(SystemExit) as e:
            sa.main()
    finally:
        get_settings.cache_clear()
    assert e.value.code == 3 and "refusing" in capsys.readouterr().err


class _Fake:
    url = sa.DEV_STORE

    def service_name(self):
        return "mailr-svc"


def test_what_counts_as_a_test_account():
    st = _Fake()
    marked = {"name": "anything-at-all", "externalId": "mailr-test:pytest-1", "roles": {"@type": "User"}}
    assert sa.is_test_account(st, marked)
    for name in ("t-1a2b3c4d-12-abcdef", "e2e-6505ced5-1-fbd34e", "zed-6cf9f7-83-47dd6d", "p0t-alice-0a1b2c"):
        assert sa.is_test_account(st, {"name": name, "roles": {"@type": "User"}}), name
    for name in ("robin-1-abc123", "alex-rivera-3-0a1b2c", "mailr-svc", "admin", "family-2-abcdef"):
        assert not sa.is_test_account(st, {"name": name, "roles": {"@type": "User"}}), name
    # Admins and the service account, even with the mark.
    assert not sa.is_test_account(st, {**marked, "roles": {"@type": "Admin"}})
    assert not sa.is_test_account(st, {**marked, "name": "mailr-svc"})
    st.url = sa.UPSTREAM
    assert sa.is_test_account(st, {"name": "alice" + "0" * 16, "roles": {"@type": "User"}})
    assert sa.is_test_account(st, {"name": "rosa0a1b2c", "roles": {"@type": "User"}})
    for name in ("demo-work", "up-svc", "qauser1", "smoke1234abcd"):
        assert not sa.is_test_account(st, {"name": name, "roles": {"@type": "User"}}), name


def test_the_mark_needs_dev_mode_and_a_run(monkeypatch):
    from mailr.config import get_settings

    assert os.environ.get(sa.MARK_ENV)  # conftest sets it for the whole session
    assert sa.mark()["externalId"].startswith(sa.MARK_PREFIX)
    monkeypatch.setenv("MAILR_DEV", "false")
    get_settings.cache_clear()
    try:
        assert sa.mark() == {} and sa.run_tag() is None
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    monkeypatch.delenv(sa.MARK_ENV)
    assert sa.mark() == {}


def _app_store() -> str:
    """The store the app makes its accounts in: the dev store, or the throwaway store a run names in
    MAILR_STALWART_URL (a loopback one is allowed while MAILR_DEV is set)."""
    from mailr.config import get_settings

    return get_settings().stalwart_url


async def test_a_test_user_is_marked_noted_and_released(app, tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setenv(sa.LEDGER_ENV, str(ledger))
    from mailr.core.users import create_user
    from mailr.db import session_scope

    async with session_scope() as db:
        u = await create_user(db, f"t-{uuid.uuid4().hex[:8]}@example.com", "Noted")
    entry = next(e for e in sa.noted() if e[1] == u.stalwart_account_id)
    assert entry in sa.read_ledger(str(ledger))
    with sa.Store(_app_store()) as st:
        (acct,) = st.get([u.stalwart_account_id])
        assert acct["externalId"] == f"{sa.MARK_PREFIX}{os.environ[sa.MARK_ENV]}"
        assert sa.is_test_account(st, acct)
    # A noted id that now names another account is left alone.
    assert sa.release([(entry[0], entry[1], entry[2] + "-other")]) == {entry[0]: 0}
    assert sa.release_ledger(str(ledger)) == {entry[0]: 1} and not ledger.exists()
    with sa.Store(_app_store()) as st:
        assert st.get([u.stalwart_account_id]) == []


async def test_release_db_takes_only_the_accounts_its_rows_name(app):
    from mailr.config import get_settings
    from mailr.core.users import create_user
    from mailr.db import session_scope

    url = get_settings().database_url
    since = int(time.time()) + 1
    await asyncio.sleep(since - time.time())  # createdAt counts whole seconds
    async with session_scope() as db:
        u = await create_user(db, f"t-{uuid.uuid4().hex[:8]}@example.com", "Released")
    with sa.Store(_app_store()) as st:
        assert st.get([u.stalwart_account_id])
    # Only accounts made since `since`: this one. Nothing made after an hour from now.
    assert await sa.release_db_async(url, since=time.time() + 3600, dry_run=True) == 0
    assert await sa.release_db_async(url, since=since, dry_run=True) == 1
    assert await sa.release_db_async(url, since=since) == 1
    with sa.Store(_app_store()) as st:
        assert st.get([u.stalwart_account_id]) == []


def test_the_sweep_leaves_young_accounts_alone():
    _, _, due = sa.sweep(sa.DEV_STORE, min_age_hours=10_000, dry_run=True)
    assert due == 0


def test_a_group_is_destroyed_after_its_members():
    """The store refuses to destroy a group while it has a member (objectIsLinked)."""
    tag = uuid.uuid4().hex[:6]
    with sa.Store(sa.DEV_STORE) as st:
        dom = st.call("x:Domain/query", {"filter": {"name": "mailr.internal"}})["ids"][0]
        g = st.call("x:Account/set", {"create": {"g": {"@type": "Group", "name": f"grp-t{tag}", "domainId": dom,
                                                       "roles": {"@type": "Custom", "roleIds": {}}, **sa.mark()}}})
        gid = g["created"]["g"]["id"]
        u = st.call("x:Account/set", {"create": {"u": {
            "@type": "User", "name": f"t-{tag}00-0-{tag}", "domainId": dom, "memberGroupIds": {gid: True},
            "credentials": {"0": {"@type": "Password", "secret": f"Zq8-{uuid.uuid4().hex}"}}, **sa.mark()}}})
        uid = u["created"]["u"]["id"]
    assert sa.release([(sa.DEV_STORE, gid, f"grp-t{tag}"), (sa.DEV_STORE, uid, f"t-{tag}00-0-{tag}")]) == {sa.DEV_STORE: 2}
