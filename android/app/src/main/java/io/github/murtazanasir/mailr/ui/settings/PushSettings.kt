package io.github.murtazanasir.mailr.ui.settings

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.i18n.AppText
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import androidx.compose.foundation.lazy.LazyListScope
import io.github.murtazanasir.mailr.look.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import io.github.murtazanasir.mailr.data.push.PushInfo
import io.github.murtazanasir.mailr.data.push.PushStatus

/** Settings → Notifications → Push: what the row shows, and the answer of the last test. */
data class PushUi(val info: PushInfo, val testing: Boolean = false, val testResult: String? = null,
                  /** The server's own ntfy (GET /push/targets `server`), null when it runs none. */
                  val server: io.github.murtazanasir.mailr.data.api.PushServer? = null,
                  /** The live connection is on (data/push/LiveConnection.kt). */
                  val live: Boolean = false)

data class PushCallbacks(
    val retry: () -> Unit = {},
    /** Several push apps, none the default: the system chooser. */
    val choose: () -> Unit = {},
    /** No push app: a page that lists them (ntfy first). */
    val getApp: () -> Unit = {},
    val test: () -> Unit = {},
    /** Copy the server's ntfy address and open ntfy, for its Settings → Default server. */
    val copyServer: () -> Unit = {},
    /** Turn the live connection on or off. */
    val setLive: (Boolean) -> Unit = {},
)

/** The ntfy app (the distributor most people install). */
const val NTFY_PACKAGE = "io.heckel.ntfy"

/**
 * One tap for the one step ntfy cannot take from a link: copy the server's address and open ntfy
 * ([distributor], or the ntfy app), where it goes in Settings → Default server. Without ntfy
 * installed, the page that lists it opens.
 */
fun copyAndOpenNtfy(context: android.content.Context, url: String, distributor: String?) {
    val cm = context.getSystemService(android.content.ClipboardManager::class.java)
    cm?.setPrimaryClip(android.content.ClipData.newPlainText("ntfy server", url))
    val pm = context.packageManager
    val launch = listOfNotNull(distributor, NTFY_PACKAGE).firstNotNullOfOrNull { pm.getLaunchIntentForPackage(it) }
    val intent = launch ?: android.content.Intent(android.content.Intent.ACTION_VIEW, android.net.Uri.parse(PUSH_APPS_URL))
    catching { context.startActivity(intent.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)) }
    android.widget.Toast.makeText(context, context.getString(R.string.settings_ntfy_copied),
        android.widget.Toast.LENGTH_LONG).show()
}

/** Where to get ntfy, the free notification helper app (Play Store and F-Droid). */
const val PUSH_APPS_URL = "https://docs.ntfy.sh/subscribe/phone/"

/**
 * What to tell ntfy about the server's own push server, as numbered steps, or null when nothing
 * is needed: no server of its own, or push already goes through it (the steps then go away and the
 * status line says instant mail works). ntfy has no link that sets its default server, so this one
 * step stays by hand, with the address to copy.
 */
fun pushServerHint(info: PushInfo, server: io.github.murtazanasir.mailr.data.api.PushServer?): String? {
    if (server == null || server.host.isBlank()) return null
    val endpointHost = info.endpoint?.let { catching { java.net.URI(it).authority }.getOrNull() }
    if (info.status == PushStatus.CONNECTED && endpointHost.equals(server.host, ignoreCase = true)) return null
    if (info.status == PushStatus.REGISTERING) return null
    val intro = if (info.status == PushStatus.CONNECTED)
        AppText.get(R.string.settings_push_hint_connected, endpointHost ?: AppText.get(R.string.settings_push_another_server))
    else AppText.get(R.string.settings_push_hint_install)
    return intro + "\n" + AppText.get(R.string.settings_push_steps, server.url)
}

/** The live connection's line: what it does, what it costs, and whether it is needed now. */
fun liveConnectionText(info: PushInfo, on: Boolean): String = when {
    on && info.healthy -> AppText.get(R.string.settings_live_standing_by, info.distributorName ?: AppText.get(R.string.settings_push_helper_app))
    on -> AppText.get(R.string.settings_live_on)
    else -> AppText.get(R.string.settings_live_off)
}

