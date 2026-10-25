package io.github.murtazanasir.mailr.ui.compose

import io.github.murtazanasir.mailr.util.catching

import android.content.ContentResolver
import android.net.Uri
import android.provider.OpenableColumns
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.data.api.Address
import io.github.murtazanasir.mailr.data.api.ComposeAttachment
import io.github.murtazanasir.mailr.data.api.ComposePayload
import io.github.murtazanasir.mailr.data.api.Identity
import io.github.murtazanasir.mailr.data.api.Outbox
import io.github.murtazanasir.mailr.data.api.Person
import io.github.murtazanasir.mailr.data.api.userMessage
import io.github.murtazanasir.mailr.data.api.templates
import io.github.murtazanasir.mailr.data.api.expandTemplate
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.live.LivePort
import io.github.murtazanasir.mailr.data.live.LiveSession
import io.github.murtazanasir.mailr.data.live.LiveUi
import io.github.murtazanasir.mailr.data.live.MailrLiveApi
import io.github.murtazanasir.mailr.data.live.OpenEditors
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import java.time.ZonedDateTime
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText

/** How the composer was opened. */
/** Reply, Reply all and Forward in a thread: its saved draft (the user's or Ambrose's) when it has one, so the
 *  thread never ends with two answers; else a new reply to [m]. ACT draft.resume */
fun replyArgs(messages: List<io.github.murtazanasir.mailr.data.api.Message>, m: io.github.murtazanasir.mailr.data.api.Message, mode: String): ComposeArgs {
    val saved = messages.lastOrNull { it.draft }
    return if (saved != null) ComposeArgs(draftId = saved.id) else ComposeArgs(replyToMessageId = m.id, mode = mode)
}

data class ComposeArgs(
    val draftId: Long? = null,
    val replyToMessageId: Long? = null,
    val mode: String? = null,               // reply | reply_all | forward
    val restoreKey: String? = null,         // payload restored after "Undo send"
    val to: String? = null,                 // mailto: ("Name" <a@b>, c@d: Mailto.parseAddresses reads it)
    val subject: String? = null,
    val body: String? = null,
    val cc: String? = null,                 // mailto: ?cc=
    val bcc: String? = null,                // mailto: ?bcc=
)

enum class Field { TO, CC, BCC }

data class ComposeState(
    /**
     * The stored copy (the draft, the reply prefill, a copy restored without its version) has not
     * arrived: the fields are not shown, so nothing typed can be replaced when it lands.
     */
    val loading: Boolean = true,
    /** What the screen says while [loading]: "Opening draft…", "Opening reply…". */
    val loadingText: String = AppText.get(R.string.compose_loading),
    val identities: List<Identity> = emptyList(),
    val identityId: Long? = null,
    val to: List<Address> = emptyList(),
    val cc: List<Address> = emptyList(),
    val bcc: List<Address> = emptyList(),
    val showCc: Boolean = false,
    val subject: String = "",
    /** The body as blocks: text, inline images and HTML kept as written (ComposeBody). */
    val blocks: List<BodyBlock> = ComposeBody.ofText(""),
    /** A signature that came with the draft; it goes out again in place of the identity's. */
    val signature: String? = null,
    /** Image bytes to show, by image block (the prepared photo, or the upload of a draft). */
    val previews: Map<Long, ByteArray> = emptyMap(),
    val quoted: String? = null,
    val showQuoted: Boolean = false,
    val attachments: List<ComposeAttachment> = emptyList(),
    val uploading: Int = 0,
    val inReplyTo: Long? = null,
    val forwardOf: Long? = null,
    val draftId: Long? = null,
    val saveStatus: String = "",
    val sending: Boolean = false,
    val error: String? = null,
    val suggestions: List<Person> = emptyList(),
    val suggestFor: Field? = null,
    val title: String = AppText.get(R.string.compose_title_compose),
) {
    val hasContent: Boolean get() = to.isNotEmpty() || cc.isNotEmpty() || subject.isNotBlank() || ComposeBody.hasContent(blocks) || attachments.isNotEmpty()

    /** The plain text the user wrote (all text blocks). */
    val body: String get() = ComposeBody.text(blocks)

    /** Inline images still uploading. */
    val imagesUploading: Int get() = blocks.count { it is BodyBlock.Image && it.uploading }
}

sealed interface ComposeResult {
    data class Sent(val outbox: Outbox, val scheduled: Boolean) : ComposeResult
    data object Discarded : ComposeResult
    /** Discarded with text in it: [payload] is what it held, for the snackbar's Undo (a new draft). */
    data class DiscardedDraft(val payload: ComposePayload) : ComposeResult
    /** The server has the draft. */
    data object SavedDraft : ComposeResult
    /** Only this phone has the latest text; it syncs when the server can be reached. */
    data object SavedLocally : ComposeResult

