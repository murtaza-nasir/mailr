package io.github.murtazanasir.mailr.ui.assistant

import io.github.murtazanasir.mailr.i18n.AppText
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.automirrored.outlined.OpenInNew
import androidx.compose.material.icons.outlined.AddComment
import androidx.compose.material.icons.outlined.Badge
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.outlined.Public
import androidx.compose.material.icons.automirrored.outlined.StickyNote2
import androidx.compose.material.icons.outlined.Lock
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.CalendarMonth
import androidx.compose.material.icons.outlined.Check
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material.icons.outlined.ExpandLess
import androidx.compose.material.icons.outlined.ExpandMore
import androidx.compose.material.icons.outlined.MailOutline
import androidx.compose.material.icons.outlined.Person
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material.icons.outlined.Stop
import androidx.compose.material.icons.outlined.TaskAlt
import androidx.compose.material.icons.outlined.Warning
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.PlayCircle
import androidx.compose.material.icons.outlined.ArrowDownward
import androidx.compose.material.icons.outlined.GraphicEq
import androidx.compose.material.icons.outlined.History
import androidx.compose.material.icons.automirrored.outlined.Label
import androidx.compose.material.icons.outlined.Psychology
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material.icons.outlined.Terminal
import io.github.murtazanasir.mailr.look.AssistChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.SmallFloatingActionButton
import io.github.murtazanasir.mailr.look.CircularProgressIndicator
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.IconButtonDefaults
import io.github.murtazanasir.mailr.look.InputChip
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextField
import androidx.compose.material3.TextFieldDefaults
import io.github.murtazanasir.mailr.look.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.draw.clip
import androidx.compose.foundation.background
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.platform.UriHandler
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.layout.heightIn
import androidx.compose.material.icons.outlined.Mic
import io.github.murtazanasir.mailr.look.LinearProgressIndicator
import io.github.murtazanasir.mailr.data.api.Approval
import io.github.murtazanasir.mailr.data.agent.ChatSummary
import io.github.murtazanasir.mailr.data.voice.VoiceSource
import io.github.murtazanasir.mailr.data.voice.VoiceState
import io.github.murtazanasir.mailr.ui.ask.AskUi
import io.github.murtazanasir.mailr.data.api.Citation
import io.github.murtazanasir.mailr.data.api.sourceRows
import io.github.murtazanasir.mailr.data.api.ComposePayload
import io.github.murtazanasir.mailr.ui.more.ApprovalCard
import io.github.murtazanasir.mailr.data.api.ReviewState
import io.github.murtazanasir.mailr.ui.util.Format
import io.github.murtazanasir.mailr.ui.present.AgentMarkdown
import io.github.murtazanasir.mailr.ui.present.rememberFollow
import kotlinx.coroutines.launch

data class AssistantCallbacks(
    val send: (String) -> Unit,
    val stop: () -> Unit,
    val newChat: () -> Unit,
    val clearContext: () -> Unit,
    val openCitation: (Citation) -> Unit,
    val openDraft: (Long?, ComposePayload?) -> Unit,
    val decide: (Approval, Boolean, String) -> Unit,
    val openUrl: (String) -> Unit,
    /** Ask on an approval card: the ask capture about it (ACT agent.ask_about @android.assistant); null hides it. */
    val askApproval: ((Approval) -> Unit)? = null,
    /** The "Used memories" line's reads and changes; null hides the line. */
    val memories: MemoryActions? = null,
    /** ⋮ → Memory (the Memory page); null hides the menu. */
    val openMemory: (() -> Unit)? = null,
    /** A person the answer looked up: their card (ACT person.card @android.assistant); null hides the chips. */
    val openPerson: ((String) -> Unit)? = null,
    /** The top bar's Chats button (earlier chats); null hides it. */
    val openChats: (() -> Unit)? = null,
    /** Retry on the last turn when it ended "Connection lost" or "Interrupted" (ACT agent.rerun); null hides it. */
    val retry: (() -> Unit)? = null,
    /** Rename the open chat to the typed text (its title in the top bar, ⋮ → Rename); null hides both. */
    val renameChat: ((String) -> Unit)? = null,
    /** The "Local only" chip's ✕ (`/local` turned off for this chat); null hides the ✕. */
    val localOff: (() -> Unit)? = null,
    /**
     * A bulk review's progress line (ACT agent.review_mail @android.assistant): [followReview] asks for its
     * progress every 2 s while it runs (and hands over its card), for as long as the line is on screen;
     * [reviewAction] is Stop (true) or Continue (false). Null shows no progress lines.
     */
    val followReview: (suspend (Long) -> Unit)? = null,
    val reviewAction: ((Long, Boolean) -> Unit)? = null,
    /** Apply on a review card, without the unticked conversations; null lets Apply approve the whole card. */
    val applyReview: ((Approval, Map<Long, String>) -> Unit)? = null,
    /** A review card's "Always …" on a sender (review id, move, sender): a filter for their future mail; null hides it. */
    val reviewFilter: ((Long, io.github.murtazanasir.mailr.data.api.ReviewGroup, io.github.murtazanasir.mailr.data.api.ReviewSender) -> Unit)? = null,
)


/**
 * The message box's dictation (ACT agent.dictate @android.assistant): the words and the recognizer's state from the
 * ask capture's model (ui/ask/AskCapture.kt). Words land in the box and wait for Send.
 */
data class ComposerVoice(
    val ui: AskUi,
    val setText: (String) -> Unit,
    val listen: () -> Unit,
    val stop: () -> Unit,
    val send: () -> Unit,
    val download: () -> Unit = {},
)

private val SUGGESTIONS = listOf(
    R.string.asst_suggest_reply,
    R.string.asst_suggest_summary,
    R.string.asst_suggest_invoice,
    R.string.asst_suggest_calendar,
)

