package io.github.murtazanasir.mailr.notes.ui

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.notes.R
import io.github.murtazanasir.mailr.i18n.AppText
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectDragGesturesAfterLongPress
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.Redo
import androidx.compose.material.icons.automirrored.filled.Undo
import androidx.compose.material.icons.automirrored.outlined.Label
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.PushPin
import androidx.compose.material.icons.outlined.Archive
import androidx.compose.material.icons.outlined.DragIndicator
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.outlined.NotificationAdd
import androidx.compose.material.icons.outlined.Palette
import androidx.compose.material.icons.outlined.PersonAdd
import androidx.compose.material.icons.outlined.PushPin
import androidx.compose.material.icons.outlined.Unarchive
import io.github.murtazanasir.mailr.look.AlertDialog
import io.github.murtazanasir.mailr.look.AssistChip
import androidx.compose.material3.BottomAppBar
import io.github.murtazanasir.mailr.look.Checkbox
import io.github.murtazanasir.mailr.look.DropdownMenu
import io.github.murtazanasir.mailr.look.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import io.github.murtazanasir.mailr.look.InputChip
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.OutlinedButton
import io.github.murtazanasir.mailr.look.OutlinedTextField
import androidx.compose.material3.PlainTooltip
import androidx.compose.material3.TooltipBox
import androidx.compose.material3.TooltipDefaults
import androidx.compose.material3.rememberTooltipState
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.SwipeToDismissBox
import androidx.compose.material3.SwipeToDismissBoxValue
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import io.github.murtazanasir.mailr.look.TextField
import androidx.compose.material3.TextFieldDefaults
import io.github.murtazanasir.mailr.look.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.rememberSwipeToDismissBoxState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.snapshotFlow
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.isAltPressed
import androidx.compose.ui.input.key.isCtrlPressed
import androidx.compose.ui.input.key.isMetaPressed
import androidx.compose.ui.input.key.isShiftPressed
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.CustomAccessibilityAction
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import io.github.murtazanasir.mailr.notes.data.Checklist
import io.github.murtazanasir.mailr.notes.data.Item
import io.github.murtazanasir.mailr.notes.data.Note
import io.github.murtazanasir.mailr.notes.data.NotesRepository
import io.github.murtazanasir.mailr.notes.data.RepeatRule
import io.github.murtazanasir.mailr.notes.data.Section
import io.github.murtazanasir.mailr.notes.data.TopGroup
import io.github.murtazanasir.mailr.notes.data.Usual
import io.github.murtazanasir.mailr.notes.data.notesMessage
import kotlinx.coroutines.delay
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/**
 * The note editor in the mailr app: full screen; a checklist checks with one
 * tap; the add line adds with the keyboard's Enter; the usual strip under it; the toolbar at the
 * bottom (colour, labels, pin, archive, share, ⋮). It autosaves 1.5 s after the last change and on
 * leaving. A view-only note, or one in Trash, opens read-only.
 */
