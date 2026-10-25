package io.github.murtazanasir.mailr.ui.calendar

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.time.AppClock
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.i18n.LocaleFormats
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.List
import androidx.compose.material.icons.automirrored.outlined.Notes
import androidx.compose.material.icons.automirrored.outlined.Reply
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.DeleteOutline
import androidx.compose.material.icons.outlined.Event
import androidx.compose.material.icons.outlined.Flag
import androidx.compose.material.icons.outlined.Repeat
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material.icons.outlined.Sell
import androidx.compose.material.icons.outlined.Start
import androidx.compose.material.icons.outlined.SubdirectoryArrowRight
import io.github.murtazanasir.mailr.look.Checkbox
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import io.github.murtazanasir.mailr.look.InputChip
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.OutlinedButton
import io.github.murtazanasir.mailr.look.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import io.github.murtazanasir.mailr.look.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.SuggestionChip
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import io.github.murtazanasir.mailr.look.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.foundation.layout.offset
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.isAltPressed
import androidx.compose.ui.input.key.isCtrlPressed
import androidx.compose.ui.input.key.isMetaPressed
import androidx.compose.ui.input.key.isShiftPressed
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.TextFieldValue
import io.github.murtazanasir.mailr.notes.ui.UndoRedoButtons
import kotlinx.coroutines.launch
import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import io.github.murtazanasir.mailr.data.api.TaskDraft
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.reminders.QuickChoices
import io.github.murtazanasir.mailr.data.reminders.ReminderPlan
import io.github.murtazanasir.mailr.ui.util.Format
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.util.Locale

