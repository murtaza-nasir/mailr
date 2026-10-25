package io.github.murtazanasir.mailr.data.reminders

import android.content.Context
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.db.ReminderEntity
import io.github.murtazanasir.mailr.i18n.AppText
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.util.Locale

/** The user's reminder alert settings (Settings → Reminders). */
data class AlertPrefs(
    val fullScreen: Boolean = false,
    /** Minutes between repeat alerts; 0 = off. */
    val remindAgain: Int = 0,
    /** At most this many repeats (the server's `repeat.max`, 1 to 3). */
    val remindMax: Int = ReminderAlerts.MAX_REPEATS,
    val ask: Boolean = true,
    val morningAgenda: Boolean = false,
    val morningHour: Int = 9,
)

/** How one notification is posted. [again]: a repeat alert, which sounds even though the notification is already there. */
/** [prep]: an event with guests, whose notification offers Prep (its meeting prep) in Dismiss's place (event.prep). */
data class AlertStyle(val fullScreen: Boolean = false, val ask: Boolean = false, val again: Boolean = false, val prep: Boolean = false)

/**
 * Small state the alerts keep between alarms: the repeat schedule per reminder key and the day the
 * morning agenda last posted. SharedPreferences in the app ([PrefsAlertLog]), memory in tests.
 */
interface AlertLog {
    /** key → (repeats done, next repeat at). */
    fun repeats(): Map<String, Pair<Int, Long>>
    fun setRepeat(key: String, count: Int, at: Long)
    fun dropRepeat(key: String)
    fun clearRepeats()
    var agendaDay: String?

    class Memory : AlertLog {
        private val m = linkedMapOf<String, Pair<Int, Long>>()
        override fun repeats() = m.toMap()
        override fun setRepeat(key: String, count: Int, at: Long) { m[key] = count to at }
        override fun dropRepeat(key: String) { m.remove(key) }
        override fun clearRepeats() = m.clear()
        override var agendaDay: String? = null
    }
}

class PrefsAlertLog(context: Context) : AlertLog {
    private val p = context.getSharedPreferences("mailr_reminder_alerts", Context.MODE_PRIVATE)

    override fun repeats(): Map<String, Pair<Int, Long>> = p.all.filterKeys { it.startsWith("r:") }.mapNotNull { (k, v) ->
        val (c, at) = (v as? String)?.split('|')?.takeIf { it.size == 2 } ?: return@mapNotNull null
        k.removePrefix("r:") to ((c.toIntOrNull() ?: 0) to (at.toLongOrNull() ?: 0L))
    }.toMap()

    override fun setRepeat(key: String, count: Int, at: Long) { p.edit().putString("r:$key", "$count|$at").apply() }
    override fun dropRepeat(key: String) { p.edit().remove("r:$key").apply() }
    override fun clearRepeats() {
        val e = p.edit()
        p.all.keys.filter { it.startsWith("r:") }.forEach { e.remove(it) }
        e.apply()
    }

    override var agendaDay: String?
        get() = p.getString("agenda_day", null)
        set(v) { p.edit().putString("agenda_day", v).apply() }
}

object ReminderAlerts {
    /** A reminder repeats at most this many times. */
    const val MAX_REPEATS = 3
    /** The window around the start or due time in which a reminder may take the screen. */
    const val FULL_SCREEN_WINDOW_MS = 15 * 60_000L
    /** The morning agenda posts only in the first hours after the morning hour (a phone switched on at 3 pm gets none). */
    const val AGENDA_LATE_MS = 3 * 60 * 60_000L

    /**
     * A full-screen alert is for what is happening now: an event that starts within 15 minutes (or
     * started in the last 15), a task due within 15 minutes either side. Never for missed reminders
     * or date-only tasks (their anchor is midnight, hours from the morning reminder).
     */
    fun fullScreenFits(r: ReminderEntity, now: Long): Boolean {
        if (r.missed) return false
        val anchor = r.startAt ?: return false
        if (r.kind == Kind.EVENT && r.detail.startsWith(AppText.get(R.string.rem_all_day))) return false
        return kotlin.math.abs(anchor - now) <= FULL_SCREEN_WINDOW_MS
    }

    /**
     * Whether a shown reminder may repeat at [now]: never a missed one; an event stops once it has
     * been going for 15 minutes.
     */
    fun mayRepeat(r: ReminderEntity, now: Long): Boolean = when {
        r.state != RState.SHOWN || r.missed -> false
        r.kind == Kind.EVENT -> (r.startAt ?: r.fireAt) + FULL_SCREEN_WINDOW_MS > now
        else -> true
    }