@Composable
fun NoteEditor(vm: NotesViewModel, id: String, focusItem: String?, host: NotesHostCallbacks) {
    val repo = vm.repo
    val flow = remember(id) { repo.note(id) }
    val state by flow.collectAsStateWithLifecycle(null)
    val labels by repo.labels.collectAsStateWithLifecycle(emptyList())
    var loaded by remember(id) { mutableStateOf(false) }
    LaunchedEffect(id) {
        // Opened from a citation or a link the phone has not synced yet: read it from the server.
        if (repo.noteNow(id) == null) repo.refreshNote(id)
        loaded = true
        repo.markSeen(id)
    }
    DisposableEffect(id) { onDispose { vm.run { repo.checkpoint(id) } } }
    val s = state
    if (s == null) {
        Scaffold(topBar = { TopAppBar(title = {}, navigationIcon = { BackButton(vm) }) }) { pad ->
            Box(Modifier.padding(pad).fillMaxSize(), contentAlignment = Alignment.Center) {
                Text(if (loaded) stringResource(R.string.notes_note_gone) else stringResource(R.string.notes_loading), color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        return
    }
    val note = s.note
    val readOnly = !note.canEdit || note.deletedAt != null
    var colour by remember { mutableStateOf(false) }
    var labelPick by remember { mutableStateOf(false) }
    var share by remember { mutableStateOf(false) }
    var history by remember { mutableStateOf(false) }
    var more by remember { mutableStateOf(false) }
    var confirmDelete by remember { mutableStateOf(false) }
    var remind by remember { mutableStateOf(false) }
    var repeatOpen by remember { mutableStateOf(false) }
    PresenceEffect(repo, "note", id, note.shared && note.deletedAt == null)
    val bg = NoteColors.background(note.color)
    val fg = NoteColors.content(note.color)
    val hist = vm.history(id)
    // The text steps outlive the process (a rotation keeps the whole history in the ViewModel).
    var savedHistory by rememberSaveable(id) { mutableStateOf<String?>(null) }
    LaunchedEffect(hist) {
        if (!hist.history.canUndo && !hist.history.canRedo) savedHistory?.let { hist.restore(it) }
        snapshotFlow { hist.version }.collect { savedHistory = hist.saved() }
    }
    val draft = remember { DraftFocus() }
    val canHistory = note.canEdit && note.deletedAt == null

    CompositionLocalProvider(LocalDraftFocus provides draft) {
    Scaffold(
        containerColor = bg,
        contentColor = fg,
        // A hardware keyboard: Ctrl+Z, Ctrl+Shift+Z and Ctrl+Y use the editor's history; a line with
        // unsaved typing (an item being edited, the add line) keeps the field's own undo.
        modifier = Modifier.onPreviewKeyEvent { e ->
            if (!canHistory || e.type != KeyEventType.KeyDown || !(e.isCtrlPressed || e.isMetaPressed) || e.isAltPressed) return@onPreviewKeyEvent false
            val redo = (e.key == Key.Z && e.isShiftPressed) || (e.key == Key.Y && !e.isShiftPressed)
            val undo = e.key == Key.Z && !e.isShiftPressed
            when {
                !undo && !redo -> false
                draft.native -> false
                redo -> { vm.redoEdit(id); true }
                else -> { vm.undoEdit(id); true }
            }
        },
        topBar = {
            TopAppBar(colors = TopAppBarDefaults.topAppBarColors(containerColor = bg, titleContentColor = fg,
                navigationIconContentColor = fg, actionIconContentColor = fg), modifier = Modifier.testTag("note-top-bar"),
                navigationIcon = { BackButton(vm) },
                title = {
                    val editing = repo.editing.collectAsState().value[id]
                    val who = editing?.takeIf { vm.now() - it.second < 10_000 }?.first
                    Text(who?.let { stringResource(R.string.notes_is_editing, it) } ?: editedLabel(note.updatedAt, vm.now()).orEmpty(),
                        style = MaterialTheme.typography.labelLarge)
                },
                actions = {
                    if (canHistory) UndoRedoButtons(hist.canUndo, hist.canRedo, onUndo = { vm.undoEdit(id) }, onRedo = { vm.redoEdit(id) })
                    if (note.deletedAt == null) {
                        // ACT note.pin @android.notes @android.notesapp
                        IconButton(onClick = {
                            vm.actIn(id) { repo.setState(id, pinned = !note.state.pinned, label = AppText.get(if (note.state.pinned) R.string.notes_op_unpinned else R.string.notes_op_pinned)) }
                        }) { Icon(if (note.state.pinned) Icons.Filled.PushPin else Icons.Outlined.PushPin, stringResource(if (note.state.pinned) R.string.notes_unpin else R.string.notes_pin)) }
                        // ACT note.archive @android.notes @android.notesapp
                        IconButton(onClick = {
                            vm.actIn(id) { repo.setState(id, archived = !note.state.archived, label = AppText.get(if (note.state.archived) R.string.notes_op_unarchived else R.string.notes_op_archived)) }
                        }) {
                            Icon(if (note.state.archived) Icons.Outlined.Unarchive else Icons.Outlined.Archive,
                                stringResource(if (note.state.archived) R.string.notes_unarchive else R.string.notes_archive))
                        }
                    }
                })
        },
        bottomBar = {
            if (note.deletedAt == null) BottomAppBar(containerColor = bg, contentColor = fg) {
                IconButton(onClick = { colour = true }, enabled = note.canEdit) { Icon(Icons.Outlined.Palette, stringResource(R.string.notes_colour)) }
                IconButton(onClick = { labelPick = true }) { Icon(Icons.AutoMirrored.Outlined.Label, stringResource(R.string.notes_labels)) }
                IconButton(onClick = { share = true }) { Icon(Icons.Outlined.PersonAdd, stringResource(R.string.notes_share)) }
                // ACT note.remind @android.notes @android.notesapp: the viewer's own reminders (any level, as pins).
                IconButton(onClick = { remind = true }) {
                    Icon(if (note.reminders.isEmpty()) Icons.Outlined.NotificationAdd else Icons.Filled.Notifications, stringResource(R.string.notes_remind_me))
                }
                Spacer(Modifier.weight(1f))
                Box {
                    IconButton(onClick = { more = true }) { Icon(Icons.Outlined.MoreVert, stringResource(R.string.notes_more_options)) }
                    NoteMenu(vm, note, more, { more = false }, host, onHistory = { history = true }, onDelete = { confirmDelete = true },
                        onShare = { share = true }, onRepeat = { repeatOpen = true })
                }
            }
        },
    ) { pad ->
        Column(Modifier.padding(pad).fillMaxSize().imePadding()) {
            Banners(vm, s, readOnly)
            if (note.reminders.isNotEmpty()) Row(Modifier.horizontalScroll(rememberScrollState()).padding(horizontal = 12.dp)) {
                note.reminders.forEach { r ->
                    InputChip(selected = false, onClick = { remind = true }, label = { Text("🔔 " + reminderLabel(r.at)) },
                        modifier = Modifier.padding(end = 6.dp).testTag("reminder-chip"))
                }
            }
            if (note.isList) ChecklistBody(vm, s, readOnly, focusItem, host, fg,
                onRepeat = if (note.canEdit && !readOnly) ({ repeatOpen = true }) else null)
            else TextBody(vm, note, readOnly, fg, host)
        }
    }
    }
    if (colour) ColorDialog(note.color, onPick = { c ->
        colour = false
        // ACT note.color @android.notes @android.notesapp
        vm.actIn(id) { repo.editNote(id, color = c, clearColor = c == null) }
    }, onDismiss = { colour = false })
    if (labelPick) LabelsDialog(labels, note.state.labels, onSave = { chosen, created ->
        labelPick = false
        vm.actIn(id) {
            // ACT notelabel.create @android.notes @android.notesapp ("Create 'name'" in the labels menu)
            created?.let { repo.createLabel(it) }
            // ACT note.label @android.notes @android.notesapp
            repo.setState(id, labels = chosen.distinct(), label = AppText.get(R.string.notes_op_labels_saved))
        }
    }, onDismiss = { labelPick = false })
    if (share) ShareSheet(vm, "note", id, onDismiss = { share = false })
    if (history) HistorySheet(vm, id, onDismiss = { history = false })
    if (remind) RemindDialog(vm, note, onDismiss = { remind = false })
    if (repeatOpen) RepeatDialog(vm, note, onDismiss = { repeatOpen = false })
    if (confirmDelete) AlertDialog(onDismissRequest = { confirmDelete = false },
        title = { Text(stringResource(R.string.notes_move_to_trash)) }, text = { Text(stringResource(R.string.notes_notes_in_trash_are_deleted_after_30_days)) },
        confirmButton = {
            TextButton(onClick = {
                confirmDelete = false
                // ACT note.delete @android.notes @android.notesapp
                vm.act { repo.trash(id) }
                vm.back()
            }) { Text(stringResource(R.string.notes_move_to_trash)) }
        },
        dismissButton = { TextButton(onClick = { confirmDelete = false }) { Text(stringResource(R.string.notes_cancel)) } })
}

/** Undo and Redo in the editor's top bar, each with its name for TalkBack and a tooltip on a long press (the task editor's too). */
@Composable
fun UndoRedoButtons(canUndo: Boolean, canRedo: Boolean, onUndo: () -> Unit, onRedo: () -> Unit) {
    val undo = stringResource(R.string.notes_undo)
    val redo = stringResource(R.string.notes_redo)
    TooltipBox(TooltipDefaults.rememberPlainTooltipPositionProvider(), tooltip = { PlainTooltip { Text(undo) } }, state = rememberTooltipState()) {
        IconButton(onClick = onUndo, enabled = canUndo, modifier = Modifier.testTag("editor-undo")) { Icon(Icons.AutoMirrored.Filled.Undo, undo) }
    }
    TooltipBox(TooltipDefaults.rememberPlainTooltipPositionProvider(), tooltip = { PlainTooltip { Text(redo) } }, state = rememberTooltipState()) {
        IconButton(onClick = onRedo, enabled = canRedo, modifier = Modifier.testTag("editor-redo")) { Icon(Icons.AutoMirrored.Filled.Redo, redo) }
    }
}

/** A field in the editor that holds typing not yet saved (an item being edited, the add line): Ctrl+Z
 *  there is the field's own. */
internal class DraftFocus {
    var native by mutableStateOf(false)
}

internal val LocalDraftFocus = staticCompositionLocalOf { DraftFocus() }

@Composable
internal fun BackButton(vm: NotesViewModel) {
    IconButton(onClick = { vm.back() }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, stringResource(R.string.notes_back)) }
}

@Composable
private fun Banners(vm: NotesViewModel, s: NotesRepository.NoteState, readOnly: Boolean) {
    val note = s.note
    val repo = vm.repo
    if (note.deletedAt != null) {
        Surface(color = MaterialTheme.colorScheme.surfaceVariant, modifier = Modifier.fillMaxWidth()) {
            Row(Modifier.padding(start = 16.dp, end = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(trashLabel(note.deletedAt, vm.now()), Modifier.weight(1f).padding(vertical = 12.dp))
                // ACT note.restore @android.notes @android.notesapp
                if (note.canDelete) TextButton(onClick = { vm.act { repo.restore(note.id, false) } }) { Text(stringResource(R.string.notes_restore)) }
            }
        }
    } else if (!note.canEdit) {
        val by = note.owner?.name?.takeIf { it.isNotBlank() } ?: note.owner?.let { if (it.kind == "group") it.name else null }
        Surface(color = MaterialTheme.colorScheme.surfaceVariant, modifier = Modifier.fillMaxWidth()) {
            Text(if (by != null) stringResource(R.string.notes_view_only_shared_by, by) else stringResource(R.string.notes_view_only), Modifier.padding(16.dp, 12.dp))
        }
    }
    // ACT note.presence @android.notes @android.notesapp: "Cleo is in this list" while her last beat is under 40 s old.
    val present by repo.presence.collectAsState()
    val names = present[note.id].orEmpty().filter { vm.now() - it.at < NotesRepository.PRESENCE_TTL_MS }.map { it.name }
    PresenceBanner(names, if (note.isList) "list" else "note")
    val offline by repo.offline.collectAsState()
    if (offline && note.deletedAt == null) {
        val ops by repo.ops.collectAsState(emptyList())
        OfflineLine(ops.count { it.parent == note.id && it.state == io.github.murtazanasir.mailr.notes.data.OpState.QUEUED })
    }
    s.failed?.let { op ->
        Surface(color = MaterialTheme.colorScheme.errorContainer, modifier = Modifier.fillMaxWidth()) {
            Row(Modifier.padding(start = 16.dp, end = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                val text = if (op.state == io.github.murtazanasir.mailr.notes.data.OpState.CONFLICT) stringResource(R.string.notes_note_conflict)
                else stringResource(R.string.notes_op_failed, op.label, op.error ?: stringResource(R.string.notes_not_saved))
                Text(text, Modifier.weight(1f).padding(vertical = 10.dp), color = MaterialTheme.colorScheme.onErrorContainer)
                if (op.state == io.github.murtazanasir.mailr.notes.data.OpState.CONFLICT) {
                    TextButton(onClick = { vm.run { repo.resolve(note.id, NotesRepository.Choice.THEIRS) } }) { Text(stringResource(R.string.notes_keep_theirs)) }
                    TextButton(onClick = { vm.run { repo.resolve(note.id, NotesRepository.Choice.MINE) } }) { Text(stringResource(R.string.notes_use_mine)) }
                } else {
                    TextButton(onClick = { vm.retry(op) }) { Text(stringResource(R.string.notes_try_again)) }
                    TextButton(onClick = { vm.discard(op) }) { Text(stringResource(R.string.notes_discard)) }
                }
            }
        }
    }
}

/** A text note: title and body, autosaved (ACT note.edit @android.notes @android.notesapp). */
@Composable
private fun TextBody(vm: NotesViewModel, note: Note, readOnly: Boolean, fg: Color, host: NotesHostCallbacks) {
    // The fields hold their selection too, so an undo or redo can put the cursor where the change was.
    var titleV by remember(note.id) { mutableStateOf(TextFieldValue(note.title)) }
    var bodyV by remember(note.id) { mutableStateOf(TextFieldValue(note.body)) }
    val title = titleV.text
    val body = bodyV.text
    var dirty by remember(note.id) { mutableStateOf(false) }
    val hist = vm.history(note.id)
    val titleFocus = remember { FocusRequester() }
    val bodyFocus = remember { FocusRequester() }
    // Someone else's change while this phone has nothing unsaved shows at once. One made as you
    // (Ambrose's rewrite, the web) is one step of the editor's Undo.
    LaunchedEffect(note.title, note.body) {
        if (!dirty) {
            if (host.meId != null && note.updatedBy?.id == host.meId) {
                hist.recordText("title", title, note.title, "insertReplacementText")
                hist.recordText("body", body, note.body, "insertReplacementText")
            }
            titleV = withText(titleV, note.title); bodyV = withText(bodyV, note.body)
        }
    }
    // The history reads and sets these fields (NoteHistory.kt); a set autosaves like typing, and the
    // field takes the focus with the cursor at the change.
    DisposableEffect(hist) {
        hist.reader = { f -> when (f) { "title" -> titleV.text; "body" -> bodyV.text; else -> null } }
        hist.writer = { f, v, caret ->
            if (f == "title") titleV = atCaret(v, caret) else bodyV = atCaret(v, caret)
            dirty = true
            catching { (if (f == "title") titleFocus else bodyFocus).requestFocus() }
        }
        onDispose { hist.reader = null; hist.writer = null }
    }
    LaunchedEffect(title, body, dirty) {
        if (!dirty) return@LaunchedEffect
        delay(1_500)
        save(vm, note, title, body)
        // An edit made while the save ran (typing, an undo) is not in it: the editor stays dirty, and
        // this effect runs again for that text. Clearing it here lost that edit.
        if (titleV.text == title && bodyV.text == body) dirty = false
    }
    DisposableEffect(note.id) {
        onDispose { if (dirty) vm.run { vm.repo.editNote(note.id, title = title, body = body) } }
    }
    Column(Modifier.fillMaxSize().padding(horizontal = 4.dp)) {
        PlainField(titleV, { v -> if (v.text != titleV.text) { recordEdit(vm, hist, "title", titleV.text, v.text); dirty = true }; titleV = v },
            stringResource(R.string.notes_title), readOnly, MaterialTheme.typography.titleLarge, fg,
            Modifier.focusRequester(titleFocus).testTag("note-title").onFocusChanged { if (!it.isFocused) hist.seal() })
        PlainField(bodyV, { v -> if (v.text != bodyV.text) { recordEdit(vm, hist, "body", bodyV.text, v.text); dirty = true }; bodyV = v },
            stringResource(R.string.notes_note), readOnly, MaterialTheme.typography.bodyLarge, fg,
            Modifier.weight(1f).focusRequester(bodyFocus).testTag("note-body").onFocusChanged { if (!it.isFocused) hist.seal() },
            singleLine = false)
        LinksRow(vm, note, host)
    }
}

/** A typed change becomes a step of the editor's history; a big removal says so, with Undo. */
private fun recordEdit(vm: NotesViewModel, hist: NoteHistory, field: String, before: String, after: String) {
    val r = hist.recordText(field, before, after) ?: return
    if (r.big) vm.say(AppText.get(R.string.notes_text_removed)) { hist.undoStep(r.step) { vm.say(it) } }
}

private suspend fun save(vm: NotesViewModel, note: Note, title: String, body: String) {
    catching {
        // Compared with the phone's copy now. The composed note can be older (Room's update is still on its
        // way after the last save), and text that matched the older copy was left out of the save.
        val cur = vm.repo.noteNow(note.id) ?: note
        vm.repo.editNote(note.id, title = title.takeIf { it != cur.title }, body = body.takeIf { it != cur.body })
    }
}

/** [v] with new text (a change from elsewhere): the selection stays, cut to the new length. */
internal fun withText(v: TextFieldValue, text: String): TextFieldValue =
    if (v.text == text) v else TextFieldValue(text, TextRange(v.selection.start.coerceAtMost(text.length), v.selection.end.coerceAtMost(text.length)))

/** [text] with the cursor at [caret] (an undo or redo: where the change was). */
internal fun atCaret(text: String, caret: Int): TextFieldValue = TextFieldValue(text, TextRange(caret.coerceIn(0, text.length)))

@Composable
internal fun PlainField(value: TextFieldValue, onChange: (TextFieldValue) -> Unit, placeholder: String, readOnly: Boolean, style: TextStyle,
                        fg: Color, modifier: Modifier = Modifier, singleLine: Boolean = true,
                        ime: ImeAction = ImeAction.Default, onIme: (() -> Unit)? = null) {
    TextField(value, onChange, readOnly = readOnly, placeholder = { Text(placeholder, style = style) }, textStyle = style.copy(color = fg),
        singleLine = singleLine, modifier = modifier.fillMaxWidth(),
        keyboardOptions = KeyboardOptions(imeAction = ime),
        keyboardActions = KeyboardActions(onAny = { onIme?.invoke() }),
        colors = TextFieldDefaults.colors(focusedContainerColor = Color.Transparent, unfocusedContainerColor = Color.Transparent,
            disabledContainerColor = Color.Transparent, focusedIndicatorColor = Color.Transparent,
            unfocusedIndicatorColor = Color.Transparent))
}

/** Link chips (a conversation, an event, a task, a recording); a tap opens it in the app. */
@Composable
private fun LinksRow(vm: NotesViewModel, note: Note, host: NotesHostCallbacks? = null) {
    if (note.links.isEmpty()) return
    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), modifier = Modifier.padding(12.dp)) {
        note.links.forEach { l ->
            val icon = when (l.targetKind) { "conversation" -> "✉"; "event" -> "📅"; "task" -> "☐"; "recording" -> "🎙"; else -> "🔗" }
            val chip: @Composable (trailing: (@Composable () -> Unit)?) -> Unit = { trailing ->
                InputChip(selected = false, onClick = { host?.openLink?.invoke(l) }, label = { Text("$icon ${l.title ?: l.targetKind}", maxLines = 1,
                    overflow = TextOverflow.Ellipsis) }, trailingIcon = trailing)
            }
            // ACT note.unlink @android.notes @android.notesapp: × on the chip, with the undo.
            if (note.canEdit) io.github.murtazanasir.mailr.look.ChipWithClose(stringResource(R.string.notes_remove_link, l.title ?: l.targetKind),
                onClose = { vm.actIn(note.id) { vm.repo.unlink(note.id, l.id) } }) { x -> chip(x) }
            else chip(null)
        }
    }
}

// ================================================================ the checklist

private sealed interface Line {
    val key: String
    data class Header(val group: Checklist.Group) : Line { override val key get() = "s-" + (group.section?.id ?: "top") }
    data class ItemLine(val item: Item, val pending: Boolean, val conflict: JsonObject?, val sectionId: String?) : Line {
        override val key get() = "i-" + item.id
    }
    data class Add(val sectionId: String?) : Line { override val key get() = "a-" + (sectionId ?: "top") }
    data class Fold(val count: Int) : Line { override val key get() = "fold" }
    data class Checked(val item: Item, val pending: Boolean) : Line { override val key get() = "c-" + item.id }
}

@Composable
private fun ChecklistBody(vm: NotesViewModel, s: NotesRepository.NoteState, readOnly: Boolean, focusItem: String?,
                          host: NotesHostCallbacks, fg: Color, onRepeat: (() -> Unit)?) {
    val note = s.note
    val repo = vm.repo
    val top = note.top ?: TopGroup(note.topOrderMode)
    val layout = remember(s) { Checklist.layout(s.items.map { it.item }, s.sections, top, note.checkedAtBottom) }
    val byId = remember(s) { s.items.associateBy { it.item.id } }
    var foldOpen by remember(note.id) { mutableStateOf(false) }
    var usual by remember(note.id) { mutableStateOf<Usual?>(null) }
    var focusedAdd by remember(note.id) { mutableStateOf<String?>(null) }
    LaunchedEffect(note.id, note.suggestUsual, s.items.size) {
        if (note.suggestUsual && !readOnly) usual = repo.usual(note.id)
    }
    val lines = buildList<Line> {
        for (g in layout.groups) {
            val sid = g.section?.id
            if (g.section != null) add(Line.Header(g))
            if (g.section != null && g.collapsed) continue
            g.items.forEach { i -> byId[i.id]?.let { add(Line.ItemLine(i, it.pending, it.conflict, sid)) } }
            if (!readOnly) add(Line.Add(sid))
        }
        if (layout.checked.isNotEmpty()) {
            add(Line.Fold(layout.checked.size))
            if (foldOpen) layout.checked.forEach { i -> add(Line.Checked(i, byId[i.id]?.pending == true)) }
        }
    }
    val ticks by repo.ticks.collectAsState()
    val list = rememberLazyListState()
    LaunchedEffect(focusItem, lines.size) {
        val i = focusItem?.let { f -> lines.indexOfFirst { it.key == "i-$f" || it.key == "c-$f" } } ?: -1
        if (i >= 0) list.scrollToItem(i + 1)
    }
    // Drag to move (long-press, then drag): the line under the finger when it lifts is the target.
    var dragging by remember { mutableStateOf<String?>(null) }
    var dragY by remember { mutableStateOf(0f) }
    val haptic = LocalHapticFeedback.current
    var titleV by remember(note.id) { mutableStateOf(TextFieldValue(note.title)) }
    val title = titleV.text
    var titleDirty by remember(note.id) { mutableStateOf(false) }
    val hist = vm.history(note.id)
    val titleFocus = remember { FocusRequester() }
    val titleScope = rememberCoroutineScope()
    LaunchedEffect(note.title) {
        if (!titleDirty) {
            if (host.meId != null && note.updatedBy?.id == host.meId) hist.recordText("title", titleV.text, note.title, "insertReplacementText")
            titleV = withText(titleV, note.title)
        }
    }
    // An undo or redo of the title puts the cursor where the change was (the list scrolls up to it).
    DisposableEffect(hist) {
        hist.reader = { f -> if (f == "title") titleV.text else null }
        hist.writer = { f, v, caret ->
            if (f == "title") {
                titleV = atCaret(v, caret); titleDirty = true
                titleScope.launch {
                    list.scrollToItem(0)
                    catching { titleFocus.requestFocus() }
                }
            }
        }
        onDispose { hist.reader = null; hist.writer = null }
    }
    LaunchedEffect(title, titleDirty) {
        if (!titleDirty) return@LaunchedEffect
        delay(1_500)
        // ACT note.edit @android.notes @android.notesapp (a list's title)
        catching { repo.editNote(note.id, title = title) }
        // A title typed while the save ran stays dirty and is saved next (see TextBody).
        if (titleV.text == title) titleDirty = false
    }
    LazyColumn(state = list, contentPadding = PaddingValues(bottom = 48.dp), modifier = Modifier.fillMaxSize().testTag("checklist")) {
        item(key = "title") {
            PlainField(titleV, { v -> if (v.text != titleV.text) { recordEdit(vm, hist, "title", titleV.text, v.text); titleDirty = true }; titleV = v },
                stringResource(R.string.notes_title), readOnly, MaterialTheme.typography.titleLarge, fg,
                Modifier.focusRequester(titleFocus).testTag("note-title").onFocusChanged { if (!it.isFocused) hist.seal() })
        }
        if (note.repeat != null) item(key = "repeat") { RepeatLine(note, fg, onRepeat) }
        lines.forEach { line ->
            item(key = line.key) {
                val lifted = dragging != null && line is Line.ItemLine && line.item.id == dragging
                val mod = Modifier.graphicsLayer { if (lifted) { translationY = dragY; shadowElevation = 8f } }
                when (line) {
                    is Line.Header -> SectionHeader(vm, note, line.group, readOnly, s.sections, mod)
                    is Line.ItemLine -> ItemRow(vm, note, line.item, line.pending, line.conflict, readOnly, s.sections, host, fg,
                        focused = line.item.id == focusItem, tickBy = ticks[line.item.id]?.first,
                        dragModifier = if (readOnly) Modifier else Modifier.pointerInput(line.item.id) {
                            detectDragGesturesAfterLongPress(
                                onDragStart = { haptic.performHapticFeedback(HapticFeedbackType.LongPress); dragging = line.item.id; dragY = 0f },
                                onDragEnd = {
                                    val info = list.layoutInfo.visibleItemsInfo
                                    val me = info.firstOrNull { it.key == line.key }
                                    if (me != null) {
                                        val center = me.offset + me.size / 2 + dragY
                                        val target = info.filter { it.key != line.key }.minByOrNull { kotlin.math.abs(it.offset + it.size / 2 - center) }
                                        val tk = target?.key as? String
                                        val down = dragY > 0
                                        when {
                                            tk == null -> Unit
                                            tk.startsWith("i-") -> vm.actIn(note.id) {
                                                // ACT checkitem.move @android.notes @android.notesapp: long-press, then drag
                                                if (down) repo.moveItem(line.item.id, after = tk.removePrefix("i-"))
                                                else repo.moveItem(line.item.id, before = tk.removePrefix("i-"))
                                            }
                                            tk.startsWith("s-") || tk.startsWith("a-") -> {
                                                val sid = tk.substring(2).takeIf { it != "top" }
                                                vm.actIn(note.id) { repo.moveItem(line.item.id, sectionId = sid, toSection = true) }
                                            }
                                        }
                                    }
                                    dragging = null; dragY = 0f
                                },
                                onDragCancel = { dragging = null; dragY = 0f },
                                onDrag = { change, amount -> change.consume(); dragY += amount.y },
                            )
                        }, modifier = mod)
                    is Line.Add -> {
                        Column {
                            AddLine(vm, note, line.sectionId, onFocus = { focusedAdd = if (it) line.key else null })
                            val strip = usual
                            if (line.sectionId == null && strip != null && strip.enabled && strip.items.isNotEmpty() &&
                                (focusedAdd == line.key || s.items.isEmpty())) UsualStrip(vm, note.id, strip) { usual = it }
                        }
                    }
                    is Line.Fold -> FoldHeader(vm, note, line.count, foldOpen, readOnly,
                        // A check by someone else, hidden in the closed fold: her name and the item on the fold's line.
                        tick = if (foldOpen) null else layout.checked.firstNotNullOfOrNull { i -> ticks[i.id]?.let { AppText.get(R.string.notes_tick_on_fold, it.first, i.text) } }) { foldOpen = !foldOpen }
                    is Line.Checked -> CheckedRow(vm, note.id, line.item, line.pending, readOnly, s.sections, fg, tickBy = ticks[line.item.id]?.first)
                }
            }
        }
        item(key = "links") { LinksRow(vm, note, host) }
    }
}

@Composable
private fun SectionHeader(vm: NotesViewModel, note: Note, g: Checklist.Group, readOnly: Boolean, all: List<Section>, modifier: Modifier) {
    val sec = g.section ?: return
    val repo = vm.repo
    var menu by remember { mutableStateOf(false) }
    var rename by remember { mutableStateOf(false) }
    var delete by remember { mutableStateOf(false) }
    Row(verticalAlignment = Alignment.CenterVertically, modifier = modifier.fillMaxWidth().heightIn(min = 48.dp)
        // ACT checksection.order_mode @android.notes @android.notesapp, ACT checksection.rename @android.notes @android.notesapp: tap collapses (per viewer); the menu holds the rest.
        .clickable(onClickLabel = stringResource(if (g.collapsed) R.string.notes_expand else R.string.notes_collapse)) { vm.act { repo.editSection(note.id, sec.id, collapsed = !g.collapsed) } }
        .padding(start = 16.dp)
        .semantics { heading(); stateDescription = AppText.get(if (g.collapsed) R.string.notes_collapsed else R.string.notes_expanded) }) {
        Icon(if (g.collapsed) Icons.Filled.ExpandMore else Icons.Filled.ExpandLess, null, Modifier.size(20.dp))
        Spacer(Modifier.width(8.dp))
        Text(sec.name.uppercase() + if (g.collapsed) " (${g.items.size})" else "", style = MaterialTheme.typography.labelLarge,
            modifier = Modifier.weight(1f))
        Text(if (sec.orderMode == "learned") stringResource(R.string.notes_section_learned) else "", style = MaterialTheme.typography.labelSmall)
        if (!readOnly) Box {
            IconButton(onClick = { menu = true }) { Icon(Icons.Outlined.MoreVert, stringResource(R.string.notes_section_options, sec.name)) }
            DropdownMenu(menu, { menu = false }) {
                DropdownMenuItem({ Text(stringResource(R.string.notes_rename)) }, { menu = false; rename = true })
                DropdownMenuItem({ Text(if (sec.orderMode == "learned") pluralStringResource(R.plurals.notes_order_learned_trips, sec.trips, sec.trips) else stringResource(R.string.notes_order_learned)) },
                    { menu = false; vm.actIn(note.id) { repo.editSection(note.id, sec.id, orderMode = "learned") } })
                DropdownMenuItem({ Text(stringResource(R.string.notes_order_manual)) }, { menu = false; vm.actIn(note.id) { repo.editSection(note.id, sec.id, orderMode = "manual") } })
                if (sec.orderMode == "learned") DropdownMenuItem({ Text(stringResource(R.string.notes_reset_learned_order)) },
                    { menu = false; vm.actIn(note.id) { repo.editSection(note.id, sec.id, resetLearned = true) } })
                // ACT checksection.reorder @android.notes @android.notesapp
                DropdownMenuItem({ Text(stringResource(R.string.notes_move_up)) }, { menu = false; vm.actIn(note.id) { repo.editSection(note.id, sec.id, direction = "up") } })
                DropdownMenuItem({ Text(stringResource(R.string.notes_move_down)) }, { menu = false; vm.actIn(note.id) { repo.editSection(note.id, sec.id, direction = "down") } })
                DropdownMenuItem({ Text(stringResource(R.string.notes_delete)) }, { menu = false; delete = true })
            }
        }
    }
    if (rename) TextDialog(stringResource(R.string.notes_rename_section), sec.name, stringResource(R.string.notes_rename), onDismiss = { rename = false }) { v ->
        rename = false
        if (v.isNotBlank() && v != sec.name) vm.actIn(note.id) { repo.editSection(note.id, sec.id, name = v.trim()) }
    }
    if (delete) AlertDialog(onDismissRequest = { delete = false }, title = { Text(stringResource(R.string.notes_delete_section_title, sec.name)) },
        text = { Text(stringResource(R.string.notes_its_items_can_stay_on_the_list_in_the_top_group)) },
        confirmButton = {
            // ACT checksection.delete @android.notes @android.notesapp: "Keep the items" (the default) or "Delete them too".
            TextButton(onClick = { delete = false; vm.actIn(note.id) { repo.deleteSection(note.id, sec.id, keepItems = true) } }) { Text(stringResource(R.string.notes_keep_the_items)) }
        },
        dismissButton = {
            TextButton(onClick = { delete = false; vm.actIn(note.id) { repo.deleteSection(note.id, sec.id, keepItems = false) } }) { Text(stringResource(R.string.notes_delete_them_too)) }
        })
}

@Composable
private fun ItemRow(vm: NotesViewModel, note: Note, item: Item, pending: Boolean, conflict: JsonObject?, readOnly: Boolean,
                    sections: List<Section>, host: NotesHostCallbacks, fg: Color, focused: Boolean, tickBy: String?,
                    dragModifier: Modifier, modifier: Modifier) {
    val repo = vm.repo
    val haptic = LocalHapticFeedback.current
    var editing by remember(item.id) { mutableStateOf(false) }
    var menu by remember { mutableStateOf(false) }
    var qtyEdit by remember { mutableStateOf(false) }
    var extraEdit by remember { mutableStateOf(false) }
    var moveTo by remember { mutableStateOf(false) }
    val swipe = rememberSwipeToDismissBoxState(confirmValueChange = { v ->
        when (v) {
            // ACT checkitem.check @android.notes @android.notesapp: swipe right checks.
            SwipeToDismissBoxValue.StartToEnd -> vm.actIn(note.id) { repo.check(item.id, true) }
            // ACT checkitem.delete @android.notes @android.notesapp: swipe left deletes, with undo.
            SwipeToDismissBoxValue.EndToStart -> vm.actIn(note.id) { repo.deleteItem(item.id) }
            SwipeToDismissBoxValue.Settled -> Unit
        }
        false
    })
    val actions = if (readOnly) emptyList() else listOf(
        CustomAccessibilityAction(stringResource(R.string.notes_check)) { vm.actIn(note.id) { repo.check(item.id, true) }; true },
        CustomAccessibilityAction(stringResource(R.string.notes_move_up)) { vm.actIn(note.id) { repo.moveItem(item.id, direction = "up") }; true },
        CustomAccessibilityAction(stringResource(R.string.notes_move_down)) { vm.actIn(note.id) { repo.moveItem(item.id, direction = "down") }; true },
        CustomAccessibilityAction(stringResource(R.string.notes_move_to_section)) { moveTo = true; true },
        CustomAccessibilityAction(stringResource(R.string.notes_delete)) { vm.actIn(note.id) { repo.deleteItem(item.id) }; true },
        CustomAccessibilityAction(stringResource(R.string.notes_edit_quantity)) { qtyEdit = true; true },
    )
    SwipeToDismissBox(swipe, enableDismissFromStartToEnd = !readOnly, enableDismissFromEndToStart = !readOnly, modifier = modifier,
        backgroundContent = {
            if (swipe.dismissDirection == SwipeToDismissBoxValue.Settled) return@SwipeToDismissBox
            val toRight = swipe.dismissDirection == SwipeToDismissBoxValue.StartToEnd
            Box(Modifier.fillMaxSize().background(if (toRight) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.errorContainer)
                .padding(horizontal = 24.dp), contentAlignment = if (toRight) Alignment.CenterStart else Alignment.CenterEnd) {
                Text(stringResource(if (toRight) R.string.notes_check else R.string.notes_delete))
            }
        }) {
        Column(Modifier.fillMaxWidth().background(if (focused) MaterialTheme.colorScheme.secondaryContainer.copy(alpha = 0.5f) else NoteColors.background(note.color))
            .pendingEdge(pending, MaterialTheme.colorScheme.primary, 8f)
            .semantics { customActions = actions }
            .testTag("item-" + item.id)) {
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)) {
                if (!readOnly) Icon(Icons.Outlined.DragIndicator, null, Modifier.then(dragModifier).padding(start = 4.dp).size(20.dp),
                    tint = fg.copy(alpha = 0.5f))
                Checkbox(item.checked, { c ->
                    haptic.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                    // ACT checkitem.check @android.notes @android.notesapp, ACT checkitem.uncheck @android.notes @android.notesapp: one tap, with a haptic tick.
                    vm.actIn(note.id) { repo.check(item.id, c) }
                }, enabled = !readOnly, modifier = Modifier.size(48.dp).semantics { contentDescription = item.text })
                item.qty?.chip?.let { c ->
                    Tag(c, Modifier.clickable(enabled = !readOnly, onClickLabel = stringResource(R.string.notes_edit_quantity)) { qtyEdit = true }.padding(end = 6.dp))
                }
                tickBy?.let { TickChip(it, Modifier.padding(end = 6.dp)) }
                Column(Modifier.weight(1f)) {
                    if (editing) {
                        var text by remember { mutableStateOf(item.text) }
                        val fr = remember { FocusRequester() }
                        var hadFocus by remember { mutableStateOf(false) }
                        val draft = LocalDraftFocus.current
                        SideEffect { if (hadFocus && editing) draft.native = text != item.text }
                        DisposableEffect(Unit) { onDispose { draft.native = false } }
                        LaunchedEffect(Unit) { fr.requestFocus() }
                        val done = {
                            editing = false
                            // ACT checkitem.edit @android.notes @android.notesapp: the text in edit mode (a typed x2 becomes the quantity).
                            if (text.isNotBlank() && text != item.text) vm.actIn(note.id) { repo.editItem(item.id, text = text) }
                        }
                        // The same box as the text below (no field padding), so a tap never moves the words; leaving the
                        // field saves and goes back to the text.
                        BasicTextField(text, { text = it }, singleLine = true,
                            textStyle = MaterialTheme.typography.bodyLarge.copy(color = fg),
                            cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
                            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
                            keyboardActions = KeyboardActions(onAny = { done() }),
                            modifier = Modifier.fillMaxWidth().testTag("item-edit").focusRequester(fr)
                                .onFocusChanged { f -> if (f.isFocused) hadFocus = true else if (hadFocus && editing) done() }
                                .padding(vertical = 8.dp))
                    } else {
                        Text(item.text, style = MaterialTheme.typography.bodyLarge,
                            textDecoration = if (item.checked) TextDecoration.LineThrough else null,
                            modifier = Modifier.fillMaxWidth().clickable(enabled = !readOnly, onClickLabel = stringResource(R.string.notes_edit)) { editing = true }
                                .padding(vertical = 8.dp))
                    }
                    item.extra?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = fg.copy(alpha = 0.7f)) }
                    val author = item.addedBy?.takeIf { it.id != null && it.id != host.meId && host.meId != null }?.name
                    if (author != null && note.shared) Text(author, style = MaterialTheme.typography.labelSmall, color = fg.copy(alpha = 0.7f))
                    if (item.taskUid != null) Text(stringResource(R.string.notes_task_badge), style = MaterialTheme.typography.labelSmall, color = fg.copy(alpha = 0.7f))
                }
                if (!readOnly) Box {
                    IconButton(onClick = { menu = true }) { Icon(Icons.Outlined.MoreVert, stringResource(R.string.notes_item_options, item.text)) }
                    DropdownMenu(menu, { menu = false }) {
                        DropdownMenuItem({ Text(stringResource(R.string.notes_edit_quantity)) }, { menu = false; qtyEdit = true })
                        DropdownMenuItem({ Text(stringResource(if (item.extra == null) R.string.notes_add_a_line else R.string.notes_edit_extra_line)) }, { menu = false; extraEdit = true })
                        DropdownMenuItem({ Text(stringResource(R.string.notes_move_up)) }, { menu = false; vm.actIn(note.id) { repo.moveItem(item.id, direction = "up") } })
                        DropdownMenuItem({ Text(stringResource(R.string.notes_move_down)) }, { menu = false; vm.actIn(note.id) { repo.moveItem(item.id, direction = "down") } })
                        if (sections.isNotEmpty()) DropdownMenuItem({ Text(stringResource(R.string.notes_move_to_section_ellipsis)) }, { menu = false; moveTo = true })
                        // ACT checkitem.to_task @android.notes @android.notesapp
                        if (item.taskUid == null) DropdownMenuItem({ Text(stringResource(R.string.notes_make_it_a_task)) }, { menu = false; vm.actIn(note.id) { repo.toTask(item.id) } })
                        DropdownMenuItem({ Text(stringResource(R.string.notes_delete)) }, { menu = false; vm.actIn(note.id) { repo.deleteItem(item.id) } })
                    }
                }
            }
            if (conflict != null) ConflictRow(vm, item, conflict)
        }
    }
    if (qtyEdit) QtyDialog(item, onDismiss = { qtyEdit = false }) { q, clear ->
        qtyEdit = false
        vm.actIn(note.id) { repo.editItem(item.id, qty = q, clearQty = clear) }
    }
    if (extraEdit) TextDialog(stringResource(R.string.notes_extra_line), item.extra.orEmpty(), stringResource(R.string.notes_save), onDismiss = { extraEdit = false }) { v ->
        extraEdit = false
        vm.actIn(note.id) { repo.editItem(item.id, extra = v) }
    }
    if (moveTo) AlertDialog(onDismissRequest = { moveTo = false }, title = { Text(stringResource(R.string.notes_move_to_section)) },
        confirmButton = { TextButton(onClick = { moveTo = false }) { Text(stringResource(R.string.notes_cancel)) } },
        text = {
            Column {
                (listOf<Section?>(null) + sections.sortedBy { it.order }).forEach { sec ->
                    Text(sec?.name ?: stringResource(R.string.notes_top_of_list), Modifier.fillMaxWidth().heightIn(min = 48.dp)
                        .clickable { moveTo = false; vm.actIn(note.id) { repo.moveItem(item.id, sectionId = sec?.id, toSection = true) } }
                        .padding(vertical = 14.dp))
                }
            }
        })
}

