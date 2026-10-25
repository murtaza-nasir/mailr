package io.github.murtazanasir.mailr.ui.settings

import io.github.murtazanasir.mailr.util.catching

import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.compose.foundation.clickable
import androidx.compose.foundation.lazy.LazyListScope
import io.github.murtazanasir.mailr.look.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.core.app.NotificationManagerCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import io.github.murtazanasir.mailr.data.settings.Prefs
import io.github.murtazanasir.mailr.data.voice.DictationOrder
import io.github.murtazanasir.mailr.data.voice.VoicePolicy

/**
 * Settings → Reminders: full-screen alerts, Remind again, the Ask button and the morning agenda.
 */
fun LazyListScope.reminderAlertItems(prefs: Prefs, update: ((Prefs) -> Prefs) -> Unit,
                                     server: io.github.murtazanasir.mailr.data.api.RepeatSettings? = null, setRemindAgain: ((Int) -> Unit)? = null) {
    item {
        Toggle(stringResource(R.string.settings_full_screen), stringResource(R.string.settings_full_screen_sub),
            prefs.fullScreenAlerts) { v -> update { it.copy(fullScreenAlerts = v) } }
    }
    if (prefs.fullScreenAlerts) item { FullScreenPermissionRow() }
    item {
        @Composable fun every(n: Int) = pluralStringResource(R.plurals.settings_remind_every, n, n)
        val opts = listOf(0 to stringResource(R.string.settings_remind_off), 5 to every(5), 10 to every(10), 15 to every(15), 30 to every(30))
        // ACT reminder.repeat @android.notification: the server's setting when it has one (one choice for the phone and the web).
        val every = server?.minutes ?: prefs.remindAgain
        val times = stringResource(when (server?.max ?: 3) { 1 -> R.string.settings_remind_once; 2 -> R.string.settings_remind_twice; else -> R.string.settings_remind_thrice })
        val current = opts.firstOrNull { it.first == every }?.second ?: every(every)
        val summary = stringResource(if (server != null) R.string.settings_remind_summary_web else R.string.settings_remind_summary, current, times)
        Choice(stringResource(R.string.settings_remind_again), if (every > 0) summary
            else current, opts.map { it.second to it.first }) { v ->
            update { it.copy(remindAgain = v) }
            if (server != null) setRemindAgain?.invoke(v)
        }
    }
    item {
        Toggle(stringResource(R.string.settings_ask_on_reminders), stringResource(R.string.settings_ask_on_reminders_sub, io.github.murtazanasir.mailr.ui.assistantName()),
            prefs.askOnReminders) { v -> update { it.copy(askOnReminders = v) } }
    }
    item {
        Toggle(stringResource(R.string.settings_morning_agenda), stringResource(R.string.settings_morning_agenda_sub),
            prefs.morningAgenda) { v -> update { it.copy(morningAgenda = v) } }
    }
}

/** Settings → Dictation: which recognizer, listening at once, and hands-free sending. */
fun LazyListScope.dictationItems(prefs: Prefs, update: ((Prefs) -> Prefs) -> Unit) {
    item { Header(stringResource(R.string.settings_header_dictation)) }
    item {
        Choice(stringResource(R.string.settings_speech_to_text), prefs.dictationOrder.label, DictationOrder.entries.map { it.label to it }) { v -> update { it.copy(dictationOrder = v) } }
    }
    item {
        val ctx = LocalContext.current
        val onDevice = remember { VoicePolicy.onDeviceAvailable(ctx) }
        ListItem(headlineContent = { Text(stringResource(R.string.settings_voice_where)) },
            supportingContent = { Text(VoicePolicy.describe(onDevice, prefs.serverDictation, prefs.dictationOrder)) })
    }
    item {
        Toggle(stringResource(R.string.settings_listen_at_once), stringResource(R.string.settings_listen_at_once_sub),
            prefs.dictationListen) { v -> update { it.copy(dictationListen = v) } }
    }
    item {
        Toggle(stringResource(R.string.settings_hands_free), stringResource(R.string.settings_hands_free_sub, io.github.murtazanasir.mailr.ui.assistantName()),
            prefs.dictationHandsFree) { v -> update { it.copy(dictationHandsFree = v) } }
    }
}

/** Android 14+ asks per app before full-screen alerts may show; this row says so and opens the setting. */
@Composable
private fun FullScreenPermissionRow() {
    val ctx = LocalContext.current
    val owner = LocalLifecycleOwner.current
    var allowed by remember { mutableStateOf(true) }
    DisposableEffect(owner) {
        val obs = LifecycleEventObserver { _, e -> if (e == Lifecycle.Event.ON_RESUME) allowed = NotificationManagerCompat.from(ctx).canUseFullScreenIntent() }
        owner.lifecycle.addObserver(obs)
        onDispose { owner.lifecycle.removeObserver(obs) }
    }
    ListItem(headlineContent = { Text(stringResource(R.string.settings_full_screen_permission)) },
        supportingContent = {
            Text(stringResource(if (allowed) R.string.settings_full_screen_allowed else R.string.settings_full_screen_not_allowed),
                color = if (allowed) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.error)
        },
        modifier = Modifier.clickable(enabled = !allowed) { openFullScreenSettings(ctx) })
}

fun openFullScreenSettings(ctx: Context) {
    if (Build.VERSION.SDK_INT >= 34) catching {
        ctx.startActivity(Intent(Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT, Uri.parse("package:${ctx.packageName}"))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
}