@Composable
fun AssistantScreen(s: AssistantState, cb: AssistantCallbacks, voice: ComposerVoice? = null, modifier: Modifier = Modifier,
                    snackbar: androidx.compose.material3.SnackbarHostState? = null) {
    // The assistant is off for this user (GET /me access): the message shows instead of a chat.
    val blocked = s.blocked
    var local by rememberSaveable { mutableStateOf("") }
    // With dictation the words live in the model, so dictated and typed words share one field.
    val input = voice?.ui?.text ?: local
    val setInput: (String) -> Unit = voice?.setText ?: { local = it }
    val list = rememberLazyListState()
    // Follow the reply while at the bottom; scrolling up detaches (ui/present/FollowState.kt).
    val last = s.turns.lastOrNull()
    val follow = rememberFollow(list, Triple(s.turns.size, last?.items, last?.approvals))
    val scope = rememberCoroutineScope()
    val userTurns = s.turns.count { it.role == "user" }
    LaunchedEffect(userTurns) { if (userTurns > 0) follow.jump() }
    // A memory's "From a chat": the message it came from, in view (after the jump above) and marked.
    val foundAt = s.turns.indexOfFirst { it.role == "user" && s.found != null && it.messageId == s.found }
    LaunchedEffect(s.found, foundAt) {
        if (foundAt >= 0) {
            withFrameNanos { }
            follow.show(foundAt)
        }
    }
    var renaming by rememberSaveable { mutableStateOf(false) }
    val renameTo = cb.renameChat
    if (renaming && renameTo != null && s.sessionId != null) {
        RenameChatDialog(ChatSummary(s.sessionId, s.title.orEmpty(), renamed = s.titleRenamed), onDismiss = { renaming = false }) { text ->
            renaming = false
            renameTo(text)
        }
    }
    Scaffold(
        modifier = modifier,
        contentWindowInsets = WindowInsets(0),
        snackbarHost = { snackbar?.let { io.github.murtazanasir.mailr.look.SnackbarHost(it) } },
        topBar = {
            // The open chat's title under the name, as the web's panel header has it after the name; a new chat has none.
            val chatTitle = s.title?.trim()?.takeIf { it.isNotEmpty() && s.sessionId != null && blocked == null }
            val rename = cb.renameChat?.takeIf { chatTitle != null }?.let { { renaming = true } }
            TopAppBar(
                title = { if (chatTitle == null) Text(s.name) else ChatTitle(s.name, chatTitle, rename) },
                actions = {
                    // ACT agent.chat_list @android.assistant: earlier chats, from the top bar (no new bottom-bar entry).
                    if (blocked == null) cb.openChats?.let { open ->
                        IconButton(onClick = open, modifier = Modifier.testTag("open-chats")) {
                            Icon(Icons.Outlined.History, stringResource(R.string.chats_title))
                        }
                    }
                    if (blocked == null) IconButton(onClick = cb.newChat, enabled = s.turns.isNotEmpty()) { Icon(Icons.Outlined.AddComment, stringResource(R.string.asst_new_chat)) }
                    if (cb.openMemory != null || rename != null) AssistantMenu(cb.openMemory, rename)
                },
            )
        },
    ) { pad ->
        Column(Modifier.padding(pad).fillMaxSize().imePadding(), horizontalAlignment = Alignment.CenterHorizontally) {
            Box(Modifier.weight(1f).fillMaxWidth()) {
                // An earlier chat is loading: a thin bar over the top edge, so nothing below it moves.
                if (s.opening) LinearProgressIndicator(Modifier.fillMaxWidth().align(Alignment.TopCenter).testTag("chat-opening"))
                if (s.turns.isEmpty() && blocked != null) NoAccess(blocked)
                else if (s.turns.isEmpty()) Welcome(onPick = { cb.send(it) })
                else LazyColumn(state = list, contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(16.dp),
                    horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.fillMaxSize().testTag("chat-log")) {
                    // Retry shows on the last turn only, while nothing runs.
                    val retry = cb.retry?.takeIf { !s.busy && blocked == null && AssistantViewModel.canRetry(s.turns) }
                    itemsIndexed(s.turns, key = { i, _ -> i }) { i, t ->
                        Box(Modifier.widthIn(max = 760.dp).fillMaxWidth()) {
                            if (t.role == "user") UserBubble(t.text, found = s.found != null && t.messageId == s.found)
                            else AssistantTurn(t, cb, retry.takeIf { i == s.turns.lastIndex }, s.reviews, s.reviewActing,
                                cb.reviewFilter?.let { ReviewAlways(s.filtersMade.keys, s.filtersBusy, it) })
                        }
                    }
                }
                // "Jump to latest" while the reader scrolled up and newer output is out of sight (a running reply or a card that waits).
                if (follow.showJump) {
                    SmallFloatingActionButton(onClick = { scope.launch { follow.jump() } },
                        containerColor = MaterialTheme.colorScheme.surfaceContainerHighest,
                        modifier = Modifier.align(Alignment.BottomCenter).padding(bottom = 12.dp).testTag("chat-jump")) {
                        Row(Modifier.padding(horizontal = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                            Icon(Icons.Outlined.ArrowDownward, null, Modifier.size(18.dp))
                            Spacer(Modifier.width(6.dp))
                            Text(stringResource(R.string.asst_jump_latest), style = MaterialTheme.typography.labelLarge)
                        }
                    }
                }
            }
            if (blocked != null) {
                if (s.turns.isNotEmpty()) Text(blocked, style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.widthIn(max = 760.dp).fillMaxWidth().padding(16.dp).testTag("assistant-blocked"))
            } else Composer(input, setInput, s, cb, voice, onSend = { if (voice != null) voice.send() else { cb.send(input); local = "" } },
                sendText = { t -> if (voice != null) { voice.setText(t); voice.send() } else { cb.send(t); local = "" } })
        }
    }
}

/**
 * The open chat's title under Ambrose's name, cut to one line. ACT agent.chat_rename @android.assistant:
 * a tap opens the rename dialog (Chats' own); TalkBack reads the name, the title and "Rename chat".
 */
@Composable
private fun ChatTitle(name: String, title: String, onRename: (() -> Unit)?) {
    val renameLabel = stringResource(R.string.chats_rename_title)
    val described = stringResource(R.string.asst_chat_title_cd, title)
    Column(
        (if (onRename != null) Modifier.clip(MaterialTheme.shapes.small)
            .clickable(onClickLabel = renameLabel, role = androidx.compose.ui.semantics.Role.Button, onClick = onRename) else Modifier)
            .semantics(mergeDescendants = true) {}
            .padding(end = 8.dp)
            .testTag("chat-title"),
    ) {
        Text(name, style = MaterialTheme.typography.titleMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
        Text(title, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.semantics { contentDescription = described })
    }
}

/** Ambrose's ⋮ menu: Memory, the page of what Ambrose remembers (no new bottom-bar entry), and Rename for an open chat. */
@Composable
private fun AssistantMenu(openMemory: (() -> Unit)?, rename: (() -> Unit)?) {
    var open by remember { mutableStateOf(false) }
    androidx.compose.foundation.layout.Box {
        IconButton(onClick = { open = true }, modifier = Modifier.testTag("assistant-more")) {
            Icon(Icons.Outlined.MoreVert, stringResource(R.string.memory_more_options))
        }
        androidx.compose.material3.DropdownMenu(open, { open = false }) {
            // ACT agent.chat_rename @android.assistant
            if (rename != null) androidx.compose.material3.DropdownMenuItem({ Text(stringResource(R.string.chats_rename)) }, { open = false; rename() },
                leadingIcon = { Icon(Icons.Outlined.Edit, null) }, modifier = Modifier.testTag("assistant-rename"))
            if (openMemory != null) androidx.compose.material3.DropdownMenuItem({ Text(stringResource(R.string.memory_title)) }, { open = false; openMemory() },
                leadingIcon = { Icon(Icons.Outlined.Psychology, null) })
        }
    }
}

/** The assistant is off for this user: the server's message, and no message box. */
@Composable
private fun NoAccess(message: String) {
    Column(Modifier.fillMaxSize().padding(24.dp).testTag("assistant-blocked"), horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center) {
        Surface(shape = CircleShape, color = MaterialTheme.colorScheme.surfaceContainerHighest, modifier = Modifier.size(72.dp)) {
            Box(contentAlignment = Alignment.Center) { Icon(Icons.Outlined.Lock, null, Modifier.size(32.dp)) }
        }
        Spacer(Modifier.height(16.dp))
        Text(stringResource(R.string.asst_off, io.github.murtazanasir.mailr.ui.assistantName()), style = MaterialTheme.typography.titleMedium)
        Spacer(Modifier.height(4.dp))
        Text(message, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = androidx.compose.ui.text.style.TextAlign.Center, modifier = Modifier.widthIn(max = 420.dp))
        Text(stringResource(R.string.asst_mail_works), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = androidx.compose.ui.text.style.TextAlign.Center)
    }
}

@Composable
private fun Welcome(onPick: (String) -> Unit) {
    Column(Modifier.fillMaxSize().padding(24.dp), horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center) {
        Surface(shape = CircleShape, color = MaterialTheme.colorScheme.primaryContainer, modifier = Modifier.size(72.dp)) {
            Box(contentAlignment = Alignment.Center) { Icon(Icons.Outlined.AutoAwesome, null, Modifier.size(36.dp)) }
        }
        Spacer(Modifier.height(16.dp))
        Text(stringResource(R.string.asst_welcome), style = MaterialTheme.typography.titleMedium)
        Text(stringResource(R.string.asst_welcome_sub), style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(20.dp))
        SUGGESTIONS.forEach { id ->
            val q = stringResource(id)
            OutlinedButton(onClick = { onPick(q) }, modifier = Modifier.padding(vertical = 4.dp).widthIn(max = 420.dp).fillMaxWidth()) { Text(q) }
        }
    }
}

/** [found]: the message a memory's "From a chat" opened the chat at, outlined. */
@Composable
private fun UserBubble(text: String, found: Boolean = false) {
    Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterEnd) {
        Surface(color = MaterialTheme.colorScheme.primaryContainer, shape = RoundedCornerShape(20.dp, 20.dp, 4.dp, 20.dp),
            border = if (found) androidx.compose.foundation.BorderStroke(2.dp, MaterialTheme.colorScheme.primary) else null,
            modifier = Modifier.widthIn(max = 520.dp).then(if (found) Modifier.testTag("chat-found") else Modifier)) {
            Text(text, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.padding(horizontal = 16.dp, vertical = 10.dp))
        }
    }
}

@Composable
private fun AssistantTurn(t: ChatTurn, cb: AssistantCallbacks, retry: (() -> Unit)? = null,
                          reviews: Map<Long, ReviewState> = emptyMap(), acting: Set<Long> = emptySet(), always: ReviewAlways? = null) {
    val handler = remember(t.citations) {
        object : UriHandler {
            override fun openUri(uri: String) {
                if (uri.startsWith("mailr-cite:")) {
                    uri.removePrefix("mailr-cite:").toIntOrNull()?.let { n -> t.citations.firstOrNull { it.n == n }?.let(cb.openCitation) }
                } else cb.openUrl(uri)
            }
        }
    }
    // The timeline in the order it happened: text, steps, cards, the continuation (ChatTimeline.kt).
    CompositionLocalProvider(LocalUriHandler provides handler) {
        Column(Modifier.fillMaxWidth().animateContentSize(), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            val shown = ChatTimeline.foldRepeatGroups(t.items)
            shown.forEachIndexed { i, item ->
                val lastItem = i == shown.lastIndex
                when (item) {
                    is TurnItem.Text -> if (item.shown.isNotBlank()) AgentMarkdown(item.shown,
                        Modifier.semantics { liveRegion = LiveRegionMode.Polite }, streaming = t.streaming && item.final == null,
                        onCitation = { n -> t.citations.firstOrNull { it.n == n }?.let(cb.openCitation) },
                        citationLabel = { n -> t.citations.firstOrNull { it.n == n }?.let { c -> c.title ?: c.subject } })
                    is TurnItem.Steps -> StepsGroup(item.steps, live = t.streaming && lastItem)
                    is TurnItem.Approval -> t.approvals.firstOrNull { it.id == item.id }?.let { ApprovalCard(it, cb.askApproval, cb.applyReview, always, cb.decide) }
                    is TurnItem.Resume -> ResumeLine(item.text)
                }
            }
            // The bulk reviews this turn started: their progress, then (below) their review card.
            t.reviews.forEach { rid -> ReviewLine(rid, reviews[rid], rid in acting, cb) }
            // Cards the timeline does not place (an older server, a review's card): after the reply.
            t.approvals.filter { a -> t.items.none { it is TurnItem.Approval && it.id == a.id } }.forEach { ApprovalCard(it, cb.askApproval, cb.applyReview, always, cb.decide) }
            if (t.reconnecting) Reconnecting()
            else if (ChatTimeline.thinking(t.items, t.streaming) { id -> t.approvals.any { it.id == id && it.status == "pending" } }) Thinking()
            if (t.citations.isNotEmpty() && !t.streaming) Sources(t.citations, cb.openCitation)
            if (t.memoryIds.isNotEmpty() && !t.streaming) cb.memories?.let { UsedMemories(t.memoryIds, it) }
            if (!t.streaming) cb.openPerson?.let { open ->
                ChatTimeline.lookedUp(t.items).forEach { email ->
                    // A person the answer looked up: their card (contact details and Ambrose's memory).
                    AssistChip(onClick = { open(email) }, label = { Text(stringResource(R.string.person_card_chip, email), maxLines = 1,
                        overflow = TextOverflow.Ellipsis) }, leadingIcon = { Icon(Icons.Outlined.Badge, null) },
                        modifier = Modifier.testTag("open-person-card"))
                }
            }
            t.drafts.forEach { (id, p) ->
                AssistChip(onClick = { cb.openDraft(id, p) }, label = { Text(stringResource(R.string.asst_open_draft, p?.subject?.ifBlank { stringResource(R.string.asst_no_subject) } ?: stringResource(R.string.asst_draft))) },
                    leadingIcon = { Icon(Icons.Outlined.Edit, null) })
            }
            if (t.interrupted && t.error == null) {
                // The run was lost before the reply ended (as the web's "Interrupted" line).
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.semantics(mergeDescendants = true) {}.testTag("turn-interrupted")) {
                    Icon(Icons.Outlined.Warning, null, Modifier.size(16.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
                    Spacer(Modifier.width(8.dp))
                    Text(stringResource(if (t.answer.isNotBlank()) R.string.asst_interrupted_incomplete else R.string.asst_interrupted_empty),
                        style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            if (t.error != null) {
                Surface(color = MaterialTheme.colorScheme.errorContainer, shape = RoundedCornerShape(12.dp)) {
                    Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Outlined.ErrorOutline, null, tint = MaterialTheme.colorScheme.onErrorContainer)
                        Spacer(Modifier.width(8.dp))
                        Text(t.error, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onErrorContainer)
                    }
                }
            }
            retry?.let { RetryButton(it) }
        }
    }
}

/**
 * A review's progress line. While it is on screen and the review runs (or has not been read yet), its
 * progress is asked for every 2 s; it stops when the chat or the screen is left, and starts again after
 * Continue.
 */
@Composable
private fun ReviewLine(rid: Long, r: ReviewState?, acting: Boolean, cb: AssistantCallbacks) {
    val follow = cb.followReview ?: return
    val live = r?.live ?: true
    LaunchedEffect(rid, live) { if (live) follow(rid) }
    if (r != null) ReviewProgressLine(r, acting, onStop = { cb.reviewAction?.invoke(rid, true) }, onResume = { cb.reviewAction?.invoke(rid, false) })
}

/** Retry under a turn that ended "Connection lost" or "Interrupted": the same question again, in the same chat. */
@Composable
private fun RetryButton(onClick: () -> Unit) {
    val label = stringResource(R.string.asst_retry_cd)
    OutlinedButton(onClick = onClick, modifier = Modifier.testTag("turn-retry").semantics { contentDescription = label }) {
        Icon(Icons.Outlined.Refresh, null, Modifier.size(18.dp))
        Spacer(Modifier.width(8.dp))
        Text(stringResource(R.string.asst_retry))
    }
}

/** "Thinking…" where the turn is, while it waits on the model. */
@Composable
private fun Thinking() {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.testTag("thinking")) {
        CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 2.dp)
        Spacer(Modifier.width(8.dp))
        Text(stringResource(R.string.asst_thinking), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/** The stream dropped and the app is re-attaching to the turn; TalkBack reads it once. */
@Composable
private fun Reconnecting() {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.testTag("reconnecting")
        .semantics(mergeDescendants = true) { liveRegion = LiveRegionMode.Polite }) {
        CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 2.dp)
        Spacer(Modifier.width(8.dp))
        Text(stringResource(R.string.asst_reconnecting), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/** Where the turn continues after the user decided an approval. */
@Composable
private fun ResumeLine(text: String) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
        HorizontalDivider(Modifier.weight(1f))
        Text(text, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(horizontal = 8.dp))
        HorizontalDivider(Modifier.weight(1f))
    }
}

/** One round's tool steps: live while it runs, one summary line when done (tap to expand). */
@Composable
private fun StepsGroup(steps: List<Step>, live: Boolean) {
    var open by remember { mutableStateOf(false) }
    val active = steps.lastOrNull { !it.done }
    val waiting = steps.firstOrNull { it.waiting }
    val rows = ChatTimeline.foldRepeats(steps)
    Surface(shape = RoundedCornerShape(14.dp), color = MaterialTheme.colorScheme.surfaceContainerLow, modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.clickable(onClickLabel = stringResource(if (open) R.string.asst_hide_steps else R.string.asst_show_steps)) { open = !open }.padding(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                when {
                    live && active != null -> CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                    waiting != null -> Icon(Icons.Outlined.Schedule, null, Modifier.size(16.dp), tint = MaterialTheme.colorScheme.tertiary)
                    else -> Icon(Icons.Outlined.Check, null, Modifier.size(16.dp), tint = MaterialTheme.colorScheme.primary)
                }
                Spacer(Modifier.width(10.dp))
                Text(
                    when {
                        live && active != null -> friendly(active)
                        rows.size == 1 -> rows[0].let { r ->
                            when {
                                r.step.waiting -> stringResource(R.string.asst_step_waiting, rowLabel(r))
                                r.step.summary.isNotBlank() -> stringResource(R.string.asst_step_summary, rowLabel(r), r.step.summary)
                                else -> rowLabel(r)
                            }
                        }
                        else -> if (waiting != null) pluralStringResource(R.plurals.asst_steps_waiting, rows.size, rows.size)
                            else pluralStringResource(R.plurals.asst_steps, rows.size, rows.size)
                    },
                    style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f),
                )
                if (rows.size > 1 || !live) Icon(if (open) Icons.Outlined.ExpandLess else Icons.Outlined.ExpandMore, null)
            }
            AnimatedVisibility(open || (live && rows.size <= 4)) {
                Column(Modifier.padding(top = 8.dp)) {
                    rows.forEach { row ->
                        val st = row.step
                        Row(Modifier.padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                            Icon(iconFor(st), null, Modifier.size(18.dp),
                                tint = if (!st.ok) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant)
                            Spacer(Modifier.width(10.dp))
                            Column(Modifier.weight(1f)) {
                                Text(rowLabel(row), style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
                                val note = if (st.waiting) stringResource(R.string.asst_waiting_approval) else st.summary
                                if (st.done && note.isNotBlank()) Text(note, style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            }
                            if (!st.done) CircularProgressIndicator(Modifier.size(12.dp), strokeWidth = 1.5.dp)
                            else if (!st.ok) Icon(Icons.Outlined.Warning, stringResource(R.string.asst_failed_desc), Modifier.size(14.dp), tint = MaterialTheme.colorScheme.error)
                        }
                    }
                }
            }
        }
    }
}

/** A row's label, with "×3" when the same call was asked for again (ChatTimeline.foldRepeats). */
private fun rowLabel(r: StepRow): String =
    if (r.times > 1) AppText.get(R.string.asst_step_repeated, friendly(r.step), r.times) else friendly(r.step)

private fun friendly(s: Step): String = s.label.takeIf { it.isNotBlank() && it != s.name } ?: when (s.name) {
    "search_conversations" -> AppText.get(R.string.asst_tool_search_mail)
    "search_semantic", "find_passages" -> AppText.get(R.string.asst_tool_passages)
    "get_conversation", "read_conversation" -> AppText.get(R.string.asst_tool_read_conversation)
    "get_message_body" -> AppText.get(R.string.asst_tool_read_message)
    "get_person" -> AppText.get(R.string.asst_tool_person)
    "build_context" -> AppText.get(R.string.asst_tool_context)
    "list_calendar", "search_events" -> AppText.get(R.string.asst_tool_calendar)
    "list_tasks" -> AppText.get(R.string.asst_tool_tasks)
    "create_draft", "update_draft" -> AppText.get(R.string.asst_tool_draft)
    else -> s.name.replace('_', ' ').replaceFirstChar { it.uppercase() }
}

/** The server's icon family for a step (agent/present.py step_icon), else a guess from its name. */
private fun iconFor(st: Step): ImageVector = when (st.icon) {
    "search" -> Icons.Outlined.Search
    "read" -> Icons.Outlined.MailOutline
    "person" -> Icons.Outlined.Person
    "calendar" -> Icons.Outlined.CalendarMonth
    "task" -> Icons.Outlined.TaskAlt
    "draft" -> Icons.Outlined.Edit
    "send" -> Icons.AutoMirrored.Filled.Send
    "label" -> Icons.AutoMirrored.Outlined.Label
    "recording" -> Icons.Outlined.GraphicEq
    "memory" -> Icons.Outlined.Psychology
    "settings" -> Icons.Outlined.Settings
    "note" -> Icons.AutoMirrored.Outlined.StickyNote2
    else -> iconFor(st.name)
}

private fun iconFor(name: String): ImageVector = when {
    "search" in name || "find" in name -> Icons.Outlined.Search
    "person" in name -> Icons.Outlined.Person
    "calendar" in name || "event" in name -> Icons.Outlined.CalendarMonth
    "task" in name -> Icons.Outlined.TaskAlt
    "draft" in name -> Icons.Outlined.Edit
    "context" in name -> Icons.Outlined.Description
    else -> Icons.Outlined.MailOutline
}

@Composable
private fun Sources(cites: List<Citation>, open: (Citation) -> Unit) {
    Column(Modifier.fillMaxWidth().padding(top = 10.dp)) {
        Text(stringResource(R.string.asst_sources), style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(6.dp))
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            // One card per cited record (sourceRows): a thread cited several times shows once, with all its numbers.
            sourceRows(cites).forEach { row ->
                val c = row.first
                if (c.kind == "recording") { RecordingSource(c, open); return@forEach }
                if (c.kind == "web") { WebSource(c, open); return@forEach }
                if (c.kind == "note") { NoteSource(c, open); return@forEach }
                Surface(onClick = { open(c) }, shape = RoundedCornerShape(12.dp), color = MaterialTheme.colorScheme.surfaceContainerHigh,
                    modifier = Modifier.widthIn(min = 160.dp, max = 240.dp)) {
                    Column(Modifier.padding(10.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            row.all.forEach { x ->
                                Surface(shape = CircleShape, color = MaterialTheme.colorScheme.primary, modifier = Modifier.size(20.dp)) {
                                    Box(contentAlignment = Alignment.Center) {
                                        Text("${x.n}", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onPrimary)
                                    }
                                }
                                Spacer(Modifier.width(4.dp))
                            }
                            Spacer(Modifier.width(2.dp))
                            val calendarItem = c.kind == "event" || c.kind == "task"
                            // An event or task: its calendar by name, with the calendar's colour.
                            if (calendarItem) {
                                Box(Modifier.size(8.dp).clip(CircleShape)
                                    .background(io.github.murtazanasir.mailr.ui.calendar.CalendarColors.of(c.from, c.color)).testTag("cite-calendar-colour"))
                                Spacer(Modifier.width(4.dp))
                            }
                            Text(c.from ?: if (calendarItem) stringResource(R.string.cal_calendar_word) else "",
                                style = MaterialTheme.typography.labelMedium, maxLines = 1, overflow = TextOverflow.Ellipsis,
                                modifier = Modifier.weight(1f))
                            if (!c.verified) Icon(Icons.Outlined.Warning, stringResource(R.string.asst_quote_not_verified), Modifier.size(14.dp),
                                tint = MaterialTheme.colorScheme.error)
                        }
                        Text(c.subject ?: stringResource(R.string.asst_no_subject), style = MaterialTheme.typography.bodySmall, maxLines = 1,
                            overflow = TextOverflow.Ellipsis)
                        c.quote?.let {
                            Text("“$it”", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                                maxLines = 2, overflow = TextOverflow.Ellipsis)
                        }
                        c.date?.let { Text(if (c.kind == "event" || c.kind == "task") Format.citeWhen(it, c.allDay) else Format.listDate(it),
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant) }
                    }
                }
            }
        }
    }
}

/**
 * A web citation: a globe, the page's site and title. A tap opens the page in the browser, outside
 * mailr (App.kt openWebCitation). ACT agent.web_search @android.assistant
 */
@Composable
private fun WebSource(c: Citation, open: (Citation) -> Unit) {
    val site = c.site ?: c.from ?: stringResource(R.string.asst_web_page)
    val title = c.title ?: c.subject ?: site
    val desc = stringResource(R.string.asst_web_source_desc, c.n, title, site)
    Surface(onClick = { open(c) }, shape = RoundedCornerShape(12.dp), color = MaterialTheme.colorScheme.secondaryContainer,
        modifier = Modifier.widthIn(min = 160.dp, max = 240.dp)
            .semantics { contentDescription = desc }) {
        Column(Modifier.padding(10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Outlined.Public, null, Modifier.size(20.dp), tint = MaterialTheme.colorScheme.onSecondaryContainer)
                Spacer(Modifier.width(6.dp))
                Text(site, style = MaterialTheme.typography.labelMedium, maxLines = 1, overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f), color = MaterialTheme.colorScheme.onSecondaryContainer)
                Icon(Icons.AutoMirrored.Outlined.OpenInNew, null, Modifier.size(14.dp), tint = MaterialTheme.colorScheme.onSecondaryContainer)
            }
            Text("${c.n} · $title", style = MaterialTheme.typography.bodySmall, maxLines = 2,
                overflow = TextOverflow.Ellipsis, color = MaterialTheme.colorScheme.onSecondaryContainer)
        }
    }
}

