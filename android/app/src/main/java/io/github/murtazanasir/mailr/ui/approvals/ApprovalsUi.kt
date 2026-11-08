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
import androidx.compose.material.icons.outlined.ArrowDropDown
import androidx.compose.material.icons.outlined.MoreTime
import androidx.compose.material.icons.outlined.ExpandLess
import androidx.compose.material.icons.outlined.ExpandMore
import androidx.compose.runtime.mutableStateMapOf
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

/** Kinds that never join a group: a review or a cleanup plan is answered on its own card. */
private val UNGROUPED_KINDS = setOf("review", "cleanup")

private fun groupable(a: Approval) = a.outboxId == null && a.status == "pending" && a.preview.kind != "review" && a.kind !in UNGROUPED_KINDS

/**
 * The waiting cards as they are listed (newest first), cut into runs: CONSECUTIVE requests from one
 * requester (one chat, one token, the assistant) of one kind ("task_new") form one group under a header
 * with Allow all and Deny all; any other card is a run of one. Outbox rows, reviews and cleanup plans
 * are never grouped. The order of the list is kept.
 */
fun approvalRuns(shown: List<Approval>): List<List<Approval>> {
    val out = mutableListOf<MutableList<Approval>>()
    for (a in shown) {
        val last = out.lastOrNull()
        val prev = last?.last()
        if (prev != null && groupable(a) && groupable(prev) && prev.requesterKey == a.requesterKey && prev.kind == a.kind) last.add(a)
        else out.add(mutableListOf(a))
    }
    return out
}

/** The groups among [approvalRuns]: runs of two or more. */
fun approvalGroups(shown: List<Approval>): List<List<Approval>> = approvalRuns(shown).filter { it.size > 1 }

/** A group's requests in the order they were asked in (oldest first), as Allow all and Deny all send them. */
fun oldestFirst(group: List<Approval>): List<Approval> = group.sortedWith(compareBy<Approval>({ it.createdAt ?: "" }, { it.id }))

/** "this chat", "Claude Code", "Ambrose": who a group of requests came from. */
fun groupSource(a: Approval): String = when {
    a.chatSessionId != null -> AppText.get(R.string.appr_source_this_chat)
    a.subject == "assistant" || a.subject.startsWith("session:") -> io.github.murtazanasir.mailr.i18n.AssistantName.now()
    else -> a.actor.ifBlank { AppText.get(R.string.appr_source_an_agent) }
}

/** Who a group header names: the agent's token name, else the assistant (a chat's requests are the assistant's). */
fun groupWho(a: Approval): String = when {
    a.chatSessionId != null || a.subject == "assistant" || a.subject.startsWith("session:") -> io.github.murtazanasir.mailr.i18n.AssistantName.now()
    else -> a.actor.ifBlank { AppText.get(R.string.appr_source_an_agent) }
}

/** "claude-code wants to add 2 tasks": the server's words for the group's kind (approvals.group.*), else the generic ones. */
fun groupHeader(group: List<Approval>): String {
    val first = group.first()
    val params = kotlinx.serialization.json.buildJsonObject {
        put("who", kotlinx.serialization.json.JsonPrimitive(groupWho(first)))
        put("count", kotlinx.serialization.json.JsonPrimitive(group.size))
    }
    val text = { key: String -> io.github.murtazanasir.mailr.ui.present.ServerText.text(io.github.murtazanasir.mailr.ui.present.Msg(key, params), "") }
    return text("approvals.group.${first.kind}").ifBlank { text("approvals.group.request") }
        .ifBlank { AppText.plural(R.plurals.appr_requests_from, group.size, group.size, groupWho(first)) }
}

