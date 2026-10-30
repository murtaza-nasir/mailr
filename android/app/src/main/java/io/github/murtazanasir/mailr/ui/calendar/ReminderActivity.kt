package io.github.murtazanasir.mailr.ui.calendar

import io.github.murtazanasir.mailr.util.catching

import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.ui.components.followSecurePrefs
import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.KeyboardArrowLeft
import androidx.compose.material.icons.automirrored.outlined.KeyboardArrowRight
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarDuration
import io.github.murtazanasir.mailr.look.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import io.github.murtazanasir.mailr.data.settings.Prefs
import io.github.murtazanasir.mailr.ui.graph
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * A reminder was tapped.
 *
 * - One notification: its quick-action sheet. Nothing changes until the user acts.
 * - The group summary: the triage queue over every reminder showing, one card each;
 *   acting removes the card and moves on; leaving keeps the rest exactly as they were.
 * - Needs the phone unlocked (a tap on the lock screen asks for it first), so a reminder's details
 *   and actions are never available on a locked phone; Edit and Email hand over to the app.
 *
 * ACT notification.open @android.notification, ACT notification.open_stack @android.notification
 */
class ReminderActivity : ComponentActivity() {
    // The app's language before Android 13; a no-op from 13 on.
    override fun attachBaseContext(newBase: android.content.Context) {
        super.attachBaseContext(io.github.murtazanasir.mailr.i18n.AppLocales.wrap(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.auto(android.graphics.Color.TRANSPARENT, android.graphics.Color.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.auto(android.graphics.Color.TRANSPARENT, android.graphics.Color.TRANSPARENT),
        )
        super.onCreate(savedInstanceState)
        followSecurePrefs(graph.settings.prefs)
        val key = intent.getStringExtra(EXTRA_KEY)
        val stack = intent.getBooleanExtra(EXTRA_STACK, false)
        val expand = intent.getStringExtra(EXTRA_EXPAND)
        if (key == null && !stack) return finish()
        val g = graph
        val actions = QuickActions(g, this)
        setContent {
            val prefs by g.settings.prefs.collectAsStateWithLifecycle(Prefs())
            MailrTheme(prefs.theme, prefs.dynamicColor, prefs.webColours(), prefs.webLook) {
                ReminderQueue(load = {
                    val rows = if (stack) g.reminders.shownQueue() else listOfNotNull(key?.let { g.reminders.reminder(it) })
                    rows.map { QuickActions.target(g, it) }
                }, actions = actions, morningHour = prefs.morningHour, expand = expand, onFinish = ::finish)
            }
        }
    }

    companion object {
        const val EXTRA_KEY = "key"
        const val EXTRA_STACK = "stack"
        const val EXTRA_EXPAND = "expand"

        fun intent(context: Context, key: String? = null, stack: Boolean = false, expand: String? = null): Intent =
            Intent(context, ReminderActivity::class.java).putExtra(EXTRA_KEY, key).putExtra(EXTRA_STACK, stack)
                .putExtra(EXTRA_EXPAND, expand).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_NO_HISTORY)
    }
}

/**
 * The one-by-one triage queue: one [QuickActionCard] per reminder, so every card action is offered here too:
 * ACT task.complete @android.triage, ACT task.snooze @android.triage, ACT task.reschedule @android.triage, ACT task.open_email @android.triage, ACT event.snooze @android.triage,
 * ACT event.remind_before @android.triage, ACT event.reschedule @android.triage, ACT event.rsvp @android.triage, ACT event.open_email @android.triage, ACT reminder.dismiss @android.triage.
 */
@Composable
internal fun ReminderQueue(load: suspend () -> List<SheetTarget>, actions: QuickActions, morningHour: Int, expand: String?,
                          onFinish: () -> Unit) {
    val items = remember { mutableStateListOf<SheetTarget>() }
    var loaded by remember { mutableStateOf(false) }
    var done by remember { mutableStateOf(false) }
    val snack = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    val pager = rememberPagerState { items.size }
    LaunchedEffect(Unit) {
        items.addAll(load())
        loaded = true
        if (items.isEmpty()) onFinish()
    }

    /** Runs an action on the current card, removes it, and offers Undo. */
    fun act(index: Int, block: suspend () -> Done) {
        scope.launch {
            val result = catching { block() }.getOrElse { Done(AppText.get(R.string.cal_could_not_do, it.message ?: AppText.get(R.string.cal_error_fallback)), null) }
            val removed = items.getOrNull(index)
            if (index in items.indices) items.removeAt(index)
            if (items.isEmpty()) done = true
            if (result.message.isNotBlank()) {
                // Undo: 7 s, then the snackbar goes (and a finished queue closes).
                val dismiss = launch { delay(7_000); snack.currentSnackbarData?.dismiss() }
                val r = snack.showSnackbar(result.message, actionLabel = if (result.undo != null) AppText.get(R.string.cal_undo) else null,
                    duration = SnackbarDuration.Indefinite, withDismissAction = result.undo == null)
                dismiss.cancel()
                if (r == SnackbarResult.ActionPerformed) {
                    result.undo?.invoke()
                    removed?.let { items.add(index.coerceIn(0, items.size), it) } // back in the queue, as it was
                    done = false
                    return@launch
                }
            }
            if (done) { delay(300); onFinish() }
        }
    }

    Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha = 0.36f))
        .clickable(remember { MutableInteractionSource() }, null, onClickLabel = stringResource(R.string.cal_close)) { onFinish() }) {
        Column(Modifier.align(Alignment.BottomCenter).windowInsetsPadding(WindowInsets.navigationBars).padding(12.dp)
            .widthIn(max = 600.dp).fillMaxWidth()) {
            SnackbarHost(snack)
            Surface(shape = MaterialTheme.shapes.extraLarge, color = MaterialTheme.colorScheme.surfaceContainerHigh, tonalElevation = 3.dp,
                modifier = Modifier.fillMaxWidth().clickable(remember { MutableInteractionSource() }, null) {}) {
                when {
                    !loaded -> Box(Modifier.heightIn(min = 120.dp))
                    done || items.isEmpty() -> Text(stringResource(R.string.cal_all_caught_up), style = MaterialTheme.typography.titleMedium,
                        modifier = Modifier.padding(24.dp))
                    else -> Column {
                        if (items.size > 1) Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 4.dp, top = 8.dp),
                            verticalAlignment = Alignment.CenterVertically) {
                            Text(stringResource(R.string.cal_page_of, pager.currentPage + 1, items.size), style = MaterialTheme.typography.labelLarge,
                                color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.weight(1f))
                            IconButton(onClick = { scope.launch { pager.animateScrollToPage(pager.currentPage - 1) } },
                                enabled = pager.currentPage > 0) { Icon(Icons.AutoMirrored.Outlined.KeyboardArrowLeft, stringResource(R.string.cal_previous)) }
                            IconButton(onClick = { scope.launch { pager.animateScrollToPage(pager.currentPage + 1) } },
                                enabled = pager.currentPage < items.size - 1) { Icon(Icons.AutoMirrored.Outlined.KeyboardArrowRight, stringResource(R.string.cal_next)) }
                        }
                        HorizontalPager(pager, beyondViewportPageCount = 0, key = { items.getOrNull(it)?.reminder?.key ?: it }) { page ->
                            val target = items.getOrNull(page) ?: return@HorizontalPager
                            QuickActionCard(target, actions, morningHour, if (items.size == 1) expand else null,
                                run = { block -> act(page, block) }, onClose = if (items.size == 1) onFinish else null)
                        }
                    }
                }
            }
        }
    }
}
