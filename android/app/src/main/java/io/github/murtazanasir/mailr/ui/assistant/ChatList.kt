package io.github.murtazanasir.mailr.ui.assistant

import io.github.murtazanasir.mailr.util.catching

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.activity.compose.BackHandler
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.material.icons.outlined.Search
import io.github.murtazanasir.mailr.look.AlertDialog
import io.github.murtazanasir.mailr.look.OutlinedTextField
import io.github.murtazanasir.mailr.look.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.window.DialogProperties
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.outlined.AddComment
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.CloudOff
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.MoreVert
import io.github.murtazanasir.mailr.look.CircularProgressIndicator
import io.github.murtazanasir.mailr.look.DropdownMenu
import io.github.murtazanasir.mailr.look.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarDuration
import io.github.murtazanasir.mailr.look.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import io.github.murtazanasir.mailr.look.TopAppBar
import androidx.compose.material3.VerticalDivider
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.agent.ChatSummary
import io.github.murtazanasir.mailr.data.agent.RenameResult
import io.github.murtazanasir.mailr.data.agent.TextPart
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.ui.util.Format
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.io.IOException
import java.time.ZonedDateTime

/*
 * Chats: the user's earlier Ambrose chats (GET /agent/sessions), newest first, each with its title (the
 * first message), when it last changed and the start of its latest answer. A chat opens in the Ambrose
 * screen and goes on from there. Delete has Undo: the row goes at once and the chat is deleted on the
 * server when the snackbar closes (DELETE /agent/sessions/{id} is permanent). Reached from the Ambrose
 * top bar; a full screen on phones, a pane beside the chat on wider screens.
 *
 * Search (the top bar's search icon): GET /agent/sessions?q= over the titles, questions and answers;
 * a result shows the text around its hit under the title, the hits marked. Rename (the row's ⋮): a
 * small dialog, PATCH /agent/sessions/{id}; the snackbar has Undo, which renames it back (an empty
 * title gives back the first question). The web does the same (ChatList.tsx, chatList.ts).
 */

/** The server's limit for a title the user gives. */
const val CHAT_TITLE_MAX = 120

/** A title as typed: one line, spaces collapsed, trimmed (the server cleans it the same way). */
fun cleanChatTitle(s: String): String = s.split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ")

/** What Rename sends for [typed], or null when nothing changes or it is too long (the web's renameBody). */
fun renameTitleBody(typed: String, c: ChatSummary): String? {
    val t = cleanChatTitle(typed)
    if (t.length > CHAT_TITLE_MAX) return null
    if (t.isEmpty()) return if (c.renamed) "" else null
    return if (t == c.title) null else t
}

enum class ChatsProblem { OFFLINE, FAILED }

data class ChatsUi(
    val loading: Boolean = true,
    val chats: List<ChatSummary> = emptyList(),
    val problem: ChatsProblem? = null,
    /** The words in the search field ("" with the field closed or empty). */
    val query: String = "",
    /** The search results for [query]; null while there is no search or before its first answer. */
    val results: List<ChatSummary>? = null,
    val searchFailed: Boolean = false,
) {
    val loaded: Boolean get() = !loading || chats.isNotEmpty()
    val searching: Boolean get() = query.isNotBlank()
}

/** A snackbar message; [undo] is set for a delete, [undoRename] for a rename. */
data class ChatsMessage(val text: String, val undo: ChatSummary? = null, val undoRename: RenameResult? = null,
                        /** `/note`: the note just made, which the snackbar's Open shows. */
                        val openNote: String? = null,
                        /** A review card's "Always …": the filter just made, which the snackbar's Undo deletes. */
                        val undoFilter: MadeFilter? = null)

/** A filter a review card's "Always …" made: its [alwaysKey] and its id. */
data class MadeFilter(val key: String, val id: Long)

/**
 * [list] and [remove] are the server's (AgentClient.sessions, deleteSession); [commitScope] outlives the
 * screen, so a delete whose snackbar was still open when the screen went still reaches the server.
 * [onDeleted] runs after the server deleted a chat (the open chat then makes way for a new one).
 */
