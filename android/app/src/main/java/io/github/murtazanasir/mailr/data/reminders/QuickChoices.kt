package io.github.murtazanasir.mailr.data.reminders

import androidx.annotation.StringRes
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText
import java.time.DayOfWeek
import java.time.Duration
import java.time.LocalTime
import java.time.ZonedDateTime
import java.time.temporal.ChronoUnit
import java.time.temporal.TemporalAdjusters

/**
 * The smart presets and wheel values of the reminder actions. Pure, so the
 * sheet, the notification buttons and the tests agree on every option, its order and its label.
 */
data class Choice(val id: String, val label: String, val at: ZonedDateTime)

enum class SpanUnit(@StringRes private val labelRes: Int, val amounts: List<Int>) {
    // The amount and unit wheels: each unit covers its whole useful range on its own (1 to 59 minutes in steps,
    // 1 to 23 hours, 1 to 30 days, 1 to 8 weeks), so any amount is reachable without the unit.
    MINUTES(R.string.rem_unit_minutes, listOf(1, 2, 3, 4) + (5..55 step 5).toList()),
    HOURS(R.string.rem_unit_hours, (1..23).toList()),
    DAYS(R.string.rem_unit_days, (1..30).toList()),
    WEEKS(R.string.rem_unit_weeks, (1..8).toList());

    val label: String get() = AppText.get(labelRes)

    fun add(t: ZonedDateTime, n: Int): ZonedDateTime = when (this) {
        MINUTES -> t.plusMinutes(n.toLong())
        HOURS -> t.plusHours(n.toLong())
        DAYS -> t.plusDays(n.toLong())
        WEEKS -> t.plusWeeks(n.toLong())
    }

    /** The amount kept when the unit changes: [n] itself when this unit has it, else the closest (2 hours → 2 days; 45 minutes → 23 hours). */
    fun nearest(n: Int): Int = amounts.minBy { kotlin.math.abs(it - n) }
}

object QuickChoices {
    const val EVENING = 18
    const val TONIGHT = 20

    /** "now" rounded up to the next 5 minutes (seconds dropped). */
    fun now(t: ZonedDateTime): ZonedDateTime {
        val m = t.truncatedTo(ChronoUnit.MINUTES)
        val extra = (5 - m.minute % 5) % 5
        return m.plusMinutes(extra.toLong())
    }

    private fun morningOf(day: ZonedDateTime, hour: Int) = day.toLocalDate().atTime(hour, 0).atZone(day.zone)

    /** ACT task.snooze @android.sheet: 10 min, 1 hour, 3 hours, This evening (before 17:00), Tomorrow morning, Next week. */
    fun taskSnooze(t: ZonedDateTime, morningHour: Int = 9): List<Choice> {
        val out = mutableListOf(
            Choice("10m", AppText.plural(R.plurals.rem_minutes_short, 10, 10), t.plusMinutes(10).truncatedTo(ChronoUnit.MINUTES)),
            Choice("1h", AppText.plural(R.plurals.rem_hours, 1, 1), t.plusHours(1).truncatedTo(ChronoUnit.MINUTES)),
            Choice("3h", AppText.plural(R.plurals.rem_hours, 3, 3), t.plusHours(3).truncatedTo(ChronoUnit.MINUTES)),
        )
        if (t.toLocalTime().isBefore(LocalTime.of(17, 0))) out += Choice("evening", AppText.get(R.string.rem_this_evening), morningOf(t, EVENING))
        out += Choice("morning", AppText.get(R.string.rem_tomorrow_morning), morningOf(t.plusDays(1), morningHour))
        out += Choice("week", AppText.get(R.string.rem_next_week), morningOf(t.with(TemporalAdjusters.next(DayOfWeek.MONDAY)), morningHour))
        return out
    }

