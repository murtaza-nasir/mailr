package io.github.murtazanasir.mailr.data.reminders

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.time.AppClock
import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.PowerManager
import io.github.murtazanasir.mailr.data.api.Occurrence
import io.github.murtazanasir.mailr.data.api.ReminderKeys
import io.github.murtazanasir.mailr.data.cal.CalendarRepository
import io.github.murtazanasir.mailr.data.cal.JoinLink
import io.github.murtazanasir.mailr.data.db.ReminderDao
import io.github.murtazanasir.mailr.data.db.ReminderEntity
import io.github.murtazanasir.mailr.ui.MainActivity
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.time.Instant
import java.time.ZoneId
import java.time.ZonedDateTime

/**
 * Local reminders for events and tasks.
 *
 * - [resync] (after every sync or local edit, on boot, on time/zone changes) plans reminders
 *   from Room, diffs them against the `reminders` table ([ReminderPlan.reconcile]) and fixes the
 *   notification shade to match.
 * - Only the earliest pending reminder is armed with AlarmManager; [fire] posts everything due
 *   and arms the next one. Several reminders due together therefore post in one pass and group.
 * - Snooze, Done and Dismiss change rows, so they survive reboots and never double-fire.
 */
class ReminderEngine(
    private val dao: ReminderDao,
    private val calendar: CalendarRepository,
    private val config: suspend () -> ReminderConfig,
    private val notifier: ReminderSink,
    private val alarms: AlarmArm,
    private val now: () -> Long = AppClock::millis,
    private val zone: () -> ZoneId = ZoneId::systemDefault,
    /** Where actions are shared with the other devices (the server; nothing in tests by default). */
    private val remote: ReminderRemote = ReminderRemote.None,
    /** Full-screen alerts, repeats, the Ask button and the morning agenda. */
    private val alerts: suspend () -> AlertPrefs = { AlertPrefs() },
    private val log: AlertLog = AlertLog.Memory(),
    /** Note reminders to fire here (empty while the Notes app is installed: it fires them). */
    private val notes: suspend () -> List<NoteReminderSource> = { emptyList() },
) {
    private val lock = Mutex()

    suspend fun resync() = lock.withLock {
        val t = now()
        normalizeStored()
        val events = calendar.eventsNow(t - ReminderPlan.LOOKBACK_MS - CalendarRepository.DAY, t + ReminderPlan.HORIZON_MS + CalendarRepository.DAY)
        val tasks = calendar.tasksNow()
        val noteRems = catching { notes() }.getOrDefault(emptyList())
        val planned = ReminderPlan.plan(events, tasks, config(), t, zone()) + ReminderPlan.planNotes(noteRems)
        val r = ReminderPlan.reconcile(dao.all(), planned, ReminderPlan.liveKeys(events, tasks) + noteRems.map { it.noteId }, t)
        if (r.upsert.isNotEmpty()) dao.put(r.upsert)
        if (r.delete.isNotEmpty()) dao.delete(r.delete)
        r.cancel.forEach { notifier.cancel(it) }
        r.repost.forEach { show(it, alert = false) }
        postDue(t)
    }

    /**
     * Rows stored under older key forms move to the canonical keys, keeping their state, so a
     * reminder handled before the upgrade stays handled. A notification in the shade moves with
     * its row (its tag is the key).
     */
    private suspend fun normalizeStored() {
        val n = ReminderPlan.normalize(dao.all(), zone())
        if (n.delete.isEmpty() && n.upsert.isEmpty()) return
        if (n.delete.isNotEmpty()) dao.delete(n.delete)
        if (n.upsert.isNotEmpty()) dao.put(n.upsert)
        for ((old, new) in n.renamed) {
            if (old.state == RState.SHOWN) notifier.cancel(old)
            if (new.state == RState.SHOWN && old.state == RState.SHOWN) show(new, alert = false)
        }
    }

    /** The alarm went off (or the watchdog ran): post what is due, arm the next. ACT reminder.fire @android.notification */
    suspend fun fire() = lock.withLock { postDue(now()) }

    /**
     * After a reboot the shade is empty. Reminders that were showing and still matter come
     * back quietly; ones that came due while the phone was off post as missed.
     */
    suspend fun onBoot() {
        lock.withLock {
            val t = now()
            dao.allShown().forEach { r ->
                if (ReminderPlan.relevant(r, t)) show(r, alert = false)
                else dao.setState(r.key, RState.DISMISSED, t)
            }
        }
        resync()
    }

    private suspend fun postDue(t: Long) {
        // A reminder the user cleared outside our actions (swipe, "Clear all") is dismissed.
        val active = notifier.activeKeys()
        if (active != null) dao.allShown().filter { it.key !in active }.forEach { dao.setState(it.key, RState.DISMISSED, t) }
        val due = dao.due(t + 5_000)
        val showMissed = due.isEmpty() || config().showMissed
        for (r in due) {
            if (!ReminderPlan.relevant(r, t)) {
                dao.setState(r.key, RState.DISMISSED, t)
                continue
            }
            val missed = ReminderPlan.missed(r, t)
            // `missed` off in /reminders/settings: a late reminder counts as fired and posts nothing, as on the server.
            if (missed && !showMissed) {
                dao.setState(r.key, RState.DISMISSED, t)
                continue
            }
            val row = r.copy(state = RState.SHOWN, missed = missed, updatedAt = t)
            dao.put(listOf(row))
            show(row, alert = true)
            // ACT reminder.repeat @android.notification: an unhandled reminder alerts again every few minutes, up to three times.
            val every = alerts().remindAgain
            if (every > 0 && !missed) log.setRepeat(row.key, 0, t + every * 60_000L)
        }
        repeatDue(t)
        agendaDue(t)
        refreshSummaries()
        alarms.arm(nextAlarm())
    }

    /** Repeats that are due: the reminder alerts again if it is still showing and still matters. */
    private suspend fun repeatDue(t: Long) {
        val every = alerts().remindAgain
        for ((key, v) in log.repeats()) {
            val (count, at) = v
            if (at > t + 5_000) continue
            val row = dao.get(key)
            if (every <= 0 || row == null || !ReminderAlerts.mayRepeat(row, t)) { log.dropRepeat(key); continue }
            show(row, alert = true, again = true)
            if (count + 1 >= alerts().remindMax) log.dropRepeat(key) else log.setRepeat(key, count + 1, t + every * 60_000L)
        }
    }

    /** ACT reminder.agenda @android.notification: once a day at the morning hour, today's events and tasks in one quiet notification. */
    private suspend fun agendaDue(t: Long) {
        val a = alerts()
        if (!a.morningAgenda) return
        val at = ReminderAlerts.agendaDue(t, zone(), a.morningHour, log.agendaDay) ?: return
        if (t + 5_000 < at) return
        val day = Instant.ofEpochMilli(t).atZone(zone()).toLocalDate()
        log.agendaDay = day.toString()
        val start = day.atStartOfDay(zone()).toInstant().toEpochMilli()
        val agenda = MorningAgenda.build(calendar.eventsNow(start, start + CalendarRepository.DAY + 3_600_000L), calendar.tasksNow(), day, zone())
        if (agenda != null) notifier.postAgenda(agenda)
    }

    /** The single alarm: the next reminder, the next repeat or the next morning agenda, whichever comes first. */
    private suspend fun nextAlarm(): Long? {
        val a = alerts()
        val t = now()
        val repeat = if (a.remindAgain > 0) log.repeats().values.minOfOrNull { it.second } else null
        val agenda = if (a.morningAgenda) ReminderAlerts.nextAgenda(t, zone(), a.morningHour, log.agendaDay) else null
        return listOfNotNull(dao.nextFire(), repeat, agenda).minOrNull()
    }

    /**
     * Posts [r] with its event's conference link (the Join action), when it has one. A new alert may
     * take the screen (full-screen alerts on); a repeat never does.
     */
    private suspend fun show(r: ReminderEntity, alert: Boolean, again: Boolean = false) {
        val a = alerts()
        val e = eventOf(r)
        val style = AlertStyle(fullScreen = alert && !again && a.fullScreen && ReminderAlerts.fullScreenFits(r, now()), ask = a.ask, again = again,
            prep = e != null && e.attendeeList.isNotEmpty())
        notifier.post(r, alert, e?.let { JoinLink.find(it.url, it.location, it.description) }, style)
    }

    /** The event behind an event reminder (this occurrence, else the series), for Join and Prep. */
    private suspend fun eventOf(r: ReminderEntity): io.github.murtazanasir.mailr.data.api.CalendarEvent? {
        if (r.kind != Kind.EVENT) return null
        return calendar.eventsOf(r.itemUid).let { all -> all.firstOrNull { it.occurrence == r.occurrence } ?: all.firstOrNull() }
    }

    private suspend fun refreshSummaries() {
        for (kind in listOf(Kind.EVENT, Kind.TASK)) {
            val spec = ReminderGrouping.summary(kind, dao.shown(kind))
            if (spec == null) notifier.cancelSummary(kind) else notifier.postSummary(spec)
        }
    }

    /** ACT task.snooze @android.notification @android.sheet, ACT event.snooze @android.notification @android.sheet: the reminder comes back at [at]; the record is unchanged. */
    suspend fun snooze(key: String, at: Long) {
        val until = lock.withLock {
            val r = dao.get(key) ?: return
            val t = now()
            notifier.cancel(r)
            val until = maxOf(at, t + 1_000)
            dao.put(listOf(r.copy(state = RState.PENDING, fireAt = until, snoozed = true, missed = false, updatedAt = t)))
            refreshSummaries()
            alarms.arm(nextAlarm())
            until
        }
        catching { remote.snooze(key, until) }
    }

    /** Quick snooze from a notification action ("5m", "15m", "1h", "morning", "start"). */
    suspend fun snooze(key: String, choice: String, morningHour: Int) {
        val r = dao.get(key) ?: return
        val z = zone()
        val nowZ = Instant.ofEpochMilli(now()).atZone(z)
        val start = if (r.kind == Kind.EVENT) r.startAt?.let { Instant.ofEpochMilli(it).atZone(z) } else null
        val at = SnoozeTimes.resolve(choice, nowZ, start, morningHour) ?: nowZ.plusMinutes(10)
        snooze(key, at.toInstant().toEpochMilli())
    }

    /** ACT reminder.dismiss @android.notification @android.sheet: done with it (Dismiss, or a swipe). The record is unchanged. */
    suspend fun dismiss(key: String) {
        lock.withLock {
            val r = dao.get(key) ?: return
            if (r.state == RState.SHOWN || r.state == RState.PENDING) dao.setState(key, RState.DISMISSED, now())
            notifier.cancel(r)
            refreshSummaries()
            alarms.arm(nextAlarm())
        }
        catching { remote.handle(listOf(key)) }
    }

    /** The user swiped the group away: every reminder of that kind is dismissed. */
    suspend fun dismissAll(kind: String) {
        val keys = lock.withLock {
            val t = now()
            val rows = dao.shown(kind)
            rows.forEach { notifier.cancel(it); dao.setState(it.key, RState.DISMISSED, t) }
            refreshSummaries()
            rows.map { it.key }
        }
        catching { remote.handle(keys) }
    }

    /**
     * ACT event.remind_before @android.notification @android.sheet: this reminder is done with, and a one-off reminder fires [minutes]
     * before the occurrence starts. It rides on the snooze mechanism (a pending, snoozed row keeps
     * its time and survives syncs while the event exists), under the key every device uses.
     */
    suspend fun remindBefore(key: String, minutes: Int): Long? {
        val (at, r) = lock.withLock {
            val r = dao.get(key) ?: return null
            val start = r.startAt ?: return null
            if (r.kind != Kind.EVENT) return null
            val at = start - minutes * 60_000L
            if (at <= now()) return null
            val newKey = ReminderKeys.event(r.itemUid, Occurrence.canonical(r.occurrence, zone()), minutes)
            val t = now()
            notifier.cancel(r)
            if (newKey != key) dao.setState(key, RState.DISMISSED, t)
            dao.put(listOf(r.copy(key = newKey, state = RState.PENDING, fireAt = at, plannedAt = at, snoozed = true,
                missed = false, updatedAt = t)))
            refreshSummaries()
            alarms.arm(nextAlarm())
            at to r
        }
        catching { remote.add(r.itemUid, r.occurrence.orEmpty(), minutes, listOf(key)) }
        return at
    }

    /** Undo of snooze / dismiss / remind before: [previous] comes back as it was, everywhere. */
    suspend fun restore(previous: ReminderEntity, drop: String? = null) {
        lock.withLock {
            drop?.takeIf { it != previous.key }?.let { k -> dao.get(k)?.let { notifier.cancel(it) }; dao.delete(listOf(k)) }
            dao.put(listOf(previous.copy(updatedAt = now())))
            if (previous.state == RState.SHOWN) show(previous, alert = false)
            refreshSummaries()
            alarms.arm(nextAlarm())
        }
        catching { remote.handle(listOfNotNull(previous.key, drop?.takeIf { it != previous.key }), undo = true) }
    }

    /** What the triage queue shows (notification.open_stack): missed first, then oldest. */
    suspend fun shownQueue(): List<ReminderEntity> =
        dao.allShown().sortedWith(compareBy<ReminderEntity>({ !it.missed }, { it.fireAt }, { it.title }))

    /**
     * ACT reminder.sync @android.notification: apply what other devices did. Handled → gone here too; snoozed → comes
     * back at that time; pending → planned again (an undo), or a one-off "remind before" row.
     * Keys this device has not planned yet get a placeholder row, which the next plan fills in.
     */
    suspend fun applyRemote(items: List<io.github.murtazanasir.mailr.data.api.RemoteReminder>) = lock.withLock {
        val t = now()
        normalizeStored()
        for (raw in items) {
            // A server before the calendar build may hold keys in older forms.
            val i = raw.copy(key = ReminderKeys.normalize(raw.key, zone()), occurrence = Occurrence.canonical(raw.occurrence, zone()))
            val kind = when (i.kind) {
                "e" -> Kind.EVENT
                "n" -> Kind.NOTE
                else -> Kind.TASK
            }
            val row = dao.get(i.key)
            when (i.state) {
                "handled" -> {
                    if (row == null) dao.put(listOf(placeholder(i, kind, RState.DISMISSED, 0L, t)))
                    else if (row.state == RState.SHOWN || row.state == RState.PENDING) {
                        if (row.state == RState.SHOWN) notifier.cancel(row)
                        dao.setState(row.key, RState.DISMISSED, t)
                    }
                }
                "snoozed" -> {
                    val until = i.snoozedUntil?.let { catching { java.time.OffsetDateTime.parse(it).toInstant().toEpochMilli() }.getOrNull() }
                        ?: continue
                    if (row == null) dao.put(listOf(placeholder(i, kind, RState.PENDING, until, t, snoozed = true)))
                    else {
                        if (row.state == RState.SHOWN) notifier.cancel(row)
                        dao.put(listOf(row.copy(state = RState.PENDING, fireAt = until, snoozed = true, missed = false, updatedAt = t)))
                    }
                }
                "pending" -> {
                    if (row == null) {
                        if (i.oneoff) {
                            val start = ReminderPlan.instant(i.occurrence, zone()) ?: continue
                            val at = start - i.minutes * 60_000L
                            dao.put(listOf(placeholder(i, kind, RState.PENDING, at, t, snoozed = true, startAt = start)))
                        }
                    } else if (row.state == RState.DISMISSED || (row.state == RState.PENDING && row.snoozed && !i.oneoff)) {
                        dao.put(listOf(row.copy(state = RState.PENDING, fireAt = row.plannedAt, snoozed = i.oneoff, updatedAt = t)))
                    }
                }
            }
        }
        refreshSummaries()
        postDue(t)
    }

    private fun placeholder(i: io.github.murtazanasir.mailr.data.api.RemoteReminder, kind: String, state: String, fireAt: Long, t: Long,
                            snoozed: Boolean = false, startAt: Long? = null) =
        ReminderEntity(i.key, kind, i.uid, i.occurrence.takeIf { kind == Kind.EVENT || kind == Kind.NOTE }, "", "", fireAt, fireAt, startAt, null,
            state, snoozed = snoozed, updatedAt = t)

    /** "Mark done" on a task reminder: works offline (the PATCH is queued) and from the lock screen. */
    suspend fun done(key: String) {
        val r = lock.withLock {
            val r = dao.get(key) ?: return
            dao.doneForItem(Kind.TASK, r.itemUid, now())
            dao.all().filter { it.itemUid == r.itemUid && it.kind == Kind.TASK }.forEach { notifier.cancel(it) }
            refreshSummaries()
            r
        }
        // Outside the lock: completing re-plans (onChanged → resync).
        catching { calendar.setTaskDone(r.itemUid, true, viaQueue = true) }
    }

    suspend fun reminder(key: String): ReminderEntity? = dao.get(key)

    /** Signed out: nothing may stay armed or showing. */
    suspend fun clear() = lock.withLock {
        dao.all().forEach { if (it.state == RState.SHOWN) notifier.cancel(it) }
        notifier.cancelSummary(Kind.EVENT)
        notifier.cancelSummary(Kind.TASK)
        log.clearRepeats()
        dao.clear()
        alarms.arm(null)
    }
}

