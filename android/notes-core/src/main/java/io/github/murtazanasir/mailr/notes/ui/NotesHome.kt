package io.github.murtazanasir.mailr.notes.ui

import io.github.murtazanasir.mailr.notes.R
import io.github.murtazanasir.mailr.i18n.AppText
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.staggeredgrid.LazyVerticalStaggeredGrid
import androidx.compose.foundation.lazy.staggeredgrid.StaggeredGridCells
import androidx.compose.foundation.lazy.staggeredgrid.StaggeredGridItemSpan
import androidx.compose.foundation.lazy.staggeredgrid.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.Label
import androidx.compose.material.icons.automirrored.outlined.List
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.PushPin
import androidx.compose.material.icons.outlined.Archive
import androidx.compose.material.icons.outlined.CheckBox
import androidx.compose.material.icons.outlined.CheckBoxOutlineBlank
import androidx.compose.material.icons.outlined.CloudOff
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.material.icons.outlined.Group
import androidx.compose.material.icons.outlined.Lightbulb
import androidx.compose.material.icons.outlined.Link
import androidx.compose.material.icons.outlined.Lock
import androidx.compose.material.icons.outlined.Menu
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.automirrored.outlined.Notes
import androidx.compose.material.icons.outlined.Notifications
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material.icons.outlined.TableChart
import io.github.murtazanasir.mailr.look.AlertDialog
import androidx.compose.material3.DrawerValue
import io.github.murtazanasir.mailr.look.DropdownMenu
import io.github.murtazanasir.mailr.look.DropdownMenuItem
import io.github.murtazanasir.mailr.look.ExtendedFloatingActionButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.ModalNavigationDrawer
import io.github.murtazanasir.mailr.look.NavigationDrawerItem
import io.github.murtazanasir.mailr.look.OutlinedTextField
import io.github.murtazanasir.mailr.look.RadioButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarDuration
import io.github.murtazanasir.mailr.look.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import io.github.murtazanasir.mailr.look.TextField
import androidx.compose.material3.TextFieldDefaults
import io.github.murtazanasir.mailr.look.TopAppBar
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.material3.rememberDrawerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.CustomAccessibilityAction
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import io.github.murtazanasir.mailr.notes.data.NoteLink
import io.github.murtazanasir.mailr.notes.data.OpEntity
import kotlinx.coroutines.launch

/** What the host app gives the Notes screens. */
data class NotesHostCallbacks(
    /** A link chip (a conversation, an event, a task, a recording): the host opens it where it lives. */
    val openLink: (NoteLink) -> Unit = {},
    /** The separate Notes app is installed ("Open in Notes app", ACT note.open_app @android.notes @android.notesapp). */
    val notesAppInstalled: Boolean = false,
    val openInNotesApp: (kind: String, id: String) -> Unit = { _, _ -> },
    /** Ask the assistant about a note (the host's ask capture), or null. */
    val ask: ((io.github.murtazanasir.mailr.notes.data.Note) -> Unit)? = null,
    /** The signed-in user's id: items, bodies and rows by someone else show their author. */
    val meId: Long? = null,
    /** The ⋮ entry that opens the note in the other app: "Open in Notes app" here, "Open in mailr" in the Notes app. */
    val otherAppLabel: String = AppText.get(R.string.notes_open_in_notes_app),
    /** "Add to home screen" on a note, list or table (a pinned launcher shortcut that opens it in this app), or null. */
    val addToHome: ((kind: String, id: String) -> Unit)? = null,
    /** Entries the host adds at the foot of the drawer (the Notes app's Settings). */
    val drawerExtras: List<Pair<String, () -> Unit>> = emptyList(),
    /** Hand a file's text to another app (the share sheet): Export of a note (Markdown) or a table (CSV); null hides them. */
    val share: ((fileName: String, text: String, mime: String) -> Unit)? = null,
)

/**
 * The Notes destination (`android.notes`): the grid with its drawer, a note, a table, a row form or
 * the label editor, one at a time, with Back to the one before. The same composable runs in the
 * Notes app.
 */
