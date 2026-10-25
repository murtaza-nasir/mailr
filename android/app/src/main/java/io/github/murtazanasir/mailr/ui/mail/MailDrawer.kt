package io.github.murtazanasir.mailr.ui.mail

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.clickable
import androidx.compose.material.icons.outlined.ExpandLess
import androidx.compose.material.icons.outlined.ExpandMore
import androidx.compose.material.icons.outlined.FolderCopy
import androidx.compose.material.icons.outlined.PushPin
import androidx.compose.material.icons.outlined.CreateNewFolder
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material3.IconButton
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.outlined.ArrowDownward
import androidx.compose.material.icons.outlined.ArrowUpward
import androidx.compose.material.icons.outlined.BookmarkAdd
import androidx.compose.material.icons.outlined.BookmarkRemove
import io.github.murtazanasir.mailr.look.DropdownMenu
import io.github.murtazanasir.mailr.look.DropdownMenuItem
import io.github.murtazanasir.mailr.look.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.selected
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.Label
import androidx.compose.material.icons.automirrored.outlined.Send
import androidx.compose.material.icons.outlined.AllInbox
import androidx.compose.material.icons.outlined.BookmarkBorder
import androidx.compose.material.icons.outlined.Work
import androidx.compose.material.icons.outlined.Home
import androidx.compose.material.icons.outlined.Flag
import androidx.compose.material.icons.outlined.Bolt
import androidx.compose.material.icons.outlined.Laptop
import androidx.compose.material.icons.outlined.Folder
import androidx.compose.material.icons.outlined.Mail
import androidx.compose.material.icons.outlined.Notifications
import androidx.compose.material.icons.outlined.FavoriteBorder
import androidx.compose.material.icons.automirrored.outlined.MenuBook
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.Drafts
import androidx.compose.material.icons.outlined.Forum
import androidx.compose.material.icons.outlined.HowToReg
import androidx.compose.material.icons.outlined.Outbox
import androidx.compose.material.icons.outlined.Inbox
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material.icons.automirrored.outlined.LabelImportant
import androidx.compose.material.icons.outlined.LocalOffer
import androidx.compose.material.icons.outlined.People
import androidx.compose.material.icons.outlined.Report
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material.icons.automirrored.outlined.ScheduleSend
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material.icons.outlined.ShoppingBag
import androidx.compose.material.icons.outlined.StarOutline
import androidx.compose.material.icons.outlined.VerifiedUser
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalDrawerSheet
import io.github.murtazanasir.mailr.look.NavigationDrawerItem
import io.github.murtazanasir.mailr.look.NavigationDrawerItemDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.foundation.layout.size
import androidx.compose.material3.LocalTextStyle
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.InboxCount
import io.github.murtazanasir.mailr.data.api.InboxLayout
import io.github.murtazanasir.mailr.data.api.inboxBadge
import io.github.murtazanasir.mailr.data.api.Label
import io.github.murtazanasir.mailr.data.api.MailAccountInfo
import io.github.murtazanasir.mailr.data.api.ViewCount
import io.github.murtazanasir.mailr.data.repo.MailView
import io.github.murtazanasir.mailr.ui.components.darkSurface
import io.github.murtazanasir.mailr.ui.util.Format
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText

private data class Box(
    val view: MailView,
    val icon: ImageVector,
    val count: Int = 0,
    val firstTab: Boolean = false,
    /** The view key a favorite stores for this entry; null when it cannot be one. */
    val favKey: String? = null,
    val tint: Color? = null,
    val depth: Int = 0,
    /** The entry's own words when the list's title says more (an account's folder: "Inbox" under its address). */
    val label: String? = null,
    /** The account a label was made in, drawn as its dot after the name in the unified Labels list. */
    val home: MailAccountInfo? = null,
    /** A folder in an account's group: pinned there or not, for the long-press menu (AccountFolderPins.kt). */
    val pin: PinPlace? = null,
)

/** Where a folder sits in an account's group: its account, pinned or not, and the pinned order. */
data class PinPlace(val accountId: Long, val address: String, val labelId: Long, val pinned: Boolean, val order: List<Long>,
                    /** The folder's full name, for "New folder inside". */
                    val name: String = "")

