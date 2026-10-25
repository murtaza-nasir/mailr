package io.github.murtazanasir.mailr.ui.settings

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.i18n.LocaleFormats
import io.github.murtazanasir.mailr.i18n.AppText
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.outlined.AdminPanelSettings
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.Person
import androidx.compose.material.icons.outlined.SmartToy
import io.github.murtazanasir.mailr.look.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import io.github.murtazanasir.mailr.look.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import io.github.murtazanasir.mailr.look.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.data.api.ActivityItem
import io.github.murtazanasir.mailr.data.api.activity
import io.github.murtazanasir.mailr.data.api.userMessage
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import java.time.LocalDate
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale

/** What the Activity screen shows. [error]: the last load failed (the list keeps what it had). */
data class ActivityUi(
    val items: List<ActivityItem> = emptyList(),
    val loading: Boolean = true,
    val error: String? = null,
    val more: Boolean = false,
)

/**
 * Settings → Activity: what you, Ambrose, your agents and your administrator
 * changed recently, newest first, grouped by day. Read-only on the phone; Undo is on the web.
 */
@Composable
fun ActivityRoute(g: AppGraph, onBack: () -> Unit) {
    var ui by remember { mutableStateOf(ActivityUi()) }
    var cursor by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    fun load(next: Boolean) = scope.launch {
        ui = ui.copy(loading = true, error = null)
        try {
            val page = g.api.activity(if (next) cursor else null)
            cursor = page.nextCursor
            ui = ActivityUi((if (next) ui.items else emptyList()) + page.items, loading = false, more = page.nextCursor != null)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            ui = ui.copy(loading = false, error = e.userMessage())
        }
    }
    LaunchedEffect(Unit) { load(false) }
    ActivityScreen(ui, onBack, onMore = { load(true) }, onRetry = { load(ui.items.isNotEmpty()) })
}

/** Settings → Activity, read-only (GET /activity). ACT activity.list @android.settings */
@Composable
fun ActivityScreen(ui: ActivityUi, onBack: () -> Unit, onMore: () -> Unit, onRetry: () -> Unit,
                   now: ZonedDateTime = AppClock.now()) {
    Scaffold(
        contentWindowInsets = WindowInsets(0),
        topBar = { TopAppBar(title = { Text(stringResource(R.string.settings_activity)) },
            navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, stringResource(R.string.settings_back)) } }) },
    ) { pad ->
        Box(Modifier.padding(pad).fillMaxSize(), contentAlignment = Alignment.TopCenter) {
            LazyColumn(Modifier.widthIn(max = 720.dp).fillMaxWidth(), contentPadding = PaddingValues(bottom = 32.dp)) {
                item {
                    Text(stringResource(R.string.settings_activity_intro, io.github.murtazanasir.mailr.ui.assistantName()),
                        style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(horizontal = 20.dp, vertical = 8.dp))
                }
                val byDay = ui.items.groupBy { day(it.at, now) }
                byDay.forEach { (label, rows) ->
                    item(key = "h-$label") { Header(label) }
                    items(rows, key = { "${it.id}-${it.at}" }) { ActivityRow(it, now) }
                }
                when {
                    ui.loading -> item { Box(Modifier.fillMaxWidth().padding(24.dp), contentAlignment = Alignment.Center) { CircularProgressIndicator() } }
                    ui.error != null -> item {
                        Column(Modifier.padding(horizontal = 20.dp, vertical = 12.dp)) {
                            Text(stringResource(R.string.settings_activity_error, ui.error), color = MaterialTheme.colorScheme.error)
                            TextButton(onClick = onRetry) { Text(stringResource(R.string.settings_try_again)) }
                        }
                    }
                    ui.items.isEmpty() -> item {
                        Text(stringResource(R.string.settings_activity_empty), Modifier.padding(horizontal = 20.dp, vertical = 16.dp)
                            .semantics { heading() }, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    ui.more -> item {
                        TextButton(onClick = onMore, modifier = Modifier.padding(horizontal = 12.dp)) { Text(stringResource(R.string.settings_activity_more)) }
                    }
                }
            }
        }
    }
}

@Composable
private fun ActivityRow(a: ActivityItem, now: ZonedDateTime) {
    val icon = when (a.who) {
        "you" -> Icons.Outlined.Person
        "ambrose" -> Icons.Outlined.AutoAwesome
        "admin" -> Icons.Outlined.AdminPanelSettings
        else -> Icons.Outlined.SmartToy
    }
    val undone = stringResource(R.string.settings_activity_undone)
    val failed = stringResource(R.string.settings_activity_failed)
    val via = a.via?.title?.takeIf { it.isNotBlank() }?.let { stringResource(R.string.trusted_from_forward, it) }
    val status = buildList {
        add(a.actor)
        add(time(a.at, now))
        if (a.undone) add(undone)
        if (!a.ok) add(failed)
    }.joinToString(" · ") + (via?.let { "\n$it" } ?: "")
    ListItem(
        headlineContent = { Text(a.text) },
        supportingContent = { Text(status) },
        leadingContent = { Icon(icon, null, tint = if (a.who == "admin") MaterialTheme.colorScheme.tertiary else MaterialTheme.colorScheme.onSurfaceVariant) },
    )
}

private fun parse(iso: String): ZonedDateTime? = catching { ZonedDateTime.parse(iso) }.getOrNull()

private fun day(iso: String, now: ZonedDateTime): String {
    val d = parse(iso)?.withZoneSameInstant(now.zone)?.toLocalDate() ?: return AppText.get(R.string.settings_day_earlier)
    val today: LocalDate = now.toLocalDate()
    return when (d) {
        today -> AppText.get(R.string.settings_day_today)
        today.minusDays(1) -> AppText.get(R.string.settings_day_yesterday)
        else -> d.format(if (d.year == today.year) LocaleFormats.date("EEEEdMMMM", "EEEE d MMMM") else LocaleFormats.date("yMMMMd", "d MMMM yyyy"))
    }
}

private fun time(iso: String, now: ZonedDateTime): String {
    val t = parse(iso)?.withZoneSameInstant(now.zone) ?: return ""
    return t.format(LocaleFormats.date("hmma", "h:mm a"))
}