/**
 * A note citation: the note's title and the cited item. A tap opens the note (App.kt).
 */
@Composable
private fun NoteSource(c: Citation, open: (Citation) -> Unit) {
    val what = stringResource(if (c.noteKind == "checklist") R.string.asst_note_list else R.string.asst_note_note)
    val title = c.title ?: c.subject ?: what
    val noteDesc = c.snippet?.let { stringResource(R.string.asst_note_source_desc_snippet, what, c.n, title, it) }
        ?: stringResource(R.string.asst_note_source_desc, what, c.n, title)
    Surface(onClick = { open(c) }, shape = RoundedCornerShape(12.dp), color = MaterialTheme.colorScheme.surfaceContainerHigh,
        modifier = Modifier.widthIn(min = 160.dp, max = 240.dp)
            .semantics { contentDescription = noteDesc }) {
        Column(Modifier.padding(10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.AutoMirrored.Outlined.StickyNote2, null, Modifier.size(20.dp), tint = MaterialTheme.colorScheme.primary)
                Spacer(Modifier.width(6.dp))
                Text(what, style = MaterialTheme.typography.labelMedium, maxLines = 1, overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f))
            }
            Text("${c.n} · $title", style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
            c.snippet?.let {
                Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 2, overflow = TextOverflow.Ellipsis)
            }
        }
    }
}

