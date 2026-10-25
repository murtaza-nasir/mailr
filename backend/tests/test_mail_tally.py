# ACT-SURFACES: api
"""The mail tally (core/tally.py): the Subscriptions and Clean up pages answer from it with the
same groups and totals as the live aggregates over the messages, for every scope, before and after
the changes that move mail between its rows; the upkeep is exact under concurrent changes; the
nightly check moves the anchor and finds nothing to correct; a rebuild runs in batches."""

import asyncio
import itertools
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from helpers.mailgroups import LIST, _ago, _ingest, _role, _set
from sqlalchemy import select, text

from mailr.core import mailgroups as mg
from mailr.core import tally
from mailr.db import sessionmaker
from mailr.models import Conversation, Message

SHOP = "List-Unsubscribe: <mailto:unsub@shop.example>"
ALERTS = "List-Unsubscribe: <https://alerts.example/u>\r\nList-Unsubscribe-Post: List-Unsubscribe=One-Click"
FLAGS = [dict(zip(("include_starred", "include_important", "include_replied"), v, strict=True))
         for v in itertools.product((False, True), repeat=3)]


def _p(user, token=None):
    return SimpleNamespace(user_id=user.id, token=token, is_token=token is not None)


async def _box(user, raw):
    """Mail around every age cutoff, lists and bulk senders, a conversation of two senders, one with
    mail in two age bands, starred, important and replied conversations, Inbox, Archive, Trash and
    Spam, categories, read mail, a local-only message, one without a date and one without a
    conversation."""
    ids: dict[str, tuple[int, int]] = {}
    for i, d in enumerate((1, 31, 50, 91, 200, 400, 800, 2000)):
        ids[f"news{i}"] = await _ingest(user, raw(f"Digest {i}", frm="Weekly <digest@news.example>",
                                                  to=user.email, date=_ago(d), extra=LIST))
    for i, d in enumerate((2, 29, 181, 366, 731, 1826)):
        ids[f"shop{i}"] = await _ingest(user, raw(f"Sale {i}", frm="Shop <deals@shop.example>", to=user.email,
                                                  date=_ago(d), extra=SHOP))
    for i, d in enumerate((10, 100, 181.5)):
        ids[f"alert{i}"] = await _ingest(user, raw(f"Alert {i}", frm="CI <noreply@alerts.example>",
                                                   to=user.email, date=_ago(d), extra=ALERTS))
    for i, (frm, d) in enumerate((("Offers <offers@news.shop.co.uk>", 45), ("Info <info@shop.co.uk>", 365.5))):
        ids[f"uk{i}"] = await _ingest(user, raw(f"Offer {i}", frm=frm, to=user.email, date=_ago(d), extra=SHOP))
    # A conversation of two senders, which the user answered.
    ids["ann1"] = await _ingest(user, raw("Trip", frm="Ann <ann@friends.example>", to=user.email,
                                          msgid=f"<trip1-{user.id}@friends.example>", date=_ago(40)))
    ids["bob1"] = await _ingest(user, raw("Re: Trip", frm="Bob <bob@friends.example>", to=user.email,
                                          msgid=f"<trip2-{user.id}@friends.example>",
                                          irt=f"<trip1-{user.id}@friends.example>", date=_ago(35)))
    ids["ann2"] = await _ingest(user, raw("Re: Trip", frm="Ann <ann@friends.example>", to=user.email,
                                          msgid=f"<trip3-{user.id}@friends.example>",
                                          irt=f"<trip2-{user.id}@friends.example>", date=_ago(31)))
    await _ingest(user, raw("Re: Trip", frm=f"Me <{user.email}>", to="ann@friends.example",
                            irt=f"<trip3-{user.id}@friends.example>", date=_ago(30.5)), source="send", roles=["sent"])
    # One sender, one conversation, mail on both sides of the 30-day edge.
    ids["carl1"] = await _ingest(user, raw("Plan", frm="Carl <carl@work.example>", to=user.email,
                                           msgid=f"<plan1-{user.id}@work.example>", date=_ago(33)))
    ids["carl2"] = await _ingest(user, raw("Re: Plan", frm="Carl <carl@work.example>", to=user.email,
                                           msgid=f"<plan2-{user.id}@work.example>",
                                           irt=f"<plan1-{user.id}@work.example>", date=_ago(25)))
    ids["dora"] = await _ingest(user, raw("Hello", frm="Dora <dora@work.example>", to=user.email, date=_ago(400)))
    ids["eve"] = await _ingest(user, raw("Spam", frm="Eve <eve@spam.example>", to=user.email, date=_ago(5)))
    ids["nodate"] = await _ingest(user, raw("Undated", frm="Weekly <digest@news.example>", to=user.email,
                                            date=_ago(3), extra=LIST))
    ids["loose"] = await _ingest(user, raw("Loose", frm="Deals <deals@shop.example>", to=user.email,
                                           date=_ago(60), extra=SHOP))
    archive, trash, junk = [await _role(user, r) for r in ("archive", "trash", "junk")]
    for k in ("news3", "news4", "news5", "news6", "news7", "shop2", "shop3", "shop4", "shop5", "alert1", "uk1",
              "dora", "carl1"):
        await _set(Message, ids[k][0], label_ids=[archive])
    for k in ("news2", "shop1"):
        await _set(Message, ids[k][0], label_ids=[trash])
    await _set(Message, ids["eve"][0], label_ids=[junk])
    await _set(Message, ids["news6"][0], category="promotions")
    await _set(Message, ids["shop3"][0], category="promotions")
    await _set(Message, ids["alert0"][0], category="updates")
    for k in ("news0", "shop4", "alert1", "dora"):
        await _set(Message, ids[k][0], seen=True)
    await _set(Message, ids["shop5"][0], local_only=True)
    await _set(Message, ids["nodate"][0], date=None)
    await _set(Conversation, ids["news5"][1], starred=True)
    await _set(Conversation, ids["shop3"][1], important=True)
    await _set(Message, ids["loose"][0], conversation_id=None)
    return ids


