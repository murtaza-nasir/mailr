package io.github.murtazanasir.mailr.ui

import io.github.murtazanasir.mailr.util.catching

// The Mail tab and its screens: the list and reader (MailPane), a conversation, and the composer.

import io.github.murtazanasir.mailr.data.agent.AskAbout
import android.net.Uri
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.MarkEmailRead
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.adaptive.currentWindowAdaptiveInfo
import androidx.compose.material3.adaptive.layout.AnimatedPane
import androidx.compose.material3.adaptive.layout.ListDetailPaneScaffold
import androidx.compose.material3.adaptive.layout.ListDetailPaneScaffoldRole
import androidx.compose.material3.adaptive.layout.PaneAdaptedValue
import androidx.compose.material3.adaptive.navigation.rememberListDetailPaneScaffoldNavigator
import androidx.compose.material3.rememberDrawerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.compose.runtime.saveable.rememberSaveable
import io.github.murtazanasir.mailr.ui.memory.isVip
import androidx.lifecycle.compose.LifecycleResumeEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavHostController
import androidx.paging.compose.collectAsLazyPagingItems
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.data.api.Citation
import io.github.murtazanasir.mailr.data.api.findNotes
import io.github.murtazanasir.mailr.data.api.activeOutbox
import io.github.murtazanasir.mailr.data.api.retrySend
import io.github.murtazanasir.mailr.ui.assistant.AssistantViewModel
import io.github.murtazanasir.mailr.ui.components.EmptyState
import io.github.murtazanasir.mailr.ui.components.LabelPickerSheet
import io.github.murtazanasir.mailr.ui.mail.CategorySheet
import io.github.murtazanasir.mailr.ui.mail.ShowInTabSheet
import io.github.murtazanasir.mailr.ui.mail.TabMenus
import io.github.murtazanasir.mailr.ui.components.SnoozeSheet
import io.github.murtazanasir.mailr.ui.compose.ComposeArgs
import io.github.murtazanasir.mailr.ui.compose.ComposeCallbacks
import io.github.murtazanasir.mailr.ui.compose.ComposeResult
import androidx.lifecycle.createSavedStateHandle
import io.github.murtazanasir.mailr.ui.compose.ComposeScreen
import io.github.murtazanasir.mailr.ui.compose.ComposeViewModel
import io.github.murtazanasir.mailr.ui.compose.RestoreStore
import io.github.murtazanasir.mailr.ui.conversation.ConversationActions
import io.github.murtazanasir.mailr.ui.conversation.ConversationScreen
import io.github.murtazanasir.mailr.ui.conversation.ConversationViewModel
import io.github.murtazanasir.mailr.ui.conversation.FromEmailSheet
import io.github.murtazanasir.mailr.ui.mail.InboxTabs
import io.github.murtazanasir.mailr.ui.mail.ListActions
import io.github.murtazanasir.mailr.ui.mail.DrawerFavorites
import io.github.murtazanasir.mailr.ui.mail.MailDrawer
import io.github.murtazanasir.mailr.ui.mail.MailListPane
import io.github.murtazanasir.mailr.ui.mail.MailboxViewModel
import io.github.murtazanasir.mailr.data.api.userMessage
import io.github.murtazanasir.mailr.data.api.screener
import io.github.murtazanasir.mailr.ui.recordings.LocalRecordings
import kotlinx.coroutines.launch
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText

@Composable
internal fun MailPane(
    g: AppGraph,
    nav: NavHostController,
    vm: MailboxViewModel,
    assistant: AssistantViewModel,
    snackbar: SnackbarHostState,
    openTarget: Destination.Conversation?,
    clearTarget: () -> Unit,
    setTab: (Tab) -> Unit,
    /** A note made from an email opens in the Notes tab. */
    openNote: (String) -> Unit = {},
) {
    val view by vm.view.collectAsStateWithLifecycle()
    val labels by vm.labels.collectAsStateWithLifecycle()
    val sections by vm.sections.collectAsStateWithLifecycle()
    val sectionTitle by vm.sectionTitle.collectAsStateWithLifecycle()
    val focusedSection by vm.focusedSection.collectAsStateWithLifecycle()
    val mainSection by vm.mainSection.collectAsStateWithLifecycle()
    val selection by vm.selection.collectAsStateWithLifecycle()
    val prefs by vm.prefs.collectAsStateWithLifecycle()
    val refreshing by vm.refreshing.collectAsStateWithLifecycle()
    val pendingChanges by vm.pendingChanges.collectAsStateWithLifecycle()
    val offlineSearches by g.mail.offlineResults.collectAsStateWithLifecycle()
    val pendingOpen by vm.pendingOpen.collectAsStateWithLifecycle()
    val queuePaused by vm.queuePaused.collectAsStateWithLifecycle()
    val confirmDelete by vm.confirmDelete.collectAsStateWithLifecycle()
    val inboxLayout by vm.layout.collectAsStateWithLifecycle()
    val tabCounts by vm.tabCounts.collectAsStateWithLifecycle()
    val tabIndicators by vm.tabIndicators.collectAsStateWithLifecycle()
    val inboxCount by g.mail.inboxCount.collectAsStateWithLifecycle()
    val session by g.sessions.session.collectAsStateWithLifecycle()
    val items = vm.conversations.collectAsLazyPagingItems()
    val drawer = rememberDrawerState(DrawerValue.Closed)
    // Favorites: read again on resume and when the drawer opens, so a change made
    // on the web shows; their Undo shows inside the drawer.
    val favorites by vm.favorites.collectAsStateWithLifecycle()
    val accountFolders by vm.accountFolders.collectAsStateWithLifecycle()
    val contextLabel by vm.contextLabel.collectAsStateWithLifecycle()
    val drawerSnackbar = remember { SnackbarHostState() }
    val meNow = g.mail.me.collectAsStateWithLifecycle().value
    val meSetup = meNow?.setup
    LifecycleResumeEffect(vm) { vm.refreshMe(); onPauseOrDispose {} }
    LaunchedEffect(drawer.targetValue) { if (drawer.targetValue == DrawerValue.Open) vm.refreshMe() }
    LaunchedEffect(vm) {
        vm.favoriteMessages.collect { m ->
            launch {
                val r = drawerSnackbar.showSnackbar(m.text, m.actionLabel, duration = SnackbarDuration.Short)
                if (r == SnackbarResult.ActionPerformed) m.action?.invoke()
            }
        }
    }
    val scope = rememberCoroutineScope()
    val navigator = rememberListDetailPaneScaffoldNavigator<String>()
    var snoozeFor by remember { mutableStateOf<List<Long>?>(null) }
    var labelFor by remember { mutableStateOf<List<Long>?>(null) }
    // Show in tab and Category (TabMenus.kt): the conversations each sheet acts on.
    var tabFor by remember { mutableStateOf<List<Long>?>(null) }
    var categoryFor by remember { mutableStateOf<List<Long>?>(null) }
    var categoryHint by remember { mutableStateOf<String?>(null) }
    var customSnooze by remember { mutableStateOf<List<Long>?>(null) }
    val tracker = io.github.murtazanasir.mailr.ui.approvals.ApprovalsTracker.of(g)
    val waiting by tracker.pending.collectAsStateWithLifecycle()
    val waitingCounts by tracker.counts.collectAsStateWithLifecycle()
    val approvals = waiting?.size ?: 0
    // The Screener entry: shown while the Screener is on, with the senders waiting (read when the drawer opens).
    var screenerWaiting by remember { mutableStateOf<Int?>(null) }
    LaunchedEffect(drawer.isOpen) {
        if (drawer.isOpen) catching { g.api.screener() }.onSuccess { screenerWaiting = if (it.enabled) it.count else null }
    }
    // The mail accounts and their own folders' numbers (GET /counts; ui/mail/MailAccounts.kt): read at
    // the start, when the drawer opens and after a refresh.
    var counts by remember { mutableStateOf(io.github.murtazanasir.mailr.data.api.SidebarCounts()) }
    suspend fun loadCounts() {
        try { counts = g.api.counts() } catch (e: kotlinx.coroutines.CancellationException) { throw e } catch (_: Exception) { }
    }
    LaunchedEffect(drawer.isOpen) { loadCounts() }
    // The Outbox (ACT draft.outbox @android.drawer): read when the drawer opens; listed only while not empty.
    var outboxRows by remember { mutableStateOf<List<io.github.murtazanasir.mailr.data.api.OutboxRow>>(emptyList()) }
    var outboxOpen by remember { mutableStateOf(false) }
    suspend fun loadOutbox() {
        try { outboxRows = g.api.activeOutbox() } catch (e: kotlinx.coroutines.CancellationException) { throw e } catch (_: Exception) { }
    }
    LaunchedEffect(drawer.isOpen) { if (drawer.isOpen) loadOutbox() }
    // Open Outbox from "Waiting for you" (Not sent) or the "couldn't be sent" snackbar.
    val outboxAsked by tracker.outboxRequest.collectAsStateWithLifecycle()
    LaunchedEffect(outboxAsked) {
        if (outboxAsked) {
            tracker.outboxRequest.value = false
            loadOutbox()
            outboxOpen = true
        }
    }
    val recordings = LocalRecordings.current
    val ctx = LocalContext.current

    LaunchedEffect(Unit) { vm.refresh.collect { items.refresh() } }
    fun openConversation(id: Long, msg: Long? = null, quote: String? = null) {
        navigator.navigateTo(ListDetailPaneScaffoldRole.Detail, listOf(id.toString(), msg?.toString().orEmpty(), quote.orEmpty()).joinToString("\u0001"))
    }
    LaunchedEffect(openTarget) {
        openTarget?.let { openConversation(it.id, it.messageId, it.quote); clearTarget() }
    }

    BackHandler(navigator.canNavigateBack()) { navigator.navigateBack() }
    BackHandler(selection.isNotEmpty()) { vm.clearSelection() }
    BackHandler(drawer.isOpen) { scope.launch { drawer.close() } }
    // A section shown alone: Back returns to all of the tab's sections.
    BackHandler(focusedSection != null && selection.isEmpty() && !drawer.isOpen && !navigator.canNavigateBack()) { vm.unfocusSection() }

    val listHidden = navigator.scaffoldValue[ListDetailPaneScaffoldRole.List] == PaneAdaptedValue.Hidden
    val activeKey = navigator.currentDestination?.content
    val activeId = activeKey?.substringBefore('\u0001')?.toLongOrNull()

    // Rows show their account's dot in the unified lists, never inside an account's own folder.
    androidx.compose.runtime.CompositionLocalProvider(
        io.github.murtazanasir.mailr.ui.mail.LocalMailAccounts provides if (view.view.startsWith("acct:")) emptyList() else counts.accounts,
    ) {
    ModalNavigationDrawer(
        drawerState = drawer,
        gesturesEnabled = !listHidden,
        drawerContent = {
            MailDrawer(labels, view, inboxLayout, tabCounts, session?.email.orEmpty(), approvals,
                // A pick closes the drawer at once: while it slid shut its scrim took the next tap, so a tab
                // tapped right after the drawer did not switch.
                // The drawer's Inbox: every section again (a section shown alone ends), as on the web.
                onOpen = { v -> if (v.view == "inbox" && v.q.isNullOrBlank()) vm.unfocusSection(); vm.open(v); scope.launch { drawer.snapTo(DrawerValue.Closed) } },
                onApprovals = { scope.launch { drawer.close() }; nav.navigate(Routes.APPROVALS) },
                screener = screenerWaiting,
                onScreener = { scope.launch { drawer.close() }; nav.navigate(Routes.SCREENER) },
                onSettings = { scope.launch { drawer.close() }; nav.navigate(Routes.SETTINGS) },
                favorites = DrawerFavorites(favorites, vm::addFavorite, vm::removeFavorite, vm::moveFavorite),
                snackbar = drawerSnackbar, inboxCount = inboxCount,
                outbox = io.github.murtazanasir.mailr.ui.mail.OutboxCount.of(outboxRows),
                onOutbox = { scope.launch { drawer.close() }; outboxOpen = true },
                accounts = counts.accounts, accountCounts = counts.views,
                accountPins = io.github.murtazanasir.mailr.ui.mail.DrawerAccountPins(accountFolders, vm::pinFolder, vm::unpinFolder, vm::movePinnedFolder, vm::setMoreFoldersOpen, vm::createFolder))
        },
    ) {
        ListDetailPaneScaffold(
            directive = navigator.scaffoldDirective,
            value = navigator.scaffoldValue,
            listPane = {
                AnimatedPane {
                    MailListPane(
                        view = view, items = items, sections = sections, sectionTitle = sectionTitle, labels = labels,
                        selection = selection, activeId = if (listHidden) null else activeId, prefs = prefs,
                        accountName = session?.name.orEmpty(), accountEmail = session?.email.orEmpty(),
                        refreshing = refreshing, pendingOps = pendingChanges.count { !it.failed },
                        pendingFailed = pendingChanges.count { it.failed },
                        snackbar = snackbar, showMenuButton = true, pendingApprovals = approvals, waiting = waitingCounts,
                        contextLabelId = contextLabel,
                        archiveRemovesLabel = io.github.murtazanasir.mailr.ui.mail.archiveRemovesLabel(meNow?.settings),
                        setup = meSetup,
                        openSetup = { meSetup?.url?.takeIf { it.isNotBlank() }?.let { openUrl(ctx, it) } },
                        offlineResults = !view.q.isNullOrBlank() && view.key in offlineSearches,
                        sectionControls = io.github.murtazanasir.mailr.ui.mail.SectionControls(focusedSection, mainSection,
                            show = vm::focusSection, back = vm::unfocusSection, grow = vm::toggleGrown),
                        tabs = inboxLayout?.takeIf { it.tabs.size > 1 && view.view == "inbox" && view.q.isNullOrBlank() && view.category == null }?.let { l ->
                            // The open tab tapped again: all of its sections.
                            { InboxTabs(l, view, tabCounts, tabIndicators) { v -> if (v.tab == view.tab) vm.unfocusSection(); vm.open(v) } }
                        },
                        actions = ListActions(
                            openDrawer = { scope.launch { drawer.open() } },
                            openSearch = { nav.navigate(Routes.SEARCH) },
                            openAccount = { nav.navigate(Routes.SETTINGS) },
                            compose = { nav.openCompose(ComposeArgs()) },
                            open = { c ->
                                if (view.view == "drafts" && c.draftCount > 0 && c.messageCount <= 1) openDraftOf(g, nav, c.id)
                                else openConversation(c.id)
                            },
                            toggle = vm::toggle,
                            clearSelection = vm::clearSelection,
                            selectAll = vm::selectAll,
                            act = { ids, a -> vm.act(ids, a) },
                            star = { c -> vm.act(listOf(c.id), if (c.starred) "unstar" else "star") },
                            swipe = { c, a -> vm.swipe(c, a) { snoozeFor = listOf(c.id) } },
                            snoozeSelected = { snoozeFor = selection.toList() },
                            labelSelected = { labelFor = selection.toList() },
                            refresh = vm::pullToRefresh,
                            openApprovals = { nav.navigate(Routes.APPROVALS) { launchSingleTop = true } },
                            openPending = { vm.showPending(true) },
                            deleteForever = vm::askDeleteForever,
                            showInTabSelected = if (TabMenus.offersShowInTab(inboxLayout?.tabs?.size ?: 0)) ({ tabFor = selection.toList() }) else null,
                            categorySelected = { categoryHint = null; categoryFor = selection.toList() },
                        ),
                    )
                }
            },
            detailPane = {
                AnimatedPane {
                    val key = navigator.currentDestination?.content
                    if (key == null) {
                        EmptyState(Icons.Outlined.MarkEmailRead, stringResource(R.string.ui_shell_no_conversation), stringResource(R.string.ui_shell_pick_one))
                    } else {
                        val parts = key.split('\u0001')
                        val id = parts[0].toLong()
                        ConversationRoute(g, nav, id, parts.getOrNull(1)?.toLongOrNull(), parts.getOrNull(2)?.ifBlank { null },
                            listView = view.view,
                            showBack = listHidden,
                            onBack = { navigator.navigateBack() },
                            onSnooze = { snoozeFor = listOf(id) },
                            onLabels = { labelFor = listOf(id) },
                            onShowInTab = if (TabMenus.offersShowInTab(inboxLayout?.tabs?.size ?: 0)) ({ tabFor = listOf(id) }) else null,
                            onCategory = { cat -> categoryHint = cat ?: "primary"; categoryFor = listOf(id) },
                            onAsk = { title -> assistant.setContext(id, title); setTab(Tab.ASSISTANT) },
                            closed = { if (navigator.canNavigateBack()) navigator.navigateBack() },
                            openNote = openNote,
                            onOpenCitation = { c ->
                                if (!openRecordingCitation(recordings, ctx, c) && !openWebCitation(ctx, c)) openTargetFrom(c)?.let { d -> openConversation(d.id, d.messageId, d.quote) }
                            })
                    }
                }
            },
        )
    }
    }

    if (outboxOpen) io.github.murtazanasir.mailr.ui.mail.OutboxSheet(outboxRows, onDismiss = { outboxOpen = false },
        onRetry = { o -> scope.launch {
            val text = try { g.api.retrySend(o.id); AppText.get(R.string.ui_shell_sending_again) } catch (e: kotlinx.coroutines.CancellationException) { throw e }
                catch (e: Exception) { e.userMessage() }
            launch { snackbar.showSnackbar(text) }
            loadOutbox()
        } },
        onOpen = { o -> outboxOpen = false; o.conversationId?.let { openConversation(it) } })
    // The list's and the reader's Snooze / Label sheets: ACT mail.snooze @android.reader, ACT mail.label @android.reader.
    snoozeFor?.let { ids ->
        SnoozeSheet(onDismiss = { snoozeFor = null }, onPick = { at -> vm.snooze(ids, at); snoozeFor = null },
            onCustom = { customSnooze = ids; snoozeFor = null })
    }
    customSnooze?.let { ids -> DateTimeDialog(onDismiss = { customSnooze = null }) { at -> vm.snooze(ids, at); customSnooze = null } }
    // ACT mail.delete_forever @android.list from the list (selection bar, the delete swipe in Trash): confirmed first.
    confirmDelete?.let { ids -> io.github.murtazanasir.mailr.ui.mail.DeleteForeverDialog(ids.size, vm::cancelDeleteForever, vm::confirmDeleteForever) }
    // Pending changes: the queue's waiting and failed changes and the drafts only this phone has.
    if (pendingOpen) io.github.murtazanasir.mailr.ui.mail.PendingChangesSheet(pendingChanges, queuePaused, io.github.murtazanasir.mailr.ui.mail.PendingCallbacks(
        retry = vm::retryPending, discard = vm::discardPending, discardDraft = vm::discardLocalDraft,
        openDraft = { id ->
            vm.showPending(false)
            scope.launch {
                val row = g.drafts.get(id) ?: return@launch
                nav.openCompose(ComposeArgs(restoreKey = RestoreStore.put(g.drafts.payloadOf(row), row.draftId, id)), row.draftId)
            }
        },
        dismiss = { vm.showPending(false) },
    ))
    tabFor?.let { ids ->
        val choices by produceState<io.github.murtazanasir.mailr.data.api.ShowInTabChoices?>(null, ids) {
            value = catching { g.mail.showInTabChoices(ids) }.getOrNull()
        }
        ShowInTabSheet(choices, onDismiss = { tabFor = null }) { t -> vm.showInTab(ids, t); tabFor = null }
    }
    categoryFor?.let { ids ->
        val current = remember(ids, categoryHint) {
            categoryHint ?: TabMenus.sharedCategory((items.itemSnapshotList.items + sections.flatMap { it.items })
                .filter { it.id in ids }.distinctBy { it.id }.map { it.category })
        }
        CategorySheet(current, onDismiss = { categoryFor = null }) { c -> vm.setCategory(ids, c); categoryFor = null }
    }
    labelFor?.let { ids ->
        val current = remember(ids) {
            items.itemSnapshotList.items.filter { it.id in ids }.flatMap { it.labelIds }.toSet()
        }
        LabelPickerSheet(labels, current, onDismiss = { labelFor = null }) { add, remove ->
            if (add.isNotEmpty()) vm.act(ids, "add_labels", add)
            if (remove.isNotEmpty()) vm.act(ids, "remove_labels", remove)
            labelFor = null
        }
    }
}

