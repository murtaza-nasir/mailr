package io.github.murtazanasir.mailr.ui.calendar

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.time.AppClock
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.ui.components.followSecurePrefs
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.Event
import androidx.compose.material.icons.outlined.Mic
import androidx.compose.material.icons.outlined.MoreHoriz
import androidx.compose.material.icons.outlined.NotificationsOff
import androidx.compose.material.icons.outlined.Snooze
import androidx.compose.material.icons.outlined.TaskAlt
import androidx.compose.material.icons.outlined.Videocam
import io.github.murtazanasir.mailr.look.Button
import io.github.murtazanasir.mailr.look.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.lifecycleScope
import io.github.murtazanasir.mailr.data.agent.AskAbout
import io.github.murtazanasir.mailr.data.cal.JoinLink
import io.github.murtazanasir.mailr.data.db.ReminderEntity
import io.github.murtazanasir.mailr.data.reminders.Kind
import io.github.murtazanasir.mailr.data.reminders.RState
import io.github.murtazanasir.mailr.data.settings.Prefs
import io.github.murtazanasir.mailr.ui.ask.AskIntents
import io.github.murtazanasir.mailr.ui.graph
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.util.Locale

/**
 * The full-screen alert (android.fullscreen): an imminent event or a task due now,
 * when the user turned full-screen alerts on. It shows over the lock screen and turns the screen
 * on. It only shows: the reminder stays in the shade until one of its actions is taken,
 * and Close or Back leave it exactly as it was. Ask and Join need the phone unlocked.
 *
 * ACT reminder.full_screen @android.fullscreen, ACT task.complete @android.fullscreen, ACT task.snooze @android.fullscreen, ACT event.snooze @android.fullscreen, ACT event.join @android.fullscreen,
 * ACT reminder.dismiss @android.fullscreen, ACT agent.ask_about @android.fullscreen
 */
class ReminderAlertActivity : ComponentActivity() {
    // The app's language before Android 13; a no-op from 13 on.
    override fun attachBaseContext(newBase: android.content.Context) {
        super.attachBaseContext(io.github.murtazanasir.mailr.i18n.AppLocales.wrap(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        followSecurePrefs(graph.settings.prefs)
        if (Build.VERSION.SDK_INT >= 27) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        }
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        val key = intent.getStringExtra(EXTRA_KEY) ?: return finish()
        val g = graph
        setContent {
            val prefs by g.settings.prefs.collectAsStateWithLifecycle(Prefs())
            var row by remember { mutableStateOf<ReminderEntity?>(null) }
            var join by remember { mutableStateOf<String?>(null) }
            var now by remember { mutableLongStateOf(AppClock.millis()) }
            LaunchedEffect(key) {
                while (true) {
                    val r = g.reminders.reminder(key)
                    // Handled here, on another device or in the shade: the screen goes too.
                    if (r == null || r.state != RState.SHOWN) { finish(); return@LaunchedEffect }
                    if (row == null && r.kind == Kind.EVENT) join = g.calendar.eventsOf(r.itemUid)
                        .let { all -> all.firstOrNull { it.occurrence == r.occurrence } ?: all.firstOrNull() }
                        ?.let { JoinLink.find(it.url, it.location, it.description) }
                    row = r
                    now = AppClock.millis()
                    delay(5_000)
                }
            }
            MailrTheme(prefs.theme, prefs.dynamicColor, prefs.webColours(), prefs.webLook) {
                row?.let { r ->
                    fun act(block: suspend () -> Unit) {
                        lifecycleScope.launch { catching { block() }; finish() }
                    }
                    ReminderAlertScreen(r, join, now, AlertCallbacks(
                        done = { act { g.reminders.done(r.key) } },
                        snooze = { choice -> act { g.reminders.snooze(r.key, choice, prefs.morningHour) } },
                        dismiss = { act { g.reminders.dismiss(r.key) } },
                        join = { url -> io.github.murtazanasir.mailr.ui.util.SafeLinks.intentFor(this, url)?.let { AskIntents.startUnlocked(this, it) } },
                        more = {
                            startActivity(ReminderActivity.intent(this, key = r.key))
                            finish()
                        },
                        ask = {
                            val about = if (r.kind == Kind.EVENT) AskAbout.Event(r.itemUid, r.occurrence, r.title) else AskAbout.Task(r.itemUid, r.title)
                            AskIntents.startUnlocked(this, AskIntents.capture(this, about, from = "fullscreen"))
                        },
                        close = ::finish,
                    ))
                }
            }
        }
    }

    companion object {
        const val EXTRA_KEY = "key"

        fun intent(context: Context, key: String): Intent = Intent(context, ReminderAlertActivity::class.java).putExtra(EXTRA_KEY, key)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_NO_USER_ACTION)
    }
}

data class AlertCallbacks(
    val done: () -> Unit,
    val snooze: (String) -> Unit,
    val dismiss: () -> Unit,
    val join: (String) -> Unit,
    val more: () -> Unit,
    val ask: () -> Unit,
    val close: () -> Unit,
)

