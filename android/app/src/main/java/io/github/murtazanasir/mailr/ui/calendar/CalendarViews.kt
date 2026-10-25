package io.github.murtazanasir.mailr.ui.calendar

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.time.AppClock
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.i18n.LocaleFormats
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectDragGesturesAfterLongPress
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.EventAvailable
import androidx.compose.material.icons.outlined.MailOutline
import androidx.compose.material.icons.outlined.Repeat
import androidx.compose.material.icons.outlined.Videocam
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.onClick
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.reminders.ReminderPlan
import io.github.murtazanasir.mailr.ui.components.EmptyState
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.time.format.TextStyle
import java.util.Locale

/** An event as the views need it: local start/end and its colour. */
data class DayEvent(val e: CalendarEvent, val start: LocalDateTime, val end: LocalDateTime, val color: Color) {
    /** All-day, or spanning days (an end at midnight belongs to the day before). */
    val multiDay: Boolean get() = e.allDay ||
        (if (end.toLocalTime() == LocalTime.MIDNIGHT) end.toLocalDate().minusDays(1) else end.toLocalDate()) != start.toLocalDate()

    fun covers(d: LocalDate): Boolean = !start.toLocalDate().isAfter(d) &&
        (end.toLocalDate().isAfter(d) || (end.toLocalDate() == d && (end.toLocalTime() > LocalTime.MIDNIGHT || start.toLocalDate() == d)))
}

/**
 * Events as wall-clock times in [zone] (the display zone: the phone's, or home when travelling).
 * All-day events are dates and never shift between zones.
 */
fun dayEvents(events: List<CalendarEvent>, cols: List<CalendarCollection>, zone: ZoneId = ZoneId.systemDefault()): List<DayEvent> {
    val colors = cols.associate { it.url to it.color }
    return events.mapNotNull { e ->
        val color = CalendarColors.of(e.calendar, e.color ?: colors[e.calendar])
        if (e.allDay) {
            val s = catching { LocalDate.parse(e.start.take(10)) }.getOrNull() ?: return@mapNotNull null
            val end = e.end?.let { catching { LocalDate.parse(it.take(10)) }.getOrNull() }?.takeIf { it.isAfter(s) } ?: s.plusDays(1)
            return@mapNotNull DayEvent(e, s.atStartOfDay(), end.atStartOfDay(), color)
        }
        val s = ReminderPlan.instant(e.start, zone) ?: return@mapNotNull null
        val end = ReminderPlan.eventEnd(e, zone) ?: s
        DayEvent(e, LocalDateTime.ofInstant(Instant.ofEpochMilli(s), zone), LocalDateTime.ofInstant(Instant.ofEpochMilli(end), zone), color)
    }
}

private val timeFmt get() = DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).withLocale(Locale.getDefault())

private fun timeRange(d: DayEvent, day: LocalDate): String = when {
    d.e.allDay && d.end.toLocalDate().minusDays(1).isAfter(d.start.toLocalDate()) -> {
        val n = java.time.temporal.ChronoUnit.DAYS.between(d.start.toLocalDate(), d.end.toLocalDate())
        AppText.get(R.string.cal_day_of, java.time.temporal.ChronoUnit.DAYS.between(d.start.toLocalDate(), day) + 1, n)
    }
    d.e.allDay -> AppText.get(R.string.cal_all_day)
    d.start.toLocalDate() != day && d.end.toLocalDate() != day -> AppText.get(R.string.cal_all_day)
    d.start.toLocalDate() != day -> AppText.get(R.string.cal_until_time, d.end.toLocalTime().format(timeFmt))
    d.end.toLocalDate() != day -> AppText.get(R.string.cal_from_time, d.start.toLocalTime().format(timeFmt))
    else -> "${d.start.toLocalTime().format(timeFmt)} – ${d.end.toLocalTime().format(timeFmt)}"
}

// ============================ schedule (agenda) ============================

