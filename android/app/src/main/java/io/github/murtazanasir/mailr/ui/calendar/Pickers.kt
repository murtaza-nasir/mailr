package io.github.murtazanasir.mailr.ui.calendar

import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.LocaleFormats
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Flag
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.Mail
import androidx.compose.material.icons.outlined.NotificationsNone
import io.github.murtazanasir.mailr.look.AlertDialog
import io.github.murtazanasir.mailr.look.Button
import io.github.murtazanasir.mailr.look.DatePicker
import io.github.murtazanasir.mailr.look.DatePickerDialog
import io.github.murtazanasir.mailr.look.DropdownMenu
import io.github.murtazanasir.mailr.look.DropdownMenuItem
import io.github.murtazanasir.mailr.look.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import io.github.murtazanasir.mailr.look.InputChip
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.OutlinedTextField
import io.github.murtazanasir.mailr.look.RadioButton
import io.github.murtazanasir.mailr.look.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import io.github.murtazanasir.mailr.look.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import io.github.murtazanasir.mailr.look.TimePicker
import androidx.compose.material3.rememberDatePickerState
import androidx.compose.material3.rememberTimePickerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import io.github.murtazanasir.mailr.data.reminders.SpanUnit
import java.time.DayOfWeek
import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneOffset
import java.time.format.TextStyle
import java.util.Locale

/** Header of an editor sheet or pane: close, title, and the primary action. */
@Composable
fun EditorHeader(title: String, action: String, enabled: Boolean, onClose: () -> Unit,
                 actions: @Composable () -> Unit = {}, onAction: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(start = 4.dp, end = 16.dp, top = 4.dp, bottom = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        IconButton(onClick = onClose) { Icon(Icons.Outlined.Close, stringResource(R.string.cal_close)) }
        Text(title, style = MaterialTheme.typography.titleMedium, maxLines = 1, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f))
        // Before Save: the task editor's Undo and Redo.
        actions()
        Button(onClick = onAction, enabled = enabled) { Text(action) }
    }
}

/** A tappable form row with a leading icon (48 dp+ target). */
@Composable
fun FieldRow(icon: ImageVector?, modifier: Modifier = Modifier, onClick: (() -> Unit)? = null, onClickLabel: String? = null,
             content: @Composable () -> Unit) {
    Row(
        modifier.fillMaxWidth().heightIn(min = 52.dp)
            .then(if (onClick != null) Modifier.clickable(onClickLabel = onClickLabel, onClick = onClick) else Modifier)
            .padding(horizontal = 16.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.width(40.dp)) { if (icon != null) Icon(icon, null, tint = MaterialTheme.colorScheme.onSurfaceVariant) }
        Box(Modifier.weight(1f)) { content() }
    }
}

/** "Open the email": the conversation a task or event was made from (editors and event details). */
@Composable
fun SourceEmailRow(onOpen: () -> Unit) {
    FieldRow(Icons.Outlined.Mail, onClick = onOpen, onClickLabel = stringResource(R.string.cal_open_email)) {
        Text(stringResource(R.string.cal_open_the_email), color = MaterialTheme.colorScheme.primary)
    }
}

/** Chips that wrap (large fonts and narrow screens never clip them). */
@Composable
fun ChipFlow(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    FlowRow(modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(0.dp)) {
        content()
    }
}

@Composable
fun PresetChip(label: String, selected: Boolean, onClick: () -> Unit) {
    FilterChip(selected = selected, onClick = onClick, label = { Text(label) },
        leadingIcon = if (selected) ({ Icon(Icons.Filled.Check, null, Modifier.size(FilterChipDefaults.IconSize)) }) else null)
}

@Composable
fun DateDialog(initial: LocalDate, onDismiss: () -> Unit, onPick: (LocalDate) -> Unit) {
    val state = rememberDatePickerState(initialSelectedDateMillis = initial.atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli())
    DatePickerDialog(onDismissRequest = onDismiss,
        confirmButton = {
            TextButton(onClick = {
                state.selectedDateMillis?.let { onPick(Instant.ofEpochMilli(it).atZone(ZoneOffset.UTC).toLocalDate()) }
            }, enabled = state.selectedDateMillis != null) { Text(stringResource(R.string.cal_ok)) }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.cal_cancel)) } }) { DatePicker(state) }
}

