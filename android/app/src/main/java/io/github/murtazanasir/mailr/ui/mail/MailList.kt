package io.github.murtazanasir.mailr.ui.mail

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.ui.semantics.Role
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
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.background
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.outlined.Archive
import androidx.compose.material.icons.outlined.CloudOff
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.DeleteForever
import androidx.compose.material.icons.outlined.MoveToInbox
import androidx.compose.material.icons.outlined.OpenInFull
import androidx.compose.material.icons.outlined.RestoreFromTrash
import androidx.compose.material.icons.outlined.Drafts
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.material.icons.outlined.Inbox
import androidx.compose.material.icons.outlined.MarkEmailUnread
import androidx.compose.material.icons.outlined.Menu
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material.icons.outlined.SelectAll
import androidx.compose.material.icons.outlined.WifiOff
import androidx.compose.material.icons.automirrored.outlined.Label
import androidx.compose.material.icons.automirrored.outlined.LabelImportant
import androidx.compose.material.icons.filled.Star
import androidx.compose.material.icons.outlined.Category
import androidx.compose.material.icons.outlined.Notifications
import androidx.compose.material.icons.outlined.NotificationsOff
import androidx.compose.material.icons.outlined.Report
import androidx.compose.material.icons.outlined.Snooze
import androidx.compose.material.icons.outlined.StarOutline
import androidx.compose.material.icons.outlined.Tab
import io.github.murtazanasir.mailr.look.CircularProgressIndicator
import io.github.murtazanasir.mailr.look.DropdownMenu
import io.github.murtazanasir.mailr.look.DropdownMenuItem
import io.github.murtazanasir.mailr.look.ExtendedFloatingActionButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import io.github.murtazanasir.mailr.look.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.paging.LoadState
import androidx.paging.compose.LazyPagingItems
import androidx.paging.compose.itemKey
import io.github.murtazanasir.mailr.data.api.ConversationSummary
import io.github.murtazanasir.mailr.data.api.Label
import io.github.murtazanasir.mailr.data.api.userMessage
import io.github.murtazanasir.mailr.data.repo.MailView
import io.github.murtazanasir.mailr.data.settings.Prefs
import io.github.murtazanasir.mailr.data.settings.SwipeAction
import io.github.murtazanasir.mailr.ui.components.ActionSheet
import io.github.murtazanasir.mailr.ui.components.SheetAction
import io.github.murtazanasir.mailr.ui.components.SheetGroup
import io.github.murtazanasir.mailr.ui.components.Avatar
import io.github.murtazanasir.mailr.ui.components.UserAvatar
import io.github.murtazanasir.mailr.ui.components.ConversationSkeleton
import io.github.murtazanasir.mailr.ui.components.EmptyState
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText

/** Callbacks from the list pane, kept together so the screen stays stateless. */
/**
 * Inbox sections on the phone: [focused] is the section shown
 * alone (its header has Back), [mainId] the tab's main section; [show] shows one alone, [back]
 * returns to all, [grow] toggles "Show more" on a capped section.
 */
data class SectionControls(
    val focused: String?,
    val mainId: String?,
    val show: (String) -> Unit,
    val back: () -> Unit,
    val grow: (String) -> Unit,
)

data class ListActions(
    val openDrawer: () -> Unit,
    val openSearch: () -> Unit,
    val openAccount: () -> Unit,
    val compose: () -> Unit,
    val open: (ConversationSummary) -> Unit,
    val toggle: (Long) -> Unit,
    val clearSelection: () -> Unit,
    val selectAll: (List<Long>) -> Unit,
    val act: (List<Long>, String) -> Unit,
    val star: (ConversationSummary) -> Unit,
    val swipe: (ConversationSummary, SwipeAction) -> Unit,
    val snoozeSelected: () -> Unit,
    val labelSelected: () -> Unit,
    val refresh: () -> Unit,
    /** The shield in the search bar: the Approvals screen. */
    val openApprovals: () -> Unit = {},
    /** The pending-changes banner: the Pending changes sheet. */
    val openPending: () -> Unit = {},
    /** Trash → Delete forever: asks for the confirmation first. */
    val deleteForever: (List<Long>) -> Unit = {},
    /** Show in tab… for the selection (null: the layout has one tab). */
    val showInTabSelected: (() -> Unit)? = null,
    /** Category… for the selection. */
    val categorySelected: (() -> Unit)? = null,
)