/**
 * The account groups' pinned folders and More folders (AccountFolderPins.kt): [prefs] from the
 * settings; the long-press menu calls [pin], [unpin] and [move], the More folders row [setMoreOpen].
 */
class DrawerAccountPins(
    val prefs: AccountFolderPrefs = AccountFolderPrefs(),
    val pin: (accountId: Long, labelId: Long, address: String) -> Unit = { _, _, _ -> },
    val unpin: (accountId: Long, labelId: Long) -> Unit = { _, _ -> },
    val move: (accountId: Long, labelId: Long, delta: Int, among: List<Long>) -> Unit = { _, _, _, _ -> },
    val setMoreOpen: (accountId: Long, open: Boolean) -> Unit = { _, _ -> },
    /** "New folder" in the group (NewFolderDialog.kt): makes the label with the account as its home; null offers none. */
    val create: (suspend (accountId: Long, name: String) -> Label)? = null,
)

/**
 * The drawer's Favorites: [keys] is the user's list (null until the settings load);
 * the long-press menu calls [add], [remove] and [move].
 */
class DrawerFavorites(
    val keys: List<String>?,
    val add: (String) -> Unit = {},
    val remove: (String) -> Unit = {},
    val move: (String, Int) -> Unit = { _, _ -> },
)

@Composable
fun MailDrawer(
    labels: List<Label>,
    current: MailView,
    layout: InboxLayout?,
    tabCounts: Map<String, Int>,
    email: String,
    approvals: Int,
    onOpen: (MailView) -> Unit,
    onApprovals: () -> Unit,
    onSettings: () -> Unit,
    favorites: DrawerFavorites = DrawerFavorites(null),
    /** Messages about Favorites (Undo), shown inside the drawer above its scrim. */
    snackbar: SnackbarHostState? = null,
    /** GET /inbox/count (ACT settings.inbox_count @android.drawer); null shows the Inbox label's unread count. */
    inboxCount: InboxCount? = null,
    /** The senders waiting in the Screener while it is on (null: off, no entry). */
    screener: Int? = null,
    onScreener: () -> Unit = {},
    /** ACT draft.outbox @android.drawer: what has not left, or did not (Outbox.kt); the entry shows only while it is not empty. */
    outbox: OutboxCount = OutboxCount(),
    onOutbox: () -> Unit = {},
    /** The mail accounts (GET /counts): the heading says "All accounts" with several (MailAccounts.kt). */
    accounts: List<MailAccountInfo> = emptyList(),
    /** GET /counts views: with "Show each account separately" on they hold each account's folders
     *  (`acct:<id>:<view>`), and the drawer lists them. ACT mail.account_view @android.drawer */
    accountCounts: Map<String, ViewCount> = emptyMap(),
    /** Each account's pinned folders and More folders (AccountFolderPins.kt). */
    accountPins: DrawerAccountPins = DrawerAccountPins(),
) {
    val byRole = labels.associateBy { it.role }
    val inboxUnread = inboxBadge(inboxCount, labels)
    // Inbox tabs sit under Inbox in the drawer, as Gmail's categories do.
    val tabs = layout?.tabs.orEmpty()
    val inbox = Box(MailView("inbox", title = stringResource(R.string.mail_folder_inbox)), Icons.Outlined.Inbox, inboxUnread, favKey = "inbox")
    val boxes = if (tabs.size > 1) {
        tabs.mapIndexed { i, t ->
            Box(MailView("inbox", title = t.name, layout = layout!!.id, tab = t.id), tabIcon(t.icon),
                if (i == 0) inboxUnread else tabCounts[t.id] ?: 0, firstTab = i == 0, favKey = if (i == 0) "inbox" else null)
        }
    } else {
        listOf(inbox)
    }
    fun folder(key: String, icon: ImageVector, count: Int = 0) =
        Box(MailView(key, title = AppText.get(FAVORITE_FOLDERS.getValue(key))), icon, count, favKey = key)
    val system = listOf(
        folder("starred", Icons.Outlined.StarOutline),
        folder("snoozed", Icons.Outlined.Schedule),
        folder("important", Icons.AutoMirrored.Outlined.LabelImportant),
        folder("sent", Icons.AutoMirrored.Outlined.Send),
        folder("scheduled", Icons.AutoMirrored.Outlined.ScheduleSend),
        folder("drafts", Icons.Outlined.Drafts, byRole["drafts"]?.total ?: 0),
        folder("all", Icons.Outlined.AllInbox),
        folder("spam", Icons.Outlined.Report, byRole["junk"]?.unread ?: 0),
        folder("trash", Icons.Outlined.Delete),
    )
    val dark = darkSurface()
    val labelTint = MaterialTheme.colorScheme.onSurfaceVariant
    fun labelBox(lb: Label) = Box(MailView("label:${lb.id}", title = lb.leaf), Icons.AutoMirrored.Outlined.Label, lb.unread,
        favKey = "label:${lb.id}", tint = Format.labelDot(lb.color, dark) ?: labelTint, depth = lb.depth)
    val userLabels = labels.filter { it.role == null && it.showInList != "hide" }
        .filter { it.showInList != "unread" || it.unread > 0 }
        .sortedBy { it.name.lowercase() }
    // Favorites carry the same counts as the entries below them.
    val favKeys = favorites.keys
    val favBoxes = favKeys?.let { keys ->
        val known = (listOf(inbox) + system).associateBy { it.favKey }
        val byId = labels.filter { it.role == null }.associateBy { "label:${it.id}" }
        keys.mapNotNull { k ->
            known[k] ?: byId[k]?.let { labelBox(it).copy(depth = 0) } ?: k.takeIf { it.startsWith("category:") }?.let {
                val c = it.removePrefix("category:")
                FAVORITE_CATEGORIES[c]?.let { title -> Box(MailView(it, title = AppText.get(title)), categoryIcon(c), favKey = it) }
            }
        }
    }
    // "New folder" (the account header's menu) and "New folder inside" (a folder's long press): the
    // account and the parent the dialog starts with.
    var newFolder by remember { mutableStateOf<Pair<MailAccountInfo, String?>?>(null) }
    val byAccount = accounts.associateBy { it.id }
    val menu = FavoriteMenu(favKeys, favorites, accountPins,
        newInside = if (accountPins.create == null) null else { id, parent -> byAccount[id]?.let { newFolder = it to parent } })

    ModalDrawerSheet(drawerContainerColor = MaterialTheme.colorScheme.surfaceContainerLow) {
        androidx.compose.foundation.layout.Box(Modifier.fillMaxHeight()) {
            LazyColumn(Modifier.fillMaxHeight().statusBarsPadding().navigationBarsPadding()) {
                item {
                    Column(Modifier.padding(start = 28.dp, end = 16.dp, top = 20.dp, bottom = 12.dp)) {
                        Text("Mailr", style = MaterialTheme.typography.titleLarge, color = MaterialTheme.colorScheme.primary,
                            fontWeight = FontWeight.Medium)
                        Text(mailboxHeading(accounts, stringResource(R.string.mail_all_accounts), email), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                }
                if (favBoxes != null) {
                    item(key = "fav-h") { Heading(stringResource(R.string.mail_favorites)) }
                    if (favBoxes.isEmpty()) {
                        // As in Office's folder pane: an empty list says how to fill it.
                        item(key = "fav-empty") {
                            Text(stringResource(R.string.mail_favorites_empty), style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.padding(start = 28.dp, end = 16.dp, bottom = 8.dp))
                        }
                    }
                    items(favBoxes, key = { "f-" + it.favKey }) { b ->
                        // Selected only on the view itself: Inbox on its first tab, not on every tab.
                        val on = b.view.view == current.view && current.q.isNullOrBlank() && current.category == null &&
                            (b.view.view != "inbox" || current.tab == null || current.tab == tabs.firstOrNull()?.id)
                        Item(b, on, onOpen, menu)
                    }
                    item(key = "fav-d") { Divider() }
                }
                items(boxes, key = { "b-" + it.view.key }) { b -> Item(b, isCurrent(b, current), onOpen, menu) }
                if (screener != null) {
                    item(key = "screener") {
                        NavigationDrawerItem(
                            label = { Text(stringResource(R.string.mail_screener)) },
                            icon = { Icon(Icons.Outlined.HowToReg, null) },
                            badge = { if (screener > 0) Text("$screener", fontWeight = FontWeight.Bold) },
                            selected = false, onClick = onScreener,
                            modifier = Modifier.padding(NavigationDrawerItemDefaults.ItemPadding),
                        )
                    }
                }
                item { Divider() }
                items(system, key = { "s-" + it.view.key }) { b -> Item(b, isCurrent(b, current), onOpen, menu) }
                // The Outbox is listed only while something is queued or failed.
                if (outbox.total > 0) item(key = "outbox") {
                    NavigationDrawerItem(
                        label = { Text(stringResource(R.string.mail_outbox)) },
                        icon = { Icon(Icons.Outlined.Outbox, null) },
                        badge = { Text("${outbox.total}", color = if (outbox.failed > 0) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
                            fontWeight = FontWeight.Bold) },
                        selected = false, onClick = onOutbox,
                        modifier = Modifier.padding(NavigationDrawerItemDefaults.ItemPadding).testTag("drawer-outbox"),
                    )
                }
                item {
                    NavigationDrawerItem(
                        label = { Text(stringResource(R.string.mail_approvals)) },
                        icon = { Icon(Icons.Outlined.VerifiedUser, null) },
                        badge = { if (approvals > 0) Text("$approvals", color = MaterialTheme.colorScheme.error, fontWeight = FontWeight.Bold) },
                        selected = false, onClick = onApprovals,
                        modifier = Modifier.padding(NavigationDrawerItemDefaults.ItemPadding),
                    )
                }
                // Each account's own folders, while the server counts them (the setting is on).
                val groups = if (accounts.size > 1 && accountCounts.keys.any { it.startsWith("acct:") }) accounts else emptyList()
                if (groups.isNotEmpty()) {
                    item(key = "acct-h") { Divider(); Heading(stringResource(R.string.mail_accounts)) }
                    groups.forEach { a ->
                        item(key = "acct-${a.id}") {
                            AccountHeader(a, if (accountPins.create == null) null else ({ newFolder = a to null }))
                        }
                        val folders = listOf(
                            Triple("inbox", Icons.Outlined.Inbox, R.string.mail_folder_inbox),
                            Triple("sent", Icons.AutoMirrored.Outlined.Send, R.string.mail_folder_sent),
                            Triple("drafts", Icons.Outlined.Drafts, R.string.mail_folder_drafts),
                            Triple("scheduled", Icons.AutoMirrored.Outlined.ScheduleSend, R.string.mail_folder_scheduled),
                            Triple("spam", Icons.Outlined.Report, R.string.mail_folder_spam),
                            Triple("trash", Icons.Outlined.Delete, R.string.mail_folder_trash),
                        )
                        items(folders, key = { "acct-${a.id}-${it.first}" }) { (base, icon, title) ->
                            val v = accountView(a.id, base)
                            val n = accountCounts[v]
                            val count = when (base) { "inbox", "spam" -> n?.unread ?: 0; "drafts", "scheduled" -> n?.total ?: 0; else -> 0 }
                            val folder = stringResource(title)
                            val b = Box(MailView(v, title = stringResource(R.string.mail_account_folder, folder, a.address)), icon, count,
                                depth = 1, label = folder)
                            Item(b, isCurrent(b, current), onOpen, menu)
                        }
                        // The account's folders: labels holding its mail and those made in it, each the
                        // label narrowed to the account. ACT mail.account_label_view @android.drawer
                        // The pinned ones first, then More folders with the rest (AccountFolderPins.kt).
                        val prefs = accountPins.prefs
                        val moreOpen = a.id in prefs.moreOpen
                        val unreadOf = { lb: Label -> accountCounts[accountLabelView(a.id, lb.id)]?.unread ?: 0 }
                        val split = splitAccountFolders(accountLabels(labels, a.id, accountCounts), labels, prefs.pinsOf(a.id), unreadOf,
                            prefs.showUnread, moreOpen, accountLabelOf(current.view, a.id))
                        val order = split.pinned.map { it.id }
                        @Composable fun folderBox(lb: Label, depth: Int, name: String, pinned: Boolean): Box {
                            val v = accountLabelView(a.id, lb.id)
                            return Box(MailView(v, title = stringResource(R.string.mail_account_folder, lb.name, a.address)),
                                Icons.AutoMirrored.Outlined.Label, unreadOf(lb),
                                tint = Format.labelDot(lb.color, dark) ?: labelTint, depth = depth, label = name,
                                pin = PinPlace(a.id, a.address, lb.id, pinned, order, lb.name))
                        }
                        items(split.pinned, key = { "acct-${a.id}-p-${it.id}" }) { lb ->
                            val b = folderBox(lb, 1, lb.name, true)
                            Item(b, isCurrent(b, current), onOpen, menu)
                        }
                        items(split.surfaced, key = { "acct-${a.id}-s-${it.id}" }) { lb ->
                            val b = folderBox(lb, 1, lb.name, false)
                            Item(b, isCurrent(b, current), onOpen, menu)
                        }
                        if (split.rest.isNotEmpty()) {
                            item(key = "acct-${a.id}-more") {
                                MoreFolders(moreOpen, split.restUnread, a.id) { accountPins.setMoreOpen(a.id, !moreOpen) }
                            }
                        }
                        if (moreOpen && split.rest.isNotEmpty()) {
                            // The rest as a tree; a folder whose parent is not beside it shows its whole path.
                            val names = split.rest.map { it.name }.toSet()
                            items(split.rest, key = { "acct-${a.id}-l-${it.id}" }) { lb ->
                                val nested = lb.depth > 0 && lb.name.substringBeforeLast('/') in names
                                val b = folderBox(lb, 2 + if (nested) lb.depth else 0, if (nested) lb.leaf else lb.name, false)
                                Item(b, isCurrent(b, current), onOpen, menu)
                            }
                        }
                    }
                }
                if (userLabels.isNotEmpty()) {
                    item {
                        Divider()
                        Heading(stringResource(R.string.mail_labels))
                    }
                    val accountsById = accounts.associateBy { it.id }
                    items(userLabels, key = { "l-${it.id}" }) { lb ->
                        val b = labelBox(lb).copy(home = lb.homeAccountId?.takeIf { accounts.size > 1 }?.let { accountsById[it] })
                        Item(b, current.view == b.view.view, onOpen, menu)
                    }
                }
                item {
                    Divider()
                    NavigationDrawerItem(
                        label = { Text(stringResource(R.string.mail_settings)) }, icon = { Icon(Icons.Outlined.Settings, null) },
                        selected = false, onClick = onSettings,
                        modifier = Modifier.padding(NavigationDrawerItemDefaults.ItemPadding),
                    )
                    Spacer(Modifier.height(12.dp))
                }
            }
            if (snackbar != null) {
                SnackbarHost(snackbar, Modifier.align(Alignment.BottomCenter).navigationBarsPadding())
            }
        }
    }
    val create = accountPins.create
    newFolder?.let { (a, parent) ->
        if (create != null) {
            val titleOf = { lb: Label -> AppText.get(R.string.mail_account_folder, lb.name, a.address) }
            NewFolderDialog(a.address, labels, parent, onDismiss = { newFolder = null }) { name ->
                val lb = create(a.id, name)
                newFolder = null
                // The new folder opens (the drawer closes with the pick).
                onOpen(MailView(accountLabelView(a.id, lb.id), title = titleOf(lb)))
            }
        }
    }
}

/**
 * An account's heading in the drawer: its dot and address, and ⋮ with "New folder" (a long press on
 * the heading opens the same menu). [onNewFolder] null: the heading alone.
 */
@Composable
private fun AccountHeader(a: MailAccountInfo, onNewFolder: (() -> Unit)?) {
    var open by remember { mutableStateOf(false) }
    val haptic = LocalHapticFeedback.current
    val options = stringResource(R.string.mail_account_options, a.address)
    Row(Modifier.fillMaxWidth()
        // A long press on the heading opens the menu too; TalkBack reaches it through ⋮.
        .then(if (onNewFolder == null) Modifier else Modifier.pointerInput(a.id) {
            detectTapGestures(onLongPress = { haptic.performHapticFeedback(HapticFeedbackType.LongPress); open = true })
        })
        .padding(start = 28.dp, end = 4.dp, top = if (onNewFolder == null) 6.dp else 0.dp, bottom = if (onNewFolder == null) 2.dp else 0.dp)
        .testTag("drawer-account-${a.id}"),
        verticalAlignment = Alignment.CenterVertically) {
        AccountDot(a, named = false)
        Spacer(Modifier.width(10.dp))
        Text(a.address, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
        if (onNewFolder != null) {
            androidx.compose.foundation.layout.Box {
                IconButton(onClick = { open = true }, modifier = Modifier.testTag("drawer-account-menu-${a.id}")) {
                    Icon(Icons.Outlined.MoreVert, options, tint = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                DropdownMenu(open, onDismissRequest = { open = false }) {
                    // ACT mail.account_folder_create @android.drawer
                    DropdownMenuItem(text = { Text(stringResource(R.string.mail_new_folder)) },
                        leadingIcon = { Icon(Icons.Outlined.CreateNewFolder, null) },
                        onClick = { open = false; onNewFolder() })
                }
            }
        }
    }
}

private fun isCurrent(b: Box, current: MailView): Boolean {
    // The inbox opened without a tab shows the first tab.
    val sameTab = b.view.tab == null || b.view.tab == current.tab || (current.tab == null && b.firstTab)
    return b.view.view == current.view && b.view.category == current.category && current.q.isNullOrBlank() && sameTab
}

@Composable
private fun Heading(text: String) {
    Text(text, style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(start = 28.dp, top = 8.dp, bottom = 8.dp).semantics { heading() })
}

/** The long-press menu's state: the stored list and its changes, and the account groups' pins. */
private class FavoriteMenu(val keys: List<String>?, val f: DrawerFavorites, val pins: DrawerAccountPins = DrawerAccountPins(),
                           /** "New folder inside" a folder of an account's group (account id, parent's name); null: not offered. */
                           val newInside: ((Long, String) -> Unit)? = null)

/**
 * An account group's "More folders": the unread mail of the folders inside it while it is closed;
 * a tap opens or closes it (ACT mail.account_folders_more @android.drawer).
 */
@Composable
private fun MoreFolders(open: Boolean, unread: Int, accountId: Long, onToggle: () -> Unit) {
    val cs = MaterialTheme.colorScheme
    val desc = if (!open && unread > 0) stringResource(R.string.mail_more_folders_unread, unread) else stringResource(R.string.mail_more_folders)
    androidx.compose.foundation.layout.Box(Modifier.padding(NavigationDrawerItemDefaults.ItemPadding).padding(start = 16.dp)) {
        Surface(
            color = Color.Transparent, contentColor = cs.onSurfaceVariant, shape = CircleShape,
            modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp).clip(CircleShape).testTag("drawer-more-$accountId")
                .clickable(role = Role.Button, onClickLabel = desc) { onToggle() }
                .semantics(mergeDescendants = true) { contentDescription = desc },
        ) {
            Row(Modifier.padding(start = 16.dp, end = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Outlined.FolderCopy, null)
                Spacer(Modifier.width(12.dp))
                Text(stringResource(R.string.mail_more_folders), maxLines = 1, overflow = TextOverflow.Ellipsis,
                    fontWeight = if (!open && unread > 0) FontWeight.Bold else null, modifier = Modifier.weight(1f))
                if (!open && unread > 0) {
                    Text(if (unread > 999) stringResource(R.string.mail_count_capped) else "$unread", fontWeight = FontWeight.Bold)
                    Spacer(Modifier.width(8.dp))
                }
                Icon(if (open) Icons.Outlined.ExpandLess else Icons.Outlined.ExpandMore, null)
            }
        }
    }
}

/**
 * A drawer entry drawn like NavigationDrawerItem, with a long press: Add to Favorites, Remove
 * from Favorites, Move up / down in Favorites (ACT nav.favorite_add @android.drawer,
 * ACT nav.favorite_remove @android.drawer, ACT nav.favorite_reorder @android.drawer).
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun Item(b: Box, selected: Boolean, onOpen: (MailView) -> Unit, menu: FavoriteMenu) {
    val key = b.favKey?.takeIf { menu.keys != null && canFavorite(it) }
    val pin = b.pin
    var open by remember { mutableStateOf(false) }
    val haptic = LocalHapticFeedback.current
    val cs = MaterialTheme.colorScheme
    val fg = if (selected) cs.onSecondaryContainer else cs.onSurfaceVariant
    // The web's look (layout.css .nav-item): rows from the start edge, rounded at the end, 44 dp,
    // 14 sp names (bold when current), 20 dp icons.
    val web = io.github.murtazanasir.mailr.look.LocalLook.current
    val shape = if (web.web) RoundedCornerShape(topEndPercent = 50, bottomEndPercent = 50) else CircleShape
    androidx.compose.foundation.layout.Box(Modifier.padding(NavigationDrawerItemDefaults.ItemPadding).padding(start = (b.depth * 16).dp)) {
        Surface(
            color = if (selected) cs.secondaryContainer else Color.Transparent,
            contentColor = if (web.web && !selected) web.palette.text else fg,
            shape = shape,
            modifier = Modifier.fillMaxWidth().heightIn(min = if (web.web) 44.dp else 56.dp).clip(shape)
                .semantics { this.selected = selected }
                .combinedClickable(
                    role = Role.Tab,
                    onClick = { onOpen(b.view) },
                    onLongClickLabel = if (key != null) stringResource(R.string.mail_favorites_options) else if (pin != null) stringResource(R.string.mail_folder_options) else null,
                    onLongClick = if (key == null && pin == null) null else ({
                        haptic.performHapticFeedback(HapticFeedbackType.LongPress)
                        open = true
                    }),
                ),
        ) {
            Row(Modifier.padding(start = if (web.web) 26.dp else 16.dp, end = 24.dp), verticalAlignment = Alignment.CenterVertically) {
                Icon(b.icon, null, tint = b.tint ?: if (web.web && !selected) web.palette.text2 else fg,
                    modifier = if (web.web) Modifier.size(20.dp) else Modifier)
                Spacer(Modifier.width(if (web.web) 18.dp else 12.dp))
                Text(b.label ?: b.view.title, maxLines = 1, overflow = TextOverflow.Ellipsis,
                    style = if (web.web) io.github.murtazanasir.mailr.look.WebTheme.style(io.github.murtazanasir.mailr.look.WebTokens.NavItem)
                        .copy(fontWeight = if (selected || b.count > 0) FontWeight.Bold else FontWeight.Normal) else LocalTextStyle.current,
                    modifier = Modifier.weight(1f, fill = b.home == null))
                if (b.home != null) {
                    Spacer(Modifier.width(8.dp))
                    AccountDot(b.home, Modifier.testTag("label-home-${b.home.id}"))
                    Spacer(Modifier.weight(1f))
                }
                if (b.count > 0) {
                    Spacer(Modifier.width(12.dp))
                    Text(if (b.count > 999) stringResource(R.string.mail_count_capped) else "${b.count}", fontWeight = FontWeight.Bold,
                        style = if (web.web) io.github.murtazanasir.mailr.look.WebTheme.style(io.github.murtazanasir.mailr.look.WebTokens.NavCount) else LocalTextStyle.current)
                }
            }
        }
        if (pin != null) {
            // An account group's folder: Pin to <address>, or Unpin and Move up / down
            // (ACT mail.account_folder_pin @android.drawer, ACT mail.account_folder_unpin @android.drawer).
            val i = pin.order.indexOf(pin.labelId)
            DropdownMenu(open, onDismissRequest = { open = false }) {
                val inside = menu.newInside
                if (inside != null && pin.name.isNotEmpty()) {
                    // ACT mail.account_folder_create @android.drawer: a folder inside this one, in the same account.
                    DropdownMenuItem(text = { Text(stringResource(R.string.mail_new_folder_inside)) }, leadingIcon = { Icon(Icons.Outlined.CreateNewFolder, null) },
                        onClick = { open = false; inside(pin.accountId, pin.name) })
                }
                if (!pin.pinned) {
                    DropdownMenuItem(text = { Text(stringResource(R.string.mail_pin_to, pin.address)) }, leadingIcon = { Icon(Icons.Outlined.PushPin, null) },
                        onClick = { open = false; menu.pins.pin(pin.accountId, pin.labelId, pin.address) })
                } else {
                    DropdownMenuItem(text = { Text(stringResource(R.string.mail_unpin)) }, leadingIcon = { Icon(Icons.Outlined.PushPin, null) },
                        onClick = { open = false; menu.pins.unpin(pin.accountId, pin.labelId) })
                    DropdownMenuItem(text = { Text(stringResource(R.string.mail_pin_up)) }, leadingIcon = { Icon(Icons.Outlined.ArrowUpward, null) },
                        enabled = i > 0, onClick = { open = false; menu.pins.move(pin.accountId, pin.labelId, -1, pin.order) })
                    DropdownMenuItem(text = { Text(stringResource(R.string.mail_pin_down)) }, leadingIcon = { Icon(Icons.Outlined.ArrowDownward, null) },
                        enabled = i in 0 until pin.order.size - 1, onClick = { open = false; menu.pins.move(pin.accountId, pin.labelId, 1, pin.order) })
                }
            }
        } else if (key != null) {
            val keys = menu.keys.orEmpty()
            val i = keys.indexOf(key)
            DropdownMenu(open, onDismissRequest = { open = false }) {
                if (i < 0) {
                    DropdownMenuItem(text = { Text(stringResource(R.string.mail_favorites_add)) }, leadingIcon = { Icon(Icons.Outlined.BookmarkAdd, null) },
                        onClick = { open = false; menu.f.add(key) })
                } else {
                    DropdownMenuItem(text = { Text(stringResource(R.string.mail_favorites_remove)) }, leadingIcon = { Icon(Icons.Outlined.BookmarkRemove, null) },
                        onClick = { open = false; menu.f.remove(key) })
                    DropdownMenuItem(text = { Text(stringResource(R.string.mail_favorites_up)) }, leadingIcon = { Icon(Icons.Outlined.ArrowUpward, null) },
                        enabled = i > 0, onClick = { open = false; menu.f.move(key, -1) })
                    DropdownMenuItem(text = { Text(stringResource(R.string.mail_favorites_down)) }, leadingIcon = { Icon(Icons.Outlined.ArrowDownward, null) },
                        enabled = i < keys.size - 1, onClick = { open = false; menu.f.move(key, 1) })
                }
            }
        }
    }
}

@Composable
private fun Divider() {
    HorizontalDivider(Modifier.padding(horizontal = 28.dp, vertical = 8.dp), color = MaterialTheme.colorScheme.outlineVariant)
}

/** Icons for the categories a favorite may name. */
private fun categoryIcon(id: String): ImageVector = when (id) {
    "social" -> Icons.Outlined.People
    "updates" -> Icons.Outlined.Info
    "forums" -> Icons.Outlined.Forum
    "promotions" -> Icons.Outlined.LocalOffer
    "purchases" -> Icons.Outlined.ShoppingBag
    else -> Icons.Outlined.Inbox
}

/** Icons for layout tabs (names from core/layouts.py ICONS). */
internal fun tabIcon(name: String?): ImageVector = when (name) {
    "tag" -> Icons.Outlined.LocalOffer
    "users" -> Icons.Outlined.People
    "info" -> Icons.Outlined.Info
    "message" -> Icons.Outlined.Forum
    "bag" -> Icons.Outlined.ShoppingBag
    "star" -> Icons.Outlined.StarOutline
    "clock" -> Icons.Outlined.Schedule
    "bookmark" -> Icons.Outlined.BookmarkBorder
    "briefcase" -> Icons.Outlined.Work
    "home" -> Icons.Outlined.Home
    "flag" -> Icons.Outlined.Flag
    "zap" -> Icons.Outlined.Bolt
    "laptop" -> Icons.Outlined.Laptop
    "book" -> Icons.AutoMirrored.Outlined.MenuBook
    "heart" -> Icons.Outlined.FavoriteBorder
    "bell" -> Icons.Outlined.Notifications
    "folder" -> Icons.Outlined.Folder
    "mail" -> Icons.Outlined.Mail
    else -> Icons.Outlined.Inbox
}
