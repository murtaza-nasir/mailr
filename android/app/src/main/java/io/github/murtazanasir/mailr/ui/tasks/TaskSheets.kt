package io.github.murtazanasir.mailr.ui.tasks

import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.i18n.AppText
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Flag
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.DeleteOutline
import androidx.compose.material.icons.outlined.EventRepeat
import androidx.compose.material.icons.outlined.Flag
import androidx.compose.material.icons.outlined.FolderOpen
import androidx.compose.material.icons.outlined.Lock
import androidx.compose.material.icons.outlined.Sell
import io.github.murtazanasir.mailr.look.AlertDialog
import androidx.compose.material3.BottomAppBar
import io.github.murtazanasir.mailr.look.Button
import io.github.murtazanasir.mailr.look.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.OutlinedButton
import io.github.murtazanasir.mailr.look.OutlinedTextField
import io.github.murtazanasir.mailr.look.RadioButton
import io.github.murtazanasir.mailr.look.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import io.github.murtazanasir.mailr.look.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.material3.TriStateCheckbox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.state.ToggleableState
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.cal.CalConflict
import io.github.murtazanasir.mailr.data.reminders.Choice
import io.github.murtazanasir.mailr.data.reminders.QuickChoices
import io.github.murtazanasir.mailr.data.reminders.WhenSheet
import io.github.murtazanasir.mailr.ui.calendar.WhenDialog
import io.github.murtazanasir.mailr.ui.calendar.WhenSheetPanel
import io.github.murtazanasir.mailr.data.reminders.ReminderPlan
import io.github.murtazanasir.mailr.ui.calendar.CalendarColors
import io.github.murtazanasir.mailr.ui.calendar.ChipFlow
import io.github.murtazanasir.mailr.ui.calendar.QuickDates
import io.github.murtazanasir.mailr.ui.calendar.WhenPicker
import io.github.murtazanasir.mailr.ui.calendar.describeMoment
import java.time.Duration
import java.time.Instant
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.temporal.TemporalAdjusters

/** The verbs snooze, reschedule, remind and defer, each saying what it changes. */
enum class TaskVerb(@androidx.annotation.StringRes val labelRes: Int, @androidx.annotation.StringRes val helpRes: Int) {
    RESCHEDULE(R.string.tasks_verb_reschedule, R.string.tasks_verb_reschedule_help),
    DEFER(R.string.tasks_verb_defer, R.string.tasks_verb_defer_help),
    REMIND(R.string.tasks_verb_remind, R.string.tasks_verb_remind_help);

    val label: String get() = AppText.get(labelRes)
    val help: String get() = AppText.get(helpRes)
}

/**
 * The swipe-left sheet (and the due chip's): Reschedule, Defer and Remind me, kept apart so
 * each says what it changes. A repeating task also offers Skip.
 */
