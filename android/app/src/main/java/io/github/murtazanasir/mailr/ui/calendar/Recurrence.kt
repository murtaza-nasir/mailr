package io.github.murtazanasir.mailr.ui.calendar

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.i18n.LocaleFormats
import java.time.DayOfWeek
import java.time.LocalDate
import java.time.format.DateTimeFormatter
import java.time.format.TextStyle
import java.util.Locale

enum class Freq { DAILY, WEEKLY, MONTHLY, YEARLY }

sealed interface RecurrenceEnd {
    data object Never : RecurrenceEnd
    data class Until(val date: LocalDate) : RecurrenceEnd
    data class Count(val n: Int) : RecurrenceEnd
}

/**
 * The subset of RFC 5545 RRULE the editor writes and reads: FREQ, INTERVAL, BYDAY (weekdays,
 * or one "nth weekday" for monthly), BYMONTHDAY, COUNT, UNTIL. Anything else is kept as raw
 * text ([raw]) so editing an event never rewrites a rule the app does not understand.
 */
data class Recurrence(
    val freq: Freq,
    val interval: Int = 1,
    val byDay: Set<DayOfWeek> = emptySet(),
    /** Monthly on the nth weekday (1..4, -1 = last), e.g. "2nd Tuesday". */
    val nth: Int? = null,
    val monthDay: Int? = null,
    val end: RecurrenceEnd = RecurrenceEnd.Never,
    val raw: String? = null,
) {
    fun toRRule(): String {
        raw?.let { return it }
        val parts = mutableListOf("FREQ=${freq.name}")
        if (interval > 1) parts += "INTERVAL=$interval"
        when {
            nth != null && byDay.size == 1 -> parts += "BYDAY=$nth${code(byDay.first())}"
            byDay.isNotEmpty() -> parts += "BYDAY=" + DayOfWeek.entries.filter { it in byDay }.joinToString(",") { code(it) }
        }
        monthDay?.let { parts += "BYMONTHDAY=$it" }
        when (val e = end) {
            is RecurrenceEnd.Count -> parts += "COUNT=${e.n}"
            is RecurrenceEnd.Until -> parts += "UNTIL=" + e.date.format(DateTimeFormatter.BASIC_ISO_DATE) + "T235959Z"
            RecurrenceEnd.Never -> Unit
        }
        return parts.joinToString(";")
    }

    /** "Every 2 weeks on Mon, Wed, until Dec 1" */
    fun describe(locale: Locale = Locale.getDefault()): String {
        if (raw != null) return AppText.get(R.string.cal_rec_custom_repeat)
        val base = when {
            freq == Freq.WEEKLY && interval == 1 && byDay == WEEKDAYS -> AppText.get(R.string.cal_rec_every_weekday)
            interval == 1 -> AppText.get(when (freq) {
                Freq.DAILY -> R.string.cal_rec_daily; Freq.WEEKLY -> R.string.cal_rec_weekly
                Freq.MONTHLY -> R.string.cal_rec_monthly; Freq.YEARLY -> R.string.cal_rec_yearly
            })
            else -> AppText.plural(when (freq) {
                Freq.DAILY -> R.plurals.cal_rec_every_days; Freq.WEEKLY -> R.plurals.cal_rec_every_weeks
                Freq.MONTHLY -> R.plurals.cal_rec_every_months; Freq.YEARLY -> R.plurals.cal_rec_every_years
            }, interval, interval)
        }
        val on = when {
            freq == Freq.WEEKLY && byDay.isNotEmpty() && byDay != WEEKDAYS ->
                AppText.get(R.string.cal_rec_on_days, base, DayOfWeek.entries.filter { it in byDay }.joinToString(", ") { it.getDisplayName(TextStyle.SHORT, locale) })
            freq == Freq.MONTHLY && nth != null && byDay.size == 1 ->
                AppText.get(R.string.cal_rec_on_nth, base, ordinal(nth), byDay.first().getDisplayName(TextStyle.FULL, locale))
            freq == Freq.MONTHLY && monthDay != null -> AppText.get(R.string.cal_rec_on_month_day, base, monthDay)
            else -> base
        }
        return when (val e = end) {
            is RecurrenceEnd.Count -> AppText.plural(R.plurals.cal_rec_count, e.n, on, e.n)
            is RecurrenceEnd.Until -> AppText.get(R.string.cal_rec_until, on, e.date.format(LocaleFormats.date("yMMMd", "MMM d, yyyy", locale)))
            RecurrenceEnd.Never -> on
        }
    }

    companion object {
        val WEEKDAYS = setOf(DayOfWeek.MONDAY, DayOfWeek.TUESDAY, DayOfWeek.WEDNESDAY, DayOfWeek.THURSDAY, DayOfWeek.FRIDAY)

        fun code(d: DayOfWeek) = d.name.take(2)

        private fun day(code: String): DayOfWeek? = DayOfWeek.entries.firstOrNull { it.name.startsWith(code.uppercase()) && code.length == 2 }

        fun ordinal(n: Int): String = when (n) {
            1 -> AppText.get(R.string.cal_rec_ordinal_first); 2 -> AppText.get(R.string.cal_rec_ordinal_second); 3 -> AppText.get(R.string.cal_rec_ordinal_third)
            4 -> AppText.get(R.string.cal_rec_ordinal_fourth); -1 -> AppText.get(R.string.cal_rec_ordinal_last); else -> AppText.get(R.string.cal_rec_ordinal_nth, n)
        }

        /** Parse what the editor can edit; anything else comes back with [raw] set. */
        fun parse(rrule: String?): Recurrence? {
            if (rrule.isNullOrBlank()) return null
            val text = rrule.removePrefix("RRULE:").trim()
            val kv = text.split(';').mapNotNull { p -> p.split('=', limit = 2).takeIf { it.size == 2 }?.let { it[0].uppercase() to it[1] } }.toMap()
            val freq = catching { Freq.valueOf(kv["FREQ"].orEmpty()) }.getOrNull() ?: return Recurrence(Freq.DAILY, raw = text)
            val known = setOf("FREQ", "INTERVAL", "BYDAY", "BYMONTHDAY", "COUNT", "UNTIL", "WKST")
            if (kv.keys.any { it !in known }) return Recurrence(freq, raw = text)
            var nth: Int? = null
            val days = kv["BYDAY"]?.split(',')?.mapNotNull { tok ->
                val m = Regex("^([+-]?\\d)?([A-Za-z]{2})$").find(tok.trim()) ?: return Recurrence(freq, raw = text)
                m.groupValues[1].takeIf { it.isNotEmpty() }?.let { nth = it.toInt() }
                day(m.groupValues[2])
            }?.toSet().orEmpty()
            if (nth != null && days.size != 1) return Recurrence(freq, raw = text)
            val end = when {
                kv["COUNT"] != null -> RecurrenceEnd.Count(kv["COUNT"]!!.toIntOrNull() ?: return Recurrence(freq, raw = text))
                kv["UNTIL"] != null -> catching {
                    RecurrenceEnd.Until(LocalDate.parse(kv["UNTIL"]!!.take(8), DateTimeFormatter.BASIC_ISO_DATE))
                }.getOrElse { return Recurrence(freq, raw = text) }
                else -> RecurrenceEnd.Never
            }
            return Recurrence(freq, kv["INTERVAL"]?.toIntOrNull()?.coerceAtLeast(1) ?: 1, days, nth,
                kv["BYMONTHDAY"]?.toIntOrNull(), end)
        }

        /** The one-tap choices for an event starting on [start]. */
        fun presets(start: LocalDate, locale: Locale = Locale.getDefault()): List<Pair<String, Recurrence?>> {
            val dow = start.dayOfWeek
            val nth = ((start.dayOfMonth - 1) / 7 + 1).let { if (start.plusWeeks(1).month != start.month) -1 else it }
            val day = dow.getDisplayName(TextStyle.FULL, locale)
            return listOf(
                AppText.get(R.string.cal_rec_does_not_repeat) to null,
                AppText.get(R.string.cal_rec_daily) to Recurrence(Freq.DAILY),
                AppText.get(R.string.cal_rec_preset_weekdays) to Recurrence(Freq.WEEKLY, byDay = WEEKDAYS),
                AppText.get(R.string.cal_rec_preset_weekly_on, day) to Recurrence(Freq.WEEKLY, byDay = setOf(dow)),
                AppText.get(R.string.cal_rec_preset_two_weeks_on, day) to Recurrence(Freq.WEEKLY, 2, setOf(dow)),
                AppText.get(R.string.cal_rec_preset_monthly_nth, ordinal(nth), day) to Recurrence(Freq.MONTHLY, byDay = setOf(dow), nth = nth),
                AppText.get(R.string.cal_rec_preset_monthly_day, start.dayOfMonth) to Recurrence(Freq.MONTHLY, monthDay = start.dayOfMonth),
                AppText.get(R.string.cal_rec_preset_yearly_on, start.format(LocaleFormats.date("MMMMd", "MMMM d", locale))) to Recurrence(Freq.YEARLY),
            )
        }
    }
}
