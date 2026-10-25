package io.github.murtazanasir.mailr.ui.assistant

import io.github.murtazanasir.mailr.util.catching
import io.github.murtazanasir.mailr.data.voice.DictationContext

import io.github.murtazanasir.mailr.time.AppClock
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.data.agent.AgentEvent
import io.github.murtazanasir.mailr.data.agent.AskAbout
import io.github.murtazanasir.mailr.data.agent.ChatSummary
import io.github.murtazanasir.mailr.data.agent.RenameResult
import io.github.murtazanasir.mailr.data.api.Approval
import io.github.murtazanasir.mailr.data.api.AssistantGate
import io.github.murtazanasir.mailr.data.api.ApprovalPreview
import io.github.murtazanasir.mailr.data.api.Citation
import io.github.murtazanasir.mailr.data.api.ComposePayload
import io.github.murtazanasir.mailr.data.api.userMessage
import io.github.murtazanasir.mailr.data.api.addNoteLink
import io.github.murtazanasir.mailr.data.api.createTextNote
import io.github.murtazanasir.mailr.data.voice.DictationOrder
import io.github.murtazanasir.mailr.data.voice.VoiceSource
import io.github.murtazanasir.mailr.ui.ask.AskCaptureModel
import io.github.murtazanasir.mailr.ui.ask.VoiceSetup
import kotlinx.coroutines.Job
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.json.contentOrNull
import kotlinx.coroutines.flow.receiveAsFlow
import io.github.murtazanasir.mailr.data.api.ApiException
import io.github.murtazanasir.mailr.ui.util.Format

/**
 * A user message, or an assistant turn as an ordered timeline of [items] (ChatTimeline.kt): text,
 * steps, approval cards (their data in [approvals]) and, after a decision, the continuation.
 */
data class ChatTurn(
    val role: String,                      // user | assistant
    val text: String = "",
    val items: List<TurnItem> = emptyList(),
    val citations: List<Citation> = emptyList(),
    val drafts: List<Pair<Long?, ComposePayload?>> = emptyList(),
    val approvals: List<Approval> = emptyList(),
    /** The notes that went into the prompt (the "Used memories" line). */
    val memoryIds: List<Long> = emptyList(),
    val error: String? = null,
    /** A reply the app wrote itself for a slash command (/copy with nothing to copy, …): not an answer. */
    val notice: Boolean = false,
    val streaming: Boolean = false,
    /** The stream dropped mid-answer and the app is re-attaching ("Reconnecting…"). */
    val reconnecting: Boolean = false,
    /** The turn never finished (its run was lost): "Interrupted", as on the web. */
    val interrupted: Boolean = false,
    /** Re-attaching gave up ("Connection lost"): the last such turn offers Retry. */
    val lost: Boolean = false,
    val startedAt: Long = AppClock.millis(),
    val finishedAt: Long? = null,
    /** A user turn's stored message id (a reopened chat), so a memory's "From a chat" can find it. */
    val messageId: Long? = null,
    /** The bulk reviews this turn started (review_mail): a progress line each, then its review card. */
    val reviews: List<Long> = emptyList(),
) {
    val steps: List<Step> get() = ChatTimeline.steps(items)
    /** The reply's text (every segment, in order) for Copy and the user role's message. */
    val answer: String get() = if (role == "user") text else ChatTimeline.text(items)
}

data class AssistantState(
    val turns: List<ChatTurn> = emptyList(),
    val busy: Boolean = false,
    val sessionId: String? = null,
    val contextConversation: Long? = null,
    val contextTitle: String? = null,
    /** The event or task "Ask assistant" attached; it goes with every turn while it is attached. */
    val about: AskAbout? = null,
    /** The user message a memory's "From a chat" opened this chat at: shown in view and marked. */
    val found: Long? = null,
    /**
     * Why the assistant is off for this user (GET /me `access`, or a `no_access` answer from the
     * chat); null while it is on. The screen shows it in place of the message box.
     */
    val blocked: String? = null,
    /** Ambrose's name (Settings → About you on the web): the bottom bar's label and this screen's title. */
    val name: String = io.github.murtazanasir.mailr.i18n.AssistantName.now(),
    /** An earlier chat is loading (opened from Chats); its turns replace the screen when they arrive. */
    val opening: Boolean = false,
    /** The open chat's title (the top bar shows it under the name); null for a new chat. */
    val title: String? = null,
    /** [title] is one the user gave (Rename), not the first question. */
    val titleRenamed: Boolean = false,
    /** The chat's slash commands (GET /agent/commands) and the user's skills, for the picker; empty until loaded. */
    val commands: List<ChatCommand> = emptyList(),
    val skills: List<SkillLite> = emptyList(),
    /** `/local`: this chat stays on the local model from the next message on (a chip shows it). */
    val localOnly: Boolean = false,
    /** The open chat's bulk reviews as last read (GET /agent/reviews/{id}), by review id. */
    val reviews: Map<Long, io.github.murtazanasir.mailr.data.api.ReviewState> = emptyMap(),
    /** Reviews whose Stop or Continue is on its way. */
    val reviewActing: Set<Long> = emptySet(),
    /** A review card's "Always …" filters made in this chat, by [alwaysKey], with the filter's id (null: not known). */
    val filtersMade: Map<String, Long?> = emptyMap(),
    /** "Always …" taps whose filter is on its way. */
    val filtersBusy: Set<String> = emptySet(),
)

/**
 * [saved]: the open chat's id survives the app's process being stopped (as the web keeps it for the
 * tab); the chat then comes back from the server ([reopen]), Used memories included.
 */