@Composable
fun NotesRoute(vm: NotesViewModel, host: NotesHostCallbacks = NotesHostCallbacks(), modifier: Modifier = Modifier) {
    val stack by vm.stack.collectAsStateWithLifecycle()
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    LaunchedEffect(vm) {
        vm.messages.collect { m ->
            scope.launch {
                snackbar.currentSnackbarData?.dismiss()
                val r = snackbar.showSnackbar(m.text, if (m.undo != null) AppText.get(R.string.notes_op_undo) else null,
                    withDismissAction = m.undo == null, duration = if (m.undo != null) SnackbarDuration.Long else SnackbarDuration.Short)
                if (r == SnackbarResult.ActionPerformed) m.undo?.let { u -> vm.run { u() } }
            }
        }
    }
    BackHandler(stack.size > 1) { vm.back() }
    Box(modifier.fillMaxSize()) {
        when (val page = stack.last()) {
            NotesPage.Grid -> NotesHome(vm, host)
            is NotesPage.OpenNote -> NoteEditor(vm, page.id, page.item, host)
            is NotesPage.OpenTable -> TableScreen(vm, page.id, host)
            is NotesPage.OpenRow -> RowForm(vm, page.tableId, page.rowId)
            NotesPage.EditLabels -> LabelEditor(vm)
        }
        SnackbarHost(snackbar, Modifier.align(Alignment.BottomCenter).padding(bottom = 80.dp))
    }
}

@Composable
fun NotesHome(vm: NotesViewModel, host: NotesHostCallbacks) {
    val state by vm.grid.collectAsStateWithLifecycle()
    val view by vm.view.collectAsStateWithLifecycle()
    val query by vm.query.collectAsStateWithLifecycle()
    val refreshing by vm.refreshing.collectAsStateWithLifecycle()
    val drawer = rememberDrawerState(DrawerValue.Closed)
    val scope = rememberCoroutineScope()
    var searching by remember { mutableStateOf(false) }
    var fabOpen by remember { mutableStateOf(false) }
    var newTable by remember { mutableStateOf(false) }
    var confirmEmpty by remember { mutableStateOf(false) }
    var templates by remember { mutableStateOf(false) }
    BackHandler(drawer.isOpen) { scope.launch { drawer.close() } }
    BackHandler(searching) { searching = false; vm.search("") }

    ModalNavigationDrawer(drawerState = drawer, drawerContent = {
        NotesDrawer(view, state, onPick = { v -> vm.show(v); scope.launch { drawer.close() } },
            onEditLabels = { vm.open(NotesPage.EditLabels); scope.launch { drawer.close() } },
            extras = host.drawerExtras.map { (label, go) -> label to { scope.launch { drawer.close() }; go() } })
    }) {
        Scaffold(
            topBar = {
                TopAppBar(
                    navigationIcon = {
                        IconButton(onClick = { scope.launch { drawer.open() } }) { Icon(Icons.Outlined.Menu, stringResource(R.string.notes_notes_menu)) }
                    },
                    title = {
                        if (searching) {
                            TextField(query, { vm.search(it) }, singleLine = true, placeholder = { Text(stringResource(R.string.notes_search_notes)) },
                                colors = TextFieldDefaults.colors(focusedContainerColor = MaterialTheme.colorScheme.surface,
                                    unfocusedContainerColor = MaterialTheme.colorScheme.surface),
                                modifier = Modifier.fillMaxWidth().testTag("notes-search"))
                        } else Text(view.title)
                    },
                    actions = {
                        if (searching) IconButton(onClick = { searching = false; vm.search("") }) { Icon(Icons.Filled.Close, stringResource(R.string.notes_close_search)) }
                        else IconButton(onClick = { searching = true }) { Icon(Icons.Outlined.Search, stringResource(R.string.notes_search_notes)) }
                        if (view == NotesView.Trash && state.cards.isNotEmpty()) TextButton(onClick = { confirmEmpty = true }) { Text(stringResource(R.string.notes_empty_trash)) }
                    },
                )
            },
            floatingActionButton = {
                if (view != NotesView.Trash && view != NotesView.Archive && view != NotesView.Shared) {
                    Box {
                        ExtendedFloatingActionButton(onClick = { fabOpen = true }, icon = { Icon(Icons.Filled.Add, null) },
                            text = { Text(stringResource(R.string.notes_new)) }, modifier = Modifier.testTag("notes-new"))
                        DropdownMenu(fabOpen, { fabOpen = false }) {
                            // ACT note.create @android.notes @android.notesapp: New note, New list, New table.
                            DropdownMenuItem({ Text(stringResource(R.string.notes_new_note)) }, { fabOpen = false; vm.newNote("text") },
                                leadingIcon = { Icon(Icons.AutoMirrored.Outlined.Notes, null) })
                            DropdownMenuItem({ Text(stringResource(R.string.notes_new_list)) }, { fabOpen = false; vm.newNote("checklist") },
                                leadingIcon = { Icon(Icons.Outlined.CheckBox, null) })
                            DropdownMenuItem({ Text(stringResource(R.string.notes_new_table)) }, { fabOpen = false; newTable = true },
                                leadingIcon = { Icon(Icons.Outlined.TableChart, null) })
                            // ACT note.from_template @android.notes @android.notesapp: a note, list or table from a template.
                            DropdownMenuItem({ Text(stringResource(R.string.notes_from_a_template)) }, { fabOpen = false; templates = true },
                                leadingIcon = { Icon(Icons.AutoMirrored.Outlined.Notes, null) })
                        }
                    }
                }
            },
        ) { pad ->
            Column(Modifier.padding(pad).fillMaxSize()) {
                QueueBanner(state, vm::retry, vm::discard)
                PullToRefreshBox(refreshing, onRefresh = vm::refresh, modifier = Modifier.fillMaxSize()) {
                    if (state.loaded && state.cards.isEmpty()) EmptyView(view, query)
                    else NotesGrid(vm, state, view, host)
                }
            }
        }
    }
    if (templates) TemplatesDialog(vm, onDismiss = { templates = false })
    if (newTable) NewTableDialog(onDismiss = { newTable = false }) { name, template -> newTable = false; vm.newTable(name, template) }
    if (confirmEmpty) AlertDialog(onDismissRequest = { confirmEmpty = false },
        title = { Text(stringResource(R.string.notes_empty_trash_title)) },
        text = { Text(pluralStringResource(R.plurals.notes_empty_trash_body, state.cards.size, state.cards.size)) },
        confirmButton = { TextButton(onClick = { confirmEmpty = false; vm.run { vm.repo.emptyTrash() } }) { Text(stringResource(R.string.notes_delete_forever)) } },
        dismissButton = { TextButton(onClick = { confirmEmpty = false }) { Text(stringResource(R.string.notes_cancel)) } })
}

