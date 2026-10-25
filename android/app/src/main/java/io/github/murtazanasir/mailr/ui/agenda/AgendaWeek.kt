package io.github.murtazanasir.mailr.ui.agenda

import androidx.compose.runtime.Composable
import androidx.compose.ui.unit.dp
import androidx.glance.ColorFilter
import androidx.glance.GlanceModifier
import androidx.glance.Image
import androidx.glance.ImageProvider
import androidx.glance.LocalContext
import androidx.glance.background
import androidx.glance.layout.Alignment
import androidx.glance.layout.Box
import androidx.glance.layout.Column
import androidx.glance.layout.Row
import androidx.glance.layout.Spacer
import androidx.glance.layout.fillMaxHeight
import androidx.glance.layout.fillMaxSize
import androidx.glance.layout.fillMaxWidth
import androidx.glance.layout.height
import androidx.glance.layout.padding
import androidx.glance.layout.size
import androidx.glance.layout.width
import androidx.glance.text.FontWeight
import androidx.glance.text.TextAlign
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText
import java.time.DayOfWeek

/**
 * Week strip (AgendaStyle.WEEK, the default): a gradient glass panel; the date with a weather pill;
 * the week from the phone's first day, today ringed in the accent colour and up to three dots of
 * event colours under each day (each day opens it); today's agenda and the next days' under it, and
 * the tasks in a column on the end side. The date and the strip keep one size at every height; the
 * agenda and the tasks scroll, each on its own. Narrow (3×2), the tasks follow the agenda in one list.
 */
@Composable
internal fun WeekAgenda(m: AgendaModel, s: AgendaSize, widgetId: Int, columns: Boolean = true) {
    val d = WeekDims
    Column(GlanceModifier.fillMaxSize().background(ImageProvider(R.drawable.agenda_week_bg))
        .padding(start = 16.dp, end = 14.dp, top = d.PAD_TOP.dp, bottom = d.PAD_BOTTOM.dp)) {
        Row(GlanceModifier.fillMaxWidth().height(d.HEADER.dp), verticalAlignment = Alignment.CenterVertically) {
            Row(GlanceModifier.defaultWeight().fillMaxHeight().talk(m), verticalAlignment = Alignment.CenterVertically) {
                if (s.narrow) {
                    Txt(m.dateNarrow, 18f, AgendaInk.text, weight = FontWeight.Medium)
                } else {
                    Txt("${m.weekday} ${m.dayNumber}", 18f, AgendaInk.text, weight = FontWeight.Medium)
                    Spacer(GlanceModifier.width(7.dp))
                    Txt(m.month, 14f, AgendaInk.text2)
                }
            }
            WeatherSpot(m, widgetId) { w ->
                Row(GlanceModifier.height(28.dp).background(ImageProvider(R.drawable.agenda_week_pill))
                    .padding(start = 7.dp, end = 10.dp).weatherTap(w), verticalAlignment = Alignment.CenterVertically) {
                    WeatherIconImage(w.icon, 20.dp)
                    Spacer(GlanceModifier.width(5.dp))
                    Txt(w.tempLine, 15f, AgendaInk.text, weight = FontWeight.Medium)
                    if (!s.narrow) {
                        Spacer(GlanceModifier.width(5.dp))
                        Txt(AppText.get(R.string.agenda_high_low_short, w.high, w.low), 10.5f, AgendaInk.text2)
                    }
                }
            }
        }
        Spacer(GlanceModifier.height(d.STRIP_GAP.dp))
        Row(GlanceModifier.fillMaxWidth().height(d.STRIP.dp)) {
            m.week.forEach { day -> StripDay(day) }
        }
        Spacer(GlanceModifier.height(d.STRIP_GAP.dp))
        Row(GlanceModifier.fillMaxWidth().defaultWeight()) {
            Column(GlanceModifier.defaultWeight().fillMaxHeight()) {
                val rows = WeekList.rows(m, s.height - d.top(), withTasks = s.narrow)
                // The days are headings; the times a column as wide as the widest time (AgendaColumns), or hugging their text when off.
                val ctx = LocalContext.current
                val timeW = if (columns) AgendaColumns.timeWidth(ctx, 10.5f, m.h24, listOf(AppText.get(R.string.agenda_all_day_short)), medium = true) else null
                if (rows.none { it is WeekList.Row.Ev }) Quiet(emptyEvents(m))
                if (rows.isNotEmpty()) ScrollList(rows) { r ->
                    when (r) {
                        is WeekList.Row.Head -> Head(r.day.shortLabel, r.first, openDay(r.day.date), r.day.label)
                        is WeekList.Row.Ev -> WeekEvent(r.e, timeW)
                        is WeekList.Row.TasksHead -> Head(AppText.get(R.string.agenda_tasks_count, r.count), r.first, null, null)
                        is WeekList.Row.Task -> TaskRow(r.t, d.TASK_ROW, fs = 12f)
                    }
                }
            }
            if (!s.narrow && m.ready) {
                Box(GlanceModifier.width(23.dp).fillMaxHeight().padding(horizontal = 11.dp)) { Hairline(vertical = true) }
                Column(GlanceModifier.width((s.width * 0.40f).dp).fillMaxHeight()) {
                    TasksColumn(m, d.TASK_HEAD, d.TASK_ROW, fs = 12f)
                }
            }
        }
    }
}

