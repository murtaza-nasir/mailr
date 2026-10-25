package io.github.murtazanasir.mailr.notesapp.capture

import io.github.murtazanasir.mailr.util.catching

import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.notesapp.R
import io.github.murtazanasir.mailr.i18n.AppText
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Mic
import io.github.murtazanasir.mailr.look.Button
import io.github.murtazanasir.mailr.look.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.OutlinedTextField
import io.github.murtazanasir.mailr.look.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import io.github.murtazanasir.mailr.look.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.notes.data.DataTable
import io.github.murtazanasir.mailr.notes.data.NotesRepository
import io.github.murtazanasir.mailr.notes.data.notesMessage
import io.github.murtazanasir.mailr.notesapp.data.NotesPrefs
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch

/** What quick capture starts as: a note, a list, or items for a list. */
enum class CaptureMode(@androidx.annotation.StringRes val labelRes: Int) {
    NOTE(R.string.notesapp_mode_note), LIST(R.string.notesapp_mode_list), ADD(R.string.notesapp_mode_add);

    val label: String get() = AppText.get(labelRes)
}

/** A sheet at the foot of the screen over a scrim; a tap on the scrim closes it. */
@Composable
fun SheetFrame(onDismiss: () -> Unit, content: @Composable () -> Unit) {
    Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha = 0.32f))
        .clickable(remember { MutableInteractionSource() }, null, onClick = onDismiss)) {
        Surface(shape = RoundedCornerShape(topStart = 28.dp, topEnd = 28.dp), color = MaterialTheme.colorScheme.surfaceContainerLow,
            tonalElevation = 2.dp,
            modifier = Modifier.align(Alignment.BottomCenter).widthIn(max = 640.dp).fillMaxWidth()
                .clickable(remember { MutableInteractionSource() }, null) {}
                .imePadding().navigationBarsPadding()) {
            Column(Modifier.verticalScroll(rememberScrollState()).padding(20.dp)) { content() }
        }
    }
}

@Composable
private fun SheetTitle(text: String) {
    Text(text, style = MaterialTheme.typography.titleLarge, modifier = Modifier.semantics { heading() }.padding(bottom = 12.dp))
}

/** A row of choices (lists, tables, sections) as chips; the chosen one is selected. */
@Composable
fun <T> Choices(label: String, options: List<T>, chosen: T?, name: (T) -> String, onPick: (T) -> Unit, tagPrefix: String) {
    Text(label, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(top = 8.dp, bottom = 4.dp))
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.heightIn(max = 168.dp).verticalScroll(rememberScrollState())) {
        options.forEachIndexed { i, o ->
            FilterChip(selected = o == chosen, onClick = { onPick(o) }, label = { Text(name(o)) }, modifier = Modifier.testTag("$tagPrefix-$i"))
        }
    }
}

/**
 * Quick capture: a note, a list, or items added to a list through the item
 * grammar in capture mode (`#list` picks the list; without one, the chosen list). The microphone
 * dictates into the text. ACT note.create @android.noteslauncher (the tile and the static shortcuts open it)
 */