class AssistantViewModel(private val g: AppGraph, private val saved: SavedStateHandle? = null) : ViewModel() {
    private val _state = MutableStateFlow(AssistantState())
    val state: StateFlow<AssistantState> = _state.asStateFlow()
    private var job: Job? = null

    init {
        saved?.get<String>(SESSION_KEY)?.let { reopen(it) }
        // The name follows the one source (AssistantName), however it learned of a change.
        viewModelScope.launch { io.github.murtazanasir.mailr.i18n.AssistantName.name.collect { n -> _state.update { it.copy(name = n) } } }
    }

    /**
     * A chat from the server as it was (GET /agent/sessions/{id}): its turns with their steps, cards,
     * sources and the memories each answer used (SessionReplay). Nothing changes when it cannot load.
     * A turn that is still running on the server is followed again, as the web does.
     */
    fun reopen(sessionId: String) {
        if (_state.value.busy) return
        job = viewModelScope.launch {
            try {
                val session = g.agent.session(sessionId)
                if (_state.value.busy || (_state.value.turns.isNotEmpty() && _state.value.sessionId != sessionId)) return@launch
                _state.update { it.copy(turns = SessionReplay.turns(session), sessionId = sessionId, title = titleOf(session),
                    titleRenamed = renamedOf(session), reviews = emptyMap()) }
                if (SessionReplay.running(session)) followRunning()
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                keepSession(null)
            }
        }
    }

    /**
     * ACT agent.chat_open @android.assistant: an earlier chat from Chats. The open stream (if any) is
     * let go; its turn goes on at the server. A chat that does not load leaves the screen as it was.
     */
    fun open(sessionId: String) {
        if (sessionId == _state.value.sessionId && !_state.value.opening) return
        val old = job
        _state.update { it.copy(opening = true) }
        job = viewModelScope.launch {
            try {
                old?.cancelAndJoin()
                val session = g.agent.session(sessionId)
                keepSession(sessionId)
                _state.update { AssistantState(turns = SessionReplay.turns(session), sessionId = sessionId, blocked = it.blocked, name = it.name,
                    title = titleOf(session), titleRenamed = renamedOf(session), commands = it.commands, skills = it.skills) }
                if (SessionReplay.running(session)) followRunning()
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                _state.update { it.copy(opening = false) }
                _notices.trySend(AppText.get(R.string.chats_open_failed))
            }
        }
    }

    private val _notices = kotlinx.coroutines.channels.Channel<String>(kotlinx.coroutines.channels.Channel.BUFFERED)
    /** Short messages for the screen's snackbar ("Couldn't open the chat."). */
    val notices: kotlinx.coroutines.flow.Flow<String> = _notices.receiveAsFlow()

    /** A chat deleted from Chats: when it is the open one, the screen starts a new chat. */
    fun deleted(sessionId: String) {
        if (_state.value.sessionId == sessionId) newChat()
    }

    /** The last turn is still running at the server (a reopened chat): follow it from its first event. */
    private suspend fun followRunning() {
        updateLast { ChatTurn("assistant", streaming = true) }
        _state.update { it.copy(busy = true) }
        try {
            recover(0, null)
        } finally {
            updateLast { it.copy(streaming = false, finishedAt = it.finishedAt ?: AppClock.millis()) }
            _state.update { it.copy(busy = false) }
        }
    }

    /**
     * A memory's "From a chat" (ACT memory.open_chat @android.settings): the chat it came from, as it
     * was, ready to continue, with [messageId] marked. Returns null when it shows, else the words that
     * say why not (a reply still running, or the chat could not load).
     */
    suspend fun openChat(sessionId: String, messageId: Long?): String? {
        val running = { AppText.get(R.string.memory_chat_still_answering, _state.value.name) }
        if (_state.value.busy) return running()
        val session = try {
            g.agent.session(sessionId)
        } catch (e: kotlinx.coroutines.CancellationException) {
            throw e
        } catch (e: Exception) {
            return AppText.get(R.string.memory_chat_open_failed)
        }
        if (_state.value.busy) return running()
        _state.update { it.copy(turns = SessionReplay.turns(session), sessionId = sessionId, found = messageId, title = titleOf(session),
            titleRenamed = renamedOf(session), reviews = emptyMap()) }
        keepSession(sessionId)
        return null
    }

    private val _renames = kotlinx.coroutines.channels.Channel<ChatsMessage>(kotlinx.coroutines.channels.Channel.BUFFERED)
    /** "Chat renamed." with Undo, or why a rename failed, for the screen's snackbar. */
    val renames: kotlinx.coroutines.flow.Flow<ChatsMessage> = _renames.receiveAsFlow()

    /** A rename made elsewhere (Chats): the top bar follows when it is the open chat. */
    fun titleChanged(id: String, title: String, renamed: Boolean) {
        _state.update { if (it.sessionId == id) it.copy(title = title, titleRenamed = renamed) else it }
    }

    /**
     * ACT agent.chat_rename @android.assistant: the open chat renamed from the top bar (the title, or
     * ⋮ → Rename), as Chats does: the new title shows at once and the snackbar offers Undo.
     */
    fun renameChat(typed: String) {
        val st = _state.value
        val id = st.sessionId ?: return
        val c = ChatSummary(id, st.title.orEmpty(), renamed = st.titleRenamed)
        val body = renameTitleBody(typed, c) ?: return
        if (body.isNotEmpty()) titleChanged(id, body, true)
        viewModelScope.launch {
            try {
                val r = g.agent.renameSession(id, body)
                titleChanged(id, r.title, r.renamed)
                _renames.trySend(ChatsMessage(AppText.get(R.string.chats_renamed), undoRename = r))
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                titleChanged(id, c.title, c.renamed)
                _renames.trySend(ChatsMessage(AppText.get(R.string.chats_rename_failed)))
            }
        }
    }

