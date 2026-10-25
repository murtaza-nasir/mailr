package io.github.murtazanasir.mailr.ui.approvals

import io.github.murtazanasir.mailr.ui.components.refuseObscuredTouches
import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.i18n.LocaleFormats
import io.github.murtazanasir.mailr.i18n.AppText
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.heightIn
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
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.outlined.Block
import androidx.compose.material.icons.outlined.Event
import androidx.compose.material.icons.outlined.NotificationsActive
import androidx.compose.material.icons.outlined.TaskAlt
import androidx.compose.material.icons.automirrored.outlined.StickyNote2
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material.icons.outlined.GppMaybe
import androidx.compose.material.icons.outlined.HourglassEmpty
import androidx.compose.material.icons.outlined.Sync
import androidx.compose.material.icons.outlined.VerifiedUser
import io.github.murtazanasir.mailr.look.AlertDialog
import androidx.compose.material3.Badge
import io.github.murtazanasir.mailr.look.FilledTonalButton
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import io.github.murtazanasir.mailr.look.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.SwipeToDismissBox
import androidx.compose.material3.SwipeToDismissBoxValue
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import io.github.murtazanasir.mailr.look.TopAppBar
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.material3.rememberSwipeToDismissBoxState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation.NavController
import androidx.navigation.compose.currentBackStackEntryAsState
import io.github.murtazanasir.mailr.data.api.Approval
import io.github.murtazanasir.mailr.data.api.OutboxRow
import io.github.murtazanasir.mailr.data.db.ReminderEntity
import io.github.murtazanasir.mailr.data.sync.NotifyDecisions
import io.github.murtazanasir.mailr.ui.components.ConversationSkeleton
import io.github.murtazanasir.mailr.ui.components.EmptyState
import io.github.murtazanasir.mailr.ui.more.ApprovalCard
import io.github.murtazanasir.mailr.ui.util.Format
import kotlinx.coroutines.delay

/** How long the banner for a new approval stays up. */
const val ARRIVAL_MILLIS = 6_000L

/** "3 agent actions waiting for approval", for the badge's and the shield's description. */
fun waitingDescription(n: Int) = AppText.plural(R.plurals.appr_waiting_description, n, n)

/** A badge's text: the count, "99+" above 99. */
/** The ask capture's chip for an approval card: its title, as the card shows it. ACT agent.ask_about @android.approvals */
fun askAbout(a: Approval) = io.github.murtazanasir.mailr.data.agent.AskAbout.Approval(a.id, a.shown?.title ?: a.preview.title.ifBlank { a.action })

fun badgeText(n: Int) = if (n > 99) "99+" else "$n"

/** A decision on a card: the approval, allow (true) or deny, and the scope ("once", "session", "always"). */
typealias Decide = (Approval, Boolean, String) -> Unit

/**
 * "Allow all n": the requests waiting from one chat or one agent, where there are several,
 * oldest first (the order they were asked in). Approvals from the older outbox queue are left out.
 */
fun allowAllGroups(pending: List<Approval>): List<List<Approval>> =
    pending.filter { it.outboxId == null }.groupBy { it.requesterKey }.values
        .filter { it.size > 1 }.map { g -> g.sortedWith(compareBy<Approval>({ it.createdAt ?: "" }, { it.id })) }

/** "this chat", "Claude Code", "Ambrose": who a group of requests came from. */
fun groupSource(a: Approval): String = when {
    a.chatSessionId != null -> AppText.get(R.string.appr_source_this_chat)
    a.subject == "assistant" || a.subject.startsWith("session:") -> io.github.murtazanasir.mailr.i18n.AssistantName.now()
    else -> a.actor.ifBlank { AppText.get(R.string.appr_source_an_agent) }
}

/** The line after "Allow all": "Allowed 3 requests." and the ones already decided elsewhere. */
fun allowedText(count: Int, skipped: Int): String =
    AppText.plural(R.plurals.appr_allowed_count, count, count) + if (skipped > 0) AppText.plural(R.plurals.appr_skipped, skipped, skipped) else ""

private fun approvalLine(a: Approval): String = a.shown?.title ?: a.preview.title.ifBlank { a.action }

/**
 * What waits for you, as the web's bell counts it (features/waiting): approvals, messages not sent,
 * and reminders showing now, [missed] of them missed.
 */
