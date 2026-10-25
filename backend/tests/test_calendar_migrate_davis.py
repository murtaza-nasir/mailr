# ACT-SURFACES: api
"""Moving from Davis into the mailr calendar, end to end (opt-in; calendar/migrate.py).

Needs a throwaway Stalwart and a throwaway Davis. Skipped unless all are set:

    MAILR_TEST_STALWART_URL=http://127.0.0.1:18995
    MAILR_TEST_STALWART_ADMIN=mailr-svc@example.test:<password>
    MAILR_TEST_DAVIS_URL=http://127.0.0.1:18998
    MAILR_TEST_DAVIS_ADMIN=admin:<password>
    MAILR_TEST_DAVIS_API_KEY=<key>

Never point it at a real Davis: it makes users there (tests/davis_seed.py) and edits an event.

Covered (calendar.migrate, calendar.migrate_cutover): seed Davis → dry run counts → copy → every
UID on the store, equal to the source under restore.normalized (the definition of "the same item":
DTSTAMP/PRODID/VERSION, SCHEDULE-* parameters, folding, part order and vCard 3 → 4 photo spelling
aside), resource names, colours and components kept → a second run copies nothing → an edit on
Davis is a conflict, `overwrite` replaces it → the mirror sees the copies → cut-over → Davis's own
data is unchanged apart from the test's edit.
"""

from __future__ import annotations

import os
import re
import sys
import uuid
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, text

from mailr.calendar import builtin, migrate, restore, sync

sys.path.insert(0, str(Path(__file__).parent))
import davis_seed

URL = os.environ.get("MAILR_TEST_STALWART_URL", "").rstrip("/")
ADMIN = os.environ.get("MAILR_TEST_STALWART_ADMIN", "")
DAVIS = os.environ.get("MAILR_TEST_DAVIS_URL", "").rstrip("/")
DAVIS_ADMIN = os.environ.get("MAILR_TEST_DAVIS_ADMIN", "")
DAVIS_KEY = os.environ.get("MAILR_TEST_DAVIS_API_KEY", "")
pytestmark = [
    pytest.mark.skipif(not (URL and ":" in ADMIN and DAVIS and ":" in DAVIS_ADMIN and DAVIS_KEY),
                       reason="set MAILR_TEST_STALWART_URL/_ADMIN and MAILR_TEST_DAVIS_URL/_ADMIN/_API_KEY"),
    pytest.mark.serial,  # points the process's store settings at another server
]
BULK = 230  # more than two pages of dav.MULTIGET_BATCH


@pytest_asyncio.fixture
async def store(monkeypatch, tmp_path):
    from mailr.config import get_settings
    from mailr.core import netguard
    from mailr.core import store as store_mod

    login, pw = ADMIN.split(":", 1)
    s = get_settings()
    monkeypatch.setattr(s, "stalwart_url", URL)
    monkeypatch.setattr(s, "stalwart_admin_user", login)
    monkeypatch.setattr(s, "stalwart_admin_password", pw)
    monkeypatch.setattr(s, "stalwart_domain", login.split("@", 1)[1])
    monkeypatch.setattr(s, "data_dir", str(tmp_path / "data"))
    monkeypatch.setattr(s, "backup_target", "")
    monkeypatch.setattr(store_mod, "_service", None)
    netguard._infra_hosts.cache_clear()
    monkeypatch.setattr(netguard, "_INFRA_CACHE", None)
    yield s
    # The network guard caches the infrastructure names and addresses: forget the test store's.
    netguard._infra_hosts.cache_clear()
    netguard._INFRA_CACHE = None
    if store_mod._service is not None:
        await store_mod._service.close()
    store_mod._service = None


@pytest.fixture(scope="module")
def seeded():
    admin, pw = DAVIS_ADMIN.split(":", 1)
    d = davis_seed.Davis(DAVIS, admin, pw, DAVIS_KEY)
    try:
        yield davis_seed.seed(d, bulk=BULK)
    finally:
        d.close()


def _source_url() -> str:
    """Davis's address for mailr. When both test servers sit on 127.0.0.1, mailr's network guard
    treats that address as its own store; "localhost" reaches Davis without that clash."""
    u = urlsplit(DAVIS)
    if u.hostname == "127.0.0.1" and urlsplit(URL).hostname == "127.0.0.1":
        return f"{u.scheme}://localhost:{u.port}/dav/"
    return DAVIS + "/dav/"


