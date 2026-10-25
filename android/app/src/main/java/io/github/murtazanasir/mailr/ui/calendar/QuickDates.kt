package io.github.murtazanasir.mailr.ui.calendar

import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.reminders.QuickChoices
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.i18n.LocaleFormats
import java.time.DayOfWeek
import java.time.Duration
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.ZoneId
import java.time.temporal.TemporalAdjusters
import java.util.Locale

/** A preset chip: its label and the date(-time) it picks. `time == null` means date only. */
data class DatePreset(val label: String, val date: LocalDate, val time: LocalTime? = null)

data class ReminderOption(val minutes: Int, val label: String)

/**
 * Presets shared by the event and task sheets, defined once so every surface agrees
 * ("Tomorrow" is the same everywhere, "Next week" is next Monday, not +7 days).
 */
object QuickDates {
    /** A new event starts at the next half hour (never "14:37") and lasts an hour. */
    fun defaultEventStart(now: LocalDateTime): LocalDateTime {
        val base = now.withSecond(0).withNano(0)
        val add = (30 - base.minute % 30) % 30
        val next = base.plusMinutes(add.toLong())
        return if (add == 0) next.plusMinutes(30) else next
    }

    fun eventStartPresets(now: LocalDateTime, morning: Int = 9): List<DatePreset> {
        val today = now.toLocalDate()
        val inHour = now.plusHours(1).withMinute(0).withSecond(0).withNano(0)
        return buildList {
            add(DatePreset(AppText.get(R.string.cal_qd_in_1_hour), inHour.toLocalDate(), inHour.toLocalTime()))
            add(DatePreset(AppText.get(R.string.cal_qd_tomorrow_at, hourLabel(morning)), today.plusDays(1), LocalTime.of(morning, 0)))
            val mon = today.with(TemporalAdjusters.next(DayOfWeek.MONDAY))
            add(DatePreset(AppText.get(R.string.cal_qd_next_mon_at, hourLabel(morning)), mon, LocalTime.of(morning, 0)))
        }
    }

    val durations: List<Pair<Int, String>>
        get() = listOf(15, 30, 45, 60, 90, 120).map { it to if (it == 90) AppText.get(R.string.cal_qd_1_5_hr) else durationLabel(Duration.ofMinutes(it.toLong())) }

    fun taskDuePresets(today: LocalDate): List<DatePreset> = buildList {
        add(DatePreset(AppText.get(R.string.rem_day_today), today))
        add(DatePreset(AppText.get(R.string.rem_tomorrow), today.plusDays(1)))
        if (today.dayOfWeek !in setOf(DayOfWeek.FRIDAY, DayOfWeek.SATURDAY, DayOfWeek.SUNDAY))
            add(DatePreset(AppText.get(R.string.rem_this_weekend), today.with(TemporalAdjusters.next(DayOfWeek.SATURDAY))))
        add(DatePreset(AppText.get(R.string.rem_next_week), today.with(TemporalAdjusters.next(DayOfWeek.MONDAY))))
    }

    fun timePresets(now: LocalDateTime, date: LocalDate): List<Pair<String, LocalTime>> = buildList {
        if (date == now.toLocalDate()) {
            val t = now.plusHours(1).let { it.withMinute((it.minute / 5) * 5) }
            if (t.toLocalDate() == date) add(AppText.get(R.string.cal_qd_in_1_hour) to t.toLocalTime().withSecond(0).withNano(0))
        }
        listOf(AppText.get(R.string.cal_qd_morning) to LocalTime.of(9, 0), AppText.get(R.string.cal_qd_afternoon) to LocalTime.of(13, 0),
            AppText.get(R.string.cal_qd_evening) to LocalTime.of(18, 0))
            .forEach { (l, t) -> if (date != now.toLocalDate() || t.isAfter(now.toLocalTime())) add(l to t) }
    }

    val timedReminders: List<ReminderOption>
        get() = listOf(0, 5, 10, 15, 30, 60, 120, 1440, 10080).map { ReminderOption(it, beforeLabel(it)) }

    /** "At start", "5 min before", "1 hour before", "2 hours before", "1 day before", "1 week before". */
    private fun beforeLabel(m: Int): String = when {
        m == 0 -> AppText.get(R.string.rem_offset_at_start)
        m == 10080 -> AppText.get(R.string.cal_qd_1_week_before)
        else -> QuickChoices.offsetLabel(m)
    }

