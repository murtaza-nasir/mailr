package io.github.murtazanasir.mailr.ui.calendar

import io.github.murtazanasir.mailr.time.AppClock
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.i18n.LocaleFormats
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.snapping.rememberSnapFlingBehavior
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import io.github.murtazanasir.mailr.look.Button
import io.github.murtazanasir.mailr.look.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import io.github.murtazanasir.mailr.look.Switch
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import io.github.murtazanasir.mailr.data.reminders.Choice
import io.github.murtazanasir.mailr.data.reminders.QuickChoices
import io.github.murtazanasir.mailr.data.reminders.SpanUnit
import kotlinx.coroutines.flow.distinctUntilChanged
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.util.Locale

/** The height of one wheel row. */
internal val WHEEL_ROW: Dp = 40.dp
/** The compact wheels' row: three show, 108 dp in all. */
internal val COMPACT_WHEEL_ROW: Dp = 36.dp
private val ROW: Dp = WHEEL_ROW
private const val VISIBLE = 5

/**
 * A finger-scrolled wheel (durations and when values): a snapping list with the
 * centre row highlighted and a haptic tick per step. [selected] is the centred index.
 */
@Composable
fun WheelPicker(items: List<String>, selected: Int, onSelect: (Int) -> Unit, modifier: Modifier = Modifier,
                label: String = "", enabled: (Int) -> Boolean = { true }, visible: Int = VISIBLE, band: Boolean = true, row: Dp = ROW) {
    val state = rememberLazyListState(initialFirstVisibleItemIndex = selected.coerceIn(0, (items.size - 1).coerceAtLeast(0)))
    val haptics = LocalHapticFeedback.current
    val rowPx = with(LocalDensity.current) { row.toPx() }
    val centre by remember {
        derivedStateOf { state.firstVisibleItemIndex + if (state.firstVisibleItemScrollOffset > rowPx / 2) 1 else 0 }
    }
    LaunchedEffect(selected, items.size) {
        if (!state.isScrollInProgress && centre != selected) state.scrollToItem(selected.coerceIn(0, (items.size - 1).coerceAtLeast(0)))
    }
    // The collector outlives recompositions, so it reads the current items, selection and
    // callback. With the first ones it kept the amount wheel calling back with the unit it had at
    // first composition: scrolling "12 hours" down switched the unit back to minutes.
    val currentItems by rememberUpdatedState(items)
    val currentSelected by rememberUpdatedState(selected)
    val currentOnSelect by rememberUpdatedState(onSelect)
    LaunchedEffect(state) {
        snapshotFlow { centre }.distinctUntilChanged().collect { i ->
            if (i in currentItems.indices && i != currentSelected) {
                haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                currentOnSelect(i)
            }
        }
    }
    Box(modifier.height(row * visible).semantics {
        contentDescription = label
        stateDescription = items.getOrElse(selected) { "" }
    }) {
        if (band) Box(Modifier.fillMaxWidth().height(row).align(Alignment.Center)
            .background(MaterialTheme.colorScheme.secondaryContainer, MaterialTheme.shapes.small))
        LazyColumn(state = state, flingBehavior = rememberSnapFlingBehavior(state),
            contentPadding = PaddingValues(vertical = row * (visible / 2))) {
            itemsIndexed(items) { i, text ->
                val d = kotlin.math.abs(i - centre)
                Box(Modifier.fillMaxWidth().height(row).alpha(if (!enabled(i)) 0.3f else when (d) { 0 -> 1f; 1 -> 0.6f; else -> 0.3f }),
                    contentAlignment = Alignment.Center) {
                    Text(text, textAlign = TextAlign.Center,
                        style = if (d == 0) MaterialTheme.typography.titleMedium else MaterialTheme.typography.bodyLarge,
                        fontWeight = if (d == 0) FontWeight.SemiBold else FontWeight.Normal,
                        color = if (d == 0) MaterialTheme.colorScheme.onSecondaryContainer else MaterialTheme.colorScheme.onSurface)
                }
            }
        }
    }
}

private val timeFmt get() = DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).withLocale(Locale.getDefault())
private val dayFmt get() = LocaleFormats.date("EEEdMMM", "EEE d MMM")