@Composable
fun AgendaView(events: List<DayEvent>, anchor: LocalDate, now: Long, selected: String?, onOpen: (CalendarEvent) -> Unit,
               onCreateOn: (LocalDate) -> Unit, pending: Set<String> = emptySet()) {
    val today = AppClock.today()
    val nowTime = LocalDateTime.ofInstant(Instant.ofEpochMilli(now), ZoneId.systemDefault())
    // Days that have something, plus the anchor day so "today" is always visible.
    val from = anchor.minusDays(7)
    val days = (0L until 67L).map { from.plusDays(it) }
        .map { d -> d to events.filter { it.covers(d) }.sortedWith(compareBy({ !it.e.allDay && it.start.toLocalDate() == d }, { it.start })) }
        .filter { (d, evs) -> evs.isNotEmpty() || d == anchor || d == today }
    val rows = remember(days) {
        buildList<Any> {
            var month: Int? = null
            for (p in days) {
                if (p.first.monthValue != month) { month = p.first.monthValue; add(p.first.withDayOfMonth(1).atStartOfDay()) }
                add(p)
            }
        }
    }
    val state = rememberLazyListState()
    LaunchedEffect(anchor, rows.size) {
        @Suppress("UNCHECKED_CAST")
        val i = rows.indexOfFirst { it is Pair<*, *> && !(it.first as LocalDate).isBefore(anchor) }
        if (i >= 0) state.scrollToItem((i - 1).coerceAtLeast(0))
    }
    if (events.isEmpty() && days.size <= 2) {
        EmptyState(Icons.Outlined.EventAvailable, stringResource(R.string.cal_schedule_empty), stringResource(R.string.cal_schedule_empty_body))
        return
    }
    LazyColumn(state = state, contentPadding = PaddingValues(bottom = 96.dp), modifier = Modifier.fillMaxSize()) {
        items(rows, key = { r -> if (r is Pair<*, *>) "d-${r.first}" else "m-$r" }) { r ->
            if (r is LocalDateTime) {
                val d = r.toLocalDate()
                Text(d.format(if (d.year == today.year) LocaleFormats.date("MMMM", "MMMM") else LocaleFormats.date("yMMMM", "MMMM yyyy")),
                    style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.padding(start = 20.dp, top = 20.dp, bottom = 4.dp).semantics { heading() })
            } else {
                @Suppress("UNCHECKED_CAST")
                val pair = r as Pair<LocalDate, List<DayEvent>>
                AgendaDay(pair.first, pair.second, pair.first == today, nowTime, selected, pending, onOpen) { onCreateOn(pair.first) }
            }
        }
    }
}

@Composable
private fun AgendaDay(day: LocalDate, evs: List<DayEvent>, isToday: Boolean, now: LocalDateTime, selected: String?, pending: Set<String>,
                      onOpen: (CalendarEvent) -> Unit, onCreate: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 6.dp)) {
        Column(Modifier.width(52.dp).padding(top = 4.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            Text(day.dayOfWeek.getDisplayName(TextStyle.SHORT, Locale.getDefault()), style = MaterialTheme.typography.labelMedium,
                color = if (isToday) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
            Box(Modifier.size(36.dp).clip(CircleShape).background(if (isToday) MaterialTheme.colorScheme.primary else Color.Transparent),
                contentAlignment = Alignment.Center) {
                Text(day.dayOfMonth.toString(), style = MaterialTheme.typography.titleMedium,
                    color = if (isToday) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurface)
            }
        }
        // The web's schedule (ScheduleView .cal-row): a dense row per event, the colour as a bar; Material's look: filled cards.
        val web = io.github.murtazanasir.mailr.look.LocalLook.current.web
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(if (web) 2.dp else 6.dp)) {
            if (evs.isEmpty()) {
                Surface(onClick = onCreate, shape = MaterialTheme.shapes.small, color = Color.Transparent,
                    modifier = Modifier.fillMaxWidth().heightIn(min = 44.dp)) {
                    Box(contentAlignment = Alignment.CenterStart, modifier = Modifier.padding(horizontal = 12.dp)) {
                        Text(if (isToday) stringResource(R.string.cal_nothing_else_today) else stringResource(R.string.cal_nothing_planned), color = MaterialTheme.colorScheme.onSurfaceVariant,
                            style = MaterialTheme.typography.bodyMedium)
                    }
                }
            }
            var nowShown = !isToday
            evs.forEach { d ->
                if (!nowShown && !d.e.allDay && d.start >= now) {
                    NowLine()
                    nowShown = true
                }
                if (web) AgendaRow(d, day, selected == d.e.key, past = d.end < now, pending = d.e.key in pending) { onOpen(d.e) }
                else EventCard(d, day, selected == d.e.key, past = d.end < now, pending = d.e.key in pending) { onOpen(d.e) }
            }
            if (!nowShown && evs.isNotEmpty()) NowLine()
        }
    }
}

