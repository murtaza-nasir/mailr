package io.github.murtazanasir.mailr.ui.calendar

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.time.AppClock
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText
import androidx.compose.runtime.DisposableEffect
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.drop
import io.github.murtazanasir.mailr.ui.components.refuseObscuredTouches
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.automirrored.outlined.Sort
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.Menu
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material.icons.outlined.SelectAll
import androidx.compose.material.icons.outlined.SyncProblem
import androidx.compose.material.icons.outlined.TaskAlt
import io.github.murtazanasir.mailr.look.AlertDialog
import io.github.murtazanasir.mailr.look.CircularProgressIndicator
import androidx.compose.material3.DrawerValue
import io.github.murtazanasir.mailr.look.DropdownMenu
import io.github.murtazanasir.mailr.look.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.ModalBottomSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SheetValue
import androidx.compose.material3.SnackbarDuration
import io.github.murtazanasir.mailr.look.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import io.github.murtazanasir.mailr.look.TextField
import androidx.compose.material3.TextFieldDefaults
import io.github.murtazanasir.mailr.look.TopAppBar
import androidx.compose.material3.VerticalDivider
import androidx.compose.material3.adaptive.currentWindowAdaptiveInfo
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.material3.rememberDrawerState
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.window.core.layout.WindowWidthSizeClass
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import io.github.murtazanasir.mailr.data.api.ConversationSummary
import io.github.murtazanasir.mailr.data.api.TaskDraft
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.cal.CalConflict
import io.github.murtazanasir.mailr.data.cal.ListCount
import io.github.murtazanasir.mailr.data.cal.TaskQueries
import io.github.murtazanasir.mailr.data.cal.TaskSort
import io.github.murtazanasir.mailr.data.reminders.ReminderPlan
import io.github.murtazanasir.mailr.ui.components.ConversationSkeleton
import io.github.murtazanasir.mailr.ui.components.EmptyState
import io.github.murtazanasir.mailr.ui.conversation.FollowUpDialog
import io.github.murtazanasir.mailr.ui.graph
import io.github.murtazanasir.mailr.ui.tasks.BulkRescheduleSheet
import io.github.murtazanasir.mailr.ui.tasks.ConflictSheet
import io.github.murtazanasir.mailr.ui.tasks.ListManager
import io.github.murtazanasir.mailr.ui.tasks.ListManagerActions
import io.github.murtazanasir.mailr.ui.tasks.ListPickerSheet
import io.github.murtazanasir.mailr.ui.tasks.PriorityDialog
import io.github.murtazanasir.mailr.ui.tasks.QuickPrefill
import io.github.murtazanasir.mailr.ui.tasks.QuickTask
import io.github.murtazanasir.mailr.ui.tasks.RescheduleSheet
import io.github.murtazanasir.mailr.ui.tasks.RowStyle
import io.github.murtazanasir.mailr.ui.tasks.SelectionBar
import io.github.murtazanasir.mailr.ui.tasks.Smart
import io.github.murtazanasir.mailr.ui.tasks.SwipeAction
import io.github.murtazanasir.mailr.ui.tasks.SwipeSettingsDialog
import io.github.murtazanasir.mailr.ui.tasks.TagsDialog
import io.github.murtazanasir.mailr.ui.tasks.TaskGroup
import io.github.murtazanasir.mailr.ui.tasks.TaskGroupList
import io.github.murtazanasir.mailr.ui.tasks.TaskGroups
import io.github.murtazanasir.mailr.ui.tasks.TaskListActions
import io.github.murtazanasir.mailr.ui.tasks.TaskQuickAddBar
import io.github.murtazanasir.mailr.ui.tasks.TaskVerb
import io.github.murtazanasir.mailr.ui.tasks.TaskView
import io.github.murtazanasir.mailr.ui.tasks.TasksDrawer
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZonedDateTime

sealed interface TaskPane {
    data object None : TaskPane
    data class Edit(val uid: String?, val parent: String? = null, val prefill: QuickParse? = null, val draft: TaskDraft? = null) : TaskPane
    /** Phones: a tap opens the quick-action sheet first; Edit goes on to the editor. */
    data class Quick(val uid: String) : TaskPane
}

/** The bottom sheets and dialogs of the Tasks screen; one at a time. */
internal sealed interface TaskSheet {
    data class Reschedule(val task: TaskItem, val verb: TaskVerb = TaskVerb.RESCHEDULE) : TaskSheet
    data class Move(val task: TaskItem) : TaskSheet
    data class CompleteSubtasks(val task: TaskItem, val open: Int) : TaskSheet
    data class FollowUpDate(val c: ConversationSummary) : TaskSheet
    data class Conflict(val c: CalConflict) : TaskSheet
    data class Lists(val startNew: Boolean) : TaskSheet
    data object BulkReschedule : TaskSheet
    data object BulkMove : TaskSheet
    data object BulkTags : TaskSheet
    data object BulkPriority : TaskSheet
    data object BulkDelete : TaskSheet
    data object ClearCompleted : TaskSheet
    data object Swipes : TaskSheet
}

/** The task request that opens quick add (the Calendar's "New task") instead of a task. */
const val NEW_TASK_REQUEST = "\u0000new-task"

/** A task request that opens a list in the web's route form (`today`, `l/<id>`, `t/<tag>`): an app link. */
const val TASK_VIEW_PREFIX = "\u0000view:"

/**
 * The Tasks tab (surface android.tasks): the drawer of smart lists,
 * lists and tags; the quick-add bar; the grouped list with swipes, selection and drag; the
 * sheets for each verb; the editor (a sheet on phones, a side pane when unfolded).
 */
@OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)
@Composable
fun TasksRoute(vm: TasksViewModel, morningHour: Int, request: String?, consumed: () -> Unit, openEmail: (Long) -> Unit = {},
               replyEmail: ((Long) -> Unit)? = null,
               /** Opens the assistant about a task (context.focused_task). */
               askAssistant: ((TaskItem) -> Unit)? = null) {
    val view by vm.view.collectAsStateWithLifecycle()
    val groups by vm.groups.collectAsStateWithLifecycle()
    val tasks by vm.tasks.collectAsStateWithLifecycle()
    val options by vm.options.collectAsStateWithLifecycle()
    val connected by vm.connected.collectAsStateWithLifecycle()
    val syncing by vm.syncing.collectAsStateWithLifecycle()
    val error by vm.error.collectAsStateWithLifecycle()
    val lists by vm.collections.collectAsStateWithLifecycle()
    val listCounts by vm.lists.collectAsStateWithLifecycle()
    val tags by vm.tags.collectAsStateWithLifecycle()
    val counts by vm.counts.collectAsStateWithLifecycle()
    val defaults by vm.defaults.collectAsStateWithLifecycle()
    val selection by vm.selection.collectAsStateWithLifecycle()
    val swipe by vm.swipe.collectAsStateWithLifecycle()
    val followUps by vm.followUps.collectAsStateWithLifecycle()
    val conflicts by vm.conflicts.collectAsStateWithLifecycle()
    val now by vm.now.collectAsStateWithLifecycle()
    val loadingCompleted by vm.loadingCompleted.collectAsStateWithLifecycle()
    val lastAdded by vm.lastAdded.collectAsStateWithLifecycle()
    val subtaskMode by vm.subtaskMode.collectAsStateWithLifecycle()
    val duePrompt by vm.duePrompt.collectAsStateWithLifecycle()
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    val drawer = rememberDrawerState(DrawerValue.Closed)
    val wide = currentWindowAdaptiveInfo().windowSizeClass.windowWidthSizeClass != WindowWidthSizeClass.COMPACT
    var pane by remember { mutableStateOf<TaskPane>(TaskPane.None) }
    var sheet by remember { mutableStateOf<TaskSheet?>(null) }
    var saving by remember { mutableStateOf(false) }
    var searching by remember { mutableStateOf(false) }
    var more by remember { mutableStateOf(false) }
    var sortMenu by remember { mutableStateOf(false) }
    var dayPrefill by remember(view) { mutableStateOf<LocalDate?>(null) }
    var prefillCleared by remember(view) { mutableStateOf(false) }
    val quickFocus = remember { FocusRequester() }
    val all = tasks.orEmpty()
    val selecting = selection.isNotEmpty()
    val zone = ZoneId.systemDefault()
    val today = Instant.ofEpochMilli(now).atZone(zone).toLocalDate()
    val current = (view as? TaskView.ListView)?.let { v -> lists.firstOrNull { it.url == v.url } }

    LaunchedEffect(vm) {
        vm.messages.collect { m ->
            scope.launch {
                val r = snackbar.showSnackbar(m.text, m.actionLabel, withDismissAction = m.actionLabel == null, duration = SnackbarDuration.Short)
                if (r == SnackbarResult.ActionPerformed) m.action?.invoke()
            }
        }
    }
    LaunchedEffect(request) {
        when (request) {
            null -> return@LaunchedEffect
            // ACT task.create @android.editor @android.tasks from the Calendar's FAB menu: straight into quick add.
            NEW_TASK_REQUEST -> {
                pane = TaskPane.None
                val v = vm.view.value
                if (v == TaskView.SmartView(Smart.COMPLETED) || v == TaskView.SmartView(Smart.WAITING)) vm.open(TaskView.TODAY)
                kotlinx.coroutines.delay(150)
                catching { quickFocus.requestFocus() }
            }
            else -> if (request.startsWith(TASK_VIEW_PREFIX)) vm.openRoute(request.removePrefix(TASK_VIEW_PREFIX))
                else {
                    // A link to a task this phone does not hold yet: GET /tasks/{uid} first.
                    if (vm.ensureTask(request) == null) vm.say(AppText.get(R.string.cal_tasks_gone))
                    // One window per task: the editor already open on it stays (with what is typed in it).
                    if ((pane as? TaskPane.Edit)?.uid != request) pane = TaskPane.Edit(request)
                }
        }
        consumed()
    }
    BackHandler(selecting) { vm.clearSelection() }
    BackHandler(searching && !selecting) { searching = false; vm.search(null) }
    BackHandler(drawer.isOpen) { scope.launch { drawer.close() } }
    BackHandler(wide && pane != TaskPane.None) { pane = TaskPane.None }

    val title = when (val v = view) {
        is TaskView.SmartView -> v.smart.title
        is TaskView.ListView -> current?.name ?: stringResource(R.string.rem_note_kind_list)
        is TaskView.TagView -> "@${v.tag}"
    }
    val smart = (view as? TaskView.SmartView)?.smart
    val readOnly = current?.readOnly == true
    val prefill = if (prefillCleared) QuickPrefill(list = (view as? TaskView.ListView)?.url) else QuickPrefill.of(view, today, dayPrefill)
    val canAdd = connected != false && !readOnly && smart != Smart.COMPLETED && smart != Smart.WAITING && !selecting

    fun openTask(t: TaskItem) { pane = if (wide) TaskPane.Edit(t.uid) else TaskPane.Quick(t.uid) }
    fun toggle(t: TaskItem) {
        if (t.completed) { vm.reopen(t); return }
        val open = all.count { it.parent == t.uid && TaskQueries.open(it) }
        if (open > 0) sheet = TaskSheet.CompleteSubtasks(t, open) else vm.complete(t)
    }
    val listActions = TaskListActions(
        // ACT task.edit @android.tasks: a tap opens the editor (unfolded) or the sheet, whose Edit opens it (phones).
        open = ::openTask,
        // ACT task.complete @android.tasks, ACT task.reopen @android.tasks
        toggle = ::toggle,
        select = { vm.toggleSelect(it.uid) },
        swipe = { t, a ->
            when (a) {
                SwipeAction.RESCHEDULE -> sheet = TaskSheet.Reschedule(t)
                // ACT task.delete @android.tasks
                SwipeAction.DELETE -> vm.delete(t)
                SwipeAction.MOVE -> sheet = TaskSheet.Move(t)
                SwipeAction.COMPLETE -> toggle(t)
            }
        },
        reschedule = { sheet = TaskSheet.Reschedule(it) },
        // ACT task.reorder @android.tasks
        reorder = { t, after, before -> vm.reorder(t, after, before) },
        // ACT task.reparent @android.tasks
        reparent = { t, parent -> vm.reparent(t, parent) },
        // ACT task.open_email @android.tasks
        openEmail = openEmail,
        openTag = { vm.open(TaskView.TagView(it)) },
        addToDay = { d -> dayPrefill = d; prefillCleared = false; catching { quickFocus.requestFocus() } },
        followUpOpen = { openEmail(it.id) },
        followUpChange = { sheet = TaskSheet.FollowUpDate(it) },
        // ACT mail.follow_up @android.tasks (cancel)
        followUpCancel = { vm.changeFollowUp(it, null) },
        // ACT mail.to_task @android.tasks
        followUpToTask = { vm.followUpToTask(it) },
    )

    ModalNavigationDrawer(
        drawerState = drawer,
        gesturesEnabled = !selecting,
        drawerContent = {
            TasksDrawer(view, counts, listCounts, tags, followUps?.size,
                onOpen = { v -> vm.open(v); searching = false; scope.launch { drawer.close() } },
                onEditLists = { sheet = TaskSheet.Lists(false); scope.launch { drawer.close() } },
                onNewList = { sheet = TaskSheet.Lists(true); scope.launch { drawer.close() } },
                onManageTags = { sheet = TaskSheet.Lists(false); scope.launch { drawer.close() } })
        },
    ) {
        Scaffold(
            contentWindowInsets = WindowInsets(0),
            topBar = {
                when {
                    selecting -> TopAppBar(
                        navigationIcon = { IconButton(onClick = vm::clearSelection) { Icon(Icons.Outlined.Close, stringResource(R.string.cal_tasks_clear_selection)) } },
                        title = { Text(pluralStringResource(R.plurals.cal_tasks_selected, selection.size, selection.size)) },
                        actions = { IconButton(onClick = vm::selectAll) { Icon(Icons.Outlined.SelectAll, stringResource(R.string.cal_tasks_select_all)) } },
                    )
                    searching -> TopAppBar(
                        navigationIcon = {
                            IconButton(onClick = { searching = false; vm.search(null) }) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, stringResource(R.string.cal_tasks_close_search)) }
                        },
                        title = {
                            val focus = remember { FocusRequester() }
                            LaunchedEffect(Unit) { catching { focus.requestFocus() } }
                            // ACT task.search @android.tasks
                            TextField(options.query.orEmpty(), { vm.search(it) }, singleLine = true,
                                placeholder = { Text(stringResource(R.string.cal_tasks_search_placeholder)) },
                                colors = TextFieldDefaults.colors(focusedContainerColor = Color.Transparent, unfocusedContainerColor = Color.Transparent),
                                modifier = Modifier.fillMaxWidth().focusRequester(focus))
                        },
                    )
                    else -> TopAppBar(
                        navigationIcon = { IconButton(onClick = { scope.launch { drawer.open() } }) { Icon(Icons.Outlined.Menu, stringResource(R.string.cal_tasks_lists_and_tags)) } },
                        title = {
                            Column {
                                Text(title, maxLines = 1)
                                val n = groups?.sumOf { it.count }
                                if (n != null && smart != Smart.WAITING) Text(pluralStringResource(R.plurals.today_tasks, n, n),
                                    style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        },
                        actions = {
                            if (syncing || loadingCompleted) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                            IconButton(onClick = { searching = true; vm.search("") }) { Icon(Icons.Outlined.Search, stringResource(R.string.cal_tasks_search)) }
                            if (smart == null || smart == Smart.ALL || smart == Smart.HIGH || smart == Smart.EMAIL) Box {
                                IconButton(onClick = { sortMenu = true }) { Icon(Icons.AutoMirrored.Outlined.Sort, stringResource(R.string.cal_tasks_sort)) }
                                DropdownMenu(sortMenu, { sortMenu = false }) {
                                    listOf(null to stringResource(R.string.cal_tasks_sort_default), TaskSort.MANUAL to stringResource(R.string.cal_tasks_sort_manual),
                                        TaskSort.DUE to stringResource(R.string.cal_task_due_date), TaskSort.PRIORITY to stringResource(R.string.cal_tasks_sort_priority),
                                        TaskSort.TITLE to stringResource(R.string.cal_tasks_sort_title), TaskSort.CREATED to stringResource(R.string.cal_tasks_sort_created)).forEach { (s, l) ->
                                        DropdownMenuItem({ Text(l) }, { sortMenu = false; vm.setSort(s) },
                                            trailingIcon = if (options.sort == s) ({ Icon(Icons.Filled.Check, stringResource(R.string.cal_selected)) }) else null)
                                    }
                                }
                            }
                            Box {
                                IconButton(onClick = { more = true }) { Icon(Icons.Outlined.MoreVert, stringResource(R.string.cal_more)) }
                                DropdownMenu(more, { more = false }) {
                                    if (view is TaskView.ListView) DropdownMenuItem({ Text(if (options.showCompleted) stringResource(R.string.cal_tasks_hide_completed) else stringResource(R.string.cal_tasks_show_completed)) },
                                        { more = false; vm.toggleCompleted() })
                                    if (smart == Smart.TODAY || smart == Smart.ALL || view is TaskView.TagView)
                                        DropdownMenuItem({ Text(if (options.showDeferred) stringResource(R.string.cal_tasks_hide_deferred) else stringResource(R.string.cal_tasks_show_deferred)) }, { more = false; vm.toggleDeferred() })
                                    DropdownMenuItem({ Text(stringResource(R.string.cal_select)) }, {
                                        more = false
                                        groups?.firstOrNull()?.entries?.firstOrNull()?.let { vm.toggleSelect(it.task.uid) }
                                    })
                                    if (view is TaskView.ListView || smart == Smart.COMPLETED)
                                        DropdownMenuItem({ Text(stringResource(R.string.cal_tasks_delete_completed)) }, { more = false; sheet = TaskSheet.ClearCompleted })
                                    DropdownMenuItem({ Text(stringResource(R.string.cal_tasks_edit_lists)) }, { more = false; sheet = TaskSheet.Lists(false) })
                                    DropdownMenuItem({ Text(stringResource(R.string.cal_tasks_swipe_actions)) }, { more = false; sheet = TaskSheet.Swipes })
                                    DropdownMenuItem({ Text(stringResource(R.string.cal_refresh)) }, { more = false; vm.refresh() })
                                }
                            }
                        },
                    )
                }
            },
            bottomBar = {
                // Multi-select: ACT task.bulk_complete @android.tasks, ACT task.bulk_reschedule @android.tasks, ACT task.bulk_move @android.tasks, ACT task.bulk_tag @android.tasks,
                // ACT task.bulk_priority @android.tasks, ACT task.bulk_delete @android.tasks
                if (selecting) SelectionBar(selection.size, onComplete = vm::bulkComplete, onReschedule = { sheet = TaskSheet.BulkReschedule },
                    onMove = { sheet = TaskSheet.BulkMove }, onTags = { sheet = TaskSheet.BulkTags },
                    onPriority = { sheet = TaskSheet.BulkPriority }, onDelete = { sheet = TaskSheet.BulkDelete })
            },
            snackbarHost = { SnackbarHost(snackbar) },
        ) { pad ->
            Row(Modifier.padding(pad).fillMaxSize()) {
                Column(Modifier.weight(1f).fillMaxHeight().imePadding()) {
                    ReminderBanners(offline = error == "offline")
                    if (conflicts.isNotEmpty()) ConflictBanner(conflicts.size) { sheet = TaskSheet.Conflict(conflicts.first()) }
                    if (readOnly) Text(stringResource(R.string.cal_tasks_list_read_only), style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(horizontal = 20.dp, vertical = 8.dp))
                    // The list takes the room the bar leaves, so the bar never covers its last rows.
                    Box(Modifier.weight(1f).fillMaxWidth()) {
                        TaskContent(vm, view, groups, all, now, lists, selection, swipe, options, followUps, readOnly, connected,
                            syncing, pane, listActions, today)
                    }
                    // Quick add at the bottom, above the tab bar, as Notes' capture bar and Ambrose's message box;
                    // with the keyboard up it rides on top of it (imePadding on the
                    // column). While tasks are selected the selection bar has the bottom (canAdd).
                    if (canAdd) TaskQuickAddBar(lists, prefill, { ZonedDateTime.ofInstant(Instant.ofEpochMilli(vm.nowMs()), zone) }, quickFocus,
                        // ACT task.create
                        onAdd = { text, keep -> vm.quickAddLine(text, keep = keep, prefill = prefill) },
                        subtaskOf = lastAdded?.second, subtask = subtaskMode, onSubtask = vm::setSubtaskMode,
                        onDetails = { text, keep ->
                            val r = QuickTask.parse(text, AppClock.now(zone), lists, keep)
                            pane = TaskPane.Edit(null, draft = QuickTask.plan(r, vm.newUid(), zone, prefill).draft)
                        },
                        onClearPrefill = { prefillCleared = true; dayPrefill = null }, chipsAbove = true)
                }
                if (wide) {
                    VerticalDivider()
                    Box(Modifier.widthIn(min = 360.dp, max = 460.dp).fillMaxWidth(0.42f).fillMaxHeight()) {
                        when (val p = pane) {
                            TaskPane.None, is TaskPane.Quick -> EmptyState(Icons.Outlined.TaskAlt, stringResource(R.string.cal_tasks_none_selected), stringResource(R.string.cal_tasks_none_selected_body))
                            is TaskPane.Edit -> TaskEditorFor(vm, p, all, lists, defaults.taskList, (view as? TaskView.ListView)?.url, morningHour, saving,
                                onSaving = { saving = it }, onClose = { pane = TaskPane.None }, onOpen = { pane = it }, openEmail = openEmail,
                                replyEmail = replyEmail, knownTags = tags.map { it.name }, askAssistant = askAssistant)
                        }
                    }
                }
            }
        }
    }

    if (!wide) (pane as? TaskPane.Quick)?.let { q ->
        val task = all.firstOrNull { it.uid == q.uid }
        val context = LocalContext.current
        val actions = remember { QuickActions(context.graph, context) }
        val couldNotDo = stringResource(R.string.cal_tasks_could_not_do)
        val undoLabel = stringResource(R.string.cal_undo)
        if (task == null) { pane = TaskPane.None } else ModalBottomSheet(onDismissRequest = { pane = TaskPane.None },
            sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
            QuickActionCard(SheetTarget(null, task = task), actions, morningHour, null,
                run = { block ->
                    pane = TaskPane.None
                    scope.launch {
                        val d = catching { block() }.getOrElse { Done(couldNotDo, null) }
                        if (d.message.isNotBlank()) {
                            val r = snackbar.showSnackbar(d.message, if (d.undo != null) undoLabel else null,
                                withDismissAction = d.undo == null, duration = SnackbarDuration.Short)
                            if (r == SnackbarResult.ActionPerformed) d.undo?.invoke()
                        }
                    }
                },
                onClose = { pane = TaskPane.None }, onEdit = { pane = TaskPane.Edit(task.uid) })
            // The verbs the sheet leaves out, each saying what it changes.
            Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 4.dp)) {
                TextButton(onClick = { pane = TaskPane.None; sheet = TaskSheet.Reschedule(task, TaskVerb.DEFER) }) { Text(stringResource(R.string.cal_tasks_defer)) }
                TextButton(onClick = { pane = TaskPane.None; sheet = TaskSheet.Reschedule(task, TaskVerb.REMIND) }) { Text(stringResource(R.string.cal_remind_me)) }
                TextButton(onClick = { pane = TaskPane.None; sheet = TaskSheet.Move(task) }) { Text(stringResource(R.string.cal_qa_move)) }
                // ACT task.skip @android.tasks
                if (task.recurring) TextButton(onClick = { pane = TaskPane.None; vm.skip(task) }) { Text(stringResource(R.string.cal_tasks_skip)) }
                if (askAssistant != null) TextButton(onClick = { pane = TaskPane.None; askAssistant(task) }) { Text(stringResource(R.string.rem_action_ask, io.github.murtazanasir.mailr.ui.assistantName())) }
            }
        }
    }
    if (!wide) (pane as? TaskPane.Edit)?.let { p ->
        var confirmDiscard by remember(p) { mutableStateOf(false) }
        var dirty by remember(p) { mutableStateOf(false) }
        val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true, confirmValueChange = { v ->
            if (v == SheetValue.Hidden && dirty) { confirmDiscard = true; false } else true
        })
        ModalBottomSheet(onDismissRequest = { if (dirty) confirmDiscard = true else pane = TaskPane.None }, sheetState = sheetState) {
            TaskEditorFor(vm, p, all, lists, defaults.taskList, (view as? TaskView.ListView)?.url, morningHour, saving, onSaving = { saving = it },
                onDirty = { dirty = it }, onClose = { if (dirty) confirmDiscard = true else pane = TaskPane.None }, onOpen = { pane = it },
                openEmail = { id -> pane = TaskPane.None; openEmail(id) },
                replyEmail = replyEmail?.let { r -> { id: Long -> pane = TaskPane.None; r(id) } }, knownTags = tags.map { it.name },
                askAssistant = askAssistant?.let { a -> { t: TaskItem -> pane = TaskPane.None; a(t) } })
        }
        if (confirmDiscard) DiscardDialog({ confirmDiscard = false }) { confirmDiscard = false; pane = TaskPane.None }
    }

    TaskSheets(vm, sheet, { sheet = it }, morningHour, lists, listCounts, tags.map { it.name }, all, selection, view)
    // The series question for a new due on a repeating task ("This occurrence" or "All").
    duePrompt?.let { ScopeSheet(stringResource(R.string.cal_scope_change_task), onDismiss = { vm.answerDueScope(null) }, forTask = true) { sc -> vm.answerDueScope(sc) } }
}