@Composable
fun RescheduleSheet(
    task: TaskItem,
    morningHour: Int,
    onReschedule: (due: String?) -> Unit,
    onDefer: (start: String?) -> Unit,
    onRemind: (minutes: Int) -> Unit,
    onSkip: (() -> Unit)?,
    /** "At a time…": an absolute reminder (an instant); without it the time is kept as an offset. */
    onRemindAt: ((String) -> Unit)? = null,
    initial: TaskVerb = TaskVerb.RESCHEDULE,
) {
    val z = ZoneId.systemDefault()
    val now = AppClock.now(z)
    var verb by remember { mutableStateOf(initial) }
    val due = task.due?.let { ReminderPlan.instant(it, z) }?.let { Instant.ofEpochMilli(it).atZone(z) }
    val dateOnly = task.due?.let { ReminderPlan.isDateOnly(it) } ?: false
    fun iso(at: ZonedDateTime, only: Boolean) = if (only) at.toLocalDate().toString() else at.toOffsetDateTime().toString()
    Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).navigationBarsPadding().padding(bottom = 12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(task.summary.ifBlank { stringResource(R.string.tasks_untitled) }, style = MaterialTheme.typography.titleMedium, maxLines = 2,
            overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(horizontal = 20.dp).semantics { heading() })
        SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth().padding(horizontal = 16.dp)) {
            TaskVerb.entries.forEachIndexed { i, v ->
                SegmentedButton(selected = verb == v, onClick = { verb = v }, shape = SegmentedButtonDefaults.itemShape(i, TaskVerb.entries.size)) {
                    Text(stringResource(v.labelRes), maxLines = 1)
                }
            }
        }
        Text(stringResource(verb.helpRes), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(horizontal = 20.dp))
        when (verb) {
            // ACT task.reschedule @android.tasks
            TaskVerb.RESCHEDULE -> {
                // The when sheet; Pick date & time… opens the full wheels.
                var full by remember { mutableStateOf(false) }
                WhenSheetPanel(WhenSheet.Spec(WhenSheet.Verb.RESCHEDULE, true, now, due, dateOnly, morningHour), stringResource(R.string.tasks_verb_reschedule), null,
                    scrollable = false, showHint = false, onFull = { full = true }) { at, only, _ -> onReschedule(iso(at, only)) }
                if (full) WhenDialog(stringResource(R.string.tasks_verb_reschedule), due ?: now.plusDays(1).withHour(morningHour).withMinute(0), emptyList(), allowNoTime = true,
                    initiallyDateOnly = dateOnly || due == null, onDismiss = { full = false }, onOtherDate = null) { at, only ->
                    full = false
                    onReschedule(iso(at, only))
                }
                Row(Modifier.padding(horizontal = 12.dp)) {
                    if (task.due != null) TextButton(onClick = { onReschedule(null) }) { Text(stringResource(R.string.tasks_no_date)) }
                    // ACT task.skip @android.tasks
                    if (onSkip != null) TextButton(onClick = onSkip) { Text(stringResource(R.string.tasks_skip_occurrence)) }
                }
            }
            // ACT task.set_start @android.tasks
            TaskVerb.DEFER -> {
                val tomorrow = now.plusDays(1).toLocalDate().atTime(morningHour, 0).atZone(z)
                val monday = now.with(TemporalAdjusters.next(java.time.DayOfWeek.MONDAY)).toLocalDate().atTime(morningHour, 0).atZone(z)
                WhenPicker(task.start?.let { ReminderPlan.instant(it, z) }?.let { Instant.ofEpochMilli(it).atZone(z) } ?: tomorrow, now,
                    listOf(Choice("tomorrow", stringResource(R.string.tasks_tomorrow), tomorrow), Choice("week", stringResource(R.string.tasks_next_week), monday)), stringResource(R.string.tasks_verb_defer),
                    allowNoTime = true, initiallyDateOnly = true) { at, only -> onDefer(iso(at, only)) }
                if (task.start != null) TextButton(onClick = { onDefer(null) }, modifier = Modifier.padding(horizontal = 12.dp)) { Text(stringResource(R.string.tasks_remove_start)) }
            }
            // ACT task.remind_at @android.tasks
            TaskVerb.REMIND -> if (due == null) {
                Text(stringResource(R.string.tasks_due_first), Modifier.padding(horizontal = 20.dp),
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            } else {
                val options = if (dateOnly) QuickDates.allDayReminders(morningHour).map { it.minutes } else QuickChoices.OFFSETS
                ChipFlow(Modifier.padding(horizontal = 16.dp)) {
                    options.filter { it !in task.reminders }.forEach { m ->
                        FilterChip(selected = false, onClick = { onRemind(m) },
                            label = { Text(if (m == 0 && !dateOnly) stringResource(R.string.tasks_at_due_time) else QuickDates.reminderLabel(m, dateOnly, morningHour)) })
                    }
                }
                Text(stringResource(R.string.tasks_at_a_time), style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(horizontal = 20.dp))
                WhenPicker(due.minusHours(1), now, emptyList(), stringResource(R.string.tasks_verb_remind), allowNoTime = false, initiallyDateOnly = false) { at, _ ->
                    if (onRemindAt != null) onRemindAt(at.toInstant().toString()) else {
                        val base = if (dateOnly) due.toLocalDate().atStartOfDay(z) else due
                        onRemind(Duration.between(at, base).toMinutes().toInt())
                    }
                }
            }
        }
    }
}

/** Move to list: the task lists with their colours; read-only lists are left out. */
@Composable
fun ListPickerSheet(title: String, lists: List<CalendarCollection>, current: String?, onPick: (String) -> Unit) {
    Column(Modifier.fillMaxWidth().navigationBarsPadding().padding(bottom = 12.dp)) {
        Text(title, style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(horizontal = 20.dp, vertical = 8.dp).semantics { heading() })
        lists.filterNot { it.readOnly }.forEach { c ->
            Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).selectable(c.url == current, role = Role.RadioButton) { onPick(c.url) }
                .padding(horizontal = 20.dp), verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(14.dp).background(CalendarColors.of(c.url, c.color), CircleShape))
                Spacer(Modifier.width(16.dp))
                Text(c.name, Modifier.weight(1f))
                if (c.url == current) Icon(Icons.Filled.Check, stringResource(R.string.tasks_current_list))
            }
        }
    }
}