/**
 * A recording citation ("▶ Weekly sync · 12:34", the speaker and the quote). A tap opens the
 * recording sheet at that moment when Recordings (Speakr) is on (App.kt). ACT recording.view @android.recording
 */
@Composable
private fun RecordingSource(c: Citation, open: (Citation) -> Unit) {
    val title = c.title ?: c.subject ?: stringResource(R.string.asst_recording)
    val at = io.github.murtazanasir.mailr.ui.recordings.fmtT(c.t)
    val recDesc = if (at.isNotEmpty()) stringResource(R.string.asst_recording_source_desc_at, c.n, title, at)
        else stringResource(R.string.asst_recording_source_desc, c.n, title)
    Surface(onClick = { open(c) }, shape = RoundedCornerShape(12.dp), color = MaterialTheme.colorScheme.tertiaryContainer,
        modifier = Modifier.widthIn(min = 160.dp, max = 240.dp)
            .semantics { contentDescription = recDesc }) {
        Column(Modifier.padding(10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Outlined.PlayCircle, null, Modifier.size(20.dp), tint = MaterialTheme.colorScheme.onTertiaryContainer)
                Spacer(Modifier.width(6.dp))
                Text(c.speaker ?: stringResource(R.string.asst_recording), style = MaterialTheme.typography.labelMedium, maxLines = 1,
                    overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f), color = MaterialTheme.colorScheme.onTertiaryContainer)
                if (!c.verified) Icon(Icons.Outlined.Warning, stringResource(R.string.asst_quote_not_in_transcript), Modifier.size(14.dp),
                    tint = MaterialTheme.colorScheme.error)
            }
            Text("${c.n} · $title${if (at.isNotEmpty()) " · $at" else ""}", style = MaterialTheme.typography.bodySmall, maxLines = 1, // i18n-ignore: number, title and time only
                overflow = TextOverflow.Ellipsis, color = MaterialTheme.colorScheme.onTertiaryContainer)
            c.quote?.let {
                Text("“$it”", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onTertiaryContainer,
                    maxLines = 2, overflow = TextOverflow.Ellipsis)
            }
        }
    }
}