/** Offer the live connection when push does not work, or when it is on. */
fun liveConnectionShown(info: PushInfo, on: Boolean): Boolean = on || (info.status != PushStatus.CONNECTED && info.status != PushStatus.REGISTERING)

/** The status line of the Push row, for each state. */
fun pushStatusText(info: PushInfo, live: Boolean = false): String {
    val app = info.distributorName ?: AppText.get(R.string.settings_push_helper_app)
    if (live && !info.healthy && info.status != PushStatus.REGISTERING)
        return AppText.get(R.string.settings_push_live_instead)
    return when (info.status) {
        PushStatus.CONNECTED -> AppText.get(R.string.settings_push_connected, app)
        PushStatus.REGISTERING -> AppText.get(R.string.settings_push_registering, app)
        PushStatus.NO_DISTRIBUTOR -> AppText.get(R.string.settings_push_no_distributor)
        PushStatus.CHOOSE -> AppText.get(R.string.settings_push_choose)
        PushStatus.FAILED -> info.error?.let { AppText.get(R.string.settings_push_failed_error, it) } ?: AppText.get(R.string.settings_push_failed)
        PushStatus.IDLE -> AppText.get(R.string.settings_push_idle)
    }
}

fun LazyListScope.pushItems(ui: PushUi, cb: PushCallbacks) {
    val info = ui.info
    item(key = "push-status") {
        val action = when (info.status) {
            PushStatus.CONNECTED, PushStatus.REGISTERING -> null
            PushStatus.NO_DISTRIBUTOR -> stringResource(R.string.settings_push_get_ntfy) to cb.getApp
            PushStatus.CHOOSE -> stringResource(R.string.settings_push_choose_action) to cb.choose
            PushStatus.FAILED, PushStatus.IDLE -> stringResource(R.string.settings_push_retry) to cb.retry
        }
        ListItem(headlineContent = { Text(stringResource(R.string.settings_push)) },
            supportingContent = { Text(pushStatusText(info, ui.live),
                color = if (info.status == PushStatus.FAILED) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant) },
            trailingContent = action?.let { (label, go) -> { TextButton(onClick = go, modifier = Modifier.testTag("push-action")) { Text(label) } } },
            modifier = Modifier.testTag("push-row"))
    }
    pushServerHint(info, ui.server)?.let { hint ->
        item(key = "push-server") {
            ListItem(headlineContent = { Text(stringResource(R.string.settings_push_server)) },
                supportingContent = { Text(hint) },
                trailingContent = { TextButton(onClick = cb.copyServer, modifier = Modifier.testTag("push-copy-server")) { Text(stringResource(R.string.settings_push_copy_open)) } },
                modifier = Modifier.testTag("push-server"))
        }
    }
    if (liveConnectionShown(info, ui.live)) item(key = "push-live") {
        // ACT notification.live_connection @android.settings
        ListItem(headlineContent = { Text(stringResource(R.string.settings_live)) },
            supportingContent = { Text(liveConnectionText(info, ui.live)) },
            trailingContent = { io.github.murtazanasir.mailr.look.Switch(checked = ui.live, onCheckedChange = cb.setLive,
                modifier = Modifier.testTag("push-live-switch")) },
            modifier = Modifier.testTag("push-live"))
    }
    if (info.status == PushStatus.CONNECTED) item(key = "push-test") {
        ListItem(headlineContent = { Text(stringResource(R.string.settings_push_test)) },
            supportingContent = { Text(ui.testResult ?: stringResource(R.string.settings_push_test_sub, info.distributorName ?: stringResource(R.string.settings_push_helper_app))) },
            trailingContent = { TextButton(onClick = cb.test, enabled = !ui.testing, modifier = Modifier.testTag("push-test")) {
                Text(stringResource(if (ui.testing) R.string.settings_push_sending else R.string.settings_push_send_test)) } })
    }
    item(key = "push-events") {
        ListItem(headlineContent = { Text(stringResource(R.string.settings_push_events)) },
            supportingContent = { Text(stringResource(R.string.settings_push_events_sub)) })
    }
}