data class WaitingCounts(val approvals: Int = 0, val notSent: Int = 0, val reminders: Int = 0, val missed: Int = 0) {
    val total: Int get() = approvals + notSent + reminders
    /** An approval or a failed send waits: the badge is red. */
    val urgent: Boolean get() = approvals + notSent > 0
    /** Only missed reminders are left: the badge is grey. */
    val quiet: Boolean get() = !urgent && reminders > 0 && missed == reminders

    /** "Waiting for you: 2 approvals, 1 not sent, 3 reminders (1 missed)", or "Nothing waiting" (the web's waitingLabel). */
    val label: String
        get() {
            val parts = buildList {
                if (approvals > 0) add(AppText.plural(R.plurals.appr_count_approvals, approvals, approvals))
                if (notSent > 0) add(AppText.plural(R.plurals.appr_count_not_sent, notSent, notSent))
                if (reminders > 0) add(if (missed > 0) AppText.plural(R.plurals.appr_count_reminders_missed, reminders, reminders, missed)
                    else AppText.plural(R.plurals.appr_count_reminders, reminders, reminders))
            }
            return if (parts.isEmpty()) AppText.get(R.string.appr_nothing_waiting) else AppText.get(R.string.appr_waiting_for_you_list, parts.joinToString(", "))
        }
}

/**
 * The bell in the inbox's search bar: shown only while something waits, with the count. Red while an
 * approval or a failed send waits, grey when only missed reminders are left. Tapping it opens
 * "Waiting for you".
 */
@Composable
fun WaitingBell(counts: WaitingCounts, onClick: () -> Unit) {
    if (counts.total <= 0) return
    val cs = MaterialTheme.colorScheme
    IconButton(onClick = onClick, modifier = Modifier.testTag("waiting-bell")
        .semantics { contentDescription = counts.label }) {
        BadgedBox(badge = {
            Badge(Modifier.testTag(if (counts.quiet) "waiting-badge-quiet" else "waiting-badge-red"),
                containerColor = if (counts.quiet) cs.outline else cs.error,
                contentColor = if (counts.quiet) cs.surface else cs.onError) { Text(badgeText(counts.total)) }
        }) {
            Icon(Icons.Outlined.NotificationsActive, null, tint = if (counts.urgent) cs.error else cs.onSurfaceVariant)
        }
    }
}

/** For the Assistant tab: its label is read with the waiting count (the tab's own icon is not read). */
fun waitingSemantics(count: Int): Modifier =
    if (count <= 0) Modifier else Modifier.semantics { contentDescription = waitingDescription(count) }

/** The Assistant tab's icon with the number of agent actions waiting for approval. */
@Composable
fun ApprovalsBadged(count: Int, icon: @Composable () -> Unit) {
    BadgedBox(badge = { Badge(Modifier.testTag("assistant-badge")) { Text(badgeText(count)) } }) { icon() }
}