/** The week strip's fixed heights in dp: the same at every widget height. */
internal object WeekDims {
    const val PAD_TOP = 10f
    const val PAD_BOTTOM = 10f
    const val HEADER = 36f
    const val STRIP_GAP = 6f
    const val STRIP = 50f
    const val HEAD = 19f
    const val EV = 25f
    const val TASK_HEAD = 19f
    const val TASK_ROW = 31f

    /** The date, the strip and the padding: what the lists do not get. */
    fun top(): Float = PAD_TOP + HEADER + STRIP_GAP * 2 + STRIP + PAD_BOTTOM

    /** The top with a day heading and one event (and, wide, the tasks heading and one task beside them). */
    fun need(narrow: Boolean): Float = top() + if (narrow) HEAD + EV else maxOf(HEAD + EV, TASK_HEAD + TASK_ROW)
}

/** A day of the strip: its letter, its number (today in a filled circle), its dots. Opens the day. */
@Composable
private fun androidx.glance.layout.RowScope.StripDay(d: AgendaModel.WeekDay) {
    val weekend = d.date.dayOfWeek == DayOfWeek.SATURDAY || d.date.dayOfWeek == DayOfWeek.SUNDAY
    val circle = 28
    Column(GlanceModifier.defaultWeight().fillMaxHeight().dayTarget(d.label, openDay(d.date)),
        horizontalAlignment = Alignment.CenterHorizontally) {
        Txt(d.letter, 9.5f, if (d.today) AgendaInk.accent else AgendaInk.text3, weight = FontWeight.Medium)
        Box(GlanceModifier.size(circle.dp).let { if (d.today) it.background(ImageProvider(R.drawable.widget_circle), colorFilter = ColorFilter.tint(AgendaInk.accent)) else it },
            contentAlignment = Alignment.Center) {
            Txt(d.number, 14f, when {
                d.today -> AgendaInk.onAccent
                d.past -> AgendaInk.text3
                weekend -> AgendaInk.text2
                else -> AgendaInk.text
            }, weight = if (d.today) FontWeight.Bold else null, align = TextAlign.Center)
        }
        Row(GlanceModifier.height(6.dp).padding(top = 1.dp), verticalAlignment = Alignment.CenterVertically) {
            d.dots.forEachIndexed { i, (cal, colour) ->
                if (i > 0) Spacer(GlanceModifier.width(2.5.dp))
                Image(ImageProvider(if (d.past) R.drawable.agenda_dot_faint else R.drawable.widget_circle), null, GlanceModifier.size(4.dp),
                    colorFilter = ColorFilter.tint(AgendaInk.event(cal, colour)))
            }
        }
    }
}