/** The server side of reminder actions (CalendarRepository's queue); None when there is no server. */
interface ReminderRemote {
    suspend fun snooze(key: String, until: Long)
    suspend fun handle(keys: List<String>, undo: Boolean = false)
    suspend fun add(uid: String, occurrence: String, minutes: Int, handle: List<String>)

    object None : ReminderRemote {
        override suspend fun snooze(key: String, until: Long) {}
        override suspend fun handle(keys: List<String>, undo: Boolean) {}
        override suspend fun add(uid: String, occurrence: String, minutes: Int, handle: List<String>) {}
    }
}

/** Where posted reminders go (the notification shade; a fake in tests). */
interface ReminderSink {
    fun post(r: ReminderEntity, alert: Boolean)
    /** [join]: the event's conference link (`join_url`), shown as a Join action. */
    fun post(r: ReminderEntity, alert: Boolean, join: String?) = post(r, alert)
    /** [style]: full screen, the Ask button, a repeat. */
    fun post(r: ReminderEntity, alert: Boolean, join: String?, style: AlertStyle) = post(r, alert, join)
    /** The morning agenda. */
    fun postAgenda(agenda: Agenda) {}
    fun cancel(r: ReminderEntity)
    fun postSummary(spec: SummarySpec)
    fun cancelSummary(kind: String)
    /** Keys of our reminder notifications currently in the shade, or null if unknown. */
    fun activeKeys(): Set<String>?
}

