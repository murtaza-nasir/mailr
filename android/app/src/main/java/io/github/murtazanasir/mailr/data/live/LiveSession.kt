package io.github.murtazanasir.mailr.data.live

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.sync.ServerEvent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonPrimitive

/**
 * An open editor's side of live editing (core/live.py on the server, LiveMerge for the merge).
 *
 * The editor (a [LivePort]: the composer, the event editor, the task editor) shows an item at a
 * version, its base. When the change stream says someone else wrote the item, the session reads
 * it (GET /live/{kind}/{id}) and
 *
 * - into a clean editor puts their copy, with "Updated by the assistant" and Undo;
 * - with unsaved changes merges both three ways against the base: a clean merge goes into the
 *   editor (still unsaved, so it saves); an overlap shows the conflict bar ("Use mine", "Use the
 *   assistant's", "Compare"). A save the server refuses (409) shows the same bar.
 *
 * It ignores the editor's own writes (`origin` is this install's client id) and versions it has.
 * Plain Kotlin, so the unit tests drive it without Android.
 */
class LiveSession(
    val kind: String,
    id: String?,
    private val clientId: String,
    private val port: LivePort,
    private val scope: CoroutineScope,
    private val api: LiveApi,
    private val now: () -> Long = System::currentTimeMillis,  // clock-ok: typing throttle
    /** Hears each new base content (an editor that keeps a request body for it). */
    private val onBase: (JsonObject) -> Unit = {},
) {
    /** The item's id as the server names it (a draft's public id); null until a new draft is saved. */
    @Volatile var id: String? = id

    /** The version the editor's copy is based on, sent as `base_version` with a save. */
    @Volatile var baseVersion: Int? = null
        private set

    /** The content at [baseVersion], in the editor's spelling. */
    private var base: JsonObject? = null

    private val _ui = MutableStateFlow(LiveUi())
    val ui: StateFlow<LiveUi> = _ui.asStateFlow()

    private var editingJob: Job? = null
    private var lastTyping = Long.MIN_VALUE / 2

    /** The editor opened (or a new draft got saved) at [version] with [content] in the editor's spelling. */
    fun start(version: Int?, content: JsonObject?) {
        baseVersion = version
        base = content
        content?.let(onBase)
    }

    /**
     * Read the item's current version (an editor opened from a stored copy): a newer copy goes into
     * the clean editor without a message; otherwise the editor just takes the version as its base.
     */
    suspend fun open() {
        val i = id ?: return
        val item = catching { api.item(kind, i) }.getOrNull() ?: return
        val v = item.version ?: return
        val theirs = port.decode(item.content) ?: return
        if (port.dirty()) {
            // Unsaved text from before (a local copy) with nothing known to merge against: it
            // replaces the item when saved, as before. With a known base (a version, or a copy the
            // server stored once: start(null, content)) the newer copy merges in under the typing.
            if (baseVersion == null && base == null) start(v, theirs) else receive(theirs, v, item.by, quiet = true)
            return
        }
        if (theirs != port.mine()) port.apply(theirs, clean = true)
        start(v, theirs)
    }

    /** A server event from /events (LiveEvents.events). */
    suspend fun onEvent(ev: ServerEvent) {
        when (ev.type) {
            "item" -> onItem(LiveSignal.item(ev.data) ?: return)
            "editing" -> onEditing(LiveSignal.editing(ev.data) ?: return)
        }
    }

    suspend fun onItem(e: LiveSignal.Item) {
        val i = id ?: return
        if (e.kind != kind || e.id != i) return
        if (e.origin != null && e.origin == clientId) return
        if (e.deleted) {
            _ui.update { it.copy(gone = true, editing = null) }
            return
        }
        val bv = baseVersion
        if (e.version != null && bv != null && e.version <= bv) return
        val item = catching { api.item(kind, i) }.getOrNull() ?: return
        val v = item.version ?: return
        if (bv != null && v <= bv) return
        val theirs = port.decode(item.content) ?: return
        receive(theirs, v, e.by ?: item.by)
    }

    fun onEditing(e: LiveSignal.Editing) {
        if (e.kind != kind || e.id != id) return
        editingJob?.cancel()
        if (e.state == "start") {
            _ui.update { it.copy(editing = e.by) }
            // No "end" (the write failed, the stream dropped): the banner goes after a while.
            editingJob = scope.launch {
                delay(EDITING_TIMEOUT_MS)
                _ui.update { it.copy(editing = null) }
            }
        } else _ui.update { it.copy(editing = null) }
    }

    /** Their copy at [version] ([theirs], in the editor's spelling) reached the open editor. */
    private fun receive(theirs: JsonObject, version: Int, by: String?, quiet: Boolean = false) {
        val open = _ui.value.conflict
        if (!port.dirty() && open == null) {
            val previous = port.mine()
            port.apply(theirs, clean = true)
            start(version, theirs)
            if (!quiet && previous != theirs) _ui.update { it.copy(notice = LiveNotice(by, previous), editing = null) }
            return
        }
        val mine = port.mine()
        val r = LiveMerge.mergeFields(base, mine, theirs, port.textFields)
        if (r.ok) {
            if (r.merged != mine) port.apply(r.merged, clean = false)
            start(version, theirs)
            _ui.update { it.copy(conflict = null, notice = if (quiet) it.notice else LiveNotice(by, null), editing = null) }
        } else {
            _ui.update { it.copy(conflict = conflictOf(by, version, mine, theirs, r), editing = null) }
        }
    }

    private fun conflictOf(by: String?, version: Int, mine: JsonObject, theirs: JsonObject, r: LiveMerge.FieldMerge): LiveConflict {
        val rows = r.conflicts.flatMap { f ->
            val chunks = r.texts[f]
            if (!chunks.isNullOrEmpty()) chunks.map { c ->
                CompareRow(f, port.show(f, JsonPrimitive(c.mine)), port.show(f, JsonPrimitive(c.theirs)))
            } else listOf(CompareRow(f, port.show(f, mine[f]), port.show(f, theirs[f])))
        }
        return LiveConflict(by, version, theirs, r.conflicts, rows)
    }

    /**
     * A save went through: [sent] is the content it sent (the editor's spelling) and [version] the
     * new version. [merged]: the server merged it with changes saved since the base, and
     * [stored] (the server's spelling) is what it keeps; the editor rebases onto it.
     */
    fun saved(sent: JsonObject, version: Int?, merged: Boolean = false, stored: JsonObject? = null) {
        val theirs = if (merged && stored != null) port.decode(stored) else null
        if (theirs == null) {
            start(version ?: baseVersion, sent)
            return
        }
        val mine = port.mine()
        if (mine == sent) {
            if (theirs != mine) port.apply(theirs, clean = !port.dirty())
        } else {
            // Typed on while the save was out: the typing goes on top of the stored copy.
            val r = LiveMerge.mergeFields(sent, mine, theirs, port.textFields)
            if (r.merged != mine) port.apply(r.merged, clean = false)
        }
        start(version ?: baseVersion, theirs)
    }

    /** A save the server refused with 409 (core/live.py LiveConflict.body): the conflict bar. */
    fun refused(body: JsonObject) {
        val t = body["theirs"] as? JsonObject ?: return
        val v = (t["version"] as? JsonPrimitive)?.intOrNull ?: return
        val theirs = (t["content"] as? JsonObject)?.let { port.decode(it) } ?: return
        val by = (t["by"] as? JsonPrimitive)?.contentOrNull
        val mine = port.mine()
        val r = LiveMerge.mergeFields(base, mine, theirs, port.textFields)
        // The local merge names the editor's fields and paragraphs for Compare; the server's list
        // (its own field names) stands in when the editor no longer differs locally.
        val fields = r.conflicts.ifEmpty {
            catching { body["fields"]!!.jsonArray.map { it.jsonPrimitive.content } }.getOrNull().orEmpty()
        }
        _ui.update { it.copy(conflict = conflictOf(by, v, mine, theirs, r.copy(conflicts = fields)), editing = null) }
    }

    /** "Use mine": the editor's copy is saved over theirs. */
    fun useMine() {
        val c = _ui.value.conflict ?: return
        start(c.version, c.theirs)
        _ui.update { it.copy(conflict = null) }
        port.saveNow()
    }

    /** "Use the assistant's": their copy replaces the editor's. */
    fun useTheirs() {
        val c = _ui.value.conflict ?: return
        port.apply(c.theirs, clean = true)
        start(c.version, c.theirs)
        _ui.update { it.copy(conflict = null) }
    }

    /** Undo on "Updated by …": the copy from before goes back and is saved as a new version. */
    fun undo() {
        val previous = _ui.value.notice?.previous ?: return
        _ui.update { it.copy(notice = null) }
        port.apply(previous, clean = false)
        port.saveNow()
    }

    fun dismissNotice() = _ui.update { it.copy(notice = null) }

    /** The user typed: tell the server, at most once a second (POST /live/typing). */
    fun typed() {
        val i = id ?: return
        val t = now()
        if (t - lastTyping < TYPING_EVERY_MS) return
        lastTyping = t
        scope.launch { catching { api.typing(kind, i) } }
    }

    companion object {
        const val EDITING_TIMEOUT_MS = 30_000L
        const val TYPING_EVERY_MS = 1_000L
    }
}

