package io.github.murtazanasir.mailr.ui.calendar

import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZonedDateTime
import java.time.temporal.ChronoUnit

/**
 * Unicode whitespace for character classes, spelled out: Android's ICU regex engine rejects the
 * Java inline "U" flag (it threw PatternSyntaxException on the first keystroke in quick add), and a
 * bare `\\s` on the JVM misses non-breaking and other Unicode spaces.
 */
internal const val WS = "\\s\\u00A0\\u1680\\u2000-\\u200A\\u2028\\u2029\\u202F\\u205F\\u3000\\uFEFF"

// The quick-add grammar. The web twin is
// frontend/src/lib/quickadd.ts; both run the vectors in spec/cases/quickadd-cases.yaml.
// No model is involved: a word is taken only when a rule below matches it exactly, and every taken word
// comes back as a token with its span so the input can highlight it and turn it back into text.

enum class QuickMode { EVENT, TASK }

enum class TokenKind { DATE, TIME, DURATION, SPAN, REPEAT, START, REMINDER, PRIORITY, TAG, COLLECTION, LOCATION, GUEST, VIDEO }

/** A taken stretch of the input: UTF-16 offsets, end exclusive. */
data class QuickToken(val kind: TokenKind, val start: Int, val end: Int, val text: String)

data class QuickCollection(val id: String, val name: String)

data class QuickPerson(val name: String, val email: String)

/** A guest suggestion. Only a typed email address is confirmed; a name waits for the user. */
data class QuickGuest(val text: String, val email: String?, val confirmed: Boolean, val matches: List<QuickPerson>)

/** Minutes before the start or due (negative = after; all-day items count from local midnight), and/or an absolute time. */
data class QuickReminder(val minutes: Int?, val at: LocalDateTime?)

data class QuickListRef(val id: String?, val name: String, val create: Boolean)

data class QuickAddOptions(
    val mode: QuickMode,
    val now: ZonedDateTime,
    val lists: List<QuickCollection> = emptyList(),
    val calendars: List<QuickCollection> = emptyList(),
    val people: List<QuickPerson> = emptyList(),
    /** Known past locations, matched after "at", "@" or "in" whatever their case. */
    val places: List<String> = emptyList(),
    /** How "12/10" reads (the date-format setting): false = month first. */
    val dayFirst: Boolean = false,
    val defaultDuration: Int = 60,
    val morningHour: Int = 9,
    val afternoonHour: Int = 14,
    val eveningHour: Int = 18,
    /** Token texts the user turned back into title text ("Keep as text"); compared case-insensitively. */
    val keep: Set<String> = emptySet(),
)

/**
 * What quick add understood. Times are local wall times in the zone of `now`. Events: `start`/`end`,
 * with `allDay` meaning both are midnights and `end` is exclusive. Tasks: `due` (midnight when
 * `dueAllDay`) and `startDate`. `repeatFrom` is "due" or "completion" for a task series.
 */
data class QuickAddResult(
    val title: String,
    val tokens: List<QuickToken>,
    val start: LocalDateTime? = null,
    val end: LocalDateTime? = null,
    val allDay: Boolean = false,
    val duration: Int? = null,
    val due: LocalDateTime? = null,
    val dueAllDay: Boolean = false,
    val startDate: LocalDate? = null,
    val rrule: String? = null,
    val repeatFrom: String? = null,
    val location: String? = null,
    val guests: List<QuickGuest> = emptyList(),
    val listOrCalendar: QuickListRef? = null,
    val tags: List<String> = emptyList(),
    /** iCalendar priority: 1 high, 5 medium, 9 low, 0 none. */
    val priority: Int = 0,
    val reminders: List<QuickReminder> = emptyList(),
    val video: Boolean = false,
)

object QuickAddGrammar {
    fun parse(text: String, opts: QuickAddOptions): QuickAddResult = QaParser(text, opts).run()

    /**
     * The input without the given token spans, tidied: one space between words, no space left before
     * punctuation that followed a removed span, no doubled comma, no stray separators at the ends.
     */
    fun buildTitle(text: String, remove: List<QuickToken>): String {
        val cut = "\u0000"
        val s = StringBuilder()
        var at = 0
        for (t in remove.sortedBy { it.start }) {
            s.append(text, at, t.start).append(cut)
            at = t.end
        }
        s.append(text.substring(at))
        return s.toString()
            .replace(Regex("[$WS]*\u0000[$WS\u0000]*"), cut)
            .replace(",\u0000,", ",")
            .replace(Regex("\u0000(?=[,.;:!?])"), "")
            .replace(cut, " ")
            .replace(Regex("[$WS]+"), " ")
            .trim()
            .replace(Regex("^[$WS,;:\\-–—]+"), "")
            .replace(Regex("[$WS,;:\\-–—.]+\$"), "")
    }

    internal val WEEKDAYS = mapOf(
        "monday" to 1, "mon" to 1, "tuesday" to 2, "tue" to 2, "tues" to 2, "wednesday" to 3, "wed" to 3, "weds" to 3, // i18n-ignore: parser keyword
        "thursday" to 4, "thu" to 4, "thur" to 4, "thurs" to 4, "friday" to 5, "fri" to 5, "saturday" to 6, "sat" to 6, // i18n-ignore: parser keyword
        "sunday" to 7, "sun" to 7, // i18n-ignore: parser keyword
    )
    internal val MONTHS = mapOf(
        "january" to 1, "jan" to 1, "february" to 2, "feb" to 2, "march" to 3, "mar" to 3, "april" to 4, "apr" to 4, // i18n-ignore: parser keyword
        "may" to 5, "june" to 6, "jun" to 6, "july" to 7, "jul" to 7, "august" to 8, "aug" to 8, "september" to 9, // i18n-ignore: parser keyword
        "sep" to 9, "sept" to 9, "october" to 10, "oct" to 10, "november" to 11, "nov" to 11, "december" to 12, "dec" to 12, // i18n-ignore: parser keyword
    )
    internal val BYDAY = listOf("", "MO", "TU", "WE", "TH", "FR", "SA", "SU")
    internal val NTH = mapOf("first" to 1, "1st" to 1, "second" to 2, "2nd" to 2, "third" to 3, "3rd" to 3, "fourth" to 4, "4th" to 4, "last" to -1) // i18n-ignore: parser keyword
    internal val LOCATION_LINKS = setOf("of", "the", "de", "la", "le", "du", "del", "da", "des", "&") // i18n-ignore: parser keyword
    internal val UPPER = Regex("^\\p{Lu}")
    internal val EMAIL = Regex("^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$")
    internal val MERIDIEM_WORD = Regex("^(am|pm|a\\.m|p\\.m)$")
    internal val CLOCK = Regex("^(\\d{1,2})(?::(\\d{2}))?(am|pm|a|p|a\\.m|p\\.m)?$")
}

