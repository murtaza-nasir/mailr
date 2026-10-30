package io.github.murtazanasir.mailr.ui

import io.github.murtazanasir.mailr.util.catching

import android.content.Context
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.createSavedStateHandle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.MailrApp
import io.github.murtazanasir.mailr.data.api.acceptInvite
import io.github.murtazanasir.mailr.data.repo.MailView
import io.github.murtazanasir.mailr.ui.assistant.AssistantViewModel
import io.github.murtazanasir.mailr.ui.compose.ComposeArgs
import io.github.murtazanasir.mailr.ui.mail.MailboxViewModel
import io.github.murtazanasir.mailr.ui.mail.UiMessage
import io.github.murtazanasir.mailr.data.api.userMessage
import io.github.murtazanasir.mailr.ui.calendar.EventRequest
import io.github.murtazanasir.mailr.ui.calendar.TASK_VIEW_PREFIX
import io.github.murtazanasir.mailr.ui.search.SearchScreen
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import io.github.murtazanasir.mailr.ui.recordings.LocalRecordings
import io.github.murtazanasir.mailr.ui.recordings.RecordingSheets
import io.github.murtazanasir.mailr.ui.recordings.Recordings
import androidx.compose.runtime.CompositionLocalProvider
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import io.github.murtazanasir.mailr.R

val Context.graph: AppGraph get() = (applicationContext as MailrApp).graph

@Composable
fun MailrRoot(deepLinks: StateFlow<Destination?>, consume: () -> Unit,
              setupLinks: StateFlow<String?> = kotlinx.coroutines.flow.MutableStateFlow(null), consumeSetup: () -> Unit = {}) {
    val g = LocalContext.current.graph
    val prefs by g.settings.prefs.collectAsStateWithLifecycle(io.github.murtazanasir.mailr.data.settings.Prefs())
    val loaded by g.settings.prefs.collectAsStateWithLifecycle(null)
    val session by g.sessions.session.collectAsStateWithLifecycle()
    // The session ended on the server (401): sign-in again, with the cached mail gone.
    // Queued changes and local drafts wait for the next sign-in.
    LaunchedEffect(Unit) { g.client.signedOut.collect { g.sessionExpired() } }
    MailrTheme(prefs.theme, prefs.dynamicColor, prefs.webColours(), prefs.webLook) {
        Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.surface) {
            val setupDue = loaded?.let { !it.phoneSetupDone && !it.notificationsAsked } == true
            when {
                session == null -> OnboardingRoute(g, setupLinks, consumeSetup)
                // The short setup after the first sign-in (ui/onboarding/PhoneSetup.kt), once.
                setupDue -> PhoneSetupRoute(g)
                // While the server refuses this version (426): the update banner above every screen.
                else -> io.github.murtazanasir.mailr.ui.update.UpdateRequiredFrame(g.updates) { Home(g, deepLinks, consume) }
            }
        }
    }
}