/** What an editor gives live editing. Content is a JSON object in the editor's own spelling. */
interface LivePort {
    /** Text fields that merge by units ("lines" or "html"); other fields merge whole. */
    val textFields: Map<String, String>

    /** The editor's content now. */
    fun mine(): JsonObject

    /** The editor has changes it has not saved. */
    fun dirty(): Boolean

    /** The server's content (GET /live, a 409, a merged save) in the editor's spelling; null when it cannot show it. */
    fun decode(server: JsonObject): JsonObject?

    /** Put [content] in the editor. [clean]: it is what the server has, so nothing needs saving. */
    fun apply(content: JsonObject, clean: Boolean)

    /** Save the editor's content now (Undo, "Use mine"). */
    fun saveNow()

    /** A field's value as the Compare view shows it. */
    fun show(field: String, value: JsonElement?): String =
        when (value) { null, is JsonNull -> ""; is JsonPrimitive -> value.contentOrNull.orEmpty(); else -> value.toString() }
}

/** The server calls live editing needs (MailrApi in the app, a fake in tests). */
interface LiveApi {
    suspend fun item(kind: String, id: String): LiveItemState?
    suspend fun typing(kind: String, id: String)
}

data class LiveItemState(val version: Int?, val content: JsonObject, val by: String? = null)

