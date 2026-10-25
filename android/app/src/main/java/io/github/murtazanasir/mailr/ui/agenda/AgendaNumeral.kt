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
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText

/**
 * Numeral duotone (AgendaStyle.NUMERAL): two translucent halves. The start half holds the date as a
 * large light numeral, the next event on a compact card ("14:30 · in 25 min · 30 min · Video call"
 * within the hour, kept true by a redraw each minute; its title on up to two lines) and the events
 * after it; the end half holds the weather with an hourly strip and the tasks. The numeral and the
 * weather keep one size at every height, the card one of two (a title of one line or two); the
 * events after the next one and the tasks scroll, each list on its own.
 *
 * The events after the next one have no day column, so no width goes to the left edge. A
 * day's name heads its first event ("Tomorrow", "Mon", "Mon 19 Oct"), and each row is the colour
 * line, the time and the title, so titles get the width. The card is a row of the same kind with a
 * little more weight, and the numeral is smaller than before, so more rows fit.
 */
@Composable
internal fun NumeralAgenda(m: AgendaModel, s: AgendaSize, widgetId: Int, columns: Boolean = true) {
    val d = NumeralDims
    val startW = s.width * (if (s.narrow) 0.6f else 0.57f)
    val inner = startW - d.PAD_START - d.PAD_END
    Box(GlanceModifier.fillMaxSize()) {
        Box(GlanceModifier.fillMaxSize().background(ImageProvider(R.drawable.agenda_numeral_frame))) {}
        Row(GlanceModifier.fillMaxSize()) {
            Column(GlanceModifier.width(startW.dp).fillMaxHeight().background(ImageProvider(R.drawable.agenda_numeral_start))
                .padding(start = d.PAD_START.dp, end = d.PAD_END.dp, top = d.PAD_TOP.dp, bottom = d.PAD_BOTTOM.dp)) {
                Row(GlanceModifier.fillMaxWidth().height(d.HEADER.dp).talk(m), verticalAlignment = Alignment.CenterVertically) {
                    Txt(m.dayNumber, 56f, AgendaInk.text, family = LIGHT)
                    Spacer(GlanceModifier.width(8.dp))
                    Column {
                        Txt(m.weekday, 15f, AgendaInk.text, weight = FontWeight.Medium)
                        Txt(m.month, 12.5f, AgendaInk.text2)
                    }
                }
                NextCard(m, inner)
                Spacer(GlanceModifier.height(d.GAP.dp))
                if (m.upcoming.isNotEmpty()) {
                    val timeW = numeralTime(columns, m.h24)
                    ScrollList(NumeralList.rows(m.upcoming, m.next?.day)) { r ->
                        when (r) {
                            is NumeralList.Row.Day -> DayLabel(r.day)
                            is NumeralList.Row.Ev -> UpcomingRow(r.u, d.ROW, timeW)
                        }
                    }
                }
            }
            Column(GlanceModifier.defaultWeight().fillMaxHeight().background(ImageProvider(R.drawable.agenda_numeral_end))
                .padding(start = 13.dp, end = 12.dp, top = d.PAD_TOP.dp, bottom = d.PAD_BOTTOM.dp)) {
                val hasWeather = m.weather != null || m.weatherHint || m.undo != null
                if (hasWeather) {
                    Column(GlanceModifier.fillMaxWidth().height(d.WEATHER.dp), verticalAlignment = Alignment.CenterVertically) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            WeatherSpot(m, widgetId) { w -> WeatherNow(w, s) }
                        }
                        val w = m.weather
                        if (w != null && m.hourly.isNotEmpty()) HourlyStrip(m.hourly, w, s.width - startW - 25f)
                    }
                    Box(GlanceModifier.fillMaxWidth().height(d.RULE.dp).padding(top = 4.dp, bottom = 5.dp)) { Hairline() }
                }
                TasksColumn(m, d.TASK_HEAD, d.TASK_ROW, circle = 15f, fs = 12f)
            }
        }
    }
}

/** The numeral duotone's fixed sizes in dp: the same at every widget height. */
internal object NumeralDims {
    const val PAD_TOP = 10f
    const val PAD_BOTTOM = 8f
    const val PAD_START = 12f
    const val PAD_END = 10f
    const val HEADER = 60f
    /** The card with a title of one line, and of two. */
    const val CARD = 46f
    const val CARD_TWO = 63f
    const val GAP = 6f
    const val ROW = 22f
    const val DAY = 16f
    const val WEATHER = 94f
    const val RULE = 10f
    const val TASK_HEAD = 16f
    const val TASK_ROW = 31f

