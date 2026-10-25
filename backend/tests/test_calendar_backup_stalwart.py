"""Calendar backups end to end against a real Stalwart.

The module runs against the suite's own store server, the dev Stalwart of
deploy/dev/docker-compose.yml, unless MAILR_TEST_STALWART_URL and MAILR_TEST_STALWART_ADMIN name
another throwaway Stalwart (tests/dev_services.py). It is never pointed at production. MAILR_TEST_WITHOUT_STALWART_DAV=1 skips it.

For the length of the test mailr's store settings point at that server. The test deletes its
accounts at the end.

Covered: events (a recurring one with an override and a time zone), a task, a second calendar, a
card and a group calendar on the store → the mirror → the export → the collections and items wiped
on the store → `restore` (a dry run first) → the forced resync → the mirror matches what it held
before; a second restore creates nothing.
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from dev_services import stalwart_dav_skip_reason
from sqlalchemy import select, text

from mailr.calendar import backup, builtin, groups, restore

URL = os.environ.get("MAILR_TEST_STALWART_URL", "").rstrip("/")
ADMIN = os.environ.get("MAILR_TEST_STALWART_ADMIN", "")
pytestmark = [
    pytest.mark.skipif(not (URL and ":" in ADMIN), reason=stalwart_dav_skip_reason()),
    pytest.mark.serial,  # points the process's store settings at another server
]

TZ = """BEGIN:VTIMEZONE\r
TZID:Europe/Berlin\r
BEGIN:STANDARD\r
DTSTART:19701025T030000\r
TZOFFSETFROM:+0200\r
TZOFFSETTO:+0100\r
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU\r
END:STANDARD\r
BEGIN:DAYLIGHT\r
DTSTART:19700329T020000\r
TZOFFSETFROM:+0100\r
TZOFFSETTO:+0200\r
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU\r
END:DAYLIGHT\r
END:VTIMEZONE\r
"""


def _cal(body: str) -> str:
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\n{TZ}{body}END:VCALENDAR\r\n"


def _ev(uid: str, summary: str, extra: str = "") -> str:
    return (f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:20261001T100000Z\r\nSUMMARY:{summary}\r\n"
            "DTSTART;TZID=Europe/Berlin:20261012T090000\r\nDTEND;TZID=Europe/Berlin:20261012T100000\r\n"
            f"{extra}END:VEVENT\r\n")


@pytest_asyncio.fixture
async def store(monkeypatch, tmp_path):
    from mailr.config import get_settings
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
    yield s
    if store_mod._service is not None:
        await store_mod._service.close()
    store_mod._service = None


@pytest_asyncio.fixture
async def people(store):
    from mailr.core.users import bootstrap, create_user, delete_store_account
    from mailr.db import sessionmaker
    from mailr.models import User

    await bootstrap()
    async with sessionmaker()() as db:
        await builtin.set_instance_enabled(db, True, "everyone")
        await db.commit()
    group = f"Crew {uuid.uuid4().hex[:6]}"
    users = []
    async with sessionmaker()() as db:
        for short in ("ana", "ben"):
            users.append(await create_user(db, f"{short}-{uuid.uuid4().hex[:6]}@example.com", short.title(),
                                           password="test-password-123"))
        for u in users:
            row = await db.get(User, u.id)
            row.assigned_groups = [group]
        await db.commit()
    yield group, users
    async with sessionmaker()() as db:
        from mailr.models import GroupCalendar

        for gc in (await db.execute(select(GroupCalendar).where(GroupCalendar.group_name == group))).scalars():
            try:
                await groups.delete(db, gc)
            except Exception:  # noqa: BLE001  best effort
                pass
        await db.commit()
        await groups.reconcile()
        await builtin.set_instance_enabled(db, False, "everyone")
        await db.commit()
    for u in users:
        await delete_store_account(u)


async def _sync(user_id: int) -> int:
    from mailr.calendar import sync
    from mailr.contacts import carddav
    from mailr.db import sessionmaker

    async with sessionmaker()() as db:
        conn = await builtin.find(db, user_id)
    res = await sync.sync_connector(conn.id, full=True)
    assert res.error is None, res.error
    st = await carddav.sync_connector(conn.id)
    assert st.get("available"), st
    return conn.id


async def _mirror(user_id: int) -> dict[tuple[str, str], tuple[str, ...]]:
    """(collection name, uid) → the normalized resource, of the user's built-in connector."""
    from mailr.db import sessionmaker

    async with sessionmaker()() as db:
        rows = (await db.execute(text(
            "SELECT c.displayname, o.uid, o.ics FROM cal_objects o JOIN cal_collections c ON c.id = o.collection_id "
            "JOIN connectors k ON k.id = o.connector_id WHERE k.builtin AND o.user_id = :u AND o.deleted_at IS NULL "
            "AND c.deleted_at IS NULL"), {"u": user_id})).all()
        cards = (await db.execute(text(
            "SELECT b.displayname, c.uid, c.vcard FROM contact_cards c JOIN address_books b ON b.id = c.book_id "
            "JOIN connectors k ON k.id = c.connector_id WHERE k.builtin AND c.user_id = :u"), {"u": user_id})).all()
    out = {(n, u): restore.normalized(t) for n, u, t in rows}
    out.update({("card:" + n, u): restore.normalized(t or "") for n, u, t in cards})
    return out


