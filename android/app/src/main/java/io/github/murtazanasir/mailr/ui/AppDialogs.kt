package io.github.murtazanasir.mailr.ui

// Dialogs and small helpers the whole app uses: date and time, the notification rationale, copy, open a link.

import io.github.murtazanasir.mailr.time.AppClock
import android.content.Context
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Box
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CalendarMonth
import androidx.compose.material.icons.outlined.CalendarMonth
import io.github.murtazanasir.mailr.look.DatePicker
import io.github.murtazanasir.mailr.look.DatePickerDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import io.github.murtazanasir.mailr.look.TimePicker
import androidx.compose.material3.rememberDatePickerState
import androidx.compose.material3.rememberTimePickerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import io.github.murtazanasir.mailr.AppGraph
import android.Manifest
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.LocalTime
import java.time.ZoneId
import java.time.ZonedDateTime
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R

@Composable
fun DateTimeDialog(onDismiss: () -> Unit, onPick: (ZonedDateTime) -> Unit) {
    var step by remember { mutableStateOf(0) }
    val date = rememberDatePickerState(initialSelectedDateMillis = AppClock.instant().plusSeconds(86_400).toEpochMilli())
    val time = rememberTimePickerState(8, 0)
    if (step == 0) {
        DatePickerDialog(onDismissRequest = onDismiss,
            confirmButton = { TextButton(onClick = { step = 1 }, enabled = date.selectedDateMillis != null) { Text(stringResource(R.string.ui_shell_next)) } },
            dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.ui_shell_cancel)) } }) { DatePicker(date) }
    } else {
        io.github.murtazanasir.mailr.look.AlertDialog(
            onDismissRequest = onDismiss,
            title = { Text(stringResource(R.string.ui_shell_pick_time)) },
            text = { Box { TimePicker(time) } },
            confirmButton = {
                TextButton(onClick = {
                    val d = Instant.ofEpochMilli(date.selectedDateMillis!!).atZone(ZoneId.of("UTC")).toLocalDate()
                    onPick(ZonedDateTime.of(d, LocalTime.of(time.hour, time.minute), ZoneId.systemDefault()))
                }) { Text(stringResource(R.string.ui_shell_done)) }
            },
            dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.ui_shell_cancel)) } },
        )
    }
}

/** Put [text] on the clipboard (Android 13+ shows its own confirmation). */
fun copyText(ctx: Context, text: String) {
    val cm = ctx.getSystemService(Context.CLIPBOARD_SERVICE) as android.content.ClipboardManager
    cm.setPrimaryClip(android.content.ClipData.newPlainText("mailr", text))
    if (android.os.Build.VERSION.SDK_INT < 33) android.widget.Toast.makeText(ctx, ctx.getString(R.string.ui_shell_copied), android.widget.Toast.LENGTH_SHORT).show()
}

/** A link from anywhere in the app: web, mailto: and tel: open; other schemes are refused (SafeLinks). */
fun openUrl(ctx: Context, url: String) {
    io.github.murtazanasir.mailr.ui.util.SafeLinks.open(ctx, url)
}

/**
 * Ask for notifications once, after sign-in, with the reason first (mail, reminders, approvals).
 * If the user declines, the Calendar and Tasks screens explain it again where it matters.
 */
@Composable
internal fun NotificationRationale(g: AppGraph) {
    if (android.os.Build.VERSION.SDK_INT < 33) return
    val ctx = LocalContext.current
    val prefs by g.settings.prefs.collectAsStateWithLifecycle(null)
    var show by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { }
    LaunchedEffect(prefs?.notificationsAsked) {
        val p = prefs ?: return@LaunchedEffect
        val legacyAsked = ctx.getSharedPreferences("mailr_ui", Context.MODE_PRIVATE).getBoolean("asked_notifications", false)
        if (!p.notificationsAsked && !legacyAsked && !io.github.murtazanasir.mailr.ui.calendar.notificationsAllowed(ctx)) {
            kotlinx.coroutines.delay(1200)
            show = true
        }
    }
    if (!show) return
    fun done() {
        show = false
        scope.launch { g.settings.update { it.copy(notificationsAsked = true) } }
    }
    io.github.murtazanasir.mailr.look.AlertDialog(
        onDismissRequest = ::done,
        icon = { Icon(Icons.Outlined.CalendarMonth, null) },
        title = { Text(stringResource(R.string.ui_shell_notify_title)) },
        text = { Text(stringResource(R.string.ui_shell_notify_text)) },
        confirmButton = { TextButton(onClick = { done(); launcher.launch(android.Manifest.permission.POST_NOTIFICATIONS) }) { Text(stringResource(R.string.ui_shell_continue)) } },
        dismissButton = { TextButton(onClick = ::done) { Text(stringResource(R.string.ui_shell_not_now)) } },
    )
}

/** The activity behind a Compose context (the system chooser of Settings → Push needs one). */
fun Context.findActivity(): android.app.Activity? {
    var c: Context? = this
    while (c is android.content.ContextWrapper) {
        if (c is android.app.Activity) return c
        c = c.baseContext
    }
    return null
}