    /** The taller half with one row of its list: the numeral, the card (two lines) and one event after it. */
    fun need(): Float = PAD_TOP + PAD_BOTTOM + maxOf(HEADER + CARD_TWO + GAP + ROW, WEATHER + RULE + TASK_HEAD + TASK_ROW)
}

/** The temperature now, large and light, with today's high over its low. Opens the weather app. */
@Composable
private fun WeatherNow(w: AgendaModel.Weather, s: AgendaSize) {
    Row(GlanceModifier.weatherTap(w), verticalAlignment = Alignment.CenterVertically) {
        WeatherIconImage(w.icon, 32.dp)
        Spacer(GlanceModifier.width(8.dp))
        Txt(w.tempLine, 30f, AgendaInk.text, family = LIGHT)
        if (!s.narrow) {
            Column(GlanceModifier.padding(start = 6.dp)) {
                Txt(AppText.get(R.string.agenda_degrees, w.high), 10f, AgendaInk.text2)
                Txt(AppText.get(R.string.agenda_degrees, w.low), 10f, AgendaInk.text3)
            }
        }
    }
}

/**
 * The next hours: "15  18  21  00", each with its picture and temperature. Part of the weather's
 * target. In [width] dp, as many slots as fit their widest label ("12 pm" on a 12-hour clock): the
 * narrow (3×2) widget on a 12-hour clock shows three, so the labels never run together ("3 pm6 pm9 pm").
 */
@Composable
private fun HourlyStrip(slots: List<AgendaModel.HourSlot>, w: AgendaModel.Weather, width: Float) {
    val ctx = LocalContext.current
    val shown = hourlyFit(slots, width) { AgendaColumns.widest(ctx, it, HOUR_SP) }
    Row(GlanceModifier.fillMaxWidth().padding(top = 8.dp).weatherTap(w)) {
        shown.forEach { h ->
            Column(GlanceModifier.defaultWeight(), horizontalAlignment = Alignment.CenterHorizontally) {
                Txt(h.label, HOUR_SP, AgendaInk.text3)
                WeatherIconImage(h.icon, 15.dp)
                Txt(AppText.get(R.string.agenda_degrees, h.temp), 10.5f, AgendaInk.text)
            }
        }
    }
}

private const val HOUR_SP = 9.5f

/** The first slots whose widest label (with a little space between) fits [width] dp; never fewer than two. */
internal fun hourlyFit(slots: List<AgendaModel.HourSlot>, width: Float, measure: (List<String>) -> Float): List<AgendaModel.HourSlot> {
    var n = slots.size
    while (n > 2 && measure(slots.take(n).map { it.label }) + 4f > width / n) n--
    return slots.take(n)
}

/**
 * The next event on a card: the colour line, then "14:30 · in 25 min" with its length and place after
 * it ("· 30 min · Video call"), and the title under them, on two lines when one is too short for it
 * ([inner]: the start half's width inside its padding, in dp). Opens the event.
 */
@Composable
private fun NextCard(m: AgendaModel, inner: Float) {
    val n = m.next
    val ctx = LocalContext.current
    val two = n != null && AgendaColumns.width(ctx, n.event.title, CARD_TITLE_SP, medium = true) > inner - CARD_CHROME
    var mod = GlanceModifier.fillMaxWidth().height((if (two) NumeralDims.CARD_TWO else NumeralDims.CARD).dp)
        .background(ImageProvider(R.drawable.agenda_numeral_card)).padding(start = 7.dp, end = 8.dp, top = 6.dp, bottom = 6.dp)
    if (n != null) mod = mod.eventTarget(n.event, openEvent(n.event))
    Row(mod, verticalAlignment = Alignment.CenterVertically) {
        if (n == null) {
            Txt(emptyEvents(m).takeIf { !m.ready } ?: AppText.get(R.string.agenda_nothing_today), 12f, AgendaInk.text2, maxLines = 2)
            return@Row
        }
        val e = n.event
        Box(GlanceModifier.width(3.dp).fillMaxHeight()
            .background(ImageProvider(R.drawable.widget_bar), colorFilter = ColorFilter.tint(AgendaInk.event(e.calendar, e.colour)))) {}
        Spacer(GlanceModifier.width(7.dp))
        Column(GlanceModifier.defaultWeight(), verticalAlignment = Alignment.CenterVertically) {
            Row(GlanceModifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Txt(nextLabel(n), 10.5f, AgendaInk.accent, weight = FontWeight.Medium)
                cardDetail(e.place, n.durationMin)?.let { Txt(it, 10.5f, AgendaInk.text2, GlanceModifier.defaultWeight()) }
            }
            Txt(e.title, CARD_TITLE_SP, AgendaInk.text, weight = FontWeight.Medium, maxLines = if (two) 2 else 1)
        }
    }
}

