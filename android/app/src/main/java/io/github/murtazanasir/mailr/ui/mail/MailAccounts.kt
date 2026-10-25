package io.github.murtazanasir.mailr.ui.mail

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.api.ConversationSummary
import io.github.murtazanasir.mailr.data.api.MailAccountInfo
import io.github.murtazanasir.mailr.ui.components.darkSurface

/**
 * Several mail accounts in one mailbox (backend core/mailaccounts.py). The
 * mailbox is unified, so the drawer's heading says "All accounts" (one account: its address); rows
 * show their account's colour dot with two or more accounts; with "Show each account separately"
 * on (set on the web), the drawer lists each account's own folders (views `acct:<id>:<view>`).
 */
object AccountColours {
    /** Light and dark tones of each hue (the web's styles/list.css). */
    private val tones = mapOf(
        "blue" to (0xFF1A73E8 to 0xFF8AB4F8), "orange" to (0xFFE8710A to 0xFFFCAD70),
        "green" to (0xFF188038 to 0xFF81C995), "purple" to (0xFF9334E6 to 0xFFC58AF9),
        "pink" to (0xFFD01884 to 0xFFFF8BCB), "teal" to (0xFF129EAF to 0xFF78D9EC),
        "red" to (0xFFD93025 to 0xFFF28B82), "yellow" to (0xFFB08C00 to 0xFFFDD663),
    )

    fun of(hue: String, dark: Boolean): Color {
        val (light, night) = tones[hue] ?: tones.getValue("blue")
        return Color(if (dark) night else light)
    }
}

/** The title over the unified mailbox: "All accounts" with several, the address with one, else [fallback]. */
fun mailboxHeading(accounts: List<MailAccountInfo>, allAccounts: String, fallback: String): String = when {
    accounts.size > 1 -> allAccounts
    accounts.size == 1 -> accounts[0].address
    else -> fallback
}

/** The view of an account's own folder. */
fun accountView(id: Long, base: String) = "acct:$id:$base"

/** A label seen through one account (its folders below Trash): `acct:<id>:label:<label id>`. */
fun accountLabelView(id: Long, labelId: Long) = "acct:$id:label:$labelId"

/**
 * The labels an account's group lists (the web's accountLabels): those holding its mail (the
 * server counts them in GET /counts), those made in it (`home_account_id`), and the labels above
 * either, so the tree keeps its shape. Sorted by path.
 */
fun accountLabels(labels: List<io.github.murtazanasir.mailr.data.api.Label>, accountId: Long, counts: Map<String, io.github.murtazanasir.mailr.data.api.ViewCount>): List<io.github.murtazanasir.mailr.data.api.Label> {
    val own = labels.filter { it.role == null }
    val byName = own.associateBy { it.name }
    val keep = mutableSetOf<Long>()
    for (l in own) {
        if (l.homeAccountId != accountId && (counts[accountLabelView(accountId, l.id)]?.total ?: 0) == 0) continue
        keep += l.id
        val parts = l.name.split('/')
        for (i in 1 until parts.size) byName[parts.subList(0, i).joinToString("/")]?.let { keep += it.id }
    }
    return own.filter { it.id in keep }.sortedBy { it.name.lowercase() }
}

/** The mail accounts the rows draw their dots from (GET /counts `accounts`); empty: no dots. */
val LocalMailAccounts = compositionLocalOf<List<MailAccountInfo>> { emptyList() }

@Composable
fun AccountDot(account: MailAccountInfo, modifier: Modifier = Modifier, size: Dp = 8.dp, named: Boolean = true) {
    val desc = stringResource(R.string.mail_account_named, account.address)
    Box(
        modifier
            .size(size)
            .background(AccountColours.of(account.color, darkSurface()), CircleShape)
            .then(if (named) Modifier.semantics { contentDescription = desc } else Modifier),
    )
}

/** A row's account dots: only with two or more accounts (the screen provides none inside an
 *  account's own folder, where every row is that account's). */
@Composable
fun RowAccounts(c: ConversationSummary, modifier: Modifier = Modifier) {
    val accounts = LocalMailAccounts.current
    if (accounts.size < 2 || c.accounts.isEmpty()) return
    val byId = accounts.associateBy { it.id }
    val shown = c.accounts.mapNotNull { byId[it] }.take(3)
    if (shown.isEmpty()) return
    Row(modifier.testTag("row-accounts"), verticalAlignment = Alignment.CenterVertically) {
        shown.forEach { AccountDot(it, Modifier.padding(start = 3.dp)) }
    }
}