/** "Updated by …" with Undo ([previous] null: their changes merged into unsaved ones, nothing to undo). */
data class LiveNotice(val by: String?, val previous: JsonObject?)

/** One differing part for Compare: the field and both versions of it. */
data class CompareRow(val field: String, val mine: String, val theirs: String)

data class LiveConflict(val by: String?, val version: Int, val theirs: JsonObject, val fields: List<String>, val rows: List<CompareRow>)

data class LiveUi(
    /** Who is writing the item now ("assistant", "agent:<name>"), for "Assistant is editing this draft…". */
    val editing: String? = null,
    val notice: LiveNotice? = null,
    val conflict: LiveConflict? = null,
    /** The item was deleted elsewhere. */
    val gone: Boolean = false,
)

/** The live events of /events, parsed. */
object LiveSignal {
    data class Item(val kind: String, val id: String, val version: Int?, val by: String?, val origin: String?, val deleted: Boolean)
    data class Editing(val kind: String, val id: String, val by: String?, val state: String)

    private fun JsonObject.str(k: String): String? = (this[k] as? JsonPrimitive)?.takeIf { it !is JsonNull }?.contentOrNull

    fun item(o: JsonObject): Item? {
        val kind = o.str("kind") ?: return null
        val id = o.str("id") ?: return null
        return Item(kind, id, (o["version"] as? JsonPrimitive)?.intOrNull, o.str("by"), o.str("origin"),
            (o["deleted"] as? JsonPrimitive)?.booleanOrNull == true)
    }

    fun editing(o: JsonObject): Editing? {
        val kind = o.str("kind") ?: return null
        val id = o.str("id") ?: return null
        return Editing(kind, id, o.str("by"), o.str("state") ?: "start")
    }
}

/** The words for who changed an item: "the assistant", the agent's name, another device. */
object LiveWords {
    private val kinds = listOf("draft", "event", "task")

    /** The sentence for [kind] (draft, event, task); any other kind goes in as the last argument. */
    private fun say(kind: String, ids: IntArray, vararg args: Any): String {
        val i = kinds.indexOf(kind)
        return if (i >= 0) AppText.get(ids[i], *args) else AppText.get(ids[3], *args, kind)
    }

    /** "Ambrose" (the user's name for the assistant), "Inbox Bot" (an agent's token name), "Someone". */
    fun name(by: String?): String = when {
        by == "assistant" -> io.github.murtazanasir.mailr.i18n.AssistantName.now()
        by != null && by.startsWith("agent:") -> by.removePrefix("agent:").ifBlank { AppText.get(R.string.live_an_agent) }
        else -> AppText.get(R.string.live_someone)
    }

    private fun mine(by: String?) = by == null || by == "user" || by == "device"

    /** "Updated by Ambrose", "Updated by Inbox Bot", "Updated on another device". */
    fun updated(by: String?): String = when {
        mine(by) -> AppText.get(R.string.live_updated_device)
        else -> AppText.get(R.string.live_updated_by, name(by))
    }

    /** "Ambrose changed this draft", "Inbox Bot changed this task", "This event changed on another device". */
    fun changed(by: String?, kind: String): String {
        return when {
            mine(by) -> say(kind, intArrayOf(R.string.live_changed_device_draft, R.string.live_changed_device_event,
                R.string.live_changed_device_task, R.string.live_changed_device_other))
            else -> say(kind, intArrayOf(R.string.live_changed_by_draft, R.string.live_changed_by_event,
                R.string.live_changed_by_task, R.string.live_changed_by_other), name(by))
        }
    }

    /** "Use Ambrose's", "Use Inbox Bot's", "Use the other copy". */
    fun useTheirs(by: String?): String = when {
        mine(by) -> AppText.get(R.string.live_use_other_copy)
        else -> AppText.get(R.string.live_use_theirs, name(by))
    }

    /** The other version's name in the Compare dialog: "Ambrose's", "Inbox Bot's", "The other copy". */
    fun theirs(by: String?): String = when {
        mine(by) -> AppText.get(R.string.live_theirs_other_copy)
        else -> AppText.get(R.string.live_theirs, name(by))
    }

    /** "Assistant is editing this draft…" */
    fun editing(by: String?, kind: String): String = say(kind, intArrayOf(R.string.live_editing_draft, R.string.live_editing_event,
        R.string.live_editing_task, R.string.live_editing_other), name(by))
}