fun interface AlarmArm {
    fun arm(at: Long?)
}

/**
 * Arms the single "next reminder" alarm.
 *
 * Exact alarms (setExactAndAllowWhileIdle) when the app may use them: USE_EXACT_ALARM is granted
 * to calendar apps on Android 13+, SCHEDULE_EXACT_ALARM on 12L and older. If exact alarms are
 * denied, it falls back to setAndAllowWhileIdle (may be late by minutes) and the UI prompts.
 * In Doze, exact-while-idle alarms are throttled to about one per 9 minutes; when a reminder is
 * due inside that window while the device idles, setAlarmClock (never throttled) is used for it.
 */
class AlarmScheduler(private val context: Context) : AlarmArm {
    private val am = context.getSystemService(AlarmManager::class.java)
    private val prefs = context.getSharedPreferences("mailr_alarms", Context.MODE_PRIVATE)

    fun canExact(): Boolean = Build.VERSION.SDK_INT < Build.VERSION_CODES.S || am.canScheduleExactAlarms()

    private fun fireIntent(): PendingIntent = PendingIntent.getBroadcast(context, 0,
        Intent(context, ReminderAlarmReceiver::class.java).setAction(ReminderAlarmReceiver.ACTION_FIRE),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)

    override fun arm(at: Long?) {
        val pi = fireIntent()
        if (at == null) {
            am.cancel(pi)
            prefs.edit().remove("armed_at").apply()
            return
        }
        val t = maxOf(at, System.currentTimeMillis() + 1_000)  // clock-ok: AlarmManager runs on the real clock
        val idle = context.getSystemService(PowerManager::class.java)?.isDeviceIdleMode == true
        val lastIdleExact = prefs.getLong("last_idle_exact", 0)
        try {
            when {
                !canExact() -> am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, t, pi)
                idle && t - lastIdleExact < 9 * 60_000L -> {
                    val show = PendingIntent.getActivity(context, 1,
                        Intent(context, MainActivity::class.java).setData(android.net.Uri.parse("mailr://calendar")),
                        PendingIntent.FLAG_IMMUTABLE)
                    am.setAlarmClock(AlarmManager.AlarmClockInfo(t, show), pi)
                }
                else -> {
                    am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, t, pi)
                    if (idle) prefs.edit().putLong("last_idle_exact", t).apply()
                }
            }
        } catch (e: SecurityException) {
            am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, t, pi)
        }
        prefs.edit().putLong("armed_at", t).apply()
    }

    fun armedAt(): Long? = prefs.getLong("armed_at", 0).takeIf { it > 0 }

    fun describe(at: Long): String = ZonedDateTime.ofInstant(Instant.ofEpochMilli(at), ZoneId.systemDefault()).toString()
}