    /** The snackbar's Undo of a rename: the title it had, or the first question when that was the automatic one. */
    fun undoRename(r: RenameResult) {
        titleChanged(r.id, r.previous.title, r.previous.renamed)
        viewModelScope.launch {
            try {
                val u = g.agent.renameSession(r.id, if (r.previous.renamed) r.previous.title else "")
                titleChanged(r.id, u.title, u.renamed)
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                titleChanged(r.id, r.title, r.renamed)
                _renames.trySend(ChatsMessage(AppText.get(R.string.chats_rename_failed)))
            }
        }
    }

    private fun keepSession(sessionId: String?) {
        if (sessionId == null) saved?.remove<String>(SESSION_KEY) else saved?.set(SESSION_KEY, sessionId)
    }

    private val voice by lazy {
        VoiceSetup(g.context, g, viewModelScope, handsFree = false, server = false).also { v ->
            v.clipContext = { _state.value.let { DictationContext.of(it.sessionId, it.about, it.contextConversation) } }
        }
    }

    /**
     * ACT agent.dictate @android.assistant: the message box's words and its microphone. The ask capture's model
     * (ui/ask/AskCapture.kt), never hands-free here: dictated words land in the box and wait for
     * Send. The source follows Settings → Dictation ([refreshVoice]).
     */
    val composer: AskCaptureModel by lazy {
        AskCaptureModel(null, VoiceSource.NONE, handsFree = false, engines = voice::engines, scope = viewModelScope,
            fallback = voice::fallback) {}
    }

    /** Which recognizer the message box uses: the phone's, the user's speech server, or none. */
    fun refreshVoice(order: DictationOrder, server: Boolean) {
        voice.server = server
        composer.setSource(voice.source(order))
    }

    /** Sends the message box's words; dictation still running stops without adding more. */
    fun sendComposer() {
        val c = composer.ui.value
        if (c.text.isBlank() || _state.value.busy || c.transcribing) return
        if (c.listening) composer.cancelListening()
        send(c.text)
        composer.clear()
    }

    override fun onCleared() {
        composer.close()
    }

    /** Ambrose's name from GET /assistant/profile, once per run; the last known name stays on any failure. */
    fun loadName() {
        if (nameLoaded) return
        nameLoaded = true
        viewModelScope.launch {
            try {
                val p = g.api.client.get("assistant/profile", kotlinx.serialization.json.JsonObject.serializer())
                io.github.murtazanasir.mailr.i18n.AssistantName.set(io.github.murtazanasir.mailr.i18n.AssistantName.fromProfile(p))
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                nameLoaded = false
            }
        }
    }
    private var nameLoaded = false

    /** The access GET /me reported: [blocked] is the message while the assistant is off, else null. */
    fun setAccess(blocked: String?) {
        _state.update { it.copy(blocked = blocked) }
    }

    fun setContext(conversationId: Long?, title: String?) {
        _state.update { it.copy(contextConversation = conversationId, contextTitle = title, about = null) }
    }

    /**
     * "Ask assistant" from an event or a task: a new chat about [about], with no conversation.
     * A new item always starts a new chat, as on the web.
     */
    fun askAbout(about: AskAbout) {
        if (_state.value.busy) stop()
        keepSession(null)
        _state.update { AssistantState(about = about, blocked = it.blocked, commands = it.commands, skills = it.skills) }
    }

    /**
     * ACT agent.ask_about @android.ask: the ask capture (ui/ask) hands over [about] and the instruction. A new
     * chat starts about the item; a [text] that is not blank is sent at once, so the reply (and any
     * approval it needs) shows here. A conversation goes as the chat's conversation context.
     */
    fun ask(about: AskAbout?, text: String?) {
        if (_state.value.busy) stop()
        keepSession(null)
        _state.update {
            when (about) {
                is AskAbout.Conversation -> AssistantState(contextConversation = about.id, contextTitle = about.title.ifBlank { null })
                null -> AssistantState()
                else -> AssistantState(about = about)
            }.copy(blocked = it.blocked, commands = it.commands, skills = it.skills)
        }
        if (!text.isNullOrBlank()) send(text)
    }

    fun newChat() {
        stop()
        keepSession(null)
        _state.update { AssistantState(contextConversation = it.contextConversation, contextTitle = it.contextTitle, about = it.about,
            blocked = it.blocked, name = it.name, commands = it.commands, skills = it.skills) }
    }

    /**
     * The picker's commands (GET /agent/commands) and the user's skills (GET /assistant/skills), read
     * again when the picker opens, at most once every [COMMANDS_TTL_MS], and at once after the chat made
     * or changed a skill ([skillsChanged]), so a skill made mid-session shows without a restart. The
     * lists on screen stay while they reload; a list that did not load is asked for again the next time.
     */
    fun loadCommands() {
        if (commandsLoading) return
        val at = commandsLoadedAt
        if (at != null && !skillsStale && AppClock.millis() - at < COMMANDS_TTL_MS) return
        commandsLoading = true
        skillsStale = false
        viewModelScope.launch {
            try {
                val cmds = catching { g.agent.commands() }.getOrNull()
                val skills = catching { g.agent.skills() }.getOrNull()
                commandsLoadedAt = if (cmds == null || skills == null) null else AppClock.millis()
                _state.update { it.copy(commands = cmds ?: it.commands, skills = skills ?: it.skills) }
            } finally {
                commandsLoading = false
            }
        }
    }
    private var commandsLoadedAt: Long? = null
    private var commandsLoading = false
    private var skillsStale = false