@Composable
fun MailListPane(
    view: MailView,
    items: LazyPagingItems<ConversationSummary>,
    sections: List<Section>,
    sectionTitle: String?,
    labels: List<Label>,
    selection: Set<Long>,
    activeId: Long?,
    prefs: Prefs,
    accountName: String,
    accountEmail: String,
    refreshing: Boolean,
    pendingOps: Int,
    snackbar: SnackbarHostState,
    showMenuButton: Boolean,
    actions: ListActions,
    listState: LazyListState = rememberLazyListState(),
    /** The inbox tabs (InboxTabs), under the search bar; hidden while selecting. */
    tabs: (@Composable () -> Unit)? = null,
    /** Agent actions waiting for approval; [waiting] (when given) counts everything the bell counts. */
    pendingApprovals: Int = 0,
    /** What waits for you (approvals, not sent, reminders): the bell in the search bar while above 0. */
    waiting: io.github.murtazanasir.mailr.ui.approvals.WaitingCounts? = null,
    /** The user label the view or tab is built on (MailboxViewModel.contextLabel); its chip is hidden. */
    contextLabelId: Long? = null,
    /** Spam and Trash have their own actions. */
    place: MailPlace = MailPlace.ofView(view.view),
    /** archive_removes_current_label: Archive in a label's list also removes the label ([archiveSlot]). */
    archiveRemovesLabel: Boolean = true,
    /** Changes the server refused or that kept failing; [pendingOps] counts those still waiting. */
    pendingFailed: Int = 0,
    /** The account's setup (GET /me); an empty inbox without a mail account says to finish on the web. */
    setup: io.github.murtazanasir.mailr.data.api.SetupState? = null,
    openSetup: () -> Unit = {},
    /** A search answered from the phone's cache (no network): the list says so. */
    offlineResults: Boolean = false,
    /** Show only one section, Back, Show more (null: no section controls). */
    sectionControls: SectionControls? = null,
) {
    val labelMap = remember(labels) { labels.associateBy { it.id } }
    // Archive or Move to Inbox, by each row's own state.
    val slotOf = { rows: List<ConversationSummary> -> archiveSlot(rows, place, labelMap, contextLabelId, archiveRemovesLabel) }
    val hiddenLabels = remember(view, labels, contextLabelId) { viewLabelIds(view, labels, contextLabelId) }
    val selectionMode = selection.isNotEmpty()
    val expandedFab by remember { derivedStateOf { listState.firstVisibleItemIndex == 0 } }

    Scaffold(
        contentWindowInsets = WindowInsets(0),
        topBar = {
            // The selection bar takes the search bar's place at the same height (a cross-fade inside
            // SearchPill), and the tabs stay under it, so selecting never moves the list (3 Oct 2026).
            Column {
                SearchPill(view, accountName, accountEmail, showMenuButton, actions,
                    waiting ?: io.github.murtazanasir.mailr.ui.approvals.WaitingCounts(approvals = pendingApprovals),
                    selecting = selectionMode) {
                    val chosen = (sections.flatMap { it.items } + items.itemSnapshotList.items).filter { it.id in selection }.distinctBy { it.id }
                    SelectionBar(selection.size, onClose = actions.clearSelection,
                        onSelectAll = { actions.selectAll(items.itemSnapshotList.items.map { it.id } + sections.flatMap { s -> s.items.map { it.id } }) },
                        onAct = { a ->
                            if (a == "delete_forever") actions.deleteForever(selection.toList()) else actions.act(selection.toList(), a)
                        }, onSnooze = actions.snoozeSelected,
                        onLabel = actions.labelSelected, anyUnread = chosen.isEmpty() || chosen.any { it.unread },
                        place = place, state = SelectionState.of(chosen), toInbox = slotOf(chosen) == ArchiveSlot.INBOX,
                        onShowInTab = actions.showInTabSelected, onCategory = actions.categorySelected)
                }
                tabs?.invoke()
            }
        },
        floatingActionButton = {
            if (!selectionMode) {
                ExtendedFloatingActionButton(
                    onClick = actions.compose,
                    expanded = expandedFab,
                    icon = { Icon(Icons.Outlined.Edit, null) },
                    text = { Text(stringResource(R.string.mail_compose)) },
                )
            }
        },
        snackbarHost = { SnackbarHost(snackbar) },
    ) { pad ->
        PullToRefreshBox(isRefreshing = refreshing, onRefresh = actions.refresh, modifier = Modifier.padding(pad).fillMaxSize()) {
            val refreshState = items.loadState.refresh
            val empty = items.itemCount == 0 && sections.all { it.items.isEmpty() }
            when {
                empty && refreshState is LoadState.Loading -> ConversationSkeleton()
                empty && refreshState is LoadState.Error -> EmptyState(
                    Icons.Outlined.WifiOff, stringResource(R.string.mail_cant_load), refreshState.error.userMessage(),
                    action = stringResource(R.string.mail_try_again) to { items.retry() })
                empty && refreshState is LoadState.NotLoading && view.view == "inbox" && view.q.isNullOrBlank() &&
                    io.github.murtazanasir.mailr.ui.onboarding.FinishOnWeb.needed(setup) ->
                    io.github.murtazanasir.mailr.ui.onboarding.FinishOnWebState(setup!!, openSetup)
                empty && refreshState is LoadState.NotLoading -> EmptyState(
                    Icons.Outlined.Inbox, emptyTitle(view), emptyBody(view))
                else -> LazyColumn(state = listState, contentPadding = PaddingValues(bottom = 96.dp), modifier = Modifier.fillMaxSize()) {
                    // A failed refresh over cached mail says the list is not current.
                    if (refreshState is LoadState.Error && !offlineResults) item(key = "stale") { StaleBanner { items.retry() } }
                    if (pendingOps > 0 || pendingFailed > 0) item(key = "pending") { PendingBanner(pendingOps, pendingFailed, actions.openPending) }
                    if (offlineResults) item(key = "offline-results") {
                        Text(stringResource(R.string.mail_search_offline_results), style = MaterialTheme.typography.labelLarge,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp).testTag("offline-results"))
                    }
                    sections.forEach { s ->
                        if (s.items.isNotEmpty()) {
                            item(key = "h-${s.key}") { SectionHeader(s.title, s.total, onShowOnly = sectionControls?.let { c -> { c.show(s.key) } }) }
                            items(s.items, key = { "s-${s.key}-${it.id}" }) { c ->
                                ConversationRow(c, labelMap, c.id in selection, selectionMode, c.id == activeId, prefs.compact,
                                    prefs.swipeStart, prefs.swipeEnd, { actions.open(c) }, { actions.toggle(c.id) },
                                    { actions.toggle(c.id) }, { actions.star(c) }, { a -> actions.swipe(c, a) },
                                    Modifier.animateItem(), hiddenLabelIds = hiddenLabels, place = place, archiveSlot = slotOf(listOf(c)))
                            }
                            if (sectionControls != null && (s.total > s.items.size || s.expanded)) item(key = "more-${s.key}") {
                                SectionMore(s, grow = { sectionControls.grow(s.key) }, showAll = { sectionControls.show(s.key) })
                            }
                        }
                    }
                    if (sectionTitle != null) item(key = "h-rest") {
                        val focused = sectionControls?.focused
                        SectionHeader(sectionTitle, null,
                            onShowOnly = if (focused == null) sectionControls?.mainId?.let { id -> { sectionControls.show(id) } } else null,
                            onBack = if (focused != null) sectionControls.back else null)
                    }
                    items(count = items.itemCount, key = items.itemKey { it.id }) { i ->
                        val c = items[i] ?: return@items
                        ConversationRow(c, labelMap, c.id in selection, selectionMode, c.id == activeId, prefs.compact,
                            prefs.swipeStart, prefs.swipeEnd, { actions.open(c) }, { actions.toggle(c.id) },
                            { actions.toggle(c.id) }, { actions.star(c) }, { a -> actions.swipe(c, a) },
                            Modifier.animateItem(), hiddenLabelIds = hiddenLabels, place = place, archiveSlot = slotOf(listOf(c)))
                    }
                    when (val a = items.loadState.append) {
                        is LoadState.Loading -> item(key = "more") {
                            Box(Modifier.fillMaxWidth().padding(20.dp), contentAlignment = Alignment.Center) {
                                CircularProgressIndicator(Modifier.size(28.dp), strokeWidth = 3.dp)
                            }
                        }
                        is LoadState.Error -> item(key = "more-err") {
                            Row(Modifier.fillMaxWidth().padding(16.dp), horizontalArrangement = Arrangement.Center,
                                verticalAlignment = Alignment.CenterVertically) {
                                Text(a.error.userMessage(), style = MaterialTheme.typography.bodySmall)
                                TextButton(onClick = { items.retry() }) { Text(stringResource(R.string.mail_retry)) }
                            }
                        }
                        else -> Unit
                    }
                }
            }
        }
    }
}

