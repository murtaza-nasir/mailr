package io.github.murtazanasir.mailr.ui.calendar

import io.github.murtazanasir.mailr.time.AppClock
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.calculateZoom
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.detectDragGesturesAfterLongPress
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.scrollBy
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.CheckBoxOutlineBlank
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.ContentCopy
import androidx.compose.material.icons.outlined.DeleteOutline
import io.github.murtazanasir.mailr.look.AlertDialog
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import io.github.murtazanasir.mailr.look.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.clipRect
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.positionInWindow
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.CustomAccessibilityAction
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.semantics.onClick
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.TaskItem
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.time.Duration
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.time.format.TextStyle
import java.time.temporal.ChronoUnit
import java.util.Locale
import kotlin.math.abs
import kotlin.math.roundToInt

val HOUR_HEIGHT = 56.dp

/** Snap for drags and the ghost, in minutes (15 by default). */
const val SNAP_MINUTES = 15

/**
 * What the grid can do besides opening events: quick create from the ghost or a long-press
 * range, and direct manipulation (move, resize, duplicate, delete) with its TalkBack forms. Every
 * call goes through [CalendarViewModel], which asks the series question and whether to send
 * updates.
 */
class GridActions(
    val createRange: (LocalDateTime, LocalDateTime) -> Unit,
    val move: (CalendarEvent, LocalDateTime, LocalDateTime) -> Unit,
    val duplicate: (CalendarEvent, LocalDateTime, LocalDateTime) -> Unit,
    val delete: (CalendarEvent) -> Unit,
    val reschedule: (CalendarEvent) -> Unit,
    val readOnly: (CalendarEvent) -> Boolean,
    val notify: (String) -> Unit,
    val completeTask: (TaskItem) -> Unit = {},
    /** A drop into the all-day strip: the event becomes all-day on that day (or moves there). */
    val toAllDay: (CalendarEvent, LocalDate) -> Unit = { _, _ -> },
    /** An all-day event dropped into the grid: 1 hour at the drop time. */
    val toTimed: (CalendarEvent, LocalDateTime) -> Unit = { _, _ -> },
    /** ACT task.reschedule @android.calendar: a task chip dropped at a time (or, null, on a day of the strip). */
    val rescheduleTask: (TaskItem, LocalDateTime?, LocalDate) -> Unit = { _, _, _ -> },
    /** Month view: an event dropped on another day, [Long] days later (negative: earlier). */
    val moveDays: (CalendarEvent, Long) -> Unit = { _, _ -> },
)

/** A timed event is drawn in the grid; all-day events and anything 24 hours or longer go to the strip. */
val DayEvent.inStrip: Boolean get() = e.allDay || Duration.between(start, end).toMinutes() >= 24 * 60

private fun snap(m: Float): Int = ((m / SNAP_MINUTES).roundToInt() * SNAP_MINUTES).coerceIn(0, 1440)

/** The state of a lifted event (long-press): where it would go now. */
private data class Lift(
    val ev: DayEvent, val start: LocalDateTime, val end: LocalDateTime,
    /** Minutes between the finger and the event's start, so it does not jump under the finger. */
    val grab: Long = 0, val mode: Int = MOVE, val dragging: Boolean = false,
    /** The finger is over the all-day strip: a drop makes the event all-day on day [stripDay]. */
    val toStrip: Boolean = false, val stripDay: Int = 0,
) {
    val moved: Boolean get() = start != ev.start || end != ev.end
    companion object { const val MOVE = 0; const val TOP = 1; const val BOTTOM = 2 }
}

/** A tap on an empty slot shows this ghost; a second tap opens quick create. A long-press drag stretches it. */
private data class Ghost(val day: Int, val start: Int, val end: Int, val stretching: Boolean = false)

/** A task chip lifted in the grid: its new time, or (over the strip) a day without a time. */
private data class TaskLift(val chip: TaskChip, val at: LocalDateTime, val grab: Int, val toStrip: Boolean = false, val stripDay: Int = 0)

/**
 * An all-day event or a date-only task lifted in the strip; [pos] is the finger in window
 * coordinates. Over the grid it drops at a time, over the strip on a day.
 */
private data class StripDrag(val item: Any, val pos: Offset, val fromDay: Int)

private class Hit(val ev: DayEvent?, val chip: OverflowChip<DayEvent>?, val task: TaskChip?)

