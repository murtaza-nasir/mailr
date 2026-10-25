package io.github.murtazanasir.mailr.ui.util

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.time.AppClock
import androidx.compose.ui.graphics.Color
import io.github.murtazanasir.mailr.data.api.Participant
import java.time.Instant
import java.time.LocalDate
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.util.Locale
import kotlin.math.abs
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.i18n.LocaleFormats

object Format {
    fun parse(iso: String?): ZonedDateTime? = iso?.let {
        catching { OffsetDateTime.parse(it).atZoneSameInstant(ZoneId.systemDefault()) }.getOrNull()
            ?: catching { Instant.parse(it).atZone(ZoneId.systemDefault()) }.getOrNull()
            ?: catching { LocalDate.parse(it.take(10)).atStartOfDay(ZoneId.systemDefault()) }.getOrNull()
    }

    /** Gmail's list dates: time today, "Sep 21" this year, "9/21/24" before. */
    fun listDate(iso: String?, now: ZonedDateTime = AppClock.now()): String {
        val d = parse(iso) ?: return ""
        return when {
            d.toLocalDate() == now.toLocalDate() ->
                d.format(DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).withLocale(Locale.getDefault()))
            d.year == now.year -> d.format(LocaleFormats.date("MMMd", "MMM d"))
            else -> d.format(LocaleFormats.date("yyMd", "M/d/yy"))
        }
    }

    /**
     * When a cited event or task is, with its day: "Today, 11:00 PM", "Tomorrow, 10:00 AM",
     * "Mon 5 Oct, 4:30 PM", or the day alone when it is all day. A time alone ("11:00 PM") let an
     * answer about tomorrow sit beside a source that was tonight.
     */
    fun citeWhen(iso: String?, allDay: Boolean = false, now: ZonedDateTime = AppClock.now()): String {
        val d = parse(iso) ?: return ""
        val dateOnly = allDay || (iso?.length == 10)
        val day = when (d.toLocalDate()) {
            now.toLocalDate() -> AppText.get(R.string.ui_today)
            now.toLocalDate().plusDays(1) -> AppText.get(R.string.ui_tomorrow)
            now.toLocalDate().minusDays(1) -> AppText.get(R.string.ui_yesterday)
            else -> d.format(if (d.year == now.year) LocaleFormats.date("EEEdMMM", "EEE d MMM") else LocaleFormats.date("yEEEdMMM", "EEE d MMM yyyy"))
        }
        return if (dateOnly) day
        else AppText.get(R.string.ui_day_time, day, d.format(DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).withLocale(Locale.getDefault())))
    }

    /** Message header dates: "Sep 21, 2:05 PM (3 days ago)". */
    fun messageDate(iso: String?, now: ZonedDateTime = AppClock.now()): String {
        val d = parse(iso) ?: return ""
        val base = if (d.year == now.year) d.format(LocaleFormats.date("MMMdhmma", "MMM d, h:mm a"))
        else d.format(LocaleFormats.date("yMMMdhmma", "MMM d, yyyy, h:mm a"))
        return AppText.get(R.string.ui_date_relative, base, relative(d, now))
    }

    /** [ago] false: the span alone ("5 min", "3 days"), for "Expires in …". */
    fun relative(d: ZonedDateTime, now: ZonedDateTime = AppClock.now(), ago: Boolean = true): String {
        val mins = java.time.Duration.between(d, now).toMinutes()
        return when {
            mins < 1 -> AppText.get(R.string.ui_just_now)
            mins < 60 -> AppText.plural(if (ago) R.plurals.ui_minutes_ago else R.plurals.ui_minutes_span, mins.toInt(), mins)
            mins < 60 * 24 -> (mins / 60).let { AppText.plural(if (ago) R.plurals.ui_hours_ago else R.plurals.ui_hours_span, it.toInt(), it) }
            mins < 60 * 24 * 2 -> AppText.get(R.string.ui_yesterday_lower)
            mins < 60 * 24 * 30 -> (mins / (60 * 24)).let { AppText.plural(if (ago) R.plurals.ui_days_ago else R.plurals.ui_days_span, it.toInt(), it) }
            else -> d.format(LocaleFormats.date("MMMd", "MMM d"))
        }
    }

    /** "me, Alice, Bob" style sender line (first names when several people). */
    fun participants(ps: List<Participant>, count: Int): String {
        if (ps.isEmpty()) return AppText.get(R.string.ui_unknown_participant)
        val names = ps.map { p ->
            when {
                p.me -> AppText.get(R.string.ui_me)
                ps.size > 1 -> firstName(p.name ?: p.email)
                else -> p.name?.takeIf { it.isNotBlank() } ?: p.email
            }
        }
        val shown = if (names.size > 3) listOf(names.first(), "…") + names.takeLast(2) else names
        return shown.joinToString(", ")
    }

    /** Snippets come from the server's HTML-to-text pass, which keeps Markdown marks ("# Title", "**x**"). */
    fun snippet(s: String): String = s
        .replace(Regex("(^|\\s)#{1,6}\\s+"), "$1")
        .replace(Regex("\\*\\*|__"), "")
        .replace(Regex("\\s+"), " ")
        .trim()

    fun firstName(s: String): String {
        val t = s.trim()
        if ('@' in t && ' ' !in t) return t.substringBefore('@')
        return t.split(' ', ',').firstOrNull { it.isNotBlank() } ?: t
    }

    fun initials(name: String?, email: String): String {
        val src = name?.takeIf { it.isNotBlank() } ?: email.substringBefore('@')
        val parts = src.split(' ', '.', '_', '-').filter { it.isNotBlank() && it.first().isLetterOrDigit() }
        return when {
            parts.size >= 2 -> "${parts[0].first()}${parts[1].first()}"
            parts.isNotEmpty() -> parts[0].take(1)
            else -> "?"
        }.uppercase()
    }

    private val avatarPalette = listOf(
        0xFF1A73E8, 0xFFD93025, 0xFF188038, 0xFFE37400, 0xFF9334E6, 0xFF12B5CB,
        0xFFE52592, 0xFF5F6368, 0xFF3F51B5, 0xFF00897B, 0xFF7CB342, 0xFFF4511E,
    ).map { Color(it) }

    fun avatarColor(key: String): Color = avatarPalette[abs(key.lowercase().hashCode()) % avatarPalette.size]

    /** A scheme token "@<scheme>/<hue>/<shade>", or null. */
    internal fun labelShade(spec: String?): LabelColourData.Shade? {
        val m = LABEL_TOKEN.matchEntire(spec?.trim()?.lowercase() ?: return null) ?: return null
        val (scheme, hue, shade) = m.destructured
        val n = shade.toInt()
        if (n !in 1..LabelColourData.SHADES) return null
        return LabelColourData.SCHEMES.firstOrNull { it.id == scheme }?.hues?.firstOrNull { it.id == hue }?.shades?.get(n - 1)
    }

    private val LABEL_TOKEN = Regex("""@([a-z]+)/([a-z]+)/([0-9])""")

    /** Background and text of a label colour: a scheme token (its light or dark pair), "#bg" or
     *  "#bg,#fg" ("#bg|#fg" too). Hex colours are the same in both themes. */
    fun labelColors(spec: String?, dark: Boolean = false): Pair<Color, Color>? {
        if (spec.isNullOrBlank()) return null
        if (spec.trimStart().startsWith("@")) {
            val sh = labelShade(spec) ?: return null
            return if (dark) parseHex(sh.darkBg)!! to parseHex(sh.darkFg)!! else parseHex(sh.bg)!! to parseHex(sh.fg)!!
        }
        val parts = spec.split(',', '|').map { it.trim() }
        val bg = parseHex(parts[0]) ?: return null
        val fg = parts.getOrNull(1)?.let(::parseHex) ?: if (bg.luminance() > 0.55f) Color(0xFF202124) else Color.White
        return bg to fg
    }

    /** The swatch beside a label's name (the drawer icon): it sits on the page background. */
    fun labelDot(spec: String?, dark: Boolean = false): Color? {
        val sh = labelShade(spec) ?: return labelColors(spec)?.first
        return parseHex(if (dark) sh.darkDot else sh.dot)
    }

    private fun parseHex(s: String): Color? = catching {
        Color(android.graphics.Color.parseColor(if (s.startsWith("#")) s else "#$s"))
    }.getOrNull()

    private fun Color.luminance(): Float = 0.2126f * red + 0.7152f * green + 0.0722f * blue

    fun bytes(n: Long): String = when {
        n < 1024 -> AppText.get(R.string.ui_bytes, n)
        n < 1024 * 1024 -> AppText.get(R.string.ui_kilobytes, n / 1024)
        else -> AppText.get(R.string.ui_megabytes, String.format(Locale.getDefault(), "%.1f", n / 1048576.0))
    }

    /** Plain text → HTML paragraphs for the composer (escapes markup, links bare URLs). */
    fun textToHtml(text: String): String = text.trimEnd().split(Regex("\n{2,}")).joinToString("") { para ->
        "<p>" + linkify(para.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")).replace("\n", "<br>") + "</p>"
    }

    private val BARE_URL = Regex("""https?://[^\s<>"]+[^\s<>".,;:!?)\]']""")

    /** Wrap bare http(s) URLs (in already-escaped text) in links. */
    fun linkify(escaped: String): String = BARE_URL.replace(escaped) { m -> "<a href=\"${m.value}\">${m.value}</a>" }

    /** HTML → plain text for editing a draft body (keeps paragraphs and line breaks). */
    fun htmlToText(html: String): String = html
        .replace(Regex("(?i)<br\\s*/?>"), "\n")
        .replace(Regex("(?i)</(p|div|li|h[1-6])>"), "\n\n")
        .replace(Regex("<[^>]+>"), "")
        .replace("&nbsp;", " ").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", "\"")
        .replace("&#39;", "'").replace("&amp;", "&")
        .replace(Regex("\n{3,}"), "\n\n")
        .trim()

    /** Split a reply's HTML into the user's part and the quoted history (mailr_quote). */
    fun splitQuote(html: String?): Pair<String, String?> {
        if (html == null) return "" to null
        val i = html.indexOf("<div class=\"mailr_quote\"")
        return if (i < 0) html to null else html.substring(0, i) to html.substring(i)
    }
}