@Composable
private fun Composer(input: String, onInput: (String) -> Unit, s: AssistantState, cb: AssistantCallbacks, voice: ComposerVoice?,
                     onSend: () -> Unit, sendText: (String) -> Unit) {
    val v = voice?.ui
    val mic = v != null && v.source != VoiceSource.NONE
    val canSend = input.isNotBlank() && v?.transcribing != true
    // ACT agent.commands @android.assistant: a `/` that opens the message lists the commands, then the
    // skills, filtered as the name is typed. Back (or deleting the `/`) closes the list.
    val picks = ChatCommands.picker(input, s.commands, s.skills)
    var dismissed by remember { mutableStateOf(false) }
    val typing = input.startsWith("/")
    LaunchedEffect(typing) { if (!typing) dismissed = false }
    val pickerOpen = picks.isNotEmpty() && !dismissed && !s.busy
    val pick: (PickItem) -> Unit = { p -> if (p.complete) sendText("/${p.name}") else onInput(ChatCommands.insert(p)) }
    Surface(tonalElevation = 2.dp, modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.widthIn(max = 800.dp).fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp)) {
            if (pickerOpen) {
                androidx.activity.compose.BackHandler { dismissed = true }
                CommandPicker(picks, pick)
            }
            if (s.localOnly) LocalOnlyChip(cb.localOff)
            val about = s.about?.let { a ->
                a.title.ifBlank {
                    when (a) {
                        is io.github.murtazanasir.mailr.data.agent.AskAbout.Event -> stringResource(R.string.asst_about_event)
                        is io.github.murtazanasir.mailr.data.agent.AskAbout.Task -> stringResource(R.string.asst_about_task)
                        is io.github.murtazanasir.mailr.data.agent.AskAbout.Recording -> stringResource(R.string.asst_about_recording)
                        is io.github.murtazanasir.mailr.data.agent.AskAbout.Conversation -> stringResource(R.string.asst_about_conversation)
                        is io.github.murtazanasir.mailr.data.agent.AskAbout.Approval -> stringResource(R.string.asst_about_approval)
                        is io.github.murtazanasir.mailr.data.agent.AskAbout.Note -> stringResource(if (a.kind == "table") R.string.asst_about_table else R.string.asst_about_note)
                    }
                }
            } ?: s.contextConversation?.let { s.contextTitle ?: stringResource(R.string.asst_about_conversation) }
            if (about != null) {
                InputChip(selected = true, onClick = cb.clearContext,
                    label = { Text(stringResource(R.string.asst_about, about), maxLines = 1, overflow = TextOverflow.Ellipsis) },
                    trailingIcon = { Icon(Icons.Outlined.Close, stringResource(R.string.asst_remove_context), Modifier.size(16.dp)) },
                    modifier = Modifier.widthIn(max = 360.dp))
            }
            if (v != null) VoiceLine(v, voice.download)
            Row(verticalAlignment = Alignment.Bottom) {
                // Dictated words put the cursor after them (not at the start of the box).
                val (field, onField) = io.github.murtazanasir.mailr.ui.components.rememberDictatedText(input, onInput)
                TextField(
                    value = field, onValueChange = onField,
                    placeholder = { Text(when (s.about) {
                        is io.github.murtazanasir.mailr.data.agent.AskAbout.Event -> stringResource(R.string.asst_ask_event)
                        is io.github.murtazanasir.mailr.data.agent.AskAbout.Task -> stringResource(R.string.asst_ask_task)
                        is io.github.murtazanasir.mailr.data.agent.AskAbout.Recording -> stringResource(R.string.asst_ask_recording)
                        is io.github.murtazanasir.mailr.data.agent.AskAbout.Conversation -> stringResource(R.string.asst_ask_conversation)
                        is io.github.murtazanasir.mailr.data.agent.AskAbout.Approval -> stringResource(R.string.asst_ask_approval)
                        is io.github.murtazanasir.mailr.data.agent.AskAbout.Note -> stringResource(R.string.asst_ask_note)
                        null -> stringResource(R.string.asst_ask_name, io.github.murtazanasir.mailr.ui.assistantName())
                    }) },
                    maxLines = 6,
                    keyboardOptions = KeyboardOptions(imeAction = androidx.compose.ui.text.input.ImeAction.Send),
                    // With the picker open, Send takes its first row (as Enter does on the web).
                    keyboardActions = KeyboardActions(onSend = { if (pickerOpen) pick(picks.first()) else if (canSend && !s.busy) onSend() }),
                    trailingIcon = if (!mic) null else ({
                        if (v!!.listening) IconButton(onClick = voice!!.stop, modifier = Modifier.testTag("composer-mic")) {
                            Icon(Icons.Outlined.Stop, stringResource(R.string.asst_stop_dictation), tint = MaterialTheme.colorScheme.error)
                        } else IconButton(onClick = voice!!.listen, enabled = !v.transcribing, modifier = Modifier.testTag("composer-mic")) {
                            Icon(Icons.Outlined.Mic, stringResource(R.string.asst_dictate))
                        }
                    }),
                    shape = RoundedCornerShape(24.dp),
                    colors = TextFieldDefaults.colors(focusedIndicatorColor = Color.Transparent, unfocusedIndicatorColor = Color.Transparent,
                        disabledIndicatorColor = Color.Transparent),
                    modifier = Modifier.weight(1f),
                )
                Spacer(Modifier.width(8.dp))
                if (s.busy) FilledIconButton(onClick = cb.stop, modifier = Modifier.size(52.dp),
                    colors = IconButtonDefaults.filledIconButtonColors(containerColor = MaterialTheme.colorScheme.errorContainer,
                        contentColor = MaterialTheme.colorScheme.onErrorContainer)) { Icon(Icons.Outlined.Stop, stringResource(R.string.asst_stop)) }
                else FilledIconButton(onClick = onSend, enabled = canSend, modifier = Modifier.size(52.dp)) {
                    Icon(Icons.AutoMirrored.Filled.Send, stringResource(R.string.asst_send))
                }
            }
        }
    }
}

