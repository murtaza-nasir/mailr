package io.github.murtazanasir.mailr.data.reminders

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.Occurrence
import io.github.murtazanasir.mailr.data.api.ReminderKeys
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.db.ReminderEntity
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.i18n.LocaleFormats
import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.util.Locale

object Kind {
    const val EVENT = "EVENT"
    const val TASK = "TASK"
    /** A note reminder (`n|<note id>|abs|<instant>`): fired here only while the Notes app is not installed. */
    const val NOTE = "NOTE"
}

object RState {
    const val PENDING = "PENDING"
    const val SHOWN = "SHOWN"
    const val DISMISSED = "DISMISSED"
    const val DONE = "DONE"
}

/** The user's reminder settings, as the planner needs them. Offsets are minutes before. */
data class ReminderConfig(
    val events: Boolean = true,
    val tasks: Boolean = true,
    /** For timed events that carry no VALARM of their own; null = none. */
    val defaultTimedEvent: Int? = 10,
    /** For all-day events without a VALARM; minutes before local midnight (-540 = 9 am that day). */
    val defaultAllDayEvent: Int? = null,
    /** Tasks with a due date/time but no VALARM get a reminder at the due time (9 am for date-only). */
    val defaultTask: Boolean = true,
    val morningHour: Int = 9,
    /** Collections whose `notify` is off: their events and tasks never remind (the server skips them too). */
    val silentCollections: Set<String> = emptySet(),
    /** `missed` of /reminders/settings: a reminder that fires late is shown; false drops it quietly. */
    val showMissed: Boolean = true,
)

/** A reminder of the user's on a note, from the Notes module's copy (data/notes). */
data class NoteReminderSource(val noteId: String, val title: String, val list: Boolean, val key: String, val at: String, val fireAt: Long)

data class PlannedReminder(
    val key: String,
    val kind: String,
    val itemUid: String,
    val occurrence: String?,
    val title: String,
    val detail: String,
    val fireAt: Long,
    val startAt: Long?,
    val endAt: Long?,
)

/** Stored reminder rows moved to canonical keys ([ReminderPlan.normalize]). */
data class Normalized(
    val upsert: List<ReminderEntity>,
    val delete: List<String>,
    val renamed: List<Pair<ReminderEntity, ReminderEntity>>,
)

/** What a sync changes in the reminders table and in the notification shade. */
data class Reconcile(
    val upsert: List<ReminderEntity>,
    val delete: List<String>,
    /** Posted notifications whose item is gone (deleted, completed, declined): take them down. */
    val cancel: List<ReminderEntity>,
    /** Posted notifications whose title or time changed: update them quietly. */
    val repost: List<ReminderEntity>,
)

/**
 * Plans reminders from synced events and tasks, and diffs the plan against what is stored.
 *
 * Idempotent by construction: a reminder's key is (kind, item, occurrence/due, offset), so
 * planning twice yields the same keys, and [reconcile] only inserts what is new, moves
 * what changed, and deletes what vanished. Rows the user acted on (shown, dismissed, snoozed)
 * keep their state across syncs, so nothing fires twice and snoozes survive.
 */
object ReminderPlan {
    /** A reminder due within this much of "now" is on time, not missed. */
    const val GRACE_MS = 2 * 60_000L
    const val HORIZON_MS = 8 * 24 * 60 * 60_000L
    const val LOOKBACK_MS = 24 * 60 * 60_000L

    fun plan(events: List<CalendarEvent>, tasks: List<TaskItem>, cfg: ReminderConfig, now: Long, zone: ZoneId): List<PlannedReminder> {
        val out = mutableListOf<PlannedReminder>()
        if (cfg.events) for (e in events) {
            if (e.cancelled || e.myStatus.equals("DECLINED", ignoreCase = true)) continue
            if (e.calendar != null && e.calendar in cfg.silentCollections) continue
            val start = instant(e.start, zone) ?: continue
            val end = eventEnd(e, zone) ?: continue
            if (end <= now) continue
            // An event marked "no reminders" (the user chose none) gets no default either, as on the server.
            val offsets = e.reminders.ifEmpty {
                if (e.noReminders) emptyList() else listOfNotNull(if (e.allDay) cfg.defaultAllDayEvent else cfg.defaultTimedEvent)
            }
            for (m in offsets.distinct()) {
                val fire = fireAt(e.start, start, m, zone)
                if (fire < now - LOOKBACK_MS || fire > now + HORIZON_MS) continue
                out += PlannedReminder(ReminderKeys.event(e.uid, e.occurrence, m), Kind.EVENT, e.uid, e.occurrence,
                    e.summary.ifBlank { AppText.get(R.string.rem_no_title) }, eventDetail(e, zone), fire, start, end)
            }
        }
        if (cfg.tasks) for (t in tasks) {
            if (t.completed) continue
            if (t.list != null && t.list in cfg.silentCollections) continue
            val due = t.due ?: continue
            val anchor = instant(due, zone) ?: continue
            val dateOnly = isDateOnly(due)
            // Absolute reminders count as minutes before the due, as the server's mirror counts them.
            val own = t.reminders + absoluteMinutes(t, zone)
            val offsets = own.ifEmpty { if (cfg.defaultTask) listOf(if (dateOnly) -cfg.morningHour * 60 else 0) else emptyList() }
            for (m in offsets.distinct()) {
                val fire = fireAt(due, anchor, m, zone)
                if (fire < now - LOOKBACK_MS || fire > now + HORIZON_MS) continue
                out += PlannedReminder(ReminderKeys.task(t.uid, t.dueKey, m), Kind.TASK, t.uid, null,
                    t.summary.ifBlank { AppText.get(R.string.rem_untitled_task) }, taskDetail(t, zone), fire, anchor, null)
            }
        }
        return out
    }