@Composable
private fun Home(g: AppGraph, deepLinks: StateFlow<Destination?>, consume: () -> Unit) {
    val nav = rememberNavController()
    val mailbox: MailboxViewModel = viewModel { MailboxViewModel(g) }
    val assistant: AssistantViewModel = viewModel { AssistantViewModel(g, createSavedStateHandle()) }
    val snackbar = remember { SnackbarHostState() }
    var tab by rememberSaveable { mutableStateOf(Tab.MAIL) }
    var openTarget by remember { mutableStateOf<Destination.Conversation?>(null) }
    var eventRequest by remember { mutableStateOf<EventRequest?>(null) }
    var taskRequest by remember { mutableStateOf<String?>(null) }
    var notesRequest by remember { mutableStateOf<Destination.Notes?>(null) }
    val scope = rememberCoroutineScope()
    val ctx = LocalContext.current
    NotificationRationale(g)
    val approvals = remember { io.github.murtazanasir.mailr.ui.approvals.ApprovalsTracker.of(g) }

    fun show(m: UiMessage) = scope.launch {
        val r = snackbar.showSnackbar(m.text, m.actionLabel, withDismissAction = m.actionLabel == null,
            duration = if (m.actionLabel != null) SnackbarDuration.Long else SnackbarDuration.Short)
        if (r == SnackbarResult.ActionPerformed) m.action?.invoke()
    }

    LaunchedEffect(Unit) { mailbox.messages.collect { show(it) } }
    // Recordings (Speakr): only while the switch is on and the server has a Speakr connector. While
    // off, LocalRecordings stays null and nothing asks /speakr for anything.
    val homePrefs by g.settings.prefs.collectAsStateWithLifecycle(io.github.murtazanasir.mailr.data.settings.Prefs())
    val speakrOn = io.github.murtazanasir.mailr.ui.recordings.rememberSpeakrOn(homePrefs.recordings, g.speakr)
    val recordings = remember(speakrOn) {
        if (!speakrOn) null else Recordings(g.speakr, scope,
            openUrl = { openUrl(ctx, it) },
            openDraft = { id -> nav.openCompose(ComposeArgs(draftId = id)) },
            // ACT agent.ask_about @android.reader @android.calendar @android.tasks @android.recording: the ask capture with the recording attached; Send opens the chat.
            ask = { a -> ctx.startActivity(io.github.murtazanasir.mailr.ui.ask.AskIntents.capture(ctx, a, from = "recording")) },
            openLink = { l ->
                when (l.kind) {
                    "event" -> { nav.popBackStack(Routes.MAIN, false); tab = Tab.CALENDAR; eventRequest = EventRequest(l.target, l.occurrence) }
                    "task" -> { nav.popBackStack(Routes.MAIN, false); tab = Tab.TASKS; taskRequest = l.target }
                    else -> (l.conversationId ?: l.target.toLongOrNull())?.let {
                        nav.popBackStack(Routes.MAIN, false); tab = Tab.MAIL; openTarget = Destination.Conversation(it)
                    }
                }
            },
            notify = { text, undo -> show(UiMessage(text, undo?.let { ctx.getString(R.string.ui_shell_undo) }, undo?.let { u -> { u() } })) },
            tasksAdded = { g.syncCalendarSoon(force = true) },
            record = { ev -> nav.navigate(Routes.record(ev?.uid, ev?.occurrence)) })
    }
    LaunchedEffect(Unit) {
        g.live.events.collect { ev ->
            when (ev.type) {
                "approval", "outbox" -> {
                    val status = (ev.data["status"] as? kotlinx.serialization.json.JsonPrimitive)?.content
                    // A new approval gets the one-time banner below (ApprovalArrivalOverlay), not a snackbar.
                    if (status == "failed" || (status == "uncertain" && (ev.data["title"] as? kotlinx.serialization.json.JsonPrimitive)?.isString == true)) show(UiMessage(ctx.getString(R.string.ui_shell_message_not_sent), ctx.getString(R.string.ui_shell_outbox)) {
                        tab = Tab.MAIL; nav.popBackStack(Routes.MAIN, false); approvals.outboxRequest.value = true })
                }
            }
        }
    }
    // ACT agent.ask_about, ACT agent.dictate @android.ask: an instruction from the ask capture (ui/ask) opens the
    // Assistant tab about its item and is sent there, so the reply and any approval show in the chat.
    LaunchedEffect(Unit) {
        g.pendingAsk.collect { req ->
            if (req == null) return@collect
            g.pendingAsk.value = null
            nav.popBackStack(Routes.MAIN, false)
            assistant.ask(req.about, req.text)
            tab = Tab.ASSISTANT
        }
    }
    // ACT household.join @android.setup: a household invite from the setup link, accepted once signed in to its server.
    LaunchedEffect(Unit) {
        val pending = io.github.murtazanasir.mailr.data.auth.PendingInvite(ctx)
        val token = pending.forServer(g.sessions.server) ?: return@LaunchedEffect
        try {
            g.api.acceptInvite(token)
            show(UiMessage(ctx.getString(R.string.ui_shell_welcome)))
            catching { g.mail.refreshMe() }
        } catch (e: kotlinx.coroutines.CancellationException) {
            throw e
        } catch (e: io.github.murtazanasir.mailr.data.api.ApiException) {
            if (e.code in setOf(403, 404, 410)) show(UiMessage(e.userMessage()))
        } catch (_: java.io.IOException) {
            return@LaunchedEffect  // offline: the next start tries again
        }
        pending.clear()
    }
    // Whether the server can transcribe dictation (GET /me features.transcribe), for captures opened later.
    val meNow by g.mail.me.collectAsStateWithLifecycle()
    LaunchedEffect(meNow?.features?.transcribe) {
        val t = meNow?.features?.transcribe ?: return@LaunchedEffect
        if (t != homePrefs.serverDictation) g.settings.update { it.copy(serverDictation = t) }
    }
    // Whether the assistant is on for this account (GET /me access): the tab and captures opened later.
    val assistantBlocked = meNow?.let { io.github.murtazanasir.mailr.data.api.AssistantGate.blocked(it) }
    LaunchedEffect(meNow == null, assistantBlocked) {
        if (meNow == null) return@LaunchedEffect
        assistant.setAccess(assistantBlocked)
        val cached = assistantBlocked.orEmpty()
        if (cached != homePrefs.assistantBlocked) g.settings.update { it.copy(assistantBlocked = cached) }
    }
    // Deep links (notifications, citation links, mailto:).
    val link by deepLinks.collectAsState()
    LaunchedEffect(link) {
        when (val d = link ?: return@LaunchedEffect) {
            is Destination.Conversation -> { tab = Tab.MAIL; nav.popBackStack(Routes.MAIN, false); openTarget = d }
            is Destination.Compose -> nav.openCompose(d.args)
            Destination.Approvals -> nav.navigate(Routes.APPROVALS) { launchSingleTop = true }
            is Destination.ApprovalItem -> { approvals.focus.value = d.key; nav.navigate(Routes.APPROVALS) { launchSingleTop = true } }
            Destination.Record -> nav.navigate(Routes.record(null, null)) { launchSingleTop = true }
            Destination.Inbox -> { tab = Tab.MAIL; mailbox.open(MailView()) }
            Destination.Scheduled -> { tab = Tab.MAIL; nav.popBackStack(Routes.MAIN, false); mailbox.open(MailView("scheduled", title = ctx.getString(R.string.ui_shell_scheduled))) }
            is Destination.Event -> {
                nav.popBackStack(Routes.MAIN, false)
                tab = Tab.CALENDAR
                if (d.uid != null || d.view != null || d.date != null) eventRequest = EventRequest(d.uid, d.occurrence, d.view, d.date, d.prep)
            }
            is Destination.Task -> {
                nav.popBackStack(Routes.MAIN, false)
                tab = Tab.TASKS
                taskRequest = d.uid ?: d.view?.let { TASK_VIEW_PREFIX + it }
            }
            is Destination.Notes -> {
                nav.popBackStack(Routes.MAIN, false)
                tab = Tab.NOTES
                notesRequest = d.takeIf { it.id.isNotBlank() }
            }
        }
        consume()
    }

    CompositionLocalProvider(LocalRecordings provides recordings) {
    NavHost(nav, startDestination = Routes.MAIN) {
        composable(Routes.MAIN) {
            MainShell(g, nav, mailbox, assistant, snackbar, tab, { tab = it }, openTarget, { openTarget = null },
                openConversation = { d -> tab = Tab.MAIL; openTarget = d },
                eventRequest = eventRequest, clearEventRequest = { eventRequest = null },
                taskRequest = taskRequest, clearTaskRequest = { taskRequest = null }, requestTask = { taskRequest = it },
                notesRequest = notesRequest, clearNotesRequest = { notesRequest = null },
                openNotes = { d -> notesRequest = d; tab = Tab.NOTES },
                requestEvent = { eventRequest = it })
        }
        composable(Routes.COMPOSE) { entry ->
            val a = entry.arguments
            fun s(k: String) = a?.getString(k)?.takeIf { it.isNotBlank() }
            val args = ComposeArgs(s("draft")?.toLongOrNull(), s("reply")?.toLongOrNull(), s("mode"), s("restore"),
                s("to"), s("subject"), s("body"), s("cc"), s("bcc"))
            ComposeRoute(g, args, entry.id, onDone = { result ->
                composeDone(g, nav, result) { show(it) }
            })
        }
        composable(Routes.SEARCH) {
            val vm: SearchViewModel = viewModel { SearchViewModel(g) }
            val prefs by g.settings.prefs.collectAsStateWithLifecycle(io.github.murtazanasir.mailr.data.settings.Prefs())
            val people by vm.people.collectAsStateWithLifecycle()
            val shownView by mailbox.view.collectAsStateWithLifecycle()
            SearchScreen(shownView.q.orEmpty(), prefs.recentSearches, people, vm::query, onSearch = { q ->
                vm.remember(q)
                mailbox.open(MailView("all", q = q, title = ctx.getString(R.string.ui_shell_search_results)))
                nav.popBackStack()
            }, onBack = { nav.popBackStack() })
        }
        composable(Routes.SETTINGS) { SettingsRoute(g, nav, scope, assistantName = assistant.state.collectAsStateWithLifecycle().value.name) { show(it) } }
        // The Memory page: Ambrose's ⋮ menu and Settings → Ambrose open it.
        composable(Routes.MEMORY) {
            io.github.murtazanasir.mailr.ui.memory.MemoryRoute(g, assistant.state.collectAsStateWithLifecycle().value.name,
                onBack = { nav.popBackStack() },
                openConversation = { id -> nav.popBackStack(Routes.MAIN, false); tab = Tab.MAIL; openTarget = Destination.Conversation(id) },
                // A memory's "From a chat": the chat in the Ambrose tab, at the message it came from.
                openChat = { sid, mid ->
                    assistant.openChat(sid, mid).also { if (it == null) { nav.popBackStack(Routes.MAIN, false); tab = Tab.ASSISTANT } }
                })
        }
        // Settings → Ambrose → Trusted addresses: identity only.
        composable(Routes.TRUSTED) {
            io.github.murtazanasir.mailr.ui.settings.TrustedRoute(g, assistant.state.collectAsStateWithLifecycle().value.name,
                openOrders = { nav.navigate(Routes.ORDERS) { launchSingleTop = true } }) { nav.popBackStack() }
        }
        // Settings → Ambrose → Standing orders.
        composable(Routes.ORDERS) {
            io.github.murtazanasir.mailr.ui.settings.OrdersRoute(g, assistant.state.collectAsStateWithLifecycle().value.name) { nav.popBackStack() }
        }
        // Settings → Ambrose → Sweep.
        composable(Routes.SWEEP) {
            io.github.murtazanasir.mailr.ui.settings.SweepRoute(g, assistant.state.collectAsStateWithLifecycle().value.name) { nav.popBackStack() }
        }
        // Settings → Activity: read-only.
        // Settings → Calendar → Phone calendars.
        composable(Routes.PHONE_CALENDARS) { io.github.murtazanasir.mailr.ui.settings.PhoneCalendarsRoute(g) { nav.popBackStack() } }
        composable(Routes.ACTIVITY) { io.github.murtazanasir.mailr.ui.settings.ActivityRoute(g) { nav.popBackStack() } }
        // ACT recording.record @android.recording: New recording (Recordings (Speakr) on; a recording in progress opens it too).
        composable(Routes.RECORD) { entry ->
            fun s(k: String) = entry.arguments?.getString(k)?.takeIf { it.isNotBlank() }
            NewRecordingRoute(g, recordings, s("uid"), s("occ"), onBack = { nav.popBackStack() })
        }
        composable(Routes.SCREENER) {
            ScreenerRoute(g, nav, openConversation = { id ->
                nav.popBackStack(Routes.MAIN, false); tab = Tab.MAIL; openTarget = Destination.Conversation(id)
            })
        }
        composable(Routes.APPROVALS) {
            ApprovalsRoute(approvals, nav, openOutbox = {
                tab = Tab.MAIL; nav.popBackStack(Routes.MAIN, false); approvals.outboxRequest.value = true
            })
        }
    }
    // A new approval: announced once at the top, except on the Approvals screen and over the chat that asked.
    val route = io.github.murtazanasir.mailr.ui.approvals.currentRoute(nav)
    val chat by assistant.state.collectAsStateWithLifecycle()
    io.github.murtazanasir.mailr.ui.approvals.ApprovalArrivalOverlay(approvals,
        hide = { a -> route == Routes.APPROVALS || (route == Routes.MAIN && tab == Tab.ASSISTANT && a.chatSessionId != null && a.chatSessionId == chat.sessionId) },
        onReview = { a ->
            approvals.focus.value = io.github.murtazanasir.mailr.data.sync.NotifyDecisions.approvalKey(a.approval)
            nav.navigate(Routes.APPROVALS) { launchSingleTop = true }
        })
    recordings?.let { RecordingSheets(it) }
    }
}