@Composable
fun CaptureSheet(repo: NotesRepository, prefs: NotesPrefs, mode: CaptureMode, listId: String?,
                 dictate: ((String) -> Unit) -> Unit, dictateNow: Boolean = false, initialText: String = "", onDone: (String?) -> Unit) {
    val notes by repo.notes.collectAsState(emptyList())
    val sections by repo.sectionsByNote.collectAsState(emptyMap())
    val lists = remember(notes, sections) { CaptureLogic.ordered(CaptureLogic.lists(notes, sections), prefs.recents(), prefs.defaultList) }
    var m by rememberSaveable { mutableStateOf(mode) }
    var title by rememberSaveable { mutableStateOf("") }
    var text by rememberSaveable { mutableStateOf(initialText) }
    var listPick by rememberSaveable { mutableStateOf(listId ?: prefs.defaultList) }
    var sectionPick by rememberSaveable { mutableStateOf<String?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    val focus = remember { FocusRequester() }
    LaunchedEffect(m) { catching { focus.requestFocus() } }
    // The capture widget's Dictate: the microphone at once, its words into the note.
    LaunchedEffect(Unit) { if (dictateNow) dictate { said -> text = said } }
    val chosen = lists.firstOrNull { it.id == listPick } ?: lists.firstOrNull()
    val plan = remember(text, lists, chosen) { CaptureLogic.plan(text, lists, chosen) }
    // A note that starts with #List adds to that list (the quick grammar); its title stays empty.
    val quick = remember(text, lists, m, title) { if (m == CaptureMode.NOTE && title.isBlank()) CaptureLogic.quickPlan(text, lists) else null }

    val noSection = stringResource(R.string.notesapp_no_section)
    SheetFrame({ onDone(null) }) {
        SheetTitle(stringResource(R.string.notesapp_quick_note))
        SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
            CaptureMode.entries.forEachIndexed { i, cm ->
                SegmentedButton(selected = m == cm, onClick = { m = cm; error = null },
                    shape = SegmentedButtonDefaults.itemShape(i, CaptureMode.entries.size), modifier = Modifier.testTag("mode-${cm.name}")) {
                    Text(stringResource(cm.labelRes))
                }
            }
        }
        Spacer(Modifier.padding(top = 12.dp))
        if (m != CaptureMode.ADD) {
            OutlinedTextField(title, { title = it }, label = { Text(stringResource(R.string.notesapp_title)) }, singleLine = true,
                modifier = Modifier.fillMaxWidth().testTag("capture-title"))
            Spacer(Modifier.padding(top = 8.dp))
        } else {
            if (lists.isEmpty()) Text(stringResource(R.string.notesapp_no_lists_capture), style = MaterialTheme.typography.bodyMedium)
            else Choices(stringResource(R.string.notesapp_list), lists, chosen, { it.name }, { listPick = it.id; sectionPick = null }, "list")
            val secs = sections[chosen?.id].orEmpty()
            if (secs.isNotEmpty() && plan.list?.id == chosen?.id) {
                Choices(stringResource(R.string.notesapp_section), listOf<io.github.murtazanasir.mailr.notes.data.Section?>(null) + secs, secs.firstOrNull { it.id == sectionPick },
                    { it?.name ?: noSection }, { sectionPick = it?.id }, "section")
            }
            Spacer(Modifier.padding(top = 8.dp))
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(text, { text = it; error = null },
                label = { Text(stringResource(when (m) { CaptureMode.NOTE -> R.string.notesapp_take_a_note; CaptureMode.LIST -> R.string.notesapp_items_one_per_line; CaptureMode.ADD -> R.string.notesapp_add_items })) },
                minLines = if (m == CaptureMode.ADD) 1 else 3,
                modifier = Modifier.weight(1f).focusRequester(focus).testTag("capture-text"))
            IconButton(onClick = { dictate { said -> text = (text.trimEnd() + if (text.isBlank()) "" else if (m == CaptureMode.NOTE) " " else "\n").trimStart() + said } }) {
                Icon(Icons.Outlined.Mic, stringResource(R.string.notesapp_dictate))
            }
        }
        if (quick != null || (m == CaptureMode.ADD && text.isNotBlank())) {
            Text(CaptureLogic.summary(quick ?: plan), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 6.dp).testTag("capture-summary"))
        }
        error?.let { Text(it, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(top = 6.dp)) }
        Row(Modifier.fillMaxWidth().padding(top = 16.dp), horizontalArrangement = Arrangement.End) {
            TextButton(onClick = { onDone(null) }) { Text(stringResource(R.string.notesapp_cancel)) }
            Spacer(Modifier.width(8.dp))
            Button(onClick = {
                scope.launch {
                    try {
                        onDone(save(repo, prefs, m, title, text, plan, sectionPick, quick) ?: return@launch)
                    } catch (e: CancellationException) { throw e } catch (e: Exception) {
                        error = e.notesMessage()
                    }
                }
            }, modifier = Modifier.testTag("capture-save")) { Text(stringResource(if (m == CaptureMode.ADD || quick != null) R.string.notesapp_add else R.string.notesapp_save)) }
        }
    }
}

/** Saves a capture; the line for the toast, or null when there was nothing to save. */
internal suspend fun save(repo: NotesRepository, prefs: NotesPrefs, m: CaptureMode, title: String, text: String,
                          plan: CaptureLogic.AddPlan, sectionId: String?, quick: CaptureLogic.AddPlan? = null): String? = when {
    // ACT note.quick @android.notesapp: "#Shopping Costco: milk" adds milk to Costco in Shopping, offline too.
    m == CaptureMode.NOTE && quick != null -> save(repo, prefs, CaptureMode.ADD, "", text, quick, null)
    m == CaptureMode.NOTE -> {
        if (title.isBlank() && text.isBlank()) null
        else { repo.createNote("text", title = title.trim(), body = text.trim()); AppText.get(R.string.notesapp_note_saved) }
    }
    m == CaptureMode.LIST -> {
        if (title.isBlank() && text.isBlank()) null
        else { repo.createNote("checklist", title = title.trim(), lines = text); AppText.get(R.string.notesapp_list_saved) }
    }
    else -> {
        val id = CaptureLogic.add(repo, plan, sectionId)
        if (id == null) null else {
            prefs.defaultList = id
            prefs.opened("note", id)
            val n = plan.items.size
            AppText.get(R.string.notesapp_added_to, if (n == 1) plan.items[0].text else AppText.plural(R.plurals.notesapp_n_items, n, n), plan.list?.name ?: AppText.get(R.string.notesapp_the_list))
        }
    }
}