private enum class QaFreq { DAILY, WEEKLY, MONTHLY, YEARLY }

private data class QaRule(
    val freq: QaFreq, val interval: Int, var byday: List<Int>? = null, var nth: Int? = null, var nthDay: Int? = null,
    var monthday: Int? = null,
) {
    fun text(): String {
        val s = StringBuilder("FREQ=$freq")
        if (interval > 1) s.append(";INTERVAL=$interval")
        val n = nth
        val nd = nthDay
        if (n != null && nd != null) s.append(";BYDAY=$n${QuickAddGrammar.BYDAY[nd]}")
        else byday?.let { d -> s.append(";BYDAY=").append(d.sorted().joinToString(",") { QuickAddGrammar.BYDAY[it] }) }
        monthday?.let { s.append(";BYMONTHDAY=$it") }
        return s.toString()
    }
}

private class QaWord(val start: Int, val raw: String, val core: String, val prot: Boolean) {
    val end = start + core.length
    val lc = core.lowercase()
    var used = false
}

private data class QaDateHit(
    val n: Int, val day: LocalDate, val time: Int? = null, val tonight: Boolean = false, val positional: Boolean = false,
    val weekday: Int? = null, val fillsTime: Boolean = false,
)

private data class QaClock(val n: Int, val min: Int, val mer: Boolean, val h: Int)

private class QaPart(val h: Int, val m: Int, var mer: Char?, val colon: Boolean, val zero: Boolean = false)

private data class QaAmount(val n: Int, val value: Int, val unit: String)

private class QaParser(val text: String, val opts: QuickAddOptions) {
    private val g = QuickAddGrammar
    private val event = opts.mode == QuickMode.EVENT
    private val nowWall: LocalDateTime = opts.now.toLocalDateTime().truncatedTo(ChronoUnit.MINUTES)
    private val today: LocalDate = nowWall.toLocalDate()
    private val nowMin = nowWall.hour * 60 + nowWall.minute
    private val dayparts = mapOf(
        "morning" to opts.morningHour * 60, "afternoon" to opts.afternoonHour * 60, // i18n-ignore: parser keyword
        "evening" to opts.eveningHour * 60, "night" to 20 * 60, // i18n-ignore: parser keyword
    )
    private val keep = opts.keep.map { it.lowercase() }.toSet()
    private val words: List<QaWord>
    private val n: Int
    private val tokens = mutableListOf<Pair<QuickToken, Boolean>>()

    init {
        val prot = mutableListOf<IntRange>()
        var i = 0
        while (i < text.length) {
            val c = text[i]
            if (c == '"' || c == '“') {
                val close = text.indexOf(if (c == '"') '"' else '”', i + 1)
                val end = if (close < 0) text.length else close + 1
                prot += i until end
                i = end
                continue
            }
            i++
        }
        words = Regex("[^$WS]+").findAll(text).map { m ->
            val raw = m.value
            val start = m.range.first
            val core = raw.replace(Regex("[,.;:]+$"), "").ifEmpty { raw }
            QaWord(start, raw, core, prot.any { r -> start <= r.last && start + raw.length > r.first })
        }.toList()
        n = words.size
    }

    private fun free(i: Int) = i in 0 until n && !words[i].prot && !words[i].used
    private fun lc(i: Int): String? = if (free(i)) words[i].lc else null
    private fun core(i: Int): String? = if (free(i)) words[i].core else null
    private fun punct(i: Int) = words[i].raw != words[i].core

    private fun take(i: Int, k: Int, kind: TokenKind, keepText: Boolean = false): Boolean {
        val start = words[i].start
        val end = words[i + k - 1].end
        val t = text.substring(start, end)
        if (t.lowercase() in keep) return false
        for (j in i until i + k) words[j].used = true
        tokens += QuickToken(kind, start, end, t) to keepText
        return true
    }

    /** The first free position where `f` matches and the take succeeds. */
    private inline fun scan(f: (Int) -> Boolean) {
        for (i in 0 until n) if (free(i) && f(i)) return
    }

    private fun dow(d: LocalDate) = d.dayOfWeek.value

    private fun nextStrict(wd: Int): LocalDate = today.plusDays((((wd - dow(today) + 7) % 7).takeIf { it != 0 } ?: 7).toLong())

    private fun validDate(y: Int, m: Int, d: Int): LocalDate? =
        if (m !in 1..12 || d < 1 || d > java.time.YearMonth.of(y, m).lengthOfMonth()) null else LocalDate.of(y, m, d)

    /** A month and day without a year: the next one on or after today. */
    private fun inferMonthDay(m: Int, d: Int): LocalDate? {
        for (k in 0 until 8) {
            val z = validDate(today.year + k, m, d)
            if (z != null && !z.isBefore(today)) return z
        }
        return null
    }

    private fun dayNum(s: String?): Int? {
        val m = s?.let { Regex("^(\\d{1,2})(st|nd|rd|th)?$").find(it) } ?: return null
        return m.groupValues[1].toInt().takeIf { it in 1..31 }
    }

    private fun ordinal(s: String?): Int? {
        val m = s?.let { Regex("^(\\d{1,2})(st|nd|rd|th)$").find(it) } ?: return null
        return m.groupValues[1].toInt().takeIf { it in 1..31 }
    }

    private fun yearAt(i: Int): Int? = lc(i)?.takeIf { Regex("^20\\d\\d$").matches(it) }?.toInt()

    /** A date expression at i, without prefix words. */
    fun dateAt(i: Int): QaDateHit? {
        val hit = dateCore(i) ?: return null
        if (hit.fillsTime) return hit
        val dp = lc(i + hit.n)
        if (dp != null && dp in dayparts && hit.time == null) return hit.copy(n = hit.n + 1, time = dayparts.getValue(dp))
        return hit
    }

