package io.github.murtazanasir.mailr.notes.ui

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.notes.R
import io.github.murtazanasir.mailr.i18n.AppText
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import io.github.murtazanasir.mailr.notes.data.DataTable
import io.github.murtazanasir.mailr.notes.data.Item
import io.github.murtazanasir.mailr.notes.data.Label
import io.github.murtazanasir.mailr.notes.data.Note
import io.github.murtazanasir.mailr.notes.data.NotesRepository
import io.github.murtazanasir.mailr.notes.data.OpEntity
import io.github.murtazanasir.mailr.notes.data.OpState
import io.github.murtazanasir.mailr.notes.data.Section
import io.github.murtazanasir.mailr.notes.data.notesMessage
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import java.util.Locale

/** The Notes views. */
sealed interface NotesView {
    val title: String
    data object All : NotesView { override val title get() = AppText.get(R.string.notes_view_all) }
    data object Lists : NotesView { override val title get() = AppText.get(R.string.notes_view_lists) }
    data object Tables : NotesView { override val title get() = AppText.get(R.string.notes_view_tables) }
    data class Label(val name: String) : NotesView { override val title get() = name }
    data object Shared : NotesView { override val title get() = AppText.get(R.string.notes_view_shared) }
    data object Archive : NotesView { override val title get() = AppText.get(R.string.notes_view_archive) }
    data object Trash : NotesView { override val title get() = AppText.get(R.string.notes_view_trash) }
}

/** Where the Notes destination is: the grid, a note, a table, a row, or the label editor. */
sealed interface NotesPage {
    data object Grid : NotesPage
    data class OpenNote(val id: String, val item: String? = null) : NotesPage
    data class OpenTable(val id: String) : NotesPage
    /** A row form; [rowId] null for a new row. */
    data class OpenRow(val tableId: String, val rowId: String?) : NotesPage
    data object EditLabels : NotesPage
}

/** A card of the grid. */
sealed interface Card {
    val id: String
    val title: String
    val color: String?
    val pinned: Boolean
    val labels: List<String>
    val pending: Boolean
    val order: String
    val updated: String

    data class NoteCard(val note: Note, val unchecked: List<Item>, val checkedCount: Int, val sections: Map<String, String>,
                        override val pending: Boolean) : Card {
        override val id get() = note.id
        override val title get() = note.title
        override val color get() = note.color
        override val pinned get() = note.state.pinned
        override val labels get() = note.state.labels
        override val order get() = note.state.order ?: ""
        override val updated get() = note.updatedAt ?: ""
    }

    data class TableCard(val table: DataTable, override val pending: Boolean) : Card {
        override val id get() = table.id
        override val title get() = table.name
        override val color get() = table.color
        override val pinned get() = table.state.pinned
        override val labels get() = table.state.labels
        override val order get() = table.state.order ?: ""
        override val updated get() = table.updatedAt ?: ""
    }
}

data class GridState(
    val cards: List<Card> = emptyList(),
    val labels: List<Label> = emptyList(),
    val pending: Int = 0,
    val failed: List<OpEntity> = emptyList(),
    val offline: Boolean = false,
    val syncedAt: Long? = null,
    val loaded: Boolean = false,
)

/** A snackbar line with an optional Undo (shown for 7 s). */
data class NotesMessage(val text: String, val undo: (suspend () -> Unit)? = null)

class NotesViewModel(val repo: NotesRepository, private val clock: () -> Long = AppClock::millis) : ViewModel() {
    val view = MutableStateFlow<NotesView>(NotesView.All)
    val query = MutableStateFlow("")
    private val _stack = MutableStateFlow<List<NotesPage>>(listOf(NotesPage.Grid))
    val stack: StateFlow<List<NotesPage>> = _stack
    private val _messages = MutableSharedFlow<NotesMessage>(extraBufferCapacity = 8, onBufferOverflow = BufferOverflow.DROP_OLDEST)
    val messages: SharedFlow<NotesMessage> = _messages
    private val _refreshing = MutableStateFlow(false)
    val refreshing: StateFlow<Boolean> = _refreshing
    /** The active filters of each open table's view: a new row gets their values (row.add `view_filters`). */
    val tableFilters = java.util.concurrent.ConcurrentHashMap<String, List<kotlinx.serialization.json.JsonObject>>()
    /** Ids the server's search found for the current query (null: no server answer yet). */
    private val serverHits = MutableStateFlow<Set<String>?>(null)

    init {
        viewModelScope.launch { repo.notices.collect { _messages.tryEmit(NotesMessage(it)) } }
        viewModelScope.launch { repo.renamed.collect { (old, new) -> follow(old, new) } }
    }

    private val base = combine(repo.notes, repo.tables, repo.previews, repo.sectionsByNote, repo.ops) { notes, tables, previews, secs, ops ->
        Base(notes, tables, previews, secs, ops)
    }

