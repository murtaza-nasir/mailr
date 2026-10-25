package io.github.murtazanasir.mailr.ui

import io.github.murtazanasir.mailr.util.catching

// The screens the home screen opens over the tabs (Settings, the Screener, Approvals) and what it says
// when the composer closes. Split out of App.kt.

import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavHostController
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.data.api.pushServer
import io.github.murtazanasir.mailr.ui.compose.ComposeArgs
import io.github.murtazanasir.mailr.ui.compose.ComposeResult
import io.github.murtazanasir.mailr.ui.compose.RestoreStore
import io.github.murtazanasir.mailr.ui.mail.UiMessage
import io.github.murtazanasir.mailr.data.api.userMessage
import io.github.murtazanasir.mailr.ui.approvals.ApprovalsScreen
import io.github.murtazanasir.mailr.ui.mail.ScreenerScreen
import io.github.murtazanasir.mailr.data.api.screener
import io.github.murtazanasir.mailr.data.api.screenerDecide
import io.github.murtazanasir.mailr.ui.settings.SettingsCallbacks
import io.github.murtazanasir.mailr.ui.settings.SettingsScreen
import kotlinx.coroutines.launch
import kotlinx.coroutines.CoroutineScope
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.i18n.LocaleFormats

/** Settings, from the avatar menu: the phone's choices, push, the account picture and the server's
 *  notification choices. [scope] is the home screen's, so a change keeps going after Back. */