@Composable
fun TimeGrid(
    days: List<LocalDate>, events: List<DayEvent>, now: Long, selected: String?,
    onOpen: (CalendarEvent) -> Unit, onCreateAt: (LocalDateTime) -> Unit, onPickDay: ((LocalDate) -> Unit)?,
    zone: ZoneId = ZoneId.systemDefault(),
    hourHeight: Dp = HOUR_HEIGHT,
    onZoom: ((Dp) -> Unit)? = null,
    tasks: List<TaskChip> = emptyList(),
    pending: Set<String> = emptySet(),
    actions: GridActions? = null,
    skeleton: Boolean = false,
    modifier: Modifier = Modifier,
) {
    val today = AppClock.today(zone)
    val nowT = LocalDateTime.ofInstant(Instant.ofEpochMilli(now), zone)
    val scroll = rememberScrollState()
    val density = LocalDensity.current
    val haptic = LocalHapticFeedback.current
    // Pointer positions are measured from the left; the day columns start on the right in a
    // right-to-left layout.
    val rtl = LocalLayoutDirection.current == LayoutDirection.Rtl
    val scope = rememberCoroutineScope()
    var zoomH by remember(hourHeight) { mutableFloatStateOf(hourHeight.value) }
    val hh = zoomH.dp
    var lift by remember(days) { mutableStateOf<Lift?>(null) }
    var ghost by remember(days) { mutableStateOf<Ghost?>(null) }
    var overflow by remember { mutableStateOf<List<DayEvent>?>(null) }
    var stripOpen by remember { mutableStateOf(false) }
    var taskLift by remember(days) { mutableStateOf<TaskLift?>(null) }
    var stripDrag by remember(days) { mutableStateOf<StripDrag?>(null) }
    // Where the strip and the hour grid are on screen, for drags between them.
    var stripPos by remember { mutableStateOf(Offset.Zero) }
    var stripW by remember { mutableFloatStateOf(1f) }
    var gridPos by remember { mutableStateOf(Offset.Zero) }
    var gridW by remember { mutableFloatStateOf(1f) }
    var viewportTop by remember { mutableFloatStateOf(0f) }
    val pxPerMinOuter = with(density) { hh.toPx() } / 60f
    /** Where a strip drag would land: (day, minute) in the grid, or (day, null) on the strip. */
    fun stripTarget(p: Offset): Pair<Int, Int?> = if (p.y >= viewportTop) {
        val gx = p.x - gridPos.x
        val d = ((if (rtl) gridW - gx else gx) / (gridW / days.size)).toInt().coerceIn(0, days.lastIndex)
        d to snap((p.y - gridPos.y) / pxPerMinOuter).coerceIn(0, 1440 - 60)
    } else ((p.x - stripPos.x).let { if (rtl) stripW - it else it } / (stripW / days.size)).toInt().coerceIn(0, days.lastIndex) to null
    val stripHighlight = stripDrag?.let { sd -> stripTarget(sd.pos).takeIf { it.second == null }?.first }
        ?: lift?.takeIf { it.toStrip }?.stripDay ?: taskLift?.takeIf { it.toStrip }?.stripDay
    val tf = remember { DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).withLocale(Locale.getDefault()) }

    LaunchedEffect(days.first()) {
        val hour = if (today in days) (nowT.hour - 1).coerceAtLeast(0) else 8
        scroll.scrollTo(with(density) { (hh * hour).roundToPx() })
    }

    // The overlap layout per day column, on segments split at midnight ("↳" on the continuation).
    val timed = remember(events) { events.filterNot { it.inStrip } }
    val segments = remember(timed) { EventLayout.splitDays(timed, { it.start }, { it.end }) }
    val layouts = remember(segments, days) {
        days.map { d ->
            val segs = segments.filter { it.date == d }
            val byItem = segs.associateBy { it.item.e.key }
            EventLayout.layoutDay(segs.map { it.item }, { it.e.key }, { byItem[it.e.key]!!.start }, { byItem[it.e.key]!!.end },
                partstat = { it.e.myStatus?.takeIf { _ -> it.e.isInvite } }, continued = { byItem[it.e.key]!!.continued })
        }
    }
    val timedTasks = remember(tasks, days) { tasks.filter { !it.dateOnly && it.at.toLocalDate() in days } }

    // Pinch to zoom the hour height (Initial pass, so the scroll never sees a two-finger gesture).
    val pinch = if (onZoom == null) Modifier else Modifier.pointerInput(Unit) {
        awaitEachGesture {
            awaitFirstDown(requireUnconsumed = false)
            var zoomed = false
            do {
                val ev = awaitPointerEvent(PointerEventPass.Initial)
                if (ev.changes.count { it.pressed } >= 2) {
                    val z = ev.calculateZoom()
                    if (z != 1f) {
                        val old = zoomH
                        zoomH = (zoomH * z).coerceIn(CalendarPrefs.MIN_HOUR, CalendarPrefs.MAX_HOUR)
                        val ratio = zoomH / old
                        scope.launch { scroll.scrollTo((scroll.value * ratio).roundToInt()) }
                        zoomed = true
                        ev.changes.forEach { it.consume() }
                    }
                }
            } while (ev.changes.any { it.pressed })
            if (zoomed) onZoom(zoomH.dp)
        }
    }

    Column(modifier.fillMaxSize().then(pinch)) {
        if (days.size > 1) Row(Modifier.fillMaxWidth().padding(start = 48.dp)) {
            days.forEach { d ->
                Column(Modifier.weight(1f).clip(MaterialTheme.shapes.small).clickable(enabled = onPickDay != null,
                    onClickLabel = stringResource(R.string.cal_open_day)) { onPickDay?.invoke(d) }.padding(vertical = 4.dp),
                    horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(d.dayOfWeek.getDisplayName(TextStyle.SHORT, Locale.getDefault()), style = MaterialTheme.typography.labelSmall,
                        color = if (d == today) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
                    Box(Modifier.size(30.dp).clip(CircleShape).background(if (d == today) MaterialTheme.colorScheme.primary else Color.Transparent),
                        contentAlignment = Alignment.Center) {
                        Text(d.dayOfMonth.toString(), style = MaterialTheme.typography.titleSmall,
                            color = if (d == today) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurface)
                    }
                }
            }
        }
        AllDayStrip(days, events.filter { it.inStrip }, tasks.filter { it.dateOnly }, stripOpen, { stripOpen = !stripOpen }, pending, onOpen,
            actions?.completeTask, highlight = stripHighlight, dragging = stripDrag?.item,
            onPlaced = { p, w -> stripPos = p; stripW = w },
            drag = actions?.let { act ->
                StripDragHandlers(
                    start = { item, p, day ->
                        val ro = (item as? DayEvent)?.let { act.readOnly(it.e) } == true
                        if (ro) act.notify(AppText.get(R.string.cal_read_only_calendar)) else {
                            haptic.performHapticFeedback(HapticFeedbackType.LongPress)
                            stripDrag = StripDrag(item, p, day)
                        }
                    },
                    move = { p -> stripDrag = stripDrag?.let { old ->
                        val n = old.copy(pos = p)
                        if (stripTarget(old.pos) != stripTarget(p)) haptic.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                        n
                    } },
                    end = {
                        val sd = stripDrag
                        stripDrag = null
                        if (sd != null) {
                            val (d, m) = stripTarget(sd.pos)
                            val at = m?.let { days[d].atStartOfDay().plusMinutes(it.toLong()) }
                            when (val item = sd.item) {
                                is DayEvent -> when {
                                    at != null -> act.toTimed(item.e, at)
                                    d != sd.fromDay -> act.toAllDay(item.e, item.start.toLocalDate().plusDays((d - sd.fromDay).toLong()))
                                }
                                is TaskChip -> when {
                                    at != null -> act.rescheduleTask(item.task, at, days[d])
                                    d != sd.fromDay -> act.rescheduleTask(item.task, null, days[d])
                                }
                            }
                        }
                    },
                    cancel = { stripDrag = null },
                )
            })
        HorizontalDivider()
        BoxWithConstraints(Modifier.fillMaxWidth().weight(1f).onGloballyPositioned { viewportTop = it.positionInWindow().y }) {
            val viewportPx = with(density) { maxHeight.toPx() }
            var pointerViewportY by remember { mutableFloatStateOf(-1f) }
            var pointerX by remember { mutableFloatStateOf(0f) }
            Row(Modifier.fillMaxSize().verticalScroll(scroll, enabled = lift == null)) {
                Column(Modifier.width(48.dp)) {
                    (0 until 24).forEach { h ->
                        Box(Modifier.height(hh).fillMaxWidth()) {
                            if (h > 0) Text(QuickDates.hourLabel(h), style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.align(Alignment.TopEnd).offset(y = (-7).dp).padding(end = 6.dp))
                        }
                    }
                }
                val lineColor = MaterialTheme.colorScheme.outlineVariant
                val shade = MaterialTheme.colorScheme.surfaceContainerLow
                BoxWithConstraints(Modifier.weight(1f).height(hh * 24).onGloballyPositioned { gridPos = it.positionInWindow(); gridW = it.size.width.toFloat() }) {
                    val colW = maxWidth / days.size
                    val colPx = with(density) { colW.toPx() }
                    val pxPerMin = with(density) { hh.toPx() } / 60f
                    val currentLift by rememberUpdatedState(lift)

                    /** x from the grid's start edge (its right edge in a right-to-left layout). */
                    fun fromStart(x: Float) = if (rtl) colPx * days.size - x else x
                    fun dayAt(x: Float) = (fromStart(x) / colPx).toInt().coerceIn(0, days.lastIndex)
                    fun minuteAt(y: Float) = y / pxPerMin
                    fun hit(x: Float, y: Float): Hit? {
                        val i = dayAt(x)
                        val fx = (fromStart(x) - i * colPx) / colPx
                        val m = minuteAt(y)
                        timedTasks.firstOrNull { t ->
                            t.at.toLocalDate() == days[i] && m >= t.at.hour * 60 + t.at.minute && m < t.at.hour * 60 + t.at.minute + 24
                        }?.let { return Hit(null, null, it) }
                        val l = layouts[i]
                        l.chips.firstOrNull { c -> fx >= c.col.toFloat() / c.cols && m >= c.top && m < c.bottom }?.let { return Hit(null, it, null) }
                        l.placed.lastOrNull { b -> fx >= b.col.toFloat() / b.cols && fx < (b.col + b.span).toFloat() / b.cols && m >= b.top && m < b.bottom }
                            ?.let { return Hit(it.item, null, null) }
                        return null
                    }
                    fun at(day: Int, minute: Int): LocalDateTime = days[day].atStartOfDay().plusMinutes(minute.toLong())
                    fun tick(old: Any?, new: Any?) { if (old != new) haptic.performHapticFeedback(HapticFeedbackType.TextHandleMove) }

                    /** Where the lift goes for a finger at (x, y). */
                    fun dragTo(l: Lift, x: Float, y: Float): Lift {
                        val i = dayAt(x)
                        val m = minuteAt(y)
                        return when (l.mode) {
                            Lift.MOVE -> {
                                val len = Duration.between(l.ev.start, l.ev.end)
                                val s = at(i, snap(m - l.grab).coerceIn(0, 1440 - SNAP_MINUTES))
                                l.copy(start = s, end = s.plus(len))
                            }
                            Lift.TOP -> {
                                val d = l.start.toLocalDate()
                                val s = d.atStartOfDay().plusMinutes(snap(m).toLong())
                                l.copy(start = minOf(s, l.end.minusMinutes(SNAP_MINUTES.toLong())))
                            }
                            else -> {
                                val d = l.end.minusMinutes(1).toLocalDate().let { if (it in days) it else days[i] }
                                val e = d.atStartOfDay().plusMinutes(snap(m).toLong())
                                l.copy(end = maxOf(e, l.start.plusMinutes(SNAP_MINUTES.toLong())))
                            }
                        }
                    }

                    /** Over the all-day strip (above the visible grid): a move there becomes all-day. */
                    fun overStrip(y: Float) = y - scroll.value < 0f
                    fun liftTo(l: Lift, x: Float, y: Float): Lift =
                        if (l.mode == Lift.MOVE && overStrip(y)) l.copy(toStrip = true, stripDay = dayAt(x)) else dragTo(l, x, y).copy(toStrip = false)
                    fun taskTo(t: TaskLift, x: Float, y: Float): TaskLift =
                        if (overStrip(y)) t.copy(toStrip = true, stripDay = dayAt(x))
                        else t.copy(toStrip = false, at = at(dayAt(x), snap(minuteAt(y) - t.grab).coerceIn(0, 1440 - SNAP_MINUTES)))

                    // Autoscroll while dragging near the top or bottom edge.
                    val draggingNow = lift?.dragging == true || ghost?.stretching == true || taskLift != null
                    LaunchedEffect(draggingNow) {
                        val edge = with(density) { 48.dp.toPx() }
                        while (draggingNow) {
                            val vy = pointerViewportY
                            val dy = when {
                                vy < 0f -> 0f
                                vy < edge -> -(edge - vy) / 3f
                                vy > viewportPx - edge -> (vy - (viewportPx - edge)) / 3f
                                else -> 0f
                            }
                            if (dy != 0f) {
                                scroll.scrollBy(dy)
                                val y = vy + scroll.value
                                lift?.takeIf { it.dragging }?.let { l -> val n = liftTo(l, pointerX, y); tick(l.start to l.end, n.start to n.end); lift = n }
                                taskLift?.let { t -> taskLift = taskTo(t, pointerX, y) }
                                ghost?.takeIf { it.stretching }?.let { g -> ghost = g.copy(end = snap(minuteAt(y))) }
                            }
                            delay(16)
                        }
                    }

                    val gestures = Modifier
                        .pointerInput(days, layouts, timedTasks, actions) {
                            detectTapGestures { o ->
                                val l = currentLift
                                if (l != null) {
                                    // A tap away from the lifted event puts it back.
                                    lift = null
                                    return@detectTapGestures
                                }
                                val h = hit(o.x, o.y)
                                when {
                                    h?.task != null -> actions?.completeTask?.invoke(h.task.task)
                                    h?.chip != null -> overflow = h.chip.items
                                    h?.ev != null -> { ghost = null; onOpen(h.ev.e) }
                                    actions == null -> {
                                        val m = (minuteAt(o.y).toInt() / 30) * 30
                                        onCreateAt(at(dayAt(o.x), m.coerceIn(0, 1410)))
                                    }
                                    else -> {
                                        val i = dayAt(o.x)
                                        val m = minuteAt(o.y)
                                        val g = ghost
                                        if (g != null && g.day == i && m >= g.start && m < g.end) {
                                            ghost = null
                                            actions.createRange(at(i, g.start), at(i, g.end))
                                        } else {
                                            val s = ((m.toInt() / 30) * 30).coerceIn(0, 1380)
                                            ghost = Ghost(i, s, s + 60)
                                        }
                                    }
                                }
                            }
                        }
                        .pointerInput(days, layouts, actions) {
                            if (actions == null) return@pointerInput
                            detectDragGesturesAfterLongPress(
                                onDragStart = { o ->
                                    pointerX = o.x
                                    pointerViewportY = o.y - scroll.value
                                    val h = hit(o.x, o.y)
                                    val ev = h?.ev
                                    when {
                                        h?.task != null -> {
                                            // ACT task.reschedule @android.calendar: a task chip is dragged to a new time.
                                            haptic.performHapticFeedback(HapticFeedbackType.LongPress)
                                            ghost = null
                                            val t = h.task
                                            taskLift = TaskLift(t, t.at, (minuteAt(o.y) - (t.at.hour * 60 + t.at.minute)).toInt())
                                        }
                                        ev != null && actions.readOnly(ev.e) -> actions.notify(AppText.get(R.string.cal_read_only_calendar))
                                        ev != null -> {
                                            haptic.performHapticFeedback(HapticFeedbackType.LongPress)
                                            ghost = null
                                            val grab = (minuteAt(o.y) - ChronoUnit.MINUTES.between(days[dayAt(o.x)].atStartOfDay(), ev.start)).toLong()
                                            lift = Lift(ev, ev.start, ev.end, grab = grab, dragging = true)
                                        }
                                        h == null -> {
                                            haptic.performHapticFeedback(HapticFeedbackType.LongPress)
                                            val s = snap(minuteAt(o.y) - SNAP_MINUTES / 2f)
                                            ghost = Ghost(dayAt(o.x), s, (s + SNAP_MINUTES).coerceAtMost(1440), stretching = true)
                                        }
                                    }
                                },
                                onDrag = { change, _ ->
                                    change.consume()
                                    val p = change.position
                                    pointerX = p.x
                                    pointerViewportY = p.y - scroll.value
                                    lift?.takeIf { it.dragging }?.let { l ->
                                        val n = liftTo(l, p.x, p.y)
                                        tick(Triple(l.start, l.end, l.toStrip), Triple(n.start, n.end, n.toStrip))
                                        lift = n
                                    }
                                    taskLift?.let { t ->
                                        val n = taskTo(t, p.x, p.y)
                                        tick(t.at to t.toStrip, n.at to n.toStrip)
                                        taskLift = n
                                    }
                                    ghost?.takeIf { it.stretching }?.let { g ->
                                        val e = snap(minuteAt(p.y))
                                        tick(g.end, e)
                                        ghost = g.copy(end = e)
                                    }
                                },
                                onDragEnd = {
                                    pointerViewportY = -1f
                                    lift?.let { l ->
                                        // A drop commits; a lift without movement stays up with the toolbar and handles.
                                        when {
                                            l.toStrip -> { lift = null; actions.toAllDay(l.ev.e, days[l.stripDay]) }
                                            l.moved -> { lift = null; actions.move(l.ev.e, l.start, l.end) }
                                            else -> lift = l.copy(dragging = false)
                                        }
                                    }
                                    taskLift?.let { t ->
                                        taskLift = null
                                        when {
                                            t.toStrip -> actions.rescheduleTask(t.chip.task, null, days[t.stripDay])
                                            t.at != t.chip.at -> actions.rescheduleTask(t.chip.task, t.at, t.at.toLocalDate())
                                        }
                                    }
                                    ghost?.takeIf { it.stretching }?.let { g ->
                                        ghost = null
                                        val a = minOf(g.start, g.end)
                                        val b = maxOf(g.start, g.end).let { if (it - a < SNAP_MINUTES) a + 60 else it }.coerceAtMost(1440)
                                        actions.createRange(at(g.day, a), at(g.day, b))
                                    }
                                },
                                onDragCancel = {
                                    pointerViewportY = -1f
                                    taskLift = null
                                    lift = lift?.let { it.copy(start = it.ev.start, end = it.ev.end, dragging = false, toStrip = false) }
                                    if (ghost?.stretching == true) ghost = null
                                },
                            )
                        }
                        .pointerInput(lift != null && lift?.dragging == false, days) {
                            val l0 = lift ?: return@pointerInput
                            if (l0.dragging || actions == null) return@pointerInput
                            // A lifted event: drag the body to move it or a handle to resize it.
                            detectDragGestures(
                                onDragStart = { o ->
                                    val l = lift ?: return@detectDragGestures
                                    val i = days.indexOf(l.start.toLocalDate()).coerceAtLeast(0)
                                    val top = ChronoUnit.MINUTES.between(days[i].atStartOfDay(), l.start) * pxPerMin
                                    val bottom = ChronoUnit.MINUTES.between(days[i].atStartOfDay(), l.end) * pxPerMin
                                    val handle = with(density) { 20.dp.toPx() }
                                    val inCol = dayAt(o.x) == i
                                    pointerX = o.x
                                    pointerViewportY = o.y - scroll.value
                                    lift = when {
                                        inCol && abs(o.y - top) < handle -> l.copy(mode = Lift.TOP, dragging = true)
                                        inCol && abs(o.y - bottom) < handle -> l.copy(mode = Lift.BOTTOM, dragging = true)
                                        inCol && o.y in top..bottom -> l.copy(mode = Lift.MOVE, dragging = true,
                                            grab = (minuteAt(o.y) - ChronoUnit.MINUTES.between(days[i].atStartOfDay(), l.start)).toLong())
                                        else -> l
                                    }
                                },
                                onDrag = { change, _ ->
                                    val l = lift ?: return@detectDragGestures
                                    if (!l.dragging) return@detectDragGestures
                                    change.consume()
                                    pointerX = change.position.x
                                    pointerViewportY = change.position.y - scroll.value
                                    val n = liftTo(l, change.position.x, change.position.y)
                                    tick(Triple(l.start, l.end, l.toStrip), Triple(n.start, n.end, n.toStrip))
                                    lift = n
                                },
                                onDragEnd = {
                                    pointerViewportY = -1f
                                    val l = lift ?: return@detectDragGestures
                                    when {
                                        l.dragging && l.toStrip -> { lift = null; actions.toAllDay(l.ev.e, days[l.stripDay]) }
                                        l.dragging && l.moved -> { lift = null; actions.move(l.ev.e, l.start, l.end) }
                                        else -> lift = l.copy(dragging = false)
                                    }
                                },
                                onDragCancel = { pointerViewportY = -1f; lift = lift?.copy(dragging = false) },
                            )
                        }

                    Canvas(Modifier.fillMaxSize()) {
                        val hPx = hh.toPx()
                        // Working hours (9–17) clear; the rest lightly shaded.
                        drawRect(shade, Offset.Zero, Size(size.width, hPx * 9))
                        drawRect(shade, Offset(0f, hPx * 17), Size(size.width, hPx * 7))
                        for (h in 0..23) {
                            drawLine(lineColor, Offset(0f, hPx * h), Offset(size.width, hPx * h), 1f)
                            if (hPx >= 48.dp.toPx()) drawLine(lineColor.copy(alpha = 0.4f), Offset(0f, hPx * h + hPx / 2), Offset(size.width, hPx * h + hPx / 2), 1f)
                        }
                        for (i in 0 until days.size) drawLine(lineColor, Offset(colPx * i, 0f), Offset(colPx * i, size.height), 1f)
                    }
                    Box(Modifier.fillMaxSize().testTag("calendar-grid").then(gestures)) {
                        if (skeleton) days.indices.forEach { i ->
                            listOf(9 to 60, 11 to 90, 14 to 45).forEachIndexed { k, (h, len) ->
                                Box(Modifier.offset(x = colW * i + 2.dp, y = hh * (h + (i + k) % 2)).width(colW - 6.dp).height(hh * (len / 60f))
                                    .clip(RoundedCornerShape(6.dp)).background(MaterialTheme.colorScheme.surfaceContainerHighest))
                            }
                        }
                        days.forEachIndexed { i, d ->
                            val l = layouts[i]
                            l.placed.forEach { b ->
                                val dimmed = lift?.ev?.e?.key == b.item.e.key
                                GridBlock(b, colW * i + colW * b.col / b.cols, colW * b.span / b.cols, hh, selected == b.item.e.key,
                                    past = b.item.end < nowT, pending = b.item.e.key in pending, dimmed = dimmed, tf = tf, onOpen = onOpen,
                                    actions = actions)
                            }
                            l.chips.forEach { c ->
                                val moreCd = pluralStringResource(R.plurals.cal_grid_more_events_cd, c.items.size, c.items.size)
                                val showThem = stringResource(R.string.cal_grid_show_them)
                                Box(Modifier.offset(x = colW * i + colW * c.col / c.cols, y = hh * (c.top / 60f)).width(colW / c.cols)
                                    .height(maxOf(hh * ((c.bottom - c.top) / 60f), 24.dp)).padding(1.dp)
                                    .clip(RoundedCornerShape(6.dp)).background(MaterialTheme.colorScheme.secondaryContainer)
                                    .semantics { contentDescription = moreCd; onClick(showThem) { overflow = c.items; true } },
                                    contentAlignment = Alignment.TopCenter) {
                                    Text("+${c.items.size}", style = MaterialTheme.typography.labelMedium,
                                        color = MaterialTheme.colorScheme.onSecondaryContainer, modifier = Modifier.padding(top = 2.dp))
                                }
                            }
                            timedTasks.filter { it.at.toLocalDate() == d }.forEach { t ->
                                TaskGridChip(t, colW * i, colW, hh, actions?.completeTask, dimmed = taskLift?.chip == t)
                            }
                            if (d == today) {
                                val y = hh * (nowT.hour + nowT.minute / 60f)
                                val c = MaterialTheme.colorScheme.error
                                val nowCd = stringResource(R.string.cal_now)
                                Box(Modifier.offset(x = colW * i, y = y - 1.dp).width(colW).height(2.dp).background(c))
                                Box(Modifier.offset(x = colW * i - 4.dp, y = y - 5.dp).size(10.dp).background(c, CircleShape)
                                    .semantics { contentDescription = nowCd })
                            }
                        }
                        ghost?.let { g ->
                            val s = minOf(g.start, g.end)
                            val e = maxOf(g.start, g.end).let { if (it == s) s + SNAP_MINUTES else it }
                            val c = MaterialTheme.colorScheme.primary
                            val ghostCd = stringResource(R.string.cal_grid_new_event_cd, at(g.day, s).toLocalTime().format(tf))
                            Box(Modifier.offset(x = colW * g.day, y = hh * (s / 60f)).width(colW).height(hh * ((e - s) / 60f)).padding(1.dp)
                                .clip(RoundedCornerShape(6.dp)).background(c.copy(alpha = 0.18f)).border(2.dp, c, RoundedCornerShape(6.dp))
                                .semantics { contentDescription = ghostCd }
                                .padding(horizontal = 6.dp, vertical = 2.dp)) {
                                Text("${at(g.day, s).toLocalTime().format(tf)} – ${at(g.day, e % 1440).toLocalTime().format(tf)}",
                                    style = MaterialTheme.typography.labelMedium, color = c, maxLines = 1)
                            }
                        }
                        taskLift?.takeIf { !it.toStrip }?.let { t ->
                            val i = days.indexOf(t.at.toLocalDate())
                            if (i >= 0) TaskGridChip(t.chip.copy(at = t.at), colW * i, colW, hh, null, lifted = true)
                        }
                        // An all-day event or task from the strip, over the grid: where it would land.
                        stripDrag?.let { sd -> stripTarget(sd.pos).takeIf { it.second != null } }?.let { (d, m) ->
                            val c = MaterialTheme.colorScheme.primary
                            val len = if (stripDrag?.item is TaskChip) 24 else 60
                            Box(Modifier.offset(x = colW * d, y = hh * (m!! / 60f)).width(colW).height(hh * (len / 60f)).padding(1.dp)
                                .clip(RoundedCornerShape(6.dp)).background(c.copy(alpha = 0.18f)).border(2.dp, c, RoundedCornerShape(6.dp))
                                .padding(horizontal = 6.dp, vertical = 2.dp)) {
                                Text(at(d, m).toLocalTime().format(tf), style = MaterialTheme.typography.labelMedium, color = c, maxLines = 1)
                            }
                        }
                        lift?.takeIf { !it.toStrip }?.let { l ->
                            val i = days.indexOf(l.start.toLocalDate())
                            if (i >= 0) {
                                val top = ChronoUnit.MINUTES.between(days[i].atStartOfDay(), l.start)
                                val len = ChronoUnit.MINUTES.between(l.start, l.end).coerceAtLeast(EventLayout.MIN_MINUTES.toLong())
                                LiftedBlock(l.ev, colW * i, colW, hh * (top / 60f), hh * (len / 60f),
                                    "${l.start.toLocalTime().format(tf)} – ${l.end.toLocalTime().format(tf)}", showHandles = !l.dragging || l.mode != Lift.MOVE)
                            }
                        }
                    }
                }
            }
            lift?.takeIf { !it.dragging }?.let { l ->
                FloatingToolbar(l.ev.e, Modifier.align(Alignment.BottomCenter).padding(16.dp),
                    onDuplicate = { lift = null; actions?.duplicate(l.ev.e, l.start, l.end) },
                    onDelete = { lift = null; actions?.delete(l.ev.e) },
                    onCancel = { lift = null })
            }
        }
    }
    overflow?.let { list ->
        AlertDialog(onDismissRequest = { overflow = null }, title = { Text(pluralStringResource(R.plurals.cal_grid_more, list.size, list.size)) },
            text = {
                Column {
                    list.sortedBy { it.start }.forEach { d ->
                        ListItem(headlineContent = { Text(d.e.summary.ifBlank { stringResource(R.string.cal_no_title) }) },
                            supportingContent = { Text("${d.start.toLocalTime().format(tf)} – ${d.end.toLocalTime().format(tf)}") },
                            leadingContent = { ColorDot(d.color) },
                            modifier = Modifier.clickable { overflow = null; onOpen(d.e) })
                    }
                }
            },
            confirmButton = { TextButton(onClick = { overflow = null }) { Text(stringResource(R.string.cal_close)) } })
    }
}

