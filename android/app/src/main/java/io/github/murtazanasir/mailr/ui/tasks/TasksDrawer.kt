package io.github.murtazanasir.mailr.ui.tasks

import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.Label
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.Checklist
import androidx.compose.material.icons.outlined.DateRange
import androidx.compose.material.icons.outlined.Event
import androidx.compose.material.icons.outlined.EventBusy
import androidx.compose.material.icons.outlined.Flag
import androidx.compose.material.icons.outlined.HourglassEmpty
import androidx.compose.material.icons.outlined.Lock
import androidx.compose.material.icons.outlined.MailOutline
import androidx.compose.material.icons.outlined.TaskAlt
import androidx.compose.material.icons.outlined.Today
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalDrawerSheet
import io.github.murtazanasir.mailr.look.NavigationDrawerItem
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.TagInfo
import io.github.murtazanasir.mailr.data.cal.ListCount
import io.github.murtazanasir.mailr.ui.calendar.CalendarColors
import io.github.murtazanasir.mailr.ui.util.Format

fun smartIcon(s: Smart): ImageVector = when (s) {
    Smart.TODAY -> Icons.Outlined.Today
    Smart.UPCOMING -> Icons.Outlined.DateRange
    Smart.SCHEDULED -> Icons.Outlined.Event
    Smart.NODATE -> Icons.Outlined.EventBusy
    Smart.HIGH -> Icons.Outlined.Flag
    Smart.EMAIL -> Icons.Outlined.MailOutline
    Smart.WAITING -> Icons.Outlined.HourglassEmpty
    Smart.ALL -> Icons.Outlined.Checklist
    Smart.COMPLETED -> Icons.Outlined.TaskAlt
}

/**
 * The Tasks drawer: the smart lists with live open counts (Today shows its
 * overdue count in red), My lists with colour, count and a lock when read-only, and the tags.
 * "Edit" opens the list manager.
 */
@Composable
fun TasksDrawer(
    view: TaskView,
    counts: Map<String, Int>,
    lists: List<ListCount>,
    tags: List<TagInfo>,
    followUps: Int?,
    onOpen: (TaskView) -> Unit,
    onEditLists: () -> Unit,
    onNewList: () -> Unit,
    onManageTags: () -> Unit,
) {
    ModalDrawerSheet(Modifier.width(320.dp)) {
        androidx.compose.foundation.layout.Column(Modifier.verticalScroll(rememberScrollState()).padding(horizontal = 12.dp, vertical = 8.dp)) {
            Text(stringResource(R.string.tasks_drawer_title), style = MaterialTheme.typography.titleLarge, modifier = Modifier.padding(start = 16.dp, top = 12.dp, bottom = 12.dp))
            Smart.entries.forEach { s ->
                val n = when (s) {
                    Smart.WAITING -> followUps
                    Smart.COMPLETED -> null
                    else -> counts[s.id]
                }
                val overdue = if (s == Smart.TODAY) counts["overdue"] ?: 0 else 0
                NavigationDrawerItem(
                    label = { Text(s.title) },
                    icon = { Icon(smartIcon(s), null) },
                    badge = {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            if (overdue > 0) {
                                Text("$overdue", color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.labelLarge)
                                Spacer(Modifier.width(8.dp))
                            }
                            if (n != null && n > 0) Text("$n", style = MaterialTheme.typography.labelLarge)
                        }
                    },
                    selected = view == TaskView.SmartView(s),
                    onClick = { onOpen(TaskView.SmartView(s)) },
                )
            }
            HorizontalDivider(Modifier.padding(vertical = 8.dp))
            SectionTitle(stringResource(R.string.tasks_my_lists), stringResource(R.string.tasks_edit), onEditLists)
            lists.forEach { lc ->
                val c = lc.collection
                NavigationDrawerItem(
                    label = { Text(c.name, maxLines = 1) },
                    icon = { Box(Modifier.size(12.dp).background(CalendarColors.of(c.url, c.color), CircleShape)) },
                    badge = {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            if (c.readOnly) Icon(Icons.Outlined.Lock, stringResource(R.string.tasks_read_only), Modifier.size(16.dp))
                            if (lc.open > 0) { Spacer(Modifier.width(6.dp)); Text("${lc.open}", style = MaterialTheme.typography.labelLarge) }
                        }
                    },
                    selected = view == TaskView.ListView(c.url),
                    onClick = { onOpen(TaskView.ListView(c.url)) },
                )
            }
            NavigationDrawerItem(label = { Text(stringResource(R.string.tasks_new_list)) }, icon = { Icon(Icons.Outlined.Add, null) }, selected = false, onClick = onNewList)
            if (tags.isNotEmpty()) {
                HorizontalDivider(Modifier.padding(vertical = 8.dp))
                SectionTitle(stringResource(R.string.tasks_tags), stringResource(R.string.tasks_manage), onManageTags)
                tags.forEach { t ->
                    NavigationDrawerItem(
                        label = { Text("@${t.name}", maxLines = 1) },
                        icon = { Icon(Icons.AutoMirrored.Outlined.Label, null, tint = Format.labelColors(t.color)?.first ?: MaterialTheme.colorScheme.onSurfaceVariant) },
                        badge = { if (t.count > 0) Text("${t.count}", style = MaterialTheme.typography.labelLarge) },
                        selected = view == TaskView.TagView(t.name),
                        onClick = { onOpen(TaskView.TagView(t.name)) },
                    )
                }
            }
        }
    }
}

@Composable
private fun SectionTitle(title: String, action: String, onAction: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(start = 16.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(title, style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.weight(1f).semantics { heading() })
        TextButton(onClick = onAction) { Text(action) }
    }
}