@Composable
internal fun SettingsRoute(g: AppGraph, nav: NavHostController, scope: CoroutineScope,
                           assistantName: String = io.github.murtazanasir.mailr.data.api.AssistantGate.DEFAULT_NAME, show: (UiMessage) -> Unit) {
    val ctx = LocalContext.current
        val vm: SettingsViewModel = viewModel { SettingsViewModel(g) }
        val prefs by g.settings.prefs.collectAsStateWithLifecycle(io.github.murtazanasir.mailr.data.settings.Prefs())
        val sessions by vm.sessions.collectAsStateWithLifecycle()
        val mailAccounts by vm.accounts.collectAsStateWithLifecycle()
        // Back from the web page where an account was fixed: read the health again.
        androidx.lifecycle.compose.LifecycleResumeEffect(vm) { vm.refreshAccounts(); onPauseOrDispose {} }
        val sess by g.sessions.session.collectAsStateWithLifecycle()
        val taskPrefs = remember { io.github.murtazanasir.mailr.ui.tasks.TaskUiPrefs(ctx) }
        // The Recordings (Speakr) switch shows only when the server has a Speakr connector.
        val speakrConnected by androidx.compose.runtime.produceState(false) {
            value = catching { g.speakr.status().connected }.getOrDefault(false)
        }
        val swipes by remember { taskPrefs.swipes() }.collectAsStateWithLifecycle(taskPrefs.swipeRight to taskPrefs.swipeLeft)
        var editSwipes by remember { mutableStateOf(false) }
        val remindSettings by g.calendar.reminderSettings.collectAsStateWithLifecycle()
        val pushInfo by g.push.info.collectAsStateWithLifecycle()
        val pushServer by produceState<io.github.murtazanasir.mailr.data.api.PushServer?>(null) {
            value = try { g.api.pushServer() } catch (e: kotlinx.coroutines.CancellationException) { throw e } catch (_: Exception) { null }
        }
        var pushTesting by remember { mutableStateOf(false) }
        var pushTestResult by remember { mutableStateOf<String?>(null) }
        // Your picture: Change (photo picker or camera), Remove, Use my sign-in picture.
        val me by g.mail.me.collectAsStateWithLifecycle()
        val updateOffer by g.updates.available.collectAsStateWithLifecycle()
        var pictureBusy by remember { mutableStateOf(false) }
        val pictures = remember { io.github.murtazanasir.mailr.ui.settings.AccountPictures(g.api, g.mail) }
        fun pictureRun(ok: Int, fail: Int, block: suspend () -> Unit) = scope.launch {
            pictureBusy = true
            catching { block() }
                .onSuccess { show(UiMessage(ctx.getString(ok))) }
                .onFailure { e ->
                    val why = if (e is io.github.murtazanasir.mailr.ui.settings.AccountPictureImage.Refused) e.message
                        else e.userMessage()
                    show(UiMessage(ctx.getString(fail, why)))
                }
            pictureBusy = false
        }
        val (pickPicture, takePicture) = io.github.murtazanasir.mailr.ui.settings.rememberPictureSources(
            onPicked = { uri -> pictureRun(R.string.ui_shell_picture_updated, R.string.ui_shell_picture_not_saved) {
                val bytes = pictures.read(ctx.contentResolver, uri)
                    ?: throw io.github.murtazanasir.mailr.ui.settings.AccountPictureImage.Refused(ctx.getString(R.string.ui_shell_picture_unreadable))
                pictures.upload(bytes)
            } },
            onNoCamera = { show(UiMessage(ctx.getString(R.string.ui_shell_no_camera))) },
        )
        SettingsScreen(prefs, sess?.name.orEmpty(), sess?.email.orEmpty(), g.sessions.server, sessions, SettingsCallbacks(
            back = { nav.popBackStack() },
            update = { f -> scope.launch { g.settings.update(f); g.reminders.resync() } },
            revoke = vm::revoke,
            signOut = vm::signOut,
            openWeb = { openUrl(ctx, g.sessions.server + "/settings") },
            taskSwipes = { editSwipes = true },
            taskSwipeSummary = stringResource(R.string.ui_shell_task_swipes, swipes.first.label, swipes.second.label),
            remindAgain = remindSettings?.repeat,
            // ACT reminder.repeat @android.notification: saved on the server for the phone and the web; the phone keeps a copy for offline.
            setRemindAgain = { v -> scope.launch {
                catching { g.calendar.setRemindAgain(v) }
                    .onFailure { show(UiMessage(ctx.getString(R.string.ui_shell_remind_again_failed))) }
                g.reminders.resync()
            } },
            push = io.github.murtazanasir.mailr.ui.settings.PushUi(pushInfo, pushTesting, pushTestResult, pushServer, live = prefs.liveConnection),
            pushActions = io.github.murtazanasir.mailr.ui.settings.PushCallbacks(
                retry = { pushTestResult = null; g.push.retry() },
                choose = { (ctx.findActivity())?.let { g.push.choose(it) } },
                getApp = { openUrl(ctx, io.github.murtazanasir.mailr.ui.settings.PUSH_APPS_URL) },
                copyServer = { pushServer?.let { io.github.murtazanasir.mailr.ui.settings.copyAndOpenNtfy(ctx, it.url, pushInfo.distributor) } },
                setLive = { v -> scope.launch { g.settings.update { it.copy(liveConnection = v) } } },
                test = { scope.launch {
                    pushTesting = true
                    val r = g.push.test()
                    pushTesting = false
                    pushTestResult = if (r.ok) ctx.getString(R.string.ui_shell_push_test_sent)
                        else ctx.getString(R.string.ui_shell_push_test_failed, r.error ?: ctx.getString(R.string.ui_shell_unknown_error))
                } },
            ),
            updateAvailable = updateOffer,
            serverVersion = me?.version,
            openUpdate = { if (!g.updates.open(ctx)) show(UiMessage(ctx.getString(R.string.ui_shell_no_app_download))) },
            // The account's notification choices: saved on the server for every device.
            setMailNotify = { v -> scope.launch {
                catching { g.api.patchSettings(io.github.murtazanasir.mailr.data.settings.ServerPrefs.mailPatch(v)) }
                    .onSuccess { catching { g.mail.refreshMe() } }
                    .onFailure { show(UiMessage(ctx.getString(R.string.ui_shell_change_failed))); catching { g.mail.refreshMe() } }
            } },
            setApprovals = { v -> scope.launch {
                catching { g.api.patchSettings(io.github.murtazanasir.mailr.data.settings.ServerPrefs.approvalsPatch(v)) }
                    .onSuccess { catching { g.mail.refreshMe() } }
                    .onFailure { show(UiMessage(ctx.getString(R.string.ui_shell_change_failed))); catching { g.mail.refreshMe() } }
            } },
            picture = io.github.murtazanasir.mailr.ui.settings.PictureUi(me?.picture, pictureBusy),
            pictureActions = io.github.murtazanasir.mailr.ui.settings.PictureCallbacks(
                pick = pickPicture,
                take = takePicture,
                remove = { pictureRun(R.string.ui_shell_picture_removed, R.string.ui_shell_picture_remove_failed) { pictures.remove() } },
                useSignIn = { pictureRun(R.string.ui_shell_picture_signin, R.string.ui_shell_picture_signin_failed) { pictures.useSignIn() } },
            ),
            openActivity = { nav.navigate(Routes.ACTIVITY) },
            assistantName = assistantName,
            openMemory = { nav.navigate(Routes.MEMORY) { launchSingleTop = true } },
            openTrusted = { nav.navigate(Routes.TRUSTED) { launchSingleTop = true } },
            openOrders = { nav.navigate(Routes.ORDERS) { launchSingleTop = true } },
            openSweep = { nav.navigate(Routes.SWEEP) { launchSingleTop = true } },
            accounts = mailAccounts,
            openAccounts = { openUrl(ctx, g.sessions.server + "/settings/mail/accounts") },
        ), speakrConnected = speakrConnected)
        // The same dialog as Tasks ⋮ → Swipe actions; both read and write TaskUiPrefs.
        if (editSwipes) io.github.murtazanasir.mailr.ui.tasks.SwipeSettingsDialog(swipes.first, swipes.second, { editSwipes = false }) { r, l ->
            editSwipes = false
            taskPrefs.swipeRight = r
            taskPrefs.swipeLeft = l
        }
}