/**
 * Opens the composer; a draft that is open already (OpenEditors) brings its composer forward
 * instead of stacking a second one. [draftId]: the draft the composer will hold (a restored copy
 * names it apart from the args).
 */
// currentBackStack is the only way to see every open editor; it is restricted to Navigation's own group
// and is checked again at each Navigation update (tests: ReplyResumesDraftTest).
@android.annotation.SuppressLint("RestrictedApi")
internal fun NavHostController.openCompose(a: ComposeArgs, draftId: Long? = a.draftId) {
    val target = io.github.murtazanasir.mailr.data.live.OpenEditors.target(
        draftId?.let { io.github.murtazanasir.mailr.data.live.OpenEditors.key("draft", it) }, currentBackStack.value.map { it.id })
    if (target != null) {
        while (currentBackStackEntry?.id != target && popBackStack()) Unit
        return
    }
    navigate(Routes.compose(a))
}

internal fun openDraftOf(g: AppGraph, nav: NavHostController, conversationId: Long) {
    g.scope.launch {
        val d = catching { g.mail.refreshConversation(conversationId) }.getOrNull()
        val draft = d?.messages?.lastOrNull { it.draft } ?: return@launch
        kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.Main) {
            nav.openCompose(ComposeArgs(draftId = draft.id))
        }
    }
}