/** The conflict sheet on a row: Keep theirs, Use mine, Keep both. */
@Composable
private fun ConflictRow(vm: NotesViewModel, item: Item, conflict: JsonObject) {
    val theirs = catching { conflict["theirs"]!!.jsonObject["content"]!!.jsonObject["text"]!!.jsonPrimitive.contentOrNull }.getOrNull()
    Surface(color = MaterialTheme.colorScheme.errorContainer, shape = RoundedCornerShape(8.dp), modifier = Modifier.fillMaxWidth().padding(8.dp)) {
        Column(Modifier.padding(8.dp)) {
            Text(theirs?.let { stringResource(R.string.notes_changed_elsewhere_theirs, it) } ?: stringResource(R.string.notes_op_changed_elsewhere), color = MaterialTheme.colorScheme.onErrorContainer)
            Row(Modifier.horizontalScroll(rememberScrollState())) {
                TextButton(onClick = { vm.run { vm.repo.resolve(item.id, NotesRepository.Choice.THEIRS) } }) { Text(stringResource(R.string.notes_keep_theirs)) }
                TextButton(onClick = { vm.run { vm.repo.resolve(item.id, NotesRepository.Choice.MINE) } }) { Text(stringResource(R.string.notes_use_mine)) }
                TextButton(onClick = { vm.run { vm.repo.resolve(item.id, NotesRepository.Choice.BOTH) } }) { Text(stringResource(R.string.notes_keep_both)) }
            }
        }
    }
}