/** "Today ———": a small accent heading with a hairline after it (opens the day when [open] is set). */
@Composable
private fun Head(text: String, first: Boolean, open: androidx.glance.action.Action?, label: String?) {
    var mod = GlanceModifier.fillMaxWidth().height((WeekDims.HEAD + if (first) 0 else 2).dp).padding(top = (if (first) 0 else 2).dp)
    if (open != null && label != null) mod = mod.dayTarget(label, open)
    Row(mod, verticalAlignment = Alignment.CenterVertically) {
        Txt(text, 10.5f, AgendaInk.accent, weight = FontWeight.Medium)
        Spacer(GlanceModifier.width(6.dp))
        Hairline(AgendaInk.hair, modifier = GlanceModifier.defaultWeight())
    }
}

/**
 * An agenda row: time (the accent while it is under way), a colour bar, the title. Opens the event.
 * [timeW]: the time column's width, so bars and titles line up; null lets the time hug its text.
 */
@Composable
private fun WeekEvent(e: AgendaModel.Event, timeW: Float?) {
    val c = AgendaInk.event(e.calendar, e.colour)
    Row(GlanceModifier.fillMaxWidth().height(WeekDims.EV.dp).eventTarget(e, openEvent(e)), verticalAlignment = Alignment.CenterVertically) {
        Txt(if (e.allDay) AppText.get(R.string.agenda_all_day_short) else e.time, 10.5f,
            when { e.now -> AgendaInk.accent; e.past -> AgendaInk.text3; else -> AgendaInk.text2 },
            if (timeW != null) GlanceModifier.width(timeW.dp) else GlanceModifier, weight = if (e.now) FontWeight.Medium else null)
        Spacer(GlanceModifier.width(7.dp))
        Box(GlanceModifier.width(3.dp).height(15.dp)
            .background(ImageProvider(if (e.past) R.drawable.agenda_bar_faint else R.drawable.widget_bar), colorFilter = ColorFilter.tint(c))) {}
        Spacer(GlanceModifier.width(7.dp))
        Txt(e.title, 12.5f, if (e.past) AgendaInk.text3 else AgendaInk.text, GlanceModifier.defaultWeight())
    }
}

/**
 * The week strip's agenda rows: today (with the last ended event, faded, when today's events still
 * fit the visible [height] with it) and the days after; narrow, the tasks follow under their heading.
 * The list scrolls, so every row is there; only the faded ended event depends on the height.
 */
object WeekList {
    sealed class Row {
        class Head(val day: AgendaModel.Day, val first: Boolean) : Row()
        class Ev(val e: AgendaModel.Event) : Row()
        class TasksHead(val count: Int, val first: Boolean) : Row()
        class Task(val t: AgendaModel.Task) : Row()
    }

    fun rows(m: AgendaModel, height: Float, withTasks: Boolean = false): List<Row> {
        if (!m.ready) return emptyList()
        val today = m.days.firstOrNull()?.takeIf { it.today }
        val last = m.earlier.takeLast(1)
        val todayH = WeekDims.HEAD + (last.size + (today?.events?.size ?: 0)) * WeekDims.EV
        val earlier = if (todayH <= height) last else emptyList()
        return buildList {
            m.days.forEach { d ->
                val evs = if (d.today) earlier + d.events else d.events
                if (evs.isEmpty()) return@forEach
                add(Row.Head(d, isEmpty()))
                evs.forEach { add(Row.Ev(it)) }
            }
            if (withTasks && m.tasks.isNotEmpty()) {
                add(Row.TasksHead(m.tasks.size, isEmpty()))
                m.tasks.forEach { add(Row.Task(it)) }
            }
        }
    }
}