/** "Thu 2 Oct, 9:05 AM (in 2 days)": the live result line under the wheels. */
fun describeMoment(at: ZonedDateTime, now: ZonedDateTime, dateOnly: Boolean = false, relative: Boolean = true): String {
    val today = now.toLocalDate()
    val day = when (at.toLocalDate()) {
        today -> AppText.get(R.string.rem_day_today)
        today.plusDays(1) -> AppText.get(R.string.rem_day_tomorrow)
        else -> at.format(dayFmt)
    }
    if (dateOnly) return day
    val moment = AppText.get(R.string.rem_day_time, day, at.format(timeFmt))
    val mins = java.time.Duration.between(now, at).toMinutes()
    val rel = when {
        !relative || mins < 0 -> return moment
        mins < 60 -> AppText.plural(R.plurals.rem_span_minutes, mins.toInt(), mins)
        mins < 24 * 60 -> if (mins % 60 == 0L) AppText.plural(R.plurals.rem_span_hours, (mins / 60).toInt(), mins / 60)
            else AppText.get(R.string.rem_span_hours_minutes, mins / 60, mins % 60)
        else -> null
    }
    val inText = rel?.let { AppText.get(R.string.rem_rel_in, it) } ?: AppText.plural(R.plurals.rem_rel_in_days, (mins / (24 * 60)).toInt(), mins / (24 * 60))
    return AppText.get(R.string.cal_moment_relative, moment, inText)
}