/**
 * A new approval, announced once at the top of the screen where the search bar sits, so the
 * list and the Compose button stay free. It goes after [ARRIVAL_MILLIS], when swiped either way,
 * or when Review is tapped.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ApprovalArrivalBanner(arrival: Arrival?, onReview: (Arrival) -> Unit, onDismiss: () -> Unit, modifier: Modifier = Modifier) {
    // The last arrival stays composed while the banner animates out.
    var shown by remember { mutableStateOf(arrival) }
    if (arrival != null) shown = arrival
    val onDismissNow by rememberUpdatedState(onDismiss)
    val current by rememberUpdatedState(arrival)
    // A swipe reports more than once; each arrival is dismissed once.
    val handled = remember { mutableSetOf<Arrival>() }
    val dismiss = { current?.let { if (handled.add(it)) onDismissNow() } }
    LaunchedEffect(arrival) {
        if (arrival != null) {
            delay(ARRIVAL_MILLIS)
            dismiss()
        }
    }
    Box(modifier.fillMaxWidth().statusBarsPadding().padding(horizontal = 12.dp, vertical = 8.dp), contentAlignment = Alignment.TopCenter) {
        AnimatedVisibility(arrival != null, enter = slideInVertically { -it } + fadeIn(), exit = slideOutVertically { -it } + fadeOut()) {
            val a = shown ?: return@AnimatedVisibility
            val swipe = rememberSwipeToDismissBoxState(confirmValueChange = { v ->
                if (v != SwipeToDismissBoxValue.Settled) dismiss()
                true
            })
            // A new arrival starts from the middle again.
            LaunchedEffect(a) { swipe.snapTo(SwipeToDismissBoxValue.Settled) }
            SwipeToDismissBox(swipe, backgroundContent = {}, modifier = Modifier.widthIn(max = 560.dp).testTag("approval-banner")) {
                // In the web's look the web's toast (components.css .toast: the toast tokens, 4 dp corners),
                // as the PWA announces an approval; Material's inverse pill in Material's.
                val web = io.github.murtazanasir.mailr.look.look
                val bg = if (web.web) web.palette.toastBg else MaterialTheme.colorScheme.inverseSurface
                val fg = if (web.web) web.palette.toastFg else MaterialTheme.colorScheme.inverseOnSurface
                val accent = if (web.web) web.palette.toastAction else MaterialTheme.colorScheme.inversePrimary
                Surface(shape = if (web.web) RoundedCornerShape(io.github.murtazanasir.mailr.look.WebTokens.RadiusXs) else RoundedCornerShape(28.dp), color = bg,
                    contentColor = fg, shadowElevation = 6.dp,
                    modifier = Modifier.fillMaxWidth().height(56.dp).semantics { liveRegion = LiveRegionMode.Polite }) {
                    Row(Modifier.padding(start = 16.dp, end = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Outlined.GppMaybe, null, Modifier.size(22.dp), tint = accent)
                        Spacer(Modifier.width(12.dp))
                        Column(Modifier.weight(1f)) {
                            Text(a.approval.preview.title.ifBlank { a.approval.action.ifBlank { stringResource(R.string.appr_an_agent_action) } },
                                style = MaterialTheme.typography.bodyMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(arrivalLine(a), style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis,
                                color = fg.copy(alpha = 0.8f))
                        }
                        TextButton(onClick = { onReview(a) }) { Text(stringResource(R.string.appr_review), color = accent) }
                    }
                }
            }
        }
    }
}

/**
 * [ApprovalArrivalBanner] for the tracker's arrivals, over whatever screen is showing. [hide]
 * is true where an arrival needs no banner (the Approvals screen, the chat that asked); such an
 * arrival counts as announced.
 */
@Composable
fun ApprovalArrivalOverlay(tracker: ApprovalsTracker, hide: (Arrival) -> Boolean, onReview: (Arrival) -> Unit) {
    val arrival by tracker.arrival.collectAsStateWithLifecycle()
    val a = arrival
    val hidden = a != null && hide(a)
    LaunchedEffect(a, hidden) { if (hidden) tracker.dismissArrival() }
    // No pointer input of its own: touches outside the banner reach the screen below.
    Box(Modifier.fillMaxSize()) {
        ApprovalArrivalBanner(a.takeUnless { hidden }, onReview = { tracker.dismissArrival(); onReview(it) },
            onDismiss = tracker::dismissArrival)
    }
}

/** "Claude Code is waiting for your OK", with "· 2 more" when others came in meanwhile. */
fun arrivalLine(a: Arrival): String =
    AppText.get(R.string.appr_arrival_line, a.approval.actor.ifBlank { AppText.get(R.string.appr_an_agent) })
        .let { if (a.more > 0) AppText.get(R.string.appr_arrival_more, it, a.more) else it }

/**
 * The Approvals screen: what waits for an answer, newest first, and below it the recent
 * decisions with their outcome. [focus] (an approval's key, from a notification) is scrolled
 * to and outlined. [card] is one pending approval with Allow once / Allow… / Deny.
 */