/** The line after "Allow all": "Allowed 3 requests." and the ones already decided elsewhere. */
fun allowedText(count: Int, skipped: Int, approve: Boolean = true): String =
    AppText.plural(if (approve) R.plurals.appr_allowed_count else R.plurals.appr_denied_count, count, count) +
        if (skipped > 0) AppText.plural(R.plurals.appr_skipped, skipped, skipped) else ""

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
    /** Allow all (true) or Deny all (false) of a group of requests from one requester (ACT approval.approve_many). */
    onDecideMany: ((List<Approval>, Boolean) -> Unit)? = null,
    /** Not sent: the messages that could not be sent, with Retry, and Open Outbox. */
    notSent: List<OutboxRow> = emptyList(),
    onRetry: (OutboxRow) -> Unit = {},
    onOutbox: (() -> Unit)? = null,
    /** Reminders showing now and missed ones: a tap opens the reminder's sheet; Review all opens them one by one. */
    reminders: List<ReminderEntity> = emptyList(),
    onReminder: (ReminderEntity) -> Unit = {},
    onReviewAll: (() -> Unit)? = null,
) {
    // Deny all asks first: a denial cannot be undone. Allow all needs no second step, since its cards are listed under it.
    var denying by remember { mutableStateOf<List<Approval>?>(null) }
    denying?.let { group ->
        AlertDialog(
            onDismissRequest = { denying = null },
            title = { Text(pluralStringResource(R.plurals.appr_deny_all_title, group.size, group.size)) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(6.dp), modifier = Modifier.testTag("deny-all-list")) {
                    oldestFirst(group).forEach { a -> Text(a.heading, style = MaterialTheme.typography.bodyMedium, maxLines = 2, overflow = TextOverflow.Ellipsis) }
                }
            },
            // A touch through another app's overlay never decides the group.
            confirmButton = { TextButton(onClick = { denying = null; onDecideMany?.invoke(oldestFirst(group), false) },
                modifier = Modifier.refuseObscuredTouches().testTag("deny-all-confirm")) { Text(stringResource(R.string.appr_deny_all)) } },
            dismissButton = { TextButton(onClick = { denying = null }) { Text(stringResource(R.string.appr_cancel)) } },
        )
    }
    // A group's open or folded state, by its newest card; a group of up to three starts open.
    val folded = remember { mutableStateMapOf<String, Boolean>() }
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
                    val runs = approvalRuns(sorted)
                    fun open(g: List<Approval>) = folded[NotifyDecisions.approvalKey(g.first())]?.not()
                        ?: (g.size <= 3 || g.any { NotifyDecisions.approvalKey(it) == focus })
                    LaunchedEffect(focus, sorted) {
                        // The row of the focused card: + 1 for the header, + 1 for each group header and folded list before it.
                        var at = 1
                        for (r in runs) {
                            if (r.size > 1) at++
                            val i = r.indexOfFirst { NotifyDecisions.approvalKey(it) == focus }
                            if (i >= 0) { list.animateScrollToItem(at + i); break }
                            at += if (r.size > 1 && !open(r)) 1 else r.size
                        }
                    }
                    LazyColumn(state = list, contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp),
                        horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.fillMaxSize().testTag("approvals-list")) {
                        if (sorted.isNotEmpty() || (notSent.isEmpty() && reminders.isEmpty())) item(key = "h-pending") {
                            // Nothing to approve is a line of text, not a heading over an empty section.
                            if (sorted.isEmpty()) Text(stringResource(R.string.appr_no_approvals_waiting), style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp))
                            else Header(stringResource(R.string.appr_header_approvals, sorted.size))
                        }
                        for (run in runs) {
                            val gk = NotifyDecisions.approvalKey(run.first())
                            if (run.size > 1) {
                                val isOpen = open(run)
                                item(key = "g-$gk") {
                                    GroupHeader(run, isOpen, onToggle = { folded[gk] = isOpen },
                                        onAllow = onDecideMany?.let { f -> { f(oldestFirst(run), true) } },
                                        onDeny = onDecideMany?.let { { denying = run } },
                                        onDecide = onDecide)
                                }
                                if (!isOpen) {
                                    item(key = "f-$gk") { FoldedGroup(run) }
                                    continue
                                }
                            }
                            items(run, key = { "p-" + NotifyDecisions.approvalKey(it) }) { a ->
                                val focused = NotifyDecisions.approvalKey(a) == focus
                                Box(Modifier.widthIn(max = 640.dp).fillMaxWidth().testTag("approval-" + NotifyDecisions.approvalKey(a))
                                    .then(if (focused) Modifier.border(BorderStroke(2.dp, MaterialTheme.colorScheme.primary), RoundedCornerShape(14.dp))
                                    else Modifier)) {
                                    card(a, onDecide)
                                }
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

/**
 * A group's header: "claude-code wants to add 2 tasks", with Allow all and Deny all (each request decided
 * once, as its own card would be; ACT approval.approve_many @android.approvals, ACT approval.deny @android.approvals),
 * the group's grant where its first card offers one (the same rule its card's menu makes, which covers the
 * others too), and a toggle that folds the cards into one line each.
 */
@Composable
private fun GroupHeader(group: List<Approval>, open: Boolean, onToggle: () -> Unit, onAllow: (() -> Unit)?, onDeny: (() -> Unit)?,
                        onDecide: Decide) {
    val toggleDesc = stringResource(if (open) R.string.appr_group_fold else R.string.appr_group_unfold)
    Surface(shape = RoundedCornerShape(12.dp), color = MaterialTheme.colorScheme.surfaceContainerHigh,
        modifier = Modifier.widthIn(max = 640.dp).fillMaxWidth().testTag("approval-group")) {
        Column(Modifier.padding(start = 16.dp, end = 4.dp, top = 6.dp, bottom = 6.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(groupHeader(group), style = MaterialTheme.typography.titleSmall, modifier = Modifier.weight(1f).semantics { heading() })
                IconButton(onClick = onToggle, modifier = Modifier.testTag("approval-group-toggle").semantics { contentDescription = toggleDesc }) {
                    Icon(if (open) Icons.Outlined.ExpandLess else Icons.Outlined.ExpandMore, null)
                }
            }
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (onAllow != null) FilledTonalButton(onClick = onAllow, modifier = Modifier.refuseObscuredTouches().testTag("allow-all")) {
                    Text(stringResource(R.string.appr_allow_all)) }
                if (onDeny != null) TextButton(onClick = onDeny, modifier = Modifier.refuseObscuredTouches().testTag("deny-all")) {
                    Text(stringResource(R.string.appr_deny_all)) }
                Spacer(Modifier.weight(1f))
                group.firstOrNull { it.grants }?.let { a -> GroupGrant(a, onDecide) }
            }
        }
    }
}

/** The group's "+N ▾": "Allow for 12 hours" (or for this chat) on its first card, which approves the others it covers too. */
@Composable
private fun GroupGrant(a: Approval, onDecide: Decide) {
    var open by remember { mutableStateOf(false) }
    val n = a.alsoCovers?.session ?: 0
    val desc = if (n > 0) pluralStringResource(R.plurals.more_allow_menu_also, n, n) else stringResource(R.string.more_allow_menu)
    Box {
        TextButton(onClick = { open = true }, contentPadding = PaddingValues(horizontal = 8.dp),
            modifier = Modifier.testTag("group-grants").semantics { contentDescription = desc }) {
            if (n > 0) Text("+$n") else Icon(Icons.Outlined.MoreTime, null, Modifier.size(18.dp))
            Icon(Icons.Outlined.ArrowDropDown, null, Modifier.size(20.dp))
        }
        io.github.murtazanasir.mailr.look.DropdownMenu(open, { open = false }) {
            io.github.murtazanasir.mailr.look.DropdownMenuItem({
                Column(Modifier.widthIn(max = 320.dp).padding(vertical = 4.dp)) {
                    Text(GrantText.sessionLabel(a) + GrantText.also(n), style = MaterialTheme.typography.bodyLarge)
                    Text(GrantText.sessionHelp(a), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }, { open = false; onDecide(a, true, "session") }, modifier = Modifier.refuseObscuredTouches().testTag("group-grant-session"))
        }
    }
}

/** A folded group: one line per request, newest first, as the cards would be. */
@Composable
private fun FoldedGroup(group: List<Approval>) {
    Column(Modifier.widthIn(max = 640.dp).fillMaxWidth().padding(horizontal = 16.dp).testTag("approval-group-folded"),
        verticalArrangement = Arrangement.spacedBy(4.dp)) {
        group.forEach { a ->
            Text(a.heading, style = MaterialTheme.typography.bodyMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
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