@pytest_asyncio.fixture
async def rosa(store, seeded):
    """A mailr user with the built-in calendar and her Davis account connected."""
    from mailr.core.crypto import connector_context, encrypt_for_user
    from mailr.core.users import bootstrap, create_user, delete_store_account
    from mailr.db import sessionmaker
    from mailr.models import Connector, User

    await bootstrap()
    async with sessionmaker()() as db:
        await builtin.set_instance_enabled(db, True, "everyone")
        await db.commit()
    async with sessionmaker()() as db:
        user = await create_user(db, f"rosa-{uuid.uuid4().hex[:6]}@example.com", "Rosa", password="test-password-123")
        await db.commit()
        target = await builtin.ensure_connector(db, user)
        src = Connector(user_id=user.id, kind="caldav", name="Davis", mode="mirror", data_class="normal",
                        config={"url": _source_url(), "username": seeded.user}, state={}, enabled=True,
                        status="new")
        db.add(src)
        await db.flush()
        u = await db.get(User, user.id)
        src.secrets_enc = encrypt_for_user(user.id, u.data_key_wrapped, {"password": seeded.password},
                                           context=connector_context(src.id))
        await db.commit()
        ids = (user.id, src.id, target.id)
    res = await sync.sync_connector(ids[2], full=True)  # the store's Calendar and Tasks
    assert res.error is None, res.error
    yield ids
    async with sessionmaker()() as db:
        await builtin.set_instance_enabled(db, False, "everyone")
        await db.commit()
        u = await db.get(User, ids[0])
    await delete_store_account(u)


def _davis_tokens(seeded) -> dict[str, str]:
    """The sync token of each of the user's collections on Davis."""
    body = ('<?xml version="1.0"?><D:propfind xmlns:D="DAV:"><D:prop><D:sync-token/></D:prop></D:propfind>')
    out = {}
    for home in (f"/dav/calendars/{seeded.user}/", f"/dav/addressbooks/{seeded.user}/"):
        r = httpx.request("PROPFIND", DAVIS + home, content=body, headers={"Depth": "1"},
                          auth=(seeded.user, seeded.password))
        for resp in re.findall(r"<d:response>(.*?)</d:response>", r.text, re.DOTALL):
            href = re.search(r"<d:href>([^<]+)</d:href>", resp)
            tok = re.search(r"<d:sync-token>([^<]*)</d:sync-token>", resp)
            if href and tok:
                out[href.group(1)] = tok.group(1)
    return out


async def _target_items(user_id: int, target_id: int) -> dict[str, dict[str, tuple[str, str]]]:
    """collection display name → uid → (resource name, text), read from the store."""
    from mailr.db import sessionmaker
    from mailr.models import Connector, User

    async with sessionmaker()() as db:
        conn = await db.get(Connector, target_id)
        user = await db.get(User, user_id)
    out: dict[str, dict[str, tuple[str, str]]] = {}
    cal_home, card_home = migrate._homes(conn)
    async with migrate.target_client(conn, user) as c:
        for info in await c.collections(cal_home):
            got = await c.multiget(info.href, list(await c.etags(info.href)))
            out[info.displayname] = {restore.backup.uid_of(t): (unquote(urlsplit(h).path.rsplit("/", 1)[1]), t)
                                     for h, _e, t in got}
        for info in await c.books(card_home):
            got = await c.multiget_cards(info.href, list(await c.etags(info.href)))
            out["card:" + info.displayname] = {restore.backup.uid_of(t): (unquote(urlsplit(h).path.rsplit("/", 1)[1]), t)
                                               for h, _e, t in got}
    return out


async def _source_items(seeded) -> dict[str, dict[str, tuple[str, str]]]:
    """Davis: collection display name → uid → (resource name, text), cards as vCard 4.0."""
    from mailr.contacts.carddav import CardDavClient

    out: dict[str, dict[str, tuple[str, str]]] = {}
    async with CardDavClient(_source_url(), seeded.user, seeded.password, private_ok=True) as c:
        cols, _ = await migrate.inventory_calendars(c, await c.home())
        for col in cols + await migrate.inventory_books(c):
            if col.shared_in:
                continue
            hrefs = list(await c.etags(col.source))
            got = await (c.multiget_cards(col.source, hrefs, version="4.0") if col.kind == "addressbook"
                         else c.multiget(col.source, hrefs))
            key = ("card:" if col.kind == "addressbook" else "") + col.name
            out[key] = {restore.backup.uid_of(t): (unquote(urlsplit(h).path.rsplit("/", 1)[1]), t) for h, _e, t in got}
    return out