    companion object {
        /** What the list says after the composer closes; null for nothing. */
        fun message(r: ComposeResult): String? = when (r) {
            SavedDraft -> AppText.get(R.string.compose_saved_to_drafts)
            SavedLocally -> AppText.get(R.string.compose_saved_locally_sync)
            else -> null
        }
    }
}

/**
 * Payloads handed back by "Undo send", or a local draft reopened from Pending changes, keyed for
 * the compose route. [Entry.localId] names the local copy the composer carries on.
 */
object RestoreStore {
    data class Entry(val payload: ComposePayload, val draftId: Long?, val localId: String? = null)
    private val map = java.util.concurrent.ConcurrentHashMap<String, Entry>()
    fun put(p: ComposePayload, draftId: Long?, localId: String? = null): String =
        java.util.UUID.randomUUID().toString().also { map[it] = Entry(p, draftId, localId) }
    fun take(key: String): Entry? = map.remove(key)
}

/**
 * The composer. [saved] keeps the id of its local copy across process death, so the text comes
 * back where it was; without one (tests, the share sheet) the copy still syncs.
 *
 * Live editing (data/live): the draft keeps the version it is based on, saves send it, and changes
 * others save while the composer is open come in ([live]). [owner] names the screen that holds the
 * composer, so opening the same draft again brings this one forward (OpenEditors).
 */