    /**
     * The chat made or changed a skill (a create_skill or update_skill step, or a skill's card carried
     * out): the picker's lists are read again now, when they were read before.
     */
    private fun skillsChanged() {
        skillsStale = true
        if (commandsLoadedAt != null) loadCommands()
    }

    /** `/local` turned off from its chip: the next messages may go to the chat's usual model. */
    fun setLocalOnly(on: Boolean) = _state.update { it.copy(localOnly = on) }

    fun send(text: String) {
        if (text.isBlank() || _state.value.busy || _state.value.blocked != null) return
        val typed = text.trim()
        ChatCommands.parse(typed, _state.value.commands)?.let { (cmd, arg) -> if (runCommand(cmd, arg, typed)) return }
        _state.update { it.copy(turns = it.turns + ChatTurn("user", text.trim()) + ChatTurn("assistant", streaming = true), busy = true, found = null) }
        run(text.trim(), null)
    }

    /**
     * ACT agent.commands @android.assistant: a slash command the app runs itself (kind `app`, as the
     * web's AssistantPanel runCommand). True when it ran here; the others go to the server as the
     * message's text, which answers `/model` and a command missing its text itself (an
     * `error` with code `command`, shown as an answer: [onEvent]) and turns the rest into a request.
     */
    internal fun runCommand(cmd: ChatCommand, text: String, typed: String): Boolean {
        if (cmd.kind != "app") return false
        val st = _state.value
        val needsChat = { notice(typed, AppText.get(R.string.asst_cmd_needs_chat, "/${cmd.name}")) }
        when (cmd.name) {
            "new" -> newChat()
            "chats" -> _commands.trySend(CommandEffect.OpenChats(text))
            "help" -> notice(typed, ChatCommands.helpText(st.commands, st.skills))
            "local" -> if (st.localOnly) notice(typed, AppText.get(R.string.asst_cmd_local_already)) else {
                _state.update { it.copy(localOnly = true) }
                notice(typed, AppText.get(R.string.asst_cmd_local_on, st.name))
            }
            "retry" -> if (!rerunLast()) notice(typed, AppText.get(R.string.asst_cmd_no_answer))
            "copy" -> {
                val t = lastAnswer() ?: return true.also { notice(typed, AppText.get(R.string.asst_cmd_no_answer)) }
                copy(ChatCommands.copyText(t.answer, t.citations))
            }
            "note" -> {
                val t = lastAnswer() ?: return true.also { notice(typed, AppText.get(R.string.asst_cmd_no_answer)) }
                saveToNote(t)
            }
            "rename" -> when {
                st.sessionId == null || st.title == null -> needsChat()
                text.isBlank() -> notice(typed, AppText.get(R.string.asst_cmd_needs_text, "/rename", cmd.example ?: "/rename"))
                else -> renameChat(text)
            }
            "retitle" -> if (st.sessionId == null) needsChat() else retitle(st.sessionId)
            "delete" -> if (st.sessionId == null) needsChat() else {
                // ACT agent.chat_delete @android.assistant: the chat goes now; the DELETE is sent when the snackbar closes.
                val c = ChatSummary(st.sessionId, st.title.orEmpty(), renamed = st.titleRenamed)
                newChat()
                _renames.trySend(ChatsMessage(AppText.get(R.string.chats_deleted), undo = c))
            }
            else -> return false
        }
        return true
    }

    private val _commands = kotlinx.coroutines.channels.Channel<CommandEffect>(kotlinx.coroutines.channels.Channel.BUFFERED)
    /** What a command asks of the screen around the chat (`/chats` opens Chats). */
    val commandEffects: kotlinx.coroutines.flow.Flow<CommandEffect> = _commands.receiveAsFlow()

    /** A reply the app gives itself (`/help`, a command that needs a chat): shown, not saved. */
    private fun notice(userText: String, text: String) = _state.update {
        it.copy(turns = it.turns + ChatTurn("user", userText) + ChatTurn("assistant",
            items = ChatTimeline.apply(emptyList(), AgentEvent.Final(text, emptyList())), finishedAt = AppClock.millis(), notice = true), found = null)
    }

    /** The last answer with text, for `/copy` and `/note`; the app's own command replies do not count. */
    private fun lastAnswer(): ChatTurn? =
        _state.value.turns.lastOrNull { it.role == "assistant" && !it.notice && it.error == null && it.answer.isNotBlank() }

    private fun copy(text: String) {
        val ok = catching {
            val cm = g.context.getSystemService(android.content.ClipboardManager::class.java)!!
            cm.setPrimaryClip(android.content.ClipData.newPlainText("mailr", text))
        }.isSuccess
        _notices.trySend(AppText.get(if (ok) R.string.ui_shell_copied else R.string.asst_cmd_copy_failed))
    }

    /**
     * ACT note.from_answer @android.assistant: `/note`, the last answer saved to a new text note, as the
     * web's saveAnswerToNote: its text and the sources it uses (ChatCommands.answerNote), then a link per
     * cited email, event, task or recording. A link that cannot be made leaves its line in the text and
     * is counted in the snackbar, whose Open shows the note in Notes.
     */
    private fun saveToNote(t: ChatTurn) {
        val n = ChatCommands.answerNote(t.answer, t.citations, AppText.get(R.string.asst_note_from, _state.value.name))
        viewModelScope.launch {
            try {
                val id = g.api.createTextNote(n.title, n.body).note?.id?.takeIf { it.isNotBlank() }
                    ?: throw IllegalStateException("no note id")
                var failed = 0
                for (link in n.links) {
                    try {
                        g.api.addNoteLink(id, link)
                    } catch (e: kotlinx.coroutines.CancellationException) {
                        throw e
                    } catch (e: Exception) {
                        failed++ // a record the note may not link to (gone, or someone else's): the text keeps it
                    }
                }
                val text = if (failed > 0) AppText.plural(R.plurals.asst_note_saved_links, failed, failed) else AppText.get(R.string.asst_note_saved)
                _renames.trySend(ChatsMessage(text, openNote = id))
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                _notices.trySend(AppText.get(R.string.asst_note_failed))
            }
        }
    }