/** Priority: None, Low, Medium, High (keys 0–3 on the web). */
@Composable
fun PriorityDialog(current: Int?, onDismiss: () -> Unit, onPick: (Int) -> Unit) {
    AlertDialog(onDismissRequest = onDismiss, title = { Text(stringResource(R.string.tasks_priority)) },
        text = {
            Column {
                CalendarColors.priorityNames.indices.reversed().forEach { lvl ->
                    Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).selectable(current == lvl, role = Role.RadioButton) { onPick(lvl) },
                        verticalAlignment = Alignment.CenterVertically) {
                        RadioButton(current == lvl, null)
                        Spacer(Modifier.width(12.dp))
                        Icon(if (lvl > 0) Icons.Filled.Flag else Icons.Outlined.Flag, null, tint = CalendarColors.priority(lvl))
                        Spacer(Modifier.width(8.dp))
                        Text(CalendarColors.priorityNames[lvl])
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.tasks_cancel)) } })
}

/**
 * Tags for a selection, tri-state: a box is on when every
 * selected task has the tag, mixed when some do. Only the boxes the user changed are applied.
 * A new tag is typed below.
 */
@Composable
fun TagsDialog(known: List<String>, selected: List<TaskItem>, onDismiss: () -> Unit, onApply: (add: Set<String>, remove: Set<String>) -> Unit) {
    val initial = remember(selected, known) {
        (known + selected.flatMap { it.categories }).distinct().associateWith { tag ->
            val n = selected.count { tag in it.categories }
            when (n) { 0 -> ToggleableState.Off; selected.size -> ToggleableState.On; else -> ToggleableState.Indeterminate }
        }
    }
    var state by remember { mutableStateOf(initial) }
    var fresh by remember { mutableStateOf("") }
    AlertDialog(onDismissRequest = onDismiss, title = { Text(stringResource(R.string.tasks_tags_title)) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                state.forEach { (tag, s) ->
                    Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).clickable {
                        state = state + (tag to if (s == ToggleableState.On) ToggleableState.Off else ToggleableState.On)
                    }, verticalAlignment = Alignment.CenterVertically) {
                        TriStateCheckbox(s, null)
                        Spacer(Modifier.width(12.dp))
                        Text("@$tag")
                    }
                }
                OutlinedTextField(fresh, { fresh = it.replace(" ", "") }, label = { Text(stringResource(R.string.tasks_new_tag)) }, singleLine = true,
                    modifier = Modifier.fillMaxWidth().padding(top = 8.dp))
            }
        },
        confirmButton = {
            TextButton(onClick = {
                val changed = state.filter { (k, v) -> initial[k] != v }
                val add = changed.filterValues { it == ToggleableState.On }.keys + listOfNotNull(fresh.removePrefix("@").ifBlank { null })
                onApply(add.toSet(), changed.filterValues { it == ToggleableState.Off }.keys)
            }) { Text(stringResource(R.string.tasks_apply)) }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.tasks_cancel)) } })
}

/** The selection bar at the bottom: Complete, Reschedule, Move, Tags, Priority, Delete. */
@Composable
fun SelectionBar(count: Int, onComplete: () -> Unit, onReschedule: () -> Unit, onMove: () -> Unit, onTags: () -> Unit,
                 onPriority: () -> Unit, onDelete: () -> Unit) {
    BottomAppBar {
        val items: List<Triple<String, ImageVector, () -> Unit>> = listOf(
            Triple(stringResource(R.string.tasks_sel_complete), Icons.Outlined.CheckCircle, onComplete),
            Triple(stringResource(R.string.tasks_sel_reschedule), Icons.Outlined.EventRepeat, onReschedule),
            Triple(stringResource(R.string.tasks_sel_move), Icons.Outlined.FolderOpen, onMove),
            Triple(stringResource(R.string.tasks_sel_tags), Icons.Outlined.Sell, onTags),
            Triple(stringResource(R.string.tasks_sel_priority), Icons.Outlined.Flag, onPriority),
            Triple(stringResource(R.string.tasks_sel_delete), Icons.Outlined.DeleteOutline, onDelete),
        )
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceEvenly) {
            items.forEach { (label, icon, click) ->
                Column(Modifier.heightIn(min = 48.dp).clickable(enabled = count > 0, onClickLabel = label, onClick = click).padding(4.dp),
                    horizontalAlignment = Alignment.CenterHorizontally) {
                    Icon(icon, null)
                    Text(label, style = MaterialTheme.typography.labelSmall, maxLines = 1)
                }
            }
        }
    }
}