/** Preset chips: each shows its label and the moment it means. One tap picks. */
@Composable
fun ChoiceChips(choices: List<Choice>, now: ZonedDateTime, onPick: (Choice) -> Unit) {
    ChipFlow(Modifier.padding(horizontal = 16.dp)) {
        choices.forEach { c ->
            FilterChip(selected = false, onClick = { onPick(c) }, label = {
                Column(Modifier.padding(vertical = 4.dp)) {
                    Text(c.label, style = MaterialTheme.typography.labelLarge)
                    Text(describeMoment(c.at, now, relative = false), style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            })
        }
    }
}

/**
 * How long: preset chips, then amount + unit wheels with the resulting moment. [confirm] is
 * the button label ("Snooze").
 */
@Composable
fun DurationPicker(presets: List<Choice>, now: ZonedDateTime, confirm: String, onPick: (ZonedDateTime) -> Unit,
                   before: ZonedDateTime? = null) {
    var unit by remember { mutableStateOf(SpanUnit.MINUTES) }
    var amount by remember { mutableIntStateOf(15) }
    val units = SpanUnit.entries
    val result = if (before != null) {
        before.minus(java.time.Duration.between(now, unit.add(now, amount)))
    } else unit.add(now, amount)
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        if (presets.isNotEmpty()) ChoiceChips(presets, now) { onPick(it.at) }
        Row(Modifier.fillMaxWidth().padding(horizontal = 24.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            key(unit) {
                WheelPicker(unit.amounts.map { it.toString() }, unit.amounts.indexOf(amount).coerceAtLeast(0),
                    { amount = unit.amounts[it] }, Modifier.weight(1f), label = stringResource(R.string.cal_wheel_amount))
            }
            WheelPicker(units.map { it.label }, units.indexOf(unit), { i ->
                unit = units[i]
                amount = unit.nearest(amount)
            }, Modifier.weight(1.4f), label = stringResource(R.string.cal_wheel_unit))
        }
        Text(if (before != null) stringResource(R.string.cal_moment_remind, describeMoment(result, now)) else stringResource(R.string.cal_moment_arrow, describeMoment(result, now)),
            style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.primary,
            modifier = Modifier.padding(horizontal = 24.dp))
        Button(onClick = { onPick(result) }, enabled = result.isAfter(now),
            modifier = Modifier.fillMaxWidth().padding(horizontal = 24.dp)) { Text(confirm) }
    }
}

/**
 * The when picker: preset chips, then day / hour / minute wheels. [allowNoTime] (tasks) adds the
 * "No time" switch; the result is (moment, dateOnly).
 */
@Composable
fun WhenPicker(initial: ZonedDateTime, now: ZonedDateTime, presets: List<Choice>, confirm: String,
               allowNoTime: Boolean, initiallyDateOnly: Boolean, onPick: (ZonedDateTime, Boolean) -> Unit) {
    val days = remember(now.toLocalDate()) { (0L..60L).map { now.toLocalDate().plusDays(it) } }
    var day by remember { mutableStateOf(initial.toLocalDate().let { d -> if (d.isBefore(days.first())) days.first() else d }) }
    var hour by remember { mutableIntStateOf(initial.hour) }
    var minute by remember { mutableIntStateOf(initial.minute / 5 * 5) }
    var dateOnly by remember { mutableStateOf(initiallyDateOnly) }
    val result = day.atTime(LocalTime.of(hour, minute)).atZone(now.zone)
    val today = now.toLocalDate()
    val todayWord = stringResource(R.string.rem_day_today)
    val tomorrowWord = stringResource(R.string.rem_day_tomorrow)
    val dayLabels = days.map { d: LocalDate -> when (d) { today -> todayWord; today.plusDays(1) -> tomorrowWord; else -> d.format(dayFmt) } }
    val hourFmt = if (is24h()) LocaleFormats.date("HH", "HH") else LocaleFormats.date("ha", "h a")
    val hours = (0..23).map { h -> LocalTime.of(h, 0).format(hourFmt) }
    val minutes = (0..55 step 5).map { "%02d".format(it) }
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        if (presets.isNotEmpty()) ChoiceChips(presets, now) { onPick(it.at, dateOnly && it.id !in setOf("later", "tonight")) }
        Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            WheelPicker(dayLabels, days.indexOf(day).coerceAtLeast(0), { day = days[it] }, Modifier.weight(1.6f), label = stringResource(R.string.cal_wheel_day))
            Box(Modifier.weight(1f).alpha(if (dateOnly) 0.35f else 1f)) {
                WheelPicker(hours, hour, { hour = it }, label = stringResource(R.string.cal_wheel_hour),
                    enabled = { h -> !(day == today && h < now.hour) })
            }
            Box(Modifier.weight(0.8f).alpha(if (dateOnly) 0.35f else 1f)) {
                WheelPicker(minutes, minute / 5, { minute = it * 5 }, label = stringResource(R.string.cal_wheel_minute))
            }
        }
        if (allowNoTime) Row(Modifier.fillMaxWidth().padding(horizontal = 24.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(stringResource(R.string.cal_no_time), Modifier.weight(1f))
            Switch(checked = dateOnly, onCheckedChange = { dateOnly = it })
        }
        Text(describeMoment(result, now, dateOnly), style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.primary, modifier = Modifier.padding(horizontal = 24.dp))
        Button(onClick = { onPick(result, dateOnly) }, modifier = Modifier.fillMaxWidth().padding(horizontal = 24.dp)) { Text(confirm) }
    }
}

/** Reminder offsets from a notification: only the offsets still ahead, one tap each; Custom… opens the wheels. */
@Composable
fun OffsetPicker(start: ZonedDateTime, now: ZonedDateTime, onPick: (Int) -> Unit) {
    var custom by remember { mutableStateOf(false) }
    val offsets = QuickChoices.offsets(now, start)
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        ChipFlow(Modifier.padding(horizontal = 16.dp)) {
            offsets.forEach { m ->
                FilterChip(selected = false, onClick = { onPick(m) }, label = {
                    Column(Modifier.padding(vertical = 4.dp)) {
                        Text(QuickChoices.offsetLabel(m), style = MaterialTheme.typography.labelLarge)
                        Text(start.minusMinutes(m.toLong()).format(timeFmt), style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                })
            }
            FilterChip(selected = custom, onClick = { custom = !custom }, label = { Text(stringResource(R.string.cal_custom_more)) })
        }
        if (offsets.isEmpty()) Text(stringResource(R.string.cal_offset_too_soon), Modifier.padding(horizontal = 24.dp),
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        if (custom) DurationPicker(emptyList(), now, stringResource(R.string.cal_remind_me), before = start, onPick = { at ->
            onPick(java.time.Duration.between(at, start).toMinutes().toInt().coerceAtLeast(0))
        })
    }
}

/**
 * The when picker in the editors, in place of the date and time dialogs. [onOtherDate]
 * opens the month grid for dates beyond the day wheel's 60 days.
 */
@Composable
fun WhenDialog(title: String, initial: ZonedDateTime, presets: List<Choice>, allowNoTime: Boolean, initiallyDateOnly: Boolean,
               onDismiss: () -> Unit, onOtherDate: (() -> Unit)?, onPick: (ZonedDateTime, Boolean) -> Unit) {
    val now = remember { AppClock.now(initial.zone) }
    Dialog(onDismissRequest = onDismiss) {
        Surface(shape = MaterialTheme.shapes.extraLarge, color = MaterialTheme.colorScheme.surfaceContainerHigh) {
            Column(Modifier.padding(vertical = 16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(title, style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(horizontal = 24.dp))
                WhenPicker(initial, now, presets, stringResource(R.string.cal_done), allowNoTime, initiallyDateOnly, onPick)
                Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp), horizontalArrangement = Arrangement.End) {
                    if (onOtherDate != null) TextButton(onClick = onOtherDate) { Text(stringResource(R.string.cal_other_date)) }
                    TextButton(onClick = onDismiss) { Text(stringResource(R.string.cal_cancel)) }
                }
            }
        }
    }
}

@Composable
private fun is24h(): Boolean = android.text.format.DateFormat.is24HourFormat(androidx.compose.ui.platform.LocalContext.current)
