"""Core background jobs and the periodic scheduler."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select, text

from mailr.config import get_settings
from mailr.core.jobs import Retry, enqueue, handler
from mailr.db import session_scope
from mailr.models import Connector, Message, Rule, User, light_message

log = logging.getLogger("mailr.tasks")


@handler("snooze.wake")
async def snooze_wake(payload: dict[str, Any], ctx) -> None:
    from mailr.core.actions import wake_snoozed
    from mailr.core.store import user_store
    from mailr.models import Conversation

    async with session_scope() as db:
        conv = await db.get(Conversation, payload["conversation_id"])
        if conv is None:
            return
        user = await db.get(User, conv.user_id)
        store = await user_store(db, user)
        await wake_snoozed(db, store, conv.id, payload["until"])


@handler("followup.check")
async def followup_check(payload: dict[str, Any], ctx) -> None:
    # ACT mail.follow_up @api
    """ACT mail.follow_up came due: nudge (inbox + "No reply" + a push) unless a reply arrived."""
    from mailr.core import push
    from mailr.core.actions import check_follow_up
    from mailr.core.store import user_store
    from mailr.models import Conversation

    async with session_scope() as db:
        conv = await db.get(Conversation, payload["conversation_id"])
        if conv is None:
            return
        user = await db.get(User, conv.user_id)
        store = await user_store(db, user)
        if await check_follow_up(db, store, conv.id, payload["at"]):
            who = next((x.get("name") or x.get("email") for x in conv.participants or []
                        if (x.get("email") or "").lower() != user.email.lower()), None)
            from mailr import i18n

            lang = i18n.user_language(user)
            await push.fan_out(db, user.id, "follow_up",
                               i18n.t("push.follow_up.title", lang, who=who) if who else i18n.t("push.follow_up.title_anyone", lang),
                               conv.subject or i18n.t("common.no_subject", lang), {"conversation_id": conv.id})


@handler("rules.apply")
async def rules_apply(payload: dict[str, Any], ctx) -> None:
    """Apply one rule to existing mail (the 'also apply to matching conversations' box)."""
    from mailr.core import threading
    from mailr.core.rules import matching_message_ids
    from mailr.core.store import user_store

    async with session_scope() as db:
        r = await db.get(Rule, payload["rule_id"])
        if r is None:
            return
        user = await db.get(User, r.user_id)
        from mailr.core.rules import scope_condition

        ids = await matching_message_ids(db, r.user_id, r.query,
                                         extra=scope_condition(r.scope, payload.get("scope")))
        store = await user_store(db, user)
        a = r.actions or {}
        read = (r.query, r.actions, r.scope)
        roles = dict((await db.execute(text(
            "SELECT role, id FROM labels WHERE user_id=:u AND role IS NOT NULL"), {"u": r.user_id})).all())
        for i in range(0, len(ids), 200):
            msgs = list((await db.execute(select(Message).options(*light_message())
                                          .where(Message.id.in_(ids[i:i + 200])))).scalars())
            add = list(a.get("add_labels") or [])
            remove = list(a.get("remove_labels") or [])
            if a.get("archive") and "inbox" in roles:
                remove.append(roles["inbox"])
            if a.get("trash") and "trash" in roles:
                await store.replace_labels(msgs, [roles["trash"]], db)
            elif a.get("spam") and "junk" in roles:
                # Own labels are kept aside for "Not spam"; the classifier's come off.
                from mailr.intel.classify import move_to_spam

                await move_to_spam(db, store, msgs, roles["junk"])
            elif add or remove:
                await store.set_labels(msgs, add=add, remove=remove, db=db)
            flags = {}
            if a.get("mark_read"):
                flags["seen"] = True
            if a.get("star"):
                flags["flagged"] = True
            if a.get("important") is not None:
                flags["important"] = bool(a["important"])
            if flags:
                await store.set_keywords(msgs, **flags)
            if a.get("category"):
                for m in msgs:
                    m.category = a["category"]
            from mailr.core.actions import mark_local_change

            mark_local_change(msgs)
            await db.flush()
            await threading.refresh(db, r.user_id, list({m.conversation_id for m in msgs if m.conversation_id}))
            await db.commit()
        now = (await db.execute(select(Rule.query, Rule.actions, Rule.scope).where(Rule.id == r.id))).first()
        r.hits = (r.hits or 0) + len(ids)
        changed = now is not None and tuple(now) != read
    if changed:
        # The rule was edited while this ran (its "apply to existing" found this job running and
        # queued nothing): once more, with the rule as it is now.
        raise Retry(delay=1)


@handler("rules.forward")
async def rules_forward(payload: dict[str, Any], ctx) -> None:
    """Forward one message to one verified forwarding address, through the send gate as the
    user. Local-only mail is never forwarded. One job per (message, address), so a retry never
    re-sends to an address that already got it."""
    from mailr.core.drafts import reply_payload
    from mailr.core.sendgate import SendGate, _SystemPrincipal
    from mailr.models import ForwardAddress, Label, Person

    addr = str(payload.get("to") or "").lower()
    async with session_scope() as db:
        m = await db.get(Message, payload["message_id"])
        if m is None or m.local_only or m.deleted:
            return
        # Checked again here: the message may have moved to Spam or Trash, or its sender been
        # blocked, since it arrived. Spam is never relayed from the user's domain.
        binned = (await db.execute(select(Label.id).where(
            Label.user_id == m.user_id, Label.role.in_(("junk", "trash")),
            Label.id.in_(list(m.label_ids or []))))).first()
        blocked = (await db.execute(select(Person.id).where(
            Person.user_id == m.user_id, Person.email == (m.from_email or "").lower(),
            Person.blocked.is_(True)))).first()
        if binned or blocked:
            log.info("not forwarding message %s: it is in Spam or Trash, or its sender is blocked", m.id)
            return
        ok = (await db.execute(select(ForwardAddress.id).where(
            ForwardAddress.user_id == m.user_id, ForwardAddress.email == addr,
            ForwardAddress.verified_at.is_not(None)))).first()
        if not ok:
            log.warning("not forwarding message %s: %s is not a verified forwarding address", m.id, addr)
            return
        user = await db.get(User, m.user_id)
        sp = _SystemPrincipal(user)
        body = await reply_payload(db, sp, m.id, "forward")
        body["to"] = [{"email": addr, "name": ""}]
        # One Outbox row per (message, address): the job runs again if the worker dies after
        # this commits, and must not queue a second copy.
        await SendGate(db, sp).submit(body, system={"auto_forward": True}, idem_key=f"fwd:{m.id}:{addr}")


async def _cleanup_uploads() -> None:
    root = Path(get_settings().data_dir) / "uploads"
    if not root.exists():
        return
    cutoff = time.time() - 30 * 86400
    async with session_scope() as db:
        referenced = set()
        rows = await db.execute(text(
            "SELECT jsonb_array_elements(upstream->'draft_payload'->'attachments')->>'blob' FROM messages "
            "WHERE draft AND NOT deleted AND upstream ? 'draft_payload'"))
        referenced.update(r[0] for r in rows if r[0])
        rows = await db.execute(text(
            "SELECT jsonb_array_elements(payload->'attachments')->>'blob' FROM outbox "
            "WHERE status IN ('queued','awaiting_approval','sending')"))
        referenced.update(r[0] for r in rows if r[0])
    for f in root.glob("*/*"):
        blob = f.name.split(".")[0]
        if blob not in referenced and f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)


async def _cleanup_db() -> None:
    async with session_scope() as db:
        await db.execute(text("DELETE FROM sessions WHERE expires_at < now()"))
        await db.execute(text("DELETE FROM idempotency_keys WHERE created_at < now() - interval '24 hours'"))
        await db.execute(text("DELETE FROM change_log WHERE created_at < now() - interval '60 days'"))
        # Live-editing history (core/live.py): versions older than 30 days go, but each item keeps
        # its newest, so version numbers never count down.
        await db.execute(text("DELETE FROM item_versions v WHERE v.created_at < now() - interval '30 days' "
                              "AND EXISTS (SELECT 1 FROM item_versions n WHERE n.user_id = v.user_id "
                              "AND n.kind = v.kind AND n.item_id = v.item_id AND n.version > v.version)"))
        await db.execute(text("DELETE FROM audit_log WHERE created_at < now() - interval '365 days'"))
        # Model decisions nothing reads any more: withdrawn ones (moved to Spam) and rejected ones
        # other than labels, on mail and decisions older than 180 days. Kept: every decision of
        # yours, your filters' and the provider's (training data), what applies now (the "why" of
        # each label, category and signal), and rejected label suggestions (a re-run must not
        # suggest them again, intel/classify.py). Production on 14 Oct 2026: 358k rows, about
        # 450 a day once the first import was in (the 21k a day average was that import).
        await db.execute(text(
            "DELETE FROM label_decisions d USING messages m WHERE m.id = d.message_id "
            "AND d.source NOT IN ('user', 'rule', 'provider') "
            "AND (d.status = 'withdrawn' OR (d.status = 'rejected' AND d.kind <> 'label')) "
            "AND d.created_at < now() - interval '180 days' AND m.date < now() - interval '180 days'"))
        from mailr.intel import usage

        await usage.prune(db)  # Model use keeps 90 days (intel/usage.py)
        from mailr.calendar.reminders import prune as prune_reminders

        await prune_reminders(db)
        from mailr.notes.tasks import prune as prune_notes

        await prune_notes(db)


async def _wake_overdue_snoozes() -> None:
    async with session_scope() as db:
        rows = await db.execute(text(
            "SELECT id, user_id, snoozed_until FROM conversations WHERE snoozed_until < now() - interval '2 minutes'"))
        for r in rows:
            await enqueue(db, "snooze.wake", {"conversation_id": r.id, "until": r.snoozed_until.isoformat()},
                          user_id=r.user_id, dedupe_key=f"snooze:{r.id}")


async def _due_follow_ups() -> None:
    async with session_scope() as db:
        rows = await db.execute(text(
            "SELECT id, user_id, follow_up_at FROM conversations WHERE follow_up_at < now() - interval '2 minutes'"))
        for r in rows:
            await enqueue(db, "followup.check", {"conversation_id": r.id, "at": r.follow_up_at.isoformat()},
                          user_id=r.user_id, dedupe_key=f"followup:{r.id}")


async def _retention() -> None:
    """Store-mode connectors: queue upstream deletion for messages past the retention window.
    Nothing while the mail export is off: deletion needs it (connectors/tasks.py retention),
    unless the export check is off (MAILR_STORE_BACKUP_CHECK=false)."""
    from mailr.core.backup import store_mode_available

    if not store_mode_available():
        return
    async with session_scope() as db:
        conns = (await db.execute(select(Connector).where(
            Connector.mode == "store", Connector.delete_upstream.is_(True), Connector.enabled.is_(True)))).scalars()
        for c in conns:
            await enqueue(db, "connector.retention", {"connector_id": c.id}, user_id=c.user_id,
                          dedupe_key=f"retention:{c.id}")


async def _exports() -> None:
    """The group calendars' daily export; each user's exports are spread over the day (SPREAD)."""
    async with session_scope() as db:
        await enqueue(db, "export.group_calendars", {}, dedupe_key="export.group_calendars")


async def _user_exports(db, uid: int) -> None:
    if get_settings().mail_export:  # MAILR_MAIL_EXPORT=false: the other exports only
        await enqueue(db, "export.user", {"user_id": uid}, user_id=uid, dedupe_key=f"export:{uid}")
    await enqueue(db, "export.calendars", {"user_id": uid}, user_id=uid, dedupe_key=f"export.calendars:{uid}")
    await enqueue(db, "export.notes", {"user_id": uid}, user_id=uid, dedupe_key=f"export.notes:{uid}")


# A first export is not queued again for an owner who had an export job of that kind this
# recently (a failing first export retries through its own attempts, then waits this long).
FIRST_EXPORT_RETRY = timedelta(hours=1)


async def _first_exports() -> None:
    """Owners with data (mail, calendars, notes or tables, the group calendars) that were never
    exported: their export is queued now, not at the next daily run (_exports), so a new install
    or a new user is backed up within minutes and the readiness check (core/health.py) does not
    wait a day for it. Skips an owner with an export job of that kind queued, running, or
    created within FIRST_EXPORT_RETRY (a long first mail export chains in batches)."""
    from mailr.calendar import backup as cal_backup
    from mailr.core import backup, health
    from mailr.notes import backup as notes_backup

    mail = await health._mail_users() if get_settings().mail_export else []
    cal = await cal_backup.owners_with_data()
    notes = await notes_backup.owners_with_data()

    def never_run() -> tuple[list[int], list[int | None], list[int]]:
        return ([u for u in mail if backup.last_run(backup.export_root(u)) is None],
                [o for o in cal if backup.last_run(cal_backup.export_dir(o)) is None],
                [o for o in notes if backup.last_run(notes_backup.export_dir(o)) is None])

    mail, cal, notes = await asyncio.to_thread(never_run)
    if not (mail or cal or notes):
        return
    async with session_scope() as db:
        recent = {(r.kind, r.user_id) for r in (await db.execute(text(
            "SELECT DISTINCT kind, user_id FROM jobs WHERE kind = ANY(:kinds) AND (status IN ('queued', 'running') "
            "OR created_at > now() - make_interval(secs => :s))"),
            {"kinds": ["export.user", "export.calendars", "export.notes", "export.group_calendars"],
             "s": FIRST_EXPORT_RETRY.total_seconds()})).all()}
        for uid in mail:
            if ("export.user", uid) not in recent:
                await enqueue(db, "export.user", {"user_id": uid}, user_id=uid, dedupe_key=f"export:{uid}")
        for o in cal:
            if o is None:
                if ("export.group_calendars", None) not in recent:
                    await enqueue(db, "export.group_calendars", {}, dedupe_key="export.group_calendars")
            elif ("export.calendars", o) not in recent:
                await enqueue(db, "export.calendars", {"user_id": o}, user_id=o, dedupe_key=f"export.calendars:{o}")
        for uid in notes:
            if ("export.notes", uid) not in recent:
                await enqueue(db, "export.notes", {"user_id": uid}, user_id=uid, dedupe_key=f"export.notes:{uid}")


async def _user_retrain(db, uid: int) -> None:
    await enqueue(db, "intel.train_all", {"user_id": uid}, user_id=uid, dedupe_key=f"intel.train_all:{uid}")


async def _user_people(db, uid: int) -> None:
    """Strength, group suggestions and signature details (contacts/tasks.py)."""
    from mailr.contacts.tasks import queue_people

    await queue_people(db, uid)


async def _user_learning(db, uid: int) -> None:
    from mailr.knowledge.tasks import queue_learning

    await queue_learning(db, uid)


async def _user_purge(db, uid: int) -> None:
    """Deleted mail's leftovers (core/purge.py); the first run after an upgrade cleans what was
    deleted before."""
    from mailr.core import purge

    await purge.queue_sweep(db, uid)


async def _user_tally(db, uid: int) -> None:
    """The nightly check of the mail tally (core/tally.py)."""
    await enqueue(db, "mail.tally_check", {"user_id": uid}, user_id=uid, dedupe_key=f"mail.tally_check:{uid}")


async def _tally_builds() -> None:
    """The mail tally of users who have none yet, or a stale one (core/tally.py)."""
    from mailr.core import tally

    await tally.queue_builds()


# Per-user work that runs once a day (or twice) is spread over the day: each user's run falls at a
# time of day of their own (user_offset), so the exports, retraining and people scores of 100 users
# are not all queued at once. (name, period, the scheduler task it replaced, what it queues).
SPREAD: list[tuple[str, timedelta, str, Any]] = [
    ("exports", timedelta(hours=24), "_exports", _user_exports),
    ("retrain", timedelta(hours=24), "_retrain", _user_retrain),
    ("people", timedelta(hours=24), "_people_daily", _user_people),
    ("learning", timedelta(hours=12), "_kb_learning", _user_learning),
    ("purge", timedelta(hours=24), "_purge_sweep", _user_purge),
    ("tally", timedelta(hours=24), "_tally_check", _user_tally),
]


def user_offset(user_id: int, period: timedelta) -> float:
    """Seconds into each period at which a user's spread run falls: the same for a user every time,
    and far apart for consecutive ids (Fibonacci hashing)."""
    return (user_id * 2654435761 % 2**32) / 2**32 * period.total_seconds()


def latest_slot(user_id: int, period: timedelta, now: datetime) -> datetime:
    """The user's most recent run time at or before `now`."""
    p, off = period.total_seconds(), user_offset(user_id, period)
    return datetime.fromtimestamp((now.timestamp() - off) // p * p + off, UTC)


async def _spread_daily() -> None:
    """Queue the spread work of each user whose time came since their last run. A user's last run is
    the schedule_runs row "<name>:<user id>", claimed in the statement that checks it, so two workers
    never queue it twice. A user without one (new, or before the spread) counts from the last run of
    the scheduler task it replaced, or from a period ago when that run is more recent; without that
    task's run, from now: the first run is at the user's next time."""
    async with session_scope() as db:
        users = list((await db.execute(select(User.id).where(User.disabled.is_(False)))).scalars())
        runs = {r.name: r.last_run_at for r in await db.execute(text("SELECT name, last_run_at FROM schedule_runs"))}
        now = (await db.execute(text("SELECT now()"))).scalar_one()
        for name, period, before, queue in SPREAD:
            for uid in users:
                key = f"{name}:{uid}"
                last = runs.get(key)
                if last is None:
                    if runs.get(before) is None:
                        await db.execute(text("INSERT INTO schedule_runs (name, last_run_at) VALUES (:n, now()) "
                                              "ON CONFLICT (name) DO NOTHING"), {"n": key})
                        continue
                    # The user's own row starts from the replaced task's run, but never later than a
                    # period ago: that task's row may still be written every day (the group calendars'
                    # export keeps "_exports"), and a user whose time falls just before it would
                    # otherwise never come due.
                    last = min(runs[before], now - period)
                    await db.execute(text("INSERT INTO schedule_runs (name, last_run_at) VALUES (:n, :t) "
                                          "ON CONFLICT (name) DO NOTHING"), {"n": key, "t": last})
                slot = latest_slot(uid, period, now)
                if slot <= last:
                    continue
                won = (await db.execute(text(
                    "INSERT INTO schedule_runs (name, last_run_at) VALUES (:n, now()) "
                    "ON CONFLICT (name) DO UPDATE SET last_run_at = now() WHERE schedule_runs.last_run_at < :slot "
                    "RETURNING name"), {"n": key, "slot": slot})).first()
                if won:
                    await queue(db, uid)
        # The rows of users who are gone or disabled.
        mine, ids = {f"{name}:" for name, *_ in SPREAD}, set(users)
        gone = [n for n in runs if n[:n.find(":") + 1] in mine and n.rpartition(":")[2].isdigit()
                and int(n.rpartition(":")[2]) not in ids]
        if gone:
            await db.execute(text("DELETE FROM schedule_runs WHERE name = ANY(:n)"), {"n": gone})


async def _intel_backfill_due() -> None:
    """A backfill job for every user with imported mail still to triage and none queued: an
    import's enqueue is skipped while that user's job runs, and a job that found nothing just
    before an import committed has ended."""
    from mailr.intel.tasks import TODO_WHERE

    async with session_scope() as db:
        users = (await db.execute(text(
            "SELECT u.id FROM users u WHERE NOT u.disabled AND EXISTS (SELECT 1 FROM messages "
            f"WHERE messages.user_id = u.id AND {TODO_WHERE} AND messages.intel_state = 'backfill')"))).scalars().all()
        for uid in users:
            await enqueue(db, "intel.backfill", {"user_id": uid}, user_id=uid, dedupe_key=f"intel.backfill:{uid}")


async def _calendar_sync_due() -> None:
    """CalDAV connectors whose next sync is due (2 min for active users, else 15 min)."""
    from mailr.calendar.sync import due_syncs

    await due_syncs()


async def _webcal_due() -> None:
    """Calendar links due for another fetch (calendar/webcal.py)."""
    from mailr.calendar.webcal import due_refreshes

    await due_refreshes()


async def _calendar_maintenance() -> None:
    """Extend recurrence windows and purge old calendar tombstones."""
    from mailr.calendar.sync import daily_maintenance

    await daily_maintenance()


async def _reminders_due() -> None:
    """Server reminders due within two minutes: queued for their exact time (calendar/planner.py)."""
    from mailr.calendar.planner import due_reminders

    await due_reminders()


async def _reminders_replan() -> None:
    """Plan the reminders of occurrences entering the 8-day horizon."""
    from mailr.calendar.planner import replan_horizon

    await replan_horizon()


async def _approvals_and_outbox() -> None:
    from mailr.core.permissions import expire_pending
    from mailr.core.sendgate import sweep_stuck

    await expire_pending()
    await sweep_stuck()


async def _routines() -> None:
    from mailr.assistant.orders import due_routines

    await due_routines()


async def _speakr_sync_due() -> None:
    """Speakr connectors not synced for ten minutes (integrations/speakr/sync.py)."""
    from mailr.integrations.speakr.sync import due_syncs

    await due_syncs()


async def _address_books_due() -> None:
    """Address books of every calendar account, read-only (contacts/carddav.py)."""
    from mailr.contacts.tasks import due_address_books

    await due_address_books()


async def _tables_nightly() -> None:
    # Snapshots of changed tables and the 30-day history cleanup (tables/history.py).
    from mailr.tables.tasks import nightly

    await nightly()


async def _lists_repeat() -> None:
    # Repeating checklists whose reset is due: ticked items unticked, history kept (notes/repeat.py).
    from mailr.notes.repeat import run_due

    await run_due()


async def _db_dump_due() -> None:
    """The nightly database dump (core/dbdump.py), queued for the maintenance lane when it is due."""
    from mailr.core import dbdump

    await dbdump.enqueue_if_due()


PERIODIC = [
    (timedelta(minutes=1), _wake_overdue_snoozes),
    (timedelta(minutes=1), _due_follow_ups),
    (timedelta(minutes=1), _routines),
    (timedelta(minutes=1), _approvals_and_outbox),
    (timedelta(minutes=1), _calendar_sync_due),
    (timedelta(minutes=1), _reminders_due),
    (timedelta(minutes=1), _speakr_sync_due),
    (timedelta(minutes=15), _webcal_due),
    (timedelta(minutes=1), _lists_repeat),
    (timedelta(hours=1), _reminders_replan),
    (timedelta(hours=1), _retention),
    (timedelta(hours=1), _intel_backfill_due),
    (timedelta(hours=6), _cleanup_uploads),
    (timedelta(hours=6), _cleanup_db),
    (timedelta(hours=24), _exports),
    (timedelta(minutes=5), _first_exports),
    (timedelta(minutes=5), _spread_daily),
    (timedelta(minutes=5), _tally_builds),
    (timedelta(hours=24), _calendar_maintenance),
    (timedelta(minutes=30), _address_books_due),
    (timedelta(hours=24), _tables_nightly),
    (timedelta(minutes=15), _db_dump_due),
]


# A task counts as due this much before its interval is up, so a tick that comes a few seconds
# early (the scheduler sleeps 30 s between ticks) does not push it back by a whole tick.
SLACK_SECONDS = 5


async def claim(name: str, every: timedelta) -> bool:
    """Mark `name` as run now when its interval is up (or it never ran), in one statement on the
    database clock. True when this caller claimed it: two workers (an overlapping deploy) never run
    the same task twice, and a restart does not run the 12-hour and daily tasks again (they once
    ran 36 times in 3 days instead of 6 when the times were kept in memory)."""
    async with session_scope() as db:
        row = (await db.execute(text(
            "INSERT INTO schedule_runs (name, last_run_at) VALUES (:n, now()) "
            "ON CONFLICT (name) DO UPDATE SET last_run_at = now() "
            "WHERE schedule_runs.last_run_at <= now() - make_interval(secs => :s) "
            "RETURNING name"), {"n": name, "s": max(0.0, every.total_seconds() - SLACK_SECONDS)})).first()
    return row is not None


async def scheduler() -> None:
    while True:
        for every, fn in PERIODIC:
            try:
                if not await claim(fn.__name__, every):
                    continue
            except Exception as e:  # noqa: BLE001  the database is away: try again next tick
                log.warning("could not read the schedule for %s: %s", fn.__name__, type(e).__name__)
                break
            try:
                await fn()
            except Exception:
                log.exception("periodic task %s failed", fn.__name__)
        # The heartbeat the readiness check reads; a hung task above stops it (core/health.py).
        from mailr.core import health

        await health.on_scheduler_tick()
        await asyncio.sleep(30)


@handler("export.user")
async def export_user(payload: dict[str, Any], ctx) -> None:
    """Incremental Maildir export per user (core/backup.py); runs again until caught up. The chain
    follows the rows a batch went past, not the files it wrote: after a database restore most rows
    are in the export already. A job left queued when MAILR_MAIL_EXPORT was turned off does nothing.
    The next batch is this job again (Retry), so it keeps its dedupe key: a second export of the
    user (the daily one, another worker's) is never queued beside it."""
    from mailr.core import backup

    if not get_settings().mail_export:
        return
    r = await backup.export_batch(payload["user_id"])
    if r["complete"] or r["stalled"]:
        from mailr.core import health

        health.after_export()
    else:
        raise Retry(delay=2)


@handler("export.calendars")
async def export_calendars(payload: dict[str, Any], ctx) -> None:
    """Daily calendar and address-book export of one user (calendar/backup.py)."""
    from mailr.calendar import backup as cal_backup
    from mailr.core import health

    await cal_backup.export_user(int(payload["user_id"]))
    health.after_export()


@handler("export.notes")
async def export_notes(payload: dict[str, Any], ctx) -> None:
    """Daily plain-file export of one user's notes, lists and tables (notes/backup.py)."""
    from mailr.core import health
    from mailr.notes import backup as notes_backup

    await notes_backup.export_user(int(payload["user_id"]))
    health.after_export()


@handler("export.group_calendars")
async def export_group_calendars(payload: dict[str, Any], ctx) -> None:
    """Daily export of the group calendars, once for the server (calendar/backup.py)."""
    from mailr.calendar import backup as cal_backup
    from mailr.core import health

    await cal_backup.export_groups()
    health.after_export()


# A bulk job over at most this many conversations keeps what its Undo needs (the labels each
# message had before). Larger jobs have no Undo; the web's confirmation says so.
BULK_UNDO_MAX = 10_000


@handler("bulk.conversations")
async def bulk_conversations(payload: dict[str, Any], ctx) -> None:
    """An action on "all N conversations" of a list, in chunks. Resumable: the job's `progress`
    column is saved after every chunk, so a restarted job continues where it stopped.

    Each chunk writes small rows: its progress to jobs.progress and its undo record to
    job_undo_parts. The payload (with the id snapshot) is written once more at most, when the
    first chunk marks that the job has an Undo; it is never rewritten per chunk."""
    from sqlalchemy import bindparam, update
    from sqlalchemy.dialects.postgresql import JSONB

    from mailr.core import actions as act
    from mailr.core.auth import ALL_SCOPES, Principal
    from mailr.core.events import notify
    from mailr.core.permissions import REQUESTER_KEY, requester
    from mailr.core.store import user_store
    from mailr.models import Job, JobUndoPart

    ids: list[int] = payload["ids"]
    done = int(payload.get("done") or 0)
    async with session_scope() as db:
        saved = (await db.execute(select(Job.progress).where(Job.id == ctx.job_id))).scalar()
    if saved is not None:
        done = max(done, int(saved))
    total = len(ids)
    chunk = 200
    marked = {k for k in ("undo", "undo_category", "reverse") if payload.get(k)}
    while done < total:
        async with session_scope() as db:
            user = await db.get(User, ctx.user_id)
            p = Principal(user=user, scopes=set(ALL_SCOPES) - {"admin"})
            if payload.get(REQUESTER_KEY) is not None:
                # An agent's approved cleanup plan: its token's limits still apply.
                p = await requester(db, user, payload)
            store = await user_store(db, user)
            msgs = await act.messages_for(db, p, conv_ids=ids[done:done + chunk])
            res = await act.apply(act.ActionContext(db, store, p), msgs, payload["action"], payload.get("args") or {})
            done = min(total, done + chunk)
            await db.execute(update(Job).where(Job.id == ctx.job_id).values(progress=done))
            mark: dict[str, Any] = {}
            rec = (res.get("undo") or {}).get("importance")
            if rec:
                # The undo record of an importance toggle, one part per chunk (GET /bulk/{job}).
                db.add(JobUndoPart(job_id=ctx.job_id, kind="importance", data=rec))
                if "undo" not in marked:
                    mark["undo"] = {"parts": True}
            crec = (res.get("undo") or {}).get("categories")
            # (Show in tab's undo, restore_tab, also holds labels: a job keeps no undo for it.)
            if crec and total <= BULK_UNDO_MAX and (res.get("undo") or {}).get("action") == "restore_category":
                # The undo record of a category change (More → Category, a cleanup group's category),
                # one part per chunk: POST /conversations/actions {"action": "restore_category", "job": id}.
                db.add(JobUndoPart(job_id=ctx.job_id, kind="category", data=crec))
                if "undo_category" not in marked:
                    mark["undo_category"] = {"parts": True}
            rev = res.get("undo") or {}
            # Only an undo made of its action and, optionally, the labels before (other records,
            # such as importance above, carry their own data).
            if rev.get("action") and set(rev) <= {"action", "labels"} and not payload.get("is_undo") \
                    and total <= BULK_UNDO_MAX:
                # The reverse of any other action (archive, delete, move, labels, snooze, mute,
                # read/unread, star): its action and, where it has one, the labels each message had
                # before. POST /conversations/actions {"action": <reverse>, "job": id} runs it.
                if rev.get("labels"):
                    db.add(JobUndoPart(job_id=ctx.job_id, kind="reverse",
                                       data={str(k): v for k, v in rev["labels"].items()}))
                if "reverse" not in marked:
                    mark["reverse"] = {"action": rev["action"], "parts": True}
            if mark:
                await db.execute(update(Job).where(Job.id == ctx.job_id).values(
                    payload=Job.payload.op("||")(bindparam("mark", mark, type_=JSONB))))
                marked |= set(mark)
            await notify(db, user.id, {"type": "bulk", "job": ctx.job_id, "done": done, "total": total,
                                       "status": "running" if done < total else "done"})


@handler("mail.account_index")
async def account_index(payload: dict[str, Any], ctx) -> None:
    """Rebuild a user's account index (Conversation.account_ids) after their accounts or addresses
    changed, or after the upgrade that added it (core/mailaccounts.py)."""
    from mailr.core import mailaccounts, sidebar

    async with session_scope() as db:
        uid = payload["user_id"]
        if await db.get(User, uid) is None:
            return
        await mailaccounts.rebuild_index(db, uid, commit_each=True)
    sidebar.forget_user(uid)


@handler("db.dump")
async def db_dump(payload: dict[str, Any], ctx) -> None:
    """The nightly database dump (core/dbdump.py), in the maintenance lane. A failed dump is
    recorded for the readiness check and not retried at once: the next one is due the next night,
    or after 26 hours without a good one."""
    from mailr.core import dbdump

    await dbdump.run_and_record(payload.get("reason") or "nightly")