    private fun dateCore(i: Int): QaDateHit? {
        val a = lc(i) ?: return null
        val b = lc(i + 1)
        when {
            a == "today" -> return QaDateHit(1, today)
            a == "tonight" -> return QaDateHit(1, today, time = 20 * 60, tonight = true)
            a == "tomorrow" || a == "tmrw" || a == "tmr" || words[i].core == "tom" -> return QaDateHit(1, today.plusDays(1))
            a == "day" && b == "after" && lc(i + 2) == "tomorrow" -> return QaDateHit(3, today.plusDays(2))
            a == "the" && b == "day" && lc(i + 2) == "after" && lc(i + 3) == "tomorrow" -> return QaDateHit(4, today.plusDays(2))
        }
        if (a == "this" || a == "next") {
            if (b != null && b in g.WEEKDAYS) {
                val wd = g.WEEKDAYS.getValue(b)
                if (a == "this") return QaDateHit(2, today.plusDays(((wd - dow(today) + 7) % 7).toLong()), weekday = wd)
                val d = nextStrict(wd)
                return QaDateHit(2, if (d.isBefore(today.plusDays(7))) d.plusDays(7) else d, weekday = wd)
            }
            if (b == "weekend") {
                val sat = today.plusDays(((6 - dow(today) + 7) % 7).toLong())
                return QaDateHit(2, if (a == "this") sat else sat.plusDays(7))
            }
            if (a == "next" && b == "week") return QaDateHit(2, nextStrict(1))
            if (a == "next" && b == "month") return QaDateHit(2, today.plusMonths(1).withDayOfMonth(1))
            if (a == "this" && b != null && b in dayparts && b != "night") return QaDateHit(2, today, time = dayparts.getValue(b))
            return null
        }
        if (a == "eom" || (a == "end" && b == "of" && (lc(i + 2) == "month" || (lc(i + 2) == "the" && lc(i + 3) == "month")))) {
            val len = if (a == "eom") 1 else if (lc(i + 2) == "the") 4 else 3
            return QaDateHit(len, today.withDayOfMonth(today.lengthOfMonth()))
        }
        if (a == "in") return relAt(i + 1)?.let { it.copy(n = it.n + 1) }
        Regex("^\\+(\\d{1,3})([dw])$").find(a)?.let { m ->
            return QaDateHit(1, today.plusDays(m.groupValues[1].toLong() * if (m.groupValues[2] == "w") 7 else 1))
        }
        if (a in g.WEEKDAYS) {
            val wd = g.WEEKDAYS.getValue(a)
            return QaDateHit(1, nextStrict(wd), positional = true, weekday = wd)
        }
        Regex("^(\\d{4})-(\\d{2})-(\\d{2})$").find(a)?.let { m ->
            val z = validDate(m.groupValues[1].toInt(), m.groupValues[2].toInt(), m.groupValues[3].toInt()) ?: return null
            return QaDateHit(1, z)
        }
        Regex("^(\\d{1,2})/(\\d{1,2})(?:/(\\d{4}|\\d{2}))?$").find(a)?.let { m ->
            val x = m.groupValues[1].toInt()
            val y = m.groupValues[2].toInt()
            val (mo, d) = if (opts.dayFirst) y to x else x to y
            val yr = m.groupValues[3]
            if (yr.isNotEmpty()) {
                val z = validDate(if (yr.length == 2) 2000 + yr.toInt() else yr.toInt(), mo, d) ?: return null
                return QaDateHit(1, z)
            }
            if (mo !in 1..12 || d !in 1..31) return null
            val z = inferMonthDay(mo, d) ?: return null
            return QaDateHit(1, z, positional = true)
        }
        // oct 12 [2027]
        if (a in g.MONTHS) {
            val d = dayNum(b) ?: return null
            val yr = yearAt(i + 2)
            val mo = g.MONTHS.getValue(a)
            val z = (if (yr != null) validDate(yr, mo, d) else inferMonthDay(mo, d)) ?: return null
            return QaDateHit(if (yr != null) 3 else 2, z)
        }
        // 12 oct [2027], 12th of oct
        val d = dayNum(a)
        if (d != null) {
            val k = if (ordinal(a) != null && b == "of") 2 else 1
            val mw = lc(i + k)
            if (mw == null || mw !in g.MONTHS) return null
            val yr = yearAt(i + k + 1)
            val mo = g.MONTHS.getValue(mw)
            val z = (if (yr != null) validDate(yr, mo, d) else inferMonthDay(mo, d)) ?: return null
            return QaDateHit(k + 1 + if (yr != null) 1 else 0, z)
        }
        // the 12th [of oct]
        if (a == "the") {
            val od = ordinal(b) ?: return null
            val m3 = lc(i + 3)
            if (lc(i + 2) == "of" && m3 != null && m3 in g.MONTHS) {
                val z = inferMonthDay(g.MONTHS.getValue(m3), od) ?: return null
                return QaDateHit(4, z)
            }
            val first = today.withDayOfMonth(1)
            for (k in 0 until 12) {
                val f = first.plusMonths(k.toLong())
                val z = validDate(f.year, f.monthValue, od)
                if (z != null && !z.isBefore(today)) return QaDateHit(2, z)
            }
        }
        return null
    }

    /** "3 days", "2h", "an hour", "half an hour" after "in". */
    private fun relAt(i: Int): QaDateHit? {
        val d = amountAt(i, true) ?: return null
        return when (d.unit) {
            "min" -> {
                val t = opts.now.plusMinutes(d.value.toLong()).toLocalDateTime().truncatedTo(ChronoUnit.MINUTES)
                QaDateHit(d.n, t.toLocalDate(), time = t.hour * 60 + t.minute, fillsTime = true)
            }
            "month" -> QaDateHit(d.n, today.plusMonths(d.value.toLong()))
            else -> QaDateHit(d.n, today.plusDays(d.value.toLong()))
        }
    }

    private fun clockUnit(u: String?): Int? = when {
        u == null -> null
        Regex("^(m|min|mins|minute|minutes)$").matches(u) -> 1
        Regex("^(h|hr|hrs|hour|hours)$").matches(u) -> 60
        else -> null
    }