    /**
     * ACT agent.review_filter @android.assistant: a review card's "Always …" on a sender, a filter for their
     * future mail doing what the review does now (filterFor, POST /rules). Made, the button says "Filter
     * made" and the snackbar offers Undo ([undoFilter]); a failure says so.
     */
    fun makeReviewFilter(reviewId: Long, group: io.github.murtazanasir.mailr.data.api.ReviewGroup, sender: io.github.murtazanasir.mailr.data.api.ReviewSender) {
        val body = filterFor(group, sender) ?: return
        val key = alwaysKey(reviewId, group, sender)
        val st = _state.value
        if (key in st.filtersBusy || key in st.filtersMade) return
        _state.update { it.copy(filtersBusy = it.filtersBusy + key) }
        viewModelScope.launch {
            try {
                val id = g.agent.createRule(body)
                _state.update { it.copy(filtersMade = it.filtersMade + (key to id)) }
                _renames.trySend(ChatsMessage(AppText.get(R.string.asst_review_filter_made_for, sender.from),
                    undoFilter = id?.let { MadeFilter(key, it) }))
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                _notices.trySend(AppText.get(R.string.asst_review_filter_failed))
            } finally {
                _state.update { it.copy(filtersBusy = it.filtersBusy - key) }
            }
        }
    }

    /** The snackbar's Undo of an "Always …": the filter is deleted (DELETE /rules/{id}) and the button comes back. */
    fun undoFilter(f: MadeFilter) {
        viewModelScope.launch {
            try {
                g.agent.deleteRule(f.id)
                _state.update { it.copy(filtersMade = it.filtersMade - f.key) }
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                _notices.trySend(AppText.get(R.string.asst_review_filter_undo_failed))
            }
        }
    }

    /** `/retry`: the last answer written again (`rerun_from` its question), as the web's rerunLast. False when there is none. */
    private fun rerunLast(): Boolean {
        val s = _state.value
        if (s.sessionId == null || s.turns.none { it.role == "assistant" }) return false
        val at = s.turns.indexOfLast { it.role == "user" && it.messageId != null }
        if (at < 0) return false
        val id = s.turns[at].messageId!!
        _state.update { it.copy(turns = it.turns.take(at + 1) + ChatTurn("assistant", streaming = true), busy = true, found = null) }
        run("", null, rerunFrom = id)
        return true
    }

    /** `/retitle`: a new short title from the first question; the top bar follows. */
    private fun retitle(id: String) {
        viewModelScope.launch {
            try {
                val r = g.agent.retitle(id)
                titleChanged(id, r.title, r.renamed)
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                _notices.trySend(AppText.get(R.string.asst_cmd_no_title))
            }
        }
    }

    /** The snackbar of a `/delete` closed without Undo: the chat is deleted at the server (on the app's scope). */
    fun commitDelete(c: ChatSummary) {
        g.scope.launch {
            try {
                g.agent.deleteSession(c.id)
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                _notices.trySend(AppText.get(R.string.chats_delete_failed))
            }
        }
    }

    /**
     * ACT approval.approve @android.assistant, ACT approval.deny @android.assistant: decide a card in the chat. The card changes in place
     * to its outcome, then the turn continues after it (the server joins the continuation to the
     * same turn).
     */
    fun decide(a: Approval, approve: Boolean, scope: String) = decide(a, approve, scope, emptyMap())

    /**
     * ACT agent.review_mail @android.assistant, ACT agent.review_move @android.assistant: Apply on a review
     * card, once, with the user's own moves on it (`changes`, conversation id -> trash, archive, read, keep
     * or label:<id>); the server accepts only conversations the card showed (agent/review.py apply_changes).
     */
    fun applyReview(a: Approval, changes: Map<Long, String>) = decide(a, true, "once", changes)

    private fun decide(a: Approval, approve: Boolean, scope: String, changes: Map<Long, String>) {
        viewModelScope.launch {
            try {
                val r = g.api.decide(a, approve, scope, changes = changes)
                updateApproval(a.copy(status = if (approve) "approved" else "denied", outcome = null))
                // A grant decided other waiting cards of this chat too: they change in place.
                r?.alsoDecided?.ids.orEmpty().forEach { id -> catching { g.api.approval(id) }.getOrNull()?.let { updateApproval(it) } }
                // Wait (briefly) for an approved action to run, so the card shows what happened.
                var card = catching { g.api.approval(a.id) }.getOrNull()
                var tries = 0
                while (card != null && card.status in setOf("approved", "executing") && tries++ < 20) {
                    kotlinx.coroutines.delay(if (tries < 8) 400 else 1000)
                    card = catching { g.api.approval(a.id) }.getOrNull() ?: card
                }
                card?.let { updateApproval(it) }
                if (!_state.value.busy) {
                    // The continuation joins the last reply: no new turn, no user bubble.
                    updateLast { it.copy(streaming = true, error = null) }
                    _state.update { it.copy(busy = true) }
                    run("", a.id)
                }
            } catch (e: Exception) {
                updateLast { it.copy(error = e.userMessage()) }
            }
        }
    }

