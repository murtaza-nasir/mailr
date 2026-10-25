package io.github.murtazanasir.mailr.ui.agenda

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.annotation.DrawableRes
import androidx.annotation.StringRes
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.DpSize
import androidx.glance.color.ColorProvider
import androidx.glance.unit.ColorProvider
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.ui.calendar.CalendarColors
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch

/**
 * The Agenda widget's four looks, picked per widget
 * in its settings. One GlanceAppWidget draws them all; the style is kept per widget id.
 */
enum class AgendaStyle(val key: String, @StringRes val label: Int, @DrawableRes val preview: Int) {
    /** A frosted panel: today's hours as a timeline with a now-line on the start side, tasks on the end side. */
    GLASS("glass", R.string.agenda_style_glass, R.drawable.agenda_style_glass),
    /** A paper card: a serif date, the days on a thin rail with dots, tasks on a sheet with a turned corner. */
    PAPER("paper", R.string.agenda_style_paper, R.drawable.agenda_style_paper),
    /** A week strip under the date (each day opens), today's agenda and the tasks below. */
    WEEK("week", R.string.agenda_style_week, R.drawable.agenda_style_week),
    /** Two halves: a large date numeral and the next event with a countdown; the weather by the hour and tasks. */
    NUMERAL("numeral", R.string.agenda_style_numeral, R.drawable.agenda_style_numeral);

    companion object {
        /** New widgets, and widgets placed before styles existed. */
        val DEFAULT = WEEK

        fun of(key: String?): AgendaStyle? = entries.firstOrNull { it.key == key }
    }
}

/** Each widget's style (SharedPreferences beside the weather choices; nothing goes to the server). */
class AgendaStyles(context: Context) {
    private val sp = context.applicationContext.getSharedPreferences("mailr_agenda_widget", Context.MODE_PRIVATE)

    operator fun get(id: Int): AgendaStyle = AgendaStyle.of(sp.getString(key(id), null)) ?: AgendaStyle.DEFAULT

    operator fun set(id: Int, style: AgendaStyle) { sp.edit().putString(key(id), style.key).apply() }

    fun has(id: Int): Boolean = sp.contains(key(id))

    fun remove(id: Int) { sp.edit().remove(key(id)).remove(columnsKey(id)).apply() }

    /** "Line up days and times" for widget [id]: on unless turned off in the widget's settings. */
    fun columns(id: Int): Boolean = sp.getBoolean(columnsKey(id), true)

    fun setColumns(id: Int, on: Boolean) { sp.edit().putBoolean(columnsKey(id), on).apply() }

    /** Widgets placed before styles existed get the default written down, so a later default never changes them. */
    fun migrate(ids: IntArray): Int {
        val missing = ids.filterNot { has(it) || it == android.appwidget.AppWidgetManager.INVALID_APPWIDGET_ID }
        if (missing.isNotEmpty()) sp.edit().apply { missing.forEach { putString(key(it), AgendaStyle.DEFAULT.key) } }.apply()
        return missing.size
    }

    private fun key(id: Int) = "agenda.style.$id"
    private fun columnsKey(id: Int) = "agenda.columns.$id"
}

/**
 * The widget's size. Only the width changes the layout: under 300 dp wide is the narrow (3×2) one.
 * The height never does: each style's top block keeps one size, and
 * the lists under it take the rest of the height and scroll.
 */
data class AgendaSize(val width: Float, val height: Float) {
    val narrow: Boolean get() = width < NARROW

    companion object {
        const val NARROW = 300f

        /**
         * The height every style fits with its top block and one row of each list: a 4×2 widget on
         * a Pixel is about 205 dp tall. The resize limit in res/xml/agenda_widget.xml stays at
         * Android's two-row value (110 dp), because launchers count rows by their smallest cell
         * (landscape), and a larger value would take two rows away.
         */
        const val MIN_HEIGHT = 200f

        fun of(size: DpSize) = AgendaSize(size.width.value, size.height.value)

        /** The height [style]'s top block needs with one row of each list under it. */
        fun need(style: AgendaStyle, narrow: Boolean = false): Float = when (style) {
            AgendaStyle.GLASS -> GlassDims.need(narrow)
            AgendaStyle.PAPER -> PaperDims.need()
            AgendaStyle.WEEK -> WeekDims.need(narrow)
            AgendaStyle.NUMERAL -> NumeralDims.need()
        }
    }
}