/** Reschedule a selection: the when picker, plus "Shift by +1 day" and "+1 week", which keep each task's time. */
@Composable
fun BulkRescheduleSheet(count: Int, morningHour: Int, onDue: (String?) -> Unit, onShift: (Long) -> Unit) {
    val z = ZoneId.systemDefault()
    val now = AppClock.now(z)
    Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).navigationBarsPadding().padding(bottom = 12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(pluralStringResource(R.plurals.tasks_bulk_reschedule, count, count), style = MaterialTheme.typography.titleMedium,
            modifier = Modifier.padding(horizontal = 20.dp).semantics { heading() })
        Row(Modifier.padding(horizontal = 16.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = { onShift(1) }) { Text(stringResource(R.string.tasks_plus_day)) }
            OutlinedButton(onClick = { onShift(7) }) { Text(stringResource(R.string.tasks_plus_week)) }
            TextButton(onClick = { onDue(null) }) { Text(stringResource(R.string.tasks_no_date)) }
        }
        Text(stringResource(R.string.tasks_shifts_keep_time), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(horizontal = 20.dp))
        HorizontalDivider()
        WhenPicker(now.plusDays(1).withHour(morningHour).withMinute(0), now, QuickChoices.taskReschedule(now, null, true, morningHour),
            stringResource(R.string.tasks_verb_reschedule), allowNoTime = true, initiallyDateOnly = true) { at, only ->
            onDue(if (only) at.toLocalDate().toString() else at.toOffsetDateTime().toString())
        }
    }
}

/**
 * The conflict sheet on the phone: the same field changed here and elsewhere. Their version is shown;
 * Keep theirs, Use mine, Keep both.
 */
@Composable
fun ConflictSheet(c: CalConflict, onTheirs: () -> Unit, onMine: () -> Unit, onBoth: () -> Unit) {
    val mine = c.mine
    fun field(k: String) = (mine?.get(k) as? kotlinx.serialization.json.JsonPrimitive)?.content
    val zone = ZoneId.systemDefault()
    val now = AppClock.now(zone)
    val noDate = stringResource(R.string.tasks_no_date)
    fun whenOf(s: String?) = s?.let { ReminderPlan.instant(it, zone) }?.let {
        describeMoment(Instant.ofEpochMilli(it).atZone(zone), now, ReminderPlan.isDateOnly(s), relative = false)
    } ?: noDate
    val theirs = c.serverTask
    Column(Modifier.fillMaxWidth().navigationBarsPadding().padding(horizontal = 20.dp, vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Outlined.Lock, null, tint = MaterialTheme.colorScheme.error)
            Spacer(Modifier.width(8.dp))
            Text(stringResource(R.string.tasks_conflict_title), style = MaterialTheme.typography.titleMedium, modifier = Modifier.semantics { heading() })
        }
        if (theirs == null) {
            Text(stringResource(R.string.tasks_conflict_deleted), color = MaterialTheme.colorScheme.onSurfaceVariant)
        } else {
            Text(stringResource(R.string.tasks_conflict_theirs), style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary)
            Text(theirs.summary, style = MaterialTheme.typography.bodyLarge)
            Text(whenOf(theirs.due), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Text(stringResource(R.string.tasks_conflict_yours), style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary)
        Text(field("summary") ?: theirs?.summary.orEmpty(), style = MaterialTheme.typography.bodyLarge)
        if (mine?.containsKey("due") == true || mine?.containsKey("clear_due") == true)
            Text(whenOf(field("due")), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        if (mine?.containsKey("completed") == true) Text(stringResource(if (field("completed") == "true") R.string.tasks_completed else R.string.tasks_reopened),
            style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.End)) {
            TextButton(onClick = onBoth) { Text(stringResource(if (theirs == null) R.string.tasks_restore_new else R.string.tasks_keep_both)) }
            if (theirs != null) OutlinedButton(onClick = onTheirs) { Text(stringResource(R.string.tasks_keep_theirs)) }
            Button(onClick = if (theirs == null) onBoth else onMine) { Text(stringResource(if (theirs == null) R.string.tasks_restore else R.string.tasks_use_mine)) }
        }
        if (theirs == null) TextButton(onClick = onTheirs, modifier = Modifier.align(Alignment.End)) { Text(stringResource(R.string.tasks_let_go)) }
    }
}

/** The swipe actions (by default right completes, left reschedules; either can be changed). */
@Composable
fun SwipeSettingsDialog(right: SwipeAction, left: SwipeAction, onDismiss: () -> Unit, onSave: (SwipeAction, SwipeAction) -> Unit) {
    var r by remember { mutableStateOf(right) }
    var l by remember { mutableStateOf(left) }
    AlertDialog(onDismissRequest = onDismiss, title = { Text(stringResource(R.string.tasks_swipe_actions)) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                listOf(stringResource(R.string.tasks_swipe_right) to r, stringResource(R.string.tasks_swipe_left) to l).forEachIndexed { i, (title, cur) ->
                    Text(title, style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(top = if (i == 0) 0.dp else 12.dp))
                    SwipeAction.entries.forEach { a ->
                        Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).selectable(cur == a, role = Role.RadioButton) {
                            if (i == 0) r = a else l = a
                        }, verticalAlignment = Alignment.CenterVertically) {
                            RadioButton(cur == a, null)
                            Spacer(Modifier.width(12.dp))
                            Text(stringResource(a.labelRes))
                        }
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = { onSave(r, l) }) { Text(stringResource(R.string.tasks_save)) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.tasks_cancel)) } })
}
