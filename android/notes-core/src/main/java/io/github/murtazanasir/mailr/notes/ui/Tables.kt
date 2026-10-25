package io.github.murtazanasir.mailr.notes.ui

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.notes.R
import io.github.murtazanasir.mailr.i18n.AppText
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
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
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.outlined.Search
import io.github.murtazanasir.mailr.look.AlertDialog
import io.github.murtazanasir.mailr.look.AssistChip
import io.github.murtazanasir.mailr.look.Button
import io.github.murtazanasir.mailr.look.Checkbox
import io.github.murtazanasir.mailr.look.DropdownMenu
import io.github.murtazanasir.mailr.look.DropdownMenuItem
import io.github.murtazanasir.mailr.look.FilterChip
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import io.github.murtazanasir.mailr.look.InputChip
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.OutlinedButton
import io.github.murtazanasir.mailr.look.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.SwipeToDismissBox
import androidx.compose.material3.SwipeToDismissBoxValue
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import io.github.murtazanasir.mailr.look.TopAppBar
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.material3.rememberSwipeToDismissBoxState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import io.github.murtazanasir.mailr.notes.data.Column as TableColumn
import io.github.murtazanasir.mailr.notes.data.DataTable
import io.github.murtazanasir.mailr.notes.data.NotesRepository
import io.github.murtazanasir.mailr.notes.data.Row as TableRow
import io.github.murtazanasir.mailr.notes.data.RowHistory
import io.github.murtazanasir.mailr.notes.data.RowsPage
import io.github.murtazanasir.mailr.notes.data.ViewEval
import io.github.murtazanasir.mailr.notes.data.SelectOption
import io.github.murtazanasir.mailr.notes.data.TableView
import io.github.murtazanasir.mailr.notes.data.notesMessage
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/** Cell values as text: numbers as typed, options by name, links by title. */
object Cells {
    fun options(c: TableColumn): List<SelectOption> = (c.config["options"] as? JsonArray)?.let {
        catching { io.github.murtazanasir.mailr.notes.data.NotesJson.decodeFromJsonElement(ListSerializer(SelectOption.serializer()), it) }.getOrNull()
    }.orEmpty()

    fun text(c: TableColumn, v: JsonElement?, refs: Map<String, JsonObject> = emptyMap()): String {
        if (v == null || v is JsonNull) return ""
        return when (c.type) {
            "checkbox" -> if ((v as? JsonPrimitive)?.booleanOrNull == true) "✓" else ""
            "select" -> (v as? JsonPrimitive)?.contentOrNull?.let { id -> options(c).firstOrNull { it.id == id }?.name ?: id }.orEmpty()
            "multi_select" -> (v as? JsonArray)?.mapNotNull { e -> (e as? JsonPrimitive)?.contentOrNull?.let { id -> options(c).firstOrNull { it.id == id }?.name ?: id } }
                ?.joinToString(", ").orEmpty()
            "datetime" -> (v as? JsonObject)?.get("at")?.jsonPrimitive?.contentOrNull?.replace('T', ' ').orEmpty()
            "currency" -> (v as? JsonPrimitive)?.contentOrNull?.let { "${(c.config["currency"] as? JsonPrimitive)?.contentOrNull ?: ""} $it".trim() }.orEmpty()
            "link" -> (v as? JsonArray)?.joinToString(", ") { e -> linkTitle(e.jsonObject, refs) }.orEmpty()
            else -> (v as? JsonPrimitive)?.contentOrNull ?: v.toString()
        }
    }

    private fun linkTitle(ref: JsonObject, refs: Map<String, JsonObject>): String {
        val kind = ref["kind"]?.jsonPrimitive?.contentOrNull
        val key = when (kind) {
            "row" -> "row:" + ref["row"]?.jsonPrimitive?.contentOrNull
            "conversation" -> "conversation:" + (ref["conversation_id"] ?: ref["id"])?.jsonPrimitive?.contentOrNull
            "event", "task" -> kind + ":" + ref["uid"]?.jsonPrimitive?.contentOrNull
            "note" -> "note:" + ref["id"]?.jsonPrimitive?.contentOrNull
            else -> ""
        }
        val r = refs[key] ?: return kind ?: AppText.get(R.string.notes_link)
        return when (r["state"]?.jsonPrimitive?.contentOrNull) {
            "hidden" -> AppText.get(R.string.notes_link_not_shared)
            "deleted" -> AppText.get(R.string.notes_link_deleted)
            else -> r["title"]?.jsonPrimitive?.contentOrNull ?: kind ?: AppText.get(R.string.notes_link)
        }
    }

    fun primary(t: DataTable): TableColumn? = t.columns.firstOrNull { it.id == t.primaryColumn } ?: t.columns.firstOrNull()