@Composable
private fun EmptyView(view: NotesView, query: String) {
    val text = when {
        query.isNotBlank() -> stringResource(R.string.notes_empty_search, query)
        view == NotesView.Trash -> stringResource(R.string.notes_empty_trash_view)
        view is NotesView.Label -> stringResource(R.string.notes_no_notes_with_this_label)
        view == NotesView.Archive -> stringResource(R.string.notes_no_archived_notes)
        view == NotesView.Shared -> stringResource(R.string.notes_nothing_is_shared_with_you_yet)
        view == NotesView.Tables -> stringResource(R.string.notes_tables_you_add_appear_here)
        view == NotesView.Lists -> stringResource(R.string.notes_lists_you_add_appear_here)
        else -> stringResource(R.string.notes_notes_you_add_appear_here)
    }
    Box(Modifier.fillMaxSize().padding(32.dp), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Icon(Icons.Outlined.Lightbulb, null, Modifier.size(64.dp), tint = MaterialTheme.colorScheme.outline)
            Spacer(Modifier.height(12.dp))
            Text(text, style = MaterialTheme.typography.bodyLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

/**
 * The offline banner and refused writes: "Offline · 3 changes waiting"; a write the server
 * refused shows with Retry and Discard.
 */
@Composable
fun QueueBanner(state: GridState, retry: (OpEntity) -> Unit, discard: (OpEntity) -> Unit) {
    if (state.offline) {
        // notes.css .nt-banner.warn in the web's look.
        val (bg, fg) = io.github.murtazanasir.mailr.look.bannerColors(io.github.murtazanasir.mailr.look.BannerTone.Warning,
            MaterialTheme.colorScheme.secondaryContainer, MaterialTheme.colorScheme.onSecondaryContainer)
        Surface(color = bg, contentColor = fg, modifier = Modifier.fillMaxWidth()) {
            Row(Modifier.padding(horizontal = 16.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Outlined.CloudOff, null)
                Spacer(Modifier.width(12.dp))
                Text(if (state.pending > 0) pluralStringResource(R.plurals.notes_offline_waiting, state.pending, state.pending) else stringResource(R.string.notes_offline),
                    style = MaterialTheme.typography.bodyMedium)
            }
        }
    }
    state.failed.firstOrNull()?.let { op ->
        val (bg, fg) = io.github.murtazanasir.mailr.look.bannerColors(io.github.murtazanasir.mailr.look.BannerTone.Danger,
            MaterialTheme.colorScheme.errorContainer, MaterialTheme.colorScheme.onErrorContainer)
        Surface(color = bg, modifier = Modifier.fillMaxWidth()) {
            Row(Modifier.padding(start = 16.dp, end = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(stringResource(R.string.notes_op_failed, op.label, op.error ?: stringResource(R.string.notes_not_saved)), style = MaterialTheme.typography.bodyMedium,
                    color = fg, modifier = Modifier.weight(1f).padding(vertical = 10.dp))
                if (op.state == io.github.murtazanasir.mailr.notes.data.OpState.FAILED) TextButton(onClick = { retry(op) }) { Text(stringResource(R.string.notes_retry)) }
                TextButton(onClick = { discard(op) }) { Text(stringResource(R.string.notes_discard)) }
            }
        }
    }
}

@Composable
private fun NotesDrawer(view: NotesView, state: GridState, onPick: (NotesView) -> Unit, onEditLabels: () -> Unit,
                        extras: List<Pair<String, () -> Unit>> = emptyList()) {
    ModalDrawerSheet {
        androidx.compose.foundation.lazy.LazyColumn(contentPadding = PaddingValues(vertical = 12.dp)) {
            item {
                Text(stringResource(R.string.notes_drawer_title), style = MaterialTheme.typography.titleLarge, modifier = Modifier.padding(horizontal = 28.dp, vertical = 12.dp))
            }
            fun entry(v: NotesView, icon: ImageVector, badge: String? = null): @Composable () -> Unit = {
                NavigationDrawerItem(label = { Text(v.title) }, selected = view == v, onClick = { onPick(v) },
                    icon = { Icon(icon, null) }, badge = badge?.let { { Text(it) } },
                    modifier = Modifier.padding(horizontal = 12.dp))
            }
            item { entry(NotesView.All, Icons.Outlined.Lightbulb)() }
            item { entry(NotesView.Lists, Icons.Outlined.CheckBox)() }
            item { entry(NotesView.Tables, Icons.Outlined.TableChart)() }
            item {
                HorizontalDivider(Modifier.padding(vertical = 8.dp))
                Row(Modifier.fillMaxWidth().padding(start = 28.dp, end = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(stringResource(R.string.notes_labels), style = MaterialTheme.typography.titleSmall, modifier = Modifier.weight(1f).semantics { heading() })
                    TextButton(onClick = onEditLabels) { Text(stringResource(R.string.notes_edit)) }
                }
            }
            state.labels.forEach { l ->
                item(key = "label-" + l.name) {
                    entry(NotesView.Label(l.name), Icons.AutoMirrored.Outlined.Label, l.count.takeIf { it > 0 }?.toString())()
                }
            }
            item { HorizontalDivider(Modifier.padding(vertical = 8.dp)) }
            item { entry(NotesView.Shared, Icons.Outlined.Group)() }
            item { entry(NotesView.Archive, Icons.Outlined.Archive)() }
            item { entry(NotesView.Trash, Icons.Outlined.Delete)() }
            if (extras.isNotEmpty()) item { HorizontalDivider(Modifier.padding(vertical = 8.dp)) }
            extras.forEach { (label, go) ->
                item(key = "extra-$label") {
                    NavigationDrawerItem(label = { Text(label) }, selected = false, onClick = go,
                        icon = { Icon(Icons.Outlined.Settings, null) }, modifier = Modifier.padding(horizontal = 12.dp))
                }
            }
        }
    }
}

/**
 * Two columns of cards (one on a narrow phone, more on a tablet): Pinned, then Others. Rows of cards
 * in a lazy list, so a long grid stays light.
 */
@Composable
private fun NotesGrid(vm: NotesViewModel, state: GridState, view: NotesView, host: NotesHostCallbacks = NotesHostCallbacks()) {
    val pinnedTitle = stringResource(R.string.notes_group_pinned)
    val othersTitle = stringResource(R.string.notes_group_others)
    BoxWithConstraints {
        val columns = if (maxWidth < 360.dp) 1 else if (maxWidth > 840.dp) 4 else if (maxWidth > 600.dp) 3 else 2
        val pinned = state.cards.filter { it.pinned && view != NotesView.Archive && view != NotesView.Trash }
        val others = state.cards - pinned.toSet()
        androidx.compose.foundation.lazy.LazyColumn(contentPadding = PaddingValues(12.dp, 8.dp, 12.dp, 96.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxSize().testTag("notes-grid")) {
            fun group(title: String?, cards: List<Card>) {
                if (title != null) item(key = "h-$title") { GroupHeader(title) }
                cards.chunked(columns).forEach { row ->
                    item(key = "r-" + row.joinToString(",") { it.id }) {
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.Top) {
                            row.forEach { c -> Box(Modifier.weight(1f)) { CardView(vm, c, state, view, host) } }
                            repeat(columns - row.size) { Spacer(Modifier.weight(1f)) }
                        }
                    }
                }
            }
            if (pinned.isNotEmpty()) {
                group(pinnedTitle, pinned)
                group(if (others.isNotEmpty()) othersTitle else null, others)
            } else group(null, others)
        }
    }
}

@Composable
private fun GroupHeader(text: String) {
    Text(text.uppercase(), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(start = 4.dp, top = 8.dp).semantics { heading() })
}

/** A card: title, the first unchecked items with their quantity chips, own labels, icons. */
@Composable
private fun CardView(vm: NotesViewModel, card: Card, state: GridState, view: NotesView, host: NotesHostCallbacks) {
    var menu by remember { mutableStateOf(false) }
    var colour by remember { mutableStateOf(false) }
    var labels by remember { mutableStateOf(false) }
    var purge by remember { mutableStateOf(false) }
    val bg = NoteColors.background(card.color)
    val fg = NoteColors.content(card.color)
    val inTrash = view == NotesView.Trash
    val isTable = card is Card.TableCard
    fun open() {
        when (card) {
            is Card.NoteCard -> vm.open(NotesPage.OpenNote(card.id))
            is Card.TableCard -> vm.open(NotesPage.OpenTable(card.id))
        }
    }
    val kindName = when (card) {
        is Card.TableCard -> stringResource(R.string.notes_kind_table)
        is Card.NoteCard -> stringResource(if (card.note.isList) R.string.notes_kind_list else R.string.notes_kind_note)
    }
    val idx = state.cards.indexOf(card)
    val pinLabel = stringResource(R.string.notes_pin)
    val unpinLabel = stringResource(R.string.notes_unpin)
    val moveEarlier = stringResource(R.string.notes_move_earlier)
    val moveLater = stringResource(R.string.notes_move_later)
    // TalkBack: the grid's drag has menu and custom-action equivalents.
    val actions = buildList {
        if (!inTrash) {
            add(CustomAccessibilityAction(if (card.pinned) unpinLabel else pinLabel) {
                vm.act { vm.repo.setState(card.id, pinned = !card.pinned, label = AppText.get(if (card.pinned) R.string.notes_op_unpinned else R.string.notes_op_pinned)) }; true
            })
            if (idx > 0) add(CustomAccessibilityAction(moveEarlier) {
                vm.act { vm.repo.setState(card.id, before = state.cards[idx - 1].id, label = AppText.get(R.string.notes_op_moved)) }; true
            })
            if (idx >= 0 && idx < state.cards.size - 1) add(CustomAccessibilityAction(moveLater) {
                vm.act { vm.repo.setState(card.id, after = state.cards[idx + 1].id, label = AppText.get(R.string.notes_op_moved)) }; true
            })
        }
    }
    Box {
        Surface(shape = RoundedCornerShape(12.dp), color = bg, contentColor = fg,
            border = if (card.color == null) androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant) else null,
            modifier = Modifier.fillMaxWidth()
                .pendingEdge(card.pending, MaterialTheme.colorScheme.primary)
                .clip(RoundedCornerShape(12.dp))
                .combinedClickable(onClickLabel = stringResource(R.string.notes_open), onLongClickLabel = stringResource(R.string.notes_options), onLongClick = { menu = true }) { open() }
                .semantics { customActions = actions }
                .testTag("card-" + card.id)) {
            Column(Modifier.padding(12.dp)) {
                Row(verticalAlignment = Alignment.Top) {
                    if (card.title.isNotBlank()) Text(card.title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold,
                        maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                    else Spacer(Modifier.weight(1f))
                    if (card.pinned) Icon(Icons.Filled.PushPin, stringResource(R.string.notes_pinned), Modifier.size(18.dp))
                }
                when (card) {
                    is Card.NoteCard -> NotePreview(vm, card, fg)
                    is Card.TableCard -> TablePreviewView(card)
                }
                if (inTrash) {
                    Text(trashLabel(when (card) { is Card.NoteCard -> card.note.deletedAt; is Card.TableCard -> card.table.deletedAt }, vm.now()),
                        style = MaterialTheme.typography.labelSmall, modifier = Modifier.padding(top = 6.dp))
                }
                CardFooter(card)
            }
        }
        DropdownMenu(menu, { menu = false }) {
            if (inTrash) {
                // ACT note.restore @android.notes @android.notesapp, ACT table.restore @android.notes @android.notesapp
                DropdownMenuItem({ Text(stringResource(R.string.notes_restore)) }, { menu = false; vm.act { vm.repo.restore(card.id, isTable) } })
                // ACT note.purge @android.notes @android.notesapp, ACT table.purge @android.notes @android.notesapp
                DropdownMenuItem({ Text(stringResource(R.string.notes_delete_forever)) }, { menu = false; purge = true })
            } else {
                DropdownMenuItem({ Text(stringResource(if (card.pinned) R.string.notes_unpin else R.string.notes_pin)) }, {
                    menu = false
                    // ACT note.pin @android.notes @android.notesapp, ACT table.pin @android.notes @android.notesapp
                    vm.act { vm.repo.setState(card.id, pinned = !card.pinned, label = AppText.get(if (card.pinned) R.string.notes_op_unpinned else R.string.notes_op_pinned)) }
                })
                val archived = when (card) { is Card.NoteCard -> card.note.state.archived; is Card.TableCard -> card.table.state.archived }
                DropdownMenuItem({ Text(stringResource(if (archived) R.string.notes_unarchive else R.string.notes_archive)) }, {
                    menu = false
                    // ACT note.archive @android.notes @android.notesapp, ACT table.archive @android.notes @android.notesapp
                    vm.act { vm.repo.setState(card.id, archived = !archived, label = AppText.get(if (archived) R.string.notes_op_unarchived else R.string.notes_op_archived)) }
                })
                DropdownMenuItem({ Text(stringResource(R.string.notes_colour)) }, { menu = false; colour = true })
                DropdownMenuItem({ Text(stringResource(R.string.notes_labels)) }, { menu = false; labels = true })
                if (idx > 0) DropdownMenuItem({ Text(stringResource(R.string.notes_move_earlier)) }, {
                    menu = false
                    // ACT note.reorder @android.notes @android.notesapp: the grid's manual order (per viewer), by menu as by TalkBack.
                    vm.act { vm.repo.setState(card.id, before = state.cards[idx - 1].id, label = AppText.get(R.string.notes_op_moved)) }
                })
                if (idx >= 0 && idx < state.cards.size - 1) DropdownMenuItem({ Text(stringResource(R.string.notes_move_later)) }, {
                    menu = false
                    vm.act { vm.repo.setState(card.id, after = state.cards[idx + 1].id, label = AppText.get(R.string.notes_op_moved)) }
                })
                host.addToHome?.let { add ->
                    // ACT note.add_to_home @android.notes @android.notesapp: a pinned launcher shortcut for this card.
                    DropdownMenuItem({ Text(stringResource(R.string.notes_add_to_home_screen)) }, { menu = false; add(if (isTable) "table" else "note", card.id) })
                }
                if (!isTable) DropdownMenuItem({ Text(stringResource(R.string.notes_make_a_copy)) }, {
                    menu = false
                    // ACT note.duplicate @android.notes @android.notesapp
                    vm.act { vm.repo.duplicate(card.id) }
                })
                val canDelete = when (card) { is Card.NoteCard -> card.note.canDelete; is Card.TableCard -> card.table.canDelete }
                if (canDelete) DropdownMenuItem({ Text(stringResource(R.string.notes_delete)) }, {
                    menu = false
                    // ACT note.delete @android.notes @android.notesapp, ACT table.delete @android.notes @android.notesapp
                    vm.act { vm.repo.trash(card.id) }
                })
            }
        }
    }
    if (colour) ColorDialog(card.color, onPick = { c ->
        colour = false
        // ACT note.color @android.notes @android.notesapp, ACT table.color @android.notes @android.notesapp
        vm.act { if (isTable) vm.repo.editTable(card.id, color = c, clearColor = c == null) else vm.repo.editNote(card.id, color = c, clearColor = c == null) }
    }, onDismiss = { colour = false })
    if (labels) LabelsDialog(state.labels, card.labels, onSave = { chosen, created ->
        labels = false
        vm.act {
            created?.let { vm.repo.createLabel(it) }
            // ACT note.label @android.notes @android.notesapp, ACT table.label @android.notes @android.notesapp
            vm.repo.setState(card.id, labels = chosen.distinct(), label = AppText.get(R.string.notes_op_labels_saved))
        }
    }, onDismiss = { labels = false })
    if (purge) AlertDialog(onDismissRequest = { purge = false }, title = { Text(stringResource(R.string.notes_delete_forever_title)) },
        text = { Text(stringResource(R.string.notes_purge_body, card.title.ifBlank { kindName })) },
        confirmButton = { TextButton(onClick = { purge = false; vm.run { vm.repo.purge(card.id, isTable) } }) { Text(stringResource(R.string.notes_delete_forever)) } },
        dismissButton = { TextButton(onClick = { purge = false }) { Text(stringResource(R.string.notes_cancel)) } })
}

@Composable
private fun NotePreview(vm: NotesViewModel, card: Card.NoteCard, fg: androidx.compose.ui.graphics.Color) {
    val n = card.note
    if (!n.isList) {
        if (n.body.isNotBlank()) Text(n.body.lines().take(10).joinToString("\n"), style = MaterialTheme.typography.bodyMedium,
            maxLines = 10, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(top = 4.dp))
        return
    }
    var lastSection: String? = null
    Column(Modifier.padding(top = 4.dp)) {
        card.unchecked.take(8).forEach { it ->
            val sec = it.sectionId?.let { s -> card.sections[s] }
            if (sec != null && sec != lastSection) {
                Text(sec.uppercase(), style = MaterialTheme.typography.labelSmall, modifier = Modifier.padding(top = 4.dp))
            }
            lastSection = sec
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.heightIn(min = 32.dp)) {
                val canCheck = n.canEdit && n.deletedAt == null
                // ACT checkitem.check @android.notes @android.notesapp: a box on a card checks in place, with the undo.
                // The box is 32 dp so the card keeps its 32 dp rows; its touch target is 48 dp all the same:
                // Compose gives a pointer target smaller than ViewConfiguration.minimumTouchTargetSize the taps
                // just outside it, and between two rows the nearer box wins. Nothing else in the row may take
                // pointer input, or it would win those taps (NotesScreensTest.aCardBoxTakesTapsWithin48dp).
                IconButton(onClick = { vm.act { vm.repo.check(it.id, true) } }, enabled = canCheck, modifier = Modifier.size(32.dp)
                    .semantics { contentDescription = AppText.get(R.string.notes_check_item, it.text) }) {
                    Icon(Icons.Outlined.CheckBoxOutlineBlank, null, Modifier.size(18.dp), tint = fg)
                }
                it.qty?.chip?.let { c -> Tag(c, Modifier.padding(end = 4.dp)) }
                Text(it.text, style = MaterialTheme.typography.bodyMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
        val more = card.unchecked.size - 8
        val parts = listOfNotNull(more.takeIf { it > 0 }?.let { stringResource(R.string.notes_more_count, it) }, card.checkedCount.takeIf { it > 0 }?.let { stringResource(R.string.notes_count_checked, it) })
        if (parts.isNotEmpty()) Text(parts.joinToString(" · "), style = MaterialTheme.typography.labelMedium, modifier = Modifier.padding(top = 4.dp))
    }
}

@Composable
private fun TablePreviewView(card: Card.TableCard) {
    val t = card.table
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 4.dp)) {
        Icon(Icons.Outlined.TableChart, null, Modifier.size(16.dp))
        Spacer(Modifier.width(6.dp))
        Text(stringResource(R.string.notes_table_counts, pluralStringResource(R.plurals.notes_rows_count, t.counts.rows, t.counts.rows),
            (t.counts.columns.takeIf { it > 0 } ?: t.columns.size).let { pluralStringResource(R.plurals.notes_columns_count, it, it) }),
            style = MaterialTheme.typography.bodySmall)
    }
    t.preview?.rows?.take(4)?.forEach { r ->
        Text(r.mapNotNull { (it as? kotlinx.serialization.json.JsonPrimitive)?.content?.takeIf { s -> s.isNotBlank() } }.joinToString(" · "),
            style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

@Composable
private fun CardFooter(card: Card) {
    val (shared, links, local, changed) = when (card) {
        is Card.NoteCard -> listOf(card.note.shared, card.note.links.isNotEmpty(), card.note.localOnly, card.note.changed)
        is Card.TableCard -> listOf(card.table.shared, card.table.links.isNotEmpty(), card.table.localOnly, card.table.changed)
    }
    val reminder = (card as? Card.NoteCard)?.note?.reminders?.isNotEmpty() == true
    if (card.labels.isEmpty() && !shared && !links && !local && !changed && !reminder) return
    FlowRow(horizontalArrangement = Arrangement.spacedBy(4.dp), verticalArrangement = Arrangement.spacedBy(4.dp),
        modifier = Modifier.padding(top = 8.dp)) {
        card.labels.forEach { Tag(it) }
        if (shared) Icon(Icons.Outlined.Group, stringResource(R.string.notes_shared), Modifier.size(18.dp))
        if (reminder) Icon(Icons.Outlined.Notifications, stringResource(R.string.notes_has_a_reminder), Modifier.size(18.dp))
        if (links) Icon(Icons.Outlined.Link, stringResource(R.string.notes_has_links), Modifier.size(18.dp))
        if (local) Icon(Icons.Outlined.Lock, stringResource(R.string.notes_local_only_badge), Modifier.size(18.dp))
        val changedLabel = stringResource(R.string.notes_changed_since_you_looked)
        if (changed) Box(Modifier.size(8.dp).clip(CircleShape).background(MaterialTheme.colorScheme.primary)
            .semantics { contentDescription = changedLabel })
    }
}

@Composable
private fun NewTableDialog(onDismiss: () -> Unit, onCreate: (String, String?) -> Unit) {
    var name by remember { mutableStateOf("") }
    var template by remember { mutableStateOf<String?>(null) }
    AlertDialog(onDismissRequest = onDismiss, title = { Text(stringResource(R.string.notes_new_table)) },
        confirmButton = { TextButton(onClick = { onCreate(name.trim(), template) }) { Text(stringResource(R.string.notes_create)) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.notes_cancel)) } },
        text = {
            Column {
                OutlinedTextField(name, { name = it }, label = { Text(stringResource(R.string.notes_name)) }, singleLine = true, modifier = Modifier.fillMaxWidth())
                Spacer(Modifier.height(8.dp))
                Text(stringResource(R.string.notes_columns), style = MaterialTheme.typography.labelLarge)
                listOf(null to stringResource(R.string.notes_new_table_one_column), "log" to stringResource(R.string.notes_new_table_log), "inventory" to stringResource(R.string.notes_new_table_inventory)).forEach { (k, label) ->
                    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)
                        .combinedClickable(role = Role.RadioButton) { template = k }) {
                        RadioButton(template == k, null)
                        Spacer(Modifier.width(8.dp))
                        Text(label)
                    }
                }
                Text(stringResource(R.string.notes_columns_on_web), style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        })
}