def _diff(a: dict, b: dict) -> list:
    ia, ib = {x["key"]: x for x in a["items"]}, {x["key"]: x for x in b["items"]}
    return [(k, {f: (ia.get(k, {}).get(f), ib.get(k, {}).get(f)) for f in set(ia.get(k, {})) | set(ib.get(k, {}))
                 if ia.get(k, {}).get(f) != ib.get(k, {}).get(f)}) for k in set(ia) | set(ib) if ia.get(k) != ib.get(k)]


async def _compare(user, *, full: bool = True, token=None, tiny: bool = False) -> int:
    """Every scope of the Clean up page and every list of the Subscriptions page, from the tally
    and live: the same. Returns how many were compared."""
    p = _p(user, token)
    n = 0
    days_set = (0, *tally.BANDS) if full else (0, 30) if tiny else (0, 30, 365)
    variants = [(False, False, fl) for fl in (FLAGS if full else FLAGS[:1] if tiny else (FLAGS[0], FLAGS[-1]))] + \
        [(u, b, FLAGS[0]) for u, b in ((True, False), (False, True), (True, True))][0 if full else 2:]
    async with sessionmaker()() as db:
        for days in days_set:
            assert await mg._tally_anchor(db, p, days) is not None
            for kind, where, (uncat, bulk, fl) in itertools.product(mg.KINDS, mg.WHERES, variants):
                sc = mg.Scope(where=where, older_than_days=days, uncategorised=uncat, bulk_only=bulk, **fl)
                for q in ("", "shop") if (not uncat and not bulk and fl == FLAGS[0]) else ("",):
                    a = await mg.groups(db, p, kind, sc, q=q, sort="name", limit=200)
                    b = await mg.groups(db, p, kind, sc, q=q, sort="name", limit=200, live=True)
                    assert a == b, (kind, sc, q, _diff(a, b))
                    n += 1
        for sort, filt in itertools.product(mg.SUB_SORTS, mg.SUB_FILTERS if full else ("all",)):
            for q in ("", "news"):
                a = await mg.subscriptions(db, p, q=q, sort=sort, filter=filt, limit=200)
                b = await mg.subscriptions(db, p, q=q, sort=sort, filter=filt, limit=200, live=True)
                assert a == b, (sort, filt, q)
                n += 1
        for sort in mg.SORTS:  # the orders (ties by key) and a later page
            sc = mg.Scope(where="all")
            a = await mg.groups(db, p, "sender", sc, sort=sort, limit=3, offset=2)
            assert a == await mg.groups(db, p, "sender", sc, sort=sort, limit=3, offset=2, live=True)
    return n