    /** ACT event.snooze @android.sheet: before the start 5 min, 10 min, At start time (> 2 min left); after it 5 and 15 min. */
    fun eventSnooze(t: ZonedDateTime, start: ZonedDateTime?): List<Choice> {
        val base = t.truncatedTo(ChronoUnit.MINUTES)
        if (start == null || !start.isAfter(t)) return listOf(
            Choice("5m", AppText.plural(R.plurals.rem_minutes_short, 5, 5), base.plusMinutes(5)), Choice("15m", AppText.plural(R.plurals.rem_minutes_short, 15, 15), base.plusMinutes(15)))
        val out = mutableListOf(Choice("5m", AppText.plural(R.plurals.rem_minutes_short, 5, 5), base.plusMinutes(5)), Choice("10m", AppText.plural(R.plurals.rem_minutes_short, 10, 10), base.plusMinutes(10)))
        if (Duration.between(t, start).toMinutes() >= 2) out += Choice("start", AppText.get(R.string.rem_when_it_starts), start)
        return out.filter { !it.at.isAfter(start) || it.id == "start" }.sortedBy { it.at }
    }

    /**
     * ACT task.reschedule @android.sheet presets, relative to now. [due] keeps its time of day ("keep the time");
     * a date-only task stays date-only unless a preset needs a time (Later today, Tonight).
     */
    fun taskReschedule(t: ZonedDateTime, due: ZonedDateTime?, dateOnly: Boolean, morningHour: Int = 9): List<Choice> {
        val time = if (due != null && !dateOnly) due.toLocalTime() else LocalTime.of(morningHour, 0)
        fun on(day: ZonedDateTime) = day.toLocalDate().atTime(time).atZone(t.zone)
        val out = mutableListOf<Choice>()
        if (t.hour < 18) out += Choice("later", AppText.get(R.string.rem_later_today), now(t.plusHours(3)))
        if (t.hour < TONIGHT) out += Choice("tonight", AppText.get(R.string.rem_tonight), morningOf(t, TONIGHT))
        out += Choice("tomorrow", AppText.get(R.string.rem_tomorrow), on(t.plusDays(1)))
        if (t.dayOfWeek in DayOfWeek.MONDAY..DayOfWeek.THURSDAY)
            out += Choice("weekend", AppText.get(R.string.rem_this_weekend), on(t.with(TemporalAdjusters.next(DayOfWeek.SATURDAY))))
        out += Choice("monday", AppText.get(R.string.rem_next_monday), on(t.with(TemporalAdjusters.next(DayOfWeek.MONDAY))))
        out += Choice("week", AppText.get(R.string.rem_in_a_week), on(t.plusWeeks(1)))
        return out.distinctBy { it.at }
    }

    /** ACT event.reschedule @android.sheet presets: +15 min, +1 hour, Tomorrow and Next week at the same time. */
    fun eventReschedule(start: ZonedDateTime): List<Choice> = listOf(
        Choice("15m", AppText.get(R.string.rem_plus_15_min), start.plusMinutes(15)),
        Choice("1h", AppText.get(R.string.rem_plus_1_hour), start.plusHours(1)),
        Choice("tomorrow", AppText.get(R.string.rem_tomorrow_same_time), start.plusDays(1)),
        Choice("week", AppText.get(R.string.rem_next_week_same_time), start.plusWeeks(1)),
    )

    val OFFSETS = listOf(0, 5, 10, 15, 30, 60, 120, 1440, 2880)

    fun offsetLabel(m: Int): String = when {
        m == 0 -> AppText.get(R.string.rem_offset_at_start)
        m < 60 -> AppText.plural(R.plurals.rem_offset_minutes_before, m, m)
        m < 1440 -> AppText.plural(R.plurals.rem_offset_hours_before, m / 60, m / 60)
        else -> AppText.plural(R.plurals.rem_offset_days_before, m / 1440, m / 1440)
    }

    /** ACT event.remind_before @android.sheet: only offsets still in the future (at least 30 s away). */
    fun offsets(t: ZonedDateTime, start: ZonedDateTime): List<Int> =
        OFFSETS.filter { m -> start.minusMinutes(m.toLong()).isAfter(t.plusSeconds(30)) }
}