class ChatListViewModel(
    private val list: suspend () -> List<ChatSummary>,
    private val remove: suspend (String) -> Unit,
    private val commitScope: CoroutineScope,
    private val onDeleted: (String) -> Unit = {},
    private val find: suspend (String) -> List<ChatSummary> = { emptyList() },
    private val rename: suspend (String, String) -> RenameResult = { _, _ -> throw IOException("no rename") },
    /** A rename here went through (or was undone): the id, the title and whether the user gave it. */
    private val onRenamed: (String, String, Boolean) -> Unit = { _, _, _ -> },
) : ViewModel() {
    private val _ui = MutableStateFlow(ChatsUi())
    val ui: StateFlow<ChatsUi> = _ui.asStateFlow()
    private val _messages = Channel<ChatsMessage>(Channel.BUFFERED)
    val messages = _messages.receiveAsFlow()
    /** The chat whose delete waits for its snackbar to close, and where its row was (list, results). */
    private class Pending(val chat: ChatSummary, val at: Int?, val resultAt: Int?)
    private var pending: Pending? = null

    /** ACT agent.chat_list @android.assistant: the list, read again each time Chats opens. */
    fun load() {
        _ui.update { it.copy(loading = true, problem = null) }
        viewModelScope.launch {
            try {
                val chats = list()
                val hidden = pending?.chat?.id
                _ui.update { it.copy(loading = false, problem = null, chats = chats.filter { c -> c.id != hidden }) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _ui.update { it.copy(loading = false, problem = if (e is IOException) ChatsProblem.OFFLINE else ChatsProblem.FAILED) }
            }
        }
    }

    /** ACT agent.chat_delete @android.assistant: the row goes now; the server deletes it when the snackbar closes. */
    fun delete(c: ChatSummary) {
        commit()
        val u = _ui.value
        val at = u.chats.indexOfFirst { it.id == c.id }.takeIf { it >= 0 }
        val inResults = u.results?.indexOfFirst { it.id == c.id }?.takeIf { it >= 0 }
        if (at == null && inResults == null) return
        pending = Pending(c, at, inResults)
        _ui.update { it.copy(chats = it.chats.filter { x -> x.id != c.id }, results = it.results?.filter { x -> x.id != c.id }) }
        _messages.trySend(ChatsMessage(AppText.get(R.string.chats_deleted), undo = c))
    }

    /** Undo: the row comes back where it was, and nothing is sent. */
    fun undo(c: ChatSummary) {
        val p = pending ?: return
        if (p.chat.id != c.id) return
        pending = null
        restore(p)
    }

    /** The snackbar of [only] closed (or the screen went, null): the waiting delete goes to the server. */
    fun commit(only: ChatSummary? = null) {
        val p = pending ?: return
        if (only != null && only.id != p.chat.id) return
        pending = null
        commitScope.launch {
            try {
                remove(p.chat.id)
                onDeleted(p.chat.id)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                restore(p)
                _messages.trySend(ChatsMessage(AppText.get(R.string.chats_delete_failed)))
            }
        }
    }

    private fun restore(p: Pending) = _ui.update {
        fun back(l: List<ChatSummary>, at: Int?) =
            if (at == null || l.any { x -> x.id == p.chat.id }) l else l.toMutableList().also { m -> m.add(at.coerceIn(0, m.size), p.chat) }
        it.copy(chats = back(it.chats, p.at), results = it.results?.let { r -> back(r, p.resultAt) })
    }

    private var searchJob: Job? = null

    /**
     * ACT agent.chat_search @android.assistant: the words in the search field. The server is asked a
     * moment after the typing stops; the rows shown stay until the results come. Blank: the whole list.
     */
    fun search(q: String) {
        _ui.update { it.copy(query = q, searchFailed = false, results = if (q.isBlank()) null else it.results) }
        searchJob?.cancel()
        val term = q.trim().replace(Regex("\\s+"), " ")
        if (term.isEmpty()) return
        searchJob = viewModelScope.launch {
            delay(SEARCH_DELAY_MS)
            try {
                val found = find(term)
                val hidden = pending?.chat?.id
                _ui.update { if (it.query.trim().replace(Regex("\\s+"), " ") != term) it else it.copy(results = found.filter { c -> c.id != hidden }) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _ui.update { it.copy(searchFailed = true) }
            }
        }
    }

    /** ACT agent.chat_rename @android.assistant: rename [c] to [typed]; the snackbar offers Undo. */
    fun rename(c: ChatSummary, typed: String) {
        val body = renameTitleBody(typed, c) ?: return
        if (body.isNotEmpty()) apply(c.id, body, true)
        viewModelScope.launch {
            try {
                val r = rename(c.id, body)
                apply(c.id, r.title, r.renamed)
                _messages.trySend(ChatsMessage(AppText.get(R.string.chats_renamed), undoRename = r))
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                apply(c.id, c.title, c.renamed)
                _messages.trySend(ChatsMessage(AppText.get(R.string.chats_rename_failed)))
            }
        }
    }

    /** Undo of a rename: the title it had, or the first question when that was the automatic one. */
    fun undoRename(r: RenameResult) {
        apply(r.id, r.previous.title, r.previous.renamed)
        viewModelScope.launch {
            try {
                val u = rename(r.id, if (r.previous.renamed) r.previous.title else "")
                apply(r.id, u.title, u.renamed)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                apply(r.id, r.title, r.renamed)
                _messages.trySend(ChatsMessage(AppText.get(R.string.chats_rename_failed)))
            }
        }
    }

    /** One chat's title, in the list and in the results (stale title hits dropped). */
    private fun apply(id: String, title: String, renamed: Boolean) {
        showTitle(id, title, renamed)
        onRenamed(id, title, renamed)
    }

    /** A chat's title as the open chat has it now (a rename from the Ambrose top bar, a new chat's first title). */
    fun showTitle(id: String, title: String, renamed: Boolean) {
        fun fix(l: List<ChatSummary>) = l.map { if (it.id == id && (it.title != title || it.renamed != renamed)) it.copy(title = title, renamed = renamed,
            titleParts = if (it.title == title) it.titleParts else null) else it }
        _ui.update { it.copy(chats = fix(it.chats), results = it.results?.let(::fix)) }
    }

    override fun onCleared() {
        commit()
    }

    companion object {
        /** How long the search waits after the last key. */
        var SEARCH_DELAY_MS = 250L
    }
}

data class ChatsCallbacks(
    val open: (ChatSummary) -> Unit,
    val newChat: () -> Unit,
    val delete: (ChatSummary) -> Unit,
    val retry: () -> Unit,
    /** Back (phone) or close the pane (wide). */
    val close: () -> Unit,
    /** The words in the search field changed ("" closes the search). */
    val search: (String) -> Unit = {},
    /** Rename a chat to the text typed in the dialog. */
    val rename: (ChatSummary, String) -> Unit = { _, _ -> },
)

/** Chats beside the Ambrose screen on a wide window, or in its place on a phone. */
@Composable
fun ChatsLayout(wide: Boolean, listOpen: Boolean, list: @Composable (Modifier) -> Unit, chat: @Composable (Modifier) -> Unit) {
    when {
        !listOpen -> chat(Modifier.fillMaxSize())
        !wide -> list(Modifier.fillMaxSize())
        else -> Row(Modifier.fillMaxSize()) {
            list(Modifier.width(360.dp).testTag("chats-pane"))
            VerticalDivider()
            chat(Modifier.weight(1f))
        }
    }
}

/** The snackbar's Undo for a delete; a delete commits when its snackbar closes or the screen goes. */
@Composable
fun ChatsMessages(vm: ChatListViewModel, snackbar: SnackbarHostState) {
    val scope = rememberCoroutineScope()
    LaunchedEffect(vm) {
        vm.messages.collect { m ->
            // A newer message replaces the one showing (its delete has gone to the server already).
            snackbar.currentSnackbarData?.dismiss()
            scope.launch {
                val undo = m.undo
                val renamed = m.undoRename
                val hasUndo = undo != null || renamed != null
                val r = snackbar.showSnackbar(m.text, if (hasUndo) AppText.get(R.string.chats_undo) else null,
                    duration = if (hasUndo) SnackbarDuration.Long else SnackbarDuration.Short)
                if (undo != null) { if (r == SnackbarResult.ActionPerformed) vm.undo(undo) else vm.commit(undo) }
                if (renamed != null && r == SnackbarResult.ActionPerformed) vm.undoRename(renamed)
            }
        }
    }
    DisposableEffect(vm) { onDispose { vm.commit() } }
}

/**
 * The list of chats. [current]: the chat open in the Ambrose screen (marked). [wide]: the list is a pane,
 * so its top bar closes it rather than going back.
 */
@Composable
fun ChatListScreen(
    ui: ChatsUi,
    assistantName: String,
    current: String?,
    cb: ChatsCallbacks,
    snackbar: SnackbarHostState = remember { SnackbarHostState() },
    modifier: Modifier = Modifier,
    wide: Boolean = false,
    now: ZonedDateTime = AppClock.now(),
) {
    // The search field replaces the title while it is open; closing it clears the search.
    var searchOpen by rememberSaveable { mutableStateOf(ui.searching) }
    var renaming by remember { mutableStateOf<ChatSummary?>(null) }
    val focus = remember { FocusRequester() }
    val closeSearch = { searchOpen = false; cb.search("") }
    BackHandler(enabled = searchOpen) { closeSearch() }
    Scaffold(
        modifier = modifier.testTag("chats"),
        contentWindowInsets = WindowInsets(0),
        topBar = {
            TopAppBar(
                title = {
                    if (searchOpen) {
                        LaunchedEffect(Unit) { catching { focus.requestFocus() } }
                        val label = stringResource(R.string.chats_search)
                        TextField(ui.query, cb.search, singleLine = true,
                            placeholder = { Text(label) },
                            colors = TextFieldDefaults.colors(focusedContainerColor = Color.Transparent,
                                unfocusedContainerColor = Color.Transparent, focusedIndicatorColor = Color.Transparent,
                                unfocusedIndicatorColor = Color.Transparent),
                            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                            trailingIcon = {
                                if (ui.query.isNotEmpty()) IconButton(onClick = { cb.search("") }, modifier = Modifier.testTag("chats-search-clear")) {
                                    Icon(Icons.Outlined.Close, stringResource(R.string.chats_clear_search))
                                }
                            },
                            modifier = Modifier.fillMaxWidth().focusRequester(focus).semantics { contentDescription = label }
                                .testTag("chats-search-field"))
                    } else {
                        Text(stringResource(R.string.chats_title))
                    }
                },
                navigationIcon = {
                    when {
                        searchOpen -> IconButton(onClick = closeSearch, modifier = Modifier.testTag("chats-search-close")) {
                            Icon(Icons.AutoMirrored.Filled.ArrowBack, stringResource(R.string.chats_search_close))
                        }
                        !wide -> IconButton(onClick = cb.close) { Icon(Icons.AutoMirrored.Filled.ArrowBack, stringResource(R.string.chats_back)) }
                    }
                },
                actions = {
                    if (!searchOpen) {
                        // ACT agent.chat_search @android.assistant: the search icon opens the field in the top bar.
                        IconButton(onClick = { searchOpen = true }, modifier = Modifier.testTag("chats-search")) {
                            Icon(Icons.Outlined.Search, stringResource(R.string.chats_search))
                        }
                        IconButton(onClick = cb.newChat, modifier = Modifier.testTag("chats-new")) {
                            Icon(Icons.Outlined.AddComment, stringResource(R.string.asst_new_chat))
                        }
                    }
                    if (wide) IconButton(onClick = cb.close) { Icon(Icons.Outlined.Close, stringResource(R.string.chats_close)) }
                },
            )
        },
        snackbarHost = { SnackbarHost(snackbar) },
    ) { pad ->
        Box(Modifier.padding(pad).fillMaxSize(), contentAlignment = Alignment.TopCenter) {
            val shown = if (ui.searching) ui.results else ui.chats
            when {
                ui.searching && ui.searchFailed && shown.isNullOrEmpty() -> Message(stringResource(R.string.chats_search_failed), error = true)
                ui.searching && shown == null -> Loading()
                ui.searching && shown!!.isEmpty() -> Message(stringResource(R.string.chats_no_match, ui.query.trim()))
                !ui.searching && !ui.loaded -> Loading()
                !ui.searching && ui.problem != null && ui.chats.isEmpty() -> Problem(ui.problem, cb.retry)
                !ui.searching && ui.chats.isEmpty() -> Empty(assistantName)
                else -> Column(Modifier.widthIn(max = 760.dp).fillMaxSize()) {
                    if (ui.searching) {
                        // The number found, for TalkBack.
                        val found = pluralStringResource(R.plurals.chats_found, shown!!.size, shown.size)
                        Box(Modifier.size(1.dp).semantics { liveRegion = LiveRegionMode.Polite; contentDescription = found })
                    }
                    LazyColumn(Modifier.fillMaxSize().testTag("chats-list"), contentPadding = PaddingValues(bottom = 24.dp)) {
                        items(shown!!, key = { it.id }) { c ->
                            ChatRow(c, c.id == current, now, cb, ui.searching) { renaming = c }
                            HorizontalDivider(Modifier.padding(start = 20.dp))
                        }
                    }
                }
            }
        }
    }
    renaming?.let { c -> RenameChatDialog(c, onDismiss = { renaming = null }) { text -> renaming = null; cb.rename(c, text) } }
}

/** [parts] as text, the hits marked. */
@Composable
private fun highlighted(parts: List<TextPart>): AnnotatedString {
    val hit = SpanStyle(background = MaterialTheme.colorScheme.tertiaryContainer, color = MaterialTheme.colorScheme.onTertiaryContainer,
        fontWeight = FontWeight.SemiBold)
    return buildAnnotatedString {
        parts.forEach { p -> if (p.hit) withStyle(hit) { append(p.text) } else append(p.text) }
    }
}

@Composable
private fun ChatRow(c: ChatSummary, isCurrent: Boolean, now: ZonedDateTime, cb: ChatsCallbacks, searching: Boolean, onRename: () -> Unit) {
    val title = c.title.trim().ifBlank { stringResource(R.string.chats_untitled) }
    val currentLabel = stringResource(R.string.chats_current)
    var menu by remember { mutableStateOf(false) }
    Row(
        Modifier.fillMaxWidth()
            .then(if (isCurrent) Modifier.background(MaterialTheme.colorScheme.secondaryContainer.copy(alpha = 0.5f)) else Modifier)
            .heightIn(min = 72.dp)
            .clickable(onClickLabel = stringResource(R.string.chats_open)) { cb.open(c) }
            .semantics(mergeDescendants = true) {
                selected = isCurrent
                if (isCurrent) stateDescription = currentLabel
            }
            .padding(start = 20.dp, top = 10.dp, bottom = 10.dp, end = 4.dp)
            .testTag("chat-row"),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                val titleParts = c.titleParts
                val shownTitle = if (searching && !titleParts.isNullOrEmpty() && c.title.isNotBlank()) highlighted(titleParts) else AnnotatedString(title)
                Text(shownTitle, style = MaterialTheme.typography.titleSmall, fontWeight = if (isCurrent) FontWeight.SemiBold else null,
                    maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                Spacer(Modifier.width(8.dp))
                Text(Format.listDate(c.updatedAt, now), style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            val snippet = c.snippetParts
            if (searching && !snippet.isNullOrEmpty()) {
                Text(highlighted(snippet), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 3, overflow = TextOverflow.Ellipsis, modifier = Modifier.testTag("chat-snippet"))
            } else {
                c.preview?.takeIf { it.isNotBlank() }?.let {
                    Text(it, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 2, overflow = TextOverflow.Ellipsis)
                }
            }
        }
        Box {
            IconButton(onClick = { menu = true }, modifier = Modifier.testTag("chat-more")) {
                Icon(Icons.Outlined.MoreVert, stringResource(R.string.chats_more, title))
            }
            DropdownMenu(menu, { menu = false }) {
                // ACT agent.chat_rename @android.assistant: a small dialog; the snackbar has Undo.
                DropdownMenuItem({ Text(stringResource(R.string.chats_rename)) }, { menu = false; onRename() },
                    leadingIcon = { Icon(Icons.Outlined.Edit, null) }, modifier = Modifier.testTag("chat-rename"))
                DropdownMenuItem({ Text(stringResource(R.string.chats_delete)) }, { menu = false; cb.delete(c) },
                    leadingIcon = { Icon(Icons.Outlined.Delete, null) }, modifier = Modifier.testTag("chat-delete"))
            }
        }
    }
}

/** Rename: the title, at most [CHAT_TITLE_MAX] characters; empty gives back the first question. */
@Composable
fun RenameChatDialog(c: ChatSummary, onDismiss: () -> Unit, save: (String) -> Unit) {
    var text by rememberSaveable(c.id, stateSaver = TextFieldValue.Saver) { mutableStateOf(TextFieldValue(c.title, TextRange(0, c.title.length))) }
    val focus = remember { FocusRequester() }
    val tooLong = cleanChatTitle(text.text).length > CHAT_TITLE_MAX
    LaunchedEffect(Unit) { catching { focus.requestFocus() } }
    AlertDialog(
        onDismissRequest = onDismiss,
        // The dialog sizes itself: with the platform's width a text field in a dialog never lets a
        // Robolectric test go idle (as in NewFolderDialog).
        properties = DialogProperties(usePlatformDefaultWidth = false),
        modifier = Modifier.padding(horizontal = 24.dp).widthIn(max = 560.dp).testTag("rename-chat-dialog"),
        title = { Text(stringResource(R.string.chats_rename_title)) },
        text = {
            OutlinedTextField(
                text, { text = it.copy(text = it.text.replace("\n", " ")) }, singleLine = true,
                label = { Text(stringResource(R.string.chats_rename_label)) },
                isError = tooLong,
                supportingText = { Text(stringResource(R.string.chats_rename_hint)) },
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
                keyboardActions = KeyboardActions(onDone = { if (!tooLong) save(text.text) }),
                modifier = Modifier.fillMaxWidth().focusRequester(focus).testTag("rename-chat-field"),
            )
        },
        confirmButton = {
            TextButton(onClick = { save(text.text) }, enabled = !tooLong, modifier = Modifier.testTag("rename-chat-save")) {
                Text(stringResource(R.string.chats_rename_save))
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.chats_rename_cancel)) } },
    )
}

@Composable
private fun Message(text: String, error: Boolean = false) {
    Text(text, color = if (error) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 24.dp).testTag("chats-no-match"))
}