@Composable
private fun NowLine() {
    val c = MaterialTheme.colorScheme.error
    val nowCd = stringResource(R.string.cal_now)
    Row(Modifier.fillMaxWidth().height(10.dp).semantics { contentDescription = nowCd }, verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(8.dp).background(c, CircleShape))
        Box(Modifier.weight(1f).height(2.dp).background(c))
    }
}

@Composable
private fun EventCard(d: DayEvent, day: LocalDate, selected: Boolean, past: Boolean, pending: Boolean = false, onClick: () -> Unit) {
    val on = CalendarColors.onColor(d.color)
    val declined = d.e.myStatus == "DECLINED"
    val invite = d.e.isInvite && (d.e.myStatus == "NEEDS-ACTION" || d.e.myStatus == "TENTATIVE")
    Surface(
        onClick = onClick,
        shape = MaterialTheme.shapes.small,
        color = if (invite || declined) MaterialTheme.colorScheme.surface else d.color,
        border = if (invite || declined) BorderStroke(1.5.dp, d.color) else if (selected) BorderStroke(2.dp, MaterialTheme.colorScheme.onSurface) else null,
        // Past events fade as a whole, so their text keeps its contrast with the card.
        modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp).alpha(if (past) 0.6f else 1f).then(if (pending) Modifier.dashedBorder() else Modifier),
    ) {
        Column(Modifier.padding(horizontal = 12.dp, vertical = 8.dp)) {
            val fg = if (invite || declined) MaterialTheme.colorScheme.onSurface else on
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(d.e.summary.ifBlank { stringResource(R.string.cal_no_title) }, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium,
                    color = fg, maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false),
                    textDecoration = if (declined || d.e.cancelled) androidx.compose.ui.text.style.TextDecoration.LineThrough else null)
                if (d.e.recurring) {
                    Spacer(Modifier.width(6.dp))
                    Icon(Icons.Outlined.Repeat, stringResource(R.string.cal_repeats), Modifier.size(14.dp), tint = fg.copy(alpha = 0.8f))
                }
                if (joinLink(d.e) != null) {
                    Spacer(Modifier.width(4.dp))
                    Icon(Icons.Outlined.Videocam, stringResource(R.string.cal_video_call), Modifier.size(14.dp), tint = fg.copy(alpha = 0.8f))
                }
                if (d.e.conversationId != null) {
                    Spacer(Modifier.width(4.dp))
                    Icon(Icons.Outlined.MailOutline, stringResource(R.string.cal_from_an_email), Modifier.size(14.dp), tint = fg.copy(alpha = 0.8f))
                }
            }
            val sub = listOfNotNull(timeRange(d, day), d.e.location?.takeIf { it.isNotBlank() },
                if (invite) stringResource(R.string.cal_invitation) else null, if (pending) stringResource(R.string.cal_not_synced_yet) else null).joinToString(" · ")
            Text(sub, style = MaterialTheme.typography.bodySmall, color = fg.copy(alpha = 0.85f), maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
    }
}

// ============================ month ============================