/** "+ Add item": Enter adds and keeps the line; several lines, or commas, add several items (the grammar). */
@Composable
private fun AddLine(vm: NotesViewModel, note: Note, sectionId: String?, onFocus: (Boolean) -> Unit) {
    var text by remember { mutableStateOf("") }
    val scope = rememberCoroutineScope()
    var focused by remember { mutableStateOf(false) }
    val draft = LocalDraftFocus.current
    SideEffect { if (focused) draft.native = text.isNotEmpty() }
    fun submit() {
        val t = text
        if (t.isBlank()) return
        text = ""
        scope.launch {
            try {
                // ACT checkitem.add @android.notes @android.notesapp, ACT note.add_items @android.notes @android.notesapp: one line, or several, through the grammar.
                val r = vm.repo.addItems(note.id, t, sectionId = sectionId) ?: return@launch
                val dup = r.duplicates.firstOrNull()
                if (dup != null) vm.say(AppText.get(R.string.notes_already_on_list, dup.replaceFirstChar { it.titlecase() }), undo = null)
                else {
                    // A step of the editor's Undo; Redo adds the same line again.
                    val hist = vm.history(note.id)
                    val step = hist.recordOp(r.handle.label, r.handle) { vm.repo.addItems(note.id, t, sectionId = sectionId)?.handle }
                    vm.say(r.handle.label) { hist.undoStep(step) { vm.say(it) } }
                }
            } catch (e: CancellationException) { throw e } catch (e: Exception) {
                vm.say(e.notesMessage())
            }
        }
    }
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(start = 12.dp)) {
        Icon(Icons.Filled.Add, null, Modifier.size(20.dp))
        TextField(text, { v ->
            // A paste of several lines adds them at once.
            if (v.contains('\n') && v.trim().contains('\n')) { text = v; submit() } else text = v.replace("\n", "")
        }, placeholder = { Text(stringResource(R.string.notes_add_item)) }, singleLine = true,
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
            keyboardActions = KeyboardActions(onDone = { submit() }),
            colors = TextFieldDefaults.colors(focusedContainerColor = Color.Transparent, unfocusedContainerColor = Color.Transparent,
                focusedIndicatorColor = Color.Transparent, unfocusedIndicatorColor = Color.Transparent),
            modifier = Modifier.fillMaxWidth().onFocusChanged {
                onFocus(it.isFocused)
                if (focused && !it.isFocused) draft.native = false
                focused = it.isFocused
            }
                .testTag("add-" + (sectionId ?: "top")))
    }
}

