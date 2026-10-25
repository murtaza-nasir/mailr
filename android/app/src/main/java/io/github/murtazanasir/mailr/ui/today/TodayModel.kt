package io.github.murtazanasir.mailr.ui.today

import androidx.annotation.StringRes
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.cal.SmartList
import io.github.murtazanasir.mailr.data.cal.TaskQueries
import io.github.murtazanasir.mailr.data.reminders.ReminderPlan
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.i18n.LocaleFormats
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZonedDateTime
import java.util.Locale

/** The part of the day the Today widget greets: its words and its little picture. */
enum class DayPart(@StringRes private val greetingRes: Int) {
    MORNING(R.string.today_greeting_morning), AFTERNOON(R.string.today_greeting_afternoon),
    EVENING(R.string.today_greeting_evening), NIGHT(R.string.today_greeting_night);

    val greeting: String get() = AppText.get(greetingRes)

    companion object {
        /** 5–11 morning, 12–16 afternoon, 17–21 evening, 22–4 night. */
        fun of(hour: Int): DayPart = when (hour) {
            in 5..11 -> MORNING
            in 12..16 -> AFTERNOON
            in 17..21 -> EVENING
            else -> NIGHT
        }
    }
}

/**
 * What the Today widget shows, built from the phone's own
 * copy of the calendar and tasks, so it works offline and changes the moment a task does.
 */