@Stable
class TaskEditorState(original: TaskItem?, list: String?, parent: String?, val uid: String, prefill: QuickParse? = null,
                      draft: TaskDraft? = null) {
    /** The task as the server has it (live editing moves it to the copy that came in: [rebase]). */
    var original: TaskItem? by mutableStateOf(original)
        private set
    private val o = original
    private val zone = ZoneId.systemDefault()
    var title by mutableStateOf(o?.summary ?: draft?.summary ?: prefill?.title.orEmpty())
    var notes by mutableStateOf(o?.notes ?: draft?.notes?.ifEmpty { null } ?: prefill?.notes.orEmpty())
    var dueDate by mutableStateOf((o?.due ?: draft?.due)?.let { dayOf(it) } ?: prefill?.date)
    var dueTime by mutableStateOf((o?.due ?: draft?.due)?.takeUnless { ReminderPlan.isDateOnly(it) }?.let { Format.parse(it)?.toLocalTime() } ?: prefill?.time)
    var priority by mutableStateOf(CalendarColors.priorityLevel(o?.priority ?: draft?.priority ?: prefill?.priority ?: 0))
    var list by mutableStateOf(o?.list ?: draft?.list ?: list)
    var parent by mutableStateOf(o?.parent ?: draft?.parent ?: parent)
    var reminders by mutableStateOf(o?.reminders ?: draft?.reminders.orEmpty())
    /**
     * Absolute reminders ("At a time…", `reminders_at`), as instants. Null when the stored copy
     * does not say (it came from the delta); they are then left alone unless one is added.
     */
    var remindersAt by mutableStateOf(o?.remindersAt ?: draft?.remindersAt)
    /** DTSTART (defer): the task is hidden from Today until then. Date only, or a time. */
    var startDate by mutableStateOf((o?.start ?: draft?.start)?.let { dayOf(it) })
    var startTime by mutableStateOf((o?.start ?: draft?.start)?.takeUnless { ReminderPlan.isDateOnly(it) }?.let { Format.parse(it)?.toLocalTime() })
    var recurrence by mutableStateOf(Recurrence.parse(o?.rrule ?: draft?.recurrence))
    /** "due" or "completion" (task variant). */
    var repeatFrom by mutableStateOf(o?.repeatFrom ?: draft?.repeatFrom ?: "due")
    var tags by mutableStateOf(o?.categories ?: draft?.categories.orEmpty())
    var showErrors by mutableStateOf(false)
    private var initial = snapshot()
    private var initialRule = recurrence?.toRRule()

    val isNew get() = original == null
    val titleError: String? get() = if (showErrors && title.isBlank()) AppText.get(R.string.cal_error_add_title) else null
    val valid get() = title.isNotBlank()
    val dirty get() = snapshot() != initial
    private fun snapshot() = listOf(title, notes, dueDate, dueTime, priority, list, reminders, startDate, startTime,
        recurrence?.toRRule(), repeatFrom, tags, remindersAt)

    /** The fields now, as [dirty] compares them (an autosave keeps the ones it sent: [markClean]). */
    fun fields(): List<Any?> = snapshot()

    /** These fields ([at], by default the ones now) are saved: [dirty] compares with them. */
    fun markClean(at: List<Any?> = snapshot()) { initial = at }

    /** The editor's undo and redo (TaskHistory.kt); made when the editor first records a step. */
    val history: TaskHistory by lazy {
        TaskHistory(AppClock::millis).also { h ->
            h.fieldsReader = ::opFields
            h.fieldWriter = ::setOpField
        }
    }

    /** The fields other than the title and notes, as a field step keeps them (in [setOpField]'s order). */
    fun opFields(): List<Any?> = listOf(dueDate, dueTime, startDate, startTime, priority, list, reminders, remindersAt,
        recurrence, repeatFrom, tags)

    @Suppress("UNCHECKED_CAST")
    fun setOpField(i: Int, v: Any?) {
        when (i) {
            0 -> dueDate = v as LocalDate?
            1 -> dueTime = v as LocalTime?
            2 -> startDate = v as LocalDate?
            3 -> startTime = v as LocalTime?
            4 -> priority = v as Int
            5 -> list = v as String?
            6 -> reminders = v as List<Int>
            7 -> remindersAt = v as List<String>?
            8 -> recurrence = v as Recurrence?
            9 -> repeatFrom = v as String
            10 -> tags = v as List<String>
        }
    }

    /** A change made in the editor: one step of its undo ([history]) when it changed a field. */
    fun edit(block: TaskEditorState.() -> Unit) {
        val before = opFields()
        block()
        history.recordFields("fields", before, opFields())
    }

    /** Live editing: the server's copy is now [item] (the fields came from it). */
    fun rebase(item: TaskItem) {
        original = item
        initialRule = Recurrence.parse(item.rrule)?.toRRule()
    }

    private fun dayOf(s: String): LocalDate? =
        if (ReminderPlan.isDateOnly(s)) catching { LocalDate.parse(s.take(10)) }.getOrNull() else Format.parse(s)?.toLocalDate()

    fun setDate(d: LocalDate?) {
        val wasTimed = dueTime != null
        dueDate = d
        if (d == null) { dueTime = null; reminders = emptyList() }
        else if (!wasTimed) reminders = reminders.filter { it < 0 || it >= 1440 }
    }

    fun setTime(t: LocalTime?) {
        // Reminders are relative to the due moment; switching date-only ↔ timed changes their meaning.
        if ((t == null) != (dueTime == null)) reminders = emptyList()
        dueTime = t
    }

    fun setStart(d: LocalDate?, t: LocalTime? = null) {
        startDate = d
        startTime = if (d == null) null else t
    }

    fun addTag(raw: String) {
        val t = raw.trim().removePrefix("@").removePrefix("#").replace(" ", "")
        if (t.isNotEmpty() && tags.none { it.equals(t, ignoreCase = true) }) tags = tags + t
    }

    /** ACT task.remind_at @android.editor: an absolute reminder ("At a time…"), sent as `reminders_at`. */
    fun addReminderAt(at: LocalDateTime) {
        if (dueDate == null) return
        val iso = at.atZone(zone).toInstant().toString()
        remindersAt = (remindersAt.orEmpty() + iso).distinct().sorted()
    }

    fun removeReminderAt(iso: String) { remindersAt = remindersAt.orEmpty() - iso }

    fun draft(): TaskDraft {
        val due = dueDate?.let { d -> dueTime?.let { t -> d.atTime(t).atZone(zone).toOffsetDateTime().toString() } ?: d.toString() }
        val start = startDate?.let { d -> startTime?.let { t -> d.atTime(t).atZone(zone).toOffsetDateTime().toString() } ?: d.toString() }
        val rule = recurrence?.toRRule()
        return TaskDraft(uid, title.trim(), notes.trim(), due, CalendarColors.priorityValue(priority), list, parent,
            if (due == null) emptyList() else reminders,
            remindersAt = if (due == null) remindersAt?.let { emptyList() } else remindersAt,
            start = start,
            clearStart = start == null && original?.start != null,
            // An unchanged rule is left alone, so a rule mailr cannot edit is never rewritten.
            recurrence = when { rule == initialRule -> null; rule == null -> ""; else -> rule },
            repeatFrom = repeatFrom.takeIf { recurrence != null || original?.recurring == true },
            categories = tags.takeIf { it.isNotEmpty() || original?.categories?.isNotEmpty() == true },
            tz = if (due != null && dueTime != null) zone.id else null)
    }
}