async def test_davis_to_the_mailr_calendar(rosa, seeded):
    """calendar.migrate end to end, then calendar.migrate_cutover."""
    from mailr.calendar import client as cal
    from mailr.db import sessionmaker
    from mailr.models import CalCollection, User

    user_id, src_id, target_id = rosa
    tokens_before = _davis_tokens(seeded)
    expected = sum(len(v) for v in seeded.calendars.values()) + 1 + sum(len(v) for v in seeded.books.values())
    # +1: kai's invitation, which Davis put into rosa's default calendar (server-side scheduling).

    dry = await migrate.migrate(user_id, src_id, dry_run=True)
    t = dry.totals()
    assert not dry.errors, dry.errors
    assert (t["collections"], t["created_collections"], t["total"], t["copied"], t["skipped"], t["conflicts"],
            t["failed"]) == (7, 7, expected, expected, 0, 0, 0), t
    from mailr.cli import print_migration

    print_migration(dry)  # the admin command's report (visible with -s)
    kinds = {x["kind"] for x in dry.not_migrated}
    assert kinds >= {"subscription", "scheduling", "delegate", "share", "shared_with_you"}, dry.not_migrated
    delegate = next(x for x in dry.not_migrated if x["kind"] == "delegate")
    assert seeded.delegate in delegate["name"] and "can edit" in delegate["detail"]
    assert next(x for x in dry.not_migrated if x["kind"] == "shared_with_you")["name"] == "Team"
    assert seeded.delegate in next(x for x in dry.not_migrated if x["kind"] == "share")["detail"]
    async with sessionmaker()() as db:
        assert (await db.execute(text("SELECT count(*) FROM cal_collections WHERE connector_id=:c AND "
                                      "deleted_at IS NULL"), {"c": target_id})).scalar() == 2  # Calendar, Tasks
    assert _davis_tokens(seeded) == tokens_before

    seen: list[tuple[int, int]] = []

    async def progress(done: int, total: int, name: str) -> None:
        seen.append((done, total))

    rep = await migrate.migrate(user_id, src_id, progress=progress)
    t = rep.totals()
    assert not rep.errors, rep.errors
    assert (t["total"], t["copied"], t["skipped"], t["conflicts"], t["failed"]) == (expected, expected, 0, 0, 0), t
    assert seen[0] == (0, expected) and seen[-1] == (expected, expected)
    print_migration(rep)
    assert len({d for d, _ in seen}) > 4  # page by page
    await migrate.record(user_id, rep)  # as the admin command does: the next run finds the same targets

    # Every UID is on the store, equal to Davis's under restore.normalized, under its resource name.
    src = await _source_items(seeded)
    dst = await _target_items(user_id, target_id)
    assert set(src) <= set(dst), (set(src), set(dst))
    for name, items in src.items():
        assert set(items) == set(dst[name]), name
        for uid, (rname, body) in items.items():
            assert restore.normalized(dst[name][uid][1]) == restore.normalized(body), (name, uid)
            assert dst[name][uid][0] == rname, (name, uid)
    series = next(b for u, (_n, b) in dst["Default Calendar"].items() if u.startswith("series-"))
    assert series.count("RECURRENCE-ID") == 2 and "BEGIN:VTIMEZONE" in series and "EXDATE" in series
    assert "RELATED-TO;RELTYPE=PARENT" in next(b for u, (_n, b) in dst["Chores"].items() if "-sub1-" in u)
    assert "ATTACH" in next(b for u, (_n, b) in dst["Default Calendar"].items() if u.startswith("dentist-"))
    photo = next(b for u, (_n, b) in dst["card:Default Address Book"].items() if u.startswith("card-v3-"))
    assert "PHOTO" in photo
    by_name = {c.name: c for c in rep.collections}
    assert by_name["Work"].color == "#3F51B5" and by_name["Chores"].components == ["VTODO"]
    async with migrate.target_client(*(await _conn_user(target_id, user_id))) as c:
        infos = {i.displayname: i for i in await c.collections(migrate._homes((await _conn_user(target_id, user_id))[0])[0])}
    assert infos["Work"].color == "#3F51B5" and infos["Chores"].components == ["VTODO"]
    assert infos["Notes"].components == ["VJOURNAL"]

    # Twice: nothing new.
    again = await migrate.migrate(user_id, src_id)
    t = again.totals()
    assert (t["copied"], t["skipped"], t["conflicts"], t["failed"], t["created_collections"]) == (0, expected, 0, 0, 0)
    assert {c.action for c in again.collections if c.action != "skipped"} == {"previous"}

    # An edit on Davis: a conflict, kept; with overwrite, the Davis version.
    tag = seeded.user.split("-", 1)[1]
    dentist = f"dentist-{tag}@northwind.example"
    body = seeded.calendars["default"][dentist].replace("SUMMARY:Dentist", "SUMMARY:Dentist (moved to 16:00)")
    async with httpx.AsyncClient(auth=(seeded.user, seeded.password)) as h:
        r = await h.put(f"{DAVIS}/dav/calendars/{seeded.user}/default/{dentist}.ics", content=body.encode(),
                        headers={"Content-Type": "text/calendar"})
    assert r.status_code == 204, r.text
    third = await migrate.migrate(user_id, src_id)
    assert third.totals()["conflicts"] == 1
    assert by(third, "Default Calendar").as_dict()["conflict_uids"] == [dentist]
    assert "moved to 16:00" not in (await _target_items(user_id, target_id))["Default Calendar"][dentist][1]
    over = await migrate.migrate(user_id, src_id, overwrite=True)
    assert (over.totals()["replaced"], over.totals()["conflicts"]) == (1, 0)
    assert "moved to 16:00" in (await _target_items(user_id, target_id))["Default Calendar"][dentist][1]

    # The mirror sees the copies.
    res = await sync.sync_connector(target_id, full=True)
    assert res.error is None, res.error
    async with sessionmaker()() as db:
        n = (await db.execute(text("SELECT count(*) FROM cal_objects WHERE connector_id=:c AND deleted_at IS NULL"),
                              {"c": target_id})).scalar()
    # Journal entries are copied, the mirror keeps events and tasks only.
    assert n == expected - sum(len(v) for v in seeded.books.values()) - len(seeded.calendars["notes"]), n

    # Cut-over: the source's default calendar and task list were the defaults; now their copies are.
    res = await sync.sync_connector(src_id, full=True)
    assert res.error is None, res.error
    async with sessionmaker()() as db:
        u = await db.get(User, user_id)
        cols = await cal.collections(db, user_id)
        src_default = next(c["url"] for c in cols if c["connector_id"] == src_id and c["url"].endswith("/default/"))
        src_chores = next(c["url"] for c in cols if c["connector_id"] == src_id and c["url"].endswith("/chores/"))
        await cal.set_defaults(db, u, src_default, src_chores)
        await db.commit()
    out = await migrate.cutover(user_id, src_id, make_default=True, hide_source=True)
    assert out["defaults"]["calendar"] == by(over, "Default Calendar").target
    assert out["defaults"]["task_list"] == by(over, "Chores").target
    assert out["hidden"] == 5
    async with sessionmaker()() as db:
        hidden = (await db.execute(select(CalCollection.hidden).where(CalCollection.connector_id == src_id,
                                                                       CalCollection.deleted_at.is_(None)))).scalars().all()
    assert hidden.count(True) == 5 and hidden.count(False) == 1  # Team (shared with rosa) stays shown

    # Davis is unchanged apart from the test's own edit.
    after = _davis_tokens(seeded)
    changed = {h for h in after if after[h] != tokens_before.get(h)}
    assert changed == {f"/dav/calendars/{seeded.user}/default/"}, changed


def by(rep: migrate.Report, name: str) -> migrate.Collection:
    return next(c for c in rep.collections if c.name == name)


async def _conn_user(target_id: int, user_id: int):
    from mailr.db import sessionmaker
    from mailr.models import Connector, User

    async with sessionmaker()() as db:
        return await db.get(Connector, target_id), await db.get(User, user_id)