/** A Month view drag: the event, the day it was lifted from, and the day under the finger. */
private data class MonthDrag(val ev: DayEvent, val from: LocalDate, val to: LocalDate)

/**
 * The month grid. With [actions], a long-press on an event chip (wide) or its dot (phone) lifts
 * it; a drop on another day moves it there and keeps its time (Month view).
 */
@Composable
fun MonthView(anchor: LocalDate, events: List<DayEvent>, wide: Boolean, onPickDay: (LocalDate) -> Unit, onOpenDay: (LocalDate) -> Unit,
              modifier: Modifier = Modifier, actions: GridActions? = null) {
    val first = weekStart(anchor.withDayOfMonth(1))
    val today = AppClock.today()
    val density = LocalDensity.current
    val rtl = LocalLayoutDirection.current == LayoutDirection.Rtl
    val haptic = androidx.compose.ui.platform.LocalHapticFeedback.current
    var drag by remember(anchor) { mutableStateOf<MonthDrag?>(null) }
    Column(modifier) {
        Row(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
            (0L until 7L).forEach { i ->
                Text(first.plusDays(i).dayOfWeek.getDisplayName(TextStyle.NARROW, Locale.getDefault()),
                    style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.weight(1f), textAlign = androidx.compose.ui.text.style.TextAlign.Center)
            }
        }
        (0 until 6).forEach { w ->
            Row(Modifier.fillMaxWidth().weight(1f)) {
                (0 until 7).forEach { dIdx ->
                    val d = first.plusDays((w * 7 + dIdx).toLong())
                    val inMonth = d.month == anchor.month
                    val evs = events.filter { it.covers(d) }.sortedWith(compareBy({ !it.e.allDay }, { it.start }))
                    val dayName = d.format(LocaleFormats.date("EEEEMMMMd", "EEEE, MMMM d"))
                    val label = if (evs.isEmpty()) stringResource(R.string.cal_month_day_no_events, dayName)
                        else pluralStringResource(R.plurals.cal_month_day_events, evs.size, dayName, evs.size)
                    val selectLabel = stringResource(R.string.cal_select)
                    val target = drag?.to == d && drag?.from != d
                    Column(
                        Modifier.weight(1f).fillMaxHeight().border(if (target) 2.dp else 0.5.dp,
                            if (target) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.5f))
                            .background(when {
                                target -> MaterialTheme.colorScheme.primary.copy(alpha = 0.14f)
                                d == anchor -> MaterialTheme.colorScheme.secondaryContainer.copy(alpha = 0.6f)
                                else -> Color.Transparent
                            })
                            .semantics(mergeDescendants = true) {
                                contentDescription = label
                                onClick(selectLabel) { onPickDay(d); true }
                            }
                            .pointerInput(d) { detectTapGestures(onTap = { if (d == anchor) onOpenDay(d) else onPickDay(d) }, onDoubleTap = { onOpenDay(d) }) }
                            .then(if (actions == null || evs.isEmpty()) Modifier else Modifier.pointerInput(d, evs, wide) {
                                // ACT event.reschedule @android.calendar: the finger's cell, counted from this one, is the new day.
                                val chipTop = with(density) { (if (wide) 30.dp else MonthChips.TOP).toPx() }
                                val chipH = with(density) { (if (wide) 17.dp else MonthChips.STEP).toPx() }
                                fun dayAt(o: Offset): LocalDate {
                                    // Days run right to left in a right-to-left layout.
                                    val dx = kotlin.math.floor(o.x / size.width).toLong().let { if (rtl) -it else it }
                                    val dy = kotlin.math.floor(o.y / size.height).toLong()
                                    return d.plusDays(dy * 7 + dx)
                                }
                                detectDragGesturesAfterLongPress(
                                    onDragStart = { o ->
                                        val shown = if (wide) minOf(evs.size, 3)
                                            else MonthChips.shown(evs.size, MonthChips.slots(with(density) { size.height.toDp() })).coerceAtLeast(1)
                                        val pick = evs.getOrNull(((o.y - chipTop) / chipH).toInt().coerceIn(0, shown - 1))
                                        when {
                                            pick == null -> Unit
                                            actions.readOnly(pick.e) -> actions.notify(AppText.get(R.string.cal_read_only_calendar))
                                            else -> {
                                                haptic.performHapticFeedback(androidx.compose.ui.hapticfeedback.HapticFeedbackType.LongPress)
                                                drag = MonthDrag(pick, d, d)
                                            }
                                        }
                                    },
                                    onDrag = { ch, _ ->
                                        ch.consume()
                                        drag?.let { m ->
                                            val to = dayAt(ch.position)
                                            if (to != m.to) haptic.performHapticFeedback(androidx.compose.ui.hapticfeedback.HapticFeedbackType.TextHandleMove)
                                            drag = m.copy(to = to)
                                        }
                                    },
                                    onDragEnd = {
                                        val m = drag
                                        drag = null
                                        if (m != null && m.to != m.from) actions.moveDays(m.ev.e, java.time.temporal.ChronoUnit.DAYS.between(m.from, m.to))
                                    },
                                    onDragCancel = { drag = null },
                                )
                            })
                            .padding(2.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                    ) {
                        Box(Modifier.size(if (wide) 26.dp else 20.dp).clip(CircleShape).background(if (d == today) MaterialTheme.colorScheme.primary else Color.Transparent),
                            contentAlignment = Alignment.Center) {
                            Text(d.dayOfMonth.toString(), style = if (wide) MaterialTheme.typography.labelLarge else MaterialTheme.typography.labelMedium,
                                fontWeight = if (!wide && (d == anchor || d == today)) FontWeight.Bold else null,
                                color = when {
                                    d == today -> MaterialTheme.colorScheme.onPrimary
                                    inMonth -> MaterialTheme.colorScheme.onSurface
                                    else -> MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.6f)
                                })
                        }
                        if (target) drag?.let { m ->
                            Text(m.ev.e.summary.ifBlank { stringResource(R.string.cal_no_title) }, maxLines = 1, overflow = TextOverflow.Ellipsis,
                                style = MaterialTheme.typography.labelSmall, color = CalendarColors.onColor(m.ev.color),
                                modifier = Modifier.fillMaxWidth().padding(top = 1.dp).clip(RoundedCornerShape(3.dp)).background(m.ev.color)
                                    .padding(horizontal = 3.dp))
                        }
                        if (wide) {
                            evs.take(3).forEach { e ->
                                Text(e.e.summary.ifBlank { stringResource(R.string.cal_no_title) }, maxLines = 1, overflow = TextOverflow.Ellipsis,
                                    style = MaterialTheme.typography.labelSmall, color = CalendarColors.onColor(e.color),
                                    modifier = Modifier.fillMaxWidth().padding(top = 1.dp).clip(RoundedCornerShape(3.dp)).background(e.color)
                                        .padding(horizontal = 3.dp))
                            }
                            if (evs.size > 3) Text("+${evs.size - 3}", style = MaterialTheme.typography.labelSmall)
                        } else if (evs.isNotEmpty()) {
                            // Phones: titles in small chips, as many as the cell holds, then "+n".
                            BoxWithConstraints(Modifier.fillMaxWidth().weight(1f)) {
                                val shown = MonthChips.shown(evs.size, MonthChips.slots(maxHeight + MonthChips.TOP))
                                Column(Modifier.fillMaxWidth().padding(top = 2.dp), verticalArrangement = Arrangement.spacedBy(MonthChips.GAP)) {
                                    evs.take(shown).forEach { e -> MonthChip(e, inMonth) }
                                    if (evs.size > shown) Text(stringResource(R.string.cal_month_more, evs.size - shown), maxLines = 1,
                                        style = MonthChips.textStyle(), color = MaterialTheme.colorScheme.onSurfaceVariant,
                                        modifier = Modifier.padding(start = 3.dp))
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

/**
 * The phone month's chips: a business-calendar month on a phone. Each
 * day shows its events' titles in small chips, all-day ones filled with the calendar's colour, timed
 * ones tinted with a bar in it, as many as the cell holds; the rest is "+n". The cell's day number
 * takes [TOP], each chip [STEP].
 */
object MonthChips {
    val TOP = 22.dp
    val HEIGHT = 14.dp
    val GAP = 1.dp
    val STEP = HEIGHT + GAP

    /** How many chip rows a cell of [cell] height holds under its day number. */
    fun slots(cell: androidx.compose.ui.unit.Dp): Int = ((cell - TOP - 2.dp) / STEP).toInt().coerceAtLeast(0)

    /** Chips shown for [count] events in [slots] rows: all when they fit, else one row gives way to "+n". */
    fun shown(count: Int, slots: Int): Int = if (count <= slots) count else (slots - 1).coerceAtLeast(0)

    @Composable
    fun textStyle() = MaterialTheme.typography.labelSmall.copy(fontSize = 10.sp, lineHeight = 12.sp,
        platformStyle = androidx.compose.ui.text.PlatformTextStyle(includeFontPadding = false))
}

@Composable
private fun MonthChip(e: DayEvent, inMonth: Boolean) {
    val title = e.e.summary.ifBlank { stringResource(R.string.cal_no_title) }
    val filled = e.multiDay
    val shape = RoundedCornerShape(3.dp)
    Row(Modifier.fillMaxWidth().height(MonthChips.HEIGHT).alpha(if (inMonth) 1f else 0.55f).clip(shape)
        .background(if (filled) e.color else e.color.copy(alpha = 0.18f)), verticalAlignment = Alignment.CenterVertically) {
        if (!filled) Box(Modifier.width(2.dp).fillMaxHeight().background(e.color))
        Text(title, maxLines = 1, overflow = TextOverflow.Clip, softWrap = false, style = MonthChips.textStyle(),
            color = if (filled) CalendarColors.onColor(e.color) else MaterialTheme.colorScheme.onSurface,
            modifier = Modifier.padding(horizontal = 3.dp))
    }
}

/**
 * The list under the month grid: the picked day's events. [compact] (the phone month, note #14): one
 * dense row each, the times in a column, a colour bar and the title with its place, so a busy day
 * reads at a glance under the grid; otherwise the day's cards.
 */
@Composable
fun DayList(day: LocalDate, events: List<DayEvent>, selected: String?, onOpen: (CalendarEvent) -> Unit, onCreate: () -> Unit,
            modifier: Modifier = Modifier, compact: Boolean = false) {
    val evs = events.filter { it.covers(day) }.sortedWith(compareBy({ !it.e.allDay }, { it.start }))
    val nowTime = java.time.Instant.ofEpochMilli(AppClock.millis()).atZone(ZoneId.systemDefault()).toLocalDateTime()
    LazyColumn(modifier, contentPadding = PaddingValues(start = 12.dp, end = 12.dp, top = 8.dp, bottom = 96.dp),
        verticalArrangement = Arrangement.spacedBy(if (compact) 2.dp else 6.dp)) {
        item(key = "h") {
            Text(day.format(LocaleFormats.date("EEEEMMMMd", "EEEE, MMMM d")), style = MaterialTheme.typography.titleSmall,
                modifier = Modifier.padding(start = 4.dp, bottom = 4.dp).semantics { heading() })
        }
        if (evs.isEmpty()) item(key = "none") {
            Surface(onClick = onCreate, color = Color.Transparent, shape = MaterialTheme.shapes.small, modifier = Modifier.fillMaxWidth()) {
                Text(stringResource(R.string.cal_nothing_planned_tap), color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(12.dp))
            }
        }
        if (compact) items(evs, key = { it.e.key }) { d -> AgendaRow(d, day, selected == d.e.key, past = d.end < nowTime) { onOpen(d.e) } }
        else items(evs, key = { it.e.key }) { d -> EventCard(d, day, selected == d.e.key, past = false) { onOpen(d.e) } }
    }
}

/** One dense agenda row: start and end in a column, the calendar's colour bar, the title and its place. */
@Composable
private fun AgendaRow(d: DayEvent, day: LocalDate, selected: Boolean, past: Boolean, pending: Boolean = false, onClick: () -> Unit) {
    val declined = d.e.myStatus == "DECLINED"
    val invite = d.e.isInvite && (d.e.myStatus == "NEEDS-ACTION" || d.e.myStatus == "TENTATIVE")
    val timed = !d.multiDay && d.start.toLocalDate() == day
    Surface(onClick = onClick, shape = MaterialTheme.shapes.small,
        color = if (selected) MaterialTheme.colorScheme.secondaryContainer else Color.Transparent,
        modifier = Modifier.fillMaxWidth().heightIn(min = 44.dp).alpha(if (past) 0.6f else 1f).then(if (pending) Modifier.dashedBorder() else Modifier)) {
        Row(Modifier.height(androidx.compose.foundation.layout.IntrinsicSize.Min).padding(horizontal = 4.dp, vertical = 5.dp),
            verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.width(74.dp)) {
                if (timed) {
                    Text(d.start.toLocalTime().format(timeFmt), style = MaterialTheme.typography.labelLarge, maxLines = 1)
                    if (d.end.toLocalDate() == day) Text(d.end.toLocalTime().format(timeFmt), style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1)
                } else Text(timeRange(d, day), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 2, overflow = TextOverflow.Ellipsis)
            }
            Box(Modifier.width(4.dp).fillMaxHeight().clip(RoundedCornerShape(2.dp))
                .background(if (invite || declined) Color.Transparent else d.color)
                .then(if (invite || declined) Modifier.border(1.dp, d.color, RoundedCornerShape(2.dp)) else Modifier))
            Spacer(Modifier.width(10.dp))
            Column(Modifier.weight(1f)) {
                Text(d.e.summary.ifBlank { stringResource(R.string.cal_no_title) }, style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.Medium, maxLines = 1, overflow = TextOverflow.Ellipsis,
                    textDecoration = if (declined || d.e.cancelled) androidx.compose.ui.text.style.TextDecoration.LineThrough else null)
                val sub = listOfNotNull(d.e.location?.takeIf { it.isNotBlank() }, if (invite) stringResource(R.string.cal_invitation) else null)
                    .joinToString(" · ")
                if (sub.isNotEmpty()) Text(sub, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
    }
}

/** The dashed border of a change that has not reached the server yet (offline). */
@Composable
fun Modifier.dashedBorder(): Modifier {
    val c = MaterialTheme.colorScheme.onSurface
    return this.drawBehind {
        drawRoundRect(c, cornerRadius = androidx.compose.ui.geometry.CornerRadius(8.dp.toPx()),
            style = androidx.compose.ui.graphics.drawscope.Stroke(1.5.dp.toPx(),
                pathEffect = androidx.compose.ui.graphics.PathEffect.dashPathEffect(floatArrayOf(6.dp.toPx(), 4.dp.toPx()))))
    }
}

/**
 * A conference link: the meeting link the server and the web find ([JoinLink]), else the event's
 * URL when it is an https link (a video link made from a custom template on another host).
 */
fun joinLink(e: CalendarEvent): String? =
    io.github.murtazanasir.mailr.data.cal.JoinLink.find(e.url, e.location, e.description)
        ?: e.url?.trim()?.takeIf { io.github.murtazanasir.mailr.data.cal.JoinLink.hostOf(it) != null }

/** "Join" for a meeting provider's link, "Join on <host>" for any other link, so the host shows before the tap. */
fun joinLabel(link: String): String =
    if (io.github.murtazanasir.mailr.data.cal.JoinLink.isMeeting(link)) AppText.get(R.string.rem_action_join)
    else io.github.murtazanasir.mailr.data.cal.JoinLink.hostOf(link)?.let { AppText.get(R.string.cal_join_on, it) } ?: AppText.get(R.string.rem_action_join)