    /**
     * A bulk review's progress (ACT agent.review_mail @android.assistant), as the web's ReviewProgress:
     * read now and every [REVIEW_POLL_MS] while it runs or is stopping, every [REVIEW_RETRY_MS] while it
     * cannot be read. When its review card is ready the card joins the turn that started the review,
     * once: a card the chat already holds changes in place instead. The caller's scope ends the polling
     * (the progress line left the screen).
     */
    suspend fun followReview(reviewId: Long) {
        while (true) {
            val next = try {
                g.agent.review(reviewId)
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                null
            }
            val card = next?.approvalId?.let { aid -> catching { g.api.approval(aid) }.getOrNull() }
            if (next != null) _state.update { it.copy(reviews = it.reviews + (reviewId to next)) }
            if (card != null) addReviewCard(reviewId, card)
            when {
                next == null || (next.approvalId != null && card == null) -> kotlinx.coroutines.delay(REVIEW_RETRY_MS)
                next.live -> kotlinx.coroutines.delay(REVIEW_POLL_MS)
                else -> return
            }
        }
    }

    /** Stop ([stop]) or Continue a review; its line follows the answer, and polling starts again while it runs. */
    fun reviewAction(reviewId: Long, stop: Boolean) {
        if (reviewId in _state.value.reviewActing) return
        _state.update { it.copy(reviewActing = it.reviewActing + reviewId) }
        viewModelScope.launch {
            try {
                val r = g.agent.reviewAction(reviewId, stop)
                _state.update { it.copy(reviews = it.reviews + (reviewId to r)) }
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                _notices.trySend(AppText.get(R.string.asst_review_could_not_change))
            } finally {
                _state.update { it.copy(reviewActing = it.reviewActing - reviewId) }
            }
        }
    }

    /** A finished review's card, in the last turn that started the review, unless the chat holds it already. */
    internal fun addReviewCard(reviewId: Long, card: Approval) {
        if (_state.value.turns.any { t -> t.approvals.any { it.id == card.id } }) {
            updateApproval(card)
            return
        }
        _state.update { s ->
            val at = s.turns.indexOfLast { it.role == "assistant" && reviewId in it.reviews }
            if (at < 0) s else s.copy(turns = s.turns.toMutableList().also { it[at] = it[at].copy(approvals = it[at].approvals + card) })
        }
    }

    /** A card changes in place, keeping what the server did not send again. A skill's card carried out: the picker reloads. */
    private fun updateApproval(card: Approval) {
        if (card.preview.kind == "skill" && card.status == "executed") skillsChanged()
        updateApprovalInPlace(card)
    }

    private fun updateApprovalInPlace(card: Approval) = _state.update { s ->
        s.copy(turns = s.turns.map { t ->
            if (t.approvals.none { it.id == card.id }) t else t.copy(
                approvals = t.approvals.map { old ->
                    if (old.id != card.id) old else card.copy(
                        preview = if (card.preview.title.isBlank()) old.preview else card.preview,
                        display = card.display ?: old.display, actor = card.actor.ifBlank { old.actor })
                },
                items = ChatTimeline.decided(t.items, card.id, card.status),
            )
        })
    }

    fun stop() {
        job?.cancel()
        _state.value.sessionId?.let { sid -> viewModelScope.launch { g.agent.stop(sid) } }
        updateLast { it.copy(streaming = false, finishedAt = AppClock.millis()) }
        _state.update { it.copy(busy = false) }
    }

    /**
     * ACT agent.stream_resume @android.assistant: one turn. When the stream drops before the turn
     * ends (a network change, the app in the background, a server restart), the turn is not lost: the
     * app re-attaches and carries on from the last event it had ([recover]), or reloads the finished
     * turn from the chat. The web does the same (useAssistant.ts recover).
     */
    private fun run(text: String, resume: Long?, rerunFrom: Long? = null) {
        // The stream may break before the server names a new chat: the chat this turn made is found by its time.
        val since = if (_state.value.sessionId == null) AppClock.millis() else null
        job = viewModelScope.launch {
            try {
                var seq = 0
                var finished = false
                var conflict = false
                val local = _state.value.localOnly && resume == null && rerunFrom == null
                g.agent.turn(text, _state.value.sessionId, _state.value.contextConversation, resume, _state.value.about, rerunFrom, local).collect { (n, ev) ->
                    if (n != null) seq = maxOf(seq, n)
                    if (ev is AgentEvent.Error && ev.status == 409 && _state.value.sessionId != null) {
                        conflict = true
                        return@collect
                    }
                    onEvent(ev)
                    if (ev is AgentEvent.Done || ev is AgentEvent.Error) finished = true
                }
                if (conflict) {
                    // The previous turn still runs at the server (this screen lost track of it): the new
                    // message goes, that turn shows again and is followed, so Stop works.
                    _state.update { s -> s.copy(turns = s.turns.dropLast(if (resume == null) 2 else 0)) }
                    _state.value.sessionId?.let { sid -> replayNow(sid) }
                    updateLast { ChatTurn("assistant", streaming = true) }
                    recover(0, null)
                } else if (!finished) recover(seq, since)
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                updateLast { it.copy(error = e.userMessage()) }
            } finally {
                updateLast { it.copy(streaming = false, reconnecting = false, finishedAt = it.finishedAt ?: AppClock.millis()) }
                _state.update { it.copy(busy = false) }
            }
        }
    }

