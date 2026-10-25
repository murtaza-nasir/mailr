package io.github.murtazanasir.mailr.ui.tasks

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.i18n.AppText
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.ui.components.refuseObscuredTouches
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectVerticalDragGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.Label
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.DeleteOutline
import androidx.compose.material.icons.outlined.DragHandle
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.material.icons.outlined.Lock
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.outlined.Star
import io.github.murtazanasir.mailr.look.AlertDialog
import io.github.murtazanasir.mailr.look.DropdownMenu
import io.github.murtazanasir.mailr.look.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.OutlinedTextField
import io.github.murtazanasir.mailr.look.RadioButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import io.github.murtazanasir.mailr.look.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.CustomAccessibilityAction
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.compose.ui.zIndex
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import io.github.murtazanasir.mailr.data.api.TagInfo
import io.github.murtazanasir.mailr.data.cal.ListCount
import io.github.murtazanasir.mailr.ui.calendar.CalendarColors
import kotlin.math.roundToInt

/** The colour palette: 12 named swatches, the same on web and Android. */
object TaskPalette {
    private val SWATCHES = listOf(
        R.string.tasks_colour_tomato to "#d50000",
        R.string.tasks_colour_flamingo to "#e67c73",
        R.string.tasks_colour_tangerine to "#f4511e",
        R.string.tasks_colour_banana to "#f6bf26",
        R.string.tasks_colour_sage to "#33b679",
        R.string.tasks_colour_basil to "#0b8043",
        R.string.tasks_colour_peacock to "#039be5",
        R.string.tasks_colour_blueberry to "#3f51b5",
        R.string.tasks_colour_lavender to "#7986cb",
        R.string.tasks_colour_grape to "#8e24aa",
        R.string.tasks_colour_graphite to "#616161",
        R.string.tasks_colour_cocoa to "#795548"
    )

    /** The swatches with their names in the app's language. */
    val swatches: List<Pair<String, String>> get() = SWATCHES.map { (name, hex) -> AppText.get(name) to hex }

    fun color(hex: String): Color = Color(android.graphics.Color.parseColor(hex))
}

/** What the list manager asks for. Each call site in the screen carries its ACT id. */
class ListManagerActions(
    val create: (name: String, color: String?) -> Unit,
    val update: (CalendarCollection, name: String?, color: String?) -> Unit,
    val reorder: (List<String>) -> Unit,
    val delete: (CalendarCollection, moveTo: String?) -> Unit,
    val renameTag: (old: String, new: String) -> Unit,
    val deleteTag: (String) -> Unit,
    val close: () -> Unit,
    /** New tasks go to this list. */
    val setDefault: (CalendarCollection) -> Unit = {},
    /** A tag's colour (null removes it). */
    val tagColor: (String, String?) -> Unit = { _, _ -> },
    /** The line to show after "Download a copy first" (saved, or why not). */
    val exported: (String) -> Unit = {},
)