/** What the share sheet makes of shared text. */
enum class ShareMode(@androidx.annotation.StringRes val labelRes: Int) {
    NOTE(R.string.notesapp_share_mode_note), LIST(R.string.notesapp_add_to_a_list), TABLE(R.string.notesapp_share_mode_table);

    val label: String get() = AppText.get(labelRes)
}

/**
 * The share target (surface `android.notesshare`): shared text becomes a
 * new note (the default), items of a list (recent lists first, each line or comma piece through
 * the grammar in capture mode, with a section to pick) or rows of a table (tab- or comma-separated
 * text, previewed as rows). A shared link becomes a note with the shared title and the address.
 */
@Composable
fun ShareSheet(repo: NotesRepository, prefs: NotesPrefs, text: String, subject: String?, onDone: (String?) -> Unit) {
    val notes by repo.notes.collectAsState(emptyList())
    val sections by repo.sectionsByNote.collectAsState(emptyMap())
    val tables by repo.tables.collectAsState(emptyList())
    val lists = remember(notes, sections) { CaptureLogic.ordered(CaptureLogic.lists(notes, sections), prefs.recents(), prefs.defaultList) }
    val editableTables = remember(tables) { tables.filter { it.canEdit && it.deletedAt == null } }
    val shared = remember(text, subject) { CaptureLogic.shared(text, subject) }
    var m by rememberSaveable { mutableStateOf(ShareMode.NOTE) }
    var title by rememberSaveable { mutableStateOf(shared.title) }
    var body by rememberSaveable { mutableStateOf(shared.body) }
    var listPick by rememberSaveable { mutableStateOf<String?>(null) }
    var sectionPick by rememberSaveable { mutableStateOf<String?>(null) }
    var tablePick by rememberSaveable { mutableStateOf<String?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    val chosenList = lists.firstOrNull { it.id == listPick } ?: lists.firstOrNull()
    val itemsText = remember(text) { CaptureLogic.itemsOf(text) }
    val plan = remember(itemsText, lists, chosenList) { CaptureLogic.plan(itemsText, lists, chosenList) }
    val chosenTable = editableTables.firstOrNull { it.id == tablePick } ?: editableTables.firstOrNull()
    val rows = remember(text, chosenTable) { chosenTable?.let { CaptureLogic.rows(text, it) }.orEmpty() }

    val noSection = stringResource(R.string.notesapp_no_section)
    val untitledTable = stringResource(io.github.murtazanasir.mailr.notes.R.string.notes_untitled_table)
    SheetFrame({ onDone(null) }) {
        SheetTitle(stringResource(R.string.notesapp_save_to_notes))
        SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
            ShareMode.entries.forEachIndexed { i, sm ->
                SegmentedButton(selected = m == sm, onClick = { m = sm; error = null },
                    shape = SegmentedButtonDefaults.itemShape(i, ShareMode.entries.size), modifier = Modifier.testTag("share-${sm.name}")) {
                    Text(stringResource(sm.labelRes), maxLines = 1)
                }
            }
        }
        Spacer(Modifier.padding(top = 12.dp))
        when (m) {
            ShareMode.NOTE -> {
                OutlinedTextField(title, { title = it }, label = { Text(stringResource(R.string.notesapp_title)) }, singleLine = true, modifier = Modifier.fillMaxWidth().testTag("share-title"))
                Spacer(Modifier.padding(top = 8.dp))
                OutlinedTextField(body, { body = it }, label = { Text(stringResource(R.string.notesapp_note)) }, minLines = 3, maxLines = 10,
                    modifier = Modifier.fillMaxWidth().testTag("share-body"))
            }
            ShareMode.LIST -> {
                if (lists.isEmpty()) Text(stringResource(R.string.notesapp_no_lists_share))
                else {
                    Choices(stringResource(R.string.notesapp_list), lists, chosenList, { it.name }, { listPick = it.id; sectionPick = null }, "list")
                    val secs = sections[chosenList?.id].orEmpty()
                    if (secs.isNotEmpty()) Choices(stringResource(R.string.notesapp_section), listOf<io.github.murtazanasir.mailr.notes.data.Section?>(null) + secs,
                        secs.firstOrNull { it.id == sectionPick }, { it?.name ?: noSection }, { sectionPick = it?.id }, "section")
                    Text(CaptureLogic.summary(plan), style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(top = 8.dp).testTag("share-summary"))
                    plan.items.take(8).forEach { Text("• " + (it.qty?.text?.let { q -> "$q " } ?: "") + it.text, style = MaterialTheme.typography.bodySmall) }
                    if (plan.items.size > 8) Text(stringResource(R.string.notesapp_and_more, plan.items.size - 8), style = MaterialTheme.typography.bodySmall)
                }
            }
            ShareMode.TABLE -> {
                if (editableTables.isEmpty()) Text(stringResource(R.string.notesapp_no_tables))
                else {
                    Choices(stringResource(R.string.notesapp_table), editableTables, chosenTable, { it.name.ifBlank { untitledTable } }, { tablePick = it.id }, "table")
                    chosenTable?.let { t -> RowsPreview(t, rows) }
                }
            }
        }
        error?.let { Text(it, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(top = 6.dp)) }
        Row(Modifier.fillMaxWidth().padding(top = 16.dp), horizontalArrangement = Arrangement.End) {
            TextButton(onClick = { onDone(null) }) { Text(stringResource(R.string.notesapp_cancel)) }
            Spacer(Modifier.width(8.dp))
            val enabled = when (m) {
                ShareMode.NOTE -> title.isNotBlank() || body.isNotBlank()
                ShareMode.LIST -> chosenList != null && plan.items.isNotEmpty()
                ShareMode.TABLE -> chosenTable != null && rows.isNotEmpty()
            }
            Button(enabled = enabled, onClick = {
                scope.launch {
                    try {
                        val msg = when (m) {
                            ShareMode.NOTE -> {
                                // ACT note.create @android.notesshare
                                repo.createNote("text", title = title.trim(), body = body.trim()); AppText.get(R.string.notesapp_note_saved)
                            }
                            ShareMode.LIST -> {
                                // ACT note.add_items @android.notesshare: each line or comma piece, through the grammar.
                                val id = CaptureLogic.add(repo, plan, sectionPick)
                                id?.let { prefs.defaultList = it; prefs.opened("note", it) }
                                AppText.plural(R.plurals.notesapp_share_items_added, plan.items.size, plan.items.size, plan.list?.name ?: AppText.get(R.string.notesapp_the_list))
                            }
                            ShareMode.TABLE -> {
                                val n = CaptureLogic.addRows(repo, chosenTable!!, rows)
                                AppText.plural(R.plurals.notesapp_rows_added, n, n, chosenTable.name)
                            }
                        }
                        onDone(msg)
                    } catch (e: CancellationException) { throw e } catch (e: Exception) {
                        error = e.notesMessage()
                    }
                }
            }, modifier = Modifier.testTag("share-save")) { Text(stringResource(if (m == ShareMode.NOTE) R.string.notesapp_save else R.string.notesapp_add)) }
        }
    }
}

