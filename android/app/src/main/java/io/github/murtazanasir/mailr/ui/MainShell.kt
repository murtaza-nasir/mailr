package io.github.murtazanasir.mailr.ui

import io.github.murtazanasir.mailr.util.catching

// The tab hosts: the bottom bar or rail (AppNavigation), the tabs inside it (MainShell), the Ambrose tab, and
// what a citation in a chat opens.

import io.github.murtazanasir.mailr.data.agent.AskAbout
import android.content.Context
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.Box
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.SmartToy
import androidx.compose.material.icons.automirrored.filled.StickyNote2
import androidx.compose.material.icons.outlined.SmartToy
import androidx.compose.material.icons.automirrored.outlined.StickyNote2
import androidx.compose.material.icons.filled.CalendarMonth
import androidx.compose.material.icons.filled.Mail
import androidx.compose.material.icons.filled.TaskAlt
import androidx.compose.material.icons.outlined.CalendarMonth
import androidx.compose.material.icons.outlined.Mail
import androidx.compose.material.icons.outlined.TaskAlt
import androidx.compose.material3.Icon
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.adaptive.currentWindowAdaptiveInfo
import androidx.compose.material3.adaptive.navigationsuite.NavigationSuiteScaffold
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavHostController
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.data.api.Citation
import io.github.murtazanasir.mailr.data.api.ComposePayload
import io.github.murtazanasir.mailr.ui.assistant.AssistantCallbacks
import io.github.murtazanasir.mailr.ui.assistant.ComposerVoice
import android.Manifest
import android.content.pm.PackageManager
import androidx.core.content.ContextCompat
import io.github.murtazanasir.mailr.ui.assistant.AssistantScreen
import io.github.murtazanasir.mailr.ui.assistant.AssistantViewModel
import io.github.murtazanasir.mailr.ui.compose.ComposeArgs
import io.github.murtazanasir.mailr.ui.compose.RestoreStore
import io.github.murtazanasir.mailr.ui.mail.MailboxViewModel
import io.github.murtazanasir.mailr.ui.calendar.CalendarRoute
import io.github.murtazanasir.mailr.ui.calendar.CalendarViewModel
import io.github.murtazanasir.mailr.ui.calendar.EventRequest
import io.github.murtazanasir.mailr.ui.calendar.NEW_TASK_REQUEST
import io.github.murtazanasir.mailr.ui.calendar.TasksRoute
import io.github.murtazanasir.mailr.ui.calendar.TasksViewModel
import io.github.murtazanasir.mailr.ui.recordings.LocalRecordings
import io.github.murtazanasir.mailr.ui.recordings.Recordings
import io.github.murtazanasir.mailr.ui.memory.isVip
import kotlinx.coroutines.launch
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import io.github.murtazanasir.mailr.R

/** The bottom bar, in its order: Mail, Calendar, Tasks, Notes, then Ambrose
 *  at the far right, as on the web. Five is the ceiling: nothing is ever added past five. */
enum class Tab(@androidx.annotation.StringRes val label: Int) {
    MAIL(R.string.ui_shell_tab_mail), CALENDAR(R.string.ui_shell_tab_calendar), TASKS(R.string.ui_shell_tab_tasks),
    NOTES(R.string.ui_shell_tab_notes), ASSISTANT(R.string.ui_shell_tab_assistant),
}