@Composable
fun TaskEditor(
    s: TaskEditorState,
    lists: List<CalendarCollection>,
    subtasks: List<TaskItem>,
    morningHour: Int,
    saving: Boolean,
    onClose: () -> Unit,
    onSave: () -> Unit,
    onDelete: (() -> Unit)?,
    onAddSubtask: ((String) -> Unit)?,
    onToggleSubtask: (TaskItem) -> Unit,
    onOpenSubtask: (TaskItem) -> Unit,
    onOpenEmail: (() -> Unit)? = null,
    knownTags: List<String> = emptyList(),
    onReplyEmail: (() -> Unit)? = null,
    onDuplicate: (() -> Unit)? = null,
    onSkip: (() -> Unit)? = null,
    /** Opens the assistant about this task (context.focused_task). */
    onAskAssistant: (() -> Unit)? = null,
    /** The live-editing bars (LiveBars), under the header. */
    live: (@Composable () -> Unit)? = null,
    /** Undo and Redo in the header, and Ctrl+Z on a keyboard ([TaskEditorState.history]); the full editor has them. */
    undoRedo: Boolean = false,
    /** A short message (a step that could not be undone). */
    onSay: (String) -> Unit = {},
) {
    var pickDate by remember { mutableStateOf(false) }
    var pickStart by remember { mutableStateOf(false) }
    var pickRepeat by remember { mutableStateOf(false) }
    var pickReminderAt by remember { mutableStateOf(false) }
    var pickWhen by remember { mutableStateOf(false) }
    var listMenu by remember { mutableStateOf(false) }
    val focus = remember { FocusRequester() }
    LaunchedEffect(Unit) { if (s.isNew && s.title.isBlank()) catching { focus.requestFocus() } }
    val today = AppClock.today()
    val hist = s.history
    val scope = rememberCoroutineScope()
    val notesFocus = remember { FocusRequester() }
    // The title and notes with their cursor. A change from elsewhere (live editing) shows at once and
    // keeps the cursor where it can; an undo or redo puts the cursor where the change was.
    var titleV by remember(s) { mutableStateOf(TextFieldValue(s.title, TextRange(s.title.length))) }
    var notesV by remember(s) { mutableStateOf(TextFieldValue(s.notes, TextRange(s.notes.length))) }
    val titleShown = withText(titleV, s.title)
    val notesShown = withText(notesV, s.notes)
    DisposableEffect(s) {
        hist.reader = { f -> when (f) { "title" -> s.title; "notes" -> s.notes; else -> null } }
        hist.writer = { f, v, caret ->
            val at = TextFieldValue(v, TextRange(caret.coerceIn(0, v.length)))
            if (f == "title") { s.title = v; titleV = at } else { s.notes = v; notesV = at }
            catching { (if (f == "title") focus else notesFocus).requestFocus() }
        }
        onDispose { hist.reader = null; hist.writer = null }
    }
    // A field that holds typing not yet part of the task (a tag, a new subtask) keeps its own Ctrl+Z.
    var draftFocus by remember { mutableStateOf(false) }
    // ACT task.undo_edit @android.editor, ACT task.redo_edit @android.editor
    val undo = { scope.launch { hist.undo(onSay) }; Unit }
    val redo = { scope.launch { hist.redo(onSay) }; Unit }

    Column(Modifier.fillMaxWidth().imePadding().onPreviewKeyEvent { e ->
        // A hardware keyboard: Ctrl+Z undoes, Ctrl+Shift+Z and Ctrl+Y redo (as in the note editor).
        if (!undoRedo || e.type != KeyEventType.KeyDown || !(e.isCtrlPressed || e.isMetaPressed) || e.isAltPressed) return@onPreviewKeyEvent false
        val isRedo = (e.key == Key.Z && e.isShiftPressed) || (e.key == Key.Y && !e.isShiftPressed)
        val isUndo = e.key == Key.Z && !e.isShiftPressed
        when {
            !isUndo && !isRedo -> false
            draftFocus -> false
            isRedo -> { redo(); true }
            else -> { undo(); true }
        }
    }) {
        EditorHeader(if (s.isNew) (if (s.parent != null) stringResource(R.string.cal_task_new_subtask) else stringResource(R.string.cal_task_new)) else stringResource(R.string.cal_task_edit),
            if (saving) stringResource(R.string.cal_saving) else stringResource(R.string.cal_save), !saving, onClose,
            actions = { if (undoRedo) UndoRedoButtons(hist.canUndo, hist.canRedo, onUndo = undo, onRedo = redo) }) {
            // ACT task.edit @android.editor: every field saves together (PATCH /tasks/{uid}).
            s.showErrors = true
            if (s.valid) onSave()
        }
        HorizontalDivider()
        live?.invoke()
        Column(Modifier.weight(1f, fill = false).verticalScroll(rememberScrollState()).padding(bottom = 24.dp)) {
            TextField(
                titleShown, { v ->
                    if (v.text != s.title) { hist.recordText("title", s.title, v.text); s.title = v.text }
                    titleV = v
                },
                placeholder = { Text(stringResource(R.string.cal_task_title_placeholder), style = MaterialTheme.typography.headlineSmall) },
                textStyle = MaterialTheme.typography.headlineSmall,
                isError = s.titleError != null,
                supportingText = s.titleError?.let { e -> { Text(e) } },
                keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences, imeAction = ImeAction.Done),
                colors = TextFieldDefaults.colors(focusedContainerColor = Color.Transparent, unfocusedContainerColor = Color.Transparent,
                    errorContainerColor = Color.Transparent),
                modifier = Modifier.fillMaxWidth().padding(start = 40.dp, end = 8.dp).focusRequester(focus).testTag("task-title")
                    .onFocusChanged { if (!it.isFocused) hist.seal() },
            )
            FieldRow(Icons.AutoMirrored.Outlined.Notes) {
                TextField(notesShown, { v ->
                    if (v.text != s.notes) { hist.recordText("notes", s.notes, v.text); s.notes = v.text }
                    notesV = v
                }, placeholder = { Text(stringResource(R.string.cal_task_add_details)) }, singleLine = false, minLines = 1, maxLines = 8,
                    keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences),
                    colors = TextFieldDefaults.colors(focusedContainerColor = Color.Transparent, unfocusedContainerColor = Color.Transparent,
                        unfocusedIndicatorColor = Color.Transparent),
                    // Lined up with the other fields, as PlainField does (TextField pads its content by 16 dp).
                    modifier = Modifier.fillMaxWidth().offset(x = (-16).dp).focusRequester(notesFocus).testTag("task-notes")
                        .onFocusChanged { if (!it.isFocused) hist.seal() })
            }
            HorizontalDivider(Modifier.padding(vertical = 4.dp))
            FieldRow(Icons.Outlined.Event) {
                Column {
                    Text(dueText(s), style = MaterialTheme.typography.bodyLarge,
                        color = if (s.dueDate == null) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface)
                    Spacer(Modifier.height(4.dp))
                    ChipFlow {
                        val presets = QuickDates.taskDuePresets(today)
                        presets.forEach { p -> PresetChip(p.label, s.dueDate == p.date) { s.edit { setDate(if (s.dueDate == p.date) null else p.date) } } }
                        PresetChip(if (s.dueDate != null && presets.none { it.date == s.dueDate }) s.dueDate!!.format(LocaleFormats.date("MMMd", "MMM d")) else stringResource(R.string.cal_task_pick_date),
                            s.dueDate != null && presets.none { it.date == s.dueDate }) { pickWhen = true }
                        if (s.dueDate != null) PresetChip(stringResource(R.string.cal_task_no_date), false) { s.edit { setDate(null) } }
                    }
                }
            }
            if (s.dueDate != null) FieldRow(Icons.Outlined.Schedule) {
                ChipFlow {
                    PresetChip(stringResource(R.string.cal_no_time), s.dueTime == null) { s.edit { setTime(null) } }
                    val presets = QuickDates.timePresets(AppClock.localNow(), s.dueDate!!)
                    presets.forEach { (label, t) -> PresetChip(stringResource(R.string.cal_task_time_preset, label, QuickDates.timeLabel(t)), s.dueTime == t) { s.edit { setTime(t) } } }
                    val custom = s.dueTime != null && presets.none { it.second == s.dueTime }
                    PresetChip(if (custom) QuickDates.timeLabel(s.dueTime!!) else stringResource(R.string.cal_task_pick_time), custom) { pickWhen = true }
                }
            }
            ReminderPicker(
                s.reminders,
                if (s.dueTime == null) QuickDates.allDayReminders(morningHour) else QuickDates.taskReminders,
                label = { QuickDates.reminderLabel(it, s.dueTime == null, morningHour) },
                onChange = { r -> s.edit { reminders = r } },
                enabled = s.dueDate != null,
                disabledHint = stringResource(R.string.cal_task_reminder_needs_due),
            )
            if (s.dueDate != null && s.reminders.isEmpty()) {
                Text(if (s.dueTime == null) stringResource(R.string.cal_task_reminded_on_day, QuickDates.hourLabel(morningHour)) else stringResource(R.string.cal_task_reminded_when_due),
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(start = 56.dp, bottom = 4.dp))
            }
            // ACT task.remind_at: "At a time…" (an absolute reminder, the task option among the reminder offsets).
            s.remindersAt?.takeIf { it.isNotEmpty() && s.dueDate != null }?.let { ats ->
                ChipFlow(Modifier.padding(start = 56.dp, end = 16.dp)) {
                    val fmt = LocaleFormats.date("EEEdMMM", "EEE d MMM")
                    ats.forEach { iso ->
                        val at = Format.parse(iso)?.withZoneSameInstant(ZoneId.systemDefault())
                        val label = at?.let { AppText.get(R.string.rem_day_time, it.format(fmt), QuickDates.timeLabel(it.toLocalTime())) } ?: iso
                        InputChip(selected = false, onClick = { s.edit { removeReminderAt(iso) } }, label = { Text(label) },
                            trailingIcon = { Icon(Icons.Outlined.Close, stringResource(R.string.cal_remove_reminder_cd, label), Modifier.size(18.dp)) })
                    }
                }
            }
            if (s.dueDate != null) TextButton(onClick = { pickReminderAt = true }, modifier = Modifier.padding(start = 48.dp)) {
                Text(stringResource(R.string.cal_task_remind_at_time))
            }
            // ACT task.set_start @android.editor: Starts (defer); the task stays out of Today until then.
            FieldRow(Icons.Outlined.Start) {
                Column {
                    val startDay = s.startDate?.let { ReminderPlan.dayWord(it, today) }
                    Text(startDay?.let { d -> s.startTime?.let { t -> stringResource(R.string.cal_task_starts_time, d, QuickDates.timeLabel(t)) } ?: stringResource(R.string.cal_qa_starts, d) }
                        ?: stringResource(R.string.cal_task_no_start_date), color = if (s.startDate == null) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface)
                    ChipFlow {
                        PresetChip(stringResource(R.string.rem_tomorrow), s.startDate == today.plusDays(1)) { s.edit { setStart(today.plusDays(1)) } }
                        val mon = today.with(java.time.temporal.TemporalAdjusters.next(java.time.DayOfWeek.MONDAY))
                        PresetChip(stringResource(R.string.rem_next_week), s.startDate == mon) { s.edit { setStart(mon) } }
                        PresetChip(stringResource(R.string.cal_task_pick), false) { pickStart = true }
                        if (s.startDate != null) PresetChip(stringResource(R.string.cal_task_no_start), false) { s.edit { setStart(null) } }
                    }
                }
            }
            // ACT task.set_repeat @android.editor @android.tasks: the repeat options, task variant (from the due date or from completion).
            FieldRow(Icons.Outlined.Repeat, onClick = { pickRepeat = true }, onClickLabel = stringResource(R.string.cal_change_repeat)) {
                Column {
                    Text(s.recurrence?.describe() ?: stringResource(R.string.cal_rec_does_not_repeat),
                        color = if (s.recurrence == null) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface)
                    if (s.recurrence?.raw != null) Text(stringResource(R.string.cal_task_custom_rule), style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            if (s.recurrence != null) {
                Row(Modifier.padding(start = 56.dp, end = 16.dp, bottom = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(stringResource(R.string.cal_task_repeat_from), style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(end = 12.dp))
                    SingleChoiceSegmentedButtonRow {
                        listOf("due" to stringResource(R.string.cal_task_due_date), "completion" to stringResource(R.string.cal_task_completion)).forEachIndexed { i, (v, l) ->
                            SegmentedButton(selected = s.repeatFrom == v, onClick = { s.edit { repeatFrom = v } },
                                shape = SegmentedButtonDefaults.itemShape(i, 2)) { Text(l, maxLines = 1) }
                        }
                    }
                }
                // ACT task.skip @android.editor
                if (onSkip != null) TextButton(onClick = onSkip, modifier = Modifier.padding(start = 48.dp)) { Text(stringResource(R.string.cal_task_skip_occurrence)) }
            }
            // ACT task.set_priority @android.editor: None, Low, Medium, High (saved with the task).
            FieldRow(Icons.Outlined.Flag) { PriorityPicker(s.priority) { p -> s.edit { priority = p } } }
            if (lists.size > 1) {
                val col = lists.firstOrNull { it.url == s.list }
                FieldRow(Icons.AutoMirrored.Outlined.List, onClick = { listMenu = true }, onClickLabel = stringResource(R.string.cal_task_change_list)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        ColorDot(CalendarColors.of(s.list, col?.color))
                        Spacer(Modifier.width(10.dp))
                        Text(col?.name ?: stringResource(R.string.cal_task_default_list))
                        CollectionMenu(lists, s.list, listMenu, { listMenu = false }) { l -> s.edit { list = l }; listMenu = false }
                    }
                }
            }
            // ACT task.tag @android.editor @android.tasks: a token field; Enter adds a tag, known tags are offered as you type.
            FieldRow(Icons.Outlined.Sell) {
                var tagText by remember { mutableStateOf("") }
                Column {
                    if (s.tags.isNotEmpty()) ChipFlow {
                        s.tags.forEach { t ->
                            InputChip(selected = false, onClick = { s.edit { tags = tags - t } }, label = { Text("@$t") },
                                trailingIcon = { Icon(Icons.Outlined.Close, stringResource(R.string.cal_task_remove_tag, t), Modifier.size(18.dp)) })
                        }
                    }
                    TextField(tagText, { v -> if (v.endsWith(" ") || v.endsWith(",")) { s.edit { addTag(v.trimEnd(' ', ',')) }; tagText = "" } else tagText = v },
                        placeholder = { Text(stringResource(R.string.cal_task_add_tags)) }, singleLine = true,
                        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
                        keyboardActions = KeyboardActions(onDone = { s.edit { addTag(tagText) }; tagText = "" }),
                        colors = TextFieldDefaults.colors(focusedContainerColor = Color.Transparent, unfocusedContainerColor = Color.Transparent),
                        modifier = Modifier.fillMaxWidth().onFocusChanged { draftFocus = it.isFocused })
                    val q = tagText.trim().removePrefix("@").lowercase()
                    val hints = if (q.isEmpty()) emptyList() else knownTags.filter { it.lowercase().startsWith(q) && it !in s.tags }.take(5)
                    if (hints.isNotEmpty()) ChipFlow {
                        hints.forEach { h -> SuggestionChip(onClick = { s.edit { addTag(h) }; tagText = "" }, label = { Text("@$h") }) }
                    }
                }
            }
            if (onAddSubtask != null) {
                HorizontalDivider(Modifier.padding(vertical = 4.dp))
                Text(stringResource(R.string.cal_task_subtasks), style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(start = 56.dp, top = 8.dp, bottom = 4.dp))
                subtasks.forEach { st ->
                    Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).padding(start = 44.dp, end = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(st.completed, { onToggleSubtask(st) })
                        Text(st.summary, Modifier.weight(1f), textDecoration = if (st.completed) TextDecoration.LineThrough else null,
                            color = if (st.completed) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface)
                        IconButton(onClick = { onOpenSubtask(st) }) { Icon(Icons.Outlined.SubdirectoryArrowRight, stringResource(R.string.cal_task_open_named, st.summary)) }
                    }
                }
                var sub by remember { mutableStateOf("") }
                FieldRow(Icons.Outlined.Add) {
                    TextField(sub, { sub = it }, placeholder = { Text(stringResource(R.string.cal_task_add_subtask)) }, singleLine = true,
                        keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences, imeAction = ImeAction.Done),
                        keyboardActions = KeyboardActions(onDone = { if (sub.isNotBlank()) { onAddSubtask(sub.trim()); sub = "" } }),
                        colors = TextFieldDefaults.colors(focusedContainerColor = Color.Transparent, unfocusedContainerColor = Color.Transparent),
                        modifier = Modifier.fillMaxWidth().onFocusChanged { draftFocus = it.isFocused })
                }
            }
            // ACT task.open_email @android.editor
            if (onOpenEmail != null) SourceEmailRow(onOpenEmail)
            // ACT task.reply_email @android.editor
            if (onReplyEmail != null) FieldRow(Icons.AutoMirrored.Outlined.Reply, onClick = onReplyEmail, onClickLabel = stringResource(R.string.cal_task_reply)) {
                Text(stringResource(R.string.cal_task_reply_email), color = MaterialTheme.colorScheme.primary)
            }
            if (onAskAssistant != null) FieldRow(Icons.Outlined.AutoAwesome, onClick = onAskAssistant, onClickLabel = stringResource(R.string.rem_action_ask, io.github.murtazanasir.mailr.ui.assistantName())) {
                Text(stringResource(R.string.rem_action_ask, io.github.murtazanasir.mailr.ui.assistantName()), color = MaterialTheme.colorScheme.primary)
            }
            // ACT task.delete @android.editor (the list removes it after the 7 s undo window), ACT task.duplicate @android.editor
            if (onDelete != null || onDuplicate != null) {
                Spacer(Modifier.height(8.dp))
                Row(Modifier.padding(start = 56.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (onDelete != null) OutlinedButton(onClick = onDelete) {
                        Icon(Icons.Outlined.DeleteOutline, null, tint = MaterialTheme.colorScheme.error)
                        Spacer(Modifier.width(8.dp))
                        Text(stringResource(R.string.cal_task_delete), color = MaterialTheme.colorScheme.error)
                    }
                    if (onDuplicate != null) OutlinedButton(onClick = onDuplicate) { Text(stringResource(R.string.cal_duplicate)) }
                }
            }
            s.original?.let { o ->
                val created = o.created?.let { Format.parse(it) }?.toLocalDate()
                val changed = o.lastModified?.let { Format.parse(it) }?.toLocalDate()
                if (created != null || changed != null) Text(listOfNotNull(created?.let { AppText.get(R.string.cal_task_created, ReminderPlan.dayWord(it, today)) },
                    changed?.let { AppText.get(R.string.cal_task_changed, ReminderPlan.dayWord(it, today)) }).joinToString(" · "),
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(start = 56.dp, top = 12.dp))
            }
        }
    }
    // ACT task.reschedule @android.editor: the when picker in place of the date and time dialogs; for a task that has a due,
    // the reschedule presets come first. Save sends the new due (PATCH /tasks/{uid}).
    if (pickWhen) {
        val z = ZoneId.systemDefault()
        val now = AppClock.now(z)
        val current = s.dueDate?.atTime(s.dueTime ?: LocalTime.of(morningHour, 0))?.atZone(z)
        WhenDialog(if (s.original?.due != null) stringResource(R.string.cal_qa_reschedule) else stringResource(R.string.rem_due), current ?: now.plusDays(1).withHour(morningHour).withMinute(0),
            if (s.original?.due != null) QuickChoices.taskReschedule(now, current, s.dueTime == null, morningHour) else emptyList(),
            allowNoTime = true, initiallyDateOnly = s.dueDate != null && s.dueTime == null,
            onDismiss = { pickWhen = false }, onOtherDate = { pickWhen = false; pickDate = true }) { at, dateOnly ->
            s.edit {
                setDate(at.toLocalDate())
                setTime(if (dateOnly) null else at.toLocalTime())
            }
            pickWhen = false
        }
    }
    if (pickDate) DateDialog(s.dueDate ?: today, { pickDate = false }) { d -> s.edit { setDate(d) }; pickDate = false }
    if (pickStart) {
        val z = ZoneId.systemDefault()
        val init = (s.startDate ?: today.plusDays(1)).atTime(s.startTime ?: LocalTime.of(morningHour, 0)).atZone(z)
        WhenDialog(stringResource(R.string.cal_starts), init, emptyList(), allowNoTime = true, initiallyDateOnly = s.startTime == null,
            onDismiss = { pickStart = false }, onOtherDate = null) { at, dateOnly ->
            s.edit { setStart(at.toLocalDate(), if (dateOnly) null else at.toLocalTime()) }
            pickStart = false
        }
    }
    if (pickRepeat) RecurrenceDialog(s.dueDate ?: today, s.recurrence, onDismiss = { pickRepeat = false }) { r ->
        s.edit {
            recurrence = r
            if (r != null && dueDate == null) setDate(today)
        }
        pickRepeat = false
    }
    if (pickReminderAt && s.dueDate != null) {
        val z = ZoneId.systemDefault()
        val due = s.dueDate!!.atTime(s.dueTime ?: LocalTime.of(morningHour, 0)).atZone(z)
        WhenDialog(stringResource(R.string.cal_task_remind_me_at), due.minusHours(1), emptyList(), allowNoTime = false, initiallyDateOnly = false,
            onDismiss = { pickReminderAt = false }, onOtherDate = null) { at, _ ->
            s.edit { addReminderAt(at.toLocalDateTime()) }
            pickReminderAt = false
        }
    }
}

private fun dueText(s: TaskEditorState): String {
    val d = s.dueDate ?: return AppText.get(R.string.rem_no_due_date)
    val day = ReminderPlan.dayWord(d, AppClock.today()).replaceFirstChar { it.uppercase() }
    val t = s.dueTime ?: return AppText.get(R.string.rem_due_date, day)
    return AppText.get(R.string.rem_due_day_comma_time, day, t.format(DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).withLocale(Locale.getDefault())))
}

/** [v] with new text from elsewhere (live editing): the selection stays, cut to the new length. */
private fun withText(v: TextFieldValue, text: String): TextFieldValue =
    if (v.text == text) v else TextFieldValue(text, TextRange(v.selection.start.coerceAtMost(text.length), v.selection.end.coerceAtMost(text.length)))