    /** The items reminders may still refer to: "uid|occurrence" for events, "uid" for tasks. */
    fun liveKeys(events: List<CalendarEvent>, tasks: List<TaskItem>): Set<String> =
        events.filterNot { it.cancelled || it.myStatus.equals("DECLINED", true) }.map { "${it.uid}|${it.occurrence}" }.toSet() +
            tasks.filterNot { it.completed }.map { it.uid }.toSet()

    private fun liveKey(r: ReminderEntity) = if (r.kind == Kind.EVENT) "${r.itemUid}|${r.occurrence}" else r.itemUid

    /**
     * Note reminders as planned rows: one per key, at its own instant (absolute reminders have no
     * offset). [reminders]: (note id, title, "Note" or "List", key, instant).
     */
    fun planNotes(reminders: List<NoteReminderSource>): List<PlannedReminder> = reminders.map { r ->
        PlannedReminder(r.key, Kind.NOTE, r.noteId, r.at, r.title.ifBlank { if (r.list) AppText.get(R.string.rem_untitled_list) else AppText.get(R.string.rem_untitled_note) },
            if (r.list) AppText.get(R.string.rem_note_kind_list) else AppText.get(R.string.rem_note_kind_note), fireAt = r.fireAt, startAt = null, endAt = null)
    }

    fun reconcile(existing: List<ReminderEntity>, planned: List<PlannedReminder>, live: Set<String>, now: Long): Reconcile {
        val byKey = existing.associateBy { it.key }
        val upsert = mutableListOf<ReminderEntity>()
        val repost = mutableListOf<ReminderEntity>()
        for (p in planned) {
            val e = byKey[p.key]
            if (e == null) {
                // New to us. If its time has passed (just synced, or created late), it still
                // fires if the event has not started / the task is not long overdue.
                val onTime = p.fireAt >= now - GRACE_MS
                val stillUseful = p.startAt != null && p.startAt > now - (if (p.kind == Kind.TASK) 60 * 60_000L else 0)
                upsert += ReminderEntity(p.key, p.kind, p.itemUid, p.occurrence, p.title, p.detail,
                    fireAt = if (onTime) p.fireAt else maxOf(p.fireAt, now), plannedAt = p.fireAt, startAt = p.startAt, endAt = p.endAt,
                    state = if (onTime || stillUseful) RState.PENDING else RState.DISMISSED, updatedAt = now)
            } else {
                var n = e.copy(title = p.title, detail = p.detail, startAt = p.startAt, endAt = p.endAt, plannedAt = p.fireAt)
                if (e.state == RState.PENDING && !e.snoozed) n = n.copy(fireAt = p.fireAt)
                if (n != e) {
                    upsert += n.copy(updatedAt = now)
                    if (e.state == RState.SHOWN && (n.title != e.title || n.detail != e.detail)) repost += n
                }
            }
        }
        val plannedKeys = planned.mapTo(HashSet()) { it.key }
        val delete = mutableListOf<String>()
        val cancel = mutableListOf<ReminderEntity>()
        for (e in existing) {
            if (e.key in plannedKeys) continue
            val itemLive = liveKey(e) in live
            // Keep snoozes and posted reminders while their item exists; drop everything else.
            val keep = itemLive && ((e.state == RState.PENDING && e.snoozed) || e.state == RState.SHOWN)
            if (!keep) {
                delete += e.key
                if (e.state == RState.SHOWN) cancel += e
            }
        }
        return Reconcile(upsert, delete, cancel, repost)
    }