/** The alert itself: the kind, the time now, the title, when and where, and large buttons at thumb height. */
@Composable
fun ReminderAlertScreen(r: ReminderEntity, join: String?, now: Long, cb: AlertCallbacks, zone: ZoneId = ZoneId.systemDefault()) {
    BackHandler(onBack = cb.close)
    val cs = MaterialTheme.colorScheme
    val event = r.kind == Kind.EVENT
    val time = DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).withLocale(Locale.getDefault())
    Surface(Modifier.fillMaxSize(), color = cs.surface) {
        Column(Modifier.fillMaxSize().windowInsetsPadding(WindowInsets.safeDrawing).padding(horizontal = 24.dp, vertical = 16.dp),
            horizontalAlignment = Alignment.CenterHorizontally) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Icon(if (event) Icons.Outlined.Event else Icons.Outlined.TaskAlt, null, Modifier.size(20.dp), tint = cs.primary)
                Spacer(Modifier.width(8.dp))
                Text(if (event) stringResource(R.string.rem_event_reminder) else stringResource(R.string.rem_task_reminder), style = MaterialTheme.typography.labelLarge, color = cs.primary,
                    modifier = Modifier.weight(1f))
                IconButton(onClick = cb.close) { Icon(Icons.Outlined.Close, stringResource(R.string.cal_alert_close_cd)) }
            }
            Spacer(Modifier.weight(0.6f))
            Text(Instant.ofEpochMilli(now).atZone(zone).format(time), style = MaterialTheme.typography.displayMedium,
                color = cs.onSurfaceVariant, fontWeight = FontWeight.Light)
            Spacer(Modifier.height(24.dp))
            Box(Modifier.size(72.dp).background(cs.primaryContainer, CircleShape), contentAlignment = Alignment.Center) {
                Icon(if (event) Icons.Outlined.Event else Icons.Outlined.TaskAlt, null, Modifier.size(36.dp), tint = cs.onPrimaryContainer)
            }
            Spacer(Modifier.height(20.dp))
            Text(r.title.ifBlank { if (event) stringResource(R.string.rem_event_reminder) else stringResource(R.string.rem_task_reminder) }, style = MaterialTheme.typography.headlineMedium,
                textAlign = TextAlign.Center, maxLines = 3, overflow = TextOverflow.Ellipsis, modifier = Modifier.semantics { heading() })
            Spacer(Modifier.height(8.dp))
            Text(lead(r, now), style = MaterialTheme.typography.titleMedium, color = cs.primary, textAlign = TextAlign.Center)
            if (r.detail.isNotBlank()) Text(r.detail, style = MaterialTheme.typography.bodyLarge, color = cs.onSurfaceVariant,
                textAlign = TextAlign.Center, maxLines = 2, overflow = TextOverflow.Ellipsis)
            Spacer(Modifier.weight(1f))
            Column(Modifier.widthIn(max = 480.dp).fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                // The main action: Done for a task, Join (or Dismiss) for an event.
                when {
                    !event -> Big(stringResource(R.string.rem_action_done), Icons.Outlined.CheckCircle, cb.done)
                    join != null -> Big(stringResource(R.string.rem_action_join), Icons.Outlined.Videocam) { cb.join(join) }
                    else -> Big(stringResource(R.string.rem_action_dismiss), Icons.Outlined.NotificationsOff, cb.dismiss)
                }
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Tonal(if (event) stringResource(R.string.rem_action_snooze_5_min) else stringResource(R.string.cal_alert_snooze_10_min), Icons.Outlined.Snooze, Modifier.weight(1f)) {
                        cb.snooze(if (event) "5m" else "10m")
                    }
                    Tonal(stringResource(R.string.rem_action_ask, io.github.murtazanasir.mailr.ui.assistantName()), Icons.Outlined.Mic, Modifier.weight(1f), cb.ask)
                }
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    TextButton(onClick = cb.more, modifier = Modifier.heightIn(min = 48.dp)) {
                        Icon(Icons.Outlined.MoreHoriz, null, Modifier.size(18.dp)); Spacer(Modifier.width(6.dp)); Text(stringResource(R.string.cal_alert_more_choices))
                    }
                    if (!event || join != null) TextButton(onClick = cb.dismiss, modifier = Modifier.heightIn(min = 48.dp)) { Text(stringResource(R.string.rem_action_dismiss)) }
                }
            }
        }
    }
}

/** "Starts in 8 min", "Started 2 min ago", "Due now", "Due in 10 min", "10 min overdue". */
internal fun lead(r: ReminderEntity, now: Long): String {
    val at = r.startAt ?: return ""
    val mins = Math.round((at - now) / 60_000.0).toInt()
    return if (r.kind == Kind.EVENT) when {
        mins > 0 -> AppText.plural(R.plurals.cal_alert_starts_in, mins, mins)
        mins == 0 -> AppText.get(R.string.cal_alert_starts_now)
        else -> AppText.plural(R.plurals.cal_alert_started_ago, -mins, -mins)
    } else when {
        mins > 0 -> AppText.plural(R.plurals.cal_alert_due_in, mins, mins)
        mins == 0 -> AppText.get(R.string.cal_alert_due_now)
        else -> AppText.plural(R.plurals.cal_alert_overdue, -mins, -mins)
    }
}

@Composable
private fun Big(label: String, icon: ImageVector, onClick: () -> Unit) {
    Button(onClick = onClick, modifier = Modifier.fillMaxWidth().heightIn(min = 64.dp), shape = RoundedCornerShape(20.dp)) {
        Icon(icon, null); Spacer(Modifier.width(10.dp)); Text(label, style = MaterialTheme.typography.titleMedium)
    }
}

@Composable
private fun Tonal(label: String, icon: ImageVector, modifier: Modifier, onClick: () -> Unit) {
    FilledTonalButton(onClick = onClick, modifier = modifier.heightIn(min = 56.dp), shape = RoundedCornerShape(20.dp)) {
        Icon(icon, null, Modifier.size(20.dp)); Spacer(Modifier.width(8.dp)); Text(label, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}