    /**
     * The stream dropped before the turn ended. Re-attach (GET /agent/chat/{id}/stream?after=<seq>)
     * with waits that grow (RETRY_STEPS: about a minute in all, so a server restart for a deploy is
     * outlasted), showing "Reconnecting…" the whole time; events the screen already has are skipped,
     * so nothing shows twice. When the run is gone (404) or the connection stays down, the chat is
     * reloaded from the server, where the turn is finished or marked as interrupted. Still offline,
     * the turn keeps what arrived, says the connection was lost and offers Retry ([retry]).
     */
    private suspend fun recover(lastSeq: Int, since: Long?) {
        var seq = lastSeq
        updateLast { it.copy(reconnecting = true) }
        var attempt = 0
        while (true) {
            // The stream may have broken before the server named the chat: it is found by its time (each try, as the server may be down).
            if (_state.value.sessionId == null && since != null) findSession(since)
            val sid = _state.value.sessionId
            if (sid != null) {
                val from = seq
                var done = false
                try {
                    g.agent.follow(sid, from).collect { (n, ev) ->
                        if (n != null) {
                            // Already shown before the stream dropped.
                            if (n <= from) return@collect
                            seq = maxOf(seq, n)
                        }
                        updateLast { if (it.reconnecting) it.copy(reconnecting = false) else it }
                        onEvent(ev)
                        if (ev is AgentEvent.Done || ev is AgentEvent.Error) done = true
                    }
                    if (done) return
                } catch (e: kotlinx.coroutines.CancellationException) {
                    throw e
                } catch (e: ApiException) {
                    // 404: no run in this process, or not registered yet (the chat row is written a moment
                    // before the run is): try again once or twice before reloading the chat.
                    if (e.code == 404 && attempt >= 2) break
                } catch (e: Exception) {
                    // The connection is still down: wait and try again.
                }
            } else if (since == null) break
            if (attempt >= RETRY_STEPS.size) break
            updateLast { it.copy(reconnecting = true) }
            kotlinx.coroutines.delay(RETRY_STEPS[attempt] * RETRY_MS)
            attempt++
        }
        val sid = _state.value.sessionId
        if (sid == null || !replayNow(sid)) {
            updateLast { it.copy(reconnecting = false, lost = true, error = it.error ?: AppText.get(R.string.asst_connection_lost)) }
        }
    }

    /** The chat this turn made, found by its time among the newest chats; nothing changes offline. */
    private suspend fun findSession(since: Long) {
        try {
            g.agent.sessions(3).firstOrNull { c -> (Format.parse(c.updatedAt)?.toInstant()?.toEpochMilli() ?: 0) >= since - 5000 }?.let { c ->
                _state.update { it.copy(sessionId = c.id, title = it.title ?: c.title.ifBlank { null }, titleRenamed = c.renamed) }
                keepSession(c.id)
            }
        } catch (e: kotlinx.coroutines.CancellationException) {
            throw e
        } catch (e: Exception) {
            // Offline: tried again on the next attempt.
        }
    }

    /**
     * ACT agent.rerun @android.assistant: Retry on the last turn when it ended "Connection lost" or
     * "Interrupted". The chat is read again first: a reply that finished meanwhile just shows, one
     * still running is followed. Otherwise the turn runs again from its stored user message
     * (`rerun_from`, as the web's Retry), and when the server never stored the message, the same
     * words go again in the same chat. The user bubble is never added twice.
     */
    fun retry() {
        val s = _state.value
        if (s.busy || s.blocked != null || !canRetry(s.turns)) return
        val userAt = s.turns.indexOfLast { it.role == "user" }
        val user = s.turns[userAt]
        val before = s.turns
        _state.update { it.copy(turns = it.turns.take(userAt + 1) + ChatTurn("assistant", streaming = true), busy = true, found = null) }
        job = viewModelScope.launch {
            val sid = _state.value.sessionId
            var messageId = user.messageId
            if (sid != null) {
                val session = try {
                    g.agent.session(sid)
                } catch (e: kotlinx.coroutines.CancellationException) {
                    throw e
                } catch (e: Exception) {
                    null
                }
                if (session == null) {
                    // Still offline: the turn stays as it was, Retry with it.
                    _state.update { it.copy(turns = before, busy = false) }
                    return@launch
                }
                val turns = SessionReplay.turns(session)
                val lastUser = turns.indexOfLast { it.role == "user" }
                val stored = turns.getOrNull(lastUser)?.takeIf { it.text.trim() == user.text.trim() }
                val reply = turns.getOrNull(lastUser + 1)
                if (stored != null && SessionReplay.running(session)) {
                    _state.update { it.copy(turns = turns.take(lastUser + 1) + ChatTurn("assistant", streaming = true)) }
                    followRunning()
                    return@launch
                }
                if (stored != null && reply != null && !reply.interrupted && reply.error == null && turns.size == lastUser + 2) {
                    // The answer finished while the connection was down.
                    _state.update { it.copy(turns = turns, busy = false) }
                    return@launch
                }
                if (stored != null) {
                    messageId = stored.messageId
                    _state.update { it.copy(turns = turns.take(lastUser + 1) + ChatTurn("assistant", streaming = true)) }
                } else messageId = null
            }
            if (sid != null && messageId != null) run("", null, rerunFrom = messageId) else run(user.text, null)
        }
    }

    /** The chat as the server has it now; false when it could not load (the screen keeps what it shows). */
    private suspend fun replayNow(sessionId: String): Boolean = try {
        val turns = SessionReplay.turns(g.agent.session(sessionId))
        _state.update { it.copy(turns = turns, reviews = emptyMap()) }
        true
    } catch (e: kotlinx.coroutines.CancellationException) {
        throw e
    } catch (e: Exception) {
        false
    }