@Composable
fun TimeDialog(initial: LocalTime, onDismiss: () -> Unit, onPick: (LocalTime) -> Unit) {
    val state = rememberTimePickerState(initial.hour, initial.minute)
    AlertDialog(onDismissRequest = onDismiss,
        text = { Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) { TimePicker(state) } },
        confirmButton = { TextButton(onClick = { onPick(LocalTime.of(state.hour, state.minute)) }) { Text(stringResource(R.string.cal_ok)) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.cal_cancel)) } })
}

/** Selected reminders as chips (tap × to remove) and an "Add reminder" menu. */
@Composable
fun ReminderPicker(selected: List<Int>, options: List<ReminderOption>, label: (Int) -> String, onChange: (List<Int>) -> Unit,
                   enabled: Boolean = true, disabledHint: String? = null) {
    var menu by remember { mutableStateOf(false) }
    var custom by remember { mutableStateOf(false) }
    FieldRow(Icons.Outlined.NotificationsNone) {
        Column {
            if (!enabled) {
                Text(disabledHint ?: stringResource(R.string.cal_no_reminders), color = MaterialTheme.colorScheme.onSurfaceVariant)
                return@Column
            }
            ChipFlow {
                selected.sorted().forEach { m ->
                    InputChip(selected = false, onClick = { onChange(selected - m) }, label = { Text(label(m)) },
                        trailingIcon = { Icon(Icons.Outlined.Close, stringResource(R.string.cal_remove_reminder_cd, label(m)), Modifier.size(18.dp)) })
                }
                Box {
                    FilterChip(selected = false, onClick = { menu = true },
                        label = { Text(if (selected.isEmpty()) stringResource(R.string.cal_add_reminder) else stringResource(R.string.cal_add)) },
                        leadingIcon = { Icon(Icons.Outlined.Add, null, Modifier.size(18.dp)) })
                    DropdownMenu(menu, { menu = false }) {
                        options.filter { it.minutes !in selected }.forEach { o ->
                            DropdownMenuItem({ Text(o.label) }, { menu = false; onChange((selected + o.minutes).distinct()) })
                        }
                        DropdownMenuItem({ Text(stringResource(R.string.cal_custom_more)) }, { menu = false; custom = true })
                    }
                }
            }
        }
    }
    if (custom) CustomReminderDialog(onDismiss = { custom = false }) { m -> custom = false; onChange((selected + m).distinct()) }
}

@Composable
private fun CustomReminderDialog(onDismiss: () -> Unit, onPick: (Int) -> Unit) {
    var amount by remember { mutableStateOf("30") }
    var unit by remember { mutableIntStateOf(0) }
    val units = listOf(SpanUnit.MINUTES.label to 1, SpanUnit.HOURS.label to 60, SpanUnit.DAYS.label to 1440, SpanUnit.WEEKS.label to 10080)
    val n = amount.toIntOrNull()
    val valid = n != null && n in 1..999 && n * units[unit].second <= 4 * 10080
    AlertDialog(onDismissRequest = onDismiss, title = { Text(stringResource(R.string.cal_custom_reminder)) },
        text = {
            Column {
                OutlinedTextField(amount, { amount = it.filter(Char::isDigit).take(3) }, singleLine = true,
                    label = { Text(stringResource(R.string.cal_how_long_before)) }, isError = !valid,
                    supportingText = { if (!valid) Text(stringResource(R.string.cal_up_to_4_weeks)) },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number), modifier = Modifier.fillMaxWidth())
                units.forEachIndexed { i, (name, _) ->
                    Row(Modifier.fillMaxWidth().selectable(unit == i, role = Role.RadioButton) { unit = i }.heightIn(min = 44.dp),
                        verticalAlignment = Alignment.CenterVertically) {
                        RadioButton(unit == i, null)
                        Spacer(Modifier.width(8.dp))
                        Text(name)
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = { onPick(n!! * units[unit].second) }, enabled = valid) { Text(stringResource(R.string.cal_add)) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.cal_cancel)) } })
}

