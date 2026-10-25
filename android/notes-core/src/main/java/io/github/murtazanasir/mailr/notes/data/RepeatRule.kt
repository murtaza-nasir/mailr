package io.github.murtazanasir.mailr.notes.data

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.i18n.LocaleFormats
import io.github.murtazanasir.mailr.notes.R
import io.github.murtazanasir.mailr.i18n.AppText
import java.time.DayOfWeek
import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneId
import java.time.format.TextStyle
import java.util.Locale

/**
 * A list's repeat rule (the server's twin is backend/mailr/notes/repeat.py):
 * `FREQ=WEEKLY;BYDAY=SU` (optionally `;INTERVAL=2` weeks) or `FREQ=DAILY;INTERVAL=3`. The phone
 * reads it for the repeat sheet and the quiet line under the title, and plans the first reset the
 * way the server does, so a repeat set offline shows its next date at once.
 */
object RepeatRule {
    sealed interface Rule
    data class Weekly(val day: DayOfWeek, val interval: Int = 1) : Rule
    data class Daily(val interval: Int) : Rule

    private val DAYS = listOf("MO", "TU", "WE", "TH", "FR", "SA", "SU")
    private val RESET_AT: LocalTime = LocalTime.of(4, 0)

    fun code(day: DayOfWeek): String = DAYS[day.value - 1]

    /** The rule, or null for an empty or unreadable one. */
    fun parse(rule: String?): Rule? {
        val raw = rule?.trim()?.removePrefix("RRULE:")?.takeIf { it.isNotEmpty() } ?: return null
        val parts = raw.split(';').mapNotNull { p ->
            val k = p.substringBefore('=', "").trim().uppercase(Locale.ROOT)
            if (k.isEmpty()) null else k to p.substringAfter('=').trim().uppercase(Locale.ROOT)
        }.toMap()
        val interval = parts["INTERVAL"]?.toIntOrNull() ?: 1
        if (interval < 1) return null
        return when (parts["FREQ"]) {
            "WEEKLY" -> DAYS.indexOf(parts["BYDAY"]).takeIf { it >= 0 }?.let { Weekly(DayOfWeek.of(it + 1), interval) }
            "DAILY" -> Daily(interval)
            else -> null
        }
    }

    /** The rule in the server's one spelling. */
    fun format(r: Rule): String = when (r) {
        is Weekly -> "FREQ=WEEKLY;BYDAY=${code(r.day)}" + if (r.interval > 1) ";INTERVAL=${r.interval}" else ""
        is Daily -> "FREQ=DAILY;INTERVAL=${r.interval}"
    }

    /** "Repeats weekly on Sunday", "Repeats every 2 weeks on Sunday", "Repeats every 3 days", "Repeats every day". */
    fun describe(r: Rule): String = when (r) {
        is Weekly -> {
            val day = r.day.getDisplayName(TextStyle.FULL, Locale.getDefault())
            if (r.interval == 1) AppText.get(R.string.notes_repeats_weekly_one, day) else AppText.plural(R.plurals.notes_repeats_weekly, r.interval, r.interval, day)
        }
        is Daily -> if (r.interval == 1) AppText.get(R.string.notes_repeats_daily_one) else AppText.plural(R.plurals.notes_repeats_daily, r.interval, r.interval)
    }

    /** The ⋮ menu's "Repeat: weekly on Sunday", "Repeat: every 3 days". */
    fun menuLabel(r: Rule): String = AppText.get(R.string.notes_repeat_menu, when (r) {
        is Weekly -> r.day.getDisplayName(TextStyle.FULL, Locale.getDefault()).let { day ->
            if (r.interval == 1) AppText.get(R.string.notes_repeat_menu_weekly_one, day)
            else AppText.plural(R.plurals.notes_repeat_menu_weekly, r.interval, r.interval, day)
        }
        is Daily -> if (r.interval == 1) AppText.get(R.string.notes_repeat_menu_daily_one)
            else AppText.plural(R.plurals.notes_repeat_menu_daily, r.interval, r.interval)
    })

    /** The quiet line under a repeating list's title: "Repeats weekly on Sunday · next Sun 11 Oct". */
    fun line(repeat: NoteRepeat?, zone: ZoneId = ZoneId.systemDefault()): String? {
        val r = parse(repeat?.rule) ?: return null
        val next = repeat?.next?.let { catching { Instant.parse(it) }.getOrNull() ?: catching { java.time.OffsetDateTime.parse(it).toInstant() }.getOrNull() }
        val day = next?.atZone(zone)?.format(LocaleFormats.date("EEEdMMM", "EEE d MMM"))
        return day?.let { AppText.get(R.string.notes_repeat_line_next, describe(r), it) } ?: describe(r)
    }

    /** The first reset of a rule set at [now]: the next of its weekday at 04:00 (never today), or N days from today at 04:00. */
    fun first(r: Rule, now: Instant, zone: ZoneId = ZoneId.systemDefault()): Instant {
        val today = now.atZone(zone).toLocalDate()
        val d: LocalDate = when (r) {
            is Daily -> today.plusDays(r.interval.toLong())
            is Weekly -> {
                val ahead = ((r.day.value - today.dayOfWeek.value) % 7 + 7) % 7
                today.plusDays(if (ahead == 0) 7L else ahead.toLong())
            }
        }
        return d.atTime(RESET_AT).atZone(zone).toInstant()
    }
}