@Composable
private fun Loading() {
    // The spinner waits a moment, so a quick load shows no flash; its room is kept either way.
    var show by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { kotlinx.coroutines.delay(300); show = true }
    Box(Modifier.fillMaxWidth().heightIn(min = 160.dp), contentAlignment = Alignment.Center) {
        if (show) CircularProgressIndicator(Modifier.testTag("chats-loading"))
    }
}

@Composable
private fun Problem(p: ChatsProblem, retry: () -> Unit) {
    Column(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 24.dp).testTag("chats-problem"),
        horizontalAlignment = Alignment.CenterHorizontally) {
        if (p == ChatsProblem.OFFLINE) {
            Icon(Icons.Outlined.CloudOff, null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.size(8.dp))
            Text(stringResource(R.string.chats_offline_title), style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.semantics { heading() })
            Text(stringResource(R.string.chats_offline_hint), color = MaterialTheme.colorScheme.onSurfaceVariant)
        } else {
            Text(stringResource(R.string.chats_load_failed), color = MaterialTheme.colorScheme.error)
        }
        TextButton(onClick = retry) { Text(stringResource(R.string.chats_retry)) }
    }
}

@Composable
private fun Empty(assistantName: String) {
    Column(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 24.dp).testTag("chats-empty")) {
        Text(stringResource(R.string.chats_empty_title), style = MaterialTheme.typography.titleMedium, modifier = Modifier.semantics { heading() })
        Text(stringResource(R.string.chats_empty_hint, assistantName), color = MaterialTheme.colorScheme.onSurfaceVariant,
            style = MaterialTheme.typography.bodyMedium)
    }
}