/** "Does not repeat" / presets / Custom…, as a dialog list. */
@Composable
fun RecurrenceDialog(start: LocalDate, current: Recurrence?, onDismiss: () -> Unit, onPick: (Recurrence?) -> Unit) {
    var custom by remember { mutableStateOf(false) }
    if (custom) {
        CustomRecurrenceDialog(start, current?.takeIf { it.raw == null } ?: Recurrence(Freq.WEEKLY, byDay = setOf(start.dayOfWeek)),
            onDismiss = { custom = false }, onPick = onPick)
        return
    }
    val presets = Recurrence.presets(start)
    AlertDialog(onDismissRequest = onDismiss, title = { Text(stringResource(R.string.cal_repeat)) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                presets.forEach { (label, r) ->
                    val sel = r?.toRRule() == current?.toRRule()
                    Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).selectable(sel, role = Role.RadioButton) { onPick(r) },
                        verticalAlignment = Alignment.CenterVertically) {
                        RadioButton(sel, null)
                        Spacer(Modifier.width(12.dp))
                        Text(label)
                    }
                }
                val isCustom = current != null && presets.none { it.second?.toRRule() == current.toRRule() }
                Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).selectable(isCustom, role = Role.RadioButton) { custom = true },
                    verticalAlignment = Alignment.CenterVertically) {
                    RadioButton(isCustom, null)
                    Spacer(Modifier.width(12.dp))
                    Text(if (isCustom) stringResource(R.string.cal_custom_rule, current!!.describe()) else stringResource(R.string.cal_custom_more))
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.cal_close)) } })
}

@Composable
private fun CustomRecurrenceDialog(start: LocalDate, initial: Recurrence, onDismiss: () -> Unit, onPick: (Recurrence) -> Unit) {
    var freq by remember { mutableStateOf(initial.freq) }
    var interval by remember { mutableStateOf(initial.interval.toString()) }
    var days by remember { mutableStateOf(initial.byDay.ifEmpty { setOf(start.dayOfWeek) }) }
    var endKind by remember { mutableIntStateOf(when (initial.end) { is RecurrenceEnd.Until -> 1; is RecurrenceEnd.Count -> 2; else -> 0 }) }
    var until by remember { mutableStateOf((initial.end as? RecurrenceEnd.Until)?.date ?: start.plusMonths(3)) }
    var count by remember { mutableStateOf(((initial.end as? RecurrenceEnd.Count)?.n ?: 10).toString()) }
    var pickUntil by remember { mutableStateOf(false) }
    val n = interval.toIntOrNull()
    val c = count.toIntOrNull()
    val valid = n != null && n in 1..99 && (freq != Freq.WEEKLY || days.isNotEmpty()) &&
        (endKind != 2 || (c != null && c in 1..999)) && (endKind != 1 || !until.isBefore(start))
    val result = Recurrence(freq, n ?: 1, if (freq == Freq.WEEKLY) days else emptySet(), null,
        if (freq == Freq.MONTHLY) start.dayOfMonth else null,
        when (endKind) { 1 -> RecurrenceEnd.Until(until); 2 -> RecurrenceEnd.Count(c ?: 1); else -> RecurrenceEnd.Never })
    AlertDialog(onDismissRequest = onDismiss, title = { Text(stringResource(R.string.cal_rec_custom_repeat)) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(stringResource(R.string.cal_rec_every))
                    Spacer(Modifier.width(8.dp))
                    OutlinedTextField(interval, { interval = it.filter(Char::isDigit).take(2) }, singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number), modifier = Modifier.width(72.dp),
                        isError = n == null || n < 1)
                }
                Spacer(Modifier.size(8.dp))
                ChipFlow {
                    Freq.entries.forEach { f ->
                        val many = (n ?: 1) > 1
                        PresetChip(pluralStringResource(when (f) {
                            Freq.DAILY -> R.plurals.cal_rec_unit_day; Freq.WEEKLY -> R.plurals.cal_rec_unit_week
                            Freq.MONTHLY -> R.plurals.cal_rec_unit_month; Freq.YEARLY -> R.plurals.cal_rec_unit_year
                        }, if (many) n!! else 1), freq == f) { freq = f }
                    }
                }
                if (freq == Freq.WEEKLY) {
                    Text(stringResource(R.string.cal_rec_on), style = MaterialTheme.typography.labelLarge, modifier = Modifier.padding(top = 12.dp, bottom = 4.dp))
                    WeekdayToggles(days) { days = it }
                }
                Text(stringResource(R.string.cal_rec_ends), style = MaterialTheme.typography.labelLarge, modifier = Modifier.padding(top = 12.dp, bottom = 4.dp))
                listOf(stringResource(R.string.cal_rec_end_never), stringResource(R.string.cal_rec_end_on, until.format(LocaleFormats.date("yMMMd", "MMM d, yyyy"))), stringResource(R.string.cal_rec_end_after)).forEachIndexed { i, l ->
                    Row(Modifier.fillMaxWidth().heightIn(min = 44.dp).selectable(endKind == i, role = Role.RadioButton) {
                        endKind = i
                        if (i == 1) pickUntil = true
                    }, verticalAlignment = Alignment.CenterVertically) {
                        RadioButton(endKind == i, null)
                        Spacer(Modifier.width(8.dp))
                        Text(l)
                        if (i == 2) {
                            Spacer(Modifier.width(8.dp))
                            OutlinedTextField(count, { count = it.filter(Char::isDigit).take(3); endKind = 2 }, singleLine = true,
                                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number), modifier = Modifier.width(80.dp))
                            Spacer(Modifier.width(8.dp))
                            Text(stringResource(R.string.cal_rec_end_times))
                        }
                    }
                }
                Text(result.describe(), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 8.dp))
            }
        },
        confirmButton = { TextButton(onClick = { onPick(result) }, enabled = valid) { Text(stringResource(R.string.cal_done)) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.cal_cancel)) } })
    if (pickUntil) DateDialog(until, { pickUntil = false }) { until = it; pickUntil = false }
}