/** "Usual: eggs · bananas · coffee": a tap adds it (ACT checkitem.usual_add @android.notes @android.notesapp); × hides it (ACT checkitem.usual_dismiss @android.notes @android.notesapp). */
@Composable
private fun UsualStrip(vm: NotesViewModel, noteId: String, usual: Usual, update: (Usual) -> Unit) {
    var menuFor by remember { mutableStateOf<String?>(null) }
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth().horizontalScroll(rememberScrollState())
        .padding(horizontal = 12.dp).testTag("usual-strip")) {
        Text(stringResource(R.string.notes_usual), style = MaterialTheme.typography.labelLarge)
        usual.items.take(8).forEach { u ->
            Box {
                io.github.murtazanasir.mailr.look.ChipWithClose(stringResource(R.string.notes_hide_usual, u.text), onClose = { menuFor = u.key },
                    modifier = Modifier.padding(start = 6.dp)) { x ->
                    InputChip(selected = false, onClick = {
                        update(usual.copy(items = usual.items - u))
                        vm.actIn(noteId) { vm.repo.addUsual(noteId, u) }
                    }, label = { Text(u.text) }, trailingIcon = x)
                }
                DropdownMenu(menuFor == u.key, { menuFor = null }) {
                    DropdownMenuItem({ Text(stringResource(R.string.notes_hide_for_now)) }, {
                        menuFor = null; update(usual.copy(items = usual.items - u)); vm.act { vm.repo.dismissUsual(noteId, u.key, never = false) }
                    })
                    DropdownMenuItem({ Text(stringResource(R.string.notes_never_suggest)) }, {
                        menuFor = null; update(usual.copy(items = usual.items - u)); vm.act { vm.repo.dismissUsual(noteId, u.key, never = true) }
                    })
                }
            }
        }
        if (usual.items.size > 1) TextButton(onClick = {
            val all = usual.items.take(8)
            update(usual.copy(items = emptyList()))
            vm.run { all.forEach { vm.repo.addUsual(noteId, it) }; vm.say(AppText.plural(R.plurals.notes_op_items_added, all.size, all.size)) }
        }) { Text(stringResource(R.string.notes_add_all)) }
    }
}