data class TodayModel(
    val part: DayPart,
    /** "Tuesday 6 October". */
    val dateLine: String,
    val events: List<Event>,
    val tasks: List<Task>,
    /** False: the user is signed out, or the calendar is not connected. */
    val ready: Boolean = true,
    /** The task whose tick can be undone for a few seconds (the header's Undo chip). */
    val undo: Undo? = null,
) {
    data class Event(val uid: String, val occurrence: String, val title: String, val time: String, val colour: String?,
                     val calendar: String?, val now: Boolean)
    data class Task(val uid: String, val title: String, val note: String?, val overdue: Boolean)
    data class Undo(val uid: String, val title: String)

    /** "2 events · 3 tasks", "Nothing due". */
    val summary: String get() = buildList {
        if (events.isNotEmpty()) add(AppText.plural(R.plurals.today_events, events.size, events.size))
        if (tasks.isNotEmpty()) add(AppText.plural(R.plurals.today_tasks, tasks.size, tasks.size))
    }.joinToString(" · ").ifEmpty { AppText.get(R.string.today_nothing_due) }

    val empty: Boolean get() = events.isEmpty() && tasks.isEmpty()

    companion object {
        val EMPTY: String get() = AppText.get(R.string.today_empty)
        const val MAX_EVENTS = 6
        const val MAX_TASKS = 8

        fun signedOut(now: ZonedDateTime) = TodayModel(DayPart.of(now.hour), dateLine(now.toLocalDate()), emptyList(), emptyList(), ready = false)

        fun dateLine(d: LocalDate): String = d.format(LocaleFormats.date("EEEEdMMMM", "EEEE d MMMM"))

        /**
         * Today's events that have not ended (all-day ones first, then by start; cancelled and
         * declined ones left out), and the open tasks of the Today smart list (due today or
         * earlier, or started), overdue first.
         */
        fun build(events: List<CalendarEvent>, tasks: List<TaskItem>, now: ZonedDateTime, h24: Boolean = false,
                  undo: Undo? = null): TodayModel {
            val zone = now.zone
            val nowMs = now.toInstant().toEpochMilli()
            val today = now.toLocalDate()
            val ev = events.asSequence()
                .filter { !it.cancelled && !it.myStatus.equals("DECLINED", ignoreCase = true) }
                .mapNotNull { e ->
                    val start = ReminderPlan.instant(e.start, zone) ?: return@mapNotNull null
                    val end = ReminderPlan.eventEnd(e, zone) ?: start
                    val dayStart = today.atStartOfDay(zone).toInstant().toEpochMilli()
                    val dayEnd = today.plusDays(1).atStartOfDay(zone).toInstant().toEpochMilli()
                    if (start >= dayEnd || end <= dayStart) return@mapNotNull null
                    if (!e.allDay && end <= nowMs) return@mapNotNull null
                    Triple(e, start, end)
                }
                .sortedWith(compareBy({ !it.first.allDay }, { it.second }))
                .distinctBy { it.first.key }
                .take(MAX_EVENTS)
                .map { (e, start, end) ->
                    Event(e.uid, e.occurrence, e.summary.ifBlank { AppText.get(R.string.today_no_title) },
                        if (e.allDay) AppText.get(R.string.today_all_day) else time(start, zone, h24), e.calendarColor ?: e.color, e.calendar,
                        now = !e.allDay && start <= nowMs && end > nowMs)
                }.toList()
            val open = tasks.filter { TaskQueries.open(it) && !TaskQueries.deferred(it, nowMs, zone) &&
                TaskQueries.inSmart(it, SmartList.TODAY, nowMs, zone) && it.parent == null }
            val tk = open
                .map { it to TaskQueries.inSmart(it, SmartList.OVERDUE, nowMs, zone) }
                .sortedWith(compareBy({ !it.second }, { dueOrder(it.first, zone) }))
                .take(MAX_TASKS)
                .map { (t, overdue) ->
                    Task(t.uid, t.summary.ifBlank { AppText.get(R.string.today_no_title) }, taskNote(t, overdue, today, zone, h24), overdue)
                }
            return TodayModel(DayPart.of(now.hour), dateLine(today), ev, tk, undo = undo)
        }

        /** A timed due by its time; a date-only due ("any time that day") after the timed ones of its day. */
        internal fun dueOrder(t: TaskItem, zone: ZoneId): Long {
            val due = t.due ?: return Long.MAX_VALUE
            val ms = ReminderPlan.instant(due, zone) ?: return Long.MAX_VALUE
            return if (ReminderPlan.isDateOnly(due)) ms + 86_400_000L - 1 else ms
        }

        internal fun time(ms: Long, zone: ZoneId, h24: Boolean): String {
            val t = Instant.ofEpochMilli(ms).atZone(zone).toLocalTime()
            // English keeps the widget's own short times ("3 PM", "3:30 PM"); another language takes its own.
            val lang = Locale.getDefault().language
            if (lang.isNotEmpty() && lang != "en") return t.format(LocaleFormats.date(if (h24) "Hmm" else "hmma", if (h24) "H:mm" else "h:mm a"))
            return if (h24) "%d:%02d".format(t.hour, t.minute)
            else {
                val h = if (t.hour % 12 == 0) 12 else t.hour % 12
                (if (t.minute == 0) "$h" else "%d:%02d".format(h, t.minute)) + if (t.hour < 12) " AM" else " PM" // i18n-ignore: English clock only (above)
            }
        }

        /** "Overdue · Mon 5 Oct", "Due 3 PM", or null for a task due today with no time. */
        private fun taskNote(t: TaskItem, overdue: Boolean, today: LocalDate, zone: ZoneId, h24: Boolean): String? {
            val due = t.due ?: return null
            val ms = ReminderPlan.instant(due, zone) ?: return null
            val day = Instant.ofEpochMilli(ms).atZone(zone).toLocalDate()
            return when {
                overdue && day.isBefore(today) -> AppText.get(R.string.today_overdue_note, day.format(
                    if (day.year == today.year) LocaleFormats.date("EEEdMMM", "EEE d MMM") else LocaleFormats.date("yMMMd", "d MMM yyyy")))
                ReminderPlan.isDateOnly(due) -> null
                overdue -> AppText.get(R.string.today_overdue_note, time(ms, zone, h24))
                else -> AppText.get(R.string.today_due_note, time(ms, zone, h24))
            }
        }
    }
}
