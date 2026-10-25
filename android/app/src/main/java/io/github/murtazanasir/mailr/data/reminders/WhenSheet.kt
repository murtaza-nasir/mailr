package io.github.murtazanasir.mailr.data.reminders

import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.i18n.LocaleFormats
import java.time.DayOfWeek
import java.time.Duration
import java.time.LocalDate
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.time.temporal.ChronoUnit
import java.time.temporal.TemporalAdjusters
import java.util.Locale

/**
 * The when sheet: the preset
 * groups (Later, Same time, Next, Before it starts), the custom amount + unit with its base
 * (From now | From due time), and the live result line. Pure, so the sheet, the triage queue and
 * the tests agree. The web twin is frontend/src/lib/whenSheet.ts; both follow the same table.
 * ACT task.snooze @android.sheet, ACT task.reschedule @android.sheet, ACT event.snooze @android.sheet, ACT event.reschedule @android.sheet, ACT event.remind_before @android.sheet.
 */
object WhenSheet {
    enum class Verb { SNOOZE, RESCHEDULE, REMIND }
    enum class Base { NOW, DUE }

    /** A preset tile: its label, the moment it resolves to, and the offset for Remind before. */
    data class Tile(val id: String, val label: String, val at: ZonedDateTime, val dateOnly: Boolean = false, val minutes: Int? = null)

    data class Group(val id: String, val label: String, val tiles: List<Tile>)

    /**
     * What the sheet is for: the verb, a task or an event, now, the due or start ([anchor], null
     * for a task without a due), whether the due is date-only, and the morning hour setting.
     */
    data class Spec(val verb: Verb, val task: Boolean, val now: ZonedDateTime, val anchor: ZonedDateTime?,
                    val dateOnly: Boolean = false, val morningHour: Int = 9)

    const val EVENING = 18

    private fun at(day: ZonedDateTime, hour: Int, minute: Int = 0) = day.toLocalDate().atTime(hour, minute).atZone(day.zone)
    private fun at(day: LocalDate, hour: Int, minute: Int, s: ZonedDateTime) = day.atTime(hour, minute).atZone(s.zone)

    /** Leaves out tiles that have passed, tiles equal to an earlier one (across groups), and empty groups. */
    private fun tidy(groups: List<Group>, now: ZonedDateTime): List<Group> {
        val seen = mutableSetOf<String>()
        return groups.map { g ->
            g.copy(tiles = g.tiles.filter { t ->
                val past = if (t.dateOnly) t.at.toLocalDate().isBefore(now.toLocalDate()) else !t.at.isAfter(now)
                val key = if (t.dateOnly) "d${t.at.toLocalDate()}" else "t${t.at.toInstant()}"
                !past && seen.add(key)
            })
        }.filter { it.tiles.isNotEmpty() }
    }

    /** The preset groups for one verb. */
    fun groups(s: Spec): List<Group> {
        val now = s.now
        val base = now.truncatedTo(ChronoUnit.MINUTES)
        fun plus(m: Long) = Tile("${m}m", if (m < 60) AppText.plural(R.plurals.rem_in_minutes, m.toInt(), m) else AppText.plural(R.plurals.rem_in_hours, (m / 60).toInt(), m / 60), base.plusMinutes(m))
        val anchor = s.anchor

        if (s.verb == Verb.REMIND) {
            if (anchor == null) return emptyList()
            return listOf(Group("before", AppText.get(R.string.rem_group_before), QuickChoices.offsets(now, anchor).map { m ->
                Tile("o$m", QuickChoices.offsetLabel(m), anchor.minusMinutes(m.toLong()), minutes = m)
            }))
        }

        if (!s.task && s.verb == Verb.SNOOZE) {
            val tiles = if (anchor != null && anchor.isAfter(now)) buildList {
                add(plus(5)); add(plus(10))
                if (Duration.between(now, anchor).toMinutes() >= 2) add(Tile("start", AppText.get(R.string.rem_when_it_starts), anchor))
            } else listOf(plus(5), plus(15))
            return tidy(listOf(Group("later", AppText.get(R.string.rem_group_later), tiles)), now)
        }

        val evening = if (now.hour < 17) listOf(Tile("evening", AppText.get(R.string.rem_this_evening), at(now, EVENING))) else emptyList()
        val later = when {
            !s.task -> if (anchor != null) listOf(Tile("p15", AppText.get(R.string.rem_15_min_later), anchor.plusMinutes(15)),
                Tile("p60", AppText.get(R.string.rem_1_hour_later), anchor.plusHours(1))) else emptyList()
            s.verb == Verb.SNOOZE -> listOf(plus(10), plus(60), plus(180)) + evening
            else -> listOf(plus(60), plus(180)) + evening
        }

        // Same time: the original time of day, on days counted from today.
        val dateOnly = s.dateOnly && s.task
        val keepDate = dateOnly && s.verb == Verb.RESCHEDULE
        val same = if (anchor != null && (!dateOnly || keepDate)) {
            fun on(d: LocalDate) = if (keepDate) at(d, s.morningHour, 0, now) else at(d, anchor.hour, anchor.minute, now)
            val today = now.toLocalDate()
            listOf(Tile("same-tomorrow", AppText.get(R.string.rem_tomorrow), on(today.plusDays(1)), keepDate),
                Tile("same-week", AppText.get(R.string.rem_next_week), on(today.plusWeeks(1)), keepDate),
                Tile("same-month", AppText.get(R.string.rem_next_month), on(today.plusMonths(1)), keepDate))
        } else emptyList()

        val weekend = if (now.dayOfWeek == DayOfWeek.SATURDAY || now.dayOfWeek == DayOfWeek.SUNDAY) AppText.get(R.string.rem_next_weekend) else AppText.get(R.string.rem_this_weekend)
        val next = listOf(
            Tile("morning", AppText.get(R.string.rem_tomorrow_morning), at(now.plusDays(1), s.morningHour), keepDate),
            Tile("monday", AppText.get(R.string.rem_next_monday), at(now.with(TemporalAdjusters.next(DayOfWeek.MONDAY)), s.morningHour), keepDate),
            Tile("weekend", weekend, at(now.with(TemporalAdjusters.next(DayOfWeek.SATURDAY)), s.morningHour), keepDate),
        )
        return tidy(listOf(Group("later", AppText.get(R.string.rem_group_later), later), Group("same", AppText.get(R.string.rem_group_same_time), same), Group("next", AppText.get(R.string.rem_group_next), next)), now)
    }