    /** All-day items: anchored at local midnight, so "9 am on the day" is -540. */
    fun allDayReminders(morning: Int = 9): List<ReminderOption> {
        val at = hourLabel(morning)
        return listOf(
            ReminderOption(-morning * 60, AppText.get(R.string.cal_qd_on_the_day_at, at)),
            ReminderOption(1440 - morning * 60, AppText.get(R.string.cal_qd_day_before_at, at)),
            ReminderOption(2 * 1440 - morning * 60, AppText.plural(R.plurals.cal_qd_days_before_at, 2, 2, at)),
            ReminderOption(7 * 1440 - morning * 60, AppText.get(R.string.cal_qd_week_before_at, at)),
        )
    }

    val taskReminders: List<ReminderOption>
        get() = listOf(ReminderOption(0, AppText.get(R.string.cal_qd_at_due_time))) +
            listOf(15, 60, 1440).map { ReminderOption(it, beforeLabel(it)) }

    fun reminderLabel(minutes: Int, allDay: Boolean, morning: Int = 9): String {
        (if (allDay) allDayReminders(morning) else timedReminders).firstOrNull { it.minutes == minutes }?.let { return it.label }
        if (allDay) {
            val days = Math.floorDiv(minutes + 1439, 1440)
            val at = LocalTime.MIDNIGHT.plusMinutes((days * 1440L - minutes).mod(1440L))
            return if (days == 0) AppText.get(R.string.cal_qd_on_the_day_at, timeLabel(at))
            else AppText.plural(R.plurals.cal_qd_days_before_at, days, days, timeLabel(at))
        }
        return when {
            minutes < 0 -> AppText.plural(R.plurals.cal_qd_min_after_start, -minutes, -minutes)
            minutes % 1440 == 0 -> AppText.plural(R.plurals.cal_qd_days_before, minutes / 1440, minutes / 1440)
            minutes % 60 == 0 -> AppText.plural(R.plurals.cal_qd_hours_before, minutes / 60, minutes / 60)
            else -> AppText.plural(R.plurals.rem_offset_minutes_before, minutes, minutes)
        }
    }

    /** English keeps the app's own short clock ("9 am", "9:30 pm"); another language takes its own. */
    private fun english(): Boolean = Locale.getDefault().language.let { it.isEmpty() || it == "en" }

    fun hourLabel(h: Int): String = if (!english()) LocalTime.of(h, 0).format(LocaleFormats.date("j", "h a")) else when {
        h == 0 -> "12 am" // i18n-ignore: English clock only (above)
        h < 12 -> "$h am" // i18n-ignore: English clock only (above)
        h == 12 -> "12 pm" // i18n-ignore: English clock only (above)
        else -> "${h - 12} pm" // i18n-ignore: English clock only (above)
    }

    fun timeLabel(t: LocalTime): String = if (t.minute == 0) hourLabel(t.hour)
        else if (!english()) t.format(LocaleFormats.date("jmm", "h:mm a"))
        else "${if (t.hour % 12 == 0) 12 else t.hour % 12}:${"%02d".format(t.minute)} ${if (t.hour < 12) "am" else "pm"}" // i18n-ignore: English clock only

    fun durationLabel(d: Duration): String {
        val m = d.toMinutes()
        return when {
            m < 60 -> AppText.plural(R.plurals.rem_span_minutes, m.toInt(), m)
            m % 60 == 0L -> AppText.plural(R.plurals.cal_qd_hr, (m / 60).toInt(), m / 60)
            else -> AppText.get(R.string.cal_qd_hr_min, m / 60, m % 60)
        }
    }
}

/** What quick-add understood: the title without the date words, and the date/time found. A task made from an email adds its notes. */
data class QuickParse(val title: String, val date: LocalDate? = null, val time: LocalTime? = null, val priority: Int = 0,
                      val notes: String = "")

/**
 * The original quick-add entry point, kept for its callers: it now runs the shared grammar
 * ([QuickAddGrammar], task mode) and reports only the date, time and priority it found, so words
 * of other kinds (#list, @tag, every …) stay in the title as before.
 */
object QuickAdd {
    fun parse(input: String, now: LocalDateTime): QuickParse {
        val r = QuickAddGrammar.parse(input, QuickAddOptions(QuickMode.TASK, now.atZone(ZoneId.systemDefault())))
        val taken = r.tokens.filter { it.kind == TokenKind.DATE || it.kind == TokenKind.TIME || it.kind == TokenKind.PRIORITY }
        val dated = taken.any { it.kind != TokenKind.PRIORITY }
        val title = QuickAddGrammar.buildTitle(input, taken)
        return QuickParse(title.ifBlank { input.trim() }, if (dated) r.due?.toLocalDate() else null,
            if (dated && !r.dueAllDay) r.due?.toLocalTime() else null, if (taken.any { it.kind == TokenKind.PRIORITY }) r.priority else 0)
    }
}