    private data class Base(val notes: List<Note>, val tables: List<DataTable>, val previews: Map<String, List<Item>>,
                            val sections: Map<String, List<Section>>, val ops: List<OpEntity>)

    val grid: StateFlow<GridState> = combine(base, repo.labels, view, query, combine(repo.offline, repo.syncedAt, serverHits) { a, b, c -> Triple(a, b, c) }) { b, labels, v, q, (offline, synced, hits) ->
        val busy = b.ops.map { it.parent }.toSet()
        val cards = ArrayList<Card>()
        for (n in b.notes) {
            val items = b.previews[n.id].orEmpty()
            val secs = b.sections[n.id].orEmpty()
            val unchecked = io.github.murtazanasir.mailr.notes.data.Checklist.layout(items, secs, n.top ?: io.github.murtazanasir.mailr.notes.data.TopGroup(n.topOrderMode), true)
                .groups.flatMap { it.items }
            cards += Card.NoteCard(n, unchecked, items.count { it.checked }, secs.associate { it.id to it.name }, n.id in busy)
        }
        for (t in b.tables) cards += Card.TableCard(t, t.id in busy)
        val shown = cards.filter { c -> inView(c, v) && matches(c, q, b.previews, hits) }
        val sorted = if (v == NotesView.Trash) shown.sortedByDescending { deletedAt(it) }
        else shown.sortedWith(compareByDescending<Card> { it.pinned && v != NotesView.Archive }.thenBy { it.order.ifEmpty { "￿" } }
            .thenByDescending { it.updated })
        GridState(sorted, labels.map { l -> l.copy(count = cards.count { c -> !deleted(c) && l.name in c.labels }) },
            b.ops.size, b.ops.filter { it.state != OpState.QUEUED }, offline, synced, loaded = true)
    }.stateIn(viewModelScope, SharingStarted.Eagerly, GridState())

    private fun deleted(c: Card) = when (c) {
        is Card.NoteCard -> c.note.deletedAt != null
        is Card.TableCard -> c.table.deletedAt != null
    }

    private fun deletedAt(c: Card) = when (c) {
        is Card.NoteCard -> c.note.deletedAt ?: ""
        is Card.TableCard -> c.table.deletedAt ?: ""
    }

    private fun inView(c: Card, v: NotesView): Boolean {
        val archived = when (c) {
            is Card.NoteCard -> c.note.state.archived
            is Card.TableCard -> c.table.state.archived
        }
        if (v == NotesView.Trash) return deleted(c)
        if (deleted(c)) return false
        return when (v) {
            NotesView.All -> !archived
            NotesView.Lists -> !archived && c is Card.NoteCard && c.note.isList
            NotesView.Tables -> !archived && c is Card.TableCard
            is NotesView.Label -> !archived && v.name in c.labels
            NotesView.Shared -> when (c) {
                is Card.NoteCard -> c.note.level != "owner" || c.note.owner?.kind == "group"
                is Card.TableCard -> c.table.level != "owner" || c.table.owner?.kind == "group"
            }
            NotesView.Archive -> archived
            NotesView.Trash -> false
        }
    }

    private fun matches(c: Card, q: String, previews: Map<String, List<Item>>, hits: Set<String>?): Boolean {
        val t = q.trim().lowercase(Locale.ROOT)
        if (t.isEmpty()) return true
        if (hits != null && c.id in hits) return true
        val text = when (c) {
            is Card.NoteCard -> c.note.title + "\n" + c.note.body + "\n" + previews[c.id].orEmpty().joinToString("\n") { it.text + " " + (it.extra ?: "") } +
                "\n" + c.sections.values.joinToString("\n")
            is Card.TableCard -> c.table.name + "\n" + c.table.description
        }
        return t.split(' ').filter { it.isNotBlank() }.all { text.lowercase(Locale.ROOT).contains(it) }
    }

    // ================================================================ navigation

    fun open(page: NotesPage) {
        _stack.value = _stack.value + when (page) {
            is NotesPage.OpenNote -> page.copy(id = repo.realId(page.id))
            is NotesPage.OpenTable -> page.copy(id = repo.realId(page.id))
            is NotesPage.OpenRow -> page.copy(tableId = repo.realId(page.tableId))
            else -> page
        }
    }

    /** Back: true when the Notes destination handled it. */
    fun back(): Boolean {
        if (_stack.value.size <= 1) return false
        _stack.value = _stack.value.dropLast(1)
        pruneHistories()
        return true
    }

    fun home() {
        _stack.value = listOf(NotesPage.Grid)
        pruneHistories()
    }

    fun show(v: NotesView) {
        view.value = v
        home()
        if (v == NotesView.Trash) viewModelScope.launch { loadTrash() }
    }