    /** Whether the From now | From due time toggle shows. */
    fun hasBaseToggle(s: Spec): Boolean = s.anchor != null && s.verb != Verb.REMIND && !(!s.task && s.verb == Verb.SNOOZE)

    /** "From due time" for a task, "From start" for an event. */
    fun baseLabel(task: Boolean) = if (task) AppText.get(R.string.rem_base_from_due) else AppText.get(R.string.rem_base_from_start)

    /** The default base: now for a snooze, the due or start for a reschedule. */
    fun defaultBase(s: Spec): Base = if (hasBaseToggle(s) && s.verb == Verb.RESCHEDULE) Base.DUE else Base.NOW

    /** The wheels' start values: 15 min (snooze), 1 day (reschedule), 30 min before (remind). */
    fun initial(verb: Verb): Pair<Int, SpanUnit> = when (verb) {
        Verb.RESCHEDULE -> 1 to SpanUnit.DAYS
        Verb.REMIND -> 30 to SpanUnit.MINUTES
        Verb.SNOOZE -> 15 to SpanUnit.MINUTES
    }

    /**
     * The custom moment: [amount] [unit] after the base (now or the anchor), or before the start
     * for Remind before. Minutes and hours are exact; days and weeks keep the time of day. A
     * date-only task stays date-only for days and weeks.
     */
    fun custom(s: Spec, base: Base, amount: Int, unit: SpanUnit): Pair<ZonedDateTime, Boolean> {
        val anchor = s.anchor
        if (s.verb == Verb.REMIND && anchor != null) {
            return anchor.minus(Duration.between(s.now, unit.add(s.now, amount))) to false
        }
        val from = if (base == Base.DUE && anchor != null && hasBaseToggle(s)) anchor else s.now.truncatedTo(ChronoUnit.MINUTES)
        val day = unit == SpanUnit.DAYS || unit == SpanUnit.WEEKS
        return unit.add(from, amount) to (s.dateOnly && s.task && s.verb == Verb.RESCHEDULE && day)
    }

    /** Whether [at] has passed (a date-only moment passes at the end of its day). */
    fun past(at: ZonedDateTime, dateOnly: Boolean, now: ZonedDateTime): Boolean =
        if (dateOnly) at.toLocalDate().isBefore(now.toLocalDate()) else !at.isAfter(now)

    /** "in 8 days", "in 3 h 20 min", "2 days ago". */
    fun relative(at: ZonedDateTime, now: ZonedDateTime, dateOnly: Boolean = false): String {
        val days = ChronoUnit.DAYS.between(now.toLocalDate(), at.toLocalDate())
        // Whole minutes from the current minute: the custom result counts from now without its
        // seconds, so 12 hours reads "in 12 h", not "in 11 h 59 min".
        val mins = Duration.between(now.truncatedTo(ChronoUnit.MINUTES), at).toMinutes()
        if (dateOnly || kotlin.math.abs(mins) >= 24 * 60) return when {
            days == 0L -> AppText.get(R.string.rem_rel_today)
            days > 0 -> AppText.plural(R.plurals.rem_rel_in_days, days.toInt(), days)
            else -> AppText.plural(R.plurals.rem_rel_days_ago, (-days).toInt(), -days)
        }
        val a = kotlin.math.abs(mins)
        val text = when {
            a < 60 -> AppText.plural(R.plurals.rem_span_minutes, a.toInt(), a)
            a % 60 == 0L -> AppText.plural(R.plurals.rem_span_hours, (a / 60).toInt(), a / 60)
            else -> AppText.get(R.string.rem_span_hours_minutes, a / 60, a % 60)
        }
        return if (mins >= 0) AppText.get(R.string.rem_rel_in, text) else AppText.get(R.string.rem_rel_ago, text)
    }