/**
 * "Edit lists" (the drawer): reorder handles, rename, colour, delete with
 * the move-or-delete choice, and a new list. The tags follow with rename and delete.
 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun ListManager(lists: List<ListCount>, tags: List<TagInfo>, actions: ListManagerActions, startNew: Boolean = false,
                defaultList: String? = null) {
    var tagColorFor by remember { mutableStateOf<TagInfo?>(null) }
    var order by remember(lists) { mutableStateOf(lists.map { it.collection.url }) }
    val byUrl = lists.associateBy { it.collection.url }
    var dragging by remember { mutableStateOf<String?>(null) }
    var dy by remember { mutableFloatStateOf(0f) }
    val rowPx = with(LocalDensity.current) { 56.dp.toPx() }
    var editing by remember { mutableStateOf<CalendarCollection?>(null) }
    var creating by remember { mutableStateOf(startNew) }
    var deleting by remember { mutableStateOf<ListCount?>(null) }
    var tagEdit by remember { mutableStateOf<String?>(null) }
    var tagDelete by remember { mutableStateOf<String?>(null) }
    val export = io.github.murtazanasir.mailr.ui.calendar.rememberIcsExport(actions.exported)

    fun move(url: String, by: Int) {
        val i = order.indexOf(url)
        val j = (i + by).coerceIn(0, order.size - 1)
        if (i < 0 || i == j) return
        order = order.toMutableList().apply { removeAt(i); add(j, url) }
    }

    Scaffold(topBar = {
        TopAppBar(title = { Text(stringResource(R.string.tasks_edit_lists)) },
            navigationIcon = { IconButton(onClick = actions.close) { Icon(Icons.Outlined.Close, stringResource(R.string.tasks_close)) } },
            actions = { TextButton(onClick = { creating = true }) { Icon(Icons.Outlined.Add, null); Spacer(Modifier.width(4.dp)); Text(stringResource(R.string.tasks_new_list)) } })
    }) { pad ->
        Column(Modifier.padding(pad).fillMaxSize().verticalScroll(rememberScrollState())) {
            order.forEach { url ->
                val lc = byUrl[url] ?: return@forEach
                val c = lc.collection
                val isDragged = dragging == url
                var menu by remember(url) { mutableStateOf(false) }
                val moveUp = stringResource(R.string.tasks_move_up)
                val moveDown = stringResource(R.string.tasks_move_down)
                val dragDesc = stringResource(R.string.tasks_drag_reorder, c.name)
                Row(
                    Modifier.fillMaxWidth().heightIn(min = 56.dp)
                        .then(if (isDragged) Modifier.zIndex(1f).graphicsLayer { translationY = dy }.shadow(6.dp)
                            .background(MaterialTheme.colorScheme.surfaceContainerHigh) else Modifier)
                        .semantics(mergeDescendants = true) {
                            customActions = listOf(
                                CustomAccessibilityAction(moveUp) { move(url, -1); actions.reorder(order); true },
                                CustomAccessibilityAction(moveDown) { move(url, 1); actions.reorder(order); true },
                            )
                        }
                        .padding(end = 4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    // ACT list.reorder @android.tasks: drag by the handle.
                    Box(Modifier.size(48.dp).pointerInput(url) {
                        detectVerticalDragGestures(
                            onDragStart = { dragging = url; dy = 0f },
                            onVerticalDrag = { ch, amount ->
                                ch.consume()
                                dy += amount
                                val steps = (dy / rowPx).roundToInt()
                                if (steps != 0) { move(url, steps); dy -= steps * rowPx }
                            },
                            onDragEnd = { dragging = null; dy = 0f; actions.reorder(order) },
                            onDragCancel = { dragging = null; dy = 0f },
                        )
                    }.semantics { contentDescription = dragDesc }, contentAlignment = Alignment.Center) {
                        Icon(Icons.Outlined.DragHandle, null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    Box(Modifier.size(14.dp).background(CalendarColors.of(c.url, c.color), CircleShape))
                    Spacer(Modifier.width(16.dp))
                    Column(Modifier.weight(1f)) {
                        Text(c.name, style = MaterialTheme.typography.bodyLarge)
                        Text(pluralStringResource(if (c.url == defaultList) R.plurals.tasks_open_count_default else R.plurals.tasks_open_count, lc.open, lc.open),
                            style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    if (c.readOnly) Icon(Icons.Outlined.Lock, stringResource(R.string.tasks_read_only), Modifier.padding(12.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
                    else Box {
                        IconButton(onClick = { menu = true }) { Icon(Icons.Outlined.MoreVert, stringResource(R.string.tasks_more_for, c.name)) }
                        DropdownMenu(menu, { menu = false }) {
                            // ACT list.rename @android.tasks: a shared list keeps its name; its colour stays the user's own.
                            DropdownMenuItem({ Text(stringResource(if (c.canRenameOrDelete) R.string.tasks_rename_colour else R.string.tasks_colour)) }, { menu = false; editing = c },
                                leadingIcon = { Icon(Icons.Outlined.Edit, null) })
                            // ACT list.set_default @android.tasks
                            if (c.url != defaultList) DropdownMenuItem({ Text(stringResource(R.string.tasks_set_default)) }, { menu = false; actions.setDefault(c) },
                                leadingIcon = { Icon(Icons.Outlined.Star, null) })
                            // ACT list.delete @android.tasks: not offered for a shared list.
                            if (c.canRenameOrDelete) DropdownMenuItem({ Text(stringResource(R.string.tasks_delete)) }, { menu = false; deleting = lc },
                                leadingIcon = { Icon(Icons.Outlined.DeleteOutline, null) })
                        }
                    }
                }
            }
            if (tags.isNotEmpty()) {
                HorizontalDivider(Modifier.padding(vertical = 8.dp))
                Text(stringResource(R.string.tasks_tags), style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 20.dp, vertical = 8.dp))
                tags.forEach { t ->
                    Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).padding(start = 20.dp, end = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                        // ACT tag.color @android.tasks: the dot opens the swatches.
                        Box(Modifier.size(48.dp).clip(CircleShape).clickable(onClickLabel = stringResource(R.string.tasks_colour_of_tag, t.name)) { tagColorFor = t },
                            contentAlignment = Alignment.Center) {
                            val tc = t.color?.let { catching { TaskPalette.color(it.take(7)) }.getOrNull() }
                            if (tc != null) Box(Modifier.size(14.dp).background(tc, CircleShape))
                            else Icon(Icons.AutoMirrored.Outlined.Label, null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        Text("@${t.name}", Modifier.weight(1f))
                        Text("${t.count}", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        IconButton(onClick = { tagEdit = t.name }) { Icon(Icons.Outlined.Edit, stringResource(R.string.tasks_rename_tag, t.name)) }
                        IconButton(onClick = { tagDelete = t.name }) { Icon(Icons.Outlined.DeleteOutline, stringResource(R.string.tasks_delete_tag, t.name)) }
                    }
                }
            }
        }
    }

    if (creating) ListDialog(null, onDismiss = { creating = false }) { name, color ->
        // ACT list.create @android.tasks
        actions.create(name, color)
        creating = false
    }
    editing?.let { c ->
        ListDialog(c, onDismiss = { editing = null }, nameEditable = c.canRenameOrDelete) { name, color ->
            // ACT list.rename @android.tasks, ACT list.color @android.tasks
            actions.update(c, name.takeIf { it != c.name }, color.takeIf { it != c.color })
            editing = null
        }
    }
    deleting?.let { lc ->
        DeleteListDialog(lc, lists.map { it.collection }.filter { it.url != lc.collection.url && !it.readOnly },
            onDismiss = { deleting = null }, onDelete = { moveTo ->
                // ACT list.delete @android.tasks
                actions.delete(lc.collection, moveTo)
                deleting = null
            }, export = export)
    }
    tagEdit?.let { old ->
        var name by remember(old) { mutableStateOf(old) }
        AlertDialog(onDismissRequest = { tagEdit = null }, title = { Text(stringResource(R.string.tasks_rename_tag_title, old)) },
            text = { OutlinedTextField(name, { name = it.replace(" ", "") }, singleLine = true, label = { Text(stringResource(R.string.tasks_tag)) }) },
            // ACT tag.rename @android.tasks
            confirmButton = { TextButton(onClick = { actions.renameTag(old, name); tagEdit = null }, enabled = name.isNotBlank()) { Text(stringResource(R.string.tasks_rename)) } },
            dismissButton = { TextButton(onClick = { tagEdit = null }) { Text(stringResource(R.string.tasks_cancel)) } })
    }
    tagColorFor?.let { t ->
        AlertDialog(onDismissRequest = { tagColorFor = null }, title = { Text(stringResource(R.string.tasks_colour_of_tag, t.name)) },
            text = {
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    TaskPalette.swatches.forEach { (name, hex) ->
                        val desc = if (t.color?.lowercase()?.take(7) == hex) stringResource(R.string.tasks_selected_suffix, name) else name
                        Box(Modifier.size(40.dp).clip(CircleShape).background(TaskPalette.color(hex))
                            .clickable(onClickLabel = name) { actions.tagColor(t.name, hex); tagColorFor = null }
                            .semantics { contentDescription = desc })
                    }
                }
            },
            confirmButton = { TextButton(onClick = { tagColorFor = null }) { Text(stringResource(R.string.tasks_cancel)) } },
            dismissButton = if (t.color != null) ({ TextButton(onClick = { actions.tagColor(t.name, null); tagColorFor = null }) { Text(stringResource(R.string.tasks_no_colour)) } }) else null)
    }
    tagDelete?.let { tag ->
        AlertDialog(onDismissRequest = { tagDelete = null }, title = { Text(stringResource(R.string.tasks_delete_tag_title, tag)) },
            text = { Text(stringResource(R.string.tasks_delete_tag_text)) },
            // ACT tag.delete @android.tasks
            confirmButton = { TextButton(onClick = { actions.deleteTag(tag); tagDelete = null }, modifier = Modifier.refuseObscuredTouches()) { Text(stringResource(R.string.tasks_delete)) } },
            dismissButton = { TextButton(onClick = { tagDelete = null }) { Text(stringResource(R.string.tasks_cancel)) } })
    }
}

/** A new list, or a list's name and colour (palette swatches with their names). */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ListDialog(c: CalendarCollection?, onDismiss: () -> Unit, nameEditable: Boolean = true, onSave: (name: String, color: String?) -> Unit) {
    var name by remember { mutableStateOf(c?.name.orEmpty()) }
    var color by remember { mutableStateOf(c?.color?.lowercase()?.take(7)) }
    AlertDialog(onDismissRequest = onDismiss, title = { Text(stringResource(if (c == null) R.string.tasks_new_list else R.string.tasks_edit_list)) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedTextField(name, { name = it }, label = { Text(stringResource(R.string.tasks_name)) }, singleLine = true, enabled = nameEditable,
                    supportingText = c?.ownerNote?.takeIf { !nameEditable }?.let { { Text(it) } }, modifier = Modifier.fillMaxWidth())
                Text(stringResource(R.string.tasks_colour), style = MaterialTheme.typography.labelLarge)
                // Rows of four: 48 dp targets that fit the narrowest dialog.
                TaskPalette.swatches.chunked(4).forEach { row ->
                    Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                        row.forEach { (label, hex) ->
                            val sel = color == hex
                            Box(Modifier.size(48.dp).selectable(sel, role = Role.RadioButton) { color = hex }
                                .semantics { contentDescription = label }, contentAlignment = Alignment.Center) {
                                Box(Modifier.size(32.dp).background(TaskPalette.color(hex), CircleShape)
                                    .then(if (sel) Modifier.border(3.dp, MaterialTheme.colorScheme.onSurface, CircleShape) else Modifier),
                                    contentAlignment = Alignment.Center) {
                                    if (sel) Icon(Icons.Filled.Check, null, Modifier.size(18.dp), tint = Color.White)
                                }
                            }
                        }
                    }
                }
                Text(TaskPalette.swatches.firstOrNull { it.second == color }?.first ?: stringResource(R.string.tasks_auto_colour),
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        },
        confirmButton = { TextButton(onClick = { onSave(name.trim(), color) }, enabled = name.isNotBlank()) { Text(stringResource(if (c == null) R.string.tasks_create else R.string.tasks_save)) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.tasks_cancel)) } })
}

