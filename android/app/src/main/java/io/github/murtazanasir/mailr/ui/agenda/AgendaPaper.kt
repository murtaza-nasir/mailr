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
import androidx.glance.text.FontFamily
import androidx.glance.text.FontWeight
import androidx.glance.text.TextAlign
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText

/**
 * Paper & rail (AgendaStyle.PAPER): a paper card with a serif date and the week number; the days on
 * a thin rail, each event a dot in its calendar's colour (today's ended events faded); the tasks on a
 * sheet whose corner is turned over, the paper fold of the mailr mark. The header keeps one size at
 * every height; the rail and the tasks scroll, each on its own.
 */
@Composable
internal fun PaperAgenda(m: AgendaModel, s: AgendaSize, widgetId: Int, columns: Boolean = true) {
    val d = PaperDims
    val sheetW = if (s.narrow) 118f else s.width * 0.42f
    Column(GlanceModifier.fillMaxSize().background(ImageProvider(R.drawable.agenda_paper_bg))) {
        Row(GlanceModifier.fillMaxWidth().height(d.HEADER.dp).padding(start = 18.dp, end = 16.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(GlanceModifier.defaultWeight().fillMaxHeight().talk(m), verticalAlignment = Alignment.CenterVertically) {
                Txt(if (s.narrow) m.dateShort else m.dateLong, 20f, AgendaInk.text, family = FontFamily.Serif)
                Txt(subline(m), 10.5f, AgendaInk.text3, GlanceModifier.padding(top = 2.dp))
            }
            WeatherSpot(m, widgetId) { w ->
                Row(GlanceModifier.fillMaxHeight().padding(start = 10.dp).weatherTap(w), verticalAlignment = Alignment.CenterVertically) {
                    WeatherIconImage(w.icon, 26.dp)
                    Spacer(GlanceModifier.width(6.dp))
                    Column(horizontalAlignment = Alignment.End) {
                        Txt(w.tempLine, 20f, AgendaInk.text, family = FontFamily.Serif)
                        Txt(AppText.get(R.string.agenda_high_low_dot, w.high, w.low), 10f, AgendaInk.text2)
                    }
                }
            }
        }
        Box(GlanceModifier.fillMaxWidth().height(d.RULE.dp).padding(horizontal = 16.dp)) { Hairline() }
        Row(GlanceModifier.fillMaxSize().padding(start = 8.dp, end = 8.dp, top = d.PAD_TOP.dp, bottom = d.PAD_BOTTOM.dp)) {
            Column(GlanceModifier.defaultWeight().fillMaxHeight().padding(start = 2.dp)) {
                val rows = PaperRail.rows(m, s.height - d.top())
                if (rows.isEmpty()) Quiet(emptyEvents(m), GlanceModifier.padding(start = 12.dp))
                // The rail hangs on the time column, so it stays a column even with "Line up days and times" off:
                // as wide as the widest time (or "all day") on the phone's clock (AgendaColumns), the days as headings.
                val ctx = LocalContext.current
                val timeW = AgendaColumns.timeWidth(ctx, 10.5f, m.h24, listOf(AppText.get(R.string.agenda_all_day_short))) + 2f
                if (rows.isNotEmpty()) ScrollList(rows) { r ->
                    when (r) {
                        is PaperRail.Row.Head -> RailHead(r.day, r.first, timeW)
                        is PaperRail.Row.Ev -> RailEvent(r.e, timeW)
                    }
                }
            }
            Spacer(GlanceModifier.width(6.dp))
            Box(GlanceModifier.width(sheetW.dp).fillMaxHeight().background(ImageProvider(R.drawable.agenda_paper_sheet))) {
                Column(GlanceModifier.fillMaxSize().padding(start = 12.dp, end = 10.dp, top = d.SHEET_TOP.dp, bottom = d.SHEET_BOTTOM.dp)) {
                    TasksColumn(m, d.TASK_HEAD, d.TASK_ROW, fs = 12f, label = AppText.get(R.string.agenda_to_do), serif = true)
                }
                // The turned corner: the fold in the accent colour over the corner it uncovers.
                Box(GlanceModifier.fillMaxSize(), contentAlignment = Alignment.TopEnd) {
                    Box(GlanceModifier.size(20.dp)) {
                        Image(ImageProvider(R.drawable.widget_fold), null, GlanceModifier.fillMaxSize(), colorFilter = ColorFilter.tint(AgendaInk.paperEdge))
                        Image(ImageProvider(R.drawable.widget_fold_cut), null, GlanceModifier.fillMaxSize(), colorFilter = ColorFilter.tint(AgendaInk.accent))
                    }
                }
            }
        }
    }
}

/** Paper & rail's fixed heights in dp: the same at every widget height. */
internal object PaperDims {
    const val HEADER = 56f
    const val RULE = 1f
    const val PAD_TOP = 9f
    const val PAD_BOTTOM = 8f
    const val HEAD = 21f
    const val EV = 27f
    const val SHEET_TOP = 9f
    const val SHEET_BOTTOM = 6f
    const val TASK_HEAD = 18f
    const val TASK_ROW = 33f

    /** The header, the rule and the padding: what the lists do not get. */
    fun top(): Float = HEADER + RULE + PAD_TOP + PAD_BOTTOM

    /** The top with a day heading and one event, or the sheet with its heading and one task, whichever is taller. */
    fun need(): Float = top() + maxOf(HEAD + EV, SHEET_TOP + SHEET_BOTTOM + TASK_HEAD + TASK_ROW)
}

/** "Week 41 · 4 events today". */
private fun subline(m: AgendaModel): String =
    AppText.get(R.string.agenda_week_line, m.weekNumber, AppText.plural(R.plurals.agenda_events_today, m.eventsToday, m.eventsToday))

/**
 * The rail's rows: every day with events. Today's ended events stay (faded) while today's rows still
 * fit the visible [height]; the oldest give way first. The list scrolls, so nothing else is cut.
 */
object PaperRail {
    sealed class Row {
        class Head(val day: AgendaModel.Day, val first: Boolean) : Row()
        class Ev(val e: AgendaModel.Event) : Row()
    }

    fun rows(m: AgendaModel, height: Float): List<Row> {
        if (!m.ready) return emptyList()
        val todayEvents = m.days.firstOrNull()?.takeIf { it.today }?.events?.size ?: 0
        var earlier = m.earlier
        while (earlier.isNotEmpty() && PaperDims.HEAD + (earlier.size + todayEvents) * PaperDims.EV > height) earlier = earlier.drop(1)
        return buildList {
            m.days.forEach { d ->
                val evs = if (d.today) earlier + d.events else d.events
                if (evs.isEmpty()) return@forEach
                add(Row.Head(d, isEmpty()))
                evs.forEach { add(Row.Ev(it)) }
            }
        }
    }
}

/** A day heading on the rail: "Today", "Thursday 8" in italic serif (opens the day). */
@Composable
private fun RailHead(d: AgendaModel.Day, first: Boolean, timeW: Float) {
    Row(GlanceModifier.fillMaxWidth().height(PaperDims.HEAD.dp).dayTarget(d.label, openDay(d.date))) {
        Spacer(GlanceModifier.width(timeW.dp))
        Box(GlanceModifier.width(13.dp).fillMaxHeight(), contentAlignment = Alignment.Center) { Hairline(vertical = true) }
        Column(GlanceModifier.defaultWeight().fillMaxHeight().padding(start = 6.dp, top = (if (first) 0 else 4).dp),
            verticalAlignment = Alignment.CenterVertically) {
            Txt(d.longLabel, 12f, AgendaInk.accent, family = FontFamily.Serif, italic = true)
        }
    }
}

/** An event on the rail: its time, a dot in its colour ringed with the paper, its title (opens the event). */
@Composable
private fun RailEvent(e: AgendaModel.Event, timeW: Float) {
    val c = AgendaInk.event(e.calendar, e.colour)
    Row(GlanceModifier.fillMaxWidth().height(PaperDims.EV.dp).eventTarget(e, openEvent(e)),
        verticalAlignment = Alignment.CenterVertically) {
        Txt(if (e.allDay) AppText.get(R.string.agenda_all_day_short) else e.time, 10.5f, if (e.past) AgendaInk.text3 else AgendaInk.text2,
            GlanceModifier.width(timeW.dp).padding(end = 2.dp), align = TextAlign.End)
        Box(GlanceModifier.width(13.dp).fillMaxHeight(), contentAlignment = Alignment.Center) {
            Hairline(vertical = true)
            Image(ImageProvider(R.drawable.widget_circle), null, GlanceModifier.size(13.dp), colorFilter = ColorFilter.tint(AgendaInk.paperEdge))
            Image(ImageProvider(if (e.past) R.drawable.agenda_dot_faint else R.drawable.widget_circle), null, GlanceModifier.size(9.dp),
                colorFilter = ColorFilter.tint(c))
        }
        Txt(e.title, 12.5f, if (e.past) AgendaInk.text3 else AgendaInk.text, GlanceModifier.defaultWeight().padding(start = 6.dp),
            weight = if (e.allDay) FontWeight.Medium else null)
    }
}