    private val dayFmt get() = LocaleFormats.date("EEEdMMM", "EEE d MMM")
    private val timeFmt get() = DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).withLocale(Locale.getDefault())

    /** "Today", "Tomorrow" or "Fri 9 Oct". */
    fun day(at: ZonedDateTime, now: ZonedDateTime): String = when (at.toLocalDate()) {
        now.toLocalDate() -> AppText.get(R.string.rem_day_today)
        now.toLocalDate().plusDays(1) -> AppText.get(R.string.rem_day_tomorrow)
        else -> at.format(dayFmt)
    }

    /** A tile's second line: "3:40 PM" today, else "Tomorrow, 9:00 AM" / "Fri 9 Oct, 12:00 PM"; a date alone when date-only. */
    fun tileTime(t: Tile, now: ZonedDateTime): String = when {
        t.dateOnly -> day(t.at, now)
        t.at.toLocalDate() == now.toLocalDate() -> t.at.format(timeFmt)
        else -> AppText.get(R.string.rem_day_time, day(t.at, now), t.at.format(timeFmt))
    }

    /** The live result: "→ Fri 9 Oct, 12:00 PM · in 8 days". */
    fun resultLine(at: ZonedDateTime, now: ZonedDateTime, dateOnly: Boolean = false): String =
        AppText.get(R.string.rem_result_line, if (dateOnly) day(at, now) else AppText.get(R.string.rem_day_time, day(at, now), at.format(timeFmt)), relative(at, now, dateOnly))

    /** The result line of Remind before: "Reminds you tomorrow, 9:00 AM · in 18 h". */
    fun remindLine(at: ZonedDateTime, now: ZonedDateTime): String {
        val d = when (at.toLocalDate()) {
            now.toLocalDate() -> AppText.get(R.string.rem_due_day_today)
            now.toLocalDate().plusDays(1) -> AppText.get(R.string.rem_due_day_tomorrow)
            else -> at.format(dayFmt)
        }
        return AppText.get(R.string.rem_remind_line, AppText.get(R.string.rem_day_time, d, at.format(timeFmt)), relative(at, now))
    }

    /** The inline status badge for a due that has passed: "Overdue 4 h", "Overdue 25 min", "Overdue 2 d". */
    fun overdue(due: ZonedDateTime, now: ZonedDateTime, dateOnly: Boolean = false): String? {
        if (dateOnly) {
            val d = ChronoUnit.DAYS.between(due.toLocalDate(), now.toLocalDate())
            return if (d > 0) AppText.plural(R.plurals.rem_overdue_days, d.toInt(), d) else null
        }
        val m = Duration.between(due, now).toMinutes()
        return when {
            m < 1 -> null
            m < 60 -> AppText.plural(R.plurals.rem_overdue_minutes, m.toInt(), m)
            m < 24 * 60 -> AppText.plural(R.plurals.rem_overdue_hours, (m / 60).toInt(), m / 60)
            else -> AppText.plural(R.plurals.rem_overdue_days, (m / (24 * 60)).toInt(), m / (24 * 60))
        }
    }

    /** The task's when line without its status: "Due today 12:00 PM", "Due Fri 9 Oct". */
    fun dueLine(due: ZonedDateTime, now: ZonedDateTime, dateOnly: Boolean): String = AppText.get(R.string.rem_due_date, dueText(due, now, dateOnly))

    /** [dueLine] without "Due": "today 12:00 PM", "Fri 9 Oct". */
    fun dueText(due: ZonedDateTime, now: ZonedDateTime, dateOnly: Boolean): String {
        val d = when (due.toLocalDate()) {
            now.toLocalDate() -> AppText.get(R.string.rem_due_day_today)
            now.toLocalDate().plusDays(1) -> AppText.get(R.string.rem_due_day_tomorrow)
            else -> due.format(dayFmt)
        }
        return if (dateOnly) d else AppText.get(R.string.rem_due_day_time, d, due.format(timeFmt))
    }

    /** What the verb changes, under the header. */
    fun hint(s: Spec, anchorText: String?): String = when {
        s.verb == Verb.REMIND -> AppText.get(R.string.rem_hint_remind)
        s.verb == Verb.SNOOZE && !s.task -> AppText.get(R.string.rem_hint_snooze_event)
        s.verb == Verb.SNOOZE -> if (anchorText != null) AppText.get(R.string.rem_hint_snooze_task_due, anchorText) else AppText.get(R.string.rem_hint_snooze_task)
        s.task -> AppText.get(R.string.rem_hint_reschedule_task)
        else -> AppText.get(R.string.rem_hint_reschedule_event)
    }
}