@Composable
internal fun ConversationRoute(
    g: AppGraph,
    nav: NavHostController,
    id: Long,
    focus: Long?,
    quote: String?,
    showBack: Boolean,
    onBack: () -> Unit,
    onSnooze: () -> Unit,
    onLabels: () -> Unit,
    onAsk: (String?) -> Unit,
    onShowInTab: (() -> Unit)? = null,
    onCategory: (String?) -> Unit = {},
    closed: () -> Unit,
    onOpenCitation: (Citation) -> Unit = {},
    listView: String = "inbox",
    openNote: (String) -> Unit = {},
) {
    val vm: ConversationViewModel = viewModel(key = "conv-$id-$focus-$quote") { ConversationViewModel(g, id, focus, quote) }
    // Reader parity: invitations, suggested replies, send states; Move to and Link to a note sheets.
    val invites by vm.extras.invites.collectAsStateWithLifecycle()
    val inviteBusy by vm.extras.busy.collectAsStateWithLifecycle()
    val replies by vm.extras.replies.collectAsStateWithLifecycle()
    val outboxRows by vm.extras.outbox.collectAsStateWithLifecycle()
    val translations by vm.extras.translations.collectAsStateWithLifecycle()
    val attachedEmails by vm.extras.attached.collectAsStateWithLifecycle()
    val changesDone by vm.extras.changesDone.collectAsStateWithLifecycle()
    val changesBusy by vm.extras.changesBusy.collectAsStateWithLifecycle()
    val translateCtx = LocalContext.current
    val translatePrefs = remember { io.github.murtazanasir.mailr.ui.conversation.TranslatePrefs(translateCtx) }
    var translateTo by remember { mutableStateOf(translatePrefs.last) }
    var moveOpen by remember { mutableStateOf(false) }
    var person by rememberSaveable { mutableStateOf<String?>(null) }
    var linkOpen by remember { mutableStateOf(false) }
    val detail by vm.detail.collectAsStateWithLifecycle()
    val error by vm.loadError.collectAsStateWithLifecycle()
    val expanded by vm.expanded.collectAsStateWithLifecycle()
    val bodies by vm.bodies.collectAsStateWithLifecycle()
    val summary by vm.summary.collectAsStateWithLifecycle()
    val brief by vm.brief.collectAsStateWithLifecycle()
    val assistantName by vm.assistantName.collectAsStateWithLifecycle()
    val fromEmail by vm.fromEmail.collectAsStateWithLifecycle()
    val labels by g.mail.labels.collectAsStateWithLifecycle()
    val meForReader by g.mail.me.collectAsStateWithLifecycle()
    val sb = remember { SnackbarHostState() }
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val tabletop = currentWindowAdaptiveInfo().windowPosture.isTabletop
    LaunchedEffect(vm) {
        vm.messages.collect { m ->
            scope.launch {
                val r = sb.showSnackbar(m.text, m.actionLabel, duration = SnackbarDuration.Short)
                if (r == SnackbarResult.ActionPerformed) m.action?.invoke()
            }
        }
    }
    LaunchedEffect(vm) { vm.closed.collect { closed() } }
    ConversationScreen(detail, error, labels, expanded, bodies, summary, quote, tabletop, sb, ConversationActions(
        back = if (showBack) onBack else null,
        act = { a -> vm.act(a, closes = a in setOf("archive", "trash", "spam", "mute", "not_spam", "untrash", "delete_forever")) },
        toggle = vm::toggle,
        expandAll = vm::expandAll,
        star = { m, on -> vm.starMessage(m.id, on) },
        // ACT draft.resume @android.reader: Reply, Reply all and Forward open the thread's saved draft (yours or
        // Ambrose's) when it has one, so a thread never ends with two answers.
        reply = { m, mode -> nav.openCompose(io.github.murtazanasir.mailr.ui.compose.replyArgs(detail?.messages.orEmpty(), m, mode)) },
        openAttachment = { m, a -> vm.openAttachment(ctx, m.id, a) },
        // ACT event.import_ics @android.reader: "Add to calendar" beside an .ics attachment.
        importIcs = { m, a -> vm.importIcs(m.id, a) },
        shareAttachment = { m, a -> vm.shareAttachment(ctx, m.id, a) },
        saveAttachment = { m, a -> vm.saveAttachment(ctx, m.id, a) },
        showImages = { m -> vm.loadBody(m.id, allowImages = true) },
        snooze = onSnooze,
        labels = onLabels,
        showInTab = onShowInTab,
        category = { onCategory(detail?.conversation?.category) },
        summarize = vm::summarize,
        followUp = { until -> vm.act(if (until == null) "cancel_follow_up" else "follow_up", until = until) },
        toTask = { vm.fromEmail("task") },
        toEvent = { vm.fromEmail("event") },
        openCitation = onOpenCitation,
        openUrl = { u -> openUrl(ctx, u) },
        unsubscribe = { m ->
            val lu = m.listUnsubscribe.orEmpty()
            val link = Regex("<([^>]+)>").findAll(lu).map { it.groupValues[1] }.let { all ->
                all.firstOrNull { it.startsWith("https://") } ?: all.firstOrNull { it.startsWith("mailto:") }
            }
            when {
                link == null -> scope.launch { sb.showSnackbar(ctx.getString(R.string.ui_shell_no_unsubscribe)) }
                link.startsWith("mailto:") -> nav.openCompose(parseMailtoArgs(Uri.parse(link)))
                else -> openUrl(ctx, link)
            }
        },
        // A mailto: link in a message body opens the composer filled in. ACT compose.deeplink @android.reader
        mailto = { uri -> nav.openCompose(parseMailtoArgs(uri)) },
        retry = vm::retry,
        ask = { onAsk(detail?.conversation?.subject) },
        // ACT agent.ask_about: say or type an instruction about this conversation.
        dictate = {
            detail?.conversation?.let { c ->
                ctx.startActivity(io.github.murtazanasir.mailr.ui.ask.AskIntents.capture(ctx, AskAbout.Conversation(c.id, c.subject.orEmpty()), from = "reader"))
            }
        },
        invite = io.github.murtazanasir.mailr.ui.conversation.InviteCallbacks(
            answer = { m, r, send -> vm.extras.answer(m.id, r, "", send) },
            applyAnyway = { m -> vm.extras.applyAnyway(m.id) },
            proposal = { m, i, accept -> vm.extras.proposal(m.id, i, accept, io.github.murtazanasir.mailr.ui.conversation.InviteText.guestName(i, m.from.email)) },
            removeCancelled = { m, i -> vm.extras.removeCancelled(m.id, i) },
            linkInvite = { m, uid -> vm.extras.linkInvite(m.id, uid) },
            moveSuggested = { m, c -> vm.extras.moveSuggested(m.id, c) },
            dismissSuggested = { m -> vm.extras.dismissSuggested(m.id) },
        ),
        moveTo = { moveOpen = true },
        noteFromEmail = { kind -> vm.extras.note(kind, openNote) },
        linkNote = { linkOpen = true },
        meeting = { vm.fromEmail("meeting") },
        pickReply = { m, text -> nav.openCompose(io.github.murtazanasir.mailr.ui.compose.replyArgs(detail?.messages.orEmpty(), m, "reply").copy(body = text)) },
        retrySend = vm.extras::retry,
        translate = { m, lang -> translatePrefs.last = lang; translateTo = lang; vm.extras.translate(m.id, lang) },
        showTranslation = { m, on -> vm.extras.showTranslation(m.id, on) },
        openPerson = { email -> person = email },
        openAttachedEmail = { m, part, images -> vm.extras.loadAttached(m.id, part, images) },
    ), brief = brief, assistantName = assistantName,
        place = io.github.murtazanasir.mailr.ui.mail.MailPlace.ofConversation(detail?.conversation?.labelIds, labels, listView),
        extras = io.github.murtazanasir.mailr.ui.conversation.ReaderExtrasUi(invites, inviteBusy, replies, outboxRows,
            translations, translateTo, attachedEmails, changesDone, changesBusy),
        contextLabelId = g.mail.contextLabelId,
        archiveRemovesLabel = io.github.murtazanasir.mailr.ui.mail.archiveRemovesLabel(meForReader?.settings))
    if (moveOpen) io.github.murtazanasir.mailr.ui.conversation.MoveToSheet(labels,
        io.github.murtazanasir.mailr.ui.mail.MailPlace.ofConversation(detail?.conversation?.labelIds, labels, listView),
        onDismiss = { moveOpen = false }) { t ->
        moveOpen = false
        vm.act(t.action, labelIds = t.labelId?.let { listOf(it) }, closes = true)
    }
    if (linkOpen) io.github.murtazanasir.mailr.ui.conversation.LinkNoteSheet({ q -> g.api.findNotes(q) }, onDismiss = { linkOpen = false }) { n ->
        linkOpen = false
        vm.extras.link(n.id, n.title)
    }
    // ACT person.card @android.reader: the sender's card (details, groups, VIP, Ambrose's memory) as a sheet.
    person?.let { email ->
        val store = remember { io.github.murtazanasir.mailr.ui.memory.ApiMemoryStore(g) }
        val me by g.mail.me.collectAsStateWithLifecycle()
        io.github.murtazanasir.mailr.ui.memory.PersonCardSheet(io.github.murtazanasir.mailr.ui.memory.PersonTarget.Email(email), store, assistantName,
            vip = { e -> me.isVip(e) }, onClose = { person = null })
    }
    // ACT mail.to_task @android.reader, ACT mail.to_event @android.reader: the editor on the server's suggestion.
    fromEmail?.let { f ->
        val prefs by g.settings.prefs.collectAsStateWithLifecycle(io.github.murtazanasir.mailr.data.settings.Prefs())
        val collections by g.calendar.collections.collectAsStateWithLifecycle()
        val defaults by g.calendar.defaults.collectAsStateWithLifecycle()
        FromEmailSheet(f, collections, defaults, prefs.morningHour, prefs.defaultEventReminder.takeIf { it >= 0 }, g.calendar::newUid,
            onClose = vm::closeFromEmail, onSaveTask = { vm.saveFromEmail(task = it) }, onSaveEvent = { vm.saveFromEmail(event = it) },
            userDefaults = { allDay -> g.calendar.eventDefaults(allDay, prefs.defaultEventReminder.takeIf { it >= 0 }) })
    }
}