class ComposeViewModel(
    private val g: AppGraph,
    private val args: ComposeArgs,
    private val saved: androidx.lifecycle.SavedStateHandle? = null,
    private val owner: String? = null,
) : ViewModel() {
    private val _state = MutableStateFlow(ComposeState(title = titleOf(args), loadingText = loadingTextOf(args)))
    val state: StateFlow<ComposeState> = _state.asStateFlow()

    private val _done = MutableSharedFlow<ComposeResult>(extraBufferCapacity = 1)
    val done: SharedFlow<ComposeResult> = _done

    /** The wait before an autosave; once the save itself starts, a later edit queues another save. */
    private var saveJob: Job? = null
    /** One save at a time; each sends what the screen shows when its turn comes. */
    private val saveLock = Mutex()
    private var loadJob: Job? = null
    /**
     * The fields the user changed ("identity", "to", "cc", "bcc", "subject", "body",
     * "attachments"). A copy or refresh that lands later fills only the others.
     */
    private val touched = mutableSetOf<String>()
    /** Where the cursor was last: (text block, offset). A photo goes in there. */
    private var cursor: Pair<Long, Int>? = null
    private var suggestJob: Job? = null
    private var localJob: Job? = null
    private var dirty = false
    /** The current send attempt's Idempotency-Key, kept while its outcome is unknown (send). */
    private var sendKey: String? = null
    /** Counts edits, so a save that ran during an edit leaves the composer dirty. */
    private var edits = 0L

    /** This composer's row in local_drafts. */
    private var localId: String = saved?.get<String>(KEY_LOCAL) ?: g.drafts.newId()

    /** The editor as live editing sees it: the compose payload, the body merging by HTML blocks. */
    private val port = object : LivePort {
        override val textFields = mapOf("html" to "html", "text" to "lines")
        override fun mine(): JsonObject = json(payload())
        override fun dirty(): Boolean = dirty
        override fun decode(server: JsonObject): JsonObject? = catching {
            json(payload(_state.value.from(MailrJson.decodeFromJsonElement(ComposePayload.serializer(), server))))
        }.getOrNull()
        override fun apply(content: JsonObject, clean: Boolean) {
            val p = catching { MailrJson.decodeFromJsonElement(ComposePayload.serializer(), content) }.getOrNull() ?: return
            val s = _state.value.from(p)
            _state.value = s
            loadPreviews(s.blocks)
            if (clean) {
                dirty = false
                saveJob?.cancel()
            } else {
                dirty = true
                edits++
                scheduleLocal()
                scheduleSave()
            }
        }
        override fun saveNow() {
            saveJob?.cancel()
            dirty = true
            viewModelScope.launch { this@ComposeViewModel.saveNow() }
        }
        override fun show(field: String, value: JsonElement?): String = when {
            value == null -> ""
            field == "html" -> io.github.murtazanasir.mailr.ui.util.Format.htmlToText((value as? JsonPrimitive)?.content.orEmpty()).trim()
            field in setOf("to", "cc", "bcc") -> catching {
                MailrJson.decodeFromJsonElement(kotlinx.serialization.builtins.ListSerializer(Address.serializer()), value)
                    .joinToString(", ") { it.display }
            }.getOrDefault(value.toString())
            else -> super.show(field, value)
        }
    }

    /** Live editing of this draft (data/live/LiveSession). */
    val live = LiveSession("draft", args.draftId?.toString(), g.client.clientId, port, viewModelScope, MailrLiveApi(g.api))
    val liveUi: StateFlow<LiveUi> get() = live.ui

    init {
        g.drafts.opened(localId)
        loadJob = viewModelScope.launch { load() }
        viewModelScope.launch { followIdentities() }
        viewModelScope.launch { g.live.events.collect { live.onEvent(it) } }
    }

    private fun json(p: ComposePayload): JsonObject = MailrJson.encodeToJsonElement(ComposePayload.serializer(), p).jsonObject

    /** The draft's server id is known (opened, or saved for the first time): live editing follows it. */
    private fun adoptDraftId(id: Long) {
        val old = live.id
        if (old == id.toString()) return
        old?.let { o -> owner?.let { OpenEditors.unregister(OpenEditors.key("draft", o), it) } }
        live.id = id.toString()
        owner?.let { OpenEditors.register(OpenEditors.key("draft", id), it) }
    }

    private fun adoptLocal(id: String) {
        if (id == localId) return
        g.drafts.released(localId)
        localId = id
        g.drafts.opened(id)
    }

    /**
     * The sending addresses (GET /me) come in on their own: a late answer, or a refresh while the
     * composer is open, fills the From line only when nothing chose an address yet, and never
     * touches what the user typed.
     */
    private suspend fun followIdentities() {
        if (g.mail.me.value == null) viewModelScope.launch {
            try { g.mail.refreshMe() } catch (e: CancellationException) { throw e } catch (e: Exception) { /* none: no From line */ }
        }
        g.mail.me.collect { me ->
            val ids = me?.identities ?: return@collect
            val default = ids.firstOrNull { it.isDefault } ?: ids.firstOrNull()
            _state.update { s -> s.copy(identities = ids, identityId = s.identityId ?: default?.id) }
        }
    }

    /**
     * Fill the composer from its stored copy. Until that copy is known the fields are not shown
     * ([ComposeState.loading]); a copy kept on this phone (Room, survives process death) shows at
     * once and stays editable, and the server's copy is merged into it afterwards (live.open).
     */
    private suspend fun load() {
        saved?.set(KEY_LOCAL, localId)
        val restored = args.restoreKey?.let { RestoreStore.take(it) }
        restored?.localId?.let { adoptLocal(it); saved?.set(KEY_LOCAL, it) }
        // A local copy of this composer (after process death, or reopened from Pending changes).
        val local = try { g.drafts.get(localId) } catch (e: CancellationException) { throw e } catch (e: Exception) { null }
        var p: ComposePayload? = null
        var draftId: Long? = null
        // The draft's live version as the server has it (null: not known, the first save replaces).
        var version: Int? = null
        // A copy handed back without its version (Undo send): read-only until the latest copy is read.
        var waitForLatest = false
        var error: String? = null
        try {
            when {
                local != null -> {
                    p = g.drafts.payloadOf(local)
                    draftId = local.draftId
                    // Not on the server yet: Back saves it again (and says so).
                    if (!local.synced) dirty = true
                }
                restored != null -> {
                    p = restored.payload
                    draftId = restored.draftId
                    waitForLatest = draftId != null
                }
                args.draftId != null -> {
                    val d = g.compose.loadDraftDetail(args.draftId)
                    p = d.payload
                    draftId = args.draftId
                    version = d.version
                }
                // ACT mail.reply @android.reader, ACT mail.forward @android.reader: the server prefills recipients, subject and quote.
                args.replyToMessageId != null -> p = g.compose.prefill(args.replyToMessageId, args.mode ?: "reply")
                // A mailto: link or a new message: the fields it carries (cc and bcc show when set).
                else -> p = ComposePayload(
                    to = Mailto.parseAddresses(args.to), cc = Mailto.parseAddresses(args.cc), bcc = Mailto.parseAddresses(args.bcc),
                    subject = args.subject.orEmpty(), text = args.body.orEmpty(),
                )
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            error = e.userMessage()
        }
        _state.update { cur ->
            (p?.let { cur.taking(it) } ?: cur).copy(
                loading = waitForLatest,
                loadingText = if (waitForLatest) AppText.get(R.string.compose_loading_latest) else cur.loadingText,
                draftId = draftId ?: cur.draftId,
                error = error ?: cur.error,
            )
        }
        // A suggested reply (ACT mail.reply_suggestions @android.reader) starts the reply's text.
        val chip = args.body?.takeIf { it.isNotBlank() && args.replyToMessageId != null && local == null && restored == null && p != null }
        if (chip != null) edit { st ->
            val first = st.blocks.firstOrNull { it is BodyBlock.Text } as? BodyBlock.Text
            if (first == null) st.copy(blocks = ComposeBody.ofText(chip) + st.blocks)
            else st.copy(blocks = ComposeBody.setText(st.blocks, first.key, if (first.text.isBlank()) chip else chip + "\n\n" + first.text))
        }
        val s = _state.value
        loadPreviews(s.blocks)
        s.draftId?.let { adoptDraftId(it) }
        if (version != null) live.start(version, json(payload(s)))
        // A copy kept on this phone (or handed back by Undo send): the server's version may be newer.
        else if (s.draftId != null) {
            // A local copy the server already had is a copy the server stored once: typing on it
            // merges with the server's newer copy against it. An unsynced copy wins as it is.
            if (local != null && local.synced) live.start(null, json(payload(s)))
            live.open()
            kotlin.coroutines.coroutineContext.ensureActive()
        }
        if (waitForLatest) _state.update { it.copy(loading = false) }
    }

    /** A stored copy or prefill into the composer: only the fields the user has not changed. */
    private fun ComposeState.taking(p: ComposePayload): ComposeState {
        val l = from(p)
        val t = touched
        return copy(
            identityId = if ("identity" in t) identityId else l.identityId,
            to = if ("to" in t) to else l.to,
            cc = if ("cc" in t) cc else l.cc,
            bcc = if ("bcc" in t) bcc else l.bcc,
            showCc = showCc || l.showCc,
            subject = if ("subject" in t) subject else l.subject,
            blocks = if ("body" in t) blocks else l.blocks,
            signature = if ("body" in t) signature ?: l.signature else l.signature,
            quoted = l.quoted ?: quoted,
            attachments = if ("attachments" in t) attachments else l.attachments,
            inReplyTo = l.inReplyTo ?: inReplyTo,
            forwardOf = l.forwardOf ?: forwardOf,
        )
    }

    /** Fetch the uploads of a draft's inline images, to show them. */
    private fun loadPreviews(blocks: List<BodyBlock>) {
        blocks.filterIsInstance<BodyBlock.Image>().filter { it.blob != null }.forEach { b ->
            viewModelScope.launch {
                g.compose.inlineImage(b.blob!!)?.let { bytes -> _state.update { it.copy(previews = it.previews + (b.key to bytes)) } }
            }
        }
    }

    /** A payload into the editor. HTML is never flattened: what cannot be edited as plain text
     *  stays as it was written (ComposeBody.parse). */
    private fun ComposeState.from(p: ComposePayload): ComposeState {
        val parsed = if (p.html == null) ParsedBody(ComposeBody.ofText(p.text.orEmpty()), null, null) else ComposeBody.parse(p.html)
        return copy(
            identityId = p.identityId ?: identityId,
            to = p.to, cc = p.cc, bcc = p.bcc, showCc = p.cc.isNotEmpty() || p.bcc.isNotEmpty(),
            subject = p.subject,
            blocks = parsed.blocks, signature = parsed.signature,
            quoted = parsed.quote, attachments = p.attachments,
            inReplyTo = p.inReplyToMessageId, forwardOf = p.forwardOfMessageId,
        )
    }

    fun payload(s: ComposeState = _state.value): ComposePayload {
        val identity = s.identities.firstOrNull { it.id == s.identityId }
        val sig = s.signature ?: if (s.quoted != null) "" else
            identity?.signatureHtml?.takeIf { it.isNotBlank() }?.let { "<br><div class=\"mailr_signature\">--<br>$it</div>" }.orEmpty()
        val html = ComposeBody.toHtml(s.blocks) + sig + (s.quoted ?: "")
        return ComposePayload(
            identityId = s.identityId, to = s.to, cc = s.cc, bcc = s.bcc, subject = s.subject, html = html,
            inReplyToMessageId = s.inReplyTo, forwardOfMessageId = s.forwardOf, attachments = s.attachments,
        )
    }

    private fun edit(block: (ComposeState) -> ComposeState) {
        var before: ComposeState? = null
        var after: ComposeState? = null
        _state.update { b -> before = b; block(b).also { after = it } }
        touched += changed(before!!, after!!)
        dirty = true
        edits++
        live.typed()
        scheduleLocal()
        scheduleSave()
    }

    private fun changed(a: ComposeState, b: ComposeState): Set<String> = buildSet {
        if (a.identityId != b.identityId) add("identity")
        if (a.to != b.to) add("to")
        if (a.cc != b.cc) add("cc")
        if (a.bcc != b.bcc) add("bcc")
        if (a.subject != b.subject) add("subject")
        if (a.blocks != b.blocks) add("body")
        if (a.attachments != b.attachments) add("attachments")
    }

    /** The local copy follows the text closely; the server save waits for a pause in typing. */
    private fun scheduleLocal() {
        localJob?.cancel()
        localJob = viewModelScope.launch {
            delay(LOCAL_DELAY_MS)
            writeLocal()
        }
    }

    private suspend fun writeLocal() {
        val s = _state.value
        if (!dirty || !s.hasContent || s.sending || s.loading) return
        catching { g.drafts.write(localId, payload(s), s.draftId) }
    }

    private fun scheduleSave() {
        saveJob?.cancel()
        saveJob = viewModelScope.launch {
            delay(SAVE_DELAY_MS)
            // From here the save runs to its answer: cancelling it mid-request would lose the
            // version the server gave it, and the next save would go from an older base.
            saveJob = null
            saveNow(auto = true)
        }
    }

    /**
     * ACT draft.save @android.editor: autosave after an edit, and on close. The text goes into Room first, then
     * to the server. "Saved" means the server has it; when it cannot be reached the status says
     * the text is on this phone, and it syncs later. Autosave waits while an inline image
     * uploads (as on the web); the upload's end saves. True when the server has the text.
     */
    private suspend fun saveNow(auto: Boolean = false): Boolean = saveLock.withLock { saveOnce(auto) }

    private suspend fun saveOnce(auto: Boolean): Boolean {
        // What the screen shows now: a save queued behind another sends the newer text.
        val s = _state.value
        if (!dirty || !s.hasContent || s.sending || s.loading) return true
        if (auto && s.imagesUploading > 0) return true
        val at = edits
        _state.update { it.copy(saveStatus = AppText.get(R.string.compose_saving)) }
        localJob?.cancel()
        return try {
            val sent = payload(s)
            g.drafts.write(localId, sent, s.draftId)
            // The base version goes with the save: changes saved since then merge in (or come back as a conflict).
            when (val r = g.drafts.sync(localId, live.baseVersion)) {
                is io.github.murtazanasir.mailr.data.repo.LocalDrafts.SaveResult.Saved -> {
                    // Typed on while the save was out: that text is not saved yet (its own save is queued).
                    if (edits == at) dirty = false
                    _state.update { it.copy(draftId = r.draftId) }
                    adoptDraftId(r.draftId)
                    // The answer rebases the editor; newer typing stays on top of it (LiveSession.saved).
                    live.saved(json(r.sent ?: sent), r.version, r.merged, r.stored)
                    // "Saved" only when the screen shows what the server keeps.
                    _state.update { it.copy(saveStatus = if (dirty) AppText.get(R.string.compose_saving) else AppText.get(R.string.compose_saved)) }
                    true
                }
                is io.github.murtazanasir.mailr.data.repo.LocalDrafts.SaveResult.Conflict -> {
                    live.refused(r.body)
                    _state.update { it.copy(saveStatus = AppText.get(R.string.compose_not_saved)) }
                    false
                }
                else -> {
                    _state.update { it.copy(saveStatus = SAVED_LOCALLY) }
                    false
                }
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            _state.update { it.copy(saveStatus = AppText.get(R.string.compose_not_saved)) }
            false
        }
    }

    // ---------- fields ----------

    fun setIdentity(id: Long) = edit { it.copy(identityId = id) }
    fun setSubject(v: String) = edit { it.copy(subject = v) }
    /** The whole body as plain text when it is one text block; else the first text block. */
    fun setBody(v: String) = edit { s ->
        val first = s.blocks.firstOrNull { it is BodyBlock.Text }
        if (first == null) s.copy(blocks = ComposeBody.ofText(v)) else s.copy(blocks = ComposeBody.setText(s.blocks, first.key, v))
    }

    fun setText(key: Long, v: String) = edit { it.copy(blocks = ComposeBody.setText(it.blocks, key, v)) }
    fun setCursor(key: Long, offset: Int) { cursor = key to offset }

    /** "Edit as plain text" on HTML kept from a draft: only that block loses its formatting. */
    fun editAsText(key: Long) = edit { it.copy(blocks = ComposeBody.toText(it.blocks, key)) }

    /** Remove an inline image (or another block) from the body. */
    fun removeBlock(key: Long) = edit { it.copy(blocks = ComposeBody.remove(it.blocks, key), previews = it.previews - key) }

    // ---------- inline photos (ACT mail.insert_image @android.editor) ----------

    /** Photos from the system photo picker or the camera go into the body at the cursor. */
    fun insertPhotos(resolver: ContentResolver, uris: List<Uri>) {
        uris.forEach { uri ->
            viewModelScope.launch {
                val read = withContext(Dispatchers.IO) {
                    catching {
                        var name = "photo"
                        resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
                            if (c.moveToFirst() && !c.isNull(0)) name = c.getString(0)
                        }
                        Triple(resolver.openInputStream(uri)?.use { it.readBytes() } ?: error("cannot read"), name, resolver.getType(uri))
                    }.getOrNull()
                }
                if (read == null) _state.update { it.copy(error = AppText.get(R.string.compose_photo_unreadable)) }
                else insertPhoto(read.first, read.second, read.third)
            }
        }
    }

    /** Check and reduce a photo like the web, put it in at the cursor with an upload bar, and
     *  upload it. Until the upload ends the image is left out of saves. */
    fun insertPhoto(bytes: ByteArray, name: String, type: String?) {
        viewModelScope.launch {
            val p = try {
                withContext(Dispatchers.Default) { InlinePhotos.prepare(bytes, name, type) }
            } catch (e: InlinePhotos.Refused) {
                _state.update { it.copy(error = e.message) }
                return@launch
            } catch (e: Exception) {
                _state.update { it.copy(error = AppText.get(R.string.compose_image_unreadable, name)) }
                return@launch
            }
            val (w, h) = p.shown
            val block = BodyBlock.Image(ComposeBody.nextKey(), null, w, h)
            _state.update { s ->
                val blocks = ComposeBody.insert(s.blocks, cursor?.first, cursor?.second ?: Int.MAX_VALUE, block)
                // The cursor moves to the text after the image, so a second photo follows the first.
                blocks.getOrNull(blocks.indexOf(block) + 1)?.let { cursor = it.key to 0 }
                s.copy(blocks = blocks, previews = s.previews + (block.key to p.bytes))
            }
            try {
                val blob = g.compose.uploadInline(p.bytes, p.filename, p.type)
                edit { s -> s.copy(blocks = s.blocks.map { if (it.key == block.key && it is BodyBlock.Image) it.copy(blob = blob) else it }) }
            } catch (e: Exception) {
                _state.update { s -> s.copy(blocks = ComposeBody.remove(s.blocks, block.key), previews = s.previews - block.key,
                    error = AppText.get(R.string.compose_image_not_inserted, name, e.userMessage())) }
            }
        }
    }
    /** ACT template.insert @android.editor: the user's templates for the sheet. */
    suspend fun templates(): List<io.github.murtazanasir.mailr.data.api.MailTemplate> = g.api.templates()

    /** A template, its variables filled for the first recipient and the From address, at the cursor; its subject when the subject is empty. */
    fun insertTemplate(t: io.github.murtazanasir.mailr.data.api.MailTemplate) {
        viewModelScope.launch {
            val s0 = _state.value
            val x = try {
                g.api.expandTemplate(t.id, s0.to + s0.cc, s0.identityId)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _state.update { it.copy(error = AppText.get(R.string.compose_template_failed, e.userMessage())) }
                return@launch
            }
            val text = templateText(x.bodyHtml)
            edit { s ->
                val at = cursor
                val block = (s.blocks.firstOrNull { it.key == at?.first } ?: s.blocks.firstOrNull { it is BodyBlock.Text }) as? BodyBlock.Text
                val blocks = if (block == null) ComposeBody.ofText(text) + s.blocks
                    else ComposeBody.setText(s.blocks, block.key, insertAt(block.text, if (at?.first == block.key) at.second else null, text))
                s.copy(blocks = blocks, subject = if (s.subject.isBlank() && x.subject.isNotBlank()) x.subject else s.subject)
            }
        }
    }

    fun toggleCc() = _state.update { it.copy(showCc = !it.showCc) }
    fun toggleQuoted() = _state.update { it.copy(showQuoted = !it.showQuoted) }

    fun addRecipient(field: Field, raw: String): Boolean {
        val parts = raw.split(',', ';', ' ').map { it.trim() }.filter { it.isNotEmpty() }
        val valid = parts.filter { EMAIL.matches(it) }
        if (valid.isEmpty()) return false
        val add = valid.map { Address(it) }
        edit { s ->
            when (field) {
                Field.TO -> s.copy(to = (s.to + add).distinctBy { it.email.lowercase() })
                Field.CC -> s.copy(cc = (s.cc + add).distinctBy { it.email.lowercase() })
                Field.BCC -> s.copy(bcc = (s.bcc + add).distinctBy { it.email.lowercase() })
            }.copy(suggestions = emptyList(), suggestFor = null)
        }
        return true
    }

    fun addPerson(field: Field, p: Person) {
        val a = Address(p.email, p.name.ifBlank { null })
        edit { s ->
            when (field) {
                Field.TO -> s.copy(to = (s.to + a).distinctBy { it.email.lowercase() })
                Field.CC -> s.copy(cc = (s.cc + a).distinctBy { it.email.lowercase() })
                Field.BCC -> s.copy(bcc = (s.bcc + a).distinctBy { it.email.lowercase() })
            }.copy(suggestions = emptyList(), suggestFor = null)
        }
    }

    fun removeRecipient(field: Field, a: Address) = edit { s ->
        when (field) {
            Field.TO -> s.copy(to = s.to - a)
            Field.CC -> s.copy(cc = s.cc - a)
            Field.BCC -> s.copy(bcc = s.bcc - a)
        }
    }

    fun query(field: Field, text: String) {
        suggestJob?.cancel()
        if (text.length < 2) {
            _state.update { it.copy(suggestions = emptyList(), suggestFor = null) }
            return
        }
        suggestJob = viewModelScope.launch {
            delay(150)
            val found = g.compose.suggest(text)
            val taken = _state.value.let { s -> (s.to + s.cc + s.bcc).map { it.email.lowercase() }.toSet() }
            _state.update { it.copy(suggestions = found.filter { p -> p.email.lowercase() !in taken }, suggestFor = field) }
        }
    }

    fun attach(resolver: ContentResolver, uris: List<Uri>) {
        if (uris.isEmpty()) return
        _state.update { it.copy(uploading = it.uploading + uris.size) }
        uris.forEach { uri ->
            viewModelScope.launch {
                try {
                    val a = g.compose.upload(resolver, uri)
                    edit { it.copy(attachments = it.attachments + a) }
                } catch (e: Exception) {
                    _state.update { it.copy(error = AppText.get(R.string.compose_attach_failed, e.userMessage())) }
                } finally {
                    _state.update { it.copy(uploading = it.uploading - 1) }
                }
            }
        }
    }

    fun removeAttachment(a: ComposeAttachment) = edit { it.copy(attachments = it.attachments - a) }

    /** Undo of [removeAttachment]: the file comes back (once). */
    fun restoreAttachment(a: ComposeAttachment) = edit { if (a in it.attachments) it else it.copy(attachments = it.attachments + a) }
    fun notify(message: String) = _state.update { it.copy(error = message) }
    fun clearError() = _state.update { it.copy(error = null) }

    // ---------- finish ----------

    /** ACT draft.send @android.editor (at = null), ACT draft.schedule @android.editor (at = the chosen time). */
    fun send(at: ZonedDateTime? = null) {
        val s = _state.value
        // A second Send while the first is on its way (a double tap) does nothing.
        if (s.sending) return
        if (s.to.isEmpty() && s.cc.isEmpty() && s.bcc.isEmpty()) {
            _state.update { it.copy(error = AppText.get(R.string.compose_need_recipient)) }
            return
        }
        if (s.uploading > 0 || s.imagesUploading > 0) {
            _state.update { it.copy(error = if (s.imagesUploading > 0) AppText.get(R.string.compose_wait_images) else AppText.get(R.string.compose_wait_attachments)) }
            return
        }
        saveJob?.cancel()
        localJob?.cancel()
        _state.update { it.copy(sending = true) }
        viewModelScope.launch {
            // The text is on this phone before it leaves, so a failed send loses nothing.
            if (dirty) catching { g.drafts.write(localId, payload(), s.draftId) }
            // A draft the worker saved meanwhile is the one the server replaces.
            val draftId = catching { g.drafts.get(localId)?.draftId }.getOrNull() ?: s.draftId
            val key = sendKey ?: java.util.UUID.randomUUID().toString().also { sendKey = it }
            try {
                val ob = g.compose.send(payload(), draftId, at?.toOffsetDateTime()?.toString(), key)
                sendKey = null
                closed = true
                catching { g.drafts.delete(localId) }
                _done.emit(ComposeResult.Sent(ob, scheduled = at != null))
            } catch (e: Exception) {
                val offline = e is java.io.IOException
                // No answer: the send may have landed, so the next Send repeats this attempt's key;
                // an answer (a refusal with a reason) ends the attempt.
                if (!offline) sendKey = null
                _state.update { it.copy(sending = false, draftId = draftId,
                    error = if (offline) AppText.get(R.string.compose_offline_not_sent)
                        else e.userMessage()) }
            }
        }
    }

    /**
     * Back. With text, the composer saves first and says where the text is: "Saved to Drafts"
     * only when the server has its last version, else "Saved on this phone, will sync". It
     * never reports Discarded while there is unsaved text.
     */
    fun close() {
        viewModelScope.launch {
            saveJob?.cancel()
            localJob?.cancel()
            if (_state.value.loading) {
                // Closed before its copy arrived: nothing was shown, so nothing to save; a copy
                // kept on this phone stays (and syncs) as it was.
                loadJob?.cancel()
                val row = try { g.drafts.get(localId) } catch (e: CancellationException) { throw e } catch (e: Exception) { null }
                closed = true
                catching { if (row != null) g.drafts.close(localId) }
                _done.emit(if (row != null && !row.synced) ComposeResult.SavedLocally else ComposeResult.Discarded)
                return@launch
            }
            val s = _state.value
            if (s.hasContent && dirty) saveNow()
            val row = catching { g.drafts.get(localId) }.getOrNull()
            val result = when {
                !s.hasContent -> if (s.draftId != null) ComposeResult.SavedDraft else ComposeResult.Discarded
                row != null && !row.synced -> ComposeResult.SavedLocally
                s.draftId != null || row?.draftId != null -> ComposeResult.SavedDraft
                else -> ComposeResult.Discarded
            }
            closed = true
            catching { if (result == ComposeResult.Discarded) g.drafts.delete(localId) else g.drafts.close(localId) }
            _done.emit(result)
        }
    }

    /** ACT draft.discard @android.editor */
    fun discard() {
        viewModelScope.launch {
            saveJob?.cancel()
            localJob?.cancel()
            val draftId = catching { g.drafts.get(localId)?.draftId }.getOrNull() ?: _state.value.draftId
            val kept = _state.value.takeIf { it.hasContent }?.let { payload(it) }
            closed = true
            catching { g.drafts.delete(localId) }
            draftId?.let { g.compose.discardDraft(it) }
            _done.emit(if (kept != null) ComposeResult.DiscardedDraft(kept) else ComposeResult.Discarded)
        }
    }

    /** Close, Send or Discard ran; otherwise the composer went away with its text (onCleared). */
    private var closed = false

    // ---------- live editing ----------

    /** "Use mine" on the conflict bar: this copy is saved over the other one. */
    fun useMine() = live.useMine()
    /** "Use the assistant's": the other copy replaces this one. */
    fun useTheirs() = live.useTheirs()
    /** Undo on "Updated by …": the text from before comes back, saved as a new version. */
    fun undoLive() = live.undo()
    fun dismissLive() = live.dismissNotice()

    override fun onCleared() {
        live.id?.let { d -> owner?.let { OpenEditors.unregister(OpenEditors.key("draft", d), it) } }
        val s = _state.value
        val id = localId
        if (closed) return
        // Left without Back (the share sheet finished, the task was removed): keep the text and sync it.
        g.scope.launch {
            catching {
                if (s.hasContent && dirty && !s.sending) g.drafts.write(id, payload(s), s.draftId)
                g.drafts.close(id)
                if (g.drafts.get(id)?.synced == false) g.drafts.sync(id)
            }
        }
    }

    companion object {
        const val KEY_LOCAL = "local_draft"
        const val LOCAL_DELAY_MS = 400L
        /** Autosave: this long after the last edit (and at once on close). */
        const val SAVE_DELAY_MS = 1500L
        val SAVED_LOCALLY: String get() = AppText.get(R.string.compose_saved_locally)

        fun titleOf(a: ComposeArgs): String = when {
            a.draftId != null -> AppText.get(R.string.compose_title_draft)
            a.mode == "forward" -> AppText.get(R.string.compose_title_forward)
            a.mode == "reply_all" -> AppText.get(R.string.compose_title_reply_all)
            a.replyToMessageId != null -> AppText.get(R.string.compose_title_reply)
            else -> AppText.get(R.string.compose_title_compose)
        }

        fun loadingTextOf(a: ComposeArgs): String = when {
            a.draftId != null -> AppText.get(R.string.compose_opening_draft)
            a.replyToMessageId != null && a.mode == "forward" -> AppText.get(R.string.compose_opening_forward)
            a.replyToMessageId != null -> AppText.get(R.string.compose_opening_reply)
            else -> AppText.get(R.string.compose_loading)
        }

        val EMAIL = Regex("^[^@\\s,;<>]+@[^@\\s,;<>]+\\.[^@\\s,;<>]+$")
    }
}