    /** ACT note.search @android.notes @android.notesapp: the server's full text (items, sections, table rows) joins the phone's own match. */
    fun search(q: String) {
        query.value = q
        serverHits.value = null
        if (q.isBlank()) return
        viewModelScope.launch {
            kotlinx.coroutines.delay(350)
            if (query.value != q) return@launch
            catching { repo.api.search(q) }.onSuccess { (n, t) -> if (query.value == q) serverHits.value = (n.map { it.id } + t.map { it.id }).toSet() }
        }
    }

    /** Trash is read from the server when it is opened (the phone keeps what it saw deleted). */
    private suspend fun loadTrash() {
        // The sync lists Trash by id; the cards come from GET /notes?view=trash. Nothing is stored here:
        // a note deleted on another device shows after the next sync marks it.
        catching { repo.pull() }
    }

    /** Pull to refresh: send what waits, then read what changed. */
    fun refresh() {
        viewModelScope.launch {
            _refreshing.value = true
            try {
                repo.drain()
                if (!repo.pull()) say(AppText.get(R.string.notes_offline_changes_wait))
            } finally {
                _refreshing.value = false
            }
        }
    }

    // ================================================================ messages and actions

    fun say(text: String, undo: (suspend () -> Unit)? = null) {
        _messages.tryEmit(NotesMessage(text, undo))
    }

    /** Run a repository action; its undo handle becomes the snackbar's Undo. */
    fun act(block: suspend () -> NotesRepository.UndoHandle?) {
        viewModelScope.launch {
            try {
                val h = block() ?: return@launch
                say(h.label, h::undo)
            } catch (e: CancellationException) { throw e } catch (e: Exception) {
                say(e.notesMessage())
            }
        }
    }

    // ---------------------------------------------------------------- the editor's undo and redo

    private val histories = HashMap<String, NoteHistory>()

    /** The open note's history (NoteHistory.kt), kept while the note is on the page stack. */
    fun history(noteId: String): NoteHistory = histories.getOrPut(noteId) { NoteHistory(noteId, clock) }

    private fun pruneHistories() {
        val open = _stack.value.mapNotNull { (it as? NotesPage.OpenNote)?.id }.toSet()
        histories.keys.retainAll(open)
    }

    /**
     * A change made in the editor of [noteId] ([act] with the editor's history): the change is a step
     * of the editor's Undo and Redo, and the snackbar's Undo goes through the history too. Redo runs
     * [block] again.
     */
    fun actIn(noteId: String, block: suspend () -> NotesRepository.UndoHandle?) {
        viewModelScope.launch {
            try {
                val h = block() ?: return@launch
                val hist = history(noteId)
                val step = hist.recordOp(h.label, h, block)
                say(h.label) { hist.undoStep(step) { say(it) } }
            } catch (e: CancellationException) { throw e } catch (e: Exception) {
                say(e.notesMessage())
            }
        }
    }

    // ACT note.undo_edit @android.notes @android.notesapp
    fun undoEdit(noteId: String) {
        viewModelScope.launch { history(noteId).undo { say(it) } }
    }

    // ACT note.redo_edit @android.notes @android.notesapp
    fun redoEdit(noteId: String) {
        viewModelScope.launch { history(noteId).redo { say(it) } }
    }

    fun run(block: suspend () -> Unit) {
        viewModelScope.launch {
            try {
                block()
            } catch (e: CancellationException) { throw e } catch (e: Exception) {
                say(e.notesMessage())
            }
        }
    }

    /** ACT note.create @android.notes @android.notesapp: a new note or list opens at once, from the phone's copy. */
    fun newNote(kind: String) {
        viewModelScope.launch {
            val v = view.value
            val (id, _) = repo.createNote(kind, labels = (v as? NotesView.Label)?.let { listOf(it.name) }.orEmpty())
            open(NotesPage.OpenNote(id))
        }
    }

    /** ACT table.create @android.notes @android.notesapp */
    fun newTable(name: String, template: String?) {
        viewModelScope.launch {
            val (id, _) = repo.createTable(name.ifBlank { AppText.get(R.string.notes_untitled_table) }, template)
            open(NotesPage.OpenTable(id))
        }
    }

    /** A note or table created offline has a temporary id until the server names it: follow it. */
    fun follow(old: String, new: String) {
        histories.remove(old)?.let { histories[new] = it }
        _stack.value = _stack.value.map { p ->
            when (p) {
                is NotesPage.OpenNote -> if (p.id == old) p.copy(id = new) else p
                is NotesPage.OpenTable -> if (p.id == old) p.copy(id = new) else p
                is NotesPage.OpenRow -> if (p.tableId == old) p.copy(tableId = new) else p
                else -> p
            }
        }
    }

    fun retry(op: OpEntity) = run { repo.retry(op) }
    fun discard(op: OpEntity) = run { repo.discard(op) }

    fun now(): Long = clock()
}