@Composable
private fun FoldHeader(vm: NotesViewModel, note: Note, count: Int, open: Boolean, readOnly: Boolean, tick: String? = null, toggle: () -> Unit) {
    HorizontalDivider()
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)
        .clickable(onClickLabel = stringResource(if (open) R.string.notes_collapse else R.string.notes_expand)) { toggle() }.padding(start = 16.dp)
        .semantics { stateDescription = AppText.get(if (open) R.string.notes_expanded else R.string.notes_collapsed) }) {
        Icon(if (open) Icons.Filled.ExpandLess else Icons.Filled.ExpandMore, null, Modifier.size(20.dp))
        Spacer(Modifier.width(8.dp))
        Text(stringResource(R.string.notes_count_checked, count), style = MaterialTheme.typography.labelLarge)
        tick?.let { TickChip(it, Modifier.padding(start = 8.dp)) }
        Spacer(Modifier.weight(1f))
        // ACT note.clear_checked @android.notes @android.notesapp: one undo for all.
        if (!readOnly && note.canEdit) TextButton(onClick = { vm.actIn(note.id) { vm.repo.clearChecked(note.id) } }) { Text(stringResource(R.string.notes_clear_checked)) }
    }
}

@Composable
private fun CheckedRow(vm: NotesViewModel, noteId: String, item: Item, pending: Boolean, readOnly: Boolean, sections: List<Section>, fg: Color,
                       tickBy: String? = null) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)
        .pendingEdge(pending, MaterialTheme.colorScheme.primary, 8f).testTag("checked-" + item.id)) {
        Spacer(Modifier.width(24.dp))
        Checkbox(true, {
            // ACT checkitem.uncheck @android.notes @android.notesapp: each row unchecks with one tap.
            vm.actIn(noteId) { vm.repo.check(item.id, false) }
        }, enabled = !readOnly, modifier = Modifier.size(48.dp).semantics { contentDescription = item.text })
        item.qty?.chip?.let { Tag(it, Modifier.padding(end = 6.dp)) }
        tickBy?.let { TickChip(it, Modifier.padding(end = 6.dp)) }
        Text(item.text, textDecoration = TextDecoration.LineThrough, color = fg.copy(alpha = 0.7f), modifier = Modifier.weight(1f))
        item.sectionId?.let { sid -> sections.firstOrNull { it.id == sid } }?.let {
            Text(it.name, style = MaterialTheme.typography.labelSmall, color = fg.copy(alpha = 0.6f), modifier = Modifier.padding(end = 16.dp))
        }
    }
}