    /** When today's agenda is due (the morning hour), or null when it has posted today or is too late. */
    fun agendaDue(now: Long, zone: ZoneId, morningHour: Int, lastDay: String?): Long? {
        val today = Instant.ofEpochMilli(now).atZone(zone).toLocalDate()
        if (lastDay == today.toString()) return null
        val at = today.atTime(morningHour, 0).atZone(zone).toInstant().toEpochMilli()
        return if (now > at + AGENDA_LATE_MS) null else at
    }

    /** The next morning-agenda moment after today's (for arming the alarm). */
    fun nextAgenda(now: Long, zone: ZoneId, morningHour: Int, lastDay: String?): Long {
        agendaDue(now, zone, morningHour, lastDay)?.let { return it }
        val tomorrow = Instant.ofEpochMilli(now).atZone(zone).toLocalDate().plusDays(1)
        return tomorrow.atTime(morningHour, 0).atZone(zone).toInstant().toEpochMilli()
    }
}

/** The morning agenda: today's events and the tasks due today or overdue, in one quiet notification. */
data class Agenda(val title: String, val lines: List<String>, val more: Int, val events: Int, val tasks: Int)

object MorningAgenda {
    private const val LINES = 6

    fun build(events: List<CalendarEvent>, tasks: List<TaskItem>, day: LocalDate, zone: ZoneId, locale: Locale = Locale.getDefault()): Agenda? {
        val time = DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).withLocale(locale)
        val dayStart = day.atStartOfDay(zone).toInstant().toEpochMilli()
        val dayEnd = day.plusDays(1).atStartOfDay(zone).toInstant().toEpochMilli()
        val todays = events.filter { e ->
            if (e.cancelled || e.myStatus.equals("DECLINED", ignoreCase = true)) return@filter false
            val s = ReminderPlan.instant(e.start, zone) ?: return@filter false
            val end = ReminderPlan.eventEnd(e, zone) ?: s
            s < dayEnd && end > dayStart
        }.sortedWith(compareBy({ !it.allDay }, { ReminderPlan.instant(it.start, zone) }))
        val due = tasks.filter { !it.completed && it.due != null }.mapNotNull { t ->
            val at = ReminderPlan.instant(t.due!!, zone) ?: return@mapNotNull null
            if (at >= dayEnd) null else t to at
        }.sortedBy { it.second }
        if (todays.isEmpty() && due.isEmpty()) return null
        val lines = buildList {
            todays.forEach { e ->
                val s = ReminderPlan.instant(e.start, zone)!!
                val whenText = if (e.allDay) AppText.get(R.string.rem_all_day) else ZonedDateTime.ofInstant(Instant.ofEpochMilli(s), zone).format(time)
                val title = e.summary.ifBlank { AppText.get(R.string.rem_no_title) }
                val where = e.location?.takeIf { it.isNotBlank() }
                add(if (where != null) AppText.get(R.string.rem_agenda_line_where, whenText, title, where) else AppText.get(R.string.rem_agenda_line, whenText, title))
            }
            due.forEach { (t, at) ->
                val lead = if (at < dayStart) AppText.get(R.string.rem_agenda_overdue) else if (ReminderPlan.isDateOnly(t.due!!)) AppText.get(R.string.rem_due) else
                    AppText.get(R.string.rem_due_date, ZonedDateTime.ofInstant(Instant.ofEpochMilli(at), zone).format(time))
                add(AppText.get(R.string.rem_agenda_line, lead, t.summary.ifBlank { AppText.get(R.string.rem_untitled_task) }))
            }
        }
        val parts = listOfNotNull(
            todays.size.takeIf { it > 0 }?.let { AppText.plural(R.plurals.rem_agenda_count_events, it, it) },
            due.size.takeIf { it > 0 }?.let { AppText.plural(R.plurals.rem_agenda_count_tasks, it, it) },
        )
        return Agenda(AppText.get(R.string.rem_agenda_title, parts.joinToString(", ")), lines.take(LINES), (lines.size - LINES).coerceAtLeast(0), todays.size, due.size)
    }
}

/**
 * Remind again comes from the server (`repeat` in /reminders/settings) so one choice serves the
 * phone and the web. The phone still fires its repeats on its own alarm. An older
 * server without `repeat` leaves the phone's own choice. ACT reminder.repeat @android.notification
 */
object RemindAgain {
    val CHOICES = listOf(5, 10, 15, 30)

    fun effective(device: AlertPrefs, server: io.github.murtazanasir.mailr.data.api.RepeatSettings?): AlertPrefs =
        if (server == null) device
        else device.copy(remindAgain = server.minutes.takeIf { it in CHOICES } ?: 0,
            remindMax = server.max.coerceIn(1, ReminderAlerts.MAX_REPEATS))

    /**
     * The minutes to send up once: a choice made on this phone before the server had the setting,
     * while the server's is still off. Null: nothing to move.
     */
    fun migration(device: Int, server: io.github.murtazanasir.mailr.data.api.RepeatSettings?): Int? =
        if (server != null && !server.enabled && device in CHOICES) device else null
}