@Composable
internal fun MainShell(
    g: AppGraph,
    nav: NavHostController,
    mailbox: MailboxViewModel,
    assistant: AssistantViewModel,
    snackbar: SnackbarHostState,
    tab: Tab,
    setTab: (Tab) -> Unit,
    openTarget: Destination.Conversation?,
    clearTarget: () -> Unit,
    openConversation: (Destination.Conversation) -> Unit,
    eventRequest: EventRequest?,
    clearEventRequest: () -> Unit,
    taskRequest: String?,
    clearTaskRequest: () -> Unit,
    requestTask: (String) -> Unit = {},
    notesRequest: Destination.Notes? = null,
    clearNotesRequest: () -> Unit = {},
    openNotes: (Destination.Notes) -> Unit = {},
    requestEvent: (EventRequest) -> Unit = {},
) {
    val shellScope = rememberCoroutineScope()
    val recordings = LocalRecordings.current
    val ctx = LocalContext.current
    val labels by mailbox.labels.collectAsStateWithLifecycle()
    val calendarVm: CalendarViewModel = viewModel { CalendarViewModel(g) }
    val tasksVm: TasksViewModel = viewModel { TasksViewModel(g) }
    val prefs by g.settings.prefs.collectAsStateWithLifecycle(io.github.murtazanasir.mailr.data.settings.Prefs())
    val inboxCount by g.mail.inboxCount.collectAsStateWithLifecycle()
    val inboxUnread = io.github.murtazanasir.mailr.data.api.inboxBadge(inboxCount, labels)
    val waiting by io.github.murtazanasir.mailr.ui.approvals.ApprovalsTracker.of(g).pending.collectAsStateWithLifecycle()
    val assistantName = assistant.state.collectAsStateWithLifecycle().value.name
    LaunchedEffect(assistant) { assistant.loadName() }
    // An item opened from Ambrose's chat (a citation or a source) shows in its own tab; back returns to
    // the chat until another tab is chosen.
    var fromAssistant by androidx.compose.runtime.saveable.rememberSaveable { androidx.compose.runtime.mutableStateOf(false) }
    val chooseTab: (Tab) -> Unit = { t -> fromAssistant = false; setTab(t) }
    AppNavigation(tab, chooseTab, inboxUnread, assistantBadge = waiting?.size ?: 0, assistantName = assistantName) {
        when (tab) {
            Tab.MAIL -> MailPane(g, nav, mailbox, assistant, snackbar, openTarget, clearTarget, setTab,
                openNote = { id -> openNotes(Destination.Notes("note", id)) })
            Tab.ASSISTANT -> AssistantRoute(nav, assistant, onOpenCitation = { c ->
                val notes = notesTarget(c)
                val record = recordTarget(c)
                when {
                    notes != null -> { fromAssistant = true; openNotes(notes) }
                    openRecordingCitation(recordings, ctx, c) || openWebCitation(ctx, c) -> Unit
                    // An event or task citation opens the record in its tab (the web opens eventPath / taskPath).
                    record is Destination.Event -> { fromAssistant = true; requestEvent(EventRequest(record.uid, record.occurrence)); setTab(Tab.CALENDAR) }
                    record is Destination.Task -> { fromAssistant = true; requestTask(record.uid!!); setTab(Tab.TASKS) }
                    else -> openTargetFrom(c)?.let { fromAssistant = true; openConversation(it) }
                }
            })
            // ACT task.open_email @android.editor, ACT event.open_email @android.editor: the editors open the conversation in the Mail tab.
            Tab.CALENDAR -> CalendarRoute(calendarVm, prefs.morningHour, prefs.defaultEventReminder.takeIf { it >= 0 },
                eventRequest, clearEventRequest, openEmail = { id -> openConversation(Destination.Conversation(id)) },
                // ACT task.create @android.tasks: "Task" in the FAB menu opens Tasks with the quick-add bar focused.
                newTask = { requestTask(NEW_TASK_REQUEST); setTab(Tab.TASKS) },
                // ACT event.email_guests @android.editor: the composer, addressed to the guests.
                compose = { to, subject, body -> nav.openCompose(ComposeArgs(to = to, subject = subject, body = body)) },
                // Ask assistant: the ask capture about this occurrence (context.focused_event); Send opens
                // the chat (ACT agent.ask_about).
                askAssistant = { e ->
                    ctx.startActivity(io.github.murtazanasir.mailr.ui.ask.AskIntents.capture(ctx, AskAbout.Event(e.uid, e.occurrence, e.summary), from = "event"))
                })
            Tab.NOTES -> io.github.murtazanasir.mailr.ui.notes.NotesTab(g, notesRequest, clearNotesRequest,
                openConversation = { id -> openConversation(Destination.Conversation(id)) },
                openTask = { uid -> requestTask(uid); setTab(Tab.TASKS) })
            Tab.TASKS -> TasksRoute(tasksVm, prefs.morningHour, taskRequest, clearTaskRequest,
                openEmail = { id -> openConversation(Destination.Conversation(id)) },
                // Ask assistant: the ask capture about this task (context.focused_task); Send opens the chat
                // (ACT agent.ask_about).
                askAssistant = { t ->
                    ctx.startActivity(io.github.murtazanasir.mailr.ui.ask.AskIntents.capture(ctx, AskAbout.Task(t.uid, t.summary), from = "task"))
                },
                // ACT task.reply_email @android.editor: a reply to the last message of the task's email.
                replyEmail = { id ->
                    shellScope.launch {
                        val msg = catching { g.api.conversation(id) }.getOrNull()?.messages?.lastOrNull { !it.draft }
                        if (msg != null) nav.openCompose(ComposeArgs(replyToMessageId = msg.id, mode = "reply"))
                        else openConversation(Destination.Conversation(id))
                    }
                })
        }
    }
    BackToAssistant(fromAssistant && tab != Tab.ASSISTANT, tab) { fromAssistant = false; setTab(Tab.ASSISTANT) }
}