    /**
     * Stored rows whose key (or occurrence) is in an older form, rewritten to the canonical one.
     * Without this, a reminder the user already handled would come
     * back under its new key and fire again. When the canonical key already has a row, the one
     * further along wins (done, dismissed, showing, snoozed, pending). [renamed] pairs the old
     * row with its replacement, so a notification in the shade can move to the new key.
     */
    fun normalize(existing: List<ReminderEntity>, zone: ZoneId): Normalized {
        val byKey = existing.associateByTo(HashMap()) { it.key }
        val upsert = LinkedHashMap<String, ReminderEntity>()
        val delete = mutableListOf<String>()
        val renamed = mutableListOf<Pair<ReminderEntity, ReminderEntity>>()
        for (e in existing) {
            val key = ReminderKeys.normalize(e.key, zone)
            val occ = e.occurrence?.let { Occurrence.canonical(it, zone) }
            if (key == e.key && occ == e.occurrence) continue
            val moved = e.copy(key = key, occurrence = occ)
            if (key == e.key) {
                upsert[key] = moved
                continue
            }
            val other = byKey[key]
            val winner = if (other != null && progress(other) >= progress(moved)) other else moved
            delete += e.key
            byKey.remove(e.key)
            byKey[key] = winner
            upsert[key] = winner
            renamed += e to winner
        }
        return Normalized(upsert.values.toList(), delete, renamed)
    }

    private fun progress(r: ReminderEntity) = when (r.state) {
        RState.DONE -> 4
        RState.DISMISSED -> 3
        RState.SHOWN -> 2
        else -> if (r.snoozed) 1 else 0
    }

    /** Is a posted reminder still worth showing (e.g. after a reboot)? */
    fun relevant(r: ReminderEntity, now: Long): Boolean = when (r.kind) {
        Kind.EVENT -> (r.endAt ?: ((r.startAt ?: r.fireAt) + 60 * 60_000L)) > now
        else -> true
    }

    fun missed(r: ReminderEntity, now: Long): Boolean = r.fireAt < now - GRACE_MS

    // ---------------- time helpers ----------------

    fun isDateOnly(iso: String): Boolean = iso.length == 10

    /**
     * A task's absolute reminders ([TaskItem.remindersAt]) as minutes before its due (a date due
     * counts from local midnight), the form of reminder keys.
     */
    fun absoluteMinutes(t: TaskItem, zone: ZoneId): List<Int> {
        val anchor = t.due?.let { instant(it, zone) } ?: return emptyList()
        return t.remindersAt.orEmpty().mapNotNull { at -> instant(at, zone)?.let { ((anchor - it) / 60_000L).toInt() } }
    }

    /**
     * When a reminder [minutes] before [iso] fires (the server's planner.fire_time). A timed
     * anchor fires an exact duration before ([anchorMs]). A date anchors at local midnight and the
     * offset is counted on the wall clock in [zone], so "1 day before at 9:00" is 9:00 across a DST
     * change.
     */
    fun fireAt(iso: String, anchorMs: Long, minutes: Int, zone: ZoneId): Long =
        if (isDateOnly(iso)) catching {
            LocalDate.parse(iso).atStartOfDay().minusMinutes(minutes.toLong()).atZone(zone).toInstant().toEpochMilli()
        }.getOrDefault(anchorMs - minutes * 60_000L)
        else anchorMs - minutes * 60_000L

    fun instant(iso: String, zone: ZoneId): Long? = catching {
        if (isDateOnly(iso)) LocalDate.parse(iso).atStartOfDay(zone).toInstant().toEpochMilli()
        else OffsetDateTime.parse(iso).toInstant().toEpochMilli()
    }.getOrNull() ?: catching { Instant.parse(iso).toEpochMilli() }.getOrNull()

    fun eventEnd(e: CalendarEvent, zone: ZoneId): Long? {
        val start = instant(e.start, zone) ?: return null
        val end = e.end?.let { instant(it, zone) }
        return when {
            end != null && end > start -> end
            e.allDay -> LocalDate.parse(e.start.take(10)).plusDays(1).atStartOfDay(zone).toInstant().toEpochMilli()
            else -> start + 60 * 60_000L
        }
    }

