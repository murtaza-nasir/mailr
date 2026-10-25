package io.github.murtazanasir.mailr.notes.data

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.time.Timeline
import io.github.murtazanasir.mailr.notes.R
import io.github.murtazanasir.mailr.i18n.AppText
import androidx.room.withTransaction
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import java.io.IOException
import java.time.Instant

/**
 * The phone's notes and tables, offline first: the Room copy, the delta sync, the item-level replay queue
 * and the live updates. Every write is applied to Room at once and queued; [drain] sends the queue
 * in order, each write with its own idempotency key and, where the server merges, the version it
 * was made from (`base_version`), so a check made with no signal lands later without a conflict.
 * Checks carry the device time of the tap (`device_at`): the server keeps the later tap.
 *
 * An entity with a queued write is not overwritten by a sync; once its writes are through, the
 * note or table is read again from the server.
 */
class NotesRepository(
    val api: NotesApi,
    private val database: NotesDatabase,
    private val scope: CoroutineScope,
    /** "Now" and the timers whose length the user sees (ticks, presence); tests pass a virtual one. */
    private val time: Timeline = Timeline.Real,
) {
    private fun clock(): Long = time.now()
    private val dao = database.notesDao()
    private val json = NotesJson
    val clientId: String get() = api.http.clientId

    /** Called after a write is queued (the host schedules [drain] for when there is a network). */
    var onQueued: (() -> Unit)? = null

    // ================================================================ reading

    /** Every note on the phone, Trash included (`deletedAt`). */
    val notes: Flow<List<Note>> = dao.notes().map { rows -> rows.mapNotNull { decodeNote(it) } }

    val tables: Flow<List<DataTable>> = dao.tables().map { rows -> rows.mapNotNull { decodeTable(it) } }

    val labels: Flow<List<Label>> = dao.labels().map { rows ->
        rows.mapNotNull { catching { json.decodeFromString(Label.serializer(), it.json) }.getOrNull() }
            .sortedWith(compareBy({ it.order ?: "" }, { it.name.lowercase() }))
    }

    /** The queue: what the banner counts ("Offline · 3 changes waiting") and the dashed edges mark. */
    val ops: Flow<List<OpEntity>> = dao.ops()

    /** When the last sync with the server finished ("Synced 6 min ago"). */
    val syncedAt: Flow<Long?> = dao.kvFlow(KV_SYNCED_AT).map { it?.toLongOrNull() }

    private val _offline = MutableStateFlow(false)
    /** The last request failed for lack of a network. */
    val offline: StateFlow<Boolean> = _offline

    /** "Dana is editing": the latest author of someone else's write, per note or table, with the time it arrived. */
    private val _editing = MutableStateFlow<Map<String, Pair<String, Long>>>(emptyMap())
    val editing: StateFlow<Map<String, Pair<String, Long>>> = _editing

    /** Temporary ids the server has named since the app started (old → new): open screens follow them. */
    private val aliases = java.util.concurrent.ConcurrentHashMap<String, String>()
    private val _renamed = MutableSharedFlow<Pair<String, String>>(extraBufferCapacity = 16, onBufferOverflow = BufferOverflow.DROP_OLDEST)
    val renamed: SharedFlow<Pair<String, String>> = _renamed

    /** The id a temporary id became, or the id itself. */
    fun realId(id: String): String = aliases[id] ?: id

    /** Short notices for the screens ("Dana stopped sharing this note"). */
    private val _notices = MutableSharedFlow<String>(extraBufferCapacity = 8, onBufferOverflow = BufferOverflow.DROP_OLDEST)
    val notices: SharedFlow<String> = _notices

    data class ItemState(val item: Item, val pending: Boolean, val conflict: JsonObject?)

    data class NoteState(
        val note: Note,
        val items: List<ItemState>,
        val sections: List<Section>,
        val pending: Boolean,
        /** A refused or conflicting write of the note itself (title, body, convert). */
        val failed: OpEntity?,
    )

    fun note(id: String): Flow<NoteState?> =
        combine(dao.note(id), dao.items(id), dao.sections(id), dao.ops()) { n, items, secs, ops ->
            val note = n?.let { decodeNote(it) } ?: return@combine null
            val busy = ops.filter { it.parent == id }.map { it.target }.toSet()
            NoteState(
                note = note,
                items = items.filter { !it.removed }.mapNotNull { e ->
                    decodeItem(e)?.let { ItemState(it, e.id in busy, e.conflict?.let { c -> catching { json.parseToJsonElement(c).jsonObject }.getOrNull() }) }
                },
                sections = secs.filter { !it.removed }.mapNotNull { catching { json.decodeFromString(Section.serializer(), it.json) }.getOrNull() },
                pending = id in busy,
                failed = ops.firstOrNull { it.target == id && it.state != OpState.QUEUED },
            )
        }

    /** Preview items of every list (the grid's cards), by note id. */
    val previews: Flow<Map<String, List<Item>>> = combine(dao.allItems(), dao.ops()) { items, _ ->
        items.filter { !it.removed }.groupBy { it.noteId }.mapValues { (_, v) -> v.mapNotNull { decodeItem(it) } }
    }

    val sectionsByNote: Flow<Map<String, List<Section>>> = dao.allSections().map { rows ->
        rows.filter { !it.removed }.groupBy { it.noteId }
            .mapValues { (_, v) -> v.mapNotNull { catching { json.decodeFromString(Section.serializer(), it.json) }.getOrNull() } }
    }

    fun table(id: String): Flow<DataTable?> = dao.table(id).map { it?.let { e -> decodeTable(e) } }

    data class RowState(val row: Row, val pending: Boolean, val conflict: JsonObject?)

    fun rows(tableId: String): Flow<List<RowState>> = combine(dao.rows(tableId), dao.ops()) { rows, ops ->
        val busy = ops.filter { it.parent == tableId }.map { it.target }.toSet()
        rows.filter { !it.removed }.mapNotNull { e ->
            catching { json.decodeFromString(Row.serializer(), e.json) }.getOrNull()?.let {
                RowState(it, e.id in busy, e.conflict?.let { c -> catching { json.parseToJsonElement(c).jsonObject }.getOrNull() })
            }
        }
    }

    fun row(id: String): Flow<RowState?> = combine(dao.rowFlow(id), dao.ops()) { e, ops ->
        e?.takeIf { !it.removed }?.let { r ->
            catching { json.decodeFromString(Row.serializer(), r.json) }.getOrNull()?.let {
                RowState(it, ops.any { o -> o.target == id }, r.conflict?.let { c -> catching { json.parseToJsonElement(c).jsonObject }.getOrNull() })
            }
        }
    }

    /** A table view's row order and footers from the last online read (the list offline). */
    fun viewPage(tableId: String, viewId: String?): Flow<RowsPage?> = dao.kvFlow(pageKey(tableId, viewId)).map { s ->
        s?.let { catching { json.decodeFromString(RowsPage.serializer(), it) }.getOrNull() }
    }

    suspend fun noteNow(id: String): Note? = dao.noteNow(id)?.let { decodeNote(it) }

    suspend fun tableNow(id: String): DataTable? = dao.tableNow(id)?.let { decodeTable(it) }

    suspend fun itemsNow(noteId: String): List<Item> = dao.itemsNow(noteId).filter { !it.removed }.mapNotNull { decodeItem(it) }

    suspend fun sectionsNow(noteId: String): List<Section> =
        dao.sectionsNow(noteId).filter { !it.removed }.mapNotNull { catching { json.decodeFromString(Section.serializer(), it.json) }.getOrNull() }

    // ================================================================ decoding and storing

    private fun decodeNote(e: NoteEntity): Note? = catching { json.decodeFromString(Note.serializer(), e.json) }.getOrNull()
    private fun decodeItem(e: ItemEntity): Item? = catching { json.decodeFromString(Item.serializer(), e.json) }.getOrNull()
    private fun decodeTable(e: TableEntity): DataTable? = catching { json.decodeFromString(DataTable.serializer(), e.json) }.getOrNull()
    private fun decodeRow(e: RowEntity): Row? = catching { json.decodeFromString(Row.serializer(), e.json) }.getOrNull()

    private fun noteEntity(n: Note) = NoteEntity(n.id, json.encodeToString(Note.serializer(), n.copy(items = null, sections = null)),
        deleted = n.deletedAt != null)

    private fun itemEntity(noteId: String, i: Item) = ItemEntity(i.id, noteId, json.encodeToString(Item.serializer(), i))
    private fun sectionEntity(noteId: String, s: Section) = SectionEntity(s.id, noteId, json.encodeToString(Section.serializer(), s))
    private fun tableEntity(t: DataTable) = TableEntity(t.id, json.encodeToString(DataTable.serializer(), t.copy(preview = null)),
        deleted = t.deletedAt != null)
    private fun rowEntity(tableId: String, r: Row) = RowEntity(r.id, tableId, json.encodeToString(Row.serializer(), r.copy(table = null)))

    /** The targets with queued writes, by parent. */
    private suspend fun busy(): Map<String, Set<String>> =
        dao.opsNow().groupBy { it.parent }.mapValues { (_, v) -> v.map { it.target }.toSet() }

    /**
     * The server's copy of a note into Room. With [full] (a note with its items and sections) the
     * items and sections are replaced too; anything with a queued write keeps the phone's copy.
     */
    private suspend fun storeNote(n: Note, full: Boolean, busy: Map<String, Set<String>>) {
        val mine = busy[n.id].orEmpty()
        if (n.id !in mine) dao.putNotes(listOf(noteEntity(n)))
        if (!full || n.items == null) return
        val serverItems = n.items.associateBy { it.id }
        val local = dao.itemsNow(n.id)
        for (e in local) if (e.id !in serverItems && e.id !in mine) dao.deleteItem(e.id)
        dao.putItems(n.items.filter { it.id !in mine }.map { itemEntity(n.id, it) })
        val serverSecs = n.sections.orEmpty().associateBy { it.id }
        for (e in dao.sectionsNow(n.id)) if (e.id !in serverSecs && e.id !in mine) dao.deleteSection(e.id)
        dao.putSections(n.sections.orEmpty().filter { it.id !in mine }.map { sectionEntity(n.id, it) })
    }

    private suspend fun forgetNote(id: String) {
        dao.deleteNote(id)
        dao.deleteItemsOf(id)
        dao.deleteSectionsOf(id)
    }

    private suspend fun forgetTable(id: String) {
        dao.deleteTable(id)
        dao.deleteRowsOf(id)
    }

    // ================================================================ sync

    private val syncLock = Mutex()

    /**
     * The delta since the last sync (`GET /notes/sync`, `GET /tables/sync`) and the labels. What the
     * caller no longer sees (unshared, left, purged) leaves the phone; a note or table new to it
     * (a group joined) is read in full. False when the server could not be reached.
     */
    suspend fun pull(): Boolean = syncLock.withLock {
        try {
            pullNotes()
            pullTables()
            catching { storeLabels(api.labels()) }
            dao.putKv(NotesKv(KV_SYNCED_AT, clock().toString()))
            _offline.value = false
            true
        } catch (e: IOException) {
            _offline.value = true
            false
        } catch (e: NotesHttpException) {
            false
        }
    }

    private suspend fun pullNotes() {
        val since = dao.kv(KV_NOTES_SINCE)
        val d = api.notesSync(since)
        val missing = ArrayList<String>()
        database.room.withTransaction {
            val busy = busy()
            for (n in d.notes) storeNote(n, full = true, busy)
            for (id in d.trashed) {
                val e = dao.noteNow(id) ?: continue
                decodeNote(e)?.let { n -> dao.putNotes(listOf(noteEntity(n.copy(deletedAt = n.deletedAt ?: Instant.ofEpochMilli(clock()).toString())))) }
            }
            val vis = d.visible
            if (vis != null) {
                for (e in dao.allNotes()) {
                    if (e.deleted || e.id in vis || Checklist.isLocal(e.id) || busy.containsKey(e.id) || e.id in d.trashed) continue
                    forgetNote(e.id)
                }
                for ((id, level) in vis) {
                    val e = dao.noteNow(id)
                    if (e == null) { missing += id; continue }
                    val n = decodeNote(e) ?: continue
                    if (n.level != level && id !in busy) dao.putNotes(listOf(noteEntity(n.copy(level = level))))
                }
            }
        }
        if (missing.size > 30 && since != null) {
            dao.deleteKv(KV_NOTES_SINCE)
            return pullNotes()
        }
        for (id in missing) catching { api.note(id) }.onSuccess { n ->
            database.room.withTransaction { storeNote(n, full = true, busy()) }
        }
        dao.putKv(NotesKv(KV_NOTES_SINCE, d.now))
    }

    private suspend fun pullTables() {
        val since = dao.kv(KV_TABLES_SINCE)
        val d = api.tablesSync(since)
        val missing = ArrayList<String>()
        database.room.withTransaction {
            val busy = busy()
            for (t in d.tables) if (t.id !in busy[t.id].orEmpty()) dao.putTables(listOf(tableEntity(t)))
            for (r in d.rows) {
                val tid = r.table ?: continue
                if (r.id !in busy[tid].orEmpty()) dao.putRows(listOf(rowEntity(tid, r)))
            }
            for (g in d.deletedRows) if (g.id !in busy[g.table].orEmpty()) dao.deleteRow(g.id)
            for (id in d.trashed) {
                val e = dao.tableNow(id) ?: continue
                decodeTable(e)?.let { t -> dao.putTables(listOf(tableEntity(t.copy(deletedAt = t.deletedAt ?: Instant.ofEpochMilli(clock()).toString())))) }
            }
            val vis = d.visible
            if (vis != null) {
                for (e in dao.allTables()) {
                    if (e.deleted || e.id in vis || Checklist.isLocal(e.id) || busy.containsKey(e.id) || e.id in d.trashed) continue
                    forgetTable(e.id)
                }
                for ((id, level) in vis) {
                    val e = dao.tableNow(id)
                    if (e == null) { missing += id; continue }
                    val t = decodeTable(e) ?: continue
                    if (t.level != level && id !in busy) dao.putTables(listOf(tableEntity(t.copy(level = level))))
                }
            }
        }
        if (missing.isNotEmpty() && since != null) {
            // A table the phone has not seen (a group joined): read every table and row again.
            dao.deleteKv(KV_TABLES_SINCE)
            return pullTables()
        }
        dao.putKv(NotesKv(KV_TABLES_SINCE, d.now))
    }

    private suspend fun storeLabels(list: List<Label>) {
        database.room.withTransaction {
            if (dao.opsNow().any { it.kind == "label" }) return@withTransaction
            dao.clearLabels()
            dao.putLabels(list.map { LabelEntity(it.name, json.encodeToString(Label.serializer(), it)) })
        }
    }

    /** One note again from the server (an event named it, or its queued writes are through). */
    suspend fun refreshNote(id: String) {
        if (Checklist.isLocal(id)) return
        try {
            val n = api.note(id)
            database.room.withTransaction { storeNote(n, full = true, busy()) }
        } catch (e: NotesHttpException) {
            if (e.code == 404 && dao.opsFor(id) == 0) forgetNote(id)
        } catch (_: IOException) {
            _offline.value = true
        }
    }

    suspend fun refreshTable(id: String) {
        if (Checklist.isLocal(id)) return
        try {
            val t = api.table(id)
            if (id !in busy()[id].orEmpty()) dao.putTables(listOf(tableEntity(t)))
        } catch (e: NotesHttpException) {
            if (e.code == 404 && dao.opsFor(id) == 0) forgetTable(id)
        } catch (_: IOException) {
            _offline.value = true
        }
    }

    /**
     * The rows of one view as the server orders and totals them (`GET /tables/{id}/rows`), kept for
     * the list offline. [spec]: the viewer's unsaved changes (a filter cleared).
     */
    suspend fun loadRows(tableId: String, viewId: String?, spec: JsonObject? = null, q: String? = null): RowsPage? {
        if (Checklist.isLocal(tableId)) return null
        return try {
            val page = api.rows(tableId, viewId, spec, q)
            database.room.withTransaction {
                val busy = busy()[tableId].orEmpty()
                dao.putRows(page.rows.filter { it.id !in busy }.map { rowEntity(tableId, it) })
                if (spec == null && q.isNullOrBlank()) {
                    dao.putKv(NotesKv(pageKey(tableId, viewId), json.encodeToString(RowsPage.serializer(),
                        page.copy(rows = page.rows.map { Row(it.id) }))))
                }
            }
            _offline.value = false
            page
        } catch (_: IOException) {
            _offline.value = true
            null
        }
    }

    suspend fun refreshRow(tableId: String, rowId: String) {
        try {
            val r = api.row(tableId, rowId)
            if (rowId !in busy()[tableId].orEmpty()) {
                if (r.deletedAt != null) dao.deleteRow(rowId) else dao.putRows(listOf(rowEntity(tableId, r)))
            }
        } catch (e: NotesHttpException) {
            if (e.code == 404) dao.deleteRow(rowId)
        } catch (_: IOException) {
            _offline.value = true
        }
    }

    // ================================================================ live updates

    private val refreshJobs = HashMap<String, Job>()

    private fun later(key: String, block: suspend () -> Unit) {
        synchronized(refreshJobs) {
            refreshJobs[key]?.cancel()
            refreshJobs[key] = scope.launch {
                delay(300)
                catching { block() }
            }
        }
    }

    /**
     * A change event from the server's stream (`/events`). The phone's
     * own writes come back with its client id as `origin` and are skipped.
     */
    fun onEvent(type: String, data: JsonObject) {
        fun s(k: String) = (data[k] as? JsonPrimitive)?.contentOrNull
        when (type) {
            "item" -> {
                val kind = s("kind") ?: return
                if (kind !in setOf("note", "checkitem", "table", "row")) return
                if (s("origin") != null && s("origin") == clientId) return
                val deleted = (data["deleted"] as? JsonPrimitive)?.booleanOrNull == true
                val purged = (data["purged"] as? JsonPrimitive)?.booleanOrNull == true
                val parent = if (kind == "note" || kind == "checkitem") s("note") ?: s("id") else s("table") ?: s("id")
                parent ?: return
                val author = (data["author"] as? JsonObject)?.get("name")?.jsonPrimitive?.contentOrNull
                author?.let { who ->
                    _editing.value = _editing.value + (parent to (who to clock()))
                }
                // Someone else's tick: after the read below, the row shows her name for a moment.
                if (kind == "checkitem" && author != null && !deleted) s("id")?.let { tickWatch[it] = parent to author }
                when (kind) {
                    "note", "checkitem" -> later("n:$parent") {
                        if (kind == "note" && purged) forgetNote(parent) else refreshAndMarkTicks(parent)
                    }
                    "table" -> later("t:$parent") {
                        when {
                            purged -> forgetTable(parent)
                            (data["rows"] as? JsonPrimitive)?.booleanOrNull == true -> { refreshTable(parent); pull() }
                            else -> refreshTable(parent)
                        }
                    }
                    "row" -> {
                        val id = s("id") ?: return
                        later("r:$id") { if (deleted) { if (id !in busy()[parent].orEmpty()) dao.deleteRow(id) } else refreshRow(parent, id) }
                    }
                }
            }
            "notes" -> when (s("what")) {
                "unshared" -> {
                    val id = s("id") ?: return
                    val by = s("by_name")
                    scope.launch {
                        catching { if (s("item_kind") == "table") forgetTable(id) else forgetNote(id) }
                        _notices.tryEmit(if (by != null) AppText.get(R.string.notes_notice_stopped_sharing, by, s("name").orEmpty()) else AppText.get(R.string.notes_notice_no_longer_shared))
                    }
                }
                else -> later("pull") { pull() }
            }
            "open" -> later("pull") { pull() }
            "presence" -> onPresence(data)
        }
    }

    // ================================================================ presence and ticks

    /** Someone else on an open shared item: [id] their user id, [at] the time of their last "here". */
    data class Present(val id: Long?, val name: String, val at: Long)

    private val _presence = MutableStateFlow<Map<String, List<Present>>>(emptyMap())
    /** Who else has each note or table open, by item id; gone after [PRESENCE_TTL_MS]. */
    val presence: StateFlow<Map<String, List<Present>>> = _presence

    /** The names of the people in an item now ("Cleo is in this list"). */
    fun presentNow(id: String): List<String> =
        _presence.value[id].orEmpty().filter { clock() - it.at < PRESENCE_TTL_MS }.map { it.name }

    private val tickWatch = java.util.concurrent.ConcurrentHashMap<String, Pair<String, String>>()
    private val _ticks = MutableStateFlow<Map<String, Pair<String, Long>>>(emptyMap())
    /** Items someone else just checked (item id → their name and the time), for a few seconds each. */
    val ticks: StateFlow<Map<String, Pair<String, Long>>> = _ticks

    /** Items this phone has open and announces (id → kind and the heartbeat). */
    private val here = java.util.concurrent.ConcurrentHashMap<String, Pair<String, Job>>()

    /**
     * ACT note.presence: a shared note, list or table opened here. The phone says "here" at once (with
     * `hello`, so the people already in it answer) and every 15 s while it stays open.
     */
    fun openPresence(kind: String, id: String) {
        if (Checklist.isLocal(id) || here.containsKey(id)) return
        here[id] = kind to scope.launch {
            var hello = true
            while (true) {
                sendPresence(kind, id, "here", hello)
                hello = false
                time.delay(PRESENCE_HEARTBEAT_MS)
            }
        }
    }

    /** The item closed here: the heartbeat stops and the others hear "left". */
    fun closePresence(id: String) {
        val (kind, job) = here.remove(id) ?: return
        job.cancel()
        scope.launch { sendPresence(kind, id, "left", false) }
    }

    private suspend fun sendPresence(kind: String, id: String, state: String, hello: Boolean) {
        val body = JsonObject(buildMap {
            put("state", JsonPrimitive(state))
            if (hello) put("hello", JsonPrimitive(true))
        })
        try {
            api.raw("POST", "${NotesApi.base(kind)}/${realId(id)}/presence", body)
        } catch (e: kotlinx.coroutines.CancellationException) {
            throw e
        } catch (_: Exception) {
            // Presence is a courtesy: offline or refused, nothing waits in the queue.
        }
    }

    private fun onPresence(data: JsonObject) {
        fun s(k: String) = (data[k] as? JsonPrimitive)?.contentOrNull
        if (s("origin") != null && s("origin") == clientId) return
        val id = s("id") ?: return
        val person = data["person"] as? JsonObject ?: return
        val pid = (person["id"] as? JsonPrimitive)?.longOrNull
        val name = (person["name"] as? JsonPrimitive)?.contentOrNull ?: return
        val others = _presence.value[id].orEmpty().filter { it.id != pid || (pid == null && it.name != name) }
        if (s("state") == "left") {
            _presence.value = (_presence.value + (id to others)).filterValues { it.isNotEmpty() }
            return
        }
        val now = clock()
        _presence.value = _presence.value + (id to (others + Present(pid, name, now)))
        scope.launch {
            time.delay(PRESENCE_TTL_MS + 50)
            val left = _presence.value[id].orEmpty().filter { clock() - it.at < PRESENCE_TTL_MS }
            _presence.value = (_presence.value + (id to left)).filterValues { it.isNotEmpty() }
        }
        // A newcomer's first signal: answer at once, so they see this phone without waiting for its next beat.
        val open = here[id]
        if ((data["hello"] as? JsonPrimitive)?.booleanOrNull == true && open != null) {
            scope.launch { sendPresence(open.first, id, "here", false) }
        }
    }

    /** A note read again after someone else's write; their checks of rows that were unchecked here are marked. */
    private suspend fun refreshAndMarkTicks(noteId: String) {
        val watched = tickWatch.filterValues { it.first == noteId }
        val before = watched.keys.associateWith { iid -> dao.item(iid)?.let { decodeItem(it) }?.checked }
        refreshNote(noteId)
        for ((iid, v) in watched) {
            tickWatch.remove(iid)
            val after = dao.item(iid)?.let { decodeItem(it) }?.checked
            if (before[iid] == false && after == true) markTick(iid, v.second)
        }
    }

    private fun markTick(itemId: String, name: String) {
        val at = clock()
        _ticks.value = _ticks.value + (itemId to (name to at))
        scope.launch {
            time.delay(TICK_MS)
            if (_ticks.value[itemId]?.second == at) _ticks.value = _ticks.value - itemId
        }
    }

    /** "Dana is editing": someone else's write in the last 10 seconds. */
    fun editingNow(id: String): String? = _editing.value[id]?.takeIf { clock() - it.second < 10_000 }?.first

    // ================================================================ the queue

    private val drainLock = Mutex()
    @Volatile private var inFlight: Long? = null
    /** The server's undo for each write sent in this process (op id → undo), for the 7 s toast. */
    private val serverUndo = HashMap<Long, Undo>()

    enum class DrainResult { DONE, RETRY, PAUSED }

    /**
     * An action's undo: a write still in the queue is taken out and the phone's copy put
     * back; a write the server has is reversed by the server's own undo, queued like any write.
     */
    inner class UndoHandle internal constructor(
        val label: String,
        private val opIds: List<Long>,
        private val restore: suspend () -> Unit,
    ) {
        suspend fun undo() {
            val queued = opIds.mapNotNull { id -> dao.op(id)?.takeIf { id != inFlight } }
            if (queued.size == opIds.size) {
                database.room.withTransaction {
                    queued.forEach { dao.deleteOp(it.id) }
                    restore()
                }
                return
            }
            for (id in opIds.reversed()) {
                val waiting = dao.op(id)?.takeIf { id != inFlight }
                if (waiting != null) {
                    dao.deleteOp(waiting.id)
                    continue
                }
                val u = synchronized(serverUndo) { serverUndo[id] } ?: continue
                val parent = opParent[id] ?: continue
                enqueue(u.method, u.path.trimStart('/'), u.body as? JsonObject, "undo", parent, parent, u.label.ifBlank { AppText.get(R.string.notes_op_undo) })
            }
            restore()
        }
    }

    private val opParent = HashMap<Long, String>()

    private suspend fun enqueue(method: String, path: String, body: JsonObject?, kind: String, target: String, parent: String,
                                label: String, creates: String? = null): Long {
        val id = dao.addOp(OpEntity(key = java.util.UUID.randomUUID().toString(), method = method, path = path,
            body = (body ?: JsonObject(emptyMap())).toString(), kind = kind, target = target, parent = parent, label = label,
            createdAt = clock(), creates = creates))
        synchronized(opParent) { opParent[id] = parent }
        return id
    }

    /**
     * A PATCH or PUT that can join a queued one for the same path: the fields merge, the first
     * write's `base_version` stays, so two edits made offline are one change for the server.
     */
    private suspend fun enqueuePatch(method: String, path: String, body: JsonObject, kind: String, target: String,
                                     parent: String, label: String): Long {
        val prev = dao.opsNow().lastOrNull { it.method == method && it.path == path && it.state == OpState.QUEUED }
        val old = prev?.let { catching { json.parseToJsonElement(it.body).jsonObject }.getOrNull() }
        if (prev != null && old != null && prev.id != inFlight && dao.opsNow().lastOrNull { it.parent == parent }?.id == prev.id &&
            (old["parse"] == null || body["parse"] == null || old["parse"] == body["parse"])) {
            val cells = (old["cells"] as? JsonObject)?.let { a -> (body["cells"] as? JsonObject)?.let { b -> JsonObject(a + b) } }
            val merged = JsonObject(old + body.filterKeys { it != "base_version" || "base_version" !in old } +
                (cells?.let { mapOf("cells" to it) } ?: emptyMap()))
            dao.updateOp(prev.copy(body = merged.toString(), label = label))
            return prev.id
        }
        return enqueue(method, path, body, kind, target, parent, label)
    }

    private fun queued() {
        onQueued?.invoke()
    }

    /**
     * Send the queue in order. A network failure stops it (RETRY); a 401 or a 426 (an app version
     * the server no longer takes) pauses it (PAUSED). A
     * refused write is marked FAILED and the server's copy read again; a 409 on an item, a row or a
     * note's text is a CONFLICT the user resolves (Keep theirs, Use mine, Keep both).
     */
    suspend fun drain(): DrainResult = drainLock.withLock {
        // After a server error the queue waits (holdUntil): every edit starts a drain, and without
        // the wait one failing write used its attempts within seconds and held up the queue meanwhile.
        if (clock() < holdUntil) return@withLock DrainResult.RETRY
        val dirtyNotes = LinkedHashSet<String>()
        val dirtyTables = LinkedHashSet<String>()
        try {
            while (true) {
                val op = dao.opsNow().firstOrNull { it.state == OpState.QUEUED } ?: break
                inFlight = op.id
                val body = catching { json.parseToJsonElement(op.body) }.getOrNull()
                try {
                    val res = api.raw(op.method, op.path, body?.takeIf { op.method != "GET" && op.method != "DELETE" || (it as? JsonObject)?.isNotEmpty() == true }, op.key)
                    database.room.withTransaction {
                        dao.deleteOp(op.id)
                        afterSuccess(op, res)
                    }
                    (res["undo"] as? JsonObject)?.let { u ->
                        catching { json.decodeFromJsonElement(Undo.serializer(), u) }.getOrNull()?.let {
                            synchronized(serverUndo) { serverUndo[op.id] = it }
                        }
                    }
                    _offline.value = false
                    holdUntil = 0L
                } catch (e: IOException) {
                    _offline.value = true
                    return@withLock DrainResult.RETRY
                } catch (e: NotesHttpException) {
                    when {
                        e.code == 401 -> return@withLock DrainResult.PAUSED
                        // This app version is too old for the server (426): the write waits, unsent and
                        // uncounted, until the app is updated.
                        e.code == 426 -> return@withLock DrainResult.PAUSED
                        e.retryable && op.attempts < 4 -> {
                            dao.updateOp(op.copy(attempts = op.attempts + 1, error = e.notesMessage()))
                            holdUntil = clock() + retryDelay(op.attempts + 1)
                            return@withLock DrainResult.RETRY
                        }
                        e.conflict && e.body != null && isLiveConflict(e.body) -> markConflict(op, e.body)
                        else -> dao.updateOp(op.copy(state = OpState.FAILED, attempts = op.attempts + 1, error = e.notesMessage()))
                    }
                } finally {
                    inFlight = null
                }
                if (op.parent.startsWith("t")) dirtyTables += op.parent else if (op.parent.startsWith("n")) dirtyNotes += op.parent
            }
        } finally {
            val left = dao.opsNow().map { it.parent }.toSet()
            for (id in dirtyNotes) if (id !in left) refreshNote(id)
            for (id in dirtyTables) if (id !in left) refreshTable(id)
        }
        DrainResult.DONE
    }

    /** When a drain may send again after a server error: 5 s, 10 s, 20 s, 40 s after attempts 1 to 4. */
    @Volatile private var holdUntil = 0L

    fun retryDelay(attempt: Int): Long = 5_000L shl (attempt - 1).coerceIn(0, 6)

    private fun isLiveConflict(body: String?): Boolean =
        body != null && catching { json.parseToJsonElement(body).jsonObject["conflict"]?.jsonPrimitive?.booleanOrNull }.getOrNull() == true

    private suspend fun markConflict(op: OpEntity, body: String) {
        dao.updateOp(op.copy(state = OpState.CONFLICT, error = AppText.get(R.string.notes_op_changed_elsewhere)))
        dao.item(op.target)?.let { dao.putItems(listOf(it.copy(conflict = body))) }
        dao.row(op.target)?.let { dao.putRows(listOf(it.copy(conflict = body))) }
        if (op.kind == "note") dao.putKv(NotesKv("conflict:${op.target}", body))
    }

    /** The server's answer to a write: ids it made replace the temporary ones, and the copy it returned is kept. */
    private suspend fun afterSuccess(op: OpEntity, res: JsonObject) {
        val made = op.creates
        if (made != null) {
            val real = when {
                made.startsWith("local-n-") -> (res["note"] as? JsonObject)?.get("id")?.jsonPrimitive?.contentOrNull
                made.startsWith("local-s-") -> (res["section"] as? JsonObject)?.get("id")?.jsonPrimitive?.contentOrNull
                made.startsWith("local-t-") -> (res["table"] as? JsonObject)?.get("id")?.jsonPrimitive?.contentOrNull
                else -> null
            }
            if (real != null) rewriteId(made, real)
            if (made.startsWith("local-n-") && real != null) {
                (res["note"] as? JsonObject)?.let { n ->
                    catching { json.decodeFromJsonElement(Note.serializer(), n) }.getOrNull()?.let { storeNote(it, full = true, busy()) }
                }
            }
            if (made.startsWith("local-t-") && real != null) {
                (res["table"] as? JsonObject)?.let { t ->
                    catching { json.decodeFromJsonElement(DataTable.serializer(), t) }.getOrNull()?.let { dao.putTables(listOf(tableEntity(it))) }
                }
            }
        }
        val more = dao.opsNow().filter { it.target == op.target }
        // A later queued write to the same item, row or note was made from the version this one changed.
        val version = ((res["item"] ?: res["row"] ?: res["note"] ?: res["table"]) as? JsonObject)?.get("version")?.jsonPrimitive?.longOrNull
        if (version != null) for (m in more) {
            val b = catching { json.parseToJsonElement(m.body).jsonObject }.getOrNull() ?: continue
            if ("base_version" in b) dao.updateOp(m.copy(body = JsonObject(b + ("base_version" to JsonPrimitive(version))).toString()))
        }
        if (more.isNotEmpty()) return
        (res["item"] as? JsonObject)?.let { it ->
            catching { json.decodeFromJsonElement(Item.serializer(), it) }.getOrNull()?.let { i -> dao.putItems(listOf(itemEntity(op.parent, i))) }
        }
        (res["row"] as? JsonObject)?.let { it ->
            catching { json.decodeFromJsonElement(Row.serializer(), it) }.getOrNull()?.let { r -> dao.putRows(listOf(rowEntity(op.parent, r))) }
        }
        if (op.kind == "delete-item") dao.deleteItem(op.target)
        if (op.kind == "delete-row") dao.deleteRow(op.target)
        if (op.kind == "purge") { forgetNote(op.target); forgetTable(op.target) }
    }

    /** A temporary id the server has now named: in the queue, in Room. */
    private suspend fun rewriteId(old: String, real: String) {
        aliases[old] = real
        _renamed.tryEmit(old to real)
        for (o in dao.opsNow()) {
            if (old !in o.path && old !in o.body && o.target != old && o.parent != old) continue
            dao.updateOp(o.copy(path = o.path.replace(old, real), body = o.body.replace(old, real),
                target = if (o.target == old) real else o.target, parent = if (o.parent == old) real else o.parent))
        }
        dao.noteNow(old)?.let { e ->
            dao.deleteNote(old)
            dao.putNotes(listOf(e.copy(id = real, json = e.json.replace(old, real))))
            dao.putItems(dao.itemsNow(old).map { it.copy(noteId = real) })
            dao.putSections(dao.sectionsNow(old).map { it.copy(noteId = real) })
        }
        dao.tableNow(old)?.let { e ->
            dao.deleteTable(old)
            dao.putTables(listOf(e.copy(id = real, json = e.json.replace(old, real))))
            dao.putRows(dao.rowsNow(old).map { it.copy(tableId = real) })
        }
        for (e in dao.allItemsNow()) if (e.json.contains(old)) dao.putItems(listOf(e.copy(json = e.json.replace(old, real))))
        for (s in dao.allSectionsNow()) if (s.id == old) {
            dao.deleteSection(old)
            dao.putSections(listOf(s.copy(id = real, json = s.json.replace(old, real))))
        }
    }

    /** Put a refused write back in the queue. */
    suspend fun retry(op: OpEntity) {
        dao.updateOp(op.copy(state = OpState.QUEUED, attempts = 0, error = null))
        queued()
    }

    /** Drop a refused write and take the server's copy. */
    suspend fun discard(op: OpEntity) {
        dao.deleteOp(op.id)
        dao.item(op.target)?.let { dao.putItems(listOf(it.copy(conflict = null))) }
        dao.row(op.target)?.let { dao.putRows(listOf(it.copy(conflict = null))) }
        dao.deleteKv("conflict:${op.target}")
        if (Checklist.isLocal(op.target) && op.creates != null) { forgetNote(op.target); forgetTable(op.target) }
        if (op.parent.startsWith("t")) refreshTable(op.parent) else refreshNote(op.parent)
    }

    enum class Choice { THEIRS, MINE, BOTH }

    /**
     * The conflict sheet on an item (Keep theirs, Use mine, Keep both) or a row (Keep theirs, Use mine):
     * the 409 carried the server's copy and its version.
     */
    suspend fun resolve(targetId: String, choice: Choice) {
        val op = dao.opsNow().firstOrNull { it.target == targetId && it.state == OpState.CONFLICT } ?: return
        val body = dao.item(targetId)?.conflict ?: dao.row(targetId)?.conflict ?: dao.kv("conflict:$targetId") ?: return
        val c = json.parseToJsonElement(body).jsonObject
        val theirs = c["theirs"]?.jsonObject
        val theirsVersion = theirs?.get("version")?.jsonPrimitive?.longOrNull
        val mine = catching { json.parseToJsonElement(op.body).jsonObject }.getOrDefault(JsonObject(emptyMap()))
        database.room.withTransaction {
            dao.item(targetId)?.let { dao.putItems(listOf(it.copy(conflict = null))) }
            dao.row(targetId)?.let { dao.putRows(listOf(it.copy(conflict = null))) }
            dao.deleteKv("conflict:$targetId")
            when (choice) {
                Choice.MINE -> dao.updateOp(op.copy(state = OpState.QUEUED, error = null,
                    body = JsonObject(mine + ("base_version" to (theirsVersion?.let(::JsonPrimitive) ?: JsonNull))).toString()))
                Choice.THEIRS, Choice.BOTH -> dao.deleteOp(op.id)
            }
        }
        if (choice == Choice.BOTH) {
            val text = (mine["text"] as? JsonPrimitive)?.contentOrNull
            if (text != null) addItems(op.parent, text, sectionId = dao.item(targetId)?.let { decodeItem(it) }?.sectionId, force = true)
        }
        if (choice != Choice.MINE) {
            if (op.parent.startsWith("t")) refreshRow(op.parent, targetId) else refreshNote(op.parent)
        }
        queued()
    }

    // ================================================================ note writes

    private suspend fun putNote(n: Note) = dao.putNotes(listOf(noteEntity(n)))

    private suspend fun requireNote(id: String): Note = dao.noteNow(id)?.let { decodeNote(it) } ?: error("no note $id")

    /** ACT note.create: a text note or a checklist (from lines, through the grammar), at once on the phone. */
    suspend fun createNote(kind: String, title: String = "", body: String = "", lines: String? = null, color: String? = null,
                           labels: List<String> = emptyList(), shopping: Boolean = false): Pair<String, UndoHandle> {
        val id = Checklist.localId("n")
        val now = Instant.ofEpochMilli(clock()).toString()
        val parsed = if (kind == "checklist" && !lines.isNullOrBlank())
            // A new list: "Costco: 2 milk" makes the section, as on the server.
            io.github.murtazanasir.mailr.notes.grammar.ItemGrammar.parse(lines, io.github.murtazanasir.mailr.notes.grammar.ItemGrammar.Mode.LIST, prefixCreates = true) else null
        val secIds = parsed?.newSections.orEmpty().associateWith { Checklist.localId("s") }
        val items = parsed?.items.orEmpty().mapIndexed { i, p ->
            Item(Checklist.uuid7(), sectionId = p.section?.let { secIds[it] }, text = p.text,
                key = io.github.murtazanasir.mailr.notes.grammar.ItemGrammar.itemKey(p.text), qty = p.qty?.let { Qty(it.amount, it.unit, it.text) },
                checked = p.checked, order = "a" + i.toString().padStart(4, '0'), addedAt = now)
        }
        val note = Note(id, kind = kind, title = title, body = body, color = color, shopping = shopping, suggestUsual = shopping,
            topOrderMode = if (shopping) "learned" else "manual", createdAt = now, updatedAt = now,
            state = ViewerState(labels = labels), counts = Counts(items = items.size))
        val req = JsonObject(buildMap {
            put("kind", JsonPrimitive(kind)); put("title", JsonPrimitive(title)); put("body", JsonPrimitive(body))
            color?.let { put("color", JsonPrimitive(it)) }
            if (labels.isNotEmpty()) put("labels", JsonArray(labels.map(::JsonPrimitive)))
            if (shopping) put("shopping", JsonPrimitive(true))
            if (!lines.isNullOrBlank()) put("lines", JsonPrimitive(lines))
        })
        val opId = database.room.withTransaction {
            putNote(note)
            dao.putSections(secIds.entries.mapIndexed { i, (name, sid) -> sectionEntity(id, Section(sid, name, order = "s$i")) })
            dao.putItems(items.map { itemEntity(id, it) })
            enqueue("POST", "notes", req, "note", id, id, if (kind == "checklist") AppText.get(R.string.notes_op_list_created) else AppText.get(R.string.notes_op_note_created), creates = id)
        }
        queued()
        return id to UndoHandle(AppText.get(R.string.notes_op_note_deleted), listOf(opId)) { forgetNote(id) }
    }

    /** ACT note.edit, ACT note.color: changed fields only, with the version they were made from. */
    suspend fun editNote(id: String, title: String? = null, body: String? = null, color: String? = null, clearColor: Boolean = false,
                         shopping: Boolean? = null, checkedAtBottom: Boolean? = null, suggestUsual: Boolean? = null,
                         topOrderMode: String? = null): UndoHandle {
        val before = requireNote(id)
        val after = before.copy(title = title ?: before.title, body = body ?: before.body,
            color = if (clearColor) null else color ?: before.color, shopping = shopping ?: before.shopping,
            checkedAtBottom = checkedAtBottom ?: before.checkedAtBottom, suggestUsual = suggestUsual ?: before.suggestUsual,
            topOrderMode = topOrderMode ?: before.topOrderMode, updatedAt = Instant.ofEpochMilli(clock()).toString(),
            top = topOrderMode?.let { (before.top ?: TopGroup()).copy(orderMode = it) } ?: before.top)
        val req = JsonObject(buildMap {
            before.version?.let { put("base_version", JsonPrimitive(it)) }
            title?.let { put("title", JsonPrimitive(it)) }
            body?.let { put("body", JsonPrimitive(it)) }
            if (clearColor) put("color", JsonPrimitive("")) else color?.let { put("color", JsonPrimitive(it)) }
            shopping?.let { put("shopping", JsonPrimitive(it)) }
            checkedAtBottom?.let { put("checked_at_bottom", JsonPrimitive(it)) }
            suggestUsual?.let { put("suggest_usual", JsonPrimitive(it)) }
            topOrderMode?.let { put("top_order_mode", JsonPrimitive(it)) }
        })
        val opId = database.room.withTransaction {
            putNote(after)
            enqueuePatch("PATCH", "notes/$id", req, "note", id, id, if (color != null || clearColor) AppText.get(R.string.notes_op_colour_changed) else AppText.get(R.string.notes_op_note_saved))
        }
        queued()
        return UndoHandle(if (color != null || clearColor) AppText.get(R.string.notes_op_colour_changed) else AppText.get(R.string.notes_op_edit_undone), listOf(opId)) { putNote(before) }
    }

    /** ACT note.pin, ACT note.archive, ACT note.label, ACT note.reorder (per viewer): `PUT /notes/{id}/state`, or `/tables/{id}/state`. */
    suspend fun setState(id: String, pinned: Boolean? = null, archived: Boolean? = null, labels: List<String>? = null,
                         before: String? = null, after: String? = null, label: String = AppText.get(R.string.notes_op_saved)): UndoHandle {
        val isTable = id.startsWith("t") || id.startsWith("local-t-")
        val req = JsonObject(buildMap {
            pinned?.let { put("pinned", JsonPrimitive(it)) }
            archived?.let { put("archived", JsonPrimitive(it)) }
            labels?.let { put("labels", JsonArray(it.map(::JsonPrimitive))) }
            before?.let { put("before", JsonPrimitive(it)) }
            after?.let { put("after", JsonPrimitive(it)) }
        })
        fun next(s: ViewerState): ViewerState {
            var n = s.copy(pinned = pinned ?: s.pinned, archived = archived ?: s.archived, labels = labels ?: s.labels)
            // Pinning unarchives; archiving unpins (PUT state).
            if (pinned == true) n = n.copy(archived = false)
            if (archived == true) n = n.copy(pinned = false)
            return n
        }
        var restore: suspend () -> Unit = {}
        val opId = database.room.withTransaction {
            if (isTable) {
                val t = dao.tableNow(id)?.let { decodeTable(it) } ?: error("no table $id")
                var st = next(t.state)
                if (before != null || after != null) st = st.copy(order = gridKey(before, after))
                dao.putTables(listOf(tableEntity(t.copy(state = st))))
                restore = { dao.putTables(listOf(tableEntity(t))) }
            } else {
                val n = requireNote(id)
                var st = next(n.state)
                if (before != null || after != null) st = st.copy(order = gridKey(before, after))
                putNote(n.copy(state = st))
                restore = { putNote(n) }
            }
            enqueuePatch("PUT", "${if (isTable) "tables" else "notes"}/$id/state", req, "state", id, id, label)
        }
        queued()
        return UndoHandle(label, listOf(opId)) { restore() }
    }

    /** An order key for the grid: just before [before], or just after [after] (the server's own key replaces it). */
    private suspend fun gridKey(before: String?, after: String?): String {
        val keys = (dao.allNotes().mapNotNull { decodeNote(it)?.state?.order } + dao.allTables().mapNotNull { decodeTable(it)?.state?.order }).sorted()
        suspend fun key(id: String): String? =
            (dao.noteNow(id)?.let { decodeNote(it) }?.state ?: dao.tableNow(id)?.let { decodeTable(it) }?.state)?.order
        if (before != null) {
            val k = key(before) ?: return Checklist.between(null, keys.firstOrNull())
            return Checklist.between(keys.lastOrNull { it < k }, k)
        }
        val k = after?.let { key(it) } ?: return Checklist.between(keys.lastOrNull(), null)
        return Checklist.between(k, keys.firstOrNull { it > k })
    }

    /**
     * ACT note.remind: this viewer's reminders on the note become [ats] (instants; an empty list
     * removes them), queued like any write. The keys are `n|<id>|abs|<instant>`, as every device plans them.
     */
    suspend fun setReminders(id: String, ats: List<Instant>): UndoHandle {
        val n = requireNote(id)
        val fmt = java.time.format.DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ssxxx")
        val canon = ats.distinct().sorted().map { java.time.OffsetDateTime.ofInstant(it, java.time.ZoneOffset.UTC).format(fmt) }
        val label = when {
            canon.isEmpty() -> AppText.get(R.string.notes_op_reminder_removed)
            canon.size == 1 -> AppText.get(R.string.notes_op_reminder_set)
            else -> AppText.plural(R.plurals.notes_op_reminders_set, canon.size, canon.size)
        }
        val opId = database.room.withTransaction {
            putNote(n.copy(reminders = canon.map { NoteReminder("n|$id|abs|$it", it) }, nextReminder = canon.firstOrNull()))
            enqueuePatch("PUT", "notes/$id/reminders", JsonObject(mapOf("at" to JsonArray(canon.map(::JsonPrimitive)))),
                "reminder", id, id, label)
        }
        queued()
        return UndoHandle(label, listOf(opId)) { putNote(n) }
    }

    /** The viewer's note reminders on the phone, with their notes (Trash left out), for the reminder planner. */
    suspend fun noteReminders(): List<Pair<Note, NoteReminder>> =
        dao.allNotes().filter { !it.deleted }.mapNotNull { decodeNote(it) }.flatMap { n -> n.reminders.map { n to it } }

    /** The templates (online): the starters, then the user's own. */
    suspend fun templates(): List<Template> = api.templates()

    /** ACT note.save_template: the note or list saved as one of the user's templates (online), named [name] or its title. */
    suspend fun saveTemplate(id: String, name: String?) {
        api.raw("POST", "notes/$id/save-template", JsonObject(buildMap { name?.takeIf { it.isNotBlank() }?.let { put("name", JsonPrimitive(it)) } }))
    }

    /** ACT note.delete_template: one of the user's own templates (online); the starters stay. */
    suspend fun deleteTemplate(templateId: String) {
        api.raw("DELETE", "notes/templates/$templateId")
    }

    /** ACT table.duplicate: a copy of the table with its rows (online); it shows after the read that follows. */
    suspend fun duplicateTable(id: String) {
        api.raw("POST", "tables/$id/duplicate", JsonObject(mapOf("rows" to JsonPrimitive(true))))
        pull()
    }

    /** ACT note.from_template: a note, list or table from a template (online); its id once stored here. */
    suspend fun fromTemplate(templateId: String, labels: List<String> = emptyList()): String? {
        val res = api.raw("POST", "notes/from-template", JsonObject(buildMap {
            put("template", JsonPrimitive(templateId))
            if (labels.isNotEmpty()) put("labels", JsonArray(labels.map(::JsonPrimitive)))
        }))
        (res["note"] as? JsonObject)?.let { o ->
            val n = json.decodeFromJsonElement(Note.serializer(), o)
            database.room.withTransaction { storeNote(n, full = n.items != null, busy()) }
            if (n.items == null) refreshNote(n.id)
            return n.id
        }
        (res["table"] as? JsonObject)?.let { o ->
            val t = json.decodeFromJsonElement(DataTable.serializer(), o)
            dao.putTables(listOf(tableEntity(t)))
            catching { pull() }
            return t.id
        }
        return null
    }

    /** ACT note.unlink: a link chip's ×, queued; the undo puts the link back. */
    suspend fun unlink(noteId: String, linkId: Long): UndoHandle {
        val n = requireNote(noteId)
        val opId = database.room.withTransaction {
            putNote(n.copy(links = n.links.filter { it.id != linkId }))
            enqueue("DELETE", "notes/$noteId/links/$linkId", null, "note", noteId, noteId, AppText.get(R.string.notes_op_link_removed))
        }
        queued()
        return UndoHandle(AppText.get(R.string.notes_op_link_removed), listOf(opId)) { putNote(n) }
    }

    /** The viewer opened a note someone else changed: the "changed since you looked" dot goes (PUT state, `seen`). */
    suspend fun markSeen(id: String) {
        val n = dao.noteNow(id)?.let { decodeNote(it) } ?: return
        if (!n.changed || Checklist.isLocal(id)) return
        database.room.withTransaction {
            putNote(n.copy(changed = false))
            enqueuePatch("PUT", "notes/$id/state", JsonObject(mapOf("seen" to JsonPrimitive(true))), "state", id, id, AppText.get(R.string.notes_op_seen))
        }
        queued()
    }

    /** The editor closed: the server ends the editing session, so the next change starts a new revision. */
    suspend fun checkpoint(id: String) {
        if (Checklist.isLocal(id) || dao.opsFor(id) > 0) return
        catching { api.raw("POST", "notes/$id/revisions/checkpoint", JsonObject(emptyMap())) }
    }

    /** ACT note.leave, ACT table.leave: a shared note or table leaves this viewer's notes (online). */
    suspend fun leave(id: String, isTable: Boolean): Undo? {
        val res = api.raw("DELETE", "${if (isTable) "tables" else "notes"}/$id/shares/me")
        if (isTable) forgetTable(id) else forgetNote(id)
        return (res["undo"] as? JsonObject)?.let { json.decodeFromJsonElement(Undo.serializer(), it) }
    }

    /** Undo of a leave: the share comes back (404 when it was stopped meanwhile). */
    suspend fun rejoin(id: String, isTable: Boolean) {
        api.raw("POST", "${if (isTable) "tables" else "notes"}/$id/shares/me", JsonObject(emptyMap()))
        if (isTable) { refreshTable(id); pull() } else refreshNote(id)
    }

    /** ACT note.to_checklist, ACT note.to_text: needs the current version; an older one is refused (409) and offered again. */
    suspend fun convert(id: String, to: String): UndoHandle {
        val n = requireNote(id)
        val req = JsonObject(buildMap {
            put("to", JsonPrimitive(to))
            n.version?.let { put("base_version", JsonPrimitive(it)) }
        })
        val opId = database.room.withTransaction {
            if (to == "checklist") {
                val parsed = io.github.murtazanasir.mailr.notes.grammar.ItemGrammar.parse(n.body, io.github.murtazanasir.mailr.notes.grammar.ItemGrammar.Mode.LIST)
                dao.putItems(parsed.items.mapIndexed { i, p ->
                    itemEntity(id, Item(Checklist.uuid7(), text = p.text, key = io.github.murtazanasir.mailr.notes.grammar.ItemGrammar.itemKey(p.text),
                        qty = p.qty?.let { Qty(it.amount, it.unit, it.text) }, checked = p.checked, order = "a" + i.toString().padStart(4, '0')))
                })
                putNote(n.copy(kind = "checklist", body = ""))
            } else {
                val items = dao.itemsNow(id).mapNotNull { decodeItem(it) }.sortedBy { it.order }
                putNote(n.copy(kind = "text", body = items.joinToString("\n") { (if (it.checked) "- [x] " else "- [ ] ") + it.text }))
                dao.deleteItemsOf(id)
            }
            enqueue("POST", "notes/$id/convert", req, "note", id, id, if (to == "checklist") AppText.get(R.string.notes_op_made_into_a_list) else AppText.get(R.string.notes_op_made_into_text))
        }
        queued()
        return UndoHandle(if (to == "checklist") AppText.get(R.string.notes_op_made_into_a_list) else AppText.get(R.string.notes_op_made_into_text), listOf(opId)) { refreshNote(id) }
    }

    /** ACT note.duplicate: the server makes the copy; it shows after the next read. */
    suspend fun duplicate(id: String): UndoHandle {
        val isTable = id.startsWith("t")
        val opId = enqueue("POST", "${if (isTable) "tables" else "notes"}/$id/duplicate",
            if (isTable) JsonObject(mapOf("rows" to JsonPrimitive(true))) else null, "note", id, id, AppText.get(R.string.notes_op_copy_made))
        queued()
        scope.launch { catching { drain(); pull() } }
        return UndoHandle(AppText.get(R.string.notes_op_copy_made), listOf(opId)) { pull() }
    }

    /** ACT note.delete, ACT table.delete: to Trash for 30 days. */
    suspend fun trash(id: String): UndoHandle {
        val isTable = id.startsWith("t") || id.startsWith("local-t-")
        val now = Instant.ofEpochMilli(clock()).toString()
        var restore: suspend () -> Unit = {}
        val opId = database.room.withTransaction {
            if (isTable) {
                val t = dao.tableNow(id)?.let { decodeTable(it) } ?: error("no table $id")
                dao.putTables(listOf(tableEntity(t.copy(deletedAt = now))))
                restore = { dao.putTables(listOf(tableEntity(t))) }
            } else {
                val n = requireNote(id)
                putNote(n.copy(deletedAt = now))
                restore = { putNote(n) }
            }
            enqueue("DELETE", "${if (isTable) "tables" else "notes"}/$id", null, "note", id, id, AppText.get(R.string.notes_op_moved_to_trash))
        }
        queued()
        return UndoHandle(AppText.get(R.string.notes_op_moved_to_trash), listOf(opId)) { restore() }
    }

    /** ACT note.restore, ACT table.restore: back from Trash. */
    suspend fun restore(id: String, isTable: Boolean): UndoHandle {
        val opId = database.room.withTransaction {
            if (isTable) dao.tableNow(id)?.let { decodeTable(it) }?.let { dao.putTables(listOf(tableEntity(it.copy(deletedAt = null)))) }
            else dao.noteNow(id)?.let { decodeNote(it) }?.let { putNote(it.copy(deletedAt = null)) }
            enqueue("POST", "${if (isTable) "tables" else "notes"}/$id/restore", null, "note", id, id, AppText.get(R.string.notes_op_restored))
        }
        queued()
        scope.launch { catching { drain(); if (isTable) { refreshTable(id); pull() } else refreshNote(id) } }
        return UndoHandle(AppText.get(R.string.notes_op_restored), listOf(opId)) { trash(id) }
    }

    /** ACT note.purge, ACT table.purge: delete forever (no undo). */
    suspend fun purge(id: String, isTable: Boolean) {
        database.room.withTransaction {
            if (isTable) forgetTable(id) else forgetNote(id)
            enqueue("DELETE", "${if (isTable) "tables" else "notes"}/$id?forever=true", null, "purge", id, id, AppText.get(R.string.notes_op_deleted_forever))
        }
        queued()
    }

    /** ACT note.purge ("Empty Trash"): notes and tables in Trash. */
    suspend fun emptyTrash() {
        database.room.withTransaction {
            dao.allNotes().filter { it.deleted }.forEach { forgetNote(it.id) }
            dao.allTables().filter { it.deleted }.forEach { forgetTable(it.id) }
            enqueue("POST", "notes/trash/empty", null, "purge", "trash", "trash", AppText.get(R.string.notes_op_trash_emptied))
        }
        queued()
    }

    /** ACT note.restore_version: online; the restore is itself a new version. */
    suspend fun restoreVersion(id: String, rev: Long): Undo? {
        val res = api.raw("POST", "notes/$id/revisions/$rev/restore", JsonObject(emptyMap()))
        refreshNote(id)
        return (res["undo"] as? JsonObject)?.let { json.decodeFromJsonElement(Undo.serializer(), it) }
    }

    /** Send a server undo now (the toast of an online action), then read the item again. */
    suspend fun sendUndo(parent: String, u: Undo) {
        enqueue(u.method, u.path.trimStart('/'), u.body as? JsonObject, "undo", parent, parent, u.label.ifBlank { AppText.get(R.string.notes_op_undo) })
        queued()
    }

    // ================================================================ Local models only and repeat

    /**
     * ACT note.local_only, ACT table.local_only: "Local models only" on one note, list or table, queued
     * like any edit. Anyone who can edit turns it on; only the owner turns it off (the server answers
     * an editor's "off" with a 403, which shows as a refused write).
     */
    suspend fun setLocalOnly(id: String, on: Boolean): UndoHandle {
        val isTable = id.startsWith("t") || id.startsWith("local-t-")
        val label = if (on) AppText.get(R.string.notes_op_local_models_only_on) else AppText.get(R.string.notes_op_local_models_only_off)
        var restore: suspend () -> Unit = {}
        val opId = database.room.withTransaction {
            if (isTable) {
                val t = dao.tableNow(id)?.let { decodeTable(it) } ?: error("no table $id")
                dao.putTables(listOf(tableEntity(t.copy(localOnly = on))))
                restore = { dao.putTables(listOf(tableEntity(t))) }
            } else {
                val n = requireNote(id)
                putNote(n.copy(localOnly = on))
                restore = { putNote(n) }
            }
            enqueuePatch("PATCH", "${if (isTable) "tables" else "notes"}/$id", JsonObject(mapOf("local_only" to JsonPrimitive(on))),
                if (isTable) "table" else "note", id, id, label)
        }
        queued()
        return UndoHandle(label, listOf(opId)) { restore() }
    }

    /**
     * ACT note.repeat: a list repeats weekly on a day or every N days ([rule] as [RepeatRule.format]),
     * or no longer (null). The phone plans the first reset as the server does; the server's answer
     * replaces it.
     */
    suspend fun setRepeat(id: String, rule: String?): UndoHandle {
        val n = requireNote(id)
        val parsed = RepeatRule.parse(rule)
        val next = parsed?.let { RepeatRule.first(it, Instant.ofEpochMilli(clock())).toString() }
        val label = if (parsed == null) AppText.get(R.string.notes_op_repeat_off) else RepeatRule.describe(parsed)
        val opId = database.room.withTransaction {
            putNote(n.copy(repeat = parsed?.let { NoteRepeat(RepeatRule.format(it), next, n.repeat?.last) }))
            enqueuePatch("PATCH", "notes/$id", JsonObject(mapOf("repeat" to (parsed?.let { JsonPrimitive(RepeatRule.format(it)) } ?: JsonNull))),
                "note", id, id, label)
        }
        queued()
        return UndoHandle(label, listOf(opId)) { putNote(n) }
    }

    // ================================================================ item writes

    private suspend fun requireItem(id: String): Pair<ItemEntity, Item> {
        val e = dao.item(id) ?: error("no item $id")
        return e to (decodeItem(e) ?: error("bad item $id"))
    }

    private suspend fun putItem(noteId: String, i: Item) = dao.putItems(listOf(itemEntity(noteId, i)))

    /** [duplicates]: texts already on the list with another unit ("Milk is already on the list · Add anyway"). */
    data class AddResult(val handle: UndoHandle, val added: Int, val merged: List<String>, val rechecked: List<String>,
                         val duplicates: List<String> = emptyList())

    /**
     * ACT checkitem.add, ACT note.add_items: lines through the grammar (list mode), shown at once;
     * duplicates merge as on the server (same key unchecked: the quantities add up with the same
     * unit; checked: it is unchecked with the new quantity). A header line makes a section.
     */
    suspend fun addItems(noteId: String, input: String, sectionId: String? = null, after: String? = null,
                         force: Boolean = false): AddResult? {
        val sections = sectionsNow(noteId)
        val parsed = io.github.murtazanasir.mailr.notes.grammar.ItemGrammar.parse(input, io.github.murtazanasir.mailr.notes.grammar.ItemGrammar.Mode.LIST,
            list = io.github.murtazanasir.mailr.notes.grammar.ItemGrammar.ListInfo(noteId, "", sections.map { it.name }))
        if (parsed.items.isEmpty() && parsed.newSections.isEmpty()) return null
        val now = Instant.ofEpochMilli(clock()).toString()
        val opIds = ArrayList<Long>()
        val snapshots = ArrayList<ItemEntity>()
        val addedIds = ArrayList<String>()
        val merged = ArrayList<String>()
        val rechecked = ArrayList<String>()
        val duplicates = ArrayList<String>()
        database.room.withTransaction {
            val secIds = HashMap<String, String>()
            sections.forEach { secIds[it.name] = it.id }
            var lastSec = sections.maxOfOrNull { it.order }
            for (name in parsed.newSections) {
                val sid = Checklist.localId("s")
                val order = Checklist.between(lastSec, null).also { lastSec = it }
                dao.putSections(listOf(sectionEntity(noteId, Section(sid, name, order = order))))
                secIds[name] = sid
                // ACT checksection.create @android.notes @android.notesapp (a header line in the add line)
                opIds += enqueue("POST", "notes/$noteId/sections", JsonObject(mapOf("name" to JsonPrimitive(name))),
                    "section", sid, noteId, AppText.get(R.string.notes_op_section_added), creates = sid)
            }
            val current = dao.itemsNow(noteId).filter { !it.removed }.mapNotNull { e -> decodeItem(e)?.let { e to it } }
            val payload = ArrayList<JsonObject>()
            var lastOrder = after?.let { a -> current.firstOrNull { it.second.id == a }?.second?.order }
            val nextOrder = after?.let { a ->
                val sorted = current.map { it.second }.sortedBy { it.order }
                sorted.getOrNull(sorted.indexOfFirst { it.id == a } + 1)?.order
            }
            for (p in parsed.items) {
                val sid = p.section?.let { secIds[it] } ?: sectionId
                val key = io.github.murtazanasir.mailr.notes.grammar.ItemGrammar.itemKey(p.text)
                val qty = p.qty?.let { Qty(it.amount, it.unit, it.text) }
                val dup = if (force) null else current.firstOrNull { it.second.key == key }
                if (dup != null) {
                    val (e, old) = dup
                    if (!old.checked) {
                        // The same unit (or both counts): the quantities add up; another unit: offered, not added.
                        if (qty?.unit != old.qty?.unit) {
                            duplicates += p.text
                            continue
                        }
                        val one = java.math.BigDecimal.ONE
                        val total = (old.qty?.amount?.toBigDecimalOrNull() ?: one) + (qty?.amount?.toBigDecimalOrNull() ?: one)
                        snapshots += e
                        putItem(noteId, old.copy(qty = Qty(total.stripTrailingZeros().toPlainString(), qty?.unit ?: old.qty?.unit, null)))
                        merged += old.id
                    } else {
                        snapshots += e
                        putItem(noteId, old.copy(checked = false, qty = qty ?: old.qty, checkedAt = null))
                        rechecked += old.id
                    }
                    payload += itemJson(Checklist.uuid7(), p.text, qty, sid, p.checked)
                    continue
                }
                val id = Checklist.uuid7()
                val order = Checklist.between(lastOrder ?: current.filter { it.second.sectionId == sid }.maxOfOrNull { it.second.order }, nextOrder)
                lastOrder = order
                putItem(noteId, Item(id, sectionId = sid, text = p.text, key = key, qty = qty, checked = p.checked, order = order,
                    addedAt = now, checkedAt = if (p.checked) now else null))
                addedIds += id
                payload += itemJson(id, p.text, qty, sid, p.checked)
            }
            if (payload.isNotEmpty()) {
                val body = JsonObject(buildMap {
                    put("items", JsonArray(payload))
                    after?.let { put("after", JsonPrimitive(it)) }
                    if (force) put("force", JsonPrimitive(true))
                })
                val target = (merged + rechecked).singleOrNull() ?: addedIds.firstOrNull() ?: noteId
                opIds += enqueue("POST", "notes/$noteId/items", body, "item", target, noteId,
                    if (payload.size == 1) AppText.get(R.string.notes_op_item_added) else AppText.plural(R.plurals.notes_op_items_added, payload.size, payload.size))
            }
        }
        queued()
        return AddResult(UndoHandle(AppText.plural(R.plurals.notes_op_items_added_undo, addedIds.size + merged.size + rechecked.size), opIds) {
            addedIds.forEach { dao.deleteItem(it) }
            dao.putItems(snapshots)
        }, addedIds.size, merged, rechecked, duplicates)
    }

    private fun itemJson(id: String, text: String, qty: Qty?, sectionId: String?, checked: Boolean) = JsonObject(buildMap {
        put("id", JsonPrimitive(id))
        put("text", JsonPrimitive(text))
        qty?.let { put("qty", JsonObject(mapOf("amount" to JsonPrimitive(it.amount), "unit" to (it.unit?.let(::JsonPrimitive) ?: JsonNull)))) }
        put("section_id", sectionId?.let(::JsonPrimitive) ?: JsonNull)
        if (checked) put("checked", JsonPrimitive(true))
    })

    /** ACT checkitem.check, ACT checkitem.uncheck: one tap, with the device time of the tap (the later tap wins). */
    suspend fun check(itemId: String, checked: Boolean): UndoHandle {
        val (e, before) = requireItem(itemId)
        val now = Instant.ofEpochMilli(clock()).toString()
        val opId = database.room.withTransaction {
            putItem(e.noteId, before.copy(checked = checked, checkedAt = if (checked) now else null))
            enqueue("PATCH", "notes/${e.noteId}/items/$itemId",
                JsonObject(mapOf("checked" to JsonPrimitive(checked), "device_at" to JsonPrimitive(now))),
                "item", itemId, e.noteId, if (checked) AppText.get(R.string.notes_op_checked) else AppText.get(R.string.notes_op_unchecked))
        }
        queued()
        return UndoHandle(if (checked) AppText.get(R.string.notes_op_checked) else AppText.get(R.string.notes_op_unchecked), listOf(opId)) { dao.putItems(listOf(e)) }
    }

    /**
     * ACT checkitem.edit: text (read in edit mode, so a typed `x2` becomes the quantity), quantity or
     * the extra line, with the item's version.
     */
    suspend fun editItem(itemId: String, text: String? = null, qty: Qty? = null, clearQty: Boolean = false,
                         extra: String? = null): UndoHandle {
        val (e, before) = requireItem(itemId)
        var after = before
        val req = HashMap<String, JsonElement>()
        before.version?.let { req["base_version"] = JsonPrimitive(it) }
        if (text != null) {
            val p = io.github.murtazanasir.mailr.notes.grammar.ItemGrammar.parse(text, io.github.murtazanasir.mailr.notes.grammar.ItemGrammar.Mode.EDIT).items.firstOrNull()
            after = after.copy(text = p?.text ?: text.trim(), key = io.github.murtazanasir.mailr.notes.grammar.ItemGrammar.itemKey(p?.text ?: text),
                qty = p?.qty?.let { Qty(it.amount, it.unit, it.text) } ?: after.qty)
            req["text"] = JsonPrimitive(text)
            req["parse"] = JsonPrimitive(true)
        }
        if (qty != null || clearQty) {
            after = after.copy(qty = if (clearQty) null else qty)
            req["qty"] = if (clearQty) JsonNull else JsonObject(mapOf("amount" to JsonPrimitive(qty!!.amount),
                "unit" to (qty.unit?.let(::JsonPrimitive) ?: JsonNull)))
        }
        if (extra != null) {
            after = after.copy(extra = extra.ifBlank { null })
            req["extra"] = JsonPrimitive(extra)
        }
        val opId = database.room.withTransaction {
            putItem(e.noteId, after)
            enqueuePatch("PATCH", "notes/${e.noteId}/items/$itemId", JsonObject(req), "item", itemId, e.noteId, AppText.get(R.string.notes_op_item_edited))
        }
        queued()
        return UndoHandle(AppText.get(R.string.notes_op_item_edited), listOf(opId)) { dao.putItems(listOf(e)) }
    }

    /** ACT checkitem.move: before or after an item, into a section, or a direction (TalkBack's Move up and Move down). */
    suspend fun moveItem(itemId: String, before: String? = null, after: String? = null, sectionId: String? = null,
                         toSection: Boolean = false, direction: String? = null): UndoHandle {
        val (e, item) = requireItem(itemId)
        val all = dao.itemsNow(e.noteId).mapNotNull { decodeItem(it) }.associateBy { it.id }
        val newSection = when {
            toSection -> sectionId
            before != null -> all[before]?.sectionId
            after != null -> all[after]?.sectionId
            else -> item.sectionId
        }
        val siblings = all.values.filter { it.sectionId == newSection && it.id != itemId }.sortedBy { it.order }
        val order = when {
            before != null -> Checklist.between(siblings.lastOrNull { it.order < (all[before]?.order ?: "") }?.order, all[before]?.order)
            after != null -> Checklist.between(all[after]?.order, siblings.firstOrNull { it.order > (all[after]?.order ?: "") }?.order)
            direction == "up" -> siblings.lastOrNull { it.order < item.order }?.let { p ->
                Checklist.between(siblings.lastOrNull { it.order < p.order }?.order, p.order) } ?: item.order
            direction == "down" -> siblings.firstOrNull { it.order > item.order }?.let { n ->
                Checklist.between(n.order, siblings.firstOrNull { it.order > n.order }?.order) } ?: item.order
            else -> Checklist.between(siblings.lastOrNull()?.order, null)
        }
        // A move by hand in learned order sets the rank between the neighbours (the server's rule); the phone keeps the rank it had until the next read.
        val req = JsonObject(buildMap {
            before?.let { put("before", JsonPrimitive(it)) }
            after?.let { put("after", JsonPrimitive(it)) }
            if (toSection) put("section_id", sectionId?.let(::JsonPrimitive) ?: JsonNull)
            direction?.let { put("direction", JsonPrimitive(it)) }
        })
        val opId = database.room.withTransaction {
            putItem(e.noteId, item.copy(sectionId = newSection, order = order, rank = if (newSection != item.sectionId) null else item.rank))
            enqueue("POST", "notes/${e.noteId}/items/$itemId/move", req, "item", itemId, e.noteId, AppText.get(R.string.notes_op_item_moved))
        }
        queued()
        return UndoHandle(AppText.get(R.string.notes_op_item_moved), listOf(opId)) { dao.putItems(listOf(e)) }
    }

    /** ACT checkitem.delete: kept in the note's history on the server. */
    suspend fun deleteItem(itemId: String): UndoHandle {
        val (e, _) = requireItem(itemId)
        val opId = database.room.withTransaction {
            dao.putItems(listOf(e.copy(removed = true)))
            enqueue("DELETE", "notes/${e.noteId}/items/$itemId", null, "delete-item", itemId, e.noteId, AppText.get(R.string.notes_op_item_deleted))
        }
        queued()
        return UndoHandle(AppText.get(R.string.notes_op_item_deleted), listOf(opId)) { dao.putItems(listOf(e)) }
    }

    /** ACT note.clear_checked: one undo for all. */
    suspend fun clearChecked(noteId: String): UndoHandle {
        val before = dao.itemsNow(noteId)
        val opId = database.room.withTransaction {
            dao.putItems(before.filter { e -> decodeItem(e)?.checked == true }.map { it.copy(removed = true) })
            enqueue("POST", "notes/$noteId/items/clear-checked", null, "note", noteId, noteId, AppText.get(R.string.notes_op_checked_items_cleared))
        }
        queued()
        return UndoHandle(AppText.get(R.string.notes_op_checked_items_cleared), listOf(opId)) { dao.putItems(before) }
    }

    /** ACT note.uncheck_all ("Start over"): one undo for all. */
    suspend fun uncheckAll(noteId: String): UndoHandle {
        val before = dao.itemsNow(noteId)
        val opId = database.room.withTransaction {
            dao.putItems(before.mapNotNull { e -> decodeItem(e)?.takeIf { it.checked }?.let { itemEntity(noteId, it.copy(checked = false, checkedAt = null)) } })
            enqueue("POST", "notes/$noteId/items/uncheck-all", null, "note", noteId, noteId, AppText.get(R.string.notes_op_all_unchecked))
        }
        queued()
        return UndoHandle(AppText.get(R.string.notes_op_all_unchecked), listOf(opId)) { dao.putItems(before) }
    }

    /** ACT checkitem.to_task: the server makes the task; the item shows its chip after the next read. */
    suspend fun toTask(itemId: String): UndoHandle {
        val (e, _) = requireItem(itemId)
        val opId = enqueue("POST", "notes/${e.noteId}/items/$itemId/to-task", null, "item", itemId, e.noteId, AppText.get(R.string.notes_op_task_made))
        queued()
        return UndoHandle(AppText.get(R.string.notes_op_task_made), listOf(opId)) { refreshNote(e.noteId) }
    }

    /**
     * The usual items of a list. Each answer is kept on the phone (kv `usual:<id>`), so offline the
     * strip and the widget show the last suggestions, without the ones added or hidden since and
     * without what is already on the list unchecked. A cancellation goes on up: the editor that asked
     * has closed and must not write its strip.
     */
    suspend fun usual(noteId: String): Usual? {
        if (Checklist.isLocal(noteId)) return null
        return try {
            api.usual(noteId).also { dao.putKv(NotesKv(usualKey(noteId), json.encodeToString(Usual.serializer(), it))) }
        } catch (e: kotlinx.coroutines.CancellationException) {
            throw e
        } catch (e: Exception) {
            if (e is IOException) _offline.value = true
            cachedUsual(noteId)
        }
    }

    /** The last usual items read for a list, less those already on it unchecked (the strip offline). */
    suspend fun cachedUsual(noteId: String): Usual? {
        val u = dao.kv(usualKey(noteId))?.let { catching { json.decodeFromString(Usual.serializer(), it) }.getOrNull() } ?: return null
        val open = itemsNow(noteId).filter { !it.checked }.map { it.key.ifEmpty { io.github.murtazanasir.mailr.notes.grammar.ItemGrammar.itemKey(it.text) } }.toSet()
        return u.copy(items = u.items.filter { it.key !in open })
    }

    /** A suggestion leaves the kept strip once it is added or hidden. */
    private suspend fun forgetUsual(noteId: String, key: String) {
        val raw = dao.kv(usualKey(noteId)) ?: return
        val u = catching { json.decodeFromString(Usual.serializer(), raw) }.getOrNull() ?: return
        dao.putKv(NotesKv(usualKey(noteId), json.encodeToString(Usual.serializer(), u.copy(items = u.items.filter { it.key != key }))))
    }

    /** ACT checkitem.usual_add: the item goes to its last section with its last quantity. */
    suspend fun addUsual(noteId: String, u: UsualItem): UndoHandle {
        val id = Checklist.localId("i")
        val opId = database.room.withTransaction {
            forgetUsual(noteId, u.key)
            putItem(noteId, Item(id, sectionId = u.sectionId, text = u.text, key = u.key, qty = u.qty, order = "zzzz",
                addedAt = Instant.ofEpochMilli(clock()).toString()))
            enqueue("POST", "notes/$noteId/items", JsonObject(mapOf("usual" to JsonArray(listOf(JsonPrimitive(u.key))))),
                "item", id, noteId, AppText.get(R.string.notes_op_added_usual, u.text))
        }
        queued()
        return UndoHandle(AppText.get(R.string.notes_op_added_usual, u.text), listOf(opId)) { dao.deleteItem(id) }
    }

    /** ACT checkitem.usual_dismiss: for one interval, or never. */
    suspend fun dismissUsual(noteId: String, key: String, never: Boolean): UndoHandle {
        forgetUsual(noteId, key)
        val opId = enqueue("POST", "notes/$noteId/usual/${java.net.URLEncoder.encode(key, "UTF-8").replace("+", "%20")}/dismiss",
            JsonObject(mapOf("for" to JsonPrimitive(if (never) "never" else "interval"))), "usual", noteId, noteId, AppText.get(R.string.notes_op_suggestion_hidden))
        queued()
        return UndoHandle(AppText.get(R.string.notes_op_suggestion_hidden), listOf(opId)) {}
    }

    // ================================================================ sections

    private suspend fun requireSection(noteId: String, sid: String): Pair<SectionEntity, Section> {
        val e = dao.sectionsNow(noteId).firstOrNull { it.id == sid } ?: error("no section $sid")
        return e to json.decodeFromString(Section.serializer(), e.json)
    }

    /** ACT checksection.create: a new section at the end. */
    suspend fun createSection(noteId: String, name: String): UndoHandle {
        val sid = Checklist.localId("s")
        val order = Checklist.between(sectionsNow(noteId).maxOfOrNull { it.order }, null)
        val opId = database.room.withTransaction {
            dao.putSections(listOf(sectionEntity(noteId, Section(sid, name.trim(), order = order,
                orderMode = if (requireNote(noteId).shopping) "learned" else "manual"))))
            enqueue("POST", "notes/$noteId/sections", JsonObject(mapOf("name" to JsonPrimitive(name.trim()))), "section", sid, noteId,
                AppText.get(R.string.notes_op_section_added), creates = sid)
        }
        queued()
        return UndoHandle(AppText.get(R.string.notes_op_section_added), listOf(opId)) { dao.deleteSection(sid) }
    }

    /**
     * ACT checksection.rename, ACT checksection.reorder, ACT checksection.order_mode: one section,
     * or the top group (`top`) for the order mode and collapsing.
     */
    suspend fun editSection(noteId: String, sid: String, name: String? = null, direction: String? = null,
                            orderMode: String? = null, collapsed: Boolean? = null, resetLearned: Boolean = false): UndoHandle {
        if (sid == "top") {
            val n = requireNote(noteId)
            val top = n.top ?: TopGroup(n.topOrderMode)
            val opId = database.room.withTransaction {
                putNote(n.copy(top = top.copy(orderMode = orderMode ?: top.orderMode, collapsed = collapsed ?: top.collapsed),
                    topOrderMode = orderMode ?: n.topOrderMode))
                enqueuePatch("PATCH", "notes/$noteId/sections/top", JsonObject(buildMap {
                    orderMode?.let { put("order_mode", JsonPrimitive(it)) }
                    collapsed?.let { put("collapsed", JsonPrimitive(it)) }
                    if (resetLearned) put("reset_learned", JsonPrimitive(true))
                }), "section", "top:$noteId", noteId, AppText.get(R.string.notes_op_saved))
            }
            queued()
            return UndoHandle(AppText.get(R.string.notes_op_saved), listOf(opId)) { putNote(n) }
        }
        val (e, s) = requireSection(noteId, sid)
        val all = sectionsNow(noteId).sortedBy { it.order }
        val order = when (direction) {
            "up" -> all.lastOrNull { it.order < s.order }?.let { p -> Checklist.between(all.lastOrNull { it.order < p.order }?.order, p.order) }
            "down" -> all.firstOrNull { it.order > s.order }?.let { n -> Checklist.between(n.order, all.firstOrNull { it.order > n.order }?.order) }
            else -> null
        } ?: s.order
        val opId = database.room.withTransaction {
            dao.putSections(listOf(sectionEntity(noteId, s.copy(name = name ?: s.name, order = order, orderMode = orderMode ?: s.orderMode,
                collapsed = collapsed ?: s.collapsed, trips = if (resetLearned) 0 else s.trips))))
            enqueuePatch("PATCH", "notes/$noteId/sections/$sid", JsonObject(buildMap {
                name?.let { put("name", JsonPrimitive(it)); put("base_name", JsonPrimitive(s.name)) }
                direction?.let { put("direction", JsonPrimitive(it)) }
                orderMode?.let { put("order_mode", JsonPrimitive(it)) }
                collapsed?.let { put("collapsed", JsonPrimitive(it)) }
                if (resetLearned) put("reset_learned", JsonPrimitive(true))
            }), "section", sid, noteId, AppText.get(R.string.notes_op_section_saved))
        }
        queued()
        return UndoHandle(AppText.get(R.string.notes_op_section_saved), listOf(opId)) { dao.putSections(listOf(e)) }
    }

    /** ACT checksection.delete: "Keep the items" (to the top group, the default) or "Delete them too". */
    suspend fun deleteSection(noteId: String, sid: String, keepItems: Boolean = true): UndoHandle {
        val (e, _) = requireSection(noteId, sid)
        val items = dao.itemsNow(noteId).filter { decodeItem(it)?.sectionId == sid }
        val opId = database.room.withTransaction {
            dao.putSections(listOf(e.copy(removed = true)))
            dao.putItems(items.mapNotNull { it2 ->
                if (keepItems) decodeItem(it2)?.let { itemEntity(noteId, it.copy(sectionId = null)) } else it2.copy(removed = true)
            })
            enqueue("DELETE", "notes/$noteId/sections/$sid?items=${if (keepItems) "keep" else "delete"}", null, "section", sid, noteId,
                AppText.get(R.string.notes_op_section_deleted))
        }
        queued()
        return UndoHandle(AppText.get(R.string.notes_op_section_deleted), listOf(opId)) { dao.putSections(listOf(e)); dao.putItems(items) }
    }

    // ================================================================ labels

    private suspend fun labelsNow() = dao.labelsNow().mapNotNull { catching { json.decodeFromString(Label.serializer(), it.json) }.getOrNull() }

    /** ACT notelabel.create */
    suspend fun createLabel(name: String, color: String? = null): UndoHandle {
        val n = name.trim()
        val opId = database.room.withTransaction {
            dao.putLabels(listOf(LabelEntity(n, json.encodeToString(Label.serializer(), Label(n, color, Checklist.between(labelsNow().maxOfOrNull { it.order ?: "" }, null))))))
            enqueue("POST", "notes/labels", JsonObject(buildMap {
                put("name", JsonPrimitive(n)); color?.let { put("color", JsonPrimitive(it)) }
            }), "label", n, "labels", AppText.get(R.string.notes_op_label_created))
        }
        queued()
        return UndoHandle(AppText.get(R.string.notes_op_label_created), listOf(opId)) { dao.deleteLabel(n) }
    }

    /** ACT notelabel.rename, ACT notelabel.color, ACT notelabel.reorder: on every note and table of this user. */
    suspend fun editLabel(name: String, newName: String? = null, color: String? = null, before: String? = null,
                          after: String? = null): UndoHandle {
        val old = labelsNow().firstOrNull { it.name == name } ?: error("no label $name")
        val notes = dao.allNotes()
        val tables = dao.allTables()
        val order = when {
            before != null -> Checklist.between(labelsNow().filter { (it.order ?: "") < (labelsNow().firstOrNull { l -> l.name == before }?.order ?: "") }
                .maxOfOrNull { it.order ?: "" }, labelsNow().firstOrNull { it.name == before }?.order)
            after != null -> Checklist.between(labelsNow().firstOrNull { it.name == after }?.order,
                labelsNow().filter { (it.order ?: "") > (labelsNow().firstOrNull { l -> l.name == after }?.order ?: "") }.minOfOrNull { it.order ?: "" })
            else -> old.order
        }
        val opId = database.room.withTransaction {
            val renamed = old.copy(name = newName?.trim() ?: old.name, color = color ?: old.color, order = order)
            if (newName != null) {
                dao.deleteLabel(name)
                renameLabelOnItems(name, newName.trim())
            }
            dao.putLabels(listOf(LabelEntity(renamed.name, json.encodeToString(Label.serializer(), renamed))))
            enqueue("PATCH", "notes/labels/${enc(name)}", JsonObject(buildMap {
                newName?.let { put("name", JsonPrimitive(it.trim())) }
                color?.let { put("color", JsonPrimitive(it)) }
                before?.let { put("before", JsonPrimitive(it)) }
                after?.let { put("after", JsonPrimitive(it)) }
            }), "label", name, "labels", if (newName != null) AppText.get(R.string.notes_op_label_renamed) else AppText.get(R.string.notes_op_label_saved))
        }
        queued()
        return UndoHandle(if (newName != null) AppText.get(R.string.notes_op_label_renamed) else AppText.get(R.string.notes_op_label_saved), listOf(opId)) {
            newName?.let { dao.deleteLabel(it.trim()) }
            dao.putLabels(listOf(LabelEntity(old.name, json.encodeToString(Label.serializer(), old))))
            dao.putNotes(notes)
            dao.putTables(tables)
        }
    }

    private suspend fun renameLabelOnItems(from: String, to: String?) {
        dao.putNotes(dao.allNotes().mapNotNull { e ->
            decodeNote(e)?.takeIf { from in it.state.labels }?.let { n ->
                noteEntity(n.copy(state = n.state.copy(labels = n.state.labels.mapNotNull { if (it == from) to else it }.distinct())))
            }
        })
        dao.putTables(dao.allTables().mapNotNull { e ->
            decodeTable(e)?.takeIf { from in it.state.labels }?.let { t ->
                tableEntity(t.copy(state = t.state.copy(labels = t.state.labels.mapNotNull { if (it == from) to else it }.distinct())))
            }
        })
    }

    /** ACT notelabel.delete: off every note and table of this user; the notes stay. */
    suspend fun deleteLabel(name: String): UndoHandle {
        val old = dao.labelsNow().firstOrNull { it.name == name }
        val notes = dao.allNotes()
        val tables = dao.allTables()
        val opId = database.room.withTransaction {
            dao.deleteLabel(name)
            renameLabelOnItems(name, null)
            enqueue("DELETE", "notes/labels/${enc(name)}", null, "label", name, "labels", AppText.get(R.string.notes_op_label_deleted))
        }
        queued()
        return UndoHandle(AppText.get(R.string.notes_op_label_deleted), listOf(opId)) {
            old?.let { dao.putLabels(listOf(it)) }
            dao.putNotes(notes)
            dao.putTables(tables)
        }
    }

    private fun enc(s: String) = java.net.URLEncoder.encode(s, "UTF-8").replace("+", "%20")

    // ================================================================ tables

    /** ACT table.create: a name and a template (one text column "Name", Log or Inventory). */
    suspend fun createTable(name: String, template: String? = null): Pair<String, UndoHandle> {
        val id = Checklist.localId("t")
        val now = Instant.ofEpochMilli(clock()).toString()
        val opId = database.room.withTransaction {
            dao.putTables(listOf(tableEntity(DataTable(id, name = name, createdAt = now, updatedAt = now,
                columns = listOf(Column("local-c", "Name")))))) // i18n-ignore: the column the server makes, until its answer
            enqueue("POST", "tables", JsonObject(buildMap {
                put("name", JsonPrimitive(name)); template?.let { put("template", JsonPrimitive(it)) }
            }), "table", id, id, AppText.get(R.string.notes_op_table_created), creates = id)
        }
        queued()
        return id to UndoHandle(AppText.get(R.string.notes_op_table_deleted), listOf(opId)) { forgetTable(id) }
    }

    /** ACT table.rename, ACT table.color */
    suspend fun editTable(id: String, name: String? = null, color: String? = null, clearColor: Boolean = false): UndoHandle {
        val before = dao.tableNow(id)?.let { decodeTable(it) } ?: error("no table $id")
        val opId = database.room.withTransaction {
            dao.putTables(listOf(tableEntity(before.copy(name = name ?: before.name, color = if (clearColor) null else color ?: before.color))))
            enqueuePatch("PATCH", "tables/$id", JsonObject(buildMap {
                before.version?.let { put("base_version", JsonPrimitive(it)) }
                name?.let { put("name", JsonPrimitive(it)) }
                if (clearColor) put("color", JsonPrimitive("")) else color?.let { put("color", JsonPrimitive(it)) }
            }), "table", id, id, if (name != null) AppText.get(R.string.notes_op_table_renamed) else AppText.get(R.string.notes_op_colour_changed))
        }
        queued()
        return UndoHandle(if (name != null) AppText.get(R.string.notes_op_table_renamed) else AppText.get(R.string.notes_op_colour_changed), listOf(opId)) { dao.putTables(listOf(tableEntity(before))) }
    }

    private suspend fun putRow(tableId: String, r: Row) = dao.putRows(listOf(rowEntity(tableId, r)))

    /** ACT row.add: a row with the phone's own id; a view's filter values come in through [viewFilters]. */
    suspend fun addRow(tableId: String, cells: JsonObject, viewFilters: List<JsonObject> = emptyList(),
                       parse: Boolean = true): Pair<String, UndoHandle> {
        val id = Checklist.uuid7()
        val now = Instant.ofEpochMilli(clock()).toString()
        val last = dao.rowsNow(tableId).mapNotNull { decodeRow(it) }.maxOfOrNull { it.order }
        val opId = database.room.withTransaction {
            putRow(tableId, Row(id, cells = cells, order = Checklist.between(last, null), createdAt = now, updatedAt = now))
            enqueue("POST", "tables/$tableId/rows", JsonObject(buildMap {
                put("id", JsonPrimitive(id)); put("cells", cells); put("parse", JsonPrimitive(parse))
                if (viewFilters.isNotEmpty()) put("view_filters", JsonArray(viewFilters))
            }), "row", id, tableId, AppText.get(R.string.notes_op_row_added))
        }
        queued()
        return id to UndoHandle(AppText.get(R.string.notes_op_row_added), listOf(opId)) { dao.deleteRow(id) }
    }

    /** ACT row.edit: changed cells only, as typed (`parse: true`), with the row's version. */
    suspend fun editRow(tableId: String, rowId: String, cells: JsonObject, parse: Boolean = true): UndoHandle {
        val e = dao.row(rowId) ?: error("no row $rowId")
        val before = decodeRow(e) ?: error("bad row $rowId")
        val merged = JsonObject(before.cells + cells).filterValues { v ->
            !(v is JsonNull || (v is JsonPrimitive && v.isString && v.content.isEmpty()) || (v is JsonArray && v.isEmpty()))
        }
        val opId = database.room.withTransaction {
            putRow(tableId, before.copy(cells = JsonObject(merged), raw = JsonObject(before.raw - cells.keys)))
            enqueuePatch("PATCH", "tables/$tableId/rows/$rowId", JsonObject(buildMap {
                before.version?.let { put("base_version", JsonPrimitive(it)) }
                put("cells", cells); put("parse", JsonPrimitive(parse))
            }), "row", rowId, tableId, AppText.get(R.string.notes_op_row_saved))
        }
        queued()
        return UndoHandle(AppText.get(R.string.notes_op_row_saved), listOf(opId)) { dao.putRows(listOf(e)) }
    }

    /** ACT row.delete, ACT row.bulk_delete: restorable for 30 days. */
    suspend fun deleteRows(tableId: String, rowIds: List<String>): UndoHandle {
        val before = rowIds.mapNotNull { dao.row(it) }
        val opId = database.room.withTransaction {
            dao.putRows(before.map { it.copy(removed = true) })
            if (rowIds.size == 1) enqueue("DELETE", "tables/$tableId/rows/${rowIds[0]}", null, "delete-row", rowIds[0], tableId, AppText.get(R.string.notes_op_row_deleted))
            else enqueue("POST", "tables/$tableId/rows/bulk", JsonObject(mapOf("op" to JsonPrimitive("delete"),
                "rows" to JsonArray(rowIds.map(::JsonPrimitive)))), "rows", tableId, tableId, AppText.plural(R.plurals.notes_op_rows_deleted, rowIds.size, rowIds.size))
        }
        queued()
        return UndoHandle(if (rowIds.size == 1) AppText.get(R.string.notes_op_row_deleted) else AppText.plural(R.plurals.notes_op_rows_deleted, rowIds.size, rowIds.size), listOf(opId)) { dao.putRows(before) }
    }

    /** ACT row.duplicate: a copy below; the server makes it. */
    suspend fun duplicateRow(tableId: String, rowId: String): UndoHandle {
        val opId = enqueue("POST", "tables/$tableId/rows/$rowId/duplicate", null, "rows", tableId, tableId, AppText.get(R.string.notes_op_row_copied))
        queued()
        scope.launch { catching { drain(); pull() } }
        return UndoHandle(AppText.get(R.string.notes_op_row_copied), listOf(opId)) { pull() }
    }

    /** ACT row.history (online): the row's changes and each cell's earlier values. */
    suspend fun rowHistory(tableId: String, rowId: String): RowHistory = api.rowHistory(tableId, rowId)

    /** ACT row.restore_version (online): the row, or one cell, as it was after a change. */
    suspend fun restoreRowVersion(tableId: String, rowId: String, seq: Long, col: String? = null): Undo? {
        val res = api.raw("POST", "tables/$tableId/rows/$rowId/restore", JsonObject(buildMap {
            put("seq", JsonPrimitive(seq)); col?.let { put("col", JsonPrimitive(it)) }
        }))
        refreshRow(tableId, rowId)
        return (res["undo"] as? JsonObject)?.let { json.decodeFromJsonElement(Undo.serializer(), it) }
    }

    /** Forget the account's notes (sign-out). The queue goes too. */
    suspend fun clear() {
        database.room.withTransaction {
            dao.allNotes().forEach { forgetNote(it.id) }
            dao.allTables().forEach { forgetTable(it.id) }
            dao.clearLabels()
            dao.opsNow().forEach { dao.deleteOp(it.id) }
            listOf(KV_NOTES_SINCE, KV_TABLES_SINCE, KV_SYNCED_AT).forEach { dao.deleteKv(it) }
            // The kept usual items and view pages are the account's too.
            dao.deleteKvPrefix("usual:")
            dao.deleteKvPrefix("rows:")
        }
    }

    companion object {
        const val KV_NOTES_SINCE = "notes_since"
        const val KV_TABLES_SINCE = "tables_since"
        const val KV_SYNCED_AT = "synced_at"
        fun pageKey(tableId: String, viewId: String?) = "rows:$tableId:${viewId ?: "default"}"
        fun usualKey(noteId: String) = "usual:$noteId"
        /** Presence: a beat every 15 s; someone is in an item while their last beat is under 40 s old. */
        const val PRESENCE_HEARTBEAT_MS = 15_000L
        const val PRESENCE_TTL_MS = 40_000L
        /** How long a row shows the name of the person who just checked it. */
        const val TICK_MS = 4_000L
    }
}