    /** The view's phone columns, or the next two shown columns. */
    fun phoneColumns(t: DataTable, v: TableView?): List<TableColumn> {
        val p = primary(t)
        val chosen = v?.spec?.phoneColumns.orEmpty().mapNotNull { id -> t.columns.firstOrNull { it.id == id } }
        if (chosen.isNotEmpty()) return chosen.take(2)
        val hidden = v?.spec?.hidden.orEmpty().toSet()
        return t.columns.filter { it.id != p?.id && it.id !in hidden }.take(2)
    }
}

/**
 * A table on the phone: a card per row with the primary value in bold
 * and the view's phone columns under it, the footer aggregates in a bar, view chips, the active
 * filters as chips (× clears one for this reading, ACT table.view_edit @android.notes @android.notesapp), + for a new row, swipe left
 * to delete, long-press to select. Columns are edited on the web.
 */
@Composable
fun TableScreen(vm: NotesViewModel, id: String, host: NotesHostCallbacks = NotesHostCallbacks()) {
    val repo = vm.repo
    val table by remember(id) { repo.table(id) }.collectAsStateWithLifecycle(null)
    val rows by remember(id) { repo.rows(id) }.collectAsStateWithLifecycle(emptyList())
    val labels by repo.labels.collectAsStateWithLifecycle(emptyList())
    var viewId by remember(id) { mutableStateOf<String?>(null) }
    var cleared by remember(id, viewId) { mutableStateOf(setOf<Int>()) }
    var q by remember(id) { mutableStateOf("") }
    var page by remember(id) { mutableStateOf<RowsPage?>(null) }
    var loading by remember { mutableStateOf(false) }
    var selected by remember(id) { mutableStateOf(setOf<String>()) }
    var menu by remember { mutableStateOf(false) }
    var rename by remember { mutableStateOf(false) }
    var colour by remember { mutableStateOf(false) }
    var labelPick by remember { mutableStateOf(false) }
    var share by remember { mutableStateOf(false) }
    var searching by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val t = table
    LaunchedEffect(id) { if (repo.tableNow(id) == null) repo.refreshTable(id) }
    val view = t?.views?.firstOrNull { it.id == viewId } ?: t?.views?.firstOrNull { it.default } ?: t?.views?.firstOrNull()
    val cached by remember(id, view?.id) { repo.viewPage(id, view?.id) }.collectAsStateWithLifecycle(null)
    val filters = view?.spec?.filters.orEmpty().filterIndexed { i, _ -> i !in cleared }
    suspend fun load() {
        loading = true
        try {
            val spec = if (cleared.isNotEmpty()) JsonObject(mapOf("filters" to JsonArray(filters))) else null
            // ACT table.search @android.notes @android.notesapp: find text in this table's cells.
            page = repo.loadRows(id, view?.id, spec, q.takeIf { it.isNotBlank() }) ?: page
        } finally {
            loading = false
        }
    }
    LaunchedEffect(id, view?.id, cleared, q) { if (t != null) load() }
    vm.tableFilters[id] = filters
    if (t == null) {
        Scaffold(topBar = { TopAppBar(title = {}, navigationIcon = { BackButton(vm) }) }) { pad ->
            Box(Modifier.padding(pad).fillMaxSize(), contentAlignment = Alignment.Center) { Text(stringResource(R.string.notes_loading)) }
        }
        return
    }
    val shown = page ?: cached
    // ACT table.presence @android.notes @android.notesapp: a shared table says it is open here; "Cleo is in this table".
    PresenceEffect(repo, "table", id, t.shared && t.deletedAt == null)
    val present by repo.presence.collectAsStateWithLifecycle()
    val presentNames = present[id].orEmpty().filter { vm.now() - it.at < NotesRepository.PRESENCE_TTL_MS }.map { it.name }
    val offline by repo.offline.collectAsStateWithLifecycle()
    val serverPage = page.takeIf { !offline }
    val refs = shown?.refs.orEmpty()
    // Online: the server's order for this reading. Offline, or before its first answer: the view
    // evaluated on the phone from its rows (the server's rules, ViewEval), so a row edited or added
    // offline goes where the view puts it.
    val ordered = remember(rows, serverPage, view, filters, cleared, q, refs) {
        val byId = rows.associateBy { it.row.id }
        if (serverPage != null) {
            val ids = serverPage.rows.map { it.id }
            val seen = ids.toSet()
            ids.mapNotNull { byId[it] } + if (cleared.isEmpty() && q.isBlank()) rows.filter { it.row.id !in seen && it.pending } else emptyList()
        } else {
            val zone = java.time.ZoneId.systemDefault()
            val titles = refs.mapValues { (it.value["title"] as? JsonPrimitive)?.contentOrNull.orEmpty() }
            val inView = ViewEval.rows(rows.map { it.row }, t.columns, view?.spec?.sort.orEmpty(), filters,
                ViewEval.Ctx(AppClock.today(zone), zone), titles)
            ViewEval.search(inView, t.columns, q) { c, v -> Cells.text(c, v, refs) }.mapNotNull { byId[it.id] }
        }
    }
    val primary = Cells.primary(t)
    val phone = Cells.phoneColumns(t, view)

    Scaffold(
        topBar = {
            TopAppBar(navigationIcon = { BackButton(vm) },
                title = {
                    if (selected.isNotEmpty()) Text(stringResource(R.string.notes_count_selected, selected.size))
                    else if (searching) OutlinedTextField(q, { q = it }, singleLine = true, placeholder = { Text(stringResource(R.string.notes_find_in_this_table)) },
                        modifier = Modifier.fillMaxWidth().testTag("table-search"))
                    else Text(t.name.ifBlank { stringResource(R.string.notes_untitled_table) }, maxLines = 1, overflow = TextOverflow.Ellipsis)
                },
                actions = {
                    if (selected.isNotEmpty()) {
                        // ACT row.bulk_delete @android.notes @android.notesapp: long-press to select, then delete with one undo.
                        IconButton(onClick = { val ids = selected.toList(); selected = emptySet(); vm.act { repo.deleteRows(id, ids) } }) {
                            Icon(Icons.Outlined.Delete, stringResource(R.string.notes_delete_selected_rows))
                        }
                        IconButton(onClick = { selected = emptySet() }) { Icon(Icons.Filled.Close, stringResource(R.string.notes_clear_selection)) }
                    } else {
                        IconButton(onClick = { searching = !searching; if (!searching) q = "" }) {
                            Icon(if (searching) Icons.Filled.Close else Icons.Outlined.Search,
                                stringResource(if (searching) R.string.notes_close_search else R.string.notes_find_in_this_table))
                        }
                        Box {
                            IconButton(onClick = { menu = true }) { Icon(Icons.Outlined.MoreVert, stringResource(R.string.notes_more_options)) }
                            DropdownMenu(menu, { menu = false }) {
                                if (t.canEdit) {
                                    // ACT table.rename @android.notes @android.notesapp
                                    DropdownMenuItem({ Text(stringResource(R.string.notes_rename)) }, { menu = false; rename = true })
                                    DropdownMenuItem({ Text(stringResource(R.string.notes_colour)) }, { menu = false; colour = true })
                                    // ACT table.local_only @android.notes @android.notesapp: on for anyone who can edit; off for the owner only.
                                    LocalOnlyMenuItem(t.localOnly, t.canLowerLocalOnly, "table") { on ->
                                        menu = false; vm.act { repo.setLocalOnly(id, on) }
                                    }
                                }
                                DropdownMenuItem({ Text(stringResource(R.string.notes_labels)) }, { menu = false; labelPick = true })
                                DropdownMenuItem({ Text(stringResource(if (t.state.pinned) R.string.notes_unpin else R.string.notes_pin)) }, {
                                    menu = false; vm.act { repo.setState(id, pinned = !t.state.pinned, label = AppText.get(if (t.state.pinned) R.string.notes_op_unpinned else R.string.notes_op_pinned)) }
                                })
                                DropdownMenuItem({ Text(stringResource(if (t.state.archived) R.string.notes_unarchive else R.string.notes_archive)) }, {
                                    menu = false; vm.act { repo.setState(id, archived = !t.state.archived, label = AppText.get(if (t.state.archived) R.string.notes_op_unarchived else R.string.notes_op_archived)) }
                                })
                                DropdownMenuItem({ Text(stringResource(R.string.notes_share)) }, { menu = false; share = true })
                                DropdownMenuItem({ Text(stringResource(R.string.notes_make_a_copy)) }, {
                                    menu = false
                                    // ACT table.duplicate @android.notes @android.notesapp: the copy, with its rows, shows after the next read.
                                    vm.run { repo.duplicateTable(id); vm.say(AppText.get(R.string.notes_table_copied)) }
                                })
                                host.share?.let { send ->
                                    DropdownMenuItem({ Text(stringResource(R.string.notes_export_csv)) }, {
                                        menu = false
                                        // ACT table.export_csv @android.notes @android.notesapp: the default view as CSV, handed to another app.
                                        vm.run { send(exportName(t.name, "csv"), repo.api.exportCsv(id), "text/csv") }
                                    })
                                }
                                if (host.notesAppInstalled) DropdownMenuItem({ Text(host.otherAppLabel) }, { menu = false; host.openInNotesApp("table", id) })
                                host.addToHome?.let { add ->
                                    // ACT note.add_to_home @android.notes @android.notesapp (a table)
                                    DropdownMenuItem({ Text(stringResource(R.string.notes_add_to_home_screen)) }, { menu = false; add("table", id) })
                                }
                                if (t.canDelete) DropdownMenuItem({ Text(stringResource(R.string.notes_delete)) }, { menu = false; vm.act { repo.trash(id) }; vm.back() })
                            }
                        }
                    }
                })
        },
        floatingActionButton = {
            // ACT row.add @android.notes @android.notesapp: + opens an empty form with the view's filter values filled in.
            if (t.canEdit && t.deletedAt == null) FloatingActionButton(onClick = { vm.open(NotesPage.OpenRow(id, null)) }) {
                Icon(Icons.Filled.Add, stringResource(R.string.notes_new_row))
            }
        },
        bottomBar = { FooterBar(t, shown) },
    ) { pad ->
        Column(Modifier.padding(pad).fillMaxSize()) {
            PresenceBanner(presentNames, "table")
            if (t.views.size > 1) Row(Modifier.horizontalScroll(rememberScrollState()).padding(horizontal = 12.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                t.views.forEach { v ->
                    // ACT table.view_edit @android.notes @android.notesapp: view chips switch views.
                    FilterChip(selected = v.id == view?.id, onClick = { viewId = v.id }, label = { Text(v.name) })
                }
            }
            if (view != null && view.spec.filters.isNotEmpty()) Row(Modifier.horizontalScroll(rememberScrollState()).padding(horizontal = 12.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                view.spec.filters.forEachIndexed { i, f ->
                    if (i in cleared) return@forEachIndexed
                    InputChip(selected = true, onClick = { cleared = cleared + i }, label = { Text(filterLabel(t, f)) },
                        trailingIcon = { Icon(Icons.Filled.Close, stringResource(R.string.notes_clear_this_filter), Modifier.width(18.dp)) })
                }
            }
            PullToRefreshBox(loading, onRefresh = { scope.launch { repo.drain(); repo.pull(); load() } }, modifier = Modifier.fillMaxSize()) {
                if (ordered.isEmpty()) Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Text(stringResource(if (view?.spec?.filters?.isNotEmpty() == true || q.isNotBlank()) R.string.notes_no_rows_match else R.string.notes_no_rows_yet),
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                LazyColumn(contentPadding = PaddingValues(12.dp, 8.dp, 12.dp, 96.dp), verticalArrangement = Arrangement.spacedBy(8.dp),
                    modifier = Modifier.fillMaxSize().testTag("rows")) {
                    items(ordered, key = { it.row.id }) { rs ->
                        RowCard(vm, t, rs, primary, phone, refs, selected.contains(rs.row.id), selecting = selected.isNotEmpty(),
                            onOpen = { if (selected.isNotEmpty()) selected = selected.toggle(rs.row.id) else vm.open(NotesPage.OpenRow(id, rs.row.id)) },
                            onSelect = { if (t.canEdit) selected = selected.toggle(rs.row.id) })
                    }
                }
            }
        }
    }
    if (rename) TextDialog(stringResource(R.string.notes_rename_table), t.name, stringResource(R.string.notes_rename), onDismiss = { rename = false }) { v ->
        rename = false
        if (v.isNotBlank() && v != t.name) vm.act { repo.editTable(id, name = v.trim()) }
    }
    if (colour) ColorDialog(t.color, onPick = { c ->
        colour = false
        // ACT table.color @android.notes @android.notesapp
        vm.act { repo.editTable(id, color = c, clearColor = c == null) }
    }, onDismiss = { colour = false })
    if (labelPick) LabelsDialog(labels, t.state.labels, onSave = { chosen, created ->
        labelPick = false
        vm.act { created?.let { repo.createLabel(it) }; repo.setState(id, labels = chosen.distinct(), label = AppText.get(R.string.notes_op_labels_saved)) }
    }, onDismiss = { labelPick = false })
    if (share) ShareSheet(vm, "table", id, onDismiss = { share = false })
}

private fun Set<String>.toggle(x: String) = if (x in this) this - x else this + x

private fun filterLabel(t: DataTable, f: JsonObject): String {
    val col = t.columns.firstOrNull { it.id == f["col"]?.jsonPrimitive?.contentOrNull }
    val op = f["op"]?.jsonPrimitive?.contentOrNull ?: ""
    val v = f["value"]
    val value = when {
        v == null || v is JsonNull -> ""
        col != null && (col.type == "select" || col.type == "multi_select") ->
            (if (v is JsonArray) v else JsonArray(listOf(v))).joinToString(", ") { e -> Cells.text(col.copy(type = "select"), e) }
        v is JsonPrimitive -> v.content
        else -> v.toString()
    }
    return listOf(col?.name ?: "?", op.replace('_', ' '), value).filter { it.isNotBlank() }.joinToString(" ")
}

@Composable
private fun RowCard(vm: NotesViewModel, t: DataTable, rs: NotesRepository.RowState, primary: TableColumn?, phone: List<TableColumn>,
                    refs: Map<String, JsonObject>, isSelected: Boolean, selecting: Boolean, onOpen: () -> Unit, onSelect: () -> Unit) {
    val swipe = rememberSwipeToDismissBoxState(confirmValueChange = { v ->
        // ACT row.delete @android.notes @android.notesapp: swipe left deletes, with undo.
        if (v == SwipeToDismissBoxValue.EndToStart) vm.act { vm.repo.deleteRows(t.id, listOf(rs.row.id)) }
        false
    })
    SwipeToDismissBox(swipe, enableDismissFromStartToEnd = false, enableDismissFromEndToStart = t.canEdit && !selecting,
        backgroundContent = {
            if (swipe.dismissDirection == SwipeToDismissBoxValue.Settled) return@SwipeToDismissBox
            Box(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.errorContainer, RoundedCornerShape(12.dp)).padding(horizontal = 24.dp),
                contentAlignment = Alignment.CenterEnd) { Text(stringResource(R.string.notes_delete)) }
        }) {
        Surface(shape = RoundedCornerShape(12.dp), color = if (isSelected) MaterialTheme.colorScheme.secondaryContainer else MaterialTheme.colorScheme.surfaceContainerLow,
            modifier = Modifier.fillMaxWidth().pendingEdge(rs.pending, MaterialTheme.colorScheme.primary)
                .combinedClickable(onLongClick = onSelect, onLongClickLabel = stringResource(R.string.notes_select), onClick = onOpen)
                .semantics { selected = isSelected }
                .testTag("row-" + rs.row.id)) {
            Column(Modifier.padding(12.dp).heightIn(min = 24.dp)) {
                val title = primary?.let { Cells.text(it, rs.row.cells[it.id], refs) }.orEmpty()
                Text(title.ifBlank { stringResource(R.string.notes_empty_value) }, fontWeight = FontWeight.SemiBold, maxLines = 2, overflow = TextOverflow.Ellipsis)
                phone.forEach { c ->
                    val v = Cells.text(c, rs.row.cells[c.id], refs)
                    val raw = (rs.row.raw[c.id] as? JsonPrimitive)?.contentOrNull
                    if (v.isNotBlank() || raw != null) Text(stringResource(R.string.notes_row_cell, c.name, v.ifBlank { "⚠ $raw" }), style = MaterialTheme.typography.bodySmall,
                        maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
                if (rs.conflict != null) Text(stringResource(R.string.notes_row_conflict_hint), style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.error)
            }
        }
    }
}

/** The view's footer aggregates (ACT table.stats @android.notes @android.notesapp), from the last online read. */
@Composable
private fun FooterBar(t: DataTable, page: RowsPage?) {
    val f = page?.footers.orEmpty().filter { it.value.value != null && it.value.value !is JsonNull }
    if (page == null) return
    Surface(color = MaterialTheme.colorScheme.surfaceContainer, modifier = Modifier.fillMaxWidth()) {
        Row(Modifier.horizontalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 10.dp).testTag("footers"),
            horizontalArrangement = Arrangement.spacedBy(16.dp)) {
            Text(pluralStringResource(R.plurals.notes_rows_count, page.count, page.count), style = MaterialTheme.typography.labelLarge)
            // ACT table.stats @android.notes @android.notesapp
            f.forEach { (col, foot) ->
                val name = t.columns.firstOrNull { it.id == col }?.name ?: col
                val v = foot.value
                val text = when (v) {
                    is JsonPrimitive -> v.content
                    is JsonObject -> (v["top"] as? JsonArray)?.joinToString(", ") { e ->
                        "${e.jsonObject["name"]?.jsonPrimitive?.contentOrNull} ${e.jsonObject["count"]?.jsonPrimitive?.contentOrNull}"
                    } ?: v.toString()
                    else -> v.toString()
                }
                Text(stringResource(R.string.notes_footer_stat, name, foot.agg ?: "", text), style = MaterialTheme.typography.labelLarge)
            }
        }
    }
}

/**
 * The row form: one field per column, typed inputs, saved per field with the
 * undo (ACT row.edit @android.notes @android.notesapp); tabs Fields and History. A new row (ACT row.add @android.notes @android.notesapp) has "Save" and "Save and add
 * another", with the view's filter values filled in by the server.
 */
@Composable
fun RowForm(vm: NotesViewModel, tableId: String, rowId: String?) {
    val repo = vm.repo
    val table by remember(tableId) { repo.table(tableId) }.collectAsStateWithLifecycle(null)
    val t = table ?: return
    val current by remember(rowId) { if (rowId != null) repo.row(rowId) else kotlinx.coroutines.flow.flowOf(null) }.collectAsStateWithLifecycle(null)
    var tab by remember { mutableStateOf(0) }
    var menu by remember { mutableStateOf(false) }
    val isNew = rowId == null
    var draft by remember(rowId) { mutableStateOf(mapOf<String, JsonElement>()) }
    val readOnly = !t.canEdit || t.deletedAt != null
    Scaffold(topBar = {
        TopAppBar(navigationIcon = { BackButton(vm) }, title = {
            Text(if (isNew) stringResource(R.string.notes_new_row) else current?.row?.let { r -> Cells.primary(t)?.let { Cells.text(it, r.cells[it.id]) } }?.ifBlank { null } ?: stringResource(R.string.notes_row),
                maxLines = 1, overflow = TextOverflow.Ellipsis)
        }, actions = {
            if (!isNew && !readOnly) Box {
                IconButton(onClick = { menu = true }) { Icon(Icons.Outlined.MoreVert, stringResource(R.string.notes_row_options)) }
                DropdownMenu(menu, { menu = false }) {
                    // ACT row.duplicate @android.notes @android.notesapp
                    DropdownMenuItem({ Text(stringResource(R.string.notes_duplicate)) }, { menu = false; vm.act { repo.duplicateRow(tableId, rowId!!) } })
                    DropdownMenuItem({ Text(stringResource(R.string.notes_delete)) }, { menu = false; vm.act { repo.deleteRows(tableId, listOf(rowId!!)) }; vm.back() })
                }
            }
        })
    }) { pad ->
        Column(Modifier.padding(pad).fillMaxSize().imePadding()) {
            if (!isNew) TabRow(tab) {
                Tab(tab == 0, { tab = 0 }, text = { Text(stringResource(R.string.notes_fields)) })
                Tab(tab == 1, { tab = 1 }, text = { Text(stringResource(R.string.notes_history)) })
            }
            if (tab == 1 && rowId != null) RowHistoryView(vm, t, rowId)
            else Column(Modifier.verticalScroll(rememberScrollState()).padding(16.dp).testTag("row-form"),
                verticalArrangement = Arrangement.spacedBy(12.dp)) {
                current?.conflict?.let { RowConflict(vm, rowId!!) }
                t.columns.forEach { c ->
                    val value = if (isNew) draft[c.id] else current?.row?.cells?.get(c.id)
                    CellField(c, value, current?.row?.raw?.get(c.id), readOnly, live = isNew) { v, parse ->
                        if (isNew) draft = draft + (c.id to (v ?: JsonNull))
                        else vm.act { repo.editRow(tableId, rowId!!, JsonObject(mapOf(c.id to (v ?: JsonNull))), parse = parse) }
                    }
                }
                if (isNew && !readOnly) Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    fun save(again: Boolean) {
                        val cells = JsonObject(draft.filterValues { it !is JsonNull })
                        vm.act {
                            val (_, h) = repo.addRow(tableId, cells, vm.tableFilters[tableId].orEmpty())
                            h
                        }
                        draft = emptyMap()
                        if (!again) vm.back()
                    }
                    Button(onClick = { save(false) }, modifier = Modifier.testTag("row-save")) { Text(stringResource(R.string.notes_save)) }
                    OutlinedButton(onClick = { save(true) }) { Text(stringResource(R.string.notes_save_and_add_another)) }
                }
                current?.row?.let { r ->
                    val by = r.updatedBy?.name ?: r.createdBy?.name
                    if (by != null) Text(stringResource(R.string.notes_last_change_by, by), style = MaterialTheme.typography.bodySmall)
                }
            }
        }
    }
}

/**
 * One field: text and long text, number and currency (numeric keyboard), date and date-time (typed:
 * "today", "fri", "12 oct", read by the server), checkbox, select and multi-select (option menus),
 * link (shown; edited on the web). [onSave] gets the value and whether the server reads it as typed.
 */
@Composable
private fun CellField(c: TableColumn, value: JsonElement?, raw: JsonElement?, readOnly: Boolean, live: Boolean = false,
                      onSave: (JsonElement?, Boolean) -> Unit) {
    when (c.type) {
        "checkbox" -> Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.heightIn(min = 48.dp)) {
            val on = (value as? JsonPrimitive)?.booleanOrNull == true
            Checkbox(on, { onSave(if (it) JsonPrimitive(true) else null, false) }, enabled = !readOnly,
                modifier = Modifier.semantics { contentDescription = c.name })
            Text(c.name)
        }
        "select", "multi_select" -> OptionField(c, value, readOnly, onSave)
        "link" -> Column {
            Text(c.name, style = MaterialTheme.typography.labelLarge)
            Text(Cells.text(c, value).ifBlank { stringResource(R.string.notes_none) }, style = MaterialTheme.typography.bodyMedium)
            Text(stringResource(R.string.notes_links_on_web), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        else -> {
            val initial = when (c.type) {
                "datetime" -> (value as? JsonObject)?.get("at")?.jsonPrimitive?.contentOrNull?.replace('T', ' ').orEmpty()
                else -> (value as? JsonPrimitive)?.contentOrNull.orEmpty()
            }
            var text by remember(c.id, initial) { mutableStateOf(initial) }
            var focused by remember { mutableStateOf(false) }
            fun commit() {
                if (!live && text != initial) onSave(if (text.isBlank()) null else JsonPrimitive(text), true)
            }
            val long = (c.config["long"] as? JsonPrimitive)?.booleanOrNull == true
            val label = when (c.type) {
                "currency" -> c.name + ((c.config["currency"] as? JsonPrimitive)?.contentOrNull?.let { " ($it)" } ?: "")
                else -> c.name
            } + if (c.required) " *" else ""
            OutlinedTextField(text, { text = it; if (live) onSave(if (it.isBlank()) null else JsonPrimitive(it), true) },
                readOnly = readOnly, label = { Text(label) },
                singleLine = !long, minLines = if (long) 3 else 1,
                placeholder = when (c.type) {
                    "date" -> ({ Text(dateHint()) })
                    "datetime" -> ({ Text("fri 3pm") }) // i18n-ignore: parser keyword example (the date grammar reads English)
                    else -> null
                },
                supportingText = (raw as? JsonPrimitive)?.contentOrNull?.let { r -> { Text(stringResource(R.string.notes_kept_as_text, r)) } },
                keyboardOptions = KeyboardOptions(keyboardType = if (c.type == "number" || c.type == "currency") KeyboardType.Decimal else KeyboardType.Text,
                    imeAction = if (long) ImeAction.Default else ImeAction.Done),
                keyboardActions = KeyboardActions(onDone = { commit() }),
                modifier = Modifier.fillMaxWidth().testTag("field-" + c.name)
                    .onFocusChanged { if (focused && !it.isFocused) commit(); focused = it.isFocused })
        }
    }
}

@Composable
private fun OptionField(c: TableColumn, value: JsonElement?, readOnly: Boolean, onSave: (JsonElement?, Boolean) -> Unit) {
    val opts = Cells.options(c)
    var open by remember { mutableStateOf(false) }
    val chosen: Set<String> = when (value) {
        is JsonArray -> value.mapNotNull { (it as? JsonPrimitive)?.contentOrNull }.toSet()
        is JsonPrimitive -> setOfNotNull(value.contentOrNull)
        else -> emptySet()
    }
    Column {
        Text(c.name, style = MaterialTheme.typography.labelLarge)
        Box {
            TextButton(onClick = { open = true }, enabled = !readOnly, modifier = Modifier.heightIn(min = 48.dp)) {
                Text(Cells.text(c, value).ifBlank { stringResource(R.string.notes_none) })
            }
            DropdownMenu(open, { open = false }) {
                if (c.type == "select") {
                    DropdownMenuItem({ Text(stringResource(R.string.notes_none)) }, { open = false; onSave(null, false) })
                    opts.forEach { o -> DropdownMenuItem({ Text(o.name) }, { open = false; onSave(JsonPrimitive(o.id), false) },
                        modifier = Modifier.semantics { selected = o.id in chosen }) }
                } else {
                    opts.forEach { o ->
                        DropdownMenuItem({ Text(o.name) }, {
                            val next = if (o.id in chosen) chosen - o.id else chosen + o.id
                            onSave(JsonArray(opts.filter { it.id in next }.map { JsonPrimitive(it.id) }), false)
                        }, leadingIcon = { Checkbox(o.id in chosen, null) })
                    }
                }
            }
        }
    }
}

@Composable
private fun RowConflict(vm: NotesViewModel, rowId: String) {
    Surface(color = MaterialTheme.colorScheme.errorContainer, shape = RoundedCornerShape(8.dp), modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(8.dp)) {
            Text(stringResource(R.string.notes_row_conflict), color = MaterialTheme.colorScheme.onErrorContainer)
            Row {
                TextButton(onClick = { vm.run { vm.repo.resolve(rowId, NotesRepository.Choice.THEIRS) } }) { Text(stringResource(R.string.notes_keep_theirs)) }
                TextButton(onClick = { vm.run { vm.repo.resolve(rowId, NotesRepository.Choice.MINE) } }) { Text(stringResource(R.string.notes_use_mine)) }
            }
        }
    }
}

/**
 * The row's history (ACT row.history @android.notes @android.notesapp): each change with its author and time; "Restore the row to this
 * point", and per cell its earlier values with "Use this value" (ACT row.restore_version @android.notes @android.notesapp). Online.
 */
@Composable
private fun RowHistoryView(vm: NotesViewModel, t: DataTable, rowId: String) {
    var h by remember(rowId) { mutableStateOf<RowHistory?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    LaunchedEffect(rowId) {
        try { h = vm.repo.rowHistory(t.id, rowId) } catch (e: CancellationException) { throw e } catch (e: Exception) { error = e.notesMessage() }
    }
    LazyColumn(contentPadding = PaddingValues(16.dp), modifier = Modifier.fillMaxSize().testTag("row-history")) {
        error?.let { item { Text(it, color = MaterialTheme.colorScheme.error) } }
        val hist = h
        if (hist == null && error == null) item { Text(stringResource(R.string.notes_loading)) }
        hist?.changes?.forEach { c ->
            item(key = "c${c.seq}") {
                Column(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
                    Text(stringResource(R.string.notes_change_line, c.who ?: stringResource(R.string.notes_someone), c.at?.take(16)?.replace('T', ' ') ?: ""), fontWeight = FontWeight.Medium)
                    c.summary?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
                    if (t.canEdit) TextButton(onClick = {
                        // The composition's own scope: the list it writes goes with the screen.
                        scope.launch {
                            try {
                                val u = vm.repo.restoreRowVersion(t.id, rowId, c.seq)
                                vm.say(AppText.get(R.string.notes_row_restored), u?.let { undo -> { vm.repo.sendUndo(t.id, undo) } })
                                h = vm.repo.rowHistory(t.id, rowId)
                            } catch (e: CancellationException) { throw e } catch (e: Exception) { vm.say(e.notesMessage()) }
                        }
                    }) { Text(stringResource(R.string.notes_restore_the_row_to_this_point)) }
                }
                HorizontalDivider()
            }
        }
        hist?.values?.forEach { (col, values) ->
            val c = t.columns.firstOrNull { it.id == col } ?: return@forEach
            if (values.size < 2) return@forEach
            item(key = "v$col") {
                Text(stringResource(R.string.notes_earlier_values_of, c.name), style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(top = 12.dp))
            }
            values.drop(1).forEach { v ->
                item(key = "v$col-${v.seq}") {
                    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)) {
                        Column(Modifier.weight(1f)) {
                            Text(v.text ?: Cells.text(c, v.value).ifBlank { stringResource(R.string.notes_empty_value) })
                            Text(stringResource(R.string.notes_change_line, v.who ?: "", v.at?.take(16)?.replace('T', ' ') ?: ""), style = MaterialTheme.typography.bodySmall)
                        }
                        if (t.canEdit) TextButton(onClick = {
                            scope.launch {
                                try {
                                    val u = vm.repo.restoreRowVersion(t.id, rowId, v.seq, col)
                                    vm.say(AppText.get(R.string.notes_value_restored), u?.let { undo -> { vm.repo.sendUndo(t.id, undo) } })
                                } catch (e: CancellationException) { throw e } catch (e: Exception) { vm.say(e.notesMessage()) }
                            }
                        }) { Text(stringResource(R.string.notes_use_this_value)) }
                    }
                }
            }
        }
    }
}

/**
 * The example a date cell shows while empty, from the clock (the web's dateHint): "today, sat or 9 oct"
 * on Fri 2 Oct, three things you can type (today, tomorrow's weekday, the day a week ahead), so it
 * never names a stale date. The words are English because the date grammar reads English.
 */
fun dateHint(today: java.time.LocalDate = io.github.murtazanasir.mailr.time.AppClock.today()): String {
    val next = today.plusDays(1).dayOfWeek.getDisplayName(java.time.format.TextStyle.SHORT, java.util.Locale.ENGLISH).lowercase()
    val week = today.plusDays(7)
    val month = week.month.getDisplayName(java.time.format.TextStyle.SHORT, java.util.Locale.ENGLISH).lowercase()
    return "today, $next or ${week.dayOfMonth} $month" // i18n-ignore: parser keyword example (the date grammar reads English)
}
