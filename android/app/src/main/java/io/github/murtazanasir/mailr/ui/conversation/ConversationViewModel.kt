package io.github.murtazanasir.mailr.ui.conversation

import io.github.murtazanasir.mailr.util.catching

import android.content.Context
import android.content.Intent
import androidx.core.content.FileProvider
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.data.api.Attachment
import io.github.murtazanasir.mailr.data.api.ConversationDetail
import io.github.murtazanasir.mailr.data.api.EventDraft
import io.github.murtazanasir.mailr.data.api.FromMessageDraft
import io.github.murtazanasir.mailr.data.api.MessageBody
import io.github.murtazanasir.mailr.data.api.AssistantRun
import io.github.murtazanasir.mailr.data.api.BriefResponse
import io.github.murtazanasir.mailr.data.api.SummaryResponse
import io.github.murtazanasir.mailr.data.api.TaskDraft
import io.github.murtazanasir.mailr.data.api.userMessage
import io.github.murtazanasir.mailr.ui.mail.UiMessage
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import kotlinx.serialization.json.contentOrNull
import java.io.File
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText

/** A task or event being made from a message: [suggestion] is null while the server reads it. */
data class FromEmail(val kind: String, val messageId: Long, val suggestion: FromMessageDraft? = null, val saving: Boolean = false,
                     /** A meeting ("Meeting with these people"): the thread's people as guests. */
                     val guests: List<io.github.murtazanasir.mailr.data.api.Address> = emptyList())

sealed interface BodyState {
    data object Loading : BodyState
    data class Ready(val body: MessageBody, val imagesAllowed: Boolean) : BodyState
    data class Failed(val message: String) : BodyState
}