    /**
     * An amount of time: "45 min", "2h", "1.5 hours", "1h30", "an hour", "half an hour", "1 hour 30 min",
     * and with `days`, also "3 days", "2 weeks", "1 month". Minutes for clock units, else the count.
     */
    fun amountAt(i: Int, days: Boolean): QaAmount? {
        val a = lc(i) ?: return null
        fun dayUnit(u: String?): Pair<Int, String>? = when {
            !days || u == null -> null
            Regex("^(d|day|days)$").matches(u) -> 1 to "day"
            Regex("^(w|wk|wks|week|weeks)$").matches(u) -> 7 to "day"
            Regex("^(month|months)$").matches(u) -> 1 to "month"
            else -> null
        }
        if (a == "half" && lc(i + 1) == "an" && clockUnit(lc(i + 2)) == 60) return QaAmount(3, 30, "min")
        if (a == "an" || a == "a") {
            if (clockUnit(lc(i + 1)) == 60) return QaAmount(2, 60, "min")
            val du = dayUnit(lc(i + 1)) ?: return null
            return QaAmount(2, du.first, du.second)
        }
        Regex("^(\\d{1,2})h(\\d{1,2})m?$").find(a)?.let { m ->
            return QaAmount(1, m.groupValues[1].toInt() * 60 + m.groupValues[2].toInt(), "min")
        }
        val m = Regex("^(\\d+(?:\\.\\d+)?)([a-z]+)$").find(a)
        val num: Double
        var k: Int
        val unitWord: String?
        if (m != null) {
            num = m.groupValues[1].toDouble()
            unitWord = m.groupValues[2]
            k = 1
        } else if (Regex("^\\d+(?:\\.\\d+)?$").matches(a)) {
            num = a.toDouble()
            unitWord = lc(i + 1)
            k = 2
        } else return null
        val cu = clockUnit(unitWord)
        val whole = num == Math.floor(num)
        if (cu != null) {
            var value = Math.round(num * cu).toInt()
            // "1 hour 30 min"
            if (cu == 60 && whole) {
                val more = lc(i + k)
                val mm = more?.let { Regex("^(\\d{1,2})(m|min|mins|minutes?)?$").find(it) }
                if (mm != null && mm.groupValues[2].isNotEmpty()) {
                    value += mm.groupValues[1].toInt()
                    k += 1
                } else if (mm != null && clockUnit(lc(i + k + 1)) == 1) {
                    value += mm.groupValues[1].toInt()
                    k += 2
                }
            }
            return if (value > 0) QaAmount(k, value, "min") else null
        }
        val du = dayUnit(unitWord)
        if (du != null && whole && num > 0) return QaAmount(k, num.toInt() * du.first, du.second)
        return null
    }

    /** A clock time at i: "3pm", "3 pm", "3:30p", "15:30", "noon", "midnight", and a bare hour when `bare`. */
    fun clockAt(i: Int, bare: Boolean): QaClock? {
        var a = lc(i) ?: return null
        if (a == "noon") return QaClock(1, 720, true, 12)
        if (a == "midnight") return QaClock(1, 0, true, 0)
        if (!bare && Regex("^@\\d").containsMatchIn(a)) a = a.substring(1)
        val m = g.CLOCK.find(a) ?: return null
        val h = m.groupValues[1].toInt()
        val min = m.groupValues[2].ifEmpty { "0" }.toInt()
        if (min > 59) return null
        var mer: Char? = m.groupValues[3].firstOrNull()
        var k = 1
        if (mer == null) {
            val nx = lc(i + 1)
            if (nx != null && g.MERIDIEM_WORD.matches(nx)) {
                mer = nx[0]
                k = 2
            }
        }
        if (mer != null) {
            if (h !in 1..12) return null
            return QaClock(k, ((h % 12) + if (mer == 'p') 12 else 0) * 60 + min, true, h)
        }
        if (m.groupValues[2].isEmpty() && !bare) return null
        if (h > 23) return null
        // A bare 1 to 6 is an afternoon hour ("at 3" = 15:00); "03:00" and 7 to 23 stay as written.
        val pm = if (h in 1..6 && !m.groupValues[1].startsWith("0")) 720 else 0
        return QaClock(1, h * 60 + min + pm, false, h)
    }

    /** A time with its optional prefix: "at 9", "@ 9:30", "by 5pm", "in the morning". */
    fun timeAt(i: Int): QaClock? {
        val a = lc(i)
        val dp = lc(i + 2)
        if (a == "in" && lc(i + 1) == "the" && dp != null && dp in dayparts && dp != "night") {
            return QaClock(3, dayparts.getValue(dp), true, 0)
        }
        if (a == "at" || a == "@" || a == "by") return clockAt(i + 1, true)?.let { it.copy(n = it.n + 1) }
        return clockAt(i, false)
    }

    private fun partOf(s: String?): QaPart? {
        if (s == null) return null
        if (s == "noon") return QaPart(12, 0, 'p', false)
        val m = g.CLOCK.find(s) ?: return null
        val p = QaPart(
            m.groupValues[1].toInt(), m.groupValues[2].ifEmpty { "0" }.toInt(),
            m.groupValues[3].firstOrNull()?.let { if (it == 'p') 'p' else 'a' }, m.groupValues[2].isNotEmpty(),
            m.groupValues[1].startsWith("0"),
        )
        if (p.m > 59) return null
        if (if (p.mer != null) p.h !in 1..12 else p.h > 23) return null
        return p
    }

    /** "3-4pm", "9:30–11", "2 to 4pm", "from 2 to 4", "3pm - 4:30pm". Start and end in minutes (end may pass 24:00). */
    fun rangeAt(i: Int): Triple<Int, Int, Int>? {
        var j = i
        var pre = false
        if (lc(j) == "from" || lc(j) == "at") {
            j++
            pre = true
        }
        val w = lc(j) ?: return null
        val one = Regex("^([^-–—]+)[-–—]([^-–—]+)$").find(w)
        val a: QaPart?
        val b: QaPart?
        var k: Int
        if (one != null) {
            a = partOf(one.groupValues[1])
            b = partOf(one.groupValues[2])
            k = j + 1
        } else {
            a = partOf(w)
            k = j + 1
            val mw = lc(k)
            if (a != null && a.mer == null && mw != null && g.MERIDIEM_WORD.matches(mw)) {
                a.mer = if (mw[0] == 'p') 'p' else 'a'
                if (a.h !in 1..12) return null
                k++
            }
            val sep = lc(k)
            if (sep == null || sep !in setOf("to", "till", "until", "-", "–", "—")) return null
            b = partOf(lc(k + 1))
            k += 2
        }
        if (a == null || b == null) return null
        val bm = lc(k)
        if (b.mer == null && bm != null && g.MERIDIEM_WORD.matches(bm)) {
            if (b.h !in 1..12) return null
            b.mer = if (bm[0] == 'p') 'p' else 'a'
            k++
        }
        if (!(pre || a.mer != null || b.mer != null || a.colon || b.colon)) return null
        fun at(p: QaPart, mer: Char?) = (if (mer != null) (p.h % 12) + (if (mer == 'p') 12 else 0) else p.h) * 60 + p.m
        fun len(s: Int, e: Int) = ((e - s + 1440) % 1440).let { if (it == 0) 1440 else it }
        var s: Int
        var e: Int
        if ((a.mer != null) == (b.mer != null)) {
            s = at(a, a.mer)
            e = at(b, b.mer)
            if (a.mer == null) {
                // No meridiem anywhere: a bare 1 to 6 starts in the afternoon ("from 2 to 4"), and an end
                // hour that would fall before the start is read as pm ("9-5" = 9:00-17:00).
                if (a.h in 1..6 && !a.zero) s += 720
                if (e <= s && b.h in 1..12 && !b.zero && e + 720 > s) e += 720
            }
        } else if (a.mer == null) {
            e = at(b, b.mer)
            s = if (a.h in 1..12) {
                val am = at(a, 'a')
                val pm = at(a, 'p')
                if (len(am, e) <= len(pm, e)) am else pm
            } else at(a, null)
        } else {
            s = at(a, a.mer)
            e = if (b.h in 1..12) {
                val am = at(b, 'a')
                val pm = at(b, 'p')
                if (len(s, am) <= len(s, pm)) am else pm
            } else at(b, null)
        }
        if (e <= s) e += 1440
        return Triple(k - i, s, e)
    }