async def test_export_wipe_restore_resync(people):
    from mailr.contacts import carddav
    from mailr.db import sessionmaker
    from mailr.models import User

    group, (ana, ben) = people
    for u in (ana, ben):
        await _sync(u.id)
    async with sessionmaker()() as db:
        conn = await builtin.find(db, ana.id)
        user = await db.get(User, ana.id)
        gc = await groups.create(db, group, "Crew rota", "#2E7D32", "write")
        await db.commit()
        gc_href = gc.href
    acct = conn.config["account"]
    home = URL + groups.home_of(acct)
    card_home = URL + f"/dav/card/{acct.replace('@', '%40')}/"

    async with carddav.client_for(conn, user) as c:
        cal = home + "default/"
        await c.put(cal + "standup.ics", _cal(_ev("bk-standup", "Standup", "RRULE:FREQ=WEEKLY;COUNT=6\r\n")
                                             + _ev("bk-standup", "Standup (moved)",
                                                   "RECURRENCE-ID;TZID=Europe/Berlin:20261019T090000\r\n")),
                    if_none_match=True)
        await c.put(cal + "review.ics", _cal(_ev("bk-review", "Review")), if_none_match=True)
        await c.put(home + "tasks/" + "t1.ics", "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//EN\r\nBEGIN:VTODO\r\n"
                    "UID:bk-task\r\nDTSTAMP:20261001T100000Z\r\nSUMMARY:Order chairs\r\nEND:VTODO\r\nEND:VCALENDAR\r\n",
                    if_none_match=True)
        proj = await c.mkcalendar(home, "projects-bk", "Projects", ["VEVENT"], "#AA3300")
        await c.put(proj + "kick.ics", _cal(_ev("bk-kickoff", "Kickoff")), if_none_match=True)
        r = await c.request("PUT", card_home + "default/ada.vcf",
                            body=b"BEGIN:VCARD\r\nVERSION:4.0\r\nUID:bk-ada\r\nFN:Ada Lovelace\r\n"
                                 b"EMAIL:ada@northwind.example\r\nEND:VCARD\r\n",
                            headers={"Content-Type": "text/vcard; charset=utf-8", "If-None-Match": "*"})
        assert r.status_code in (201, 204), r.status_code
    async with groups.admin_client() as c:
        await c.put(URL + gc_href + "rota.ics", _cal(_ev("bk-rota", "Rota")), if_none_match=True)
    for u in (ana, ben):
        await _sync(u.id)
    before = await _mirror(ana.id)
    assert {k[1] for k in before} >= {"bk-standup", "bk-review", "bk-task", "bk-kickoff", "bk-ada", "bk-rota"}

    await backup.export_user(ana.id)
    await backup.export_groups()

    # The loss: the second calendar goes, the other items and the card are deleted, the group
    # calendar is emptied.
    async with carddav.client_for(conn, user) as c:
        await c.delete_collection(proj)
        for h in ("default/standup.ics", "default/review.ics", "tasks/t1.ics"):
            await c.delete(home + h)
        await c.delete(card_home + "default/ada.vcf")
    async with groups.admin_client() as c:
        await c.delete(URL + gc_href + "rota.ics")

    dry = await restore.restore(backup.root(), dry_run=True)
    assert not dry.errors, dry.errors
    assert dry.total.collections_created == 1 and dry.total.created == 6, dry.total

    rep = await restore.restore(backup.root())
    assert not rep.errors, rep.errors
    assert (rep.total.collections_created, rep.total.created, rep.total.conflicts) == (1, 6, 0), rep.total
    assert conn.id in rep.connectors
    async with sessionmaker()() as db:
        toks = (await db.execute(text("SELECT count(*) FROM cal_collections WHERE connector_id = :c AND "
                                      "sync_token IS NOT NULL"), {"c": conn.id})).scalar()
    assert toks == 0  # the forced resync cleared them

    # The worker would run the queued full syncs; run them here.
    for u in (ana, ben):
        await _sync(u.id)
    after = await _mirror(ana.id)
    assert after == before

    again = await restore.restore(backup.root())
    assert not again.errors and again.total.created == 0 and again.total.conflicts == 0, again.total
    assert again.total.skipped == 6


async def _gc(name: str):
    from mailr.db import sessionmaker
    from mailr.models import GroupCalendar

    async with sessionmaker()() as db:
        return (await db.execute(select(GroupCalendar).where(GroupCalendar.name == name))).scalars().first()