/**
 * The commands and skills for the `/` being typed, above the message box: each row has its name and one
 * line on what it does. A tap puts `/name ` in the box, or sends a command that takes no text once its
 * whole name is typed.
 */
@Composable
private fun CommandPicker(items: List<PickItem>, onPick: (PickItem) -> Unit) {
    val label = stringResource(R.string.asst_cmd_picker)
    Surface(shape = RoundedCornerShape(16.dp), color = MaterialTheme.colorScheme.surfaceContainerHigh,
        modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp).testTag("command-picker").semantics { contentDescription = label }) {
        LazyColumn(Modifier.heightIn(max = 280.dp), contentPadding = PaddingValues(vertical = 6.dp)) {
            items(items.size, key = { i -> (if (items[i].command) "c:" else "s:") + items[i].name }) { i ->
                val p = items[i]
                Row(Modifier.fillMaxWidth().clickable(role = androidx.compose.ui.semantics.Role.Button) { onPick(p) }
                    .padding(horizontal = 14.dp, vertical = 8.dp).testTag("pick-${p.name}"),
                    verticalAlignment = Alignment.CenterVertically) {
                    Icon(if (p.command) Icons.Outlined.Terminal else Icons.Outlined.AutoAwesome, null, Modifier.size(18.dp),
                        tint = MaterialTheme.colorScheme.onSurfaceVariant)
                    Spacer(Modifier.width(12.dp))
                    Text("/${p.name}", style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary, maxLines = 1)
                    Spacer(Modifier.width(10.dp))
                    Text(p.desc, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                }
            }
        }
    }
}