    /** A weekday list: "mon", "mon and thu", "mon, wed and fri", "mon wed fri". */
    private fun weekdayList(i: Int): Pair<Int, List<Int>>? {
        val days = mutableListOf<Int>()
        var k = i
        while (true) {
            val s = lc(k)
            if (s == null || s !in g.WEEKDAYS) break
            days += g.WEEKDAYS.getValue(s)
            k++
            val c = lc(k)
            val nx = lc(k + 1)
            if ((c == "and" || c == "&") && nx != null && nx in g.WEEKDAYS) k++
        }
        return if (days.isEmpty()) null else (k - i) to days.distinct()
    }

    private fun unitFreq(u: String?): QaFreq? = when {
        u == null -> null
        Regex("^days?$").matches(u) -> QaFreq.DAILY
        Regex("^weeks?$").matches(u) -> QaFreq.WEEKLY
        Regex("^months?$").matches(u) -> QaFreq.MONTHLY
        Regex("^years?$").matches(u) -> QaFreq.YEARLY
        else -> null
    }

    private fun repeatAt(i: Int): Triple<Int, QaRule, Boolean>? {
        val a = lc(i)
        if (a != "every" && a != "every!") return null
        val bang = a == "every!"
        var k = i + 1
        val s = lc(k) ?: return null
        val s1 = lc(k + 1)
        val rule: QaRule
        if (unitFreq(s) != null && !s.endsWith("s")) {
            rule = QaRule(unitFreq(s)!!, 1)
            k++
        } else if (s == "other" && unitFreq(s1) != null && !s1!!.endsWith("s")) {
            rule = QaRule(unitFreq(s1)!!, 2)
            k += 2
        } else if (Regex("^\\d{1,3}$").matches(s) && unitFreq(s1) != null) {
            val iv = s.toInt()
            if (iv < 1) return null
            rule = QaRule(unitFreq(s1)!!, iv)
            k += 2
        } else if (s == "weekday" || s == "weekdays") {
            rule = QaRule(QaFreq.WEEKLY, 1, byday = listOf(1, 2, 3, 4, 5))
            k++
        } else if (s == "weekend" || s == "weekends") {
            rule = QaRule(QaFreq.WEEKLY, 1, byday = listOf(6, 7))
            k++
        } else if (s in g.NTH && s1 != null && s1 in g.WEEKDAYS) {
            rule = QaRule(QaFreq.MONTHLY, 1, nth = g.NTH.getValue(s), nthDay = g.WEEKDAYS.getValue(s1))
            k += 2
        } else if (ordinal(s) != null) {
            rule = QaRule(QaFreq.MONTHLY, 1, monthday = ordinal(s))
            k++
        } else {
            val wl = weekdayList(k) ?: return null
            rule = QaRule(QaFreq.WEEKLY, 1, byday = wl.second)
            k += wl.first
        }
        // "every 2 weeks on mon and thu", "every month on the 1st", "every month on the last fri"
        if (lc(k) == "on") {
            if (rule.freq == QaFreq.WEEKLY && rule.byday == null) {
                weekdayList(k + 1)?.let { wl ->
                    rule.byday = wl.second
                    k += 1 + wl.first
                }
            } else if (rule.freq == QaFreq.MONTHLY && rule.monthday == null && rule.nth == null) {
                var j = k + 1
                if (lc(j) == "the") j++
                val od = ordinal(lc(j))
                val nth = lc(j)?.let { g.NTH[it] }
                val after = lc(j + 1)
                if (od != null) {
                    rule.monthday = od
                    k = j + 1
                } else if (lc(j) == "day" && dayNum(after) != null) {
                    rule.monthday = dayNum(after)
                    k = j + 2
                } else if (nth != null && after != null && after in g.WEEKDAYS) {
                    rule.nth = nth
                    rule.nthDay = g.WEEKDAYS.getValue(after)
                    k = j + 2
                }
            }
        }
        return Triple(k - i, rule, bang)
    }

    /** The first day of a series: the first matching day `ok` accepts, counting from today. */
    private fun firstDay(r: QaRule, ok: (LocalDate) -> Boolean): LocalDate {
        val byday = r.byday
        val monthday = r.monthday
        val nth = r.nth
        val nthDay = r.nthDay
        fun matches(z: LocalDate): Boolean = when {
            byday != null -> dow(z) in byday
            monthday != null -> z.dayOfMonth == monthday
            nth != null && nthDay != null -> dow(z) == nthDay &&
                (if (nth > 0) (z.dayOfMonth - 1) / 7 + 1 == nth else z.dayOfMonth + 7 > z.lengthOfMonth())
            else -> true
        }
        if (byday != null || monthday != null || nth != null) {
            for (k in 0 until 3000) {
                val z = today.plusDays(k.toLong())
                if (matches(z) && ok(z)) return z
            }
            return today
        }
        for (k in 0 until 1000) {
            val step = (k * r.interval).toLong()
            val z = when (r.freq) {
                QaFreq.DAILY -> today.plusDays(step)
                QaFreq.WEEKLY -> today.plusDays(7 * step)
                QaFreq.MONTHLY -> today.plusMonths(step)
                QaFreq.YEARLY -> today.plusMonths(12 * step)
            }
            if (ok(z)) return z
        }
        return today
    }

