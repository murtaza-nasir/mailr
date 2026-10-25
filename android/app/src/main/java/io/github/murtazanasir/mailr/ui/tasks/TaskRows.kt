package io.github.murtazanasir.mailr.ui.tasks

import io.github.murtazanasir.mailr.i18n.AppText
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import android.os.Build
import android.view.HapticFeedbackConstants
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.Notes
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.DeleteOutline
import androidx.compose.material.icons.outlined.DragIndicator
import androidx.compose.material.icons.outlined.FolderOpen
import androidx.compose.material.icons.outlined.EventRepeat
import androidx.compose.material.icons.outlined.ExpandLess
import androidx.compose.material.icons.outlined.ExpandMore
import androidx.compose.material.icons.outlined.Lock
import androidx.compose.material.icons.outlined.MailOutline
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.outlined.NotificationsNone
import androidx.compose.material.icons.outlined.Repeat
import androidx.compose.material.icons.automirrored.outlined.Reply
import io.github.murtazanasir.mailr.look.Checkbox
import io.github.murtazanasir.mailr.look.DropdownMenu
import io.github.murtazanasir.mailr.look.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.SwipeToDismissBoxValue
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.scale
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathMeasure
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.semantics.CustomAccessibilityAction
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.zIndex
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import io.github.murtazanasir.mailr.data.api.ConversationSummary
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.reminders.ReminderPlan
import io.github.murtazanasir.mailr.ui.calendar.CalendarColors
import io.github.murtazanasir.mailr.ui.theme.Mail
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.ZoneId
import kotlin.math.abs
import kotlin.math.roundToInt

/** What the list asks the screen to do. Each action site in the screen carries its ACT id. */
class TaskListActions(
    val open: (TaskItem) -> Unit,
    val toggle: (TaskItem) -> Unit,
    val select: (TaskItem) -> Unit,
    val swipe: (TaskItem, SwipeAction) -> Unit,
    val reschedule: (TaskItem) -> Unit,
    val reorder: (TaskItem, after: String?, before: String?) -> Unit,
    val reparent: (TaskItem, parent: String) -> Unit,
    val openEmail: (Long) -> Unit,
    val openTag: (String) -> Unit,
    val addToDay: (java.time.LocalDate) -> Unit,
    val followUpOpen: (ConversationSummary) -> Unit = {},
    val followUpChange: (ConversationSummary) -> Unit = {},
    val followUpCancel: (ConversationSummary) -> Unit = {},
    val followUpToTask: (ConversationSummary) -> Unit = {},
)

/** How the rows look in this view. */
data class RowStyle(
    /** The list dot and name in the meta line (smart and tag views). */
    val showList: Boolean = true,
    /** Drag handles (manual order in a list). */
    val manual: Boolean = false,
    /** Subtasks open at first (a list view); elsewhere they wait for a tap on n/m. */
    val expandSubtasks: Boolean = false,
    val showNotes: Boolean = true,
)