/**
 * One event block: fill, outline (declined, needs reply) or hatched (tentative); a dashed
 * border while a change waits for the server; "↳" on a continuation. TalkBack gets the drag as
 * custom actions.
 */
@Composable
private fun GridBlock(
    b: PlacedBlock<DayEvent>, x: Dp, width: Dp, hh: Dp, selected: Boolean, past: Boolean, pending: Boolean, dimmed: Boolean,
    tf: DateTimeFormatter, onOpen: (CalendarEvent) -> Unit, actions: GridActions?,
) {
    val d = b.item
    val top = hh * (b.top / 60f)
    val height = hh * ((b.bottom - b.top) / 60f)
    val declined = d.e.myStatus == "DECLINED"
    val fill = b.style == BlockStyle.FILL
    val surface = MaterialTheme.colorScheme.surface
    val fg = when (b.style) {
        BlockStyle.FILL -> CalendarColors.onColor(d.color)
        else -> MaterialTheme.colorScheme.onSurface
    }
    val outline = MaterialTheme.colorScheme.onSurface
    val onPhone = io.github.murtazanasir.mailr.data.phonecal.PhoneEvents.isPhone(d.e)
    // TalkBack: "Standup, 9:00 AM to 9:15 AM, tentative, not synced yet".
    val label = listOfNotNull(
        d.e.summary.ifBlank { stringResource(R.string.cal_no_title) },
        stringResource(R.string.cal_grid_time_to, d.start.toLocalTime().format(tf), d.end.toLocalTime().format(tf)),
        if (b.continued) stringResource(R.string.cal_grid_continued) else null,
        if (b.style == BlockStyle.HATCHED) stringResource(R.string.cal_grid_tentative_lower) else null,
        if (b.style == BlockStyle.OUTLINE) (if (declined) stringResource(R.string.cal_grid_declined_lower) else stringResource(R.string.cal_grid_needs_reply_lower)) else null,
        if (pending) stringResource(R.string.cal_grid_not_synced_lower) else null,
        if (onPhone) stringResource(R.string.phonecal_on_phone) else null,
    ).joinToString(", ")
    val moveEarlier = stringResource(R.string.cal_grid_move_earlier)
    val moveLater = stringResource(R.string.cal_grid_move_later)
    val nextDay = stringResource(R.string.cal_grid_next_day)
    val previousDay = stringResource(R.string.cal_grid_previous_day)
    val rescheduleMore = stringResource(R.string.cal_grid_reschedule_more)
    val openEvent = stringResource(R.string.cal_open_event)
    val stateText = if (b.style == BlockStyle.HATCHED) stringResource(R.string.cal_grid_tentative) else if (declined) stringResource(R.string.cal_grid_declined) else stringResource(R.string.cal_grid_needs_reply)
    val a11y = actions?.takeUnless { it.readOnly(d.e) }?.let { act ->
        listOf(
            CustomAccessibilityAction(moveEarlier) { act.move(d.e, d.start.minusMinutes(15), d.end.minusMinutes(15)); true },
            CustomAccessibilityAction(moveLater) { act.move(d.e, d.start.plusMinutes(15), d.end.plusMinutes(15)); true },
            CustomAccessibilityAction(nextDay) { act.move(d.e, d.start.plusDays(1), d.end.plusDays(1)); true },
            CustomAccessibilityAction(previousDay) { act.move(d.e, d.start.minusDays(1), d.end.minusDays(1)); true },
            CustomAccessibilityAction(rescheduleMore) { act.reschedule(d.e); true },
        )
    }.orEmpty()
    Box(
        Modifier.offset(x = x, y = top).width(width).height(height).padding(start = 1.dp, end = 2.dp, bottom = 1.dp)
            .alpha(when { dimmed -> 0.3f; past || d.e.cancelled -> 0.6f; else -> 1f })
            .clip(RoundedCornerShape(6.dp))
            .drawBehind {
                val r = CornerRadius(6.dp.toPx())
                when (b.style) {
                    BlockStyle.FILL -> drawRoundRect(d.color, cornerRadius = r)
                    BlockStyle.OUTLINE -> {
                        drawRoundRect(surface, cornerRadius = r)
                        drawRoundRect(d.color, cornerRadius = r, style = Stroke(1.5.dp.toPx()))
                    }
                    BlockStyle.HATCHED -> {
                        drawRoundRect(surface, cornerRadius = r)
                        drawRoundRect(d.color.copy(alpha = 0.28f), cornerRadius = r)
                        val step = 8.dp.toPx()
                        clipRect {
                            var s = -size.height
                            while (s < size.width) {
                                drawLine(d.color.copy(alpha = 0.55f), Offset(s, size.height), Offset(s + size.height, 0f), 1.5.dp.toPx())
                                s += step
                            }
                        }
                        drawRoundRect(d.color, cornerRadius = r, style = Stroke(1.dp.toPx()))
                    }
                }
                if (pending) drawRoundRect(outline, cornerRadius = r,
                    style = Stroke(1.5.dp.toPx(), pathEffect = PathEffect.dashPathEffect(floatArrayOf(6.dp.toPx(), 4.dp.toPx()))))
                if (selected) drawRoundRect(outline, cornerRadius = r, style = Stroke(2.dp.toPx()))
            }
            .semantics {
                contentDescription = label
                onClick(openEvent) { onOpen(d.e); true }
                if (a11y.isNotEmpty()) customActions = a11y
                if (!fill) stateDescription = stateText
            }
            .padding(horizontal = 4.dp, vertical = 2.dp),
    ) {
        Column {
            // The block's size is the grid's, in dp: above 130 % text the title stops growing, so a
            // week column at a large text size still shows the start of the title, not only "…".
            val scale = LocalDensity.current.fontScale
            val base = MaterialTheme.typography.labelMedium
            val style = if (scale <= 1.3f) base else base.copy(fontSize = base.fontSize * (1.3f / scale), lineHeight = base.lineHeight * (1.3f / scale))
            Text((if (b.continued) "↳ " else "") + d.e.summary.ifBlank { stringResource(R.string.cal_no_title) }, style = style,
                // The web's event blocks (calendar.css .cal-ev): bold titles.
                fontWeight = if (io.github.murtazanasir.mailr.look.LocalLook.current.web) FontWeight.Bold else FontWeight.SemiBold, color = fg, maxLines = if (b.compact || width < 64.dp) 1 else if (height > 40.dp) 2 else 1,
                softWrap = width >= 64.dp,
                // A narrow block shows the title's first letters, cut at the edge: "…" alone said nothing.
                overflow = if (width < 64.dp) TextOverflow.Clip else TextOverflow.Ellipsis,
                textDecoration = if (declined || d.e.cancelled) TextDecoration.LineThrough else null)
            if (!b.compact && height > 44.dp && width >= 64.dp) {
                val sub = listOfNotNull(d.start.toLocalTime().format(tf), d.e.location?.takeIf { it.isNotBlank() }).joinToString(" · ")
                Text(sub, style = MaterialTheme.typography.labelSmall, color = fg.copy(alpha = 0.85f), maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            // "On this phone": the phone mark, where the block has room for a line of its own.
            if (onPhone && !b.compact && height > 30.dp && width >= 28.dp) OnPhoneMark(fg.copy(alpha = 0.85f), size = 12, described = false)
        }
    }
}

@Composable
private fun LiftedBlock(d: DayEvent, x: Dp, width: Dp, y: Dp, height: Dp, time: String, showHandles: Boolean) {
    val on = CalendarColors.onColor(d.color)
    val movingCd = stringResource(R.string.cal_grid_moving_to_cd, d.e.summary, time)
    val dragStart = stringResource(R.string.cal_grid_drag_start)
    val dragEnd = stringResource(R.string.cal_grid_drag_end)
    Box(Modifier.offset(x = x, y = y).width(width).height(height).padding(horizontal = 2.dp)) {
        Box(Modifier.fillMaxSize().shadow(8.dp, RoundedCornerShape(6.dp)).clip(RoundedCornerShape(6.dp)).background(d.color)
            .semantics { contentDescription = movingCd }.padding(horizontal = 4.dp, vertical = 2.dp)) {
            Column {
                Text(d.e.summary.ifBlank { stringResource(R.string.cal_no_title) }, style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.SemiBold,
                    color = on, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(time, style = MaterialTheme.typography.labelSmall, color = on, maxLines = 1)
            }
        }
        if (showHandles) {
            val c = MaterialTheme.colorScheme.surface
            Box(Modifier.align(Alignment.TopCenter).offset(y = (-6).dp).size(12.dp).background(c, CircleShape).border(2.dp, d.color, CircleShape)
                .semantics { contentDescription = dragStart })
            Box(Modifier.align(Alignment.BottomCenter).offset(y = 6.dp).size(12.dp).background(c, CircleShape).border(2.dp, d.color, CircleShape)
                .semantics { contentDescription = dragEnd })
        }
    }
}

/** Dragging on Android: the toolbar over a lifted event. */
@Composable
private fun FloatingToolbar(e: CalendarEvent, modifier: Modifier, onDuplicate: () -> Unit, onDelete: () -> Unit, onCancel: () -> Unit) {
    Surface(modifier, shape = RoundedCornerShape(28.dp), color = MaterialTheme.colorScheme.inverseSurface, shadowElevation = 6.dp) {
        Row(Modifier.padding(horizontal = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            val c = MaterialTheme.colorScheme.inverseOnSurface
            // ACT event.duplicate @android.calendar
            TextButton(onClick = onDuplicate, modifier = Modifier.heightIn(min = 48.dp)) {
                Icon(Icons.Outlined.ContentCopy, null, tint = c); Spacer(Modifier.width(6.dp)); Text(stringResource(R.string.cal_duplicate), color = c)
            }
            // ACT event.delete @android.calendar
            TextButton(onClick = onDelete, modifier = Modifier.heightIn(min = 48.dp)) {
                Icon(Icons.Outlined.DeleteOutline, null, tint = c); Spacer(Modifier.width(6.dp)); Text(stringResource(R.string.cal_delete), color = c)
            }
            TextButton(onClick = onCancel, modifier = Modifier.heightIn(min = 48.dp)) {
                Icon(Icons.Outlined.Close, null, tint = c); Spacer(Modifier.width(6.dp)); Text(stringResource(R.string.cal_cancel), color = c)
            }
        }
    }
}

/** A task with a due time, as a checkbox chip at its time. */
@Composable
private fun TaskGridChip(t: TaskChip, x: Dp, width: Dp, hh: Dp, complete: ((TaskItem) -> Unit)?, dimmed: Boolean = false, lifted: Boolean = false) {
    val y = hh * ((t.at.hour * 60 + t.at.minute) / 60f)
    val taskCd = stringResource(R.string.cal_grid_task_cd, t.task.summary, QuickDates.timeLabel(t.at.toLocalTime()))
    val completeLabel = stringResource(R.string.cal_complete)
    Row(Modifier.offset(x = x, y = y).width(width).height(maxOf(hh * (24 / 60f), 22.dp)).padding(horizontal = 2.dp, vertical = 1.dp)
        .alpha(if (dimmed) 0.3f else 1f)
        .then(if (lifted) Modifier.shadow(6.dp, RoundedCornerShape(11.dp)) else Modifier)
        .clip(RoundedCornerShape(11.dp)).background(MaterialTheme.colorScheme.tertiaryContainer)
        .semantics { contentDescription = taskCd; onClick(completeLabel) { complete?.invoke(t.task); true } }
        .padding(horizontal = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        Icon(Icons.Outlined.CheckBoxOutlineBlank, null, Modifier.size(14.dp), tint = MaterialTheme.colorScheme.onTertiaryContainer)
        Spacer(Modifier.width(3.dp))
        Text(t.task.summary, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onTertiaryContainer,
            maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

/** Long-press drags of strip items (all-day events, date-only tasks), in window coordinates. */
internal class StripDragHandlers(
    val start: (Any, Offset, Int) -> Unit,
    val move: (Offset) -> Unit,
    val end: () -> Unit,
    val cancel: () -> Unit,
)

/**
 * All-day events (and date-only tasks) above the grid; past three rows it collapses into "+n
 * more". A long-press drags an item to another day or down into the grid; [highlight] marks
 * the day a drop from the grid would make all-day.
 */
@Composable
private fun AllDayStrip(days: List<LocalDate>, strip: List<DayEvent>, tasks: List<TaskChip>, open: Boolean, toggle: () -> Unit,
                        pending: Set<String>, onOpen: (CalendarEvent) -> Unit, complete: ((TaskItem) -> Unit)?,
                        highlight: Int? = null, dragging: Any? = null, onPlaced: (Offset, Float) -> Unit = { _, _ -> },
                        drag: StripDragHandlers? = null) {
    val perDay = days.map { d -> strip.filter { it.covers(d) } to tasks.filter { it.at.toLocalDate() == d } }
    if (perDay.all { it.first.isEmpty() && it.second.isEmpty() } && highlight == null) return
    val max = if (open) Int.MAX_VALUE else 3
    Row(Modifier.fillMaxWidth().padding(start = 48.dp, top = 2.dp, bottom = 4.dp)
        .onGloballyPositioned { onPlaced(it.positionInWindow(), it.size.width.toFloat()) }) {
        perDay.forEachIndexed { dayIdx, (evs, ts) ->
            Column(Modifier.weight(1f).padding(horizontal = 1.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                if (highlight == dayIdx) {
                    val c = MaterialTheme.colorScheme.primary
                    Text(stringResource(R.string.cal_all_day), style = MaterialTheme.typography.labelMedium, color = c, maxLines = 1,
                        modifier = Modifier.fillMaxWidth().heightIn(min = 22.dp).clip(RoundedCornerShape(4.dp)).background(c.copy(alpha = 0.18f))
                            .border(2.dp, c, RoundedCornerShape(4.dp)).padding(horizontal = 4.dp, vertical = 3.dp))
                }
                val items = evs.map { it as Any } + ts
                items.take(max).forEach { item ->
                    var placed by remember { mutableStateOf(Offset.Zero) }
                    val dragMod = Modifier.onGloballyPositioned { placed = it.positionInWindow() }
                        .alpha(if (dragging == item) 0.4f else 1f)
                        .then(if (drag == null) Modifier else Modifier.pointerInput(item, dayIdx) {
                            detectDragGesturesAfterLongPress(
                                onDragStart = { o -> drag.start(item, placed + o, dayIdx) },
                                onDrag = { ch, _ -> ch.consume(); drag.move(placed + ch.position) },
                                onDragEnd = drag.end,
                                onDragCancel = drag.cancel,
                            )
                        })
                    if (item is DayEvent) {
                        val dashed = item.e.key in pending
                        val outline = MaterialTheme.colorScheme.onSurface
                        val title = item.e.summary.ifBlank { stringResource(R.string.cal_no_title) }
                        // A phone calendar's event: "On this phone" after the title (TalkBack reads it too).
                        val shown = if (io.github.murtazanasir.mailr.data.phonecal.PhoneEvents.isPhone(item.e)) "$title · ${stringResource(R.string.phonecal_on_phone)}" else title
                        Text(shown, maxLines = 1, overflow = TextOverflow.Ellipsis,
                            style = MaterialTheme.typography.labelMedium, color = CalendarColors.onColor(item.color),
                            modifier = dragMod.fillMaxWidth().heightIn(min = 22.dp).clip(RoundedCornerShape(4.dp)).background(item.color)
                                .drawBehind {
                                    if (dashed) drawRoundRect(outline, cornerRadius = CornerRadius(4.dp.toPx()),
                                        style = Stroke(1.5.dp.toPx(), pathEffect = PathEffect.dashPathEffect(floatArrayOf(6f, 4f))))
                                }
                                .clickable(onClickLabel = stringResource(R.string.cal_open_event)) { onOpen(item.e) }.padding(horizontal = 4.dp, vertical = 3.dp))
                    } else if (item is TaskChip) {
                        Row(dragMod.fillMaxWidth().heightIn(min = 22.dp).clip(RoundedCornerShape(11.dp))
                            .background(MaterialTheme.colorScheme.tertiaryContainer)
                            .clickable(onClickLabel = stringResource(R.string.cal_complete)) { complete?.invoke(item.task) }.padding(horizontal = 4.dp),
                            verticalAlignment = Alignment.CenterVertically) {
                            Icon(Icons.Outlined.CheckBoxOutlineBlank, null, Modifier.size(14.dp), tint = MaterialTheme.colorScheme.onTertiaryContainer)
                            Spacer(Modifier.width(3.dp))
                            Text(item.task.summary, style = MaterialTheme.typography.labelSmall, maxLines = 1, overflow = TextOverflow.Ellipsis,
                                color = MaterialTheme.colorScheme.onTertiaryContainer)
                        }
                    }
                }
                if (items.size > 3) Text(if (open) stringResource(R.string.cal_grid_less) else pluralStringResource(R.plurals.rem_more, items.size - 3, items.size - 3), style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.clip(RoundedCornerShape(4.dp)).clickable(onClickLabel = if (open) stringResource(R.string.cal_grid_show_less) else stringResource(R.string.cal_grid_show_all)) { toggle() }
                        .heightIn(min = 24.dp).padding(horizontal = 4.dp, vertical = 2.dp))
            }
        }
    }
}