@Composable
private fun ConflictBanner(n: Int, onReview: () -> Unit) {
    val (bg, fg) = io.github.murtazanasir.mailr.look.bannerColors(io.github.murtazanasir.mailr.look.BannerTone.Warning,
        MaterialTheme.colorScheme.errorContainer, MaterialTheme.colorScheme.onErrorContainer)
    Surface(color = bg, shape = MaterialTheme.shapes.medium,
        modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp).clickable(onClickLabel = stringResource(R.string.cal_tasks_review), onClick = onReview)) {
        Row(Modifier.padding(start = 16.dp, end = 8.dp, top = 6.dp, bottom = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Outlined.SyncProblem, null, tint = fg)
            Spacer(Modifier.width(12.dp))
            Text(pluralStringResource(R.plurals.cal_tasks_changed_elsewhere, n, n), Modifier.weight(1f),
                color = fg)
            TextButton(onClick = onReview) { Text(stringResource(R.string.cal_tasks_review)) }
        }
    }
}

/** The list, or the state that stands in for it. */
@Composable
private fun TaskContent(
    vm: TasksViewModel, view: TaskView, groups: List<TaskGroup>?, all: List<TaskItem>, now: Long, lists: List<CalendarCollection>,
    selection: Set<String>, swipe: Pair<SwipeAction, SwipeAction>, options: TaskScreenOptions, followUps: List<ConversationSummary>?,
    readOnly: Boolean, connected: Boolean?, syncing: Boolean, pane: TaskPane, actions: TaskListActions, today: LocalDate,
) {
    val smart = (view as? TaskView.SmartView)?.smart
    val zone = ZoneId.systemDefault()
    val endOfToday = today.plusDays(1).atStartOfDay(zone).toInstant().toEpochMilli()
    val dueFollowUps = when (smart) {
        Smart.WAITING -> followUps.orEmpty()
        Smart.TODAY -> followUps.orEmpty().filter { c -> c.followUpAt?.let { ReminderPlan.instant(it, zone) }?.let { it < endOfToday } == true }
        else -> emptyList()
    }
    // Completed is paged: 50 at a time, more as the list nears its end.
    var limit by remember(view) { mutableIntStateOf(50) }
    val state = rememberLazyListState()
    val shown = if (smart == Smart.COMPLETED) page(groups.orEmpty(), limit) else groups.orEmpty()
    if (smart == Smart.COMPLETED) LaunchedEffect(state, groups) {
        snapshotFlow { state.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: 0 }.collect { last ->
            if (last >= state.layoutInfo.totalItemsCount - 5 && (groups?.sumOf { it.count } ?: 0) > limit) limit += 50
        }
    }
    when {
        connected == false -> EmptyState(Icons.Outlined.TaskAlt, stringResource(R.string.cal_tasks_not_set_up),
            stringResource(R.string.cal_tasks_not_set_up_body),
            action = stringResource(R.string.cal_check_again) to vm::refresh)
        groups == null -> ConversationSkeleton(6)
        smart == Smart.WAITING && dueFollowUps.isEmpty() -> EmptyState(Icons.Outlined.TaskAlt, stringResource(R.string.cal_tasks_nobody_to_wait),
            stringResource(R.string.cal_tasks_nobody_to_wait_body))
        groups.isEmpty() && dueFollowUps.isEmpty() -> {
            val tomorrow = all.count { TaskQueries.open(it) && TaskGroups.dueDay(it, zone) == today.plusDays(1) }
            when {
                !options.query.isNullOrBlank() -> EmptyState(Icons.Outlined.Search, stringResource(R.string.cal_tasks_no_match),
                    stringResource(R.string.cal_tasks_no_match_body))
                smart == Smart.TODAY -> EmptyState(Icons.Outlined.TaskAlt, stringResource(R.string.cal_tasks_nothing_today),
                    if (tomorrow > 0) null else stringResource(R.string.cal_tasks_nothing_today_body),
                    action = if (tomorrow > 0) pluralStringResource(R.plurals.cal_tasks_tomorrow, tomorrow, tomorrow) to { vm.open(TaskView.SmartView(Smart.UPCOMING)) } else null)
                smart == Smart.COMPLETED -> EmptyState(Icons.Outlined.TaskAlt, stringResource(R.string.cal_tasks_nothing_completed), null)
                readOnly -> EmptyState(Icons.Outlined.TaskAlt, stringResource(R.string.cal_tasks_no_tasks), stringResource(R.string.cal_tasks_list_read_only))
                else -> EmptyState(Icons.Outlined.TaskAlt, stringResource(R.string.cal_tasks_no_tasks), stringResource(R.string.cal_tasks_no_tasks_body))
            }
        }
        else -> PullToRefreshBox(syncing, vm::refresh, Modifier.fillMaxSize()) {
            val manual = view is TaskView.ListView && (options.sort == null || options.sort == TaskSort.MANUAL) && !readOnly && options.query.isNullOrBlank()
            TaskGroupList(shown, now, lists, selection, swipe,
                RowStyle(showList = view !is TaskView.ListView, manual = manual, expandSubtasks = view is TaskView.ListView),
                actions, followUps = dueFollowUps,
                followUpTitle = when (smart) { Smart.TODAY -> stringResource(R.string.cal_tasks_follow_ups); Smart.WAITING -> stringResource(R.string.cal_tasks_waiting_for_reply); else -> null },
                highlighted = (pane as? TaskPane.Edit)?.uid, state = state)
        }
    }
}