async def _check(user) -> dict:
    res = await tally.check(user.id)
    assert res is not None
    return res


async def test_tally_matches_live_and_follows_changes(client, user, raw):
    """cleanup.groups, subscription.list: the tally's groups and totals are those of the live
    aggregates for every scope, with an anchor two days old (so the mail between a band's edge and
    the cutoff is read live), and after ingest, archive, delete, label, mark read, category,
    star, store sync delete and purge."""
    ids = await _box(user, raw)
    await tally.rebuild(user.id, anchor=datetime.now(UTC) - timedelta(days=2), batch=3)
    async with sessionmaker()() as db:
        spread = (await db.execute(text("SELECT count(*) FROM mail_tally_units WHERE user_id = :u AND spread"),
                                   {"u": user.id})).scalar()
        loose = (await db.execute(text("SELECT count(*) FROM mail_tally_units WHERE user_id = :u AND conv < 0"),
                                  {"u": user.id})).scalar()
    assert spread >= 4 and loose == 1, "two-sender and two-band conversations, a message without one"
    assert await _compare(user) > 900

    async def act(action, *keys, **extra):
        r = await client.post("/api/v1/conversations/actions",
                              json={"action": action, "ids": [ids[k][1] for k in keys], **extra})
        assert r.status_code in (200, 202), r.text
        await _compare(user, full=False, tiny=True)

    ids["new1"] = await _ingest(user, raw("Digest new", frm="Weekly <digest@news.example>", to=user.email,
                                          date=_ago(0.5), extra=LIST))
    ids["carl3"] = await _ingest(user, raw("Re: Plan", frm="Carl <carl@work.example>", to=user.email,
                                           irt=f"<plan2-{user.id}@work.example>", date=_ago(0.2)))
    await _compare(user, full=False)
    await act("archive", "news0", "shop0", "carl3")
    await act("trash", "alert0", "news1")
    lab = (await client.post("/api/v1/labels", json={"name": "Keep"})).json()["id"]
    await act("add_labels", "shop1", label_ids=[lab])
    await act("read", "news4", "uk0", "ann1")
    await act("unread", "dora")
    await act("star", "shop2")
    await act("important", "uk1")
    await act("category", "news3", "alert1", category="social")
    await act("inbox", "news7")
    await act("delete_forever", "news2")
    # Deleted in the store itself (another mail app): store sync marks it deleted; the purge
    # scrubs the row.
    from mailr.core import purge, storesync

    async with sessionmaker()() as db:
        jid = (await db.get(Message, ids["shop4"][0])).jmap_id
        await storesync._UserSync(user.id)._apply_destroyed(db, [jid])
        await db.commit()
    await _compare(user, full=False)
    async with sessionmaker()() as db:
        await purge.scrub(db, user.id, [ids["shop4"][0]])
        await db.commit()
    # A reply makes a conversation protected.
    await _ingest(user, raw("Re: Hello", frm=f"Me <{user.email}>", to="dora@work.example",
                            irt=None, date=_ago(0.1)), source="send", roles=["sent"])
    await _compare(user)
    assert (await _check(user))["senders"] == 0
    await _compare(user, full=False)  # the anchor moved to now: no mail between edge and cutoff


async def test_restricted_token_counts_without_local_only_mail(user, raw):
    """A token that may not read local-only mail gets the tally without it; one limited to some
    accounts groups live."""
    await _box(user, raw)
    await tally.rebuild(user.id)
    await _compare(user, full=False, token=SimpleNamespace(local_only=False, connector_ids=[]))
    async with sessionmaker()() as db:
        assert await mg._tally_anchor(db, _p(user, SimpleNamespace(local_only=False, connector_ids=[1])), 0) is None
        assert await mg._tally_anchor(db, _p(user), 45) is None, "a cutoff between the bands counts live"