/** The ⋮ menu of the editor. */
@Composable
private fun NoteMenu(vm: NotesViewModel, note: Note, open: Boolean, close: () -> Unit, host: NotesHostCallbacks,
                     onHistory: () -> Unit, onDelete: () -> Unit, onShare: () -> Unit, onRepeat: () -> Unit) {
    val repo = vm.repo
    var rename by remember { mutableStateOf(false) }
    DropdownMenu(open, close) {
        if (note.canEdit) {
            // ACT note.to_checklist @android.notes @android.notesapp, ACT note.to_text @android.notes @android.notesapp
            DropdownMenuItem({ Text(stringResource(if (note.isList) R.string.notes_make_it_text else R.string.notes_make_it_a_list)) }, {
                close(); vm.actIn(note.id) { repo.convert(note.id, if (note.isList) "text" else "checklist") }
            })
            if (note.isList) {
                DropdownMenuItem({ Text(stringResource(R.string.notes_shopping_list)) }, { close(); vm.actIn(note.id) { repo.editNote(note.id, shopping = !note.shopping) } },
                    leadingIcon = { Checkbox(note.shopping, null) })
                DropdownMenuItem({ Text(stringResource(R.string.notes_show_checked_at_the_bottom)) }, {
                    close(); vm.actIn(note.id) { repo.editNote(note.id, checkedAtBottom = !note.checkedAtBottom) }
                }, leadingIcon = { Checkbox(note.checkedAtBottom, null) })
                DropdownMenuItem({ Text(stringResource(R.string.notes_suggest_usual_items)) }, { close(); vm.actIn(note.id) { repo.editNote(note.id, suggestUsual = !note.suggestUsual) } },
                    leadingIcon = { Checkbox(note.suggestUsual, null) })
                DropdownMenuItem({ Text(stringResource(R.string.notes_add_a_section)) }, { close(); rename = true })
                // ACT note.uncheck_all @android.notes @android.notesapp ("Start over")
                DropdownMenuItem({ Text(stringResource(R.string.notes_uncheck_all)) }, { close(); vm.actIn(note.id) { repo.uncheckAll(note.id) } })
                DropdownMenuItem({ Text(stringResource(R.string.notes_clear_checked)) }, { close(); vm.actIn(note.id) { repo.clearChecked(note.id) } })
                // ACT note.repeat @android.notes @android.notesapp: Off, Weekly on a day, Every N days.
                DropdownMenuItem({ Text(RepeatRule.parse(note.repeat?.rule)?.let { RepeatRule.menuLabel(it) } ?: stringResource(R.string.notes_repeat_ellipsis)) },
                    { close(); onRepeat() }, modifier = Modifier.testTag("menu-repeat"))
            }
            // ACT note.local_only @android.notes @android.notesapp: on for anyone who can edit; off for the owner only.
            LocalOnlyMenuItem(note.localOnly, note.canLowerLocalOnly, if (note.isList) "list" else "note") { on ->
                close(); vm.act { repo.setLocalOnly(note.id, on) }
            }
        }
        DropdownMenuItem({ Text(stringResource(R.string.notes_make_a_copy)) }, { close(); vm.act { repo.duplicate(note.id) } })
        if (!note.id.startsWith("local-")) DropdownMenuItem({ Text(stringResource(R.string.notes_save_as_template)) }, {
            close()
            // ACT note.save_template @android.notes @android.notesapp: named after the note; it shows under New from template.
            vm.run { repo.saveTemplate(note.id, note.title.ifBlank { null }); vm.say(AppText.get(R.string.notes_saved_as_template)) }
        })
        host.share?.let { share ->
            if (!note.id.startsWith("local-")) DropdownMenuItem({ Text(stringResource(R.string.notes_export)) }, {
                close()
                // ACT note.export @android.notes @android.notesapp: the note as Markdown, handed to another app.
                vm.run { share(exportName(note.title, "md"), repo.api.exportNote(note.id), "text/markdown") }
            })
        }
        // ACT note.history @android.notes @android.notesapp
        DropdownMenuItem({ Text(stringResource(R.string.notes_version_history)) }, { close(); onHistory() })
        DropdownMenuItem({ Text(stringResource(R.string.notes_share)) }, { close(); onShare() })
        // ACT note.open_app @android.notes @android.notesapp: "Open in Notes app" when the Notes app is installed ("Open in mailr" there).
        if (host.notesAppInstalled) DropdownMenuItem({ Text(host.otherAppLabel) }, { close(); host.openInNotesApp("note", note.id) })
        host.addToHome?.let { add ->
            // ACT note.add_to_home @android.notes @android.notesapp: a pinned launcher shortcut with the kind's icon and the note's colour.
            DropdownMenuItem({ Text(stringResource(R.string.notes_add_to_home_screen)) }, { close(); add("note", note.id) })
        }
        host.ask?.let { ask -> DropdownMenuItem({ Text(stringResource(R.string.notes_ask_assistant)) }, { close(); ask(note) }) }
        if (note.level != "owner" && note.owner?.kind != "group") DropdownMenuItem({ Text(stringResource(R.string.notes_remove_from_my_notes)) }, {
            close()
            // ACT note.leave @android.notes @android.notesapp
            vm.run {
                val u = repo.leave(note.id, isTable = false)
                vm.back()
                vm.say(AppText.get(R.string.notes_removed_from_your_notes), if (u != null) ({ repo.rejoin(note.id, false) }) else null)
            }
        })
        if (note.canDelete) DropdownMenuItem({ Text(stringResource(R.string.notes_delete)) }, { close(); onDelete() })
    }
    if (rename) TextDialog(stringResource(R.string.notes_new_section), "", stringResource(R.string.notes_add), onDismiss = { rename = false }) { v ->
        rename = false
        // ACT checksection.create @android.notes @android.notesapp
        if (v.isNotBlank()) vm.actIn(note.id) { repo.createSection(note.id, v) }
    }
}