@Composable
fun ApprovalsScreen(
    pending: List<Approval>?,
    history: List<ApprovalRecord>,
    error: String?,
    refreshing: Boolean,
    snackbar: SnackbarHostState,
    onBack: (() -> Unit)?,
    onRefresh: () -> Unit,
    onDecide: Decide,
    focus: String? = null,
    /** Ask on each card: the ask capture with the approval attached (ACT agent.ask_about). */
    onAsk: ((Approval) -> Unit)? = null,
    /** Apply on a review card with the user's moves on it (ACT agent.review_move); null approves the whole card. */
    onApplyReview: ((Approval, Map<Long, String>) -> Unit)? = null,
    card: @Composable (Approval, Decide) -> Unit = { a, d -> ApprovalCard(a, onAsk, onApplyReview, onDecide = d) },
    /** "Allow all n" for several requests from one chat or agent (ACT approval.approve_many). */
    onDecideMany: ((List<Approval>) -> Unit)? = null,
    /** Not sent: the messages that could not be sent, with Retry, and Open Outbox. */
    notSent: List<OutboxRow> = emptyList(),
    onRetry: (OutboxRow) -> Unit = {},
    onOutbox: (() -> Unit)? = null,
    /** Reminders showing now and missed ones: a tap opens the reminder's sheet; Review all opens them one by one. */
    reminders: List<ReminderEntity> = emptyList(),
    onReminder: (ReminderEntity) -> Unit = {},
    onReviewAll: (() -> Unit)? = null,
) {
    var confirming by remember { mutableStateOf<List<Approval>?>(null) }
    confirming?.let { group ->
        AlertDialog(
            onDismissRequest = { confirming = null },
            title = { Text(stringResource(R.string.appr_allow_each_once)) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(6.dp), modifier = Modifier.testTag("allow-all-list")) {
                    group.forEachIndexed { i, a -> Text("${i + 1}. ${approvalLine(a)}", style = MaterialTheme.typography.bodyMedium) }
                }
            },
            // A touch through another app's overlay never approves the group.
            confirmButton = { TextButton(onClick = { confirming = null; onDecideMany?.invoke(group) },
                modifier = Modifier.refuseObscuredTouches().testTag("allow-all-confirm")) { Text(stringResource(R.string.appr_allow_all_n, group.size)) } },
            dismissButton = { TextButton(onClick = { confirming = null }) { Text(stringResource(R.string.appr_cancel)) } },
        )
    }
    // Other apps' overlays are hidden here, and the screen stays out of screenshots and Recents.
    io.github.murtazanasir.mailr.ui.components.SensitiveScreen()
    Scaffold(
        contentWindowInsets = WindowInsets(0),
        topBar = {
            TopAppBar(title = { Text(stringResource(R.string.appr_waiting_for_you)) },
                navigationIcon = { onBack?.let { IconButton(onClick = it) { Icon(Icons.AutoMirrored.Filled.ArrowBack, stringResource(R.string.appr_back)) } } })
        },
        snackbarHost = { SnackbarHost(snackbar) },
    ) { pad ->
        PullToRefreshBox(refreshing, onRefresh, Modifier.padding(pad).fillMaxSize()) {
            when {
                pending == null && error != null -> EmptyState(Icons.Outlined.VerifiedUser, stringResource(R.string.appr_cant_load), error,
                    action = stringResource(R.string.appr_try_again) to onRefresh)
                pending == null -> ConversationSkeleton(4)
                pending.isEmpty() && history.isEmpty() && notSent.isEmpty() && reminders.isEmpty() ->
                    EmptyState(Icons.Outlined.VerifiedUser, stringResource(R.string.appr_nothing_waiting),
                        stringResource(R.string.appr_empty_text))
                else -> {
                    val list = rememberLazyListState()
                    val sorted = pending  // newest first, as GET /approvals returns them
                    LaunchedEffect(focus, sorted) {
                        val at = sorted.indexOfFirst { NotifyDecisions.approvalKey(it) == focus }
                        if (at >= 0) list.animateScrollToItem(at + 1)  // + 1: the header
                    }
                    LazyColumn(state = list, contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp),
                        horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.fillMaxSize().testTag("approvals-list")) {
                        if (sorted.isNotEmpty() || (notSent.isEmpty() && reminders.isEmpty())) item(key = "h-pending") {
                            // Nothing to approve is a line of text, not a heading over an empty section.
                            if (sorted.isEmpty()) Text(stringResource(R.string.appr_no_approvals_waiting), style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp))
                            else Header(stringResource(R.string.appr_header_approvals, sorted.size))
                        }
                        if (onDecideMany != null) items(allowAllGroups(sorted), key = { "all-" + it.first().requesterKey }) { group ->
                            AllowAllRow(group) { confirming = group }
                        }
                        items(sorted, key = { "p-" + NotifyDecisions.approvalKey(it) }) { a ->
                            val focused = NotifyDecisions.approvalKey(a) == focus
                            Box(Modifier.widthIn(max = 640.dp).fillMaxWidth().testTag("approval-" + NotifyDecisions.approvalKey(a))
                                .then(if (focused) Modifier.border(BorderStroke(2.dp, MaterialTheme.colorScheme.primary), RoundedCornerShape(14.dp))
                                else Modifier)) {
                                card(a, onDecide)
                            }
                        }
                        // ACT draft.retry_send @android.approvals, ACT draft.outbox @android.approvals
                        if (notSent.isNotEmpty()) {
                            item(key = "h-notsent") { Header(stringResource(R.string.appr_header_not_sent, notSent.size), action = onOutbox?.let { stringResource(R.string.appr_open_outbox) to it }) }
                            items(notSent, key = { "o-${it.id}" }) { o -> NotSentRow(o, onRetry) }
                        }
                        // ACT notification.open @android.approvals, ACT notification.open_stack @android.approvals
                        if (reminders.isNotEmpty()) {
                            val fired = reminders.filter { !it.missed }
                            val missed = reminders.filter { it.missed }
                            item(key = "h-reminders") {
                                Header(if (fired.isNotEmpty()) stringResource(R.string.appr_header_reminders, reminders.size) else stringResource(R.string.appr_header_missed_reminders, missed.size),
                                    action = onReviewAll?.takeIf { reminders.size > 1 }?.let { stringResource(R.string.appr_review_all) to it })
                            }
                            items(fired, key = { "r-${it.key}" }) { r -> ReminderRow(r, onReminder) }
                            if (missed.isNotEmpty() && fired.isNotEmpty()) item(key = "h-missed") { SubHeader(stringResource(R.string.appr_missed)) }
                            items(missed, key = { "r-${it.key}" }) { r -> ReminderRow(r, onReminder) }
                        }
                        if (history.isNotEmpty()) {
                            item(key = "h-history") { Header(stringResource(R.string.appr_recent_decisions)) }
                            items(history, key = { "d-${it.id}" }) { r -> HistoryRow(r) }
                        }
                    }
                }
            }
        }
    }
}