@Composable
internal fun ComposeRoute(g: AppGraph, args: ComposeArgs, owner: String, onDone: (ComposeResult) -> Unit) {
    // The saved state keeps the local draft's id across process death. [owner], the back stack
    // entry, lets opening the same draft again come back here (one window per draft, OpenEditors).
    val vm: ComposeViewModel = viewModel(key = "compose-$args") { ComposeViewModel(g, args, createSavedStateHandle(), owner) }
    val s by vm.state.collectAsStateWithLifecycle()
    val liveUi by vm.liveUi.collectAsStateWithLifecycle()
    val sb = remember { SnackbarHostState() }
    val ctx = LocalContext.current
    var pickSchedule by remember { mutableStateOf(false) }
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
        vm.attach(ctx.contentResolver, uris)
    }
    LaunchedEffect(vm) { vm.done.collect { onDone(it) } }
    LaunchedEffect(s.error) { s.error?.let { sb.showSnackbar(it); vm.clearError() } }
    BackHandler { vm.close() }
    val photos = io.github.murtazanasir.mailr.ui.compose.rememberPhotoActions(vm)
    ComposeScreen(s, sb, ComposeCallbacks(
        close = vm::close, discard = vm::discard, send = { vm.send(it) }, pickSchedule = { pickSchedule = true },
        attach = { picker.launch(arrayOf("*/*")) }, setIdentity = vm::setIdentity, setSubject = vm::setSubject,
        setBody = vm::setBody, addRecipient = vm::addRecipient, addPerson = vm::addPerson,
        removeRecipient = vm::removeRecipient, query = vm::query, toggleCc = vm::toggleCc, toggleQuoted = vm::toggleQuoted,
        removeAttachment = vm::removeAttachment, restoreAttachment = vm::restoreAttachment,
        setText = vm::setText, setCursor = vm::setCursor, removeBlock = vm::removeBlock, editAsText = vm::editAsText,
        takePhoto = photos.take, pickPhotos = photos.pick,
        templates = vm::templates, insertTemplate = vm::insertTemplate,
    ), live = {
        // ACT draft.live_update @android.editor, ACT draft.resolve_conflict @android.editor
        io.github.murtazanasir.mailr.ui.components.LiveBars(liveUi, "draft", io.github.murtazanasir.mailr.ui.components.LiveBarActions(
            undo = vm::undoLive, dismiss = vm::dismissLive, useMine = vm::useMine, useTheirs = vm::useTheirs))
    })
    if (pickSchedule) DateTimeDialog(onDismiss = { pickSchedule = false }) { at -> pickSchedule = false; vm.send(at) }
}