/**
 * The grouped list of the Tasks screen: section headers (with "+ Add to Sat" on day sections),
 * rows that swipe, select and drag, subtasks that expand inline, and the follow-up rows.
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun TaskGroupList(
    groups: List<TaskGroup>,
    now: Long,
    lists: List<CalendarCollection>,
    selection: Set<String>,
    swipe: Pair<SwipeAction, SwipeAction>,
    style: RowStyle,
    actions: TaskListActions,
    followUps: List<ConversationSummary> = emptyList(),
    followUpTitle: String? = null,
    highlighted: String? = null,
    state: LazyListState = rememberLazyListState(),
    modifier: Modifier = Modifier,
) {
    val byUrl = remember(lists) { lists.associateBy { it.url } }
    val zone = ZoneId.systemDefault()
    var completing by remember { mutableStateOf(setOf<String>()) }
    var toggled by remember { mutableStateOf(setOf<String>()) }
    var dragging by remember { mutableStateOf<String?>(null) }
    var dy by remember { mutableFloatStateOf(0f) }
    var dx by remember { mutableFloatStateOf(0f) }
    val rtl = LocalLayoutDirection.current == LayoutDirection.Rtl
    val scope = rememberCoroutineScope()
    val indentPx = with(LocalDensity.current) { 56.dp.toPx() }
    val selecting = selection.isNotEmpty()

    fun expanded(uid: String) = (uid in toggled) != style.expandSubtasks
    // ACT task.complete @android.tasks, ACT task.reopen @android.tasks: the check plays its animation before the task leaves its section.
    fun check(t: TaskItem) {
        if (t.completed || t.cancelled) { actions.toggle(t); return }
        completing = completing + t.uid
        scope.launch {
            delay(650)
            actions.toggle(t)
            delay(400)
            completing = completing - t.uid
        }
    }
    // The rows in screen order with their parents, so a drop knows its neighbours.
    val order = groups.flatMap { g -> g.entries.flatMap { e -> listOf(e.task to null) + e.shown.map { it.task to e.task.uid } } }

    fun neighbours(t: TaskItem): Pair<TaskItem?, TaskItem?> {
        val siblings = order.map { it.first }.filter { it.parent == t.parent }
        val i = siblings.indexOfFirst { it.uid == t.uid }
        return siblings.getOrNull(i - 1) to siblings.getOrNull(i + 1)
    }

    fun moveUp(t: TaskItem) {
        val sibs = order.map { it.first }.filter { it.parent == t.parent }
        val i = sibs.indexOfFirst { it.uid == t.uid }
        if (i > 0) actions.reorder(t, sibs.getOrNull(i - 2)?.uid, sibs[i - 1].uid)
    }

    fun moveDown(t: TaskItem) {
        val sibs = order.map { it.first }.filter { it.parent == t.parent }
        val i = sibs.indexOfFirst { it.uid == t.uid }
        if (i >= 0 && i < sibs.size - 1) actions.reorder(t, sibs[i + 1].uid, sibs.getOrNull(i + 2)?.uid)
    }

    /** Where a drag let go: across (indent/outdent) or along (reorder among its siblings). */
    fun drop(t: TaskItem) {
        val info = state.layoutInfo.visibleItemsInfo
        val me = info.firstOrNull { it.key == "t-${t.uid}" || it.key == "s-${t.uid}" }
        if (me != null && abs(dx) > indentPx && abs(dy) < me.size) {
            // Toward the end (right, or left in a right-to-left layout) makes it a subtask.
            val toEnd = if (rtl) -dx else dx
            if (toEnd > 0 && t.parent == null) neighbours(t).first?.let { actions.reparent(t, it.uid) }
            if (toEnd < 0 && t.parent != null) actions.reparent(t, "")
            return
        }
        if (me == null || abs(dy) < me.size / 2f) return
        val center = me.offset + me.size / 2f + dy
        val target = info.firstOrNull { (it.key as? String)?.let { k -> k.startsWith("t-") || k.startsWith("s-") } == true &&
            center >= it.offset && center < it.offset + it.size }
        val tu = (target?.key as? String)?.drop(2) ?: return
        val sibs = order.map { it.first }.filter { it.parent == t.parent }
        val ti = sibs.indexOfFirst { it.uid == tu }
        val mi = sibs.indexOfFirst { it.uid == t.uid }
        if (ti < 0 || mi < 0 || ti == mi) return
        if (ti > mi) actions.reorder(t, sibs[ti].uid, sibs.getOrNull(ti + 1)?.uid)
        else actions.reorder(t, sibs.getOrNull(ti - 1)?.uid, sibs[ti].uid)
    }

    @Composable
    fun ItemRow(t: TaskItem, depth: Int, entry: TaskEntry?, nested: Boolean) {
        val isDragged = dragging == t.uid
        val sibs = remember(order, t.uid) { order.map { it.first }.filter { it.parent == t.parent } }
        val idx = sibs.indexOfFirst { it.uid == t.uid }
        val sMoveUp = stringResource(R.string.tasks_a11y_move_up)
        val sMoveDown = stringResource(R.string.tasks_a11y_move_down)
        val sSubtask = stringResource(R.string.tasks_a11y_make_subtask)
        val sTop = stringResource(R.string.tasks_a11y_top_level)
        val sReschedule = stringResource(R.string.tasks_a11y_reschedule)
        val sSelect = stringResource(R.string.tasks_a11y_select)
        val dragDesc = stringResource(R.string.tasks_a11y_drag)
        val a11y = buildList {
            if (style.manual) {
                if (idx > 0) add(CustomAccessibilityAction(sMoveUp) { moveUp(t); true })
                if (idx in 0 until sibs.size - 1) add(CustomAccessibilityAction(sMoveDown) { moveDown(t); true })
                if (t.parent == null && idx > 0) add(CustomAccessibilityAction(sSubtask) { actions.reparent(t, sibs[idx - 1].uid); true })
                if (t.parent != null) add(CustomAccessibilityAction(sTop) { actions.reparent(t, ""); true })
            }
            add(CustomAccessibilityAction(sReschedule) { actions.reschedule(t); true })
            add(CustomAccessibilityAction(sSelect) { actions.select(t); true })
        }
        val handle: (@Composable () -> Unit)? = if (style.manual && !selecting) ({
            Box(Modifier.size(width = 32.dp, height = 48.dp).pointerInput(t.uid) {
                detectDragGestures(
                    onDragStart = { dragging = t.uid; dx = 0f; dy = 0f },
                    onDrag = { change, amount -> change.consume(); dx += amount.x; dy += amount.y },
                    onDragEnd = { drop(t); dragging = null; dx = 0f; dy = 0f },
                    onDragCancel = { dragging = null; dx = 0f; dy = 0f },
                )
            }.semantics { contentDescription = dragDesc }, contentAlignment = Alignment.Center) {
                Icon(Icons.Outlined.DragIndicator, null, Modifier.size(20.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }) else null
        val body: @Composable () -> Unit = {
            TaskLine(
                task = t, depth = depth, now = now, zone = zone,
                list = if (style.showList && depth == 0) byUrl[t.list] else null,
                subtasks = entry?.takeIf { it.subtasks.isNotEmpty() }?.let { it.doneCount to it.subtasks.size },
                expanded = entry != null && expanded(t.uid), nested = nested,
                showNotes = style.showNotes, completing = t.uid in completing,
                selecting = selecting, selected = t.uid in selection, highlighted = t.uid == highlighted,
                readOnly = byUrl[t.list]?.readOnly == true, handle = handle, a11y = a11y,
                onToggle = { check(t) }, onOpen = { if (selecting) actions.select(t) else actions.open(t) },
                onLongPress = { actions.select(t) },
                onExpand = { toggled = if (t.uid in toggled) toggled - t.uid else toggled + t.uid },
                onDue = { actions.reschedule(t) }, onEmail = { t.conversationId?.let(actions.openEmail) }, onTag = actions.openTag,
            )
        }
        val mod = if (isDragged) Modifier.zIndex(1f).graphicsLayer {
            translationY = dy
            translationX = dx.coerceIn(-indentPx * 1.5f, indentPx * 1.5f)
        }.shadow(6.dp) else Modifier
        Box(mod) {
            if (selecting || byUrl[t.list]?.readOnly == true || isDragged) body()
            else io.github.murtazanasir.mailr.ui.components.SwipeRow(
                enableStart = true, enableEnd = true,
                onSwipe = { v ->
                    when (v) {
                        SwipeToDismissBoxValue.StartToEnd -> if (swipe.first == SwipeAction.COMPLETE) check(t) else actions.swipe(t, swipe.first)
                        SwipeToDismissBoxValue.EndToStart -> if (swipe.second == SwipeAction.COMPLETE) check(t) else actions.swipe(t, swipe.second)
                        else -> Unit
                    }
                    false
                },
                background = { dir, _ -> SwipeBackground(if (dir == SwipeToDismissBoxValue.StartToEnd) swipe.first else swipe.second, dir) },
                resetKey = t,
            ) { body() }
        }
    }

    LazyColumn(modifier.fillMaxSize(), state = state, contentPadding = PaddingValues(bottom = 96.dp)) {
        if (followUps.isNotEmpty() && followUpTitle != null) {
            item(key = "h-followups") { GroupHeader(followUpTitle, followUps.size, false, null, actions.addToDay) }
            followUps.forEach { c ->
                item(key = "f-${c.id}") { FollowUpRow(c, now, actions) }
            }
        }
        groups.forEach { grp ->
            if (grp.title.isNotEmpty()) item(key = "h-${grp.id}") { GroupHeader(grp.title, grp.count, grp.emphasis, grp.day, actions.addToDay) }
            grp.entries.forEach { e ->
                item(key = "t-${e.task.uid}") {
                    Column(Modifier.animateItem()) { ItemRow(e.task, 0, e, false) }
                }
                if (expanded(e.task.uid)) e.shown.forEach { s ->
                    item(key = "s-${s.task.uid}") {
                        Column(Modifier.animateItem()) { ItemRow(s.task, 1, null, s.nested) }
                    }
                }
            }
        }
    }
}

@Composable
private fun SwipeBackground(a: SwipeAction, dir: SwipeToDismissBoxValue) {
    val (bg, icon) = when (a) {
        SwipeAction.COMPLETE -> Mail.colors.success to Icons.Filled.Check
        SwipeAction.RESCHEDULE -> MaterialTheme.colorScheme.tertiary to Icons.Outlined.EventRepeat
        SwipeAction.DELETE -> MaterialTheme.colorScheme.error to Icons.Outlined.DeleteOutline
        SwipeAction.MOVE -> MaterialTheme.colorScheme.secondary to Icons.Outlined.FolderOpen
    }
    Box(Modifier.fillMaxSize().background(bg).padding(horizontal = 24.dp),
        contentAlignment = if (dir == SwipeToDismissBoxValue.StartToEnd) Alignment.CenterStart else Alignment.CenterEnd) {
        Icon(icon, stringResource(a.labelRes), tint = Color.White)
    }
}

@Composable
fun GroupHeader(title: String, count: Int, emphasis: Boolean, day: java.time.LocalDate?, addToDay: (java.time.LocalDate) -> Unit) {
    Row(Modifier.fillMaxWidth().padding(start = 20.dp, end = 8.dp, top = 14.dp, bottom = 2.dp).semantics { heading() },
        verticalAlignment = Alignment.CenterVertically) {
        // The web's group names (tasks list): bold, in the text colour, red when overdue.
        val web = io.github.murtazanasir.mailr.look.LocalLook.current.web
        Text(title, style = MaterialTheme.typography.titleSmall.let { if (web) it.copy(fontWeight = androidx.compose.ui.text.font.FontWeight.Bold) else it },
            color = if (emphasis) MaterialTheme.colorScheme.error else if (web) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.primary)
        Spacer(Modifier.width(8.dp))
        Text(count.toString(), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.weight(1f))
        if (day != null) TextButton(onClick = { addToDay(day) }) {
            Icon(Icons.Outlined.Add, null, Modifier.size(16.dp))
            Spacer(Modifier.width(4.dp))
            Text(stringResource(R.string.tasks_add), style = MaterialTheme.typography.labelLarge)
        }
    }
}

/**
 * One task row: the priority-coloured check (⟳ inside for a repeating
 * task), the title, the due chip and the icons, the meta line, and the n/m subtask toggle.
 */
@OptIn(ExperimentalFoundationApi::class, ExperimentalLayoutApi::class)
@Composable
fun TaskLine(
    task: TaskItem,
    depth: Int,
    now: Long,
    zone: ZoneId,
    list: CalendarCollection?,
    subtasks: Pair<Int, Int>?,
    expanded: Boolean,
    nested: Boolean,
    showNotes: Boolean,
    completing: Boolean,
    selecting: Boolean,
    selected: Boolean,
    highlighted: Boolean,
    readOnly: Boolean,
    handle: (@Composable () -> Unit)?,
    a11y: List<CustomAccessibilityAction>,
    onToggle: () -> Unit,
    onOpen: () -> Unit,
    onLongPress: () -> Unit,
    onExpand: () -> Unit,
    onDue: () -> Unit,
    onEmail: () -> Unit,
    onTag: (String) -> Unit,
) {
    val checked = task.completed || completing
    val fade by animateFloatAsState(if (checked && !task.completed) 0.55f else if (task.completed) 0.6f else 1f, tween(300), label = "fade") // i18n-ignore: animation label
    val level = CalendarColors.priorityLevel(task.priority)
    val chip = DueChip.of(task, now, zone)
    val sSelect = stringResource(R.string.tasks_a11y_select)
    val sOpen = stringResource(R.string.tasks_a11y_open_task)
    val sState = stringResource(if (checked) R.string.tasks_state_done else R.string.tasks_state_not_done)
    val sToggle = stringResource(if (checked) R.string.tasks_mark_not_done else R.string.tasks_mark_done)
    val sEmail = stringResource(R.string.tasks_open_email)
    val sTags = task.categories.associateWith { stringResource(R.string.tasks_open_tag, it) }
    val bg = when {
        selected -> MaterialTheme.colorScheme.secondaryContainer
        highlighted -> MaterialTheme.colorScheme.surfaceContainerHigh
        else -> MaterialTheme.colorScheme.surface
    }
    // The web's look: a 1 px divider under each task.
    val look = io.github.murtazanasir.mailr.look.LocalLook.current
    Surface(color = bg, modifier = if (look.web) io.github.murtazanasir.mailr.look.Hairline.bottom(look.palette.divider) else Modifier) {
        Row(
            Modifier.fillMaxWidth().heightIn(min = 56.dp)
                .combinedClickable(onClickLabel = if (selecting) sSelect else sOpen, onLongClickLabel = sSelect,
                    onClick = onOpen, onLongClick = onLongPress)
                .padding(start = (4 + depth * 32).dp, end = 4.dp, top = 4.dp, bottom = 4.dp)
                .semantics(mergeDescendants = true) {
                    stateDescription = sState
                    if (selecting) this.selected = selected
                    // The due, the email and the tags are small marks in the row; their actions live here (and in the sheet).
                    customActions = buildList {
                        if (!readOnly) add(CustomAccessibilityAction(sToggle) { onToggle(); true })
                        addAll(a11y)
                        if (task.conversationId != null) add(CustomAccessibilityAction(sEmail) { onEmail(); true })
                        task.categories.forEach { tag -> add(CustomAccessibilityAction(sTags.getValue(tag)) { onTag(tag); true }) }
                    }
                },
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (handle != null) handle() else if (depth == 0) Spacer(Modifier.width(4.dp))
            if (selecting) Box(Modifier.size(48.dp), contentAlignment = Alignment.Center) { Checkbox(selected, { onLongPress() }) }
            else TaskCheck(checked, CalendarColors.priority(level), repeating = task.recurring, small = depth > 0,
                enabled = !readOnly, onToggle = onToggle)
            Spacer(Modifier.width(4.dp))
            Column(Modifier.weight(1f).alpha(fade).padding(vertical = 4.dp)) {
                Text(task.summary.ifBlank { stringResource(R.string.tasks_untitled) }, style = MaterialTheme.typography.bodyLarge, maxLines = 2,
                    overflow = TextOverflow.Ellipsis, textDecoration = if (checked) TextDecoration.LineThrough else null)
                val tags = task.categories
                val notes = task.notes?.let { notesLine(it) }?.takeIf { showNotes }
                val icons = task.recurring || task.reminders.isNotEmpty() || task.conversationId != null || !task.notes.isNullOrBlank()
                if (chip != null || icons || list != null || tags.isNotEmpty() || notes != null || nested || readOnly) {
                    FlowRow(Modifier.padding(top = 3.dp), horizontalArrangement = Arrangement.spacedBy(6.dp),
                        verticalArrangement = Arrangement.spacedBy(2.dp)) {
                        chip?.let { DueChipView(it) }
                        if (task.recurring) Icon(Icons.Outlined.Repeat, stringResource(R.string.tasks_repeats), Modifier.size(15.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
                        if (task.reminders.isNotEmpty()) Icon(Icons.Outlined.NotificationsNone, stringResource(R.string.tasks_has_reminder), Modifier.size(15.dp),
                            tint = MaterialTheme.colorScheme.onSurfaceVariant)
                        if (task.conversationId != null) Icon(Icons.Outlined.MailOutline, stringResource(R.string.tasks_from_email), Modifier.size(15.dp),
                            tint = MaterialTheme.colorScheme.primary)
                        if (!task.notes.isNullOrBlank()) Icon(Icons.AutoMirrored.Outlined.Notes, stringResource(R.string.tasks_has_notes), Modifier.size(15.dp),
                            tint = MaterialTheme.colorScheme.onSurfaceVariant)
                        if (readOnly) Icon(Icons.Outlined.Lock, stringResource(R.string.tasks_read_only_list), Modifier.size(15.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
                        list?.let { c ->
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Box(Modifier.size(8.dp).background(CalendarColors.of(c.url, c.color), CircleShape))
                                Spacer(Modifier.width(4.dp))
                                Text(c.name, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1)
                            }
                        }
                        tags.forEach { tag ->
                            Text("@$tag", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.tertiary)
                        }
                        if (nested) Text(stringResource(R.string.tasks_nested), style = MaterialTheme.typography.labelSmall, fontStyle = FontStyle.Italic,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                        notes?.let {
                            Text(it.take(60), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                                maxLines = 1, overflow = TextOverflow.Ellipsis)
                        }
                    }
                }
            }
            subtasks?.let { (done, total) ->
                val subDesc = pluralStringResource(R.plurals.tasks_subtasks_done, total, done, total)
                Row(Modifier.heightIn(min = 48.dp).clickable(onClickLabel = stringResource(if (expanded) R.string.tasks_hide_subtasks else R.string.tasks_show_subtasks), onClick = onExpand)
                    .padding(horizontal = 8.dp).semantics { contentDescription = subDesc },
                    verticalAlignment = Alignment.CenterVertically) {
                    Text("$done/$total", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Icon(if (expanded) Icons.Outlined.ExpandLess else Icons.Outlined.ExpandMore, null, Modifier.size(18.dp),
                        tint = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
    }
}

@Composable
fun DueChipView(c: DueChip, onClick: (() -> Unit)? = null) {
    val (fg, bg) = when (c.tone) {
        DueChip.Tone.OVERDUE -> MaterialTheme.colorScheme.onErrorContainer to MaterialTheme.colorScheme.errorContainer
        DueChip.Tone.SOON -> MaterialTheme.colorScheme.error to MaterialTheme.colorScheme.error.copy(alpha = 0.10f)
        DueChip.Tone.NORMAL -> MaterialTheme.colorScheme.onSurfaceVariant to MaterialTheme.colorScheme.surfaceContainerHighest
        DueChip.Tone.DEFERRED, DueChip.Tone.DONE -> MaterialTheme.colorScheme.onSurfaceVariant to Color.Transparent
    }
    Text(c.text, style = MaterialTheme.typography.labelMedium, color = fg, maxLines = 1,
        fontStyle = if (c.tone == DueChip.Tone.DEFERRED) FontStyle.Italic else null,
        modifier = Modifier.background(bg, RoundedCornerShape(6.dp))
            .then(if (onClick != null) Modifier.clickable(onClickLabel = stringResource(R.string.tasks_a11y_reschedule), onClick = onClick) else Modifier)
            .padding(horizontal = 6.dp, vertical = 1.dp))
}

/**
 * The check: a ring in the priority colour (⟳ inside for a repeating task) that fills, draws
 * its tick and gives a small bounce plus a haptic tick when completed.
 */
@Composable
fun TaskCheck(checked: Boolean, color: Color, repeating: Boolean, small: Boolean, enabled: Boolean = true, onToggle: () -> Unit) {
    val view = LocalView.current
    val progress = remember { Animatable(if (checked) 1f else 0f) }
    val bounce = remember { Animatable(1f) }
    LaunchedEffect(checked) {
        if (checked && progress.value < 1f) {
            if (Build.VERSION.SDK_INT >= 30) view.performHapticFeedback(HapticFeedbackConstants.CONFIRM)
            launch { bounce.animateTo(1.25f, tween(110)); bounce.animateTo(1f, spring(Spring.DampingRatioMediumBouncy, Spring.StiffnessMedium)) }
            progress.animateTo(1f, tween(380))
        } else if (!checked) progress.animateTo(0f, tween(200))
    }
    val ring = if (color == MaterialTheme.colorScheme.onSurfaceVariant) taskRing(MaterialTheme.colorScheme) else color
    val fill = if (color == MaterialTheme.colorScheme.onSurfaceVariant) MaterialTheme.colorScheme.primary else color
    val tick = MaterialTheme.colorScheme.surface
    val d = if (small) 20.dp else 24.dp
    val toggleLabel = stringResource(if (checked) R.string.tasks_mark_not_done else R.string.tasks_mark_done)
    val repeatingDesc = stringResource(R.string.tasks_repeating)
    Box(
        Modifier.size(48.dp).clickable(enabled = enabled, onClickLabel = toggleLabel, onClick = onToggle)
            .semantics { role = Role.Checkbox; if (repeating) contentDescription = repeatingDesc },
        contentAlignment = Alignment.Center,
    ) {
        Canvas(Modifier.size(d).scale(bounce.value)) {
            val p = progress.value
            val r = size.minDimension / 2
            drawCircle(fill.copy(alpha = p), radius = r * p.coerceAtLeast(0.001f))
            drawCircle(ring, radius = r - 1.dp.toPx(), style = Stroke(2.dp.toPx()))
            if (p > 0.35f) {
                val path = Path().apply {
                    moveTo(size.width * 0.28f, size.height * 0.52f)
                    lineTo(size.width * 0.44f, size.height * 0.67f)
                    lineTo(size.width * 0.73f, size.height * 0.36f)
                }
                val m = PathMeasure().apply { setPath(path, false) }
                val seg = Path()
                m.getSegment(0f, m.length * ((p - 0.35f) / 0.65f), seg, true)
                drawPath(seg, tick, style = Stroke(2.4.dp.toPx(), cap = StrokeCap.Round, join = StrokeJoin.Round))
            }
        }
        if (repeating && progress.value < 0.35f) Icon(Icons.Outlined.Repeat, null, Modifier.size(if (small) 10.dp else 12.dp), tint = ring)
    }
}

/**
 * A "Waiting for reply" row (mail.follow_up): read-only, not a task. A tap opens the email; the
 * menu changes the date, cancels the follow-up or makes it a task.
 */
@Composable
fun FollowUpRow(c: ConversationSummary, now: Long, actions: TaskListActions) {
    var menu by remember { mutableStateOf(false) }
    val zone = ZoneId.systemDefault()
    val at = c.followUpAt?.let { ReminderPlan.instant(it, zone) }
    val today = Instant.ofEpochMilli(now).atZone(zone).toLocalDate()
    val label = at?.let { DueChip.short(Instant.ofEpochMilli(it).atZone(zone).toLocalDate(), today).lowercase().let { s ->
        AppText.get(if (s.first().isDigit()) R.string.tasks_follow_up_on else R.string.tasks_follow_up, s) } }
    val who = c.participants.firstOrNull { !it.me && it.email.isNotBlank() }?.let { it.name?.ifBlank { null } ?: it.email }
    Row(Modifier.fillMaxWidth().heightIn(min = 56.dp).clickable(onClickLabel = stringResource(R.string.tasks_open_email)) { actions.followUpOpen(c) }
        .padding(start = 8.dp, end = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(48.dp), contentAlignment = Alignment.Center) {
            Icon(Icons.AutoMirrored.Outlined.Reply, null, Modifier.size(20.dp), tint = MaterialTheme.colorScheme.primary)
        }
        Column(Modifier.weight(1f).padding(vertical = 6.dp)) {
            Text(c.subject.ifBlank { stringResource(R.string.tasks_no_subject) }, style = MaterialTheme.typography.bodyLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(listOfNotNull(label, who?.let { stringResource(R.string.tasks_waiting_on, it) }).joinToString(" · "), style = MaterialTheme.typography.labelMedium,
                color = if (at != null && at < now) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1)
        }
        Box {
            IconButton(onClick = { menu = true }) { Icon(Icons.Outlined.MoreVert, stringResource(R.string.tasks_follow_up_actions)) }
            DropdownMenu(menu, { menu = false }) {
                DropdownMenuItem({ Text(stringResource(R.string.tasks_fu_open)) }, { menu = false; actions.followUpOpen(c) })
                DropdownMenuItem({ Text(stringResource(R.string.tasks_fu_change)) }, { menu = false; actions.followUpChange(c) })
                DropdownMenuItem({ Text(stringResource(R.string.tasks_fu_cancel)) }, { menu = false; actions.followUpCancel(c) })
                DropdownMenuItem({ Text(stringResource(R.string.tasks_fu_to_task)) }, { menu = false; actions.followUpToTask(c) })
            }
        }
    }
    HorizontalDivider(Modifier.padding(start = 56.dp), color = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.4f))
}

/** The first line of a task's notes for its row, Markdown links as their text: "From the list
 *  [Shop](https://…)" reads "From the list Shop" (the web's TaskRow does the same). */
internal fun notesLine(notes: String): String? =
    notes.lineSequence().firstOrNull { it.isNotBlank() }?.replace(Regex("""\[([^\]]*)]\([^)]*\)"""), "$1")?.trim()

/**
 * The empty task circle's ring: the scheme's outline, moved toward onSurfaceVariant until it stands
 * at 3:1 or more against the surface and the background (WCAG 1.4.11, non-text contrast). The web
 * look's outline (#D5DEE1 on white) was 1.37:1.
 */
fun taskRing(scheme: androidx.compose.material3.ColorScheme): Color {
    fun contrast(a: Color, b: Color): Float {
        val (hi, lo) = a.luminance().let { la -> b.luminance().let { lb -> if (la > lb) la to lb else lb to la } }
        return (hi + 0.05f) / (lo + 0.05f)
    }
    fun enough(c: Color) = contrast(c, scheme.surface) >= MIN_RING_CONTRAST && contrast(c, scheme.background) >= MIN_RING_CONTRAST
    if (enough(scheme.outline)) return scheme.outline
    for (i in 1..20) {
        val c = androidx.compose.ui.graphics.lerp(scheme.outline, scheme.onSurfaceVariant, i / 20f)
        if (enough(c)) return c
    }
    return scheme.onSurfaceVariant
}

/** WCAG 1.4.11's minimum, with a little room for rounding in the drawn stroke. */
const val MIN_RING_CONTRAST = 3.1f
