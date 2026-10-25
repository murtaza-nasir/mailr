package io.github.murtazanasir.mailr.ui.calendar

import io.github.murtazanasir.mailr.util.catching

import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.AlarmOff
import androidx.compose.material.icons.outlined.CloudOff
import androidx.compose.material.icons.outlined.NotificationsOff
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.compose.ui.unit.dp
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import io.github.murtazanasir.mailr.ui.graph

fun notificationsAllowed(ctx: Context): Boolean = NotificationManagerCompat.from(ctx).areNotificationsEnabled() &&
    (Build.VERSION.SDK_INT < 33 || ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED)

fun openNotificationSettings(ctx: Context, channel: String? = null) {
    val i = if (channel != null) Intent(Settings.ACTION_CHANNEL_NOTIFICATION_SETTINGS)
        .putExtra(Settings.EXTRA_APP_PACKAGE, ctx.packageName).putExtra(Settings.EXTRA_CHANNEL_ID, channel)
    else Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, ctx.packageName)
    catching { ctx.startActivity(i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
}

fun openExactAlarmSettings(ctx: Context) {
    if (Build.VERSION.SDK_INT >= 31) catching {
        ctx.startActivity(Intent(Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM, Uri.parse("package:${ctx.packageName}"))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
}

/** Re-reads permissions whenever the screen resumes (the user may come back from Settings). */
@Composable
private fun rememberResumeTick(): Int {
    var tick by remember { mutableIntStateOf(0) }
    val owner = LocalLifecycleOwner.current
    DisposableEffect(owner) {
        val obs = LifecycleEventObserver { _, e -> if (e == Lifecycle.Event.ON_RESUME) tick++ }
        owner.lifecycle.addObserver(obs)
        onDispose { owner.lifecycle.removeObserver(obs) }
    }
    return tick
}

/**
 * Reminders need notifications and exact alarms. Explain that where it matters (on the
 * Calendar and Tasks screens), with the button that fixes it, instead of asking out of context.
 */
@Composable
fun ReminderBanners(offline: Boolean) {
    val ctx = LocalContext.current
    val tick = rememberResumeTick()
    val notifOk = remember(tick) { notificationsAllowed(ctx) }
    val exactOk = remember(tick) { ctx.graph.alarms.canExact() }
    val ask = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (!granted) openNotificationSettings(ctx)
    }
    Column {
        AnimatedVisibility(!notifOk) {
            Banner(Icons.Outlined.NotificationsOff, stringResource(R.string.cal_banner_reminders_off), stringResource(R.string.cal_banner_reminders_off_body),
                stringResource(R.string.cal_banner_turn_on)) {
                val prefs = ctx.getSharedPreferences("mailr_ui", Context.MODE_PRIVATE)
                if (Build.VERSION.SDK_INT >= 33 && !prefs.getBoolean("asked_notifications_twice", false)) {
                    prefs.edit().putBoolean("asked_notifications_twice", true).apply()
                    ask.launch(Manifest.permission.POST_NOTIFICATIONS)
                } else openNotificationSettings(ctx)
            }
        }
        AnimatedVisibility(notifOk && !exactOk) {
            Banner(Icons.Outlined.AlarmOff, stringResource(R.string.cal_banner_late), stringResource(R.string.cal_banner_late_body),
                stringResource(R.string.cal_banner_allow)) { openExactAlarmSettings(ctx) }
        }
        AnimatedVisibility(offline) {
            Banner(Icons.Outlined.CloudOff, stringResource(R.string.cal_banner_offline), stringResource(R.string.cal_banner_offline_body), null) {}
        }
    }
}

@Composable
private fun Banner(icon: ImageVector, title: String, body: String, action: String?, onAction: () -> Unit) {
    // Reminders that cannot ring, and being offline: the web's warning tint in its look.
    val (bg, fg) = io.github.murtazanasir.mailr.look.bannerColors(io.github.murtazanasir.mailr.look.BannerTone.Warning,
        MaterialTheme.colorScheme.secondaryContainer, MaterialTheme.colorScheme.onSecondaryContainer)
    Surface(color = bg, shape = MaterialTheme.shapes.medium,
        modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp)) {
        Row(Modifier.padding(start = 16.dp, end = 8.dp, top = 10.dp, bottom = 10.dp), verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.Start) {
            Icon(icon, null, tint = fg)
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(title, style = MaterialTheme.typography.titleSmall, color = fg)
                Text(body, style = MaterialTheme.typography.bodySmall, color = fg)
            }
            if (action != null) TextButton(onClick = onAction) { Text(action, color = if (io.github.murtazanasir.mailr.look.look.web) fg else androidx.compose.ui.graphics.Color.Unspecified) }
        }
    }
}