/**
 * Back from an item opened from Ambrose's chat returns to the chat (note #13). Composed after the tabs'
 * content and registered again on each tab change, so it comes before the tab's own handlers (an open
 * conversation would otherwise close to its list first). A sheet or dialog over the item still closes first:
 * it is a window of its own.
 */
@Composable
internal fun BackToAssistant(enabled: Boolean, tab: Tab, onBack: () -> Unit) {
    androidx.compose.runtime.key(tab) {
        androidx.activity.compose.BackHandler(enabled, onBack)
    }
}

/**
 * An event or task citation (`kind` event or task, with its uid) as the place it opens: the Calendar tab at
 * the occurrence, or the Tasks tab at the task. Null for other citations.
 */
internal fun recordTarget(c: Citation): Destination? = when {
    c.kind == "event" && !c.uid.isNullOrBlank() -> Destination.Event(c.uid, c.occurrence)
    c.kind == "task" && !c.uid.isNullOrBlank() -> Destination.Task(c.uid)
    else -> null
}

/**
 * A recording citation opens the recording sheet at its moment (ACT recording.view @android.assistant); with
 * Recordings (Speakr) off, Speakr's own link opens in the browser. False for other citations.
 */
internal fun openRecordingCitation(rec: Recordings?, ctx: Context, c: Citation): Boolean {
    if (c.kind != "recording") return false
    val connector = c.connectorId
    val recording = c.recordingId
    if (rec != null && c.isRecording && connector != null && recording != null) rec.show(connector, recording, c.t)
    else c.url?.let { openUrl(ctx, it) }
    return true
}

/**
 * A note or item citation ([[note:…#item]]) or a table or row citation ([[table:…#row]]) opens the
 * native Notes screen: the note with the item focused, the table with the row's form.
 * Null for other citations.
 */
fun notesTarget(c: Citation): Destination.Notes? = when {
    // A citation opens the note with its item focused, or the table with its row.
    c.kind == "note" && c.noteId != null -> Destination.Notes("note", c.noteId, c.itemId)
    c.kind == "table" && c.tableId != null -> Destination.Notes("table", c.tableId, c.rowId)
    else -> null
}

/**
 * A web citation opens its page in the browser (a Custom Tab), outside mailr (agent.web_search).
 * False for other citations; one without a usable address does nothing.
 */
internal fun openWebCitation(ctx: Context, c: Citation): Boolean {
    if (c.kind != "web") return false
    if (c.isWeb) c.url?.let { openUrl(ctx, it) }
    return true
}

internal fun openTargetFrom(c: Citation): Destination.Conversation? =
    c.conversationId?.let { Destination.Conversation(it, c.messageId, c.quote?.takeIf { c.verified }) }

