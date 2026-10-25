package io.github.murtazanasir.mailr.ui.agenda

import androidx.compose.runtime.Composable
import androidx.compose.ui.unit.dp
import androidx.glance.ColorFilter
import androidx.glance.GlanceModifier
import androidx.glance.ImageProvider
import androidx.glance.action.clickable
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
import androidx.glance.LocalContext
import androidx.glance.Image
import androidx.glance.action.actionParametersOf
import androidx.glance.appwidget.action.actionRunCallback
import androidx.glance.appwidget.action.actionStartActivity
import androidx.glance.semantics.contentDescription
import androidx.glance.semantics.semantics
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.ui.today.TodayWidgets

/**
 * Glass timeline (AgendaStyle.GLASS): a frosted panel; the date with the weather at its end; today's
 * hours as a timeline (hour lines, event blocks split into columns where they overlap, a now-line)
 * and the next day's first event under it; the tasks in a column on the end side. Narrow (3×2), the
 * first two tasks sit in a row under the timeline. The header keeps one size at every height; the
 * timeline shows as many hours as fit at full size, and the tasks scroll.
 */
@Composable
internal fun GlassAgenda(m: AgendaModel, s: AgendaSize, widgetId: Int, columns: Boolean = true) {
    val d = GlassDims
    val pad = d.PAD
    val innerW = s.width - (pad + 2) * 2
    val tasksW = if (s.narrow) 0f else (s.width - pad * 2) * 0.43f
    val timelineW = if (s.narrow) innerW else innerW - tasksW - 25f
    val tlH = d.timelineHeight(s.height, s.narrow)
    Column(GlanceModifier.fillMaxSize().background(ImageProvider(R.drawable.agenda_glass_bg))
        .padding(horizontal = (pad + 2).dp, vertical = pad.dp)) {
        Row(GlanceModifier.fillMaxWidth().height(d.HEADER.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(GlanceModifier.defaultWeight().fillMaxHeight().talk(m), verticalAlignment = Alignment.CenterVertically) {
                Txt(m.weekday, 12f, AgendaInk.accent, weight = FontWeight.Medium)
                Txt(m.dayMonth, 23f, AgendaInk.text)
            }
            WeatherSpot(m, widgetId) { w ->
                Row(GlanceModifier.fillMaxHeight().padding(start = 8.dp).weatherTap(w), verticalAlignment = Alignment.CenterVertically) {
                    WeatherIconImage(w.icon, 34.dp)
                    Spacer(GlanceModifier.width(8.dp))
                    Column(horizontalAlignment = Alignment.End) {
                        Txt(w.tempLine, 27f, AgendaInk.text, family = LIGHT)
                        Txt(w.rangeLine, 10.5f, AgendaInk.text2, GlanceModifier.padding(top = 1.dp))
                    }
                }
            }
        }
        Spacer(GlanceModifier.height(d.GAP.dp))
        Hairline()
        Spacer(GlanceModifier.height(d.GAP.dp))
        Row(GlanceModifier.fillMaxWidth().defaultWeight()) {
            Column(GlanceModifier.defaultWeight().fillMaxHeight()) {
                if (!m.ready || m.timeline.isEmpty() && m.eventCount == 0) {
                    Box(GlanceModifier.fillMaxWidth().height(tlH.dp)) { Quiet(emptyEvents(m), GlanceModifier.padding(start = 4.dp)) }
                } else {
                    Timeline(m, timelineW, tlH)
                }
                NextDayFoot(m, columns, timelineW - 27f)
                if (s.narrow && m.ready) NarrowTasks(m)
            }
            if (!s.narrow && m.ready) {
                Box(GlanceModifier.width(25.dp).fillMaxHeight().padding(start = 12.dp, end = 12.dp, top = 2.dp)) { Hairline(vertical = true) }
                Column(GlanceModifier.width(tasksW.dp).fillMaxHeight()) {
                    TasksColumn(m, d.TASK_HEAD, d.TASK_ROW)
                }
            }
        }
    }
}

/** The glass timeline's fixed heights in dp: the same at every widget height. Only the hours shown change. */
internal object GlassDims {
    const val PAD = 14f
    const val HEADER = 54f
    const val GAP = 5f
    const val FOOT = 22f
    const val NARROW_TASKS = 32f
    /** The least room an hour gets on the timeline. */
    const val HOUR = 15f
    const val MIN_HOURS = 3
    const val MAX_HOURS = 13
    const val TASK_HEAD = 18f
    const val TASK_ROW = 34f

    /** The padding, the header and its rule: what the body does not get. */
    fun top(): Float = PAD * 2 + HEADER + GAP * 2 + 1

    /** The timeline's height in a widget [height] dp tall: the body less the next-day line (and, narrow, the tasks row). */
    fun timelineHeight(height: Float, narrow: Boolean): Float =
        maxOf(height - top() - FOOT - (if (narrow) NARROW_TASKS else 0f), MIN_HOURS * HOUR)

    /** The hours that fit [timelineHeight] at [HOUR] dp or more each, from [MIN_HOURS] to [MAX_HOURS]. */
    fun hours(timelineHeight: Float): Int = (timelineHeight / HOUR).toInt().coerceIn(MIN_HOURS, MAX_HOURS)

    /** The top with the fewest hours and the next-day line, or the tasks heading and one task, whichever is taller. */
    fun need(narrow: Boolean): Float =
        top() + maxOf(MIN_HOURS * HOUR + FOOT + (if (narrow) NARROW_TASKS else 0f), if (narrow) 0f else TASK_HEAD + TASK_ROW)
}

/** Today's hours: hour lines, the event blocks, the now-line, in three layers. */
@Composable
private fun Timeline(m: AgendaModel, width: Float, height: Float) {
    val lay = AgendaTimeline.layout(m.timeline, m.nowMinute, height, GlassDims.hours(height), 2, 24f)
    val left = 27f
    val colsW = width - left
    Box(GlanceModifier.fillMaxWidth().height(height.dp)) {
        // Hour lines and their labels; none near now, where the now-line is, and none the bottom edge would cut.
        Box(GlanceModifier.fillMaxSize()) {
            lay.hours.filter { it.label && it.y + 6f <= height }.take(9).forEach { h ->
                Box(GlanceModifier.fillMaxSize().padding(top = maxOf(0f, h.y - 6f).dp)) {
                    Row(GlanceModifier.fillMaxWidth().height(12.dp), verticalAlignment = Alignment.CenterVertically) {
                        Txt(h.hour.toString(), 9.5f, AgendaInk.text3, GlanceModifier.width(20.dp), align = TextAlign.End)
                        Spacer(GlanceModifier.width(4.dp))
                        Hairline(AgendaInk.hair, modifier = GlanceModifier.defaultWeight())
                    }
                }
            }
        }
        // The blocks.
        lay.blocks.take(16).chunked(8).forEach { group ->
            Box(GlanceModifier.fillMaxSize()) {
                group.forEach { b ->
                    val w = colsW / b.cols
                    Box(GlanceModifier.fillMaxSize().padding(start = (left + w * b.col).dp, top = b.top.dp)) {
                        Block(b, w - if (b.cols > 1) 2f else 0f, b.height - 2f)
                    }
                }
            }
        }
        // The now-line: a dot and a line in the accent colour.
        lay.nowY?.let { y ->
            Box(GlanceModifier.fillMaxSize().padding(top = maxOf(0f, y - 4.5f).dp)) {
                Row(GlanceModifier.fillMaxWidth().height(9.dp).padding(start = 19.dp), verticalAlignment = Alignment.CenterVertically) {
                    Image(ImageProvider(R.drawable.widget_circle), null, GlanceModifier.size(9.dp), colorFilter = ColorFilter.tint(AgendaInk.accent))
                    Box(GlanceModifier.defaultWeight().height(2.dp).background(AgendaInk.accent)) {}
                }
            }
        }
    }
}

/** An event block: tinted in the event's colour with a bar at its start; ended ones fainter. */
@Composable
private fun Block(b: AgendaTimeline.Block, width: Float, height: Float) {
    val e = b.timed.event
    val c = AgendaInk.event(e.calendar, e.colour)
    val past = b.timed.past
    Row(GlanceModifier.width(width.dp).height(height.dp)
        .background(ImageProvider(if (past) R.drawable.agenda_glass_block_past else R.drawable.agenda_glass_block), colorFilter = ColorFilter.tint(c))
        .eventTarget(e, openEvent(e))) {
        Box(GlanceModifier.width(3.dp).fillMaxHeight()
            .background(ImageProvider(if (past) R.drawable.agenda_bar_faint else R.drawable.widget_bar), colorFilter = ColorFilter.tint(c))) {}
        Column(GlanceModifier.defaultWeight().fillMaxHeight().padding(horizontal = 7.dp, vertical = (if (height > 30) 4 else 2).dp),
            verticalAlignment = Alignment.CenterVertically) {
            Txt(e.title, 12f, if (past) AgendaInk.text2 else AgendaInk.text, weight = FontWeight.Medium)
            if (height >= 34) {
                val sub = listOfNotNull("${e.time} – ${e.endTime}", e.place).joinToString(" · ")
                Txt(sub, 10f, if (past) AgendaInk.text3 else AgendaInk.text2)
            }
        }
    }
}

/**
 * "Thu  10:00  Office hours  +1": the first event of the next day that has any (opens that day). With
 * [columns], the day and the time sit in boxes as wide as the widest day and time (AgendaColumns),
 * so the title starts at the same place whatever the day; when that leaves the title less than
 * AgendaColumns.MIN_TITLE of the [width] dp (the wide widget, where the tasks take the end side),
 * the line runs on, as one line has nothing to line up with.
 */
@Composable
private fun NextDayFoot(m: AgendaModel, on: Boolean, width: Float) {
    val day = m.days.drop(1).firstOrNull { it.events.isNotEmpty() } ?: return
    val e = day.events.first()
    val ctx = LocalContext.current
    val sp = 11f
    val label = AgendaModel.relativeDay(day.date, m.days.first().date)
    val dayW = maxOf(AgendaColumns.dayWidth(ctx, sp, medium = true), AgendaColumns.width(ctx, label, sp, medium = true))
    val timeW = AgendaColumns.timeWidth(ctx, sp, m.h24, listOf(AppText.get(R.string.agenda_all_day_short)))
    val plus = if (day.events.size > 1) 24f else 0f
    val columns = on && width - dayW - timeW - AgendaColumns.GAP * 2 - plus >= AgendaColumns.MIN_TITLE
    Row(GlanceModifier.fillMaxWidth().height(22.dp).padding(start = 27.dp, top = 2.dp).dayTarget(day.label, openDay(day.date)),
        verticalAlignment = Alignment.CenterVertically) {
        val dayMod = if (columns) GlanceModifier.width(dayW.dp) else GlanceModifier
        Txt(label, sp, AgendaInk.accent, dayMod, weight = FontWeight.Medium)
        Spacer(GlanceModifier.width(AgendaColumns.GAP.dp))
        if (columns) {
            Txt(if (e.allDay) AppText.get(R.string.agenda_all_day_short) else e.time, sp, AgendaInk.text2, GlanceModifier.width(timeW.dp))
            Spacer(GlanceModifier.width(AgendaColumns.GAP.dp))
        } else if (!e.allDay) {
            Txt(e.time, sp, AgendaInk.text2)
            Spacer(GlanceModifier.width(AgendaColumns.GAP.dp))
        }
        Txt(e.title, sp, AgendaInk.text, GlanceModifier.defaultWeight())
        if (day.events.size > 1) Txt(AppText.get(R.string.agenda_plus, day.events.size - 1), sp, AgendaInk.text3, GlanceModifier.padding(start = 6.dp))
    }
}

/** Narrow: the first two tasks side by side under the timeline. */
@Composable
private fun NarrowTasks(m: AgendaModel) {
    val ctx = LocalContext.current
    Row(GlanceModifier.fillMaxWidth().height(24.dp), verticalAlignment = Alignment.CenterVertically) {
        m.tasks.take(2).forEachIndexed { i, t ->
            if (i > 0) Spacer(GlanceModifier.width(10.dp))
            Row(GlanceModifier.defaultWeight().fillMaxHeight(), verticalAlignment = Alignment.CenterVertically) {
                Box(GlanceModifier.width(20.dp).fillMaxHeight()
                    .clickable(actionRunCallback<CompleteAgendaTaskAction>(actionParametersOf(KEY_AGENDA_TASK to t.uid)))
                    .semantics { contentDescription = AppText.get(R.string.today_complete_cd, t.title) }, contentAlignment = Alignment.Center) {
                    Image(ImageProvider(R.drawable.agenda_ring), null, GlanceModifier.size(14.dp),
                        colorFilter = ColorFilter.tint(if (t.overdue) AgendaInk.danger else AgendaInk.accent))
                }
                Spacer(GlanceModifier.width(4.dp))
                Txt(t.title, 11.5f, AgendaInk.text, GlanceModifier.defaultWeight()
                    .clickable(actionStartActivity(TodayWidgets.open(ctx, TodayWidgets.taskUri(t.uid)))))
            }
        }
        if (m.tasks.isEmpty()) Quiet(AppText.get(R.string.agenda_no_tasks))
    }
}