    private val time get() = DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).withLocale(Locale.getDefault())

    fun eventDetail(e: CalendarEvent, zone: ZoneId): String {
        val where = e.location?.takeIf { it.isNotBlank() }?.let { " · $it" }.orEmpty()
        if (e.allDay) return AppText.get(R.string.rem_all_day) + where
        val s = instant(e.start, zone)?.let { Instant.ofEpochMilli(it).atZone(zone) } ?: return where.trimStart(' ', '·')
        val end = eventEnd(e, zone)?.let { Instant.ofEpochMilli(it).atZone(zone) }
        val range = if (end != null && end.toLocalDate() == s.toLocalDate()) "${s.format(time)} – ${end.format(time)}" else s.format(time)
        return range + where
    }

    fun taskDetail(t: TaskItem, zone: ZoneId): String {
        val due = t.due ?: return AppText.get(R.string.rem_no_due_date)
        if (isDateOnly(due)) return AppText.get(R.string.rem_due_date, dayWord(LocalDate.parse(due), AppClock.today(zone)))
        val d = instant(due, zone)?.let { Instant.ofEpochMilli(it).atZone(zone) } ?: return AppText.get(R.string.rem_due)
        val day = dayWord(d.toLocalDate(), AppClock.today(zone))
        return AppText.get(R.string.rem_due_day_comma_time, day, d.format(time))
    }

    fun dayWord(d: LocalDate, today: LocalDate): String = when (d) {
        today -> AppText.get(R.string.rem_due_day_today)
        today.plusDays(1) -> AppText.get(R.string.rem_due_day_tomorrow)
        today.minusDays(1) -> AppText.get(R.string.rem_due_day_yesterday)
        else -> d.format(LocaleFormats.date("EEEMMMd", "EEE, MMM d"))
    }
}

/** The group summary for one kind of reminder (events or tasks). */
data class SummarySpec(val kind: String, val count: Int, val title: String, val lines: List<String>, val more: Int)

object ReminderGrouping {
    const val MAX_LINES = 6

    /**
     * Posted as soon as one reminder of a kind is showing and updated on every change. It is
     * cancelled only when none is left, because cancelling a summary also takes down its
     * children.
     */
    fun summary(kind: String, shown: List<ReminderEntity>): SummarySpec? {
        val rows = shown.filter { it.kind == kind && it.state == RState.SHOWN }
            .sortedWith(compareBy({ it.startAt ?: it.fireAt }, { it.title }))
        if (rows.isEmpty()) return null
        val title = AppText.plural(if (kind == Kind.EVENT) R.plurals.rem_summary_events else R.plurals.rem_summary_tasks, rows.size, rows.size)
        val lines = rows.take(MAX_LINES).map { r ->
            if (r.missed) AppText.get(R.string.rem_summary_line_missed, r.title, r.detail) else AppText.get(R.string.rem_summary_line, r.title, r.detail)
        }
        return SummarySpec(kind, rows.size, title, lines, (rows.size - MAX_LINES).coerceAtLeast(0))
    }
}

data class SnoozeChoice(val id: String, val label: String, val at: ZonedDateTime)

object SnoozeTimes {
    /** Choices offered by "Snooze…", soonest first. */
    fun options(now: ZonedDateTime, kind: String, startAt: ZonedDateTime?, morningHour: Int = 9): List<SnoozeChoice> {
        val base = now.withSecond(0).withNano(0)
        val list = mutableListOf(
            SnoozeChoice("5m", AppText.plural(R.plurals.rem_minutes_long, 5, 5), base.plusMinutes(5)),
            SnoozeChoice("15m", AppText.plural(R.plurals.rem_minutes_long, 15, 15), base.plusMinutes(15)),
            SnoozeChoice("1h", AppText.plural(R.plurals.rem_hours, 1, 1), base.plusHours(1)),
        )
        if (kind == Kind.EVENT && startAt != null && startAt.isAfter(base.plusMinutes(2)) && startAt.isBefore(base.plusDays(1))) {
            list += SnoozeChoice("start", AppText.get(R.string.rem_when_it_starts), startAt)
        }
        val m = morning(now, morningHour)
        list += SnoozeChoice("morning", if (m.toLocalDate() == now.toLocalDate()) AppText.get(R.string.rem_this_morning) else AppText.get(R.string.rem_tomorrow_morning), m)
        // Soonest first; drop a choice within a minute of an earlier one.
        return list.sortedBy { it.at }.fold(mutableListOf<SnoozeChoice>()) { acc, c ->
            if (acc.none { kotlin.math.abs(java.time.Duration.between(it.at, c.at).toMinutes()) < 1 }) acc += c
            acc
        }
    }

    /** "Tomorrow morning" — or this morning when it is still the small hours. */
    fun morning(now: ZonedDateTime, hour: Int = 9): ZonedDateTime {
        val day = if (now.toLocalTime().isBefore(LocalTime.of(4, 0))) now.toLocalDate() else now.toLocalDate().plusDays(1)
        return day.atTime(hour, 0).atZone(now.zone)
    }

    fun resolve(id: String, now: ZonedDateTime, startAt: ZonedDateTime?, morningHour: Int = 9): ZonedDateTime? =
        options(now, if (startAt != null) Kind.EVENT else Kind.TASK, startAt, morningHour).firstOrNull { it.id == id }?.at
            ?: when (id) {
                "5m" -> now.plusMinutes(5)
                "10m" -> now.plusMinutes(10)
                "15m" -> now.plusMinutes(15)
                "1h" -> now.plusHours(1)
                "morning" -> morning(now, morningHour)
                else -> null
            }
}