private fun page(groups: List<TaskGroup>, limit: Int): List<TaskGroup> {
    var left = limit
    return groups.mapNotNull { g ->
        if (left <= 0) return@mapNotNull null
        val e = g.entries.take(left)
        left -= e.size
        g.copy(entries = e)
    }
}

/** The sheets and dialogs, one at a time; each action site carries its ACT id. */
@OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)
@Composable
private fun TaskSheets(vm: TasksViewModel, sheet: TaskSheet?, set: (TaskSheet?) -> Unit, morningHour: Int, lists: List<CalendarCollection>,
                       listCounts: List<ListCount>, knownTags: List<String>, all: List<TaskItem>, selection: Set<String>, view: TaskView) {
    val close = { set(null) }
    @Composable
    fun Bottom(content: @Composable () -> Unit) {
        ModalBottomSheet(onDismissRequest = close, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) { content() }
    }
    when (val s = sheet) {
        null -> Unit
        is TaskSheet.Reschedule -> Bottom {
            RescheduleSheet(s.task, morningHour,
                // ACT task.reschedule @android.tasks
                // A repeating task asks the series question first ("This occurrence" or "All").
                onReschedule = { due -> close(); vm.requestReschedule(s.task, due) },
                // ACT task.set_start @android.tasks
                onDefer = { start -> close(); vm.setStart(s.task, start) },
                // ACT task.remind_at @android.tasks
                onRemind = { m -> close(); vm.remindAt(s.task, m) },
                onRemindAt = { at -> close(); vm.remindAtTime(s.task, at) },
                // ACT task.skip
                onSkip = if (s.task.recurring) ({ close(); vm.skip(s.task) }) else null,
                initial = s.verb)
        }
        // ACT task.move_list @android.editor @android.tasks
        is TaskSheet.Move -> Bottom { ListPickerSheet(stringResource(R.string.cal_tasks_move_to_list), lists, s.task.list) { url -> close(); vm.moveToList(s.task, url) } }
        // ACT task.bulk_move
        TaskSheet.BulkMove -> Bottom { ListPickerSheet(pluralStringResource(R.plurals.cal_tasks_move_n_to, selection.size, selection.size), lists, null) { url -> close(); vm.bulkMove(url) } }
        // ACT task.bulk_reschedule
        TaskSheet.BulkReschedule -> Bottom {
            BulkRescheduleSheet(selection.size, morningHour, onDue = { d -> close(); vm.bulkReschedule(d) }, onShift = { n -> close(); vm.bulkShift(n) })
        }
        // ACT task.bulk_tag
        TaskSheet.BulkTags -> TagsDialog(knownTags, all.filter { it.uid in selection }, close) { add, remove -> close(); vm.bulkTag(add, remove) }
        // ACT task.bulk_priority
        TaskSheet.BulkPriority -> PriorityDialog(null, close) { lvl -> close(); vm.bulkPriority(lvl) }
        // ACT task.bulk_delete
        TaskSheet.BulkDelete -> AlertDialog(onDismissRequest = close, title = { Text(pluralStringResource(R.plurals.cal_tasks_delete_n, selection.size, selection.size)) },
            text = { Text(stringResource(R.string.cal_tasks_undo_seconds)) },
            confirmButton = { TextButton(onClick = { close(); vm.bulkDelete() }, modifier = Modifier.refuseObscuredTouches()) { Text(stringResource(R.string.cal_delete), color = MaterialTheme.colorScheme.error) } },
            dismissButton = { TextButton(onClick = close) { Text(stringResource(R.string.cal_cancel)) } })
        is TaskSheet.CompleteSubtasks -> AlertDialog(onDismissRequest = close,
            title = { Text(pluralStringResource(R.plurals.cal_tasks_complete_subtasks, s.open, s.open)) },
            text = { Text(stringResource(R.string.cal_tasks_has_open_subtasks, s.task.summary.take(60))) },
            // ACT task.complete (with its subtasks; one undo covers all)
            confirmButton = { TextButton(onClick = { close(); vm.complete(s.task, withSubtasks = true) }) { Text(stringResource(R.string.cal_tasks_complete_all)) } },
            dismissButton = { TextButton(onClick = { close(); vm.complete(s.task) }) { Text(stringResource(R.string.cal_tasks_only_this_one)) } })
        // ACT mail.follow_up @android.tasks
        is TaskSheet.FollowUpDate -> FollowUpDialog(s.c.followUpAt, close) { until -> close(); vm.changeFollowUp(s.c, until) }
        is TaskSheet.Conflict -> Bottom {
            ConflictSheet(s.c, onTheirs = { close(); vm.keepTheirs(s.c) }, onMine = { close(); vm.keepMine(s.c) }, onBoth = { close(); vm.keepBoth(s.c) })
        }
        is TaskSheet.Lists -> androidx.compose.ui.window.Dialog(onDismissRequest = close,
            properties = androidx.compose.ui.window.DialogProperties(usePlatformDefaultWidth = false)) {
            Surface(Modifier.fillMaxSize()) {
                val tagInfos by vm.tags.collectAsStateWithLifecycle()
                val defs by vm.defaults.collectAsStateWithLifecycle()
                ListManager(listCounts, tagInfos, ListManagerActions(
                    // ACT list.create @android.tasks
                    create = { n, c -> vm.createList(n, c) },
                    // ACT list.rename @android.tasks, ACT list.color @android.tasks
                    update = { c, n, col -> vm.updateList(c, n, col) },
                    // ACT list.reorder @android.tasks
                    reorder = { vm.reorderLists(it) },
                    // ACT list.delete @android.tasks
                    delete = { c, moveTo -> vm.deleteList(c, moveTo, c.name) },
                    // ACT tag.rename @android.tasks
                    renameTag = { o, n -> vm.renameTag(o, n) },
                    // ACT tag.delete @android.tasks
                    deleteTag = { vm.deleteTag(it) },
                    // ACT list.set_default @android.tasks
                    setDefault = { vm.setDefaultList(it) },
                    // ACT tag.color @android.tasks
                    tagColor = { tag, color -> vm.setTagColor(tag, color) },
                    close = close,
                    // ACT event.export_ics @android.tasks: "Download a copy first" in the delete dialog.
                    exported = { vm.say(it) },
                ), startNew = s.startNew, defaultList = defs.taskList)
            }
        }
        // ACT task.clear_completed @android.tasks: confirmed with the count; no undo.
        TaskSheet.ClearCompleted -> {
            val url = (view as? TaskView.ListView)?.url
            val n = all.count { it.completed && (url == null || it.list == url) }
            AlertDialog(onDismissRequest = close, title = { Text(if (n == 0) stringResource(R.string.cal_tasks_no_completed) else pluralStringResource(R.plurals.cal_tasks_delete_n_completed, n, n)) },
                text = { Text(if (n == 0) stringResource(R.string.cal_tasks_nothing_to_delete) else stringResource(R.string.cal_tasks_deleted_on_server)) },
                confirmButton = {
                    if (n > 0) TextButton(onClick = { close(); vm.clearCompleted(url) }, modifier = Modifier.refuseObscuredTouches()) { Text(stringResource(R.string.cal_delete), color = MaterialTheme.colorScheme.error) }
                    else TextButton(onClick = close) { Text(stringResource(R.string.cal_ok)) }
                },
                dismissButton = { if (n > 0) TextButton(onClick = close) { Text(stringResource(R.string.cal_cancel)) } })
        }
        TaskSheet.Swipes -> {
            val sw by vm.swipe.collectAsStateWithLifecycle()
            SwipeSettingsDialog(sw.first, sw.second, close) { r, l -> close(); vm.setSwipe(r, l) }
        }
    }
}