/** "3 requests from claude-code · Allow all 3": the list opens in a dialog before anything is decided. */
@Composable
private fun AllowAllRow(group: List<Approval>, onAllow: () -> Unit) {
    Surface(shape = RoundedCornerShape(12.dp), color = MaterialTheme.colorScheme.surfaceContainerHigh,
        modifier = Modifier.widthIn(max = 640.dp).fillMaxWidth().testTag("allow-all")) {
        Row(Modifier.padding(start = 16.dp, end = 8.dp, top = 6.dp, bottom = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(pluralStringResource(R.plurals.appr_requests_from, group.size, group.size, groupSource(group.first())), style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.weight(1f))
            FilledTonalButton(onClick = onAllow, modifier = Modifier.refuseObscuredTouches()) { Text(stringResource(R.string.appr_allow_all_n, group.size)) }
        }
    }
}

@Composable
private fun Header(text: String, action: Pair<String, () -> Unit>? = null) {
    Row(Modifier.widthIn(max = 640.dp).fillMaxWidth().padding(top = 4.dp).heightIn(min = 40.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(text, style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary,
            modifier = Modifier.weight(1f).semantics { heading() })
        action?.let { (label, f) -> TextButton(onClick = f) { Text(label) } }
    }
}

@Composable
private fun SubHeader(text: String) {
    Text(text, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.widthIn(max = 640.dp).fillMaxWidth())
}

/** A message that was not sent: the subject, who it was for and why, and Retry. */
@Composable
private fun NotSentRow(o: OutboxRow, onRetry: (OutboxRow) -> Unit) {
    Row(Modifier.widthIn(max = 640.dp).fillMaxWidth().testTag("waiting-not-sent"), verticalAlignment = Alignment.CenterVertically) {
        Icon(Icons.Outlined.ErrorOutline, null, Modifier.size(22.dp), tint = MaterialTheme.colorScheme.error)
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(o.subject.ifBlank { stringResource(R.string.appr_no_subject) }, style = MaterialTheme.typography.bodyMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(listOfNotNull(o.recipients.takeIf { it.isNotEmpty() }?.let { stringResource(R.string.appr_to, it.joinToString()) },
                io.github.murtazanasir.mailr.ui.conversation.plainSendError(o.error)).joinToString(" · "),
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 2,
                overflow = TextOverflow.Ellipsis)
        }
        FilledTonalButton(onClick = { onRetry(o) }) { Text(stringResource(R.string.appr_retry)) }
    }
}

/** "Starts 10:30 AM", "Due Tue 9:00 AM", "Missed · Due yesterday 5:00 PM": a reminder's line. */
fun reminderLine(r: ReminderEntity, now: java.time.ZonedDateTime = AppClock.now()): String {
    val at = java.time.Instant.ofEpochMilli(r.startAt ?: r.fireAt).atZone(now.zone)
    val days = java.time.temporal.ChronoUnit.DAYS.between(now.toLocalDate(), at.toLocalDate())
    val time = at.format(LocaleFormats.date("hmma", "h:mm a"))
    val day = when (days) {
        0L -> time
        -1L -> AppText.get(R.string.appr_rem_yesterday, time)
        1L -> AppText.get(R.string.appr_rem_tomorrow, time)
        else -> at.format(LocaleFormats.date("EEEdMMMhmma", "EEE d MMM, h:mm a"))
    }
    val what = when (r.kind) {
        io.github.murtazanasir.mailr.data.reminders.Kind.TASK -> AppText.get(R.string.appr_rem_due, day)
        io.github.murtazanasir.mailr.data.reminders.Kind.EVENT -> AppText.get(if (at.isBefore(now)) R.string.appr_rem_started else R.string.appr_rem_starts, day)
        else -> day
    }
    return if (r.missed) AppText.get(R.string.appr_rem_missed, what) else what
}

/** One reminder; a tap opens its sheet with every action the phone has. */
@Composable
private fun ReminderRow(r: ReminderEntity, onOpen: (ReminderEntity) -> Unit) {
    val icon = when (r.kind) {
        io.github.murtazanasir.mailr.data.reminders.Kind.TASK -> Icons.Outlined.TaskAlt
        io.github.murtazanasir.mailr.data.reminders.Kind.EVENT -> Icons.Outlined.Event
        else -> Icons.AutoMirrored.Outlined.StickyNote2
    }
    Surface(shape = RoundedCornerShape(12.dp), color = MaterialTheme.colorScheme.surfaceContainer,
        modifier = Modifier.widthIn(max = 640.dp).fillMaxWidth().testTag("waiting-reminder")) {
        Row(Modifier.clickable(onClickLabel = stringResource(R.string.appr_open_reminder)) { onOpen(r) }.padding(horizontal = 12.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically) {
            Icon(icon, null, Modifier.size(22.dp), tint = if (r.missed) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.primary)
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(r.title.ifBlank { stringResource(R.string.appr_no_title) }, style = MaterialTheme.typography.bodyMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(reminderLine(r), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

@Composable
private fun HistoryRow(r: ApprovalRecord) {
    val (icon, tint) = outcomeLook(r.status)
    Row(Modifier.widthIn(max = 640.dp).fillMaxWidth().testTag("decided-${r.id}"), verticalAlignment = Alignment.CenterVertically) {
        Surface(shape = CircleShape, color = MaterialTheme.colorScheme.surfaceContainerHigh, modifier = Modifier.size(32.dp)) {
            Box(contentAlignment = Alignment.Center) { Icon(icon, null, Modifier.size(18.dp), tint = tint) }
        }
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(r.preview.title.ifBlank { r.action }, style = MaterialTheme.typography.bodyMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
            val whenText = Format.parse(r.settledAt)?.let { Format.relative(it) }
            Text(listOfNotNull(r.outcome, r.actor.ifBlank { null }, whenText).joinToString(" · "),
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            r.error?.let {
                Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error, maxLines = 2,
                    overflow = TextOverflow.Ellipsis)
            }
        }
    }
}

@Composable
private fun outcomeLook(status: String): Pair<ImageVector, androidx.compose.ui.graphics.Color> = when (status) {
    "executed" -> Icons.Outlined.CheckCircle to MaterialTheme.colorScheme.primary
    "approved", "executing" -> Icons.Outlined.Sync to MaterialTheme.colorScheme.primary
    "failed" -> Icons.Outlined.ErrorOutline to MaterialTheme.colorScheme.error
    "denied" -> Icons.Outlined.Block to MaterialTheme.colorScheme.onSurfaceVariant
    else -> Icons.Outlined.HourglassEmpty to MaterialTheme.colorScheme.onSurfaceVariant
}

/** The route on top of [nav]'s back stack, for where the banner stays hidden. */
@Composable
fun currentRoute(nav: NavController): String? = nav.currentBackStackEntryAsState().value?.destination?.route
