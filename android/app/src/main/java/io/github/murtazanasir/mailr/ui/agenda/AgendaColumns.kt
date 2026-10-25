package io.github.murtazanasir.mailr.ui.agenda

import android.content.Context
import android.graphics.Paint
import android.graphics.Typeface
import android.util.TypedValue
import io.github.murtazanasir.mailr.ui.today.TodayModel
import java.time.LocalDate
import java.time.ZoneOffset
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap

/**
 * The event lists' columns: the day, the time and the titles as separate
 * columns, so that all days and all times line up.
 * The day column is as wide as the widest short weekday in the phone's language, the time column as
 * wide as the widest time of the day on the phone's clock (12 or 24 hour), so every title starts at
 * the same place. Glance has no font features (no "tnum"), so each column is a box of fixed width.
 * Widths are measured once per language, text size, clock and font scale, and kept.
 */
object AgendaColumns {
    /** The gap after the day column and after the time column, in dp. */
    const val GAP = 6f

    /**
     * The least room a title keeps; with less (the narrow 3×2 widget), the day column goes first and
     * the days become labels, so titles are not cut shorter than they were before the columns.
     */
    const val MIN_TITLE = 80f

    private val cache = ConcurrentHashMap<String, Float>()

    /** The widest short weekday ("Wed", "Mi.", "周三") at [sp], in dp. */
    fun dayWidth(context: Context, sp: Float, medium: Boolean = false): Float {
        val monday = LocalDate.of(2026, 1, 5) // a Monday
        val days = (0L until 7L).map { AgendaModel.weekdayShort(monday.plusDays(it)) }
        return widest(context, "day", days, sp, medium)
    }

    /**
     * The widest time of the day at [sp], in dp: every hour at the minutes whose digits are widest
     * ("12:58 PM", "23:58"), as the widget writes them, and any [extra] words that share the column
     * ("All day").
     */
    fun timeWidth(context: Context, sp: Float, h24: Boolean, extra: List<String> = emptyList(), medium: Boolean = false): Float {
        val times = buildList {
            for (h in 0 until 24) for (m in MINUTES) add(TodayModel.time((h * 60L + m) * 60_000L, ZoneOffset.UTC, h24))
            addAll(extra)
        }
        return widest(context, "time$h24", times, sp, medium)
    }

    /** The widest of [texts] at [sp], in dp (the hourly strip's labels). */
    fun widest(context: Context, texts: List<String>, sp: Float): Float =
        widest(context, "any:" + texts.joinToString("\u0001"), texts, sp, false)

    /** The width of one [text] at [sp], in dp (a day label such as "Tomorrow" or "Mon 19 Oct"). */
    fun width(context: Context, text: String, sp: Float, medium: Boolean = false): Float =
        widest(context, "one:$text", listOf(text), sp, medium)

    private val MINUTES = listOf(0, 1, 11, 18, 28, 38, 48, 58)

    private fun widest(context: Context, kind: String, texts: List<String>, sp: Float, medium: Boolean): Float {
        val res = context.resources
        val dm = res.displayMetrics
        val key = listOf(kind, Locale.getDefault().toLanguageTag(), sp, medium, res.configuration.fontScale, dm.density,
            texts.lastOrNull()).joinToString("|")
        return cache.getOrPut(key) {
            val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
                textSize = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP, sp, dm)
                typeface = if (medium) Typeface.create("sans-serif-medium", Typeface.NORMAL) else Typeface.DEFAULT
            }
            val px = texts.maxOf { paint.measureText(it) }
            // Without real text measuring (some test runs), an estimate from the longest text.
            val guess = texts.maxOf { it.length } * sp * 0.58f * res.configuration.fontScale
            val dp = px / dm.density
            kotlin.math.ceil(maxOf(dp, if (dp < guess * 0.5f) guess else 0f) + 1f)
        }
    }
}