/** `/local`: the chat stays on the local model; the ✕ turns it off. */
@Composable
private fun LocalOnlyChip(onOff: (() -> Unit)?) {
    InputChip(selected = true, onClick = { onOff?.invoke() },
        label = { Text(stringResource(R.string.asst_cmd_local_chip), maxLines = 1) },
        leadingIcon = { Icon(Icons.Outlined.Lock, null, Modifier.size(16.dp)) },
        trailingIcon = if (onOff == null) null else ({ Icon(Icons.Outlined.Close, stringResource(R.string.asst_cmd_local_off), Modifier.size(16.dp)) }),
        modifier = Modifier.testTag("chat-local-only"))
}

/** Above the message box while dictating: listening with the input level, transcribing, or why nothing came. */
@Composable
private fun VoiceLine(v: AskUi, download: () -> Unit) {
    val cs = MaterialTheme.colorScheme
    val mod = Modifier.fillMaxWidth().padding(start = 8.dp, end = 4.dp, bottom = 6.dp).heightIn(min = 24.dp)
        .semantics { liveRegion = LiveRegionMode.Polite }.testTag("composer-voice")
    when (val st = v.voice) {
        is VoiceState.Listening -> Row(mod, verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Outlined.GraphicEq, null, Modifier.size(18.dp), tint = cs.primary)
            Spacer(Modifier.width(8.dp))
            Text(stringResource(if (v.source == VoiceSource.SERVER) R.string.asst_voice_recording else R.string.asst_voice_listening),
                style = MaterialTheme.typography.labelLarge, color = cs.primary, modifier = Modifier.weight(1f))
            LinearProgressIndicator(progress = { 0.08f + 0.92f * st.level }, modifier = Modifier.width(64.dp).height(4.dp))
        }
        VoiceState.Transcribing -> Row(mod, verticalAlignment = Alignment.CenterVertically) {
            CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
            Spacer(Modifier.width(8.dp))
            Text(stringResource(R.string.asst_voice_transcribing), style = MaterialTheme.typography.labelLarge, color = cs.primary)
        }
        is VoiceState.Failed -> Row(mod, verticalAlignment = Alignment.CenterVertically) {
            Text(st.message, style = MaterialTheme.typography.bodySmall, color = cs.error, modifier = Modifier.weight(1f))
            if (st.download) androidx.compose.material3.TextButton(onClick = download) { Text(stringResource(R.string.asst_get_model)) }
        }
        else -> Unit
    }
}
