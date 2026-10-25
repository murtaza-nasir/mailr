package io.github.murtazanasir.mailr.ui.calendar

import androidx.annotation.StringRes
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.ui.util.Format
import kotlin.math.abs

/** One colour swatch: an id, its visible name and the two theme variants (ARGB). */
data class Swatch(val id: String, @StringRes val nameRes: Int, val light: Long, val dark: Long) {
    val name: String get() = AppText.get(nameRes)
    val hex: String get() = "#%06x".format(light and 0xFFFFFF)
    fun color(dark: Boolean): Color = Color(if (dark) this.dark else light)
}

object CalendarColors {
    private val palette = listOf(0xFF1A73E8, 0xFF0B8043, 0xFF8E24AA, 0xFFE67C73, 0xFFF4511E, 0xFF039BE5,
        0xFF33B679, 0xFFD50000, 0xFF7986CB, 0xFFF6BF26).map { Color(it) }

    /** The server's calendar-color, else a stable colour per calendar URL. */
    fun of(url: String?, hex: String?): Color =
        Format.labelColors(hex)?.first ?: palette[abs((url ?: "").hashCode()) % palette.size]

    fun onColor(c: Color): Color = if (c.luminance() > 0.5f) Color(0xFF1F1F1F) else Color.White

    /**
     * The colour palette: the 12 named swatches, the same palette as the web (features/calendar/colour.ts),
     * with a light and a dark variant. The light hex is what gets stored.
     */
    val swatches = listOf(
        Swatch("tomato", R.string.cal_colour_tomato, 0xFFD50000, 0xFFE66A5F), Swatch("flamingo", R.string.cal_colour_flamingo, 0xFFE67C73, 0xFFEE9B94),
        Swatch("tangerine", R.string.cal_colour_tangerine, 0xFFF4511E, 0xFFF28B5B), Swatch("banana", R.string.cal_colour_banana, 0xFFF6BF26, 0xFFF5CF60),
        Swatch("sage", R.string.cal_colour_sage, 0xFF33B679, 0xFF5CC794), Swatch("basil", R.string.cal_colour_basil, 0xFF0B8043, 0xFF3FA36D),
        Swatch("peacock", R.string.cal_colour_peacock, 0xFF039BE5, 0xFF4FB6EB), Swatch("blueberry", R.string.cal_colour_blueberry, 0xFF3F51B5, 0xFF6F7FD6),
        Swatch("lavender", R.string.cal_colour_lavender, 0xFF7986CB, 0xFFA0AADB), Swatch("grape", R.string.cal_colour_grape, 0xFF8E24AA, 0xFFB36FC7),
        Swatch("graphite", R.string.cal_colour_graphite, 0xFF616161, 0xFF9E9E9E), Swatch("cocoa", R.string.cal_colour_cocoa, 0xFF795548, 0xFFA1887F),
    )

    /** The swatch a stored colour belongs to (either variant), if any. */
    fun swatchOf(hex: String?): Swatch? {
        val c = Format.labelColors(hex)?.first ?: return null
        return swatches.firstOrNull { Color(it.light) == c || Color(it.dark) == c }
    }

    /** A colour's name for TalkBack and labels: the swatch name, else null. */
    fun nameOf(hex: String?): String? = swatchOf(hex)?.name

    /** iCalendar priority: 1–4 high, 5 medium, 6–9 low, 0 none. */
    fun priorityLevel(p: Int): Int = when (p) {
        in 1..4 -> 3
        5 -> 2
        in 6..9 -> 1
        else -> 0
    }

    @Composable
    fun priority(level: Int): Color = when (level) {
        3 -> MaterialTheme.colorScheme.error
        2 -> Color(0xFFE37400)
        1 -> Color(0xFF1A73E8)
        else -> MaterialTheme.colorScheme.onSurfaceVariant
    }

    val priorityNames: List<String> get() = listOf(R.string.cal_priority_none, R.string.cal_priority_low,
        R.string.cal_priority_medium, R.string.cal_priority_high).map { AppText.get(it) }
    /** The iCalendar value written for each level. */
    fun priorityValue(level: Int) = when (level) { 3 -> 1; 2 -> 5; 1 -> 9; else -> 0 }
}