async def test_group_restore_makes_lost_group_calendars_again(people):
    """The group part of a restore (restore._group_target, _restore_groups): a read-only group
    calendar whose collection is gone from the store gets a new one (with its ACL); a group
    calendar mailr no longer knows is made again with its editors (matched by email); a dry run
    changes nothing; built-in calendars off and an unreadable export file are reported per
    calendar, not raised."""
    from mailr.db import sessionmaker
    from mailr.models import CalendarGroup, GroupCalendar

    group, (ana, ben) = people
    for u in (ana, ben):
        await _sync(u.id)
    async with sessionmaker()() as db:
        rota = await groups.create(db, group, "Crew rota", "#2E7D32", "write")
        notices = await groups.create(db, group, "Crew notices", "#1565C0", "read", [ana.id])
        board = await groups.create(db, group, "Crew board", "#AA3300", "read", [ana.id])
        await db.commit()
        rota_href, notices_href, board_href = rota.href, notices.href, board.href
    async with groups.admin_client() as c:
        await c.put(URL + rota_href + "r.ics", _cal(_ev("gr-rota", "Rota")), if_none_match=True)
    for href, uid in ((notices_href, "gr-notice"), (board_href, "gr-board")):
        async with sessionmaker()() as db:
            cg = await db.get(CalendarGroup, group)
        async with groups.owner_client(cg.owner_login) as c:
            await c.put(URL + href + "n.ics", _cal(_ev(uid, "Notice")), if_none_match=True)
    await backup.export_groups()

    async def acl(href: str) -> list:
        from mailr.calendar import sharing

        async with sessionmaker()() as db:
            cg = await db.get(CalendarGroup, group)
        return sorted((a.login, sorted(a.privs)) for a in await sharing._read(cg.owner_login, href))

    acl_before = await acl(notices_href)
    assert len(acl_before) == 2 and await acl(board_href) == acl_before  # Ana edits, the group reads

    # The loss: the notices collection is deleted on the store; the rota and board calendars are
    # deleted in mailr (their rows and their collections).
    async with groups.owner_client(cg.owner_login) as c:
        await c.delete_collection(URL + notices_href)
    async with sessionmaker()() as db:
        await groups.delete(db, await db.get(GroupCalendar, rota.id))
        await groups.delete(db, await db.get(GroupCalendar, board.id))
        await db.commit()
    await groups.reconcile()

    dry = await restore.restore(backup.root(), groups_only=True, dry_run=True)
    assert not dry.errors, dry.errors
    assert (dry.total.collections_created, dry.total.created) == (3, 3), dry.total
    assert await _gc("Crew rota") is None  # the dry run made nothing

    rep = await restore.restore(backup.root(), groups_only=True)
    assert not rep.errors, rep.errors
    assert (rep.total.collections_created, rep.total.created, rep.total.failed) == (3, 3, 0), rep.total
    rota2, notices2, board2 = await _gc("Crew rota"), await _gc("Crew notices"), await _gc("Crew board")
    assert rota2 is not None and rota2.id != rota.id and rota2.access == "write"
    assert notices2.id == notices.id and notices2.href and notices2.editors == [ana.id]
    assert await acl(notices2.href) == acl_before  # the read-only calendar's ACL is written again
    # The board is made again with its editor, found by email.
    assert board2.id != board.id and board2.access == "read" and board2.editors == [ana.id]
    assert await acl(board2.href) == acl_before
    async with groups.admin_client() as c:
        assert len(await c.etags(URL + rota2.href)) == 1
    async with groups.owner_client(cg.owner_login) as c:
        assert len(await c.etags(URL + notices2.href)) == 1 and len(await c.etags(URL + board2.href)) == 1
    # Both members' built-in connectors are resynced.
    async with sessionmaker()() as db:
        conns = {(await builtin.find(db, u.id)).id for u in (ana, ben)}
    assert conns <= rep.connectors
    # A second run finds both and creates nothing.
    again = await restore.restore(backup.root(), groups_only=True)
    assert not again.errors and again.total.collections_existing == 3 and again.total.created == 0, again.total

    # Problems are reported per calendar: built-in calendars off, then a damaged export file.
    async with sessionmaker()() as db:
        await builtin.set_instance_enabled(db, False, "everyone")
        await db.commit()
    off = await restore.restore(backup.root(), groups_only=True)
    assert off.total.failed == 3 and all("built-in calendars are off" in e for e in off.errors), off.errors
    async with sessionmaker()() as db:
        await builtin.set_instance_enabled(db, True, "everyone")
        await db.commit()
    snap = restore.find_snapshots(backup.root())
    files = [f for s in snap for f in s.rglob("*.ics")]
    assert files
    for f in files:
        f.unlink()
    broken = await restore.restore(backup.root(), groups_only=True)
    assert broken.total.failed == 3 and len(broken.errors) == 3, broken.errors
