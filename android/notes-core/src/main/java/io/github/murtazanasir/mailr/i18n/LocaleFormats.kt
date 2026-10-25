package io.github.murtazanasir.mailr.i18n

import io.github.murtazanasir.mailr.util.catching

import java.text.NumberFormat
import java.time.format.DateTimeFormatter
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap

/**
 * Dates and numbers in the app's language.
 *
 * In English the app keeps its own patterns ("Sep 21", "Mon 5 Oct, 4:30 PM"): they are the product's
 * voice. In every other language the pattern comes from the language's own rules, from a skeleton
 * (the fields to show, in any order: "MMMd", "EEEdMMM", "yMMMd", "jmm"); Android picks the order,
 * the separators and the 12- or 24-hour clock.
 */
object LocaleFormats {
    private val cache = ConcurrentHashMap<Triple<String, String, Locale>, DateTimeFormatter>()

    /**
     * A formatter for [skeleton] in [locale]. [english] is the app's own pattern, used as is in English
     * (and in a plain JVM test, which has no Android date patterns).
     */
    fun date(skeleton: String, english: String, locale: Locale = Locale.getDefault()): DateTimeFormatter =
        cache.getOrPut(Triple(skeleton, english, locale)) {
            DateTimeFormatter.ofPattern(pattern(skeleton, english, locale), locale)
        }

    /** The pattern [date] uses. */
    fun pattern(skeleton: String, english: String, locale: Locale = Locale.getDefault()): String {
        if (locale.language == "en" || locale.language.isEmpty()) return english
        return catching { android.text.format.DateFormat.getBestDateTimePattern(locale, skeleton) }
            .getOrNull()?.takeIf { it.isNotBlank() } ?: english
    }

    /** A whole number with the language's grouping ("1,234", "1 234", "1.234"). */
    fun number(n: Long, locale: Locale = Locale.getDefault()): String = NumberFormat.getIntegerInstance(locale).format(n)

    fun number(n: Int, locale: Locale = Locale.getDefault()): String = number(n.toLong(), locale)
}