/** The search bar's height, which the selection bar keeps. */
private val PILL_HEIGHT = 56.dp

private fun emptyTitle(v: MailView) = AppText.get(when {
    !v.q.isNullOrBlank() -> R.string.mail_empty_no_results
    v.view == "inbox" -> R.string.mail_empty_inbox
    v.view == "snoozed" -> R.string.mail_empty_snoozed
    v.view == "starred" -> R.string.mail_empty_starred
    v.view == "drafts" -> R.string.mail_empty_drafts
    v.view == "scheduled" -> R.string.mail_empty_scheduled
    v.view == "spam" -> R.string.mail_empty_spam
    v.view == "trash" -> R.string.mail_empty_trash
    else -> R.string.mail_empty_other
})

private fun emptyBody(v: MailView) = when {
    !v.q.isNullOrBlank() -> R.string.mail_empty_body_search
    v.view == "inbox" -> R.string.mail_empty_body_inbox
    v.view == "snoozed" -> R.string.mail_empty_body_snoozed
    else -> null
}?.let { AppText.get(it) }

/** "Show N more" (in place, up to [SECTION_GROWN]), then "Show fewer", and "Show all N" (the section
 *  alone) while it holds more. */
@Composable
private fun SectionMore(s: Section, grow: () -> Unit, showAll: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        if (!s.expanded) {
            TextButton(onClick = grow, modifier = Modifier.testTag("section-show-more")) {
                Text(stringResource(R.string.mail_section_show_more, minOf(s.total, maxOf(s.cap, SECTION_GROWN)) - s.items.size))
            }
        } else {
            if (s.total > s.items.size) TextButton(onClick = showAll, modifier = Modifier.testTag("section-show-all")) {
                Text(stringResource(R.string.mail_section_show_all, s.total))
            }
            TextButton(onClick = grow, modifier = Modifier.testTag("section-show-fewer")) {
                Text(stringResource(R.string.mail_section_show_fewer))
            }
        }
    }
}