/**
 * The styles' colours, light and dark (the mockups' tokens): ink in three strengths, mailr's teal
 * accent, the overdue red, and hairlines. Panels and cards are translucent drawables with a night
 * variant (res/drawable-night), because a tint cannot carry alpha before Android 12.
 */
object AgendaInk {
    private fun c(day: Long, night: Long) = ColorProvider(Color(day), Color(night))
    val text = c(0xFF10232A, 0xFFEEF3F5)
    val text2 = c(0xB810232A, 0xBDE8F0F3)
    val text3 = c(0x8510232A, 0x85E8F0F3)
    val accent = c(0xFF1F6C86, 0xFF8FD0E3)
    val onAccent = c(0xFFFFFFFF, 0xFF003543)
    val danger = c(0xFFB3261E, 0xFFF2B8B5)
    val line = c(0x1F10232A, 0x1FFFFFFF)
    val hair = c(0x1410232A, 0x14FFFFFF)
    /** What the paper style's rail dots and turned corner sit on. */
    val paperEdge = c(0xFFF6F4EF, 0xFF1C252C)

    /** An event's colour: its calendar colour, lighter at night (the swatch's dark variant when it is one). */
    fun event(calendar: String?, colour: String?): ColorProvider {
        val day = CalendarColors.of(calendar, colour)
        val night = CalendarColors.swatchOf(colour)?.let { Color(it.dark) } ?: lighten(day)
        return ColorProvider(day, night)
    }

    private fun lighten(c: Color) = Color(c.red + (1 - c.red) * 0.3f, c.green + (1 - c.green) * 0.3f, c.blue + (1 - c.blue) * 0.3f)
}

/**
 * Redraws that keep the time true (the glass timeline's now-line, the numeral's countdown): one
 * inexact alarm that does not wake the phone, so it fires while the phone is in use. Each drawn
 * widget asks for the moment its face next changes; the earliest wins. With no glass or numeral
 * widget nothing asks again and the alarm stops.
 */
object AgendaTicks {
    private const val ACTION = "io.github.murtazanasir.mailr.agenda.TICK"
    private const val KEY = "agenda.tick.at"
    const val QUARTER = 15 * 60_000L

    /** When [style]'s face for [m] next changes on its own, or null when only new data changes it. */
    fun nextChange(style: AgendaStyle, m: AgendaModel, now: Long): Long? = when (style) {
        AgendaStyle.GLASS -> if (m.ready && m.timeline.isNotEmpty()) (now / QUARTER + 1) * QUARTER else null
        AgendaStyle.NUMERAL -> m.next?.let { n ->
            when {
                n.minutes != null -> (now / 60_000 + 1) * 60_000
                n.underWay -> n.event.end
                else -> n.event.start - AgendaModel.COUNTDOWN_MIN * 60_000L
            }
        }?.coerceAtLeast(now + 1)
        else -> null
    }

    /** Asks for a redraw at [at] unless an earlier one is already set. */
    fun request(context: Context, at: Long?, now: Long = AppClock.millis()) {
        if (at == null) return
        val sp = context.getSharedPreferences("mailr_agenda_widget", Context.MODE_PRIVATE)
        val set = sp.getLong(KEY, 0)
        if (set in (now + 1)..at) return
        sp.edit().putLong(KEY, at).apply()
        val am = context.getSystemService(AlarmManager::class.java) ?: return
        am.setWindow(AlarmManager.RTC, at, 60_000, pending(context))
    }

    fun cancel(context: Context) {
        context.getSharedPreferences("mailr_agenda_widget", Context.MODE_PRIVATE).edit().remove(KEY).apply()
        context.getSystemService(AlarmManager::class.java)?.cancel(pending(context))
    }

    internal fun fired(context: Context) {
        context.getSharedPreferences("mailr_agenda_widget", Context.MODE_PRIVATE).edit().remove(KEY).apply()
    }

    private fun pending(context: Context): PendingIntent = PendingIntent.getBroadcast(context, 0,
        Intent(context, AgendaTickReceiver::class.java).setAction(ACTION), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
}

/** The tick alarm: every Agenda widget drawn again (each asks for its next tick). */
class AgendaTickReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val g = AgendaWidgets.graph(context) ?: return
        val done = goAsync()
        AgendaTicks.fired(context)
        g.scope.launch {
            try {
                AgendaWidgets.refresh(context)
            } catch (e: CancellationException) {
                throw e
            } finally {
                done.finish()
            }
        }
    }
}