    private fun onEvent(ev: AgentEvent) {
        when (ev) {
            is AgentEvent.Session -> {
                // The chat's title comes with the session (the first question, or the name it was given).
                _state.update { it.copy(sessionId = ev.id, title = ev.title?.takeIf { t -> t.isNotBlank() } ?: it.title) }
                keepSession(ev.id)
                // The question's stored id (for `/retry`), on the last user message.
                if (ev.userMessageId != null && !ev.continuation) _state.update { s ->
                    val u = s.turns.indexOfLast { it.role == "user" }
                    if (u < 0 || s.turns[u].messageId != null) s
                    else s.copy(turns = s.turns.toMutableList().also { it[u] = it[u].copy(messageId = ev.userMessageId) })
                }
                updateLast { it.copy(items = ChatTimeline.apply(it.items, ev), memoryIds = ev.memoryIds.ifEmpty { it.memoryIds }) }
            }
            // The short title made for a new chat (the server sends none for a chat the user named).
            is AgentEvent.Title -> _state.update { it.copy(title = ev.title) }
            is AgentEvent.Token, is AgentEvent.StepStarted -> updateLast { it.copy(items = ChatTimeline.apply(it.items, ev)) }
            is AgentEvent.StepDone -> {
                updateLast { it.copy(items = ChatTimeline.apply(it.items, ev)) }
                if (ev.ok && ev.name in SKILL_TOOLS) skillsChanged()
            }
            // The citation arrives before the streamed text that links to it, so the number works at once.
            is AgentEvent.Cite -> updateLast { it.copy(citations = merge(it.citations, listOf(ev.citation))) }
            is AgentEvent.Segment -> updateLast {
                it.copy(items = ChatTimeline.apply(it.items, ev), citations = merge(it.citations, ev.citations))
            }
            is AgentEvent.Draft -> updateLast { it.copy(drafts = it.drafts + (ev.draftId to ev.payload)) }
            is AgentEvent.ApprovalRequired -> updateLast {
                val card = ev.approval ?: Approval(ev.approvalId, actor = _state.value.name, preview = ApprovalPreview(ev.title, ev.lines))
                it.copy(approvals = it.approvals.filter { a -> a.id != card.id } + card, items = ChatTimeline.apply(it.items, ev))
            }
            is AgentEvent.ApprovalUpdate -> updateApproval(ev.approval)
            is AgentEvent.Review -> updateLast { if (ev.reviewId in it.reviews) it else it.copy(reviews = it.reviews + ev.reviewId) }
            is AgentEvent.Final -> updateLast {
                it.copy(items = ChatTimeline.apply(it.items, ev), citations = merge(it.citations, ev.citations))
            }
            is AgentEvent.Done -> updateLast { it.copy(streaming = false, finishedAt = AppClock.millis(), memoryIds = ev.memoryIds.ifEmpty { it.memoryIds }) }
            // The server's own reply to a slash command (agent/commands.py): an answer, not a failure.
            is AgentEvent.Error -> if (ev.code == "command") updateLast {
                it.copy(items = ChatTimeline.apply(it.items, AgentEvent.Final(ev.message, emptyList())), streaming = false,
                    finishedAt = AppClock.millis())
            } else {
                updateLast { it.copy(error = ev.message, streaming = false) }
                // The assistant is off for this user: the message replaces the message box.
                if (AssistantGate.isNoAccess(ev.code, ev.status)) _state.update { it.copy(blocked = ev.message) }
            }
        }
    }

    private fun merge(a: List<Citation>, b: List<Citation>): List<Citation> = a + b.filter { c -> a.none { it.n == c.n } }

    private fun updateLast(block: (ChatTurn) -> ChatTurn) = _state.update { s ->
        val i = s.turns.indexOfLast { it.role == "assistant" }
        if (i < 0) s else s.copy(turns = s.turns.toMutableList().also { it[i] = block(it[i]) })
    }

    companion object {
        private const val SESSION_KEY = "assistant.session"

        /** The picker's lists are read again when it opens, at most this often (unless a skill changed). */
        internal const val COMMANDS_TTL_MS = 30_000L

        /** The chat's tools that make or change a skill (agent/knowledge_tools.py). */
        private val SKILL_TOOLS = setOf("create_skill", "update_skill")

        /** A stored chat's title and whether the user gave it (GET /agent/sessions/{id}). */
        fun titleOf(session: kotlinx.serialization.json.JsonObject): String? =
            (session["title"] as? kotlinx.serialization.json.JsonPrimitive)?.contentOrNull?.takeIf { it.isNotBlank() }

        fun renamedOf(session: kotlinx.serialization.json.JsonObject): Boolean =
            (session["renamed"] as? kotlinx.serialization.json.JsonPrimitive)?.contentOrNull == "true"

        /**
         * The waits before each attempt to re-attach, in units of [RETRY_MS]: 1, 2, 3, 5, 8, 13, 13 and 15 s,
         * a minute in all, so a server restart during a deploy does not end the chat.
         */
        internal val RETRY_STEPS = listOf(1L, 2L, 3L, 5L, 8L, 13L, 13L, 15L)
        /** How often a running review's progress is read, and how long to wait when it cannot be (tests shorten them). */
        @androidx.annotation.VisibleForTesting
        internal var REVIEW_POLL_MS = 2000L
        @androidx.annotation.VisibleForTesting
        internal var REVIEW_RETRY_MS = 5000L

        /** One unit of [RETRY_STEPS] (tests shorten it). */
        @androidx.annotation.VisibleForTesting
        internal var RETRY_MS = 1000L

        /** Retry is offered on the last turn only, when it ended "Connection lost" or "Interrupted" after a user message. */
        fun canRetry(turns: List<ChatTurn>): Boolean {
            val last = turns.lastOrNull() ?: return false
            return last.role == "assistant" && !last.streaming && (last.lost || last.interrupted) &&
                turns.getOrNull(turns.size - 2)?.role == "user"
        }
    }
}

/** What a slash command asks of the screen around the chat (MainShell's AssistantRoute). */
sealed interface CommandEffect {
    /** `/chats`: Chats opens, searched for [query] when it is not blank. */
    data class OpenChats(val query: String) : CommandEffect
}