/** Shared text as rows: the column names, then up to five rows. */
@Composable
private fun RowsPreview(t: DataTable, rows: List<List<String>>) {
    Text(if (rows.isEmpty()) stringResource(R.string.notesapp_nothing_to_add_rows) else pluralStringResource(io.github.murtazanasir.mailr.notes.R.plurals.notes_rows_count, rows.size, rows.size),
        style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(top = 8.dp).testTag("share-rows"))
    val cols = t.columns.take(4)
    if (rows.isEmpty() || cols.isEmpty()) return
    Column(Modifier.padding(top = 4.dp)) {
        Row { cols.forEach { Text(it.name, fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(1f)) } }
        rows.take(5).forEach { r ->
            Row { cols.forEachIndexed { i, _ -> Text(r.getOrElse(i) { "" }, style = MaterialTheme.typography.bodySmall, maxLines = 1, modifier = Modifier.weight(1f)) } }
        }
        if (rows.size > 5) Text(stringResource(R.string.notesapp_and_more, rows.size - 5), style = MaterialTheme.typography.bodySmall)
    }
}

/** The sheet for a share the app cannot take yet (images), or when no one is signed in. */
@Composable
fun NoticeSheet(title: String, text: String, action: String? = null, onAction: () -> Unit = {}, onDone: () -> Unit) {
    SheetFrame(onDone) {
        SheetTitle(title)
        Text(text, style = MaterialTheme.typography.bodyLarge)
        Row(Modifier.fillMaxWidth().padding(top = 16.dp), horizontalArrangement = Arrangement.End) {
            TextButton(onClick = onDone) { Text(stringResource(R.string.notesapp_close)) }
            if (action != null) Button(onClick = onAction) { Text(action) }
        }
    }
}