@Composable
private fun SectionHeader(title: String, count: Int?, onShowOnly: (() -> Unit)? = null, onBack: (() -> Unit)? = null) {
    val web = io.github.murtazanasir.mailr.look.LocalLook.current
    val showOnly: @Composable () -> Unit = {
        if (onShowOnly != null) IconButton(onClick = onShowOnly, modifier = Modifier.size(32.dp).testTag("section-focus")) {
            Icon(Icons.Outlined.OpenInFull, stringResource(R.string.mail_section_show_only, title), Modifier.size(16.dp))
        }
    }
    // Shown alone, the section has nothing to fold: the arrow and the name are one target, back to
    // all sections (as on the web).
    val backLabel = stringResource(R.string.mail_section_back)
    val backTarget: Modifier = if (onBack != null) Modifier.fillMaxHeight().clickable(onClickLabel = backLabel, role = Role.Button, onClick = onBack)
        .testTag("section-back") else Modifier
    val arrow: @Composable () -> Unit = {
        if (onBack != null) {
            Icon(Icons.AutoMirrored.Filled.ArrowBack, null, Modifier.size(18.dp))
            Spacer(Modifier.width(10.dp))
        }
    }
    if (web.web) {
        // list.css .section-head: a 32 px band on the surface container, the name in the secondary
        // text colour, the count as a small pill, a divider under it.
        Row(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceContainer)
            .then(io.github.murtazanasir.mailr.look.Hairline.bottom(web.palette.divider))
            .height(32.dp).padding(horizontal = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            // The name and its count take the room left; "Show only" stays at the end.
            Row(Modifier.weight(1f).then(backTarget), verticalAlignment = Alignment.CenterVertically) {
                arrow()
                Text(title, style = io.github.murtazanasir.mailr.look.WebTheme.style(io.github.murtazanasir.mailr.look.WebTokens.SectionHead), color = web.palette.text2,
                    maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false).semantics { heading() })
                if (count != null) {
                    Spacer(Modifier.width(8.dp))
                    Box(Modifier.heightIn(min = 20.dp).background(if (web.dark) web.palette.surfaceHigh else web.palette.surface, RoundedCornerShape(50))
                        .padding(horizontal = 7.dp), contentAlignment = Alignment.Center) {
                        Text("$count", style = io.github.murtazanasir.mailr.look.WebTheme.style(io.github.murtazanasir.mailr.look.WebTokens.SectionCount), color = web.palette.text2)
                    }
                }
            }
            showOnly()
        }
        return
    }
    Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 16.dp, top = 16.dp, bottom = 4.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Row(Modifier.weight(1f).then(backTarget), verticalAlignment = Alignment.CenterVertically) {
            arrow()
            Text(title.uppercase(), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary,
                modifier = Modifier.semantics { heading() })
            if (count != null) {
                Spacer(Modifier.width(8.dp))
                Text("$count", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        showOnly()
    }
}

/**
 * The search bar, or the selection bar in its place while [selecting]: both are 56 dp tall in the
 * same slot and cross-fade, so nothing under them moves.
 */
@Composable
private fun SearchPill(view: MailView, name: String, email: String, showMenu: Boolean, actions: ListActions,
                       waiting: io.github.murtazanasir.mailr.ui.approvals.WaitingCounts = io.github.murtazanasir.mailr.ui.approvals.WaitingCounts(),
                       selecting: Boolean = false, selectionBar: @Composable () -> Unit = {}) {
    // The web's look (layout.css .topbar, .search-box): the page's background behind a 48 px box in the input colour.
    val web = io.github.murtazanasir.mailr.look.LocalLook.current
    val pillHeight = if (web.web) 48.dp else PILL_HEIGHT
    Column(Modifier.fillMaxWidth().then(if (web.web) Modifier.background(web.palette.bg) else Modifier)
        .statusBarsPadding().padding(horizontal = 12.dp, vertical = 8.dp)) {
        AnimatedContent(selecting, Modifier.fillMaxWidth().height(pillHeight),
            transitionSpec = { (fadeIn() togetherWith fadeOut()).using(null) }, label = "bar") { sel -> // i18n-ignore: animation label
        if (sel) selectionBar() else Surface(
            shape = RoundedCornerShape(28.dp),
            color = if (web.web) web.palette.inputBg else MaterialTheme.colorScheme.surfaceContainerHigh,
            modifier = Modifier.fillMaxWidth().height(pillHeight).clip(RoundedCornerShape(28.dp)).clickable(onClickLabel = stringResource(R.string.mail_search_mail)) { actions.openSearch() },
        ) {
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(horizontal = 4.dp)) {
                if (showMenu) IconButton(onClick = actions.openDrawer) { Icon(Icons.Outlined.Menu, stringResource(R.string.mail_open_navigation)) }
                else IconButton(onClick = actions.openSearch) { Icon(Icons.Outlined.Search, stringResource(R.string.mail_search_mail)) }
                Text(
                    if (!view.q.isNullOrBlank()) view.q!! else stringResource(R.string.mail_search_in, view.title.lowercase().replaceFirstChar { it.titlecase() }),
                    style = MaterialTheme.typography.bodyLarge, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f).padding(start = 4.dp),
                )
                // "Waiting for you": approvals, messages not sent and reminders (the web's bell).
                io.github.murtazanasir.mailr.ui.approvals.WaitingBell(waiting, actions.openApprovals)
                IconButton(onClick = actions.openAccount) { UserAvatar(name.ifBlank { null }, email, size = 32.dp) }
            }
        }
        }
        if (view.view != "inbox" || view.category != null || !view.q.isNullOrBlank()) {
            Text(view.title, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary,
                modifier = Modifier.padding(start = 16.dp, top = 12.dp).semantics { heading() })
        }
    }
}