// ACT screener.review @android.screener: the Screener, the first-time senders waiting (Allow, Block).
@Composable
internal fun ScreenerRoute(g: AppGraph, nav: NavHostController, openConversation: (Long) -> Unit) {
        val ctx = LocalContext.current
        var list by remember { mutableStateOf<io.github.murtazanasir.mailr.data.api.ScreenerList?>(null) }
        var err by remember { mutableStateOf<String?>(null) }
        var refreshing by remember { mutableStateOf(false) }
        val sb = remember { SnackbarHostState() }
        val sc = rememberCoroutineScope()
        fun load() = sc.launch {
            refreshing = true
            catching { g.api.screener() }.onSuccess { list = it; err = null }.onFailure { err = it.message ?: ctx.getString(R.string.ui_shell_network_error_title) }
            refreshing = false
        }
        LaunchedEffect(Unit) { load() }
        ScreenerScreen(list, err, refreshing, sb, onBack = { nav.popBackStack() }, onRefresh = { load() },
            onDecide = { s, d ->
                list = list?.let { l -> l.copy(senders = l.senders.filter { it.id != s.id }, count = l.count - 1) }
                sc.launch {
                    catching { g.api.screenerDecide(s.id, d) }
                        .onSuccess { sb.showSnackbar(ctx.getString(if (d == "allow") R.string.ui_shell_screener_allowed else R.string.ui_shell_screener_blocked, s.title)) }
                        .onFailure { sb.showSnackbar(ctx.getString(R.string.ui_shell_not_changed, it.message ?: ctx.getString(R.string.ui_shell_network_error))); load() }
                }
            },
            onOpen = { s -> openConversation(s.latest.conversationId) })
}