class ConversationViewModel(
    private val g: AppGraph,
    val conversationId: Long,
    val focusMessageId: Long?,
    val quote: String?,
) : ViewModel() {

    val detail: StateFlow<ConversationDetail?> =
        g.mail.conversationFlow(conversationId).stateIn(viewModelScope, SharingStarted.Eagerly, null)

    private val _loadError = MutableStateFlow<String?>(null)
    val loadError: StateFlow<String?> = _loadError.asStateFlow()

    private val _expanded = MutableStateFlow<Set<Long>>(emptySet())
    val expanded: StateFlow<Set<Long>> = _expanded.asStateFlow()

    private val _bodies = MutableStateFlow<Map<Long, BodyState>>(emptyMap())
    val bodies: StateFlow<Map<Long, BodyState>> = _bodies.asStateFlow()

    /** ACT brief.read @android.reader: the assistant's brief on this conversation. */
    private val _brief = MutableStateFlow<AssistantRun?>(null)
    val brief: StateFlow<AssistantRun?> = _brief.asStateFlow()
    /** The assistant's name (Settings → Ambrose → About you); "Ambrose" until the user names it. */
    val assistantName: StateFlow<String> = io.github.murtazanasir.mailr.i18n.AssistantName.name

    /** ACT mail.to_task @android.reader, ACT mail.to_event @android.reader: the task or event being made from this email (null: none). */
    private val _fromEmail = MutableStateFlow<FromEmail?>(null)
    val fromEmail: StateFlow<FromEmail?> = _fromEmail.asStateFlow()

    private val _summary = MutableStateFlow<SummaryResponse?>(null)
    val summary: StateFlow<SummaryResponse?> = _summary.asStateFlow()

    private val _messages = MutableSharedFlow<UiMessage>(extraBufferCapacity = 4)
    val messages: SharedFlow<UiMessage> = _messages

    /** Emits when the conversation left the current view (archived, deleted…) so the pane closes. */
    private val _closed = MutableSharedFlow<Unit>(extraBufferCapacity = 1)
    val closed: SharedFlow<Unit> = _closed

    /** Invitations, suggested replies and send states (ReaderExtras.kt). */
    val extras = ReaderExtras(g, conversationId, viewModelScope) { _messages.emit(it) }

    init {
        viewModelScope.launch {
            detail.collect { d ->
                if (d != null) extras.onDetail(d, (g.mail.me.value?.settings?.get("reply_suggestions") as? kotlinx.serialization.json.JsonPrimitive)?.content != "false")
            }
        }
        viewModelScope.launch { load() }
        viewModelScope.launch { loadBrief() }
        viewModelScope.launch {
            g.mail.invalidations.collect {
                if ("conversation" in it || "message" in it) catching { g.mail.refreshConversation(conversationId) }
                if ("assistant" in it || "conversation" in it) loadBrief()
            }
        }
    }

    private suspend fun load() {
        try {
            val d = g.mail.refreshConversation(conversationId)
            _loadError.value = null
            initExpansion(d)
            if (d.conversation.unread) g.mail.act(listOf(conversationId), "read")
        } catch (e: Exception) {
            val cached = detail.value
            if (cached != null) initExpansion(cached) else _loadError.value = e.userMessage()
        }
    }

    private suspend fun loadBrief() {
        catching { g.api.client.get("conversations/$conversationId/brief", BriefResponse.serializer()) }
            .onSuccess { _brief.value = it.brief?.takeIf { b -> b.output.isNotBlank() } }
        if (_brief.value != null) catching {
            g.api.client.get("assistant/profile", kotlinx.serialization.json.JsonObject.serializer())
        }.getOrNull()?.let { p -> io.github.murtazanasir.mailr.i18n.AssistantName.set(io.github.murtazanasir.mailr.i18n.AssistantName.fromProfile(p)) }
    }

    fun retry() {
        viewModelScope.launch { load() }
    }

    private fun initExpansion(d: ConversationDetail) {
        if (_expanded.value.isNotEmpty()) return
        val msgs = d.messages
        val open = buildSet {
            msgs.lastOrNull()?.let { add(it.id) }
            msgs.filter { !it.seen || it.draft }.forEach { add(it.id) }
            focusMessageId?.let { add(it) }
        }
        _expanded.value = open
        open.forEach { loadBody(it) }
    }

    fun toggle(messageId: Long) {
        val now = _expanded.value
        _expanded.value = if (messageId in now) now - messageId else now + messageId
        if (messageId !in now) loadBody(messageId)
    }

    fun expandAll() {
        val ids = detail.value?.messages?.map { it.id } ?: return
        _expanded.value = ids.toSet()
        ids.forEach { loadBody(it) }
    }

    fun loadBody(messageId: Long, allowImages: Boolean? = null) {
        val current = _bodies.value[messageId]
        if (allowImages == null && current is BodyState.Ready) return
        viewModelScope.launch {
            // The web's image choice, or this phone's override (data/settings/ServerPrefs).
            val images = allowImages ?: g.settings.prefs.first().showImages()
            if (current !is BodyState.Ready) _bodies.update { it + (messageId to BodyState.Loading) }
            // Fetch first, then merge: `value + (id to fetch())` would read the map before the
            // suspension and drop bodies that parallel loads stored meanwhile.
            val result = try {
                val q = if (messageId == focusMessageId) quote else null
                val b = g.mail.body(messageId, images, q)
                // Spam never loads images on its own: the proxy's fetch would tell the sender
                // that the address is live. ACT mail.show_images @android.reader
                if (allowImages == null && images && b.junk) BodyState.Ready(g.mail.body(messageId, false, q), false)
                else BodyState.Ready(b, images)
            } catch (e: Exception) {
                BodyState.Failed(e.userMessage())
            }
            _bodies.update { it + (messageId to result) }
        }
    }

    /** The reader's own actions: ACT mail.archive @android.reader, ACT mail.delete @android.reader ("trash"), ACT mail.spam @android.reader. */
    fun act(action: String, labelIds: List<Long>? = null, until: String? = null, closes: Boolean = false) {
        viewModelScope.launch {
            try {
                val out = g.mail.act(listOf(conversationId), action, labelIds, until, contextLabelId = g.mail.contextLabelId)
                if (closes) _closed.tryEmit(Unit)
                val undo = out.undo
                val text = when (action) {
                    "archive" -> AppText.get(R.string.conv_done_archived)
                    "trash" -> AppText.get(R.string.conv_done_moved_trash)
                    "spam" -> AppText.get(R.string.conv_done_reported_spam)
                    "not_spam", "inbox" -> AppText.get(R.string.conv_done_moved_inbox)
                    "untrash" -> AppText.get(R.string.conv_done_restored_inbox)
                    "delete_forever" -> AppText.get(R.string.conv_done_deleted_forever)
                    "snooze" -> AppText.get(R.string.conv_done_snoozed)
                    "unread" -> AppText.get(R.string.conv_done_marked_unread)
                    "mute" -> AppText.get(R.string.conv_done_muted)
                    "follow_up" -> AppText.get(R.string.conv_done_follow_up_set)
                    "move" -> AppText.get(R.string.conv_done_moved)
                    "cancel_follow_up" -> AppText.get(R.string.conv_done_follow_up_cancelled)
                    else -> AppText.get(R.string.conv_done)
                } + if (out.queuedOffline) AppText.get(R.string.conv_will_sync_suffix) else ""
                _messages.emit(if (undo != null) UiMessage(text, AppText.get(R.string.conv_undo)) { g.mail.undo(undo) } else UiMessage(text))
                if (!closes) g.mail.refreshConversation(conversationId)
            } catch (e: Exception) {
                _messages.emit(UiMessage(e.userMessage()))
            }
        }
    }

    /**
     * ACT mail.to_task @android.reader, ACT mail.to_event @android.reader: asks the server what the newest message says
     * (POST /calendar/from-message; nothing is saved) and opens the editor on it. Without a
     * suggestion the editor starts from the subject.
     */
    fun fromEmail(kind: String) {
        val d = detail.value ?: return
        val m = d.messages.lastOrNull { !it.draft } ?: return
        // ACT mail.schedule_meeting @android.reader: an event whose guests are the thread's people (not me).
        val guests = if (kind == "meeting") meetingGuests(d, myAddresses()) else emptyList()
        _fromEmail.value = FromEmail(kind, m.id, guests = guests)
        viewModelScope.launch {
            val draft = try {
                g.calendar.api.fromMessage(m.id, if (kind == "meeting") "event" else kind).draft
            } catch (e: java.io.IOException) {
                _fromEmail.value = null
                _messages.emit(UiMessage(e.userMessage()))
                return@launch
            } catch (e: Exception) {
                FromMessageDraft(title = m.subject.replace(Regex("^\\s*((re|fwd?)\\s*:\\s*)+", RegexOption.IGNORE_CASE), "").trim())
            }
            _fromEmail.update { f -> if (f?.messageId == m.id && f.kind == kind) f.copy(suggestion = draft) else f }
        }
    }

    fun closeFromEmail() { _fromEmail.value = null }

    /** My addresses: the sign-in address and every sending identity. */
    private fun myAddresses(): Set<String> = buildSet {
        g.sessions.session.value?.email?.lowercase()?.let { add(it) }
        g.mail.me.value?.let { me -> add(me.user.email.lowercase()); me.identities.forEach { add(it.email.lowercase()) } }
    }

    /** Save from the editor: POST /calendar/from-message/confirm, linked to this conversation. Undo deletes it. */
    fun saveFromEmail(task: TaskDraft? = null, event: EventDraft? = null) {
        val f = _fromEmail.value ?: return
        _fromEmail.value = f.copy(saving = true)
        viewModelScope.launch {
            try {
                val msg = if (task != null) {
                    val t = g.calendar.taskFromMessage(f.messageId, conversationId, task)
                    UiMessage(AppText.get(R.string.conv_added_to_tasks), AppText.get(R.string.conv_undo)) { g.calendar.deleteTask(t) }
                } else if (f.kind == "meeting") {
                    // The editor asked whether to invite the guests; the event links to this conversation.
                    val ev = event ?: return@launch
                    g.calendar.createEvent(ev.copy(conversationId = conversationId))
                    UiMessage(if (ev.sendUpdates == true && !ev.attendees.isNullOrEmpty()) AppText.get(R.string.conv_meeting_saved_invited)
                        else AppText.get(R.string.conv_meeting_saved_linked))
                } else {
                    val e = g.calendar.eventFromMessage(f.messageId, conversationId, event ?: return@launch)
                    UiMessage(AppText.get(R.string.conv_event_created), AppText.get(R.string.conv_undo)) { g.calendar.deleteEvent(e, "all") }
                }
                _fromEmail.value = null
                _messages.emit(msg)
            } catch (e: Exception) {
                _fromEmail.update { it?.copy(saving = false) }
                _messages.emit(UiMessage(e.userMessage()))
            }
        }
    }

    fun starMessage(messageId: Long, star: Boolean) {
        viewModelScope.launch {
            catching {
                g.api.messageAction(io.github.murtazanasir.mailr.data.api.ActionRequest(listOf(messageId), if (star) "star" else "unstar"),
                    java.util.UUID.randomUUID().toString())
                g.mail.refreshConversation(conversationId)
            }.onFailure { _messages.emit(UiMessage(it.userMessage())) }
        }
    }

    fun summarize() {
        viewModelScope.launch {
            _summary.value = SummaryResponse(status = "pending")
            repeat(20) {
                val s = catching {
                    g.api.client.get("conversations/$conversationId/summary", SummaryResponse.serializer())
                }.getOrElse { e -> SummaryResponse(status = "unavailable", reason = e.userMessage()) }
                _summary.value = s
                if (s.status != "pending") return@launch
                kotlinx.coroutines.delay(3000)
            }
        }
    }

    /**
     * ACT event.import_ics @android.reader: the .ics attachment into the default calendar (POST /calendar/import
     * with the message and its part). Events already in a calendar are skipped, not doubled.
     */
    fun importIcs(messageId: Long, a: Attachment) {
        viewModelScope.launch {
            val text = try {
                importSummary(g.calendar.importFromMessage(messageId, a.part))
            } catch (e: java.io.IOException) {
                AppText.get(R.string.conv_ics_offline)
            } catch (e: Exception) {
                AppText.get(R.string.conv_ics_could_not_add, a.filename, e.userMessage())
            }
            _messages.emit(UiMessage(text))
        }
    }

    /** Download an attachment to the cache and hand it to a viewer app. */
    fun openAttachment(context: Context, messageId: Long, a: Attachment) {
        viewModelScope.launch {
            try {
                val file = g.attachments.file(messageId, a)
                val uri = FileProvider.getUriForFile(context, context.packageName + ".files", file)
                val view = Intent(Intent.ACTION_VIEW).setDataAndType(uri, a.type)
                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
                context.startActivity(Intent.createChooser(view, a.filename).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            } catch (e: android.content.ActivityNotFoundException) {
                _messages.emit(UiMessage(AppText.get(R.string.conv_no_app_opens, a.filename)))
            } catch (e: Exception) {
                _messages.emit(UiMessage(e.userMessage()))
            }
        }
    }

    /** ACT mail.attachment_share @android.reader: the attachment, downloaded whole, through the share sheet. */
    fun shareAttachment(context: Context, messageId: Long, a: Attachment) {
        viewModelScope.launch {
            try {
                val file = g.attachments.file(messageId, a)
                context.startActivity(attachmentShareIntent(context, file, a))
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                _messages.emit(UiMessage(e.userMessage()))
            }
        }
    }

    /** ACT mail.attachment_save @android.reader: a copy in the phone's Downloads folder (MediaStore). */
    fun saveAttachment(context: Context, messageId: Long, a: Attachment) {
        viewModelScope.launch {
            val text = try {
                val file = g.attachments.file(messageId, a)
                io.github.murtazanasir.mailr.data.files.Downloads.save(context, file, io.github.murtazanasir.mailr.data.files.AttachmentCache.safeName(a.filename), a.type)
                AppText.get(R.string.conv_attachment_saved, a.filename)
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                AppText.get(R.string.conv_attachment_not_saved, a.filename, e.userMessage())
            }
            _messages.emit(UiMessage(text))
        }
    }
}

/** The line after an import: how many were added, already there, or failed. */
fun importSummary(r: io.github.murtazanasir.mailr.data.api.ImportResult): String {
    val n = r.imported.size
    val parts = buildList {
        if (n > 0) add(if (n == 1) AppText.get(R.string.conv_import_added_one, r.imported[0].summary.ifBlank { AppText.get(R.string.conv_import_one_event) })
            else AppText.plural(R.plurals.conv_import_added_n, n, n))
        if (r.skipped.isNotEmpty()) add(if (n == 0 && r.skipped.size == 1) AppText.get(R.string.conv_import_already)
            else AppText.plural(R.plurals.conv_import_already_n, r.skipped.size, r.skipped.size))
        if (r.failed.isNotEmpty()) add(AppText.plural(R.plurals.conv_import_failed_n, r.failed.size, r.failed.size))
    }
    return parts.joinToString(AppText.get(R.string.conv_import_separator)).ifEmpty { AppText.get(R.string.conv_import_nothing) }
}

/** The share sheet for a downloaded attachment: the file through the FileProvider, readable by the chosen app. */
fun attachmentShareIntent(context: Context, file: File, a: Attachment): Intent {
    val uri = FileProvider.getUriForFile(context, context.packageName + ".files", file)
    val send = Intent(Intent.ACTION_SEND).setType(a.type.ifBlank { "application/octet-stream" })
        .putExtra(Intent.EXTRA_STREAM, uri)
        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        .apply { clipData = android.content.ClipData.newRawUri(a.filename, uri) }
    return Intent.createChooser(send, a.filename).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_GRANT_READ_URI_PERMISSION)
}