@Composable
fun WeekdayToggles(days: Set<DayOfWeek>, onChange: (Set<DayOfWeek>) -> Unit) {
    val first = java.time.temporal.WeekFields.of(Locale.getDefault()).firstDayOfWeek
    Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        (0L until 7L).map { first.plus(it) }.forEach { d ->
            val on = d in days
            val name = d.getDisplayName(TextStyle.FULL, Locale.getDefault())
            Box(
                Modifier.size(40.dp).background(if (on) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surfaceContainerHighest, CircleShape)
                    .selectable(on, role = Role.Checkbox) { onChange(if (on) days - d else days + d) }
                    .semantics { contentDescription = name },
                contentAlignment = Alignment.Center,
            ) {
                Text(d.getDisplayName(TextStyle.NARROW, Locale.getDefault()),
                    color = if (on) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurface,
                    style = MaterialTheme.typography.labelLarge)
            }
        }
    }
}

/** Which calendar / task list. Coloured dots; the server default is marked. */
@Composable
fun CollectionMenu(collections: List<CalendarCollection>, selected: String?, expanded: Boolean, onDismiss: () -> Unit, onPick: (String) -> Unit) {
    DropdownMenu(expanded, onDismiss) {
        collections.forEach { c ->
            DropdownMenuItem(
                text = { Text(c.name) },
                leadingIcon = { Box(Modifier.size(14.dp).background(CalendarColors.of(c.url, c.color), CircleShape)) },
                trailingIcon = if (c.url == selected) ({ Icon(Icons.Filled.Check, stringResource(R.string.cal_selected)) }) else null,
                onClick = { onPick(c.url) },
            )
        }
    }
}

@Composable
fun ColorDot(color: Color, size: Int = 12) {
    Box(Modifier.size(size.dp).background(color, CircleShape))
}

@Composable
fun PriorityPicker(level: Int, onChange: (Int) -> Unit) {
    SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
        CalendarColors.priorityNames.forEachIndexed { i, name ->
            SegmentedButton(selected = level == i, onClick = { onChange(i) },
                shape = SegmentedButtonDefaults.itemShape(i, CalendarColors.priorityNames.size),
                icon = {
                    if (i > 0) Icon(Icons.Filled.Flag, null, Modifier.size(16.dp), tint = CalendarColors.priority(i))
                    else SegmentedButtonDefaults.Icon(level == i)
                }) { Text(name, maxLines = 1) }
        }
    }
}