@Composable
internal fun AssistantRoute(nav: NavHostController, vm: AssistantViewModel, onOpenCitation: (Citation) -> Unit) {
    val s by vm.state.collectAsStateWithLifecycle()
    val ctx = LocalContext.current
    // ACT agent.dictate @android.assistant: the message box's microphone, by Settings → Dictation (phone first, or the
    // user's own speech server through mailr). Words land in the box; Send sends them.
    val g = ctx.graph
    val prefs by g.settings.prefs.collectAsStateWithLifecycle(null)
    val me by g.mail.me.collectAsStateWithLifecycle()
    val server = me?.features?.transcribe ?: prefs?.serverDictation ?: false
    LaunchedEffect(prefs?.dictationOrder, server) { prefs?.let { vm.refreshVoice(it.dictationOrder, server) } }
    var person by androidx.compose.runtime.saveable.rememberSaveable { androidx.compose.runtime.mutableStateOf<String?>(null) }
    val c by vm.composer.ui.collectAsStateWithLifecycle()
    val mic = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { ok -> if (ok) vm.composer.listen() }
    val voice = ComposerVoice(c, vm.composer::setText,
        listen = {
            if (ContextCompat.checkSelfPermission(ctx, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) vm.composer.listen()
            else mic.launch(Manifest.permission.RECORD_AUDIO)
        },
        stop = vm.composer::stopListening, send = vm::sendComposer, download = { vm.composer.download() })
    // Before GET /me answers this run, the last access the app saw decides whether the chat shows.
    val shown = if (me == null && s.blocked == null) s.copy(blocked = io.github.murtazanasir.mailr.data.api.AssistantGate.blocked(null, prefs?.assistantBlocked)) else s
    // Chats (earlier chats): in place of the chat on a phone, a pane beside it on a wider window.
    var chatsOpen by androidx.compose.runtime.saveable.rememberSaveable { androidx.compose.runtime.mutableStateOf(false) }
    val wide = currentWindowAdaptiveInfo().windowSizeClass.windowWidthSizeClass != androidx.window.core.layout.WindowWidthSizeClass.COMPACT
    val notices = androidx.compose.runtime.remember { SnackbarHostState() }
    LaunchedEffect(vm) { vm.notices.collect { notices.showSnackbar(it) } }
    // The slash commands and skills for the message box's picker (asked again at a `/` when they did not load).
    val slash = c.text.startsWith("/")
    LaunchedEffect(vm, slash) { vm.loadCommands() }
    // A rename from the top bar: "Chat renamed." with Undo (as in Chats); `/delete`: "Chat deleted." with Undo;
    // `/note`: "Saved to a note" with Open (the note in Notes); a review card's "Always …": "Filter made …" with Undo.
    LaunchedEffect(vm) {
        vm.renames.collect { m ->
            val r = m.undoRename
            val d = m.undo
            val f = m.undoFilter
            val note = m.openNote
            notices.currentSnackbarData?.dismiss()
            launch {
                val undo = r != null || d != null || f != null
                val action = when {
                    undo -> io.github.murtazanasir.mailr.i18n.AppText.get(R.string.chats_undo)
                    note != null -> io.github.murtazanasir.mailr.i18n.AppText.get(R.string.asst_note_open)
                    else -> null
                }
                val res = notices.showSnackbar(m.text, action,
                    duration = if (action != null) androidx.compose.material3.SnackbarDuration.Long else androidx.compose.material3.SnackbarDuration.Short)
                val undone = res == androidx.compose.material3.SnackbarResult.ActionPerformed
                if (r != null && undone) vm.undoRename(r)
                if (d != null) { if (undone) vm.open(d.id) else vm.commitDelete(d) }
                if (f != null && undone) vm.undoFilter(f)
                if (note != null && undone) onOpenCitation(Citation(0, "note", noteId = note))
            }
        }
    }
    // `/chats words`: Chats opens, searched for the words.
    var chatsQuery by androidx.compose.runtime.saveable.rememberSaveable { androidx.compose.runtime.mutableStateOf<String?>(null) }
    LaunchedEffect(vm) {
        vm.commandEffects.collect { e ->
            when (e) {
                is io.github.murtazanasir.mailr.ui.assistant.CommandEffect.OpenChats -> {
                    chatsQuery = e.query.takeIf { it.isNotBlank() }
                    chatsOpen = true
                }
            }
        }
    }
    androidx.activity.compose.BackHandler(enabled = chatsOpen && !wide) { chatsOpen = false }
    io.github.murtazanasir.mailr.ui.assistant.ChatsLayout(wide, chatsOpen && shown.blocked == null, list = { mod ->
        val chats: io.github.murtazanasir.mailr.ui.assistant.ChatListViewModel = viewModel {
            io.github.murtazanasir.mailr.ui.assistant.ChatListViewModel({ g.agent.sessions() }, { g.agent.deleteSession(it) }, g.scope,
                onDeleted = { id -> vm.deleted(id) }, find = { q -> g.agent.searchSessions(q) },
                rename = { id, title -> g.agent.renameSession(id, title) },
                onRenamed = { id, title, renamed -> vm.titleChanged(id, title, renamed) })
        }
        // The open chat's title as the top bar has it (a rename there, a new chat's first title).
        LaunchedEffect(chats, s.sessionId, s.title, s.titleRenamed) {
            val id = s.sessionId
            val t = s.title
            if (id != null && t != null) chats.showTitle(id, t, s.titleRenamed)
        }
        val ui by chats.ui.collectAsStateWithLifecycle()
        val snack = androidx.compose.runtime.remember { SnackbarHostState() }
        LaunchedEffect(Unit) { chats.load() }
        LaunchedEffect(chats, chatsQuery) { chatsQuery?.let { q -> chats.search(q); chatsQuery = null } }
        io.github.murtazanasir.mailr.ui.assistant.ChatsMessages(chats, snack)
        io.github.murtazanasir.mailr.ui.assistant.ChatListScreen(ui, s.name, s.sessionId, io.github.murtazanasir.mailr.ui.assistant.ChatsCallbacks(
            open = { c -> vm.open(c.id); if (!wide) chatsOpen = false },
            newChat = { vm.newChat(); if (!wide) chatsOpen = false },
            delete = chats::delete, retry = chats::load, close = { chatsOpen = false },
            search = chats::search, rename = chats::rename),
            snack, mod, wide = wide)
    }) { mod ->
    AssistantScreen(shown, voice = voice, modifier = mod, snackbar = notices, cb = AssistantCallbacks(
        send = vm::send, stop = vm::stop, newChat = vm::newChat, clearContext = { vm.setContext(null, null) },
        openCitation = onOpenCitation,
        // ACT draft.open @android.assistant: a draft that is open already comes forward (openCompose, OpenEditors).
        openDraft = { id, p: ComposePayload? ->
            if (id != null) nav.openCompose(ComposeArgs(draftId = id))
            else if (p != null) nav.openCompose(ComposeArgs(restoreKey = RestoreStore.put(p, null)))
        },
        decide = vm::decide,
        openUrl = { openUrl(ctx, it) },
        askApproval = { a -> ctx.startActivity(io.github.murtazanasir.mailr.ui.ask.AskIntents.capture(ctx,
            io.github.murtazanasir.mailr.ui.approvals.askAbout(a), from = "approval")) },
        memories = io.github.murtazanasir.mailr.ui.assistant.MemoryActions(
            load = { ids -> g.api.memoriesById(ids) },
            save = { id, text -> g.api.updateMemory(id, text) },
            delete = { id -> g.api.deleteMemory(id) }),
        openMemory = { nav.navigate(Routes.MEMORY) { launchSingleTop = true } },
        openPerson = { person = it },
        openChats = { chatsOpen = !chatsOpen || !wide },
        retry = vm::retry,
        renameChat = vm::renameChat,
        localOff = { vm.setLocalOnly(false) },
        followReview = vm::followReview,
        reviewAction = vm::reviewAction,
        applyReview = vm::applyReview,
        reviewFilter = vm::makeReviewFilter,
    ))
    }
    // ACT person.card @android.assistant: the card of a person the answer looked up, as a sheet over the chat.
    person?.let { email ->
        val store = androidx.compose.runtime.remember { io.github.murtazanasir.mailr.ui.memory.ApiMemoryStore(g) }
        io.github.murtazanasir.mailr.ui.memory.PersonCardSheet(io.github.murtazanasir.mailr.ui.memory.PersonTarget.Email(email), store, s.name,
            vip = { me.isVip(it) }, onClose = { person = null },
            // A memory's Source opens its email, as a message citation does.
            openSource = { id -> person = null; onOpenCitation(Citation(0, "message", conversationId = id)) })
    }
}

/** The top-level navigation: bottom bar on phones and the folded Fold, rail when unfolded. */
@Composable
fun AppNavigation(tab: Tab, setTab: (Tab) -> Unit, inboxUnread: Int, assistantBadge: Int = 0,
                  assistantName: String = io.github.murtazanasir.mailr.ui.assistantName(), content: @Composable () -> Unit) {
    if (io.github.murtazanasir.mailr.look.LocalLook.current.web) return WebAppNavigation(tab, setTab, inboxUnread, assistantBadge, assistantName, content)
    // With the keyboard up, a bottom bar would sit between the content and the keyboard; hide
    // it (a rail on wider screens stays).
    // Measured height, not WindowInsets.isImeVisible: the latter reads "visible" until the
    // first insets arrive.
    val imeVisible = androidx.compose.foundation.layout.WindowInsets.ime.getBottom(androidx.compose.ui.platform.LocalDensity.current) > 0
    val suiteType = androidx.compose.material3.adaptive.navigationsuite.NavigationSuiteScaffoldDefaults
        .calculateFromAdaptiveInfo(currentWindowAdaptiveInfo()).let {
            if (imeVisible && it == androidx.compose.material3.adaptive.navigationsuite.NavigationSuiteType.NavigationBar)
                androidx.compose.material3.adaptive.navigationsuite.NavigationSuiteType.None else it
        }
    // The Mail badge is drawn only; TalkBack reads the count as the tab's state.
    val unreadState = if (inboxUnread > 0) pluralStringResource(R.plurals.ui_shell_unread, inboxUnread, inboxUnread) else null
    NavigationSuiteScaffold(
        layoutType = suiteType,
        navigationSuiteItems = {
            Tab.entries.forEach { t ->
                item(
                    selected = t == tab,
                    onClick = { setTab(t) },
                    modifier = io.github.murtazanasir.mailr.ui.approvals.waitingSemantics(if (t == Tab.ASSISTANT) assistantBadge else 0)
                        .then(if (t == Tab.MAIL && unreadState != null) Modifier.semantics { stateDescription = unreadState } else Modifier),
                    icon = {
                        val icon = when (t) {
                            Tab.MAIL -> if (t == tab) Icons.Filled.Mail else Icons.Outlined.Mail
                            // The same pictures as the web's bar: a robot for Ambrose, a sticky note for Notes.
                            Tab.ASSISTANT -> if (t == tab) Icons.Filled.SmartToy else Icons.Outlined.SmartToy
                            Tab.CALENDAR -> if (t == tab) Icons.Filled.CalendarMonth else Icons.Outlined.CalendarMonth
                            Tab.TASKS -> if (t == tab) Icons.Filled.TaskAlt else Icons.Outlined.TaskAlt
                            Tab.NOTES -> if (t == tab) Icons.AutoMirrored.Filled.StickyNote2 else Icons.AutoMirrored.Outlined.StickyNote2
                        }
                        if (t == Tab.MAIL && inboxUnread > 0) {
                            androidx.compose.material3.BadgedBox(badge = {
                                androidx.compose.material3.Badge { Text(if (inboxUnread > 99) stringResource(R.string.ui_shell_badge_capped) else "$inboxUnread") }
                            }) { Icon(icon, null) }
                        } else if (t == Tab.ASSISTANT && assistantBadge > 0) {
                            // Agent actions waiting for approval (ApprovalsTracker).
                            io.github.murtazanasir.mailr.ui.approvals.ApprovalsBadged(assistantBadge) { Icon(icon, null) }
                        } else Icon(icon, null)
                    },
                    label = { Text(stringResource(t.label, assistantName)) },
                )
            }
        },
    ) {
        // Clip: a closed ModalNavigationDrawer parks just left of its parent, which is on top of
        // the rail. Without a bottom bar, nothing else keeps content clear of the gesture bar or
        // the unfolded Fold's taskbar.
        val bottom = if (suiteType == androidx.compose.material3.adaptive.navigationsuite.NavigationSuiteType.NavigationBar)
            Modifier else Modifier.windowInsetsPadding(androidx.compose.foundation.layout.WindowInsets.navigationBars.only(androidx.compose.foundation.layout.WindowInsetsSides.Bottom))
        Box(Modifier.fillMaxSize().clipToBounds().then(bottom)) { content() }
    }
}