    private val bareRepeat = mapOf(
        "daily" to { QaRule(QaFreq.DAILY, 1) }, "weekly" to { QaRule(QaFreq.WEEKLY, 1) }, "monthly" to { QaRule(QaFreq.MONTHLY, 1) }, // i18n-ignore: parser keyword
        "yearly" to { QaRule(QaFreq.YEARLY, 1) }, "annually" to { QaRule(QaFreq.YEARLY, 1) }, // i18n-ignore: parser keyword
        "weekdays" to { QaRule(QaFreq.WEEKLY, 1, byday = listOf(1, 2, 3, 4, 5)) }, // i18n-ignore: parser keyword
    )

    private class Rem(val before: Int? = null, val day: LocalDate? = null, val time: Int? = null)

    fun run(): QuickAddResult {
        var priority = 0
        val tags = mutableListOf<String>()
        var collection: QuickListRef? = null
        val reminders = mutableListOf<Rem>()
        var repeat: Pair<QaRule, Boolean>? = null
        var startDate: LocalDate? = null
        var span: Pair<LocalDate, LocalDate>? = null
        var date: QaDateHit? = null
        var range: Triple<Int, Int, Int>? = null
        var time: QaClock? = null
        var duration: Int? = null
        var allDay = false
        var location: String? = null
        val guests = mutableListOf<QuickGuest>()
        var video = false

        // 1. Priority (tasks): !!!, !!, ! and p1, p2, p3.
        if (!event) scan { i ->
            val s = words[i].lc
            val p = when (s) { "!!!", "p1" -> 1; "!!", "p2" -> 5; "!", "p3" -> 9; else -> 0 }
            if (p != 0 && take(i, 1, TokenKind.PRIORITY)) { priority = p; true } else false
        }

        // 2. Tags (tasks): @phone.
        if (!event) for (i in 0 until n) {
            val c = core(i) ?: continue
            if (Regex("^@\\p{L}[\\p{L}\\p{N}_-]*$").matches(c) && take(i, 1, TokenKind.TAG)) {
                val t = c.substring(1)
                if (tags.none { it.equals(t, ignoreCase = true) }) tags += t
            }
        }

        // 3. Calendar or list: #Work.
        scan { i ->
            val c = words[i].core
            if (!Regex("^#\\p{L}[\\p{L}\\p{N}_\\-/]*$").matches(c)) return@scan false
            val q = c.substring(1).lowercase()
            val pool = if (event) opts.calendars else opts.lists
            fun squash(s: String) = s.lowercase().replace(Regex("[\\s_-]+"), "")
            val found = pool.filter { it.name.lowercase() == q }
            val pick = found.ifEmpty {
                pool.filter { it.name.lowercase().startsWith(q) || squash(it.name).startsWith(q) }
                    .sortedWith(compareBy<QuickCollection>({ it.name.length }, { it.name }))
            }
            if (pick.isNotEmpty()) {
                if (!take(i, 1, TokenKind.COLLECTION)) return@scan false
                collection = QuickListRef(pick[0].id, pick[0].name, false)
                return@scan true
            }
            if (event || !take(i, 1, TokenKind.COLLECTION)) return@scan false
            collection = QuickListRef(null, c.substring(1), true)
            true
        }

        // 4. Reminders: "remind 10m before", "remind me at 9am", "remind tom 9am".
        for (i in 0 until n) {
            val a = lc(i)
            if (a != "remind" && a != "reminder") continue
            var j = i + 1
            if (lc(j) == "me") j++
            val amt = amountAt(j, true)
            if (amt != null && lc(j + amt.n) == "before") {
                val before = when (amt.unit) { "min" -> amt.value; "day" -> amt.value * 1440; else -> null }
                if (before != null && take(i, j + amt.n + 1 - i, TokenKind.REMINDER)) reminders += Rem(before = before)
                continue
            }
            val d = dateAt(j)
            if (d != null) {
                var k = j + d.n
                val atWord = lc(k) == "at"
                val c = if (d.fillsTime) null else if (atWord) clockAt(k + 1, true) else clockAt(k, false)
                if (c != null) k += c.n + if (atWord) 1 else 0
                val t = c?.min ?: d.time
                if (t != null && take(i, k - i, TokenKind.REMINDER)) reminders += Rem(day = d.day, time = t)
                continue
            }
            val c = timeAt(j)
            if (c != null && take(i, j + c.n - i, TokenKind.REMINDER)) reminders += Rem(time = c.min)
        }

        // 5. Repeat: "every …" anywhere (the bare words wait for the positional pass).
        scan { i ->
            val r = repeatAt(i) ?: return@scan false
            if (!take(i, r.first, TokenKind.REPEAT)) return@scan false
            repeat = r.second to r.third
            true
        }

        // 6. Start (tasks): "start mon", "starts 12 oct", "from mon".
        if (!event) scan { i ->
            if (words[i].lc !in setOf("start", "starts", "starting", "from")) return@scan false
            var j = i + 1
            if (lc(j) == "on") j++
            val d = dateAt(j)
            if (d == null || d.fillsTime || !take(i, j + d.n - i, TokenKind.START)) return@scan false
            startDate = d.day
            true
        }

        // 7. Multi-day span (events): "12–14 oct", "oct 12-14", "fri to sun", "30 oct - 2 nov".
        if (event) scan { i ->
            var j = i
            if (lc(j) == "from") j++
            val a = lc(j) ?: return@scan false
            var hit: Triple<Int, LocalDate, LocalDate>? = null
            val dash = Regex("^(\\d{1,2})(?:st|nd|rd|th)?[-–—](\\d{1,2})(?:st|nd|rd|th)?$")
            val m1 = dash.find(a)
            val next = lc(j + 1)
            val m2 = if (m1 == null && a in g.MONTHS && next != null) dash.find(next) else null
            if (m1 != null && next != null && next in g.MONTHS || m2 != null) {
                val m = m1 ?: m2!!
                val mo = g.MONTHS.getValue(if (m1 != null) next!! else a)
                val yr = yearAt(j + 2)
                val s = if (yr != null) validDate(yr, mo, m.groupValues[1].toInt()) else inferMonthDay(mo, m.groupValues[1].toInt())
                val e = s?.let { validDate(it.year, mo, m.groupValues[2].toInt()) }
                if (s != null && e != null && e.isAfter(s)) hit = Triple(j + 2 + if (yr != null) 1 else 0, s, e)
            } else if (m1 == null) {
                val da = dateAt(j)
                val sep = da?.let { lc(j + it.n) }
                if (da != null && da.time == null && sep != null && sep in setOf("to", "through", "thru", "until", "till", "-", "–", "—")) {
                    val b0 = j + da.n + 1
                    val db = dateAt(b0)
                    if (db != null && db.time == null) {
                        var e = db.day
                        val bw = lc(b0)
                        if (db.weekday != null && bw != null && bw in g.WEEKDAYS) {
                            e = da.day.plusDays((((db.weekday - dow(da.day) + 7) % 7).takeIf { it != 0 } ?: 7).toLong())
                        } else {
                            while (e.isBefore(da.day)) e = e.plusMonths(12)
                        }
                        if (e.isAfter(da.day)) hit = Triple(b0 + db.n, da.day, e)
                    }
                }
            }
            val h = hit ?: return@scan false
            if (!take(i, h.first - i, TokenKind.SPAN)) return@scan false
            span = h.second to h.third
            true
        }

        // 8. Date (explicit forms anywhere; "on", "by", "due" may lead).
        if (span == null) scan { i ->
            var j = i
            val a = words[i].lc
            if (a == "due") {
                j++
                if (lc(j) == "on" || lc(j) == "by") j++
            } else if (a == "on" || a == "by") j++
            val d = dateAt(j)
            if (d == null || (d.positional && j == i)) return@scan false
            if (!take(i, j + d.n - i, TokenKind.DATE)) return@scan false
            date = d
            true
        }

        // 9. Range (events), then a single time.
        if (event && date?.fillsTime != true) scan { i ->
            val r = rangeAt(i) ?: return@scan false
            if (!take(i, r.first, TokenKind.TIME)) return@scan false
            range = r
            true
        }
        if (range == null && date?.fillsTime != true) scan { i ->
            val c = timeAt(i) ?: return@scan false
            if (!take(i, c.n, TokenKind.TIME)) return@scan false
            time = c
            true
        }

        // 10. Duration and all day (events).
        if (event) scan { i ->
            val a = words[i].lc
            if (a == "all-day" || a == "allday" || (a == "all" && lc(i + 1) == "day")) {
                if (!take(i, if (a == "all") 2 else 1, TokenKind.DURATION)) return@scan false
                allDay = true
                return@scan true
            }
            if (a != "for") {
                // A bare amount right after a time or a date: "thu 3pm 45m", "3pm 45 min", "9am 1h30". Taking it
                // lets a bare weekday before it stay a date.
                val prev = if (i > 0) tokens.firstOrNull { it.first.end == words[i - 1].end }?.first else null
                if (prev == null || (prev.kind != TokenKind.TIME && prev.kind != TokenKind.DATE)) return@scan false
                val bare = amountAt(i, false) ?: return@scan false
                if (bare.unit != "min" || !take(i, bare.n, TokenKind.DURATION)) return@scan false
                duration = bare.value
                return@scan true
            }
            val amt = amountAt(i + 1, false) ?: return@scan false
            if (!take(i, amt.n + 1, TokenKind.DURATION)) return@scan false
            duration = amt.value
            true
        }

        // 11. Location (events): "at Café Luna", "@ Café Luna", "@Home", "in Room 204", or a known place.
        if (event) scan { i ->
            val a = words[i].lc
            val j: Int
            var atWord = false
            if (a == "at" || a == "@" || a == "in") j = i + 1
            else if (Regex("^@\\p{Lu}").containsMatchIn(words[i].core)) {
                j = i
                atWord = true
            } else return@scan false
            // A known place, longest first.
            for (p in opts.places.sortedByDescending { it.length }) {
                val pw = p.lowercase().split(Regex("[$WS]+"))
                var ok = true
                var k = 0
                while (k < pw.size && ok) {
                    val w = lc(j + k)
                    ok = w != null && (if (atWord && k == 0) w.substring(1) else w) == pw[k]
                    k++
                }
                if (ok && take(i, j + pw.size - i, TokenKind.LOCATION)) {
                    location = text.substring(words[j].start + if (atWord) 1 else 0, words[j + pw.size - 1].end)
                    return@scan true
                }
            }
            val first = core(j) ?: return@scan false
            val firstText = if (atWord) first.substring(1) else first
            val fl = firstText.lowercase()
            if (!g.UPPER.containsMatchIn(firstText) || fl in g.MONTHS || fl in g.WEEKDAYS) return@scan false
            // After "at" or "@" the place runs to the end of the line ("at Back office"), up to a comma or
            // the next token: a weekday or other date, a repeat word, "with", "video", or a word that starts
            // with # ! @ +. After "in" it takes capitalised words only ("in Room 204"), as "in" is common in
            // plain titles.
            val rest = a != "in"
            fun placeWord(x: Int, c: String): Boolean {
                val l = c.lowercase()
                if (l == "with" || l in bareRepeat || (event && l == "video") || c.first() in "#!@+") return false
                return dateAt(x) == null
            }
            var k = j + 1
            if (!punct(j)) {
                while (rest || k - j < 6) {
                    val c = core(k) ?: break
                    val c1 = core(k + 1)
                    if (g.UPPER.containsMatchIn(c) || c.first().isDigit()) {
                        k++
                        if (punct(k - 1)) break
                    } else if (c.lowercase() in g.LOCATION_LINKS && c1 != null && g.UPPER.containsMatchIn(c1)) {
                        k += 2
                        if (punct(k - 1)) break
                    } else if (rest && placeWord(k, c)) {
                        k++
                        if (punct(k - 1)) break
                    } else break
                }
            }
            if (!take(i, k - i, TokenKind.LOCATION)) return@scan false
            location = text.substring(words[j].start + if (atWord) 1 else 0, words[k - 1].end)
            true
        }

        // 12. Guests (events): "with Dana", "with Dana and Lee", "with dana@x.example". Suggestions only; the
        // words stay in the title.
        if (event) for (i in 0 until n) {
            if (lc(i) != "with") continue
            var k = i + 1
            val found = mutableListOf<Pair<Int, Int>>()
            while (true) {
                val c = core(k) ?: break
                val cl = c.lowercase()
                if (g.EMAIL.matches(c)) {
                    found += k to 1
                    k++
                } else if (g.UPPER.containsMatchIn(c) && cl !in g.MONTHS && cl !in g.WEEKDAYS) {
                    var e = k + 1
                    if (!punct(k)) while (e - k < 3 && core(e)?.let { g.UPPER.containsMatchIn(it) } == true) {
                        e++
                        if (punct(e - 1)) break
                    }
                    found += k to (e - k)
                    k = e
                } else break
                val sepComma = punct(k - 1) && words[k - 1].raw.endsWith(",")
                val nx = core(k + 1)
                if ((lc(k) == "and" || lc(k) == "&") && nx != null && (g.EMAIL.matches(nx) || g.UPPER.containsMatchIn(nx))) k++
                else if (!sepComma) break
            }
            if (found.isEmpty()) continue
            words[i].used = true
            for ((gi, len) in found) {
                if (!take(gi, len, TokenKind.GUEST, keepText = true)) continue
                val t = text.substring(words[gi].start, words[gi + len - 1].end)
                val isEmail = g.EMAIL.matches(t)
                val tl = t.lowercase()
                val matches = opts.people.filter { p ->
                    if (isEmail) p.email.lowercase() == tl
                    else p.name.lowercase().startsWith(tl) || p.name.lowercase().split(Regex("[$WS]+")).any { it.startsWith(tl) }
                }
                guests += QuickGuest(t, if (isEmail) t else null, isEmail, matches)
            }
            // The joining words count as taken for the positional rules; they stay in the title.
            for (j in i + 1 until k) if (!words[j].prot) words[j].used = true
        }

        // 13. Video (events): +meet / +video anywhere.
        if (event) for (i in 0 until n) {
            val a = lc(i)
            if ((a == "+meet" || a == "+video") && take(i, 1, TokenKind.VIDEO)) video = true
        }

        // 14. Positional words, right to left until nothing changes: a bare weekday or 12/10, and the bare
        // repeat words, only at the end or before other tokens (a weekday also before "at"); bare "video"
        // only after a token and at the end.
        fun tailOk(j: Int, isDate: Boolean): Boolean {
            for (k in j until n) {
                val w = words[k]
                if (w.used) continue
                if (isDate && event && w.lc == "video" && !w.prot) continue
                if (isDate && !w.prot && (w.lc == "at" || w.lc == "@")) {
                    for (x in j until k) if (!words[x].used && words[x].lc != "video") return false
                    return true
                }
                return false
            }
            return true
        }
        var changed = true
        while (changed) {
            changed = false
            for (i in n - 1 downTo 0) {
                if (!free(i)) continue
                val a = words[i].lc
                if (repeat == null && a in bareRepeat && tailOk(i + 1, false) && take(i, 1, TokenKind.REPEAT)) {
                    repeat = bareRepeat.getValue(a)() to false
                    changed = true
                    continue
                }
                if (date == null && span == null) {
                    val d = dateAt(i)
                    if (d != null && d.positional && tailOk(i + d.n, true) && take(i, d.n, TokenKind.DATE)) {
                        date = d
                        changed = true
                        continue
                    }
                }
                if (event && !video && a == "video" && i > 0 && words[i - 1].used && tailOk(i + 1, false) && take(i, 1, TokenKind.VIDEO)) {
                    video = true
                    changed = true
                }
            }
        }

        // ---- resolution
        val d0 = date
        val r0 = range
        val c0 = time
        val rep = repeat
        var clock: Int? = r0?.second ?: c0?.min ?: d0?.time
        if (c0 != null && !c0.mer && d0?.tonight == true && c0.h in 1..11 && c0.min < 720) clock = c0.min + 720
        if (r0 != null && d0?.tonight == true && r0.second < 720) clock = r0.second + 720
        var day: LocalDate? = d0?.day
        var anchor: LocalDateTime? = null
        var start: LocalDateTime? = null
        var end: LocalDateTime? = null
        var evAllDay = false
        var due: LocalDateTime? = null
        var dueAllDay = false
        fun at(d: LocalDate, min: Int) = d.atStartOfDay().plusMinutes(min.toLong())

        if (event) {
            val sp = span
            if (sp != null) {
                evAllDay = true
                start = sp.first.atStartOfDay()
                end = sp.second.plusDays(1).atStartOfDay()
                anchor = start
            } else if (clock != null && !allDay) {
                val t = clock
                val dd = day ?: if (rep != null) firstDay(rep.first) { true } else if (t > nowMin) today else today.plusDays(1)
                start = at(dd, t)
                end = if (r0 != null) at(dd, r0.third + (t - r0.second)) else start.plusMinutes((duration ?: opts.defaultDuration).toLong())
                anchor = start
            } else if (day != null || rep != null || allDay) {
                val dd = day ?: if (rep != null) firstDay(rep.first) { true } else today
                evAllDay = true
                start = dd.atStartOfDay()
                end = dd.plusDays(1).atStartOfDay()
                anchor = start
            }
        } else {
            if (clock != null) {
                val t = clock
                if (day == null) day = if (rep != null) firstDay(rep.first) { z -> z.isAfter(today) || t > nowMin }
                else if (t > nowMin) today else today.plusDays(1)
                due = at(day, t)
                anchor = due
            } else if (day != null || rep != null) {
                val dd = day ?: firstDay(rep!!.first) { z -> z.isAfter(today) }
                due = dd.atStartOfDay()
                dueAllDay = true
                anchor = due
            }
        }
        val rems = reminders.map { r ->
            if (r.before != null) QuickReminder(r.before, null)
            else {
                val rd = r.day ?: anchor?.toLocalDate() ?: if (r.time!! > nowMin) today else today.plusDays(1)
                val atT = at(rd, r.time!!)
                QuickReminder(anchor?.let { ChronoUnit.MINUTES.between(atT, it).toInt() }, atT)
            }
        }
        val sorted = tokens.sortedBy { it.first.start }
        return QuickAddResult(
            title = QuickAddGrammar.buildTitle(text, sorted.filter { !it.second }.map { it.first }),
            tokens = sorted.map { it.first },
            start = start,
            end = end,
            allDay = evAllDay,
            duration = if (event) duration else null,
            due = due,
            dueAllDay = dueAllDay,
            startDate = if (event) null else startDate,
            rrule = rep?.first?.text(),
            repeatFrom = if (event || rep == null) null else if (rep.second) "completion" else "due",
            location = if (event) location else null,
            guests = if (event) guests else emptyList(),
            listOrCalendar = collection,
            tags = tags,
            priority = priority,
            reminders = rems,
            video = event && video,
        )
    }
}
