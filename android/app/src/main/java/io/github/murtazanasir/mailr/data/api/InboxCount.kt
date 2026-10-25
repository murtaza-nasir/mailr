package io.github.murtazanasir.mailr.data.api

import kotlinx.serialization.Serializable

/**
 * GET /inbox/count: the number by Inbox (ACT settings.inbox_count @android.drawer). With the `inbox_count`
 * setting "first_tab" (the default) it is the unread conversations the active layout's first tab
 * lists, the same number as that tab's own unread indicator; with "all", every unread
 * conversation in the Inbox.
 */
@Serializable
data class InboxCount(
    val count: Int = 0,
    val mode: String = "first_tab",
    val layout: String? = null,
    /** The counted tab (the layout's first); null with "all". */
    val tab: String? = null,
)

/**
 * The number by Inbox in the drawer and on the Mail tab: the server's Inbox count, or the Inbox
 * label's unread count when there is none (a server without GET /inbox/count).
 */
fun inboxBadge(count: InboxCount?, labels: List<Label>): Int =
    count?.count ?: labels.firstOrNull { it.role == "inbox" }?.unread ?: 0