/** A one-line text dialog (rename, a new section, the extra line). */
@Composable
fun TextDialog(title: String, initial: String, confirm: String, onDismiss: () -> Unit, onDone: (String) -> Unit) =
    NameDialog(title, initial, confirm, onDismiss, onDone)

/** The quantity editor: − and + for a count, a field for amount and unit, Remove. */
@Composable
private fun QtyDialog(item: Item, onDismiss: () -> Unit, onSave: (io.github.murtazanasir.mailr.notes.data.Qty?, Boolean) -> Unit) {
    var amount by remember { mutableStateOf(item.qty?.amount ?: "1") }
    var unit by remember { mutableStateOf(item.qty?.unit.orEmpty()) }
    fun bump(d: Int) {
        val n = (amount.toBigDecimalOrNull() ?: java.math.BigDecimal.ONE) + d.toBigDecimal()
        if (n.signum() > 0) amount = n.stripTrailingZeros().toPlainString()
    }
    val oneLess = stringResource(R.string.notes_one_less)
    val oneMore = stringResource(R.string.notes_one_more)
    AlertDialog(onDismissRequest = onDismiss, title = { Text(stringResource(R.string.notes_quantity_of, item.text)) },
        text = {
            Column {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    OutlinedButton(onClick = { bump(-1) }, modifier = Modifier.semantics { contentDescription = oneLess }) { Text("−") }
                    OutlinedTextField(amount, { amount = it }, singleLine = true, label = { Text(stringResource(R.string.notes_amount)) },
                        keyboardOptions = KeyboardOptions(keyboardType = androidx.compose.ui.text.input.KeyboardType.Decimal),
                        modifier = Modifier.weight(1f).padding(horizontal = 8.dp))
                    OutlinedButton(onClick = { bump(1) }, modifier = Modifier.semantics { contentDescription = oneMore }) { Text("+") }
                }
                OutlinedTextField(unit, { unit = it }, singleLine = true, label = { Text(stringResource(R.string.notes_unit_hint)) },
                    modifier = Modifier.fillMaxWidth())
            }
        },
        confirmButton = {
            TextButton(onClick = {
                val a = amount.trim()
                val u = unit.trim().ifEmpty { null }?.let { io.github.murtazanasir.mailr.notes.grammar.ItemGrammar.unitOf(it) ?: it }
                if (a.toBigDecimalOrNull() == null) onDismiss() else onSave(io.github.murtazanasir.mailr.notes.data.Qty(a, u), false)
            }) { Text(stringResource(R.string.notes_save)) }
        },
        dismissButton = { TextButton(onClick = { onSave(null, true) }) { Text(stringResource(R.string.notes_remove)) } })
}

/** A file name for an export: the title's letters, digits, spaces and dashes. */
fun exportName(title: String, ext: String): String =
    (title.filter { it.isLetterOrDigit() || it in " -_" }.trim().ifBlank { "note" }).take(80) + "." + ext