/** What the selected conversations are, so the bar offers the opposite. */
data class SelectionState(val anyUnstarred: Boolean = true, val anyNotImportant: Boolean = true, val anyUnmuted: Boolean = true) {
    companion object {
        fun of(chosen: List<ConversationSummary>): SelectionState = if (chosen.isEmpty()) SelectionState() else SelectionState(
            anyUnstarred = chosen.any { !it.starred }, anyNotImportant = chosen.any { !it.important }, anyUnmuted = chosen.any { !it.muted })
    }
}

// Selection bar on the mail list (android.list):
// ACT mail.archive @android.list, ACT mail.delete @android.list ("trash"), ACT mail.read @android.list, ACT mail.unread @android.list, ACT mail.star @android.list.
// In Spam: ACT mail.not_spam @android.list; in Trash: ACT mail.untrash @android.list, ACT mail.delete_forever @android.list.
@Composable
internal fun SelectionBar(count: Int, onClose: () -> Unit, onSelectAll: () -> Unit, onAct: (String) -> Unit,
                          onSnooze: () -> Unit, onLabel: () -> Unit, anyUnread: Boolean,
                          place: MailPlace = MailPlace.OTHER, state: SelectionState = SelectionState(),
                          onShowInTab: (() -> Unit)? = null, onCategory: (() -> Unit)? = null,
                          /** None of the selected rows is in the Inbox: Move to Inbox where Archive sits. */
                          toInbox: Boolean = false,
                          modifier: Modifier = Modifier) {
    var menu by remember { mutableStateOf(false) }
    // The search bar's shape and height (PILL_HEIGHT), in its place: the list under it does not move.
    Surface(shape = RoundedCornerShape(28.dp), color = MaterialTheme.colorScheme.secondaryContainer,
        contentColor = MaterialTheme.colorScheme.onSecondaryContainer,
        modifier = modifier.fillMaxWidth().height(PILL_HEIGHT).testTag("selection-bar")) {
        val selectedDesc = stringResource(R.string.mail_selected, count)
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(horizontal = 4.dp)) {
            IconButton(onClick = onClose) { Icon(Icons.AutoMirrored.Filled.ArrowBack, stringResource(R.string.mail_clear_selection)) }
            Text("$count", style = MaterialTheme.typography.titleLarge, modifier = Modifier.weight(1f).padding(start = 4.dp)
                .semantics { contentDescription = selectedDesc })
            when (place) {
                MailPlace.TRASH -> {
                    IconButton(onClick = { onAct("untrash") }) { Icon(Icons.Outlined.RestoreFromTrash, stringResource(R.string.mail_restore)) }
                    IconButton(onClick = { onAct("delete_forever") }) { Icon(Icons.Outlined.DeleteForever, stringResource(R.string.mail_delete_forever)) }
                }
                MailPlace.SPAM -> {
                    IconButton(onClick = { onAct("not_spam") }) { Icon(Icons.Outlined.MoveToInbox, stringResource(R.string.mail_not_spam)) }
                    IconButton(onClick = { onAct("trash") }) { Icon(Icons.Outlined.Delete, stringResource(R.string.mail_delete)) }
                }
                MailPlace.OTHER -> {
                    // ACT mail.move @android.list (Move to Inbox)
                    if (toInbox) IconButton(onClick = { onAct("inbox") }) { Icon(Icons.Outlined.MoveToInbox, stringResource(R.string.mail_move_to_inbox)) }
                    else IconButton(onClick = { onAct("archive") }) { Icon(Icons.Outlined.Archive, stringResource(R.string.mail_archive)) }
                    IconButton(onClick = { onAct("trash") }) { Icon(Icons.Outlined.Delete, stringResource(R.string.mail_delete)) }
                }
            }
            IconButton(onClick = { onAct(if (anyUnread) "read" else "unread") }) {
                Icon(if (anyUnread) Icons.Outlined.Drafts else Icons.Outlined.MarkEmailUnread, if (anyUnread) stringResource(R.string.mail_mark_read) else stringResource(R.string.mail_mark_unread))
            }
            IconButton(onClick = { menu = true }) { Icon(Icons.Outlined.MoreVert, stringResource(R.string.mail_more_actions)) }
            // The ⋮ menu as a bottom sheet in groups:
            // the common actions first, then Organize, then More.
            if (menu) {
                val other = place == MailPlace.OTHER
                ActionSheet(selectedDesc, listOf(
                    SheetGroup(null, listOfNotNull(
                        SheetAction(stringResource(R.string.mail_select_all), Icons.Outlined.SelectAll, onClick = onSelectAll),
                        if (anyUnread) SheetAction(stringResource(R.string.mail_mark_unread), Icons.Outlined.MarkEmailUnread) { onAct("unread") } else null,
                        if (state.anyUnstarred) SheetAction(stringResource(R.string.mail_star), Icons.Outlined.StarOutline) { onAct("star") }
                        else SheetAction(stringResource(R.string.mail_unstar), Icons.Filled.Star) { onAct("unstar") },
                        // ACT mail.important @android.list (the selection bar's ⋮ sheet)
                        if (state.anyNotImportant) SheetAction(stringResource(R.string.mail_mark_important), Icons.AutoMirrored.Outlined.LabelImportant) { onAct("important") }
                        else SheetAction(stringResource(R.string.mail_mark_not_important), Icons.AutoMirrored.Outlined.LabelImportant) { onAct("not_important") },
                        if (other) SheetAction(stringResource(R.string.mail_snooze), Icons.Outlined.Snooze, onClick = onSnooze) else null,
                    )),
                    SheetGroup(stringResource(R.string.ui_menu_organize), listOfNotNull(
                        SheetAction(stringResource(R.string.mail_label_as), Icons.AutoMirrored.Outlined.Label, onClick = onLabel),
                        // ACT mail.show_in_tab @android.list, ACT mail.category @android.list (TabMenus.kt sheets)
                        if (other) onShowInTab?.let { SheetAction(stringResource(R.string.mail_show_in_tab_menu), Icons.Outlined.Tab, onClick = it) } else null,
                        if (other) onCategory?.let { SheetAction(stringResource(R.string.mail_category_menu), Icons.Outlined.Category, onClick = it) } else null,
                    )),
                    // ACT mail.mute @android.list
                    SheetGroup(stringResource(R.string.ui_menu_more), if (!other) emptyList() else listOf(
                        if (state.anyUnmuted) SheetAction(stringResource(R.string.mail_mute), Icons.Outlined.NotificationsOff) { onAct("mute") }
                        else SheetAction(stringResource(R.string.mail_unmute), Icons.Outlined.Notifications) { onAct("unmute") },
                        SheetAction(stringResource(R.string.mail_report_spam), Icons.Outlined.Report) { onAct("spam") },
                    )),
                ), onDismiss = { menu = false })
            }
        }
    }
}
