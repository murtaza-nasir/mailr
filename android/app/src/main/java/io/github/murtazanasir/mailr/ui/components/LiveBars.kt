package io.github.murtazanasir.mailr.ui.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Close
import io.github.murtazanasir.mailr.look.AlertDialog
import io.github.murtazanasir.mailr.look.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.live.LiveConflict
import io.github.murtazanasir.mailr.data.live.LiveUi
import io.github.murtazanasir.mailr.data.live.LiveWords
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText

/** What the live-editing bars can do (LiveSession). */
data class LiveBarActions(
    val undo: () -> Unit,
    val dismiss: () -> Unit,
    val useMine: () -> Unit,
    val useTheirs: () -> Unit,
)

/**
 * Live editing in an open editor (data/live): "Assistant is editing this draft…" while an agent
 * writes; "Updated by the assistant" with Undo after their copy came in; the conflict bar when both
 * changed the same part ("Use mine", "Use the assistant's", "Compare").
 */
@Composable
fun LiveBars(ui: LiveUi, kind: String, a: LiveBarActions, modifier: Modifier = Modifier) {
    var comparing by remember { mutableStateOf(false) }
    Column(modifier.fillMaxWidth()) {
        ui.editing?.let { by ->
            Bar(MaterialTheme.colorScheme.secondaryContainer) {
                CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 2.dp)
                Spacer(Modifier.width(10.dp))
                Text(LiveWords.editing(by, kind), style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
            }
        }
        ui.conflict?.let { c ->
            Bar(MaterialTheme.colorScheme.tertiaryContainer) {
                Column(Modifier.weight(1f)) {
                    Text(LiveWords.changed(c.by, kind), style = MaterialTheme.typography.bodyMedium)
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                        TextButton(onClick = a.useMine) { Text(stringResource(R.string.ui_live_use_mine)) }
                        TextButton(onClick = a.useTheirs) { Text(LiveWords.useTheirs(c.by)) }
                        TextButton(onClick = { comparing = true }) { Text(stringResource(R.string.ui_live_compare)) }
                    }
                }
            }
            if (comparing) CompareDialog(c, onDismiss = { comparing = false },
                onMine = { comparing = false; a.useMine() }, onTheirs = { comparing = false; a.useTheirs() })
        }
        if (ui.conflict == null) ui.notice?.let { n ->
            // A short message: it goes by itself after a while.
            LaunchedEffect(n) {
                kotlinx.coroutines.delay(if (n.previous != null) 10_000 else 5_000)
                a.dismiss()
            }
            Bar(MaterialTheme.colorScheme.surfaceVariant) {
                Text(LiveWords.updated(n.by), style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
                if (n.previous != null) TextButton(onClick = a.undo) { Text(stringResource(R.string.ui_live_undo)) }
                IconButton(onClick = a.dismiss) { Icon(Icons.Outlined.Close, stringResource(R.string.ui_live_dismiss), Modifier.size(18.dp)) }
            }
        }
        if (ui.gone) Bar(MaterialTheme.colorScheme.surfaceVariant) {
            Text(when (kind) {
                "draft" -> stringResource(R.string.ui_live_gone_draft)
                "event" -> stringResource(R.string.ui_live_gone_event)
                "task" -> stringResource(R.string.ui_live_gone_task)
                else -> stringResource(R.string.ui_live_gone, kind)
            }, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
        }
    }
}

@Composable
private fun Bar(color: androidx.compose.ui.graphics.Color, content: @Composable androidx.compose.foundation.layout.RowScope.() -> Unit) {
    Surface(color = color, modifier = Modifier.fillMaxWidth().semantics { liveRegion = LiveRegionMode.Polite }) {
        Row(Modifier.heightIn(min = 44.dp).padding(start = 16.dp, end = 4.dp, top = 4.dp, bottom = 4.dp),
            verticalAlignment = Alignment.CenterVertically, content = content)
    }
}

/** Both versions of each part that differs. */
@Composable
private fun CompareDialog(c: LiveConflict, onDismiss: () -> Unit, onMine: () -> Unit, onTheirs: () -> Unit) {
    val theirName = LiveWords.theirs(c.by).replaceFirstChar { it.uppercase() }
    AlertDialog(onDismissRequest = onDismiss,
        title = { Text(stringResource(R.string.ui_live_compare)) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                c.rows.forEachIndexed { i, r ->
                    if (i > 0) HorizontalDivider(Modifier.padding(vertical = 8.dp))
                    Text(fieldLabel(r.field), style = MaterialTheme.typography.labelLarge)
                    Spacer(Modifier.height(4.dp))
                    Text(stringResource(R.string.ui_live_yours), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary)
                    Text(r.mine.ifBlank { stringResource(R.string.ui_live_empty) }, style = MaterialTheme.typography.bodyMedium)
                    Spacer(Modifier.height(6.dp))
                    Text(theirName, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.tertiary)
                    Text(r.theirs.ifBlank { stringResource(R.string.ui_live_empty) }, style = MaterialTheme.typography.bodyMedium)
                }
            }
        },
        confirmButton = { TextButton(onClick = onMine) { Text(stringResource(R.string.ui_live_use_mine)) } },
        dismissButton = { TextButton(onClick = onTheirs) { Text(LiveWords.useTheirs(c.by)) } })
}

/** A field's name as the Compare view shows it. */
fun fieldLabel(field: String): String = when (field) {
    "html", "text" -> AppText.get(R.string.ui_field_message)
    "summary", "title" -> AppText.get(R.string.ui_field_title)
    "notes", "description" -> AppText.get(R.string.ui_field_notes)
    "to" -> AppText.get(R.string.ui_field_to)
    "cc" -> AppText.get(R.string.ui_field_cc)
    "bcc" -> AppText.get(R.string.ui_field_bcc)
    "due" -> AppText.get(R.string.ui_field_due)
    "start" -> AppText.get(R.string.ui_field_start)
    "end" -> AppText.get(R.string.ui_field_end)
    "rrule" -> AppText.get(R.string.ui_field_repeat)
    "tags", "categories" -> AppText.get(R.string.ui_field_tags)
    "guests", "attendee_list" -> AppText.get(R.string.ui_field_guests)
    "video", "url" -> AppText.get(R.string.ui_field_video)
    else -> field.replace('_', ' ').replaceFirstChar { it.uppercase() }
}