async def test_concurrent_changes_to_one_sender(user, raw):
    """Two transactions change mail of one sender at once (two conversations in one tally row, and
    two messages of one conversation): both changes are counted, and the check finds nothing."""
    ids = {}
    for i in range(3):
        ids[i] = await _ingest(user, raw(f"Note {i}", frm="Fay <fay@corp.example>", to=user.email,
                                         msgid=f"<n{i}-{user.id}@corp.example>", date=_ago(3 + i)))
    ids[3] = await _ingest(user, raw("Re: Note 0", frm="Fay <fay@corp.example>", to=user.email,
                                     irt=f"<n0-{user.id}@corp.example>", date=_ago(2)))

    async def change(mid, **values):
        async with sessionmaker()() as db:
            m = await db.get(Message, mid)
            for k, v in values.items():
                setattr(m, k, v)
            await db.flush()
            await asyncio.sleep(0.05)  # both transactions hold their change before either commits
            await db.commit()

    for _round in range(3):
        await asyncio.gather(change(ids[1][0], seen=True), change(ids[2][0], seen=True))
        await asyncio.gather(change(ids[0][0], category="updates"), change(ids[3][0], seen=True))
        await asyncio.gather(change(ids[1][0], seen=False), change(ids[2][0], category="social"))
    await _compare(user, full=False)
    assert (await _check(user))["senders"] == 0


async def test_check_corrects_drift_and_rebuild_from_cli(user, raw, capsys):
    """The nightly check finds a row counted wrong and corrects it; `mailr tally rebuild` counts
    again from scratch."""
    await _box(user, raw)
    async with sessionmaker()() as db:
        await db.execute(text("UPDATE mail_tally SET messages = messages + 5 WHERE id = (SELECT min(id) "
                              "FROM mail_tally WHERE user_id = :u)"), {"u": user.id})
        await db.execute(text("DELETE FROM mail_tally_units WHERE conv = :c"), {"c": (await db.execute(
            select(Message.conversation_id).where(Message.user_id == user.id, Message.from_email == "carl@work.example")
        )).scalars().first()})
        await db.commit()
    res = await _check(user)
    assert res["senders"] >= 2
    await _compare(user, full=False)
    async with sessionmaker()() as db:
        drifted, total = (await db.execute(text("SELECT drifted, drifted_total FROM mail_tally_users WHERE user_id = :u"),
                                           {"u": user.id})).one()
    assert drifted == total == res["senders"]
    assert (await _check(user))["senders"] == 0
    from mailr.cli import _tally_rebuild

    assert await _tally_rebuild(str(user.id)) == 0
    assert "counted" in capsys.readouterr().out
    await _compare(user, full=False)


async def test_stale_tally_counts_live_until_rebuilt(user, raw):
    """A change too large to count per conversation marks the tally stale: the pages count live
    until the build job has counted again."""
    await _box(user, raw)
    async with sessionmaker()() as db:
        tally.invalidate(db, user.id)
        await db.commit()
        assert (await tally.state(db, user.id))[0] == "stale"
        assert await mg._tally_anchor(db, _p(user), 0) is None
    while not await tally.build_step(user.id, batch=4):
        pass
    await _compare(user, full=False)


async def test_new_user_has_a_ready_tally(user):
    async with sessionmaker()() as db:
        assert (await tally.state(db, user.id))[0] == "ready"


async def test_upgrade_queues_a_build(user, raw):
    """A user without a tally (as after the upgrade) gets the build job; it counts a batch per run
    until the tally is ready."""
    from mailr.core.jobs import _HANDLERS, Retry
    from mailr.models import Job

    await _box(user, raw)
    async with sessionmaker()() as db:
        await db.execute(text("DELETE FROM mail_tally_users WHERE user_id = :u"), {"u": user.id})
        await db.commit()
    await tally.queue_builds()
    async with sessionmaker()() as db:
        job = (await db.execute(select(Job).where(Job.kind == "mail.tally_build", Job.user_id == user.id))).scalar_one()
        assert job.lane == "backfill"
    for _ in range(100):
        try:
            await _HANDLERS["mail.tally_build"]({"user_id": user.id}, None)
            break
        except Retry:
            continue
    await _compare(user, full=False, tiny=True)


async def test_upkeep_error_keeps_the_change_and_marks_the_tally_stale(user, raw, monkeypatch):
    """An error in the upkeep never fails the user's change: the tally is marked stale (the pages
    count live) and counted again by the build job."""
    def boom(conn, owner):
        raise RuntimeError("upkeep")

    monkeypatch.setattr(tally, "STRICT", False)
    monkeypatch.setattr(tally, "_recount", boom)
    mid, _cid = await _ingest(user, raw("Kept", frm="Gil <gil@corp.example>", to=user.email, date=_ago(1)))
    async with sessionmaker()() as db:
        assert await db.get(Message, mid) is not None
        assert (await tally.state(db, user.id))[0] == "stale"
