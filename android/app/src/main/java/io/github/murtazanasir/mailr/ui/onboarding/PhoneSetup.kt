package io.github.murtazanasir.mailr.ui.onboarding

import io.github.murtazanasir.mailr.i18n.AppText
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.BatteryChargingFull
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.NotificationsActive
import androidx.compose.material.icons.outlined.Bolt
import io.github.murtazanasir.mailr.look.Button
import io.github.murtazanasir.mailr.look.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.push.PushStatus

/** The short setup after the first sign-in on a phone: notifications, battery, the push app.
 *  ACT notification.subscribe @android.setup: the phone's way to turn on notifications and push. */
enum class PhoneSetupItem { NOTIFICATIONS, BATTERY, PUSH }

data class PhoneSetupState(
    /** Android's API level: notifications need a runtime permission from 33 (Android 13). */
    val sdk: Int,
    val notificationsAllowed: Boolean,
    /** The app is exempt from battery optimisation, so new mail and reminders are not delayed. */
    val batteryExempt: Boolean,
    val push: PushStatus,
    val pushApp: String? = null,
    /** What to set in ntfy for the server's own push server (pushServerHint), or null. */
    val pushHint: String? = null,
    /** The live connection is on (data/push/LiveConnection.kt): mail arrives at once without a push app. */
    val live: Boolean = false,
)

/** Whether each item is already done on this phone. */
fun PhoneSetupState.done(item: PhoneSetupItem): Boolean = when (item) {
    PhoneSetupItem.NOTIFICATIONS -> notificationsAllowed
    PhoneSetupItem.BATTERY -> batteryExempt
    PhoneSetupItem.PUSH -> push == PushStatus.CONNECTED || push == PushStatus.REGISTERING || live
}

/** The items this phone shows: all three, in order. Push only matters once notifications do. */
fun phoneSetupItems(s: PhoneSetupState): List<PhoneSetupItem> = PhoneSetupItem.entries.toList()

/** The setup is worth showing: something is not done yet. */
fun phoneSetupNeeded(s: PhoneSetupState): Boolean = phoneSetupItems(s).any { !s.done(it) }

data class PhoneSetupCallbacks(
    val allowNotifications: () -> Unit = {},
    val openBattery: () -> Unit = {},
    val getPushApp: () -> Unit = {},
    val choosePushApp: () -> Unit = {},
    val finish: () -> Unit = {},
    /** Copy the server's ntfy address and open ntfy. */
    val copyPushServer: () -> Unit = {},
    /** No push app: keep a live connection instead. */
    val useLive: () -> Unit = {},
)

private data class ItemText(val icon: ImageVector, val title: String, val body: String, val action: String, val doneText: String)

private fun text(item: PhoneSetupItem, s: PhoneSetupState): ItemText = when (item) {
    PhoneSetupItem.NOTIFICATIONS -> ItemText(Icons.Outlined.NotificationsActive, AppText.get(R.string.onb_notif_title),
        AppText.get(R.string.onb_notif_body), AppText.get(R.string.onb_notif_action), AppText.get(R.string.onb_notif_done))
    PhoneSetupItem.BATTERY -> ItemText(Icons.Outlined.BatteryChargingFull, AppText.get(R.string.onb_battery_title),
        AppText.get(R.string.onb_battery_body),
        AppText.get(R.string.onb_battery_action), AppText.get(R.string.onb_battery_done))
    PhoneSetupItem.PUSH -> ItemText(Icons.Outlined.Bolt, AppText.get(R.string.onb_push_title),
        AppText.get(if (s.push == PushStatus.CHOOSE) R.string.onb_push_body_choose else R.string.onb_push_body),
        AppText.get(if (s.push == PushStatus.CHOOSE) R.string.onb_push_action_choose else R.string.onb_push_action_get),
        if (s.live && s.push != PushStatus.CONNECTED) AppText.get(R.string.onb_push_done_live)
        else AppText.get(R.string.onb_push_done, s.pushApp ?: AppText.get(R.string.onb_push_helper_app)))
}

@Composable
fun PhoneSetupScreen(s: PhoneSetupState, cb: PhoneSetupCallbacks) {
    Box(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.surface)) {
        Column(
            Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding().verticalScroll(rememberScrollState()).padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Spacer(Modifier.height(16.dp))
            Text(stringResource(R.string.onb_title), style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Medium,
                modifier = Modifier.testTag("phone-setup-title"))
            Spacer(Modifier.height(8.dp))
            Text(stringResource(R.string.onb_intro),
                style = MaterialTheme.typography.bodyLarge, color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center,
                modifier = Modifier.widthIn(max = 460.dp))
            Spacer(Modifier.height(24.dp))
            Column(Modifier.widthIn(max = 460.dp).fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                for (item in phoneSetupItems(s)) {
                    val t = text(item, s)
                    val done = s.done(item)
                    Surface(shape = RoundedCornerShape(24.dp), color = MaterialTheme.colorScheme.surfaceContainerLow, tonalElevation = 1.dp,
                        modifier = Modifier.fillMaxWidth().testTag("phone-setup-${item.name.lowercase()}")) {
                        Column(Modifier.padding(20.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Icon(if (done) Icons.Outlined.CheckCircle else t.icon, null, Modifier.size(28.dp),
                                    tint = if (done) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
                                Spacer(Modifier.size(12.dp))
                                Text(t.title, style = MaterialTheme.typography.titleMedium)
                            }
                            Spacer(Modifier.height(8.dp))
                            Text(if (done) t.doneText else t.body, style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant)
                            // The server's own ntfy, with its address to copy (ntfy has no link that sets it).
                            if (item == PhoneSetupItem.PUSH && s.pushHint != null) {
                                Spacer(Modifier.height(8.dp))
                                Text(s.pushHint, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.testTag("phone-setup-push-hint"))
                                androidx.compose.material3.TextButton(onClick = cb.copyPushServer,
                                    modifier = Modifier.testTag("phone-setup-push-copy")) { Text(stringResource(R.string.onb_copy_open_ntfy)) }
                            }
                            if (!done) {
                                Spacer(Modifier.height(12.dp))
                                FilledTonalButton(onClick = when (item) {
                                    PhoneSetupItem.NOTIFICATIONS -> cb.allowNotifications
                                    PhoneSetupItem.BATTERY -> cb.openBattery
                                    PhoneSetupItem.PUSH -> if (s.push == PushStatus.CHOOSE) cb.choosePushApp else cb.getPushApp
                                }, modifier = Modifier.fillMaxWidth().height(48.dp).testTag("phone-setup-${item.name.lowercase()}-action")) { Text(t.action) }
                                if (item == PhoneSetupItem.PUSH && s.push == PushStatus.NO_DISTRIBUTOR) {
                                    // No other app: the live connection, with its cost said before the tap.
                                    Spacer(Modifier.height(4.dp))
                                    androidx.compose.material3.TextButton(onClick = cb.useLive,
                                        modifier = Modifier.fillMaxWidth().testTag("phone-setup-push-live")) {
                                        Text(stringResource(R.string.onb_use_live),
                                            textAlign = TextAlign.Center)
                                    }
                                }
                            }
                        }
                    }
                }
            }
            Spacer(Modifier.height(24.dp))
            Button(onClick = cb.finish, modifier = Modifier.widthIn(max = 460.dp).fillMaxWidth().height(52.dp).testTag("phone-setup-finish")) {
                Text(stringResource(if (phoneSetupNeeded(s)) R.string.onb_done_for_now else R.string.onb_go_inbox))
            }
        }
    }
}