private const val CARD_TITLE_SP = 13.5f
/** What the card takes from the start half's width besides the title: its padding, the line and the gap after it. */
private const val CARD_CHROME = 7f + 8f + 3f + 7f

/** " · 30 min · Video call" after the card's time (the length first: a long place is cut at the end); null when there is neither. */
internal fun cardDetail(place: String?, durationMin: Int): String? =
    listOfNotNull(duration(durationMin), place).takeIf { it.isNotEmpty() }?.joinToString(" · ", prefix = " · ")

/** "14:30 · in 25 min", "Now · until 15:00", "Tomorrow 10:00", "Mon 9:00", "Mon 19 Oct 9:00" or "14:30". */
internal fun nextLabel(n: AgendaModel.Next): String = when {
    n.underWay -> AppText.get(R.string.agenda_now_until, n.endTime)
    n.minutes != null -> AppText.get(R.string.agenda_in_min, n.event.time, n.minutes)
    n.day != null -> "${n.day} ${n.event.time}"
    else -> n.event.time
}

/** "30 min", "1 h", "1 h 15 min". */
internal fun duration(min: Int): String? = when {
    min <= 0 -> null
    min < 60 -> AppText.get(R.string.agenda_minutes, min)
    min % 60 == 0 -> AppText.get(R.string.agenda_hours, min / 60)
    else -> AppText.get(R.string.agenda_hours_minutes, min / 60, min % 60)
}

/** The time column's width in dp (as wide as the widest time), or null when "Line up days and times" is off. */
@Composable
private fun numeralTime(on: Boolean, h24: Boolean): Float? =
    if (on) AgendaColumns.timeWidth(LocalContext.current, ROW_SP, h24) else null

private const val ROW_SP = 10.5f
/** The colour line and the space after it. */
private const val BAR = 9f

/**
 * The events after the next one, with a day's name over its first event. The rows that go on with
 * the card's day ([after]: the card's day, null today) get none, so today's events follow the card
 * as they are.
 */
object NumeralList {
    sealed class Row {
        class Day(val day: String) : Row()
        class Ev(val u: AgendaModel.Upcoming) : Row()
    }

    fun rows(upcoming: List<AgendaModel.Upcoming>, after: String?): List<Row> = buildList {
        var last: String? = after
        upcoming.forEach { u ->
            if (u.day != null && u.day != last) add(Row.Day(u.day))
            last = u.day
            add(Row.Ev(u))
        }
    }
}

/** A later day's name over its first event, from the time column on: "Tomorrow", "Mon", "Mon 19 Oct". */
@Composable
private fun DayLabel(day: String) {
    Row(GlanceModifier.fillMaxWidth().height(NumeralDims.DAY.dp).padding(start = BAR.dp), verticalAlignment = Alignment.Bottom) {
        Txt(day, 10f, AgendaInk.accent, weight = FontWeight.Medium)
    }
}

/**
 * An event after the next one: the colour line, its time and its title. With [timeW] the time sits
 * in a box as wide as the widest time, so titles start at the same place; null: the title follows
 * the time. Opens the event.
 */
@Composable
private fun UpcomingRow(u: AgendaModel.Upcoming, height: Float, timeW: Float?) {
    val e = u.event
    Row(GlanceModifier.fillMaxWidth().height(height.dp).eventTarget(e, openEvent(e)), verticalAlignment = Alignment.CenterVertically) {
        Box(GlanceModifier.width(3.dp).height(13.dp)
            .background(ImageProvider(R.drawable.widget_bar), colorFilter = ColorFilter.tint(AgendaInk.event(e.calendar, e.colour)))) {}
        Spacer(GlanceModifier.width((BAR - 3f).dp))
        Txt(e.time, ROW_SP, AgendaInk.text2, if (timeW != null) GlanceModifier.width(timeW.dp) else GlanceModifier)
        Spacer(GlanceModifier.width(AgendaColumns.GAP.dp))
        Txt(e.title, 12f, AgendaInk.text, GlanceModifier.defaultWeight())
    }
}