/** An open task saves this long after the last change (as the composer does). */
internal const val AUTOSAVE_MS = 1500L

@Composable
internal fun TaskEditorFor(
    vm: TasksViewModel, p: TaskPane.Edit, all: List<TaskItem>, lists: List<CalendarCollection>,
    defaultList: String?, filter: String?, morningHour: Int, saving: Boolean, onSaving: (Boolean) -> Unit,
    onDirty: (Boolean) -> Unit = {}, onClose: () -> Unit, onOpen: (TaskPane) -> Unit, openEmail: (Long) -> Unit = {},
    replyEmail: ((Long) -> Unit)? = null, knownTags: List<String> = emptyList(),
    askAssistant: ((TaskItem) -> Unit)? = null,
) {
    val orig = p.uid?.let { uid -> all.firstOrNull { it.uid == uid } }
    if (p.uid != null && orig == null) {
        EmptyState(Icons.Outlined.TaskAlt, stringResource(R.string.cal_tasks_not_found), stringResource(R.string.cal_tasks_not_found_body))
        return
    }
    val parentList = p.parent?.let { pu -> all.firstOrNull { it.uid == pu }?.list }
    val s = remember(p) {
        TaskEditorState(orig, parentList ?: filter ?: defaultList, p.parent, orig?.uid ?: p.draft?.uid ?: vm.newUid(), p.prefill, p.draft)
    }
    // An open task saves by itself (a pause in typing, and leaving), so only a new one asks before it is discarded,
    // and a repeating task whose due moved (that save asks which occurrences move, so it waits for Save).
    LaunchedEffect(s) {
        snapshotFlow { s.dirty && (s.isNew || s.original?.let { o -> o.recurring && o.due != null && s.draft().due.let { it != null && it != o.due } } == true) }
            .collect(onDirty)
    }
    // Live editing (data/live): the task's version goes with each save; changes others make come in.
    val g = vm.graph
    val saveRef = remember(s) { arrayOf<() -> Unit>({}) }
    val port = remember(s) { if (s.isNew) null else TaskLivePort(s) { saveRef[0]() } }
    val live = rememberLiveSession(g, "task", if (s.isNew) null else s.uid, port, rebased = { port?.rebased(it) })
    val liveUi = live?.ui?.collectAsStateWithLifecycle()

    // The task as the editor opened it, and whether a save went through since: leaving then offers Undo.
    val opened = remember(s) { orig }
    val savedAny = remember(s) { booleanArrayOf(false) }
    val activity = LocalContext.current as? android.app.Activity

    /** Save the task with its live base; [quiet] for an autosave. */
    fun saveLive(quiet: Boolean, then: (Boolean) -> Unit = {}) {
        val o = orig ?: return
        val session = live ?: return
        val lp = port ?: return
        if (!s.valid) return then(false)
        val sentFields = s.fields()
        val sent = lp.mine()
        val sentBody = TaskLivePort.body(s)
        val ls = io.github.murtazanasir.mailr.data.cal.LiveSave(session.baseVersion, lp.baseline, onRefused = { body -> session.refused(body) })
        vm.save(o, s.draft(), live = ls, quiet = quiet) { ok ->
            if (ok) {
                savedAny[0] = true
                s.markClean(sentFields)
                lp.baseline = sentBody
                if (!ls.unchanged) session.saved(sent, ls.version)
            }
            then(ok)
        }
    }
    saveRef[0] = { saveLive(quiet = true) }
    // Autosave: 1.5 s after the last change; typing is reported to the server (at most once a second).
    LaunchedEffect(s, live) {
        val session = live ?: return@LaunchedEffect
        snapshotFlow { s.fields() }.drop(1).collectLatest {
            session.typed()
            kotlinx.coroutines.delay(AUTOSAVE_MS)
            if (s.dirty && s.valid && session.ui.value.conflict == null) saveLive(quiet = true)
        }
    }
    // Leaving the editor saves what is not saved yet.
    DisposableEffect(s) {
        onDispose {
            val leaving = activity?.isChangingConfigurations != true
            if (!s.isNew && s.dirty && s.valid && live?.ui?.value?.conflict == null) {
                saveLive(quiet = true) { ok -> if (ok && leaving) opened?.let(vm::offerRestore) }
            } else if (savedAny[0] && leaving) opened?.let(vm::offerRestore)
        }
    }
    val subtasks = orig?.let { o -> all.filter { it.parent == o.uid } }.orEmpty()
    TaskEditor(s, lists.filterNot { it.readOnly && it.url != s.list }, subtasks, morningHour, saving,
        onClose = onClose,
        onSave = {
            onSaving(true)
            if (orig != null && live != null) saveLive(quiet = false) { ok -> onSaving(false); if (ok) onClose() }
            else vm.save(orig, s.draft()) { ok -> onSaving(false); if (ok) onClose() }
        },
        live = liveUi?.let { u -> {
            // ACT task.live_update @android.editor, ACT task.resolve_conflict @android.editor
            io.github.murtazanasir.mailr.ui.components.LiveBars(u.value, "task", io.github.murtazanasir.mailr.ui.components.LiveBarActions(
                undo = { live.undo() }, dismiss = { live.dismissNotice() }, useMine = { live.useMine() }, useTheirs = { live.useTheirs() }))
        } },
        onDelete = orig?.let { o -> { onClose(); vm.delete(o) } },
        // ACT task.subtask_add @android.editor @android.tasks: one step of the editor's Undo (the subtask goes; Redo adds it again).
        onAddSubtask = orig?.takeIf { it.parent == null }?.let { o -> { text: String ->
            var uid = vm.quickAdd(text, parent = o.uid)
            if (uid != null) s.history.recordAction("subtask",
                undo = { uid?.let { vm.removeSubtask(it) } == true },
                redo = { uid = vm.quickAdd(text, parent = o.uid); uid != null })
        } },
        // A tick is one step of the editor's Undo too.
        onToggleSubtask = { st ->
            val done = !st.completed
            vm.setDone(st, done)
            s.history.recordAction("tick", undo = { vm.setDoneNow(st.uid, !done) }, redo = { vm.setDoneNow(st.uid, done) })
        },
        onOpenSubtask = { st -> onOpen(TaskPane.Edit(st.uid)) },
        onOpenEmail = orig?.conversationId?.let { id -> { openEmail(id) } },
        knownTags = knownTags,
        // ACT task.reply_email @android.editor: the reply composer when the app offers one, else the email (its Reply is one tap away).
        onReplyEmail = orig?.conversationId?.let { id -> { (replyEmail ?: openEmail)(id) } },
        // ACT task.duplicate @android.tasks
        onDuplicate = orig?.let { o -> { onClose(); vm.duplicate(o) } },
        onSkip = orig?.takeIf { it.recurring }?.let { o -> { onClose(); vm.skip(o) } },
        onAskAssistant = orig?.let { o -> askAssistant?.let { a -> { a(o) } } },
        undoRedo = true,
        onSay = vm::say,
    )
}