/**
 * list.delete: "Move n tasks to …" (the default) or "Delete them too", then a confirmation.
 * Deleting the tasks cannot be undone.
 */
@Composable
fun DeleteListDialog(lc: ListCount, others: List<CalendarCollection>, onDismiss: () -> Unit, onDelete: (moveTo: String?) -> Unit,
                     export: ((CalendarCollection) -> Unit)? = null) {
    var moveTo by remember { mutableStateOf(others.firstOrNull()?.url) }
    var deleteTasks by remember { mutableStateOf(others.isEmpty()) }
    var sure by remember { mutableStateOf(false) }
    val n = lc.open
    AlertDialog(onDismissRequest = onDismiss, title = { Text(stringResource(R.string.tasks_delete_list_title, lc.collection.name)) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                if (n > 0 && others.isNotEmpty()) {
                    Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).selectable(!deleteTasks, role = Role.RadioButton) { deleteTasks = false },
                        verticalAlignment = Alignment.CenterVertically) {
                        RadioButton(!deleteTasks, null)
                        Spacer(Modifier.width(8.dp))
                        Text(pluralStringResource(R.plurals.tasks_move_n_tasks, n, n))
                    }
                    if (!deleteTasks) others.forEach { o ->
                        Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).padding(start = 32.dp)
                            .selectable(moveTo == o.url, role = Role.RadioButton) { moveTo = o.url }, verticalAlignment = Alignment.CenterVertically) {
                            Box(Modifier.size(12.dp).background(CalendarColors.of(o.url, o.color), CircleShape))
                            Spacer(Modifier.width(12.dp))
                            Text(o.name, Modifier.weight(1f))
                            if (moveTo == o.url) Icon(Icons.Filled.Check, stringResource(R.string.tasks_chosen))
                        }
                    }
                    Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).selectable(deleteTasks, role = Role.RadioButton) { deleteTasks = true },
                        verticalAlignment = Alignment.CenterVertically) {
                        RadioButton(deleteTasks, null)
                        Spacer(Modifier.width(8.dp))
                        Text(stringResource(R.string.tasks_delete_them_too))
                    }
                }
                Text(if (deleteTasks && n > 0) pluralStringResource(R.plurals.tasks_delete_list_and_tasks, n, n)
                    else stringResource(R.string.tasks_delete_list_only), style = MaterialTheme.typography.bodySmall,
                    color = if (deleteTasks && n > 0) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 8.dp))
                // ACT event.export_ics @android.tasks: the list's tasks as an .ics file, before it goes.
                if (export != null) io.github.murtazanasir.mailr.ui.calendar.DownloadCopyButton(lc.collection, export)
                if (sure) Text(stringResource(R.string.tasks_delete_again), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.error,
                    modifier = Modifier.padding(top = 8.dp))
            }
        },
        confirmButton = {
            TextButton(onClick = { if (!sure) sure = true else onDelete(if (deleteTasks) null else moveTo) }, modifier = Modifier.refuseObscuredTouches()) {
                Text(stringResource(if (sure) R.string.tasks_delete else R.string.tasks_delete_list), color = MaterialTheme.colorScheme.error)
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.tasks_cancel)) } })
}