// ACT approval.approve @android.approvals, ACT approval.deny @android.approvals: "Waiting for you" (pending approvals, newest
// first; then messages not sent and reminders showing, as in the web's bell; then recent decisions).
@Composable
internal fun ApprovalsRoute(approvals: io.github.murtazanasir.mailr.ui.approvals.ApprovalsTracker, nav: NavHostController,
                            openOutbox: () -> Unit = {}) {
    val ctx = LocalContext.current
        val pending by approvals.pending.collectAsStateWithLifecycle()
        val history by approvals.history.collectAsStateWithLifecycle()
        val err by approvals.error.collectAsStateWithLifecycle()
        val refreshing by approvals.refreshing.collectAsStateWithLifecycle()
        val focus by approvals.focus.collectAsStateWithLifecycle()
        val notSent by approvals.notSent.collectAsStateWithLifecycle()
        val reminders by approvals.reminders.collectAsStateWithLifecycle()
        val sb = remember { SnackbarHostState() }
        LaunchedEffect(Unit) {
            approvals.messages.collect { m ->
                val rule = m.undoRuleId
                val r = sb.showSnackbar(m.text, actionLabel = rule?.let { ctx.getString(R.string.ui_shell_undo) },
                    duration = if (rule != null) androidx.compose.material3.SnackbarDuration.Long else androidx.compose.material3.SnackbarDuration.Short)
                if (rule != null && r == androidx.compose.material3.SnackbarResult.ActionPerformed) approvals.undoGrant(rule)
            }
        }
        LaunchedEffect(Unit) { approvals.refresh() }
        androidx.compose.runtime.DisposableEffect(Unit) { onDispose { approvals.focus.value = null } }
        ApprovalsScreen(pending, history, err, refreshing, sb, onBack = { nav.popBackStack() }, onRefresh = approvals::refreshNow,
            onDecide = approvals::decide, focus = focus, onDecideMany = approvals::decideMany, onApplyReview = approvals::applyReview,
            // ACT agent.ask_about: Ask on a card opens the capture with the approval attached; the card still decides.
            onAsk = { a -> ctx.startActivity(io.github.murtazanasir.mailr.ui.ask.AskIntents.capture(ctx,
                io.github.murtazanasir.mailr.ui.approvals.askAbout(a), from = "approval")) },
            // The web bell's other sections: Not sent (Retry, Open Outbox) and Reminders (the sheet, Review all).
            notSent = notSent, onRetry = approvals::retry, onOutbox = openOutbox,
            reminders = reminders,
            onReminder = { r -> ctx.startActivity(io.github.murtazanasir.mailr.ui.calendar.ReminderActivity.intent(ctx, key = r.key)) },
            onReviewAll = { ctx.startActivity(io.github.murtazanasir.mailr.ui.calendar.ReminderActivity.intent(ctx, stack = true)) })
}

/** What the home screen says when the composer closes: "Sending…" or the scheduled time with Undo,
 *  or why nothing was sent. */
internal fun composeDone(g: AppGraph, nav: NavHostController, result: ComposeResult, show: (UiMessage) -> Unit) {
    nav.popBackStack()
    if (result is ComposeResult.Sent) {
        val ob = result.outbox
        val text = if (result.scheduled) AppText.get(R.string.ui_shell_scheduled_for, io.github.murtazanasir.mailr.ui.util.Format.parse(ob.sendAt)
            ?.format(LocaleFormats.date("EEEMMMdhmma", "EEE, MMM d, h:mm a")) ?: AppText.get(R.string.ui_shell_later))
        else AppText.get(R.string.ui_shell_sending)
        show(UiMessage(text, AppText.get(R.string.ui_shell_undo)) {
            catching { g.compose.cancel(ob.id) }.onSuccess { c ->
                c.payload?.let { p -> nav.openCompose(ComposeArgs(restoreKey = RestoreStore.put(p, c.draftId))) }
            }.onFailure { show(UiMessage(AppText.get(R.string.ui_shell_too_late_undo))) }
        })
    } else if (result is ComposeResult.DiscardedDraft) {
        // ACT draft.discard @android.editor: Undo opens the same message again, as a new draft.
        show(UiMessage(AppText.get(R.string.ui_shell_draft_discarded), AppText.get(R.string.ui_shell_undo)) {
            nav.openCompose(ComposeArgs(restoreKey = RestoreStore.put(result.payload, null)))
        })
    } else ComposeResult.message(result)?.let { show(UiMessage(it)) }
}
