package io.github.murtazanasir.mailr.ui.calendar

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.ui.tasks.TaskPlace
import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.data.api.ApiException
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import io.github.murtazanasir.mailr.data.api.ConversationSummary
import io.github.murtazanasir.mailr.data.api.TaskDraft
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.api.toDraft
import io.github.murtazanasir.mailr.data.api.userMessage
import io.github.murtazanasir.mailr.data.cal.BulkOp
import io.github.murtazanasir.mailr.data.cal.CalConflict
import io.github.murtazanasir.mailr.data.cal.ListCount
import io.github.murtazanasir.mailr.data.cal.TaskQueries
import io.github.murtazanasir.mailr.data.cal.TaskSort
import io.github.murtazanasir.mailr.data.cal.WriteResult
import io.github.murtazanasir.mailr.data.reminders.ReminderPlan
import io.github.murtazanasir.mailr.ui.mail.UiMessage
import io.github.murtazanasir.mailr.ui.tasks.DueChip
import io.github.murtazanasir.mailr.ui.tasks.GroupOptions
import io.github.murtazanasir.mailr.ui.tasks.QuickPrefill
import io.github.murtazanasir.mailr.ui.tasks.QuickTask
import io.github.murtazanasir.mailr.ui.tasks.Smart
import io.github.murtazanasir.mailr.ui.tasks.SwipeAction
import io.github.murtazanasir.mailr.ui.tasks.TaskGroup
import io.github.murtazanasir.mailr.ui.tasks.TaskGroups
import io.github.murtazanasir.mailr.ui.tasks.TaskUiPrefs
import io.github.murtazanasir.mailr.ui.tasks.TaskView
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.ZonedDateTime

/** A new due for a repeating task, waiting for the series question; [draft] is the editor's whole change, if it came from there. */
data class DuePrompt(val task: TaskItem, val due: String, val draft: TaskDraft?, val onDone: (Boolean) -> Unit = {})

/** The screen's own settings beside the view: sort, completed, deferred, search. */
data class TaskScreenOptions(val sort: TaskSort? = null, val showCompleted: Boolean = false, val showDeferred: Boolean = false,
                             val query: String? = null)

/**
 * The Tasks app on the phone (surface android.tasks). Everything is
 * read from Room through [io.github.murtazanasir.mailr.data.cal.CalendarRepository], so the lists, their
 * counts and every change work offline; writes change Room first and undo through the same
 * action family.
 */
class TasksViewModel(private val g: AppGraph, private val clock: () -> Long = AppClock::millis) : ViewModel() {
    /** The app graph, for the task editor's live editing (LiveEditors.kt). */
    internal val graph: AppGraph get() = g
    private val zone: ZoneId get() = ZoneId.systemDefault()
    private val prefs = TaskUiPrefs(g.context)

    private val _view = MutableStateFlow(prefs.view ?: TaskView.TODAY)
    val view: StateFlow<TaskView> = _view.asStateFlow()
    private val _options = MutableStateFlow(TaskScreenOptions())
    val options: StateFlow<TaskScreenOptions> = _options.asStateFlow()
    private val _messages = MutableSharedFlow<UiMessage>(extraBufferCapacity = 8)
    val messages: SharedFlow<UiMessage> = _messages
    private val _loadingCompleted = MutableStateFlow(false)
    val loadingCompleted: StateFlow<Boolean> = _loadingCompleted.asStateFlow()
    private val _selection = MutableStateFlow<Set<String>>(emptySet())
    val selection: StateFlow<Set<String>> = _selection.asStateFlow()
    /** What a swipe to the right and to the left do (a setting). */
    val swipe: StateFlow<Pair<SwipeAction, SwipeAction>> = prefs.swipes()
        .stateIn(viewModelScope, SharingStarted.Eagerly, prefs.swipeRight to prefs.swipeLeft)
    private val _duePrompt = MutableStateFlow<DuePrompt?>(null)
    /** A new due for a repeating task waits for the series question ("This occurrence" or "All"). */
    val duePrompt: StateFlow<DuePrompt?> = _duePrompt.asStateFlow()
    private val _lastAdded = MutableStateFlow<Pair<String, String>?>(null)
    /** The last top-level task quick add created (UID, title): the parent in subtask mode. */
    val lastAdded: StateFlow<Pair<String, String>?> = _lastAdded.asStateFlow()
    private val _subtaskMode = MutableStateFlow(false)
    /** Quick add's subtask mode (the web's Tab): new lines become subtasks of [lastAdded]. */
    val subtaskMode: StateFlow<Boolean> = _subtaskMode.asStateFlow()
    private val _followUps = MutableStateFlow<List<ConversationSummary>?>(null)
    /** Conversations with a follow-up reminder set (mail.follow_up), for "Waiting for reply" and Today. */
    val followUps: StateFlow<List<ConversationSummary>?> = _followUps.asStateFlow()

    /** A minute ticker so sections, chips and counts follow the clock. */
    private val ticks = flow {
        while (true) {
            emit(clock())
            delay(60_000 - clock() % 60_000)
        }
    }
    val now: StateFlow<Long> = ticks.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), clock())

    val connected = g.calendar.connected
    val syncing = g.calendar.syncing
    /** "offline", or the last sync error. */
    val error = g.calendar.error
    val collections: StateFlow<List<CalendarCollection>> = g.calendar.collections.map { cols -> cols.filter { it.tasks } }
        .stateIn(viewModelScope, SharingStarted.Eagerly, emptyList())
    val defaults = g.calendar.defaults
    val tasks: StateFlow<List<TaskItem>?> = g.calendar.tasks().stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), null)
    val lists: StateFlow<List<ListCount>> = g.calendar.listsWithCounts().stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())
    val tags = g.calendar.tags().stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())
    val conflicts: StateFlow<List<CalConflict>> = g.calendar.conflicts.map { cs -> cs.filter { it.kind == "task" } }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())

    /** Open counts per smart list (and "overdue"), from Room. */
    val counts: StateFlow<Map<String, Int>> = combine(tasks, now) { t, n -> TaskGroups.counts(t.orEmpty(), n, zone) }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyMap())

    val groups: StateFlow<List<TaskGroup>?> = combine(tasks, _view, _options, now, collections) { t, v, o, n, cols ->
        t?.let { TaskGroups.build(v, it, n, zone, GroupOptions(o.sort, o.showCompleted, o.showDeferred, o.query, cols)) }
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), null)

    /** The list the view is about (a list view), for the old callers and the quick-add default. */
    val list: StateFlow<String?> = _view.map { (it as? TaskView.ListView)?.url }.stateIn(viewModelScope, SharingStarted.Eagerly, null)
    val showCompleted: StateFlow<Boolean> = _options.map { it.showCompleted }.stateIn(viewModelScope, SharingStarted.Eagerly, false)

    init {
        g.syncCalendarSoon()
        viewModelScope.launch { catching { g.calendar.refreshTags() } }
        loadFollowUps()
    }

    // ---------------- the view ----------------

    fun open(v: TaskView) {
        _view.value = v
        _selection.value = emptySet()
        _options.value = _options.value.copy(sort = null, query = null, showCompleted = v == TaskView.SmartView(Smart.COMPLETED))
        prefs.view = v
        if (v == TaskView.SmartView(Smart.WAITING) || v == TaskView.TODAY) loadFollowUps()
        if (v == TaskView.SmartView(Smart.COMPLETED)) loadCompleted()
    }

    /**
     * A list from a link in the web's route form: `today` and the other smart lists, `l/<id>`
     * (the numeric id or the URL) or `t/<tag>`. An unknown route leaves the view as it is.
     */
    fun openRoute(route: String) {
        val v = TaskView.parse(route) ?: return
        if (v is TaskView.ListView && v.url.all(Char::isDigit)) {
            val url = collections.value.firstOrNull { it.calendarId?.toString() == v.url }?.url ?: return
            open(TaskView.ListView(url))
        } else open(v)
    }

    /**
     * A task a link names (`/tasks/task/<uid>`): Room first, else GET /tasks/{uid}, so a task this
     * phone has not synced (completed long ago, or just made elsewhere) still opens.
     */
    suspend fun ensureTask(uid: String): TaskItem? = g.calendar.fetchTask(uid)

    fun say(text: String) { viewModelScope.launch { _messages.emit(UiMessage(text)) } }

    /** Old entry point: a list, or every list. */
    fun setList(url: String?) = open(url?.let { TaskView.ListView(it) } ?: TaskView.SmartView(Smart.ALL))

    fun setSort(s: TaskSort?) { _options.value = _options.value.copy(sort = s) }

    fun toggleCompleted() {
        val on = !_options.value.showCompleted
        _options.value = _options.value.copy(showCompleted = on)
        if (on) loadCompleted()
    }

    fun toggleDeferred() { _options.value = _options.value.copy(showDeferred = !_options.value.showDeferred) }

    /** ACT task.search @android.tasks: null closes the search. */
    fun search(q: String?) { _options.value = _options.value.copy(query = q) }

    fun setSwipe(right: SwipeAction, left: SwipeAction) {
        prefs.swipeRight = right
        prefs.swipeLeft = left
    }

    private fun loadCompleted() {
        viewModelScope.launch {
            _loadingCompleted.value = true
            catching { g.calendar.syncTasks(includeCompleted = true) }
            _loadingCompleted.value = false
        }
    }

    fun refresh() {
        viewModelScope.launch {
            if (!g.calendar.sync()) g.calendar.error.value?.let { e ->
                _messages.emit(UiMessage(if (e == "offline") AppText.get(R.string.cal_offline_showing_full) else e))
            }
            if (_options.value.showCompleted) catching { g.calendar.syncTasks(includeCompleted = true) }
        }
        loadFollowUps()
    }

    // ---------------- selection ----------------

    fun toggleSelect(uid: String) {
        _selection.value = _selection.value.let { if (uid in it) it - uid else it + uid }
    }

    fun selectAll() { _selection.value = groups.value.orEmpty().flatMap { g -> g.entries.map { it.task.uid } }.toSet() }
    fun clearSelection() { _selection.value = emptySet() }

    // ---------------- create and edit ----------------

    /**
     * ACT task.create @android.editor @android.tasks, ACT task.subtask_add @android.editor @android.tasks: one quick-add line through the shared grammar.
     * [keep] holds the words turned back into text; [prefill] what the view adds. A "#New list"
     * the line names is created first. Returns the new task's UID (null when nothing was added).
     */
    fun quickAdd(text: String, parent: String? = null, keep: Set<String> = emptySet(), prefill: QuickPrefill? = null): String? {
        val now = ZonedDateTime.ofInstant(Instant.ofEpochMilli(clock()), zone)
        val pre = (prefill ?: QuickPrefill.of(_view.value, now.toLocalDate())).copy(parent = parent)
        val r = QuickTask.parse(text, now, collections.value, keep)
        if (r.title.isBlank()) return null
        val plan = QuickTask.plan(r, g.calendar.newUid(), zone, if (parent != null) pre.copy(day = null, tag = null) else pre)
        viewModelScope.launch {
            var d = plan.draft
            plan.createList?.let { name ->
                // ACT list.create @android.tasks, from "#Name" in a quick-add line.
                catching { g.calendar.createCollection(name, kind = "tasks") }
                    .onSuccess { d = d.copy(list = it.url) }
                    .onFailure { _messages.emit(UiMessage(AppText.get(R.string.cal_tasks_create_list_failed, name, it.userMessage()))) }
            }
            if (d.parent != null) d = d.copy(list = d.list ?: tasks.value?.firstOrNull { it.uid == d.parent }?.list)
            save(null, d)
        }
        return plan.draft.uid
    }

    /**
     * ACT task.subtask_add (subtask mode on) or ACT task.create: a quick-add line. In subtask
     * mode the line becomes a subtask of the last task added; otherwise it becomes that task.
     */
    fun quickAddLine(text: String, keep: Set<String> = emptySet(), prefill: QuickPrefill? = null): String? {
        val parent = _lastAdded.value?.first?.takeIf { _subtaskMode.value }
        val uid = quickAdd(text, parent = parent, keep = keep, prefill = prefill) ?: return null
        if (parent == null) {
            val title = QuickTask.parse(text, ZonedDateTime.ofInstant(Instant.ofEpochMilli(clock()), zone), collections.value, keep).title
            _lastAdded.value = uid to title
        }
        return uid
    }

    /** Subtask mode on or off (a toggle chip under the quick-add bar). */
    fun setSubtaskMode(on: Boolean) { _subtaskMode.value = on && _lastAdded.value != null }

    /**
     * ACT task.edit @android.editor @android.tasks, ACT task.reschedule @android.editor @android.tasks (a new due from the editor), or a new task. A new due
     * on a repeating task asks the series question first, unless [scope] is given.
     */
    /**
     * Save the editor's task. [live]: the editor's live-editing base (an autosave or Save of an
     * open task); a live conflict goes to the editor's bar ([LiveSave.onRefused]), not a message.
     * [quiet]: an autosave, which says nothing when it worked.
     */
    fun save(orig: TaskItem?, d: TaskDraft, scope: String? = null, live: io.github.murtazanasir.mailr.data.cal.LiveSave? = null,
             quiet: Boolean = false, onDone: (Boolean) -> Unit = {}) {
        if (orig != null && orig.recurring && scope == null && d.due != null && orig.due != null && d.due != orig.due) {
            if (quiet) return  // a repeating task's new due asks which ones move: that waits for Save
            _duePrompt.value = DuePrompt(orig, d.due, d, onDone)
            return
        }
        viewModelScope.launch {
            try {
                val rule = if (orig != null && scope != null && d.recurrence == null)
                    TaskQueries.ruleAfterMove(orig.rrule, orig.due, d.due, scope, zone) else null
                val dd = if (rule != null) d.copy(recurrence = rule) else d
                val r = if (orig == null) g.calendar.createTask(dd) else g.calendar.updateTask(orig, dd, live)
                if (r == WriteResult.QUEUED && !quiet) _messages.emit(UiMessage(AppText.get(R.string.cal_saved_offline)))
                onDone(true)
            } catch (e: ApiException) {
                if (io.github.murtazanasir.mailr.data.cal.LiveSave.conflictOf(e) == null || live == null) _messages.emit(UiMessage(e.userMessage()))
                onDone(false)
            }
        }
    }

    /** ACT task.duplicate @android.editor @android.tasks: a copy with its subtasks. */
    fun duplicate(t: TaskItem) = act(AppText.get(R.string.cal_tasks_fail_duplicate)) {
        val copy = g.calendar.newUid()
        g.calendar.createTask(full(t).copy(uid = copy, summary = t.summary, sortOrder = t.sortOrder?.plus(1)))
        g.calendar.subtasks(t.uid).forEach { c -> g.calendar.createTask(full(c).copy(uid = g.calendar.newUid(), parent = copy)) }
        UiMessage(AppText.get(R.string.cal_tasks_duplicated, t.summary.take(40)), AppText.get(R.string.cal_undo)) {
            g.calendar.task(copy)?.let { g.calendar.deleteTask(it, cascade = true) }
        }
    }

    /** A draft that carries every field of [t] (TaskItem.toDraft leaves the newer ones unchanged). */
    private fun full(t: TaskItem): TaskDraft = t.toDraft().copy(start = t.start, recurrence = t.rrule, repeatFrom = t.repeatFrom,
        categories = t.categories, sortOrder = t.sortOrder, tz = t.tz)

    // ---------------- complete, reopen, skip ----------------

    /** Old entry point. ACT task.complete @android.tasks (done), ACT task.reopen @android.tasks (not done). */
    fun setDone(t: TaskItem, done: Boolean) = if (done) complete(t) else reopen(t)

    /**
     * ACT task.complete: a repeating task advances ("Next: Fri"); with
     * [withSubtasks] its open subtasks are completed too, under one undo.
     */
    fun complete(t: TaskItem, withSubtasks: Boolean = false) = act(AppText.get(R.string.cal_tasks_fail_complete)) {
        val kids = if (withSubtasks) g.calendar.subtasks(t.uid).filter { TaskQueries.open(it) } else emptyList()
        if (kids.isNotEmpty()) {
            val uids = listOf(t.uid) + kids.map { it.uid }
            g.calendar.bulk(uids, BulkOp.COMPLETE)
            return@act UiMessage(AppText.plural(R.plurals.cal_tasks_completed_with_subtasks, kids.size, t.summary.take(30), kids.size), AppText.get(R.string.cal_undo)) {
                g.calendar.bulk(uids, BulkOp.REOPEN)
            }
        }
        val r = g.calendar.setTaskDone(t.uid, true)
        val fresh = g.calendar.task(t.uid)
        if (t.recurring && fresh != null && !fresh.completed && fresh.due != t.due) {
            val next = DueChip.next(fresh.due, clock(), zone)
            // Undo of an advance: PATCH completed false restores the due and deletes the history copy.
            UiMessage(AppText.get(R.string.cal_tasks_next, next.toString()), AppText.get(R.string.cal_undo)) { g.calendar.setTaskDone(t.uid, false) }
        } else UiMessage(if (r == WriteResult.QUEUED) AppText.get(R.string.cal_tasks_completed_queued, t.summary.take(40)) else AppText.get(R.string.cal_completed_task, t.summary.take(40)), AppText.get(R.string.cal_undo)) { g.calendar.setTaskDone(t.uid, false) }
    }

    /** ACT task.reopen @android.tasks @android.editor: a task (or a subtask) back to open; Undo completes it again. */
    fun reopen(t: TaskItem) = act(AppText.get(R.string.cal_tasks_fail_reopen)) {
        g.calendar.setTaskDone(t.uid, false)
        UiMessage(AppText.get(R.string.cal_tasks_reopened, t.summary.take(40)), AppText.get(R.string.cal_undo)) { g.calendar.setTaskDone(t.uid, true) }
    }

    /** ACT task.skip @android.tasks: the next occurrence without a completed copy. */
    fun skip(t: TaskItem) = act(AppText.get(R.string.cal_tasks_fail_skip)) {
        g.calendar.skipTask(t.uid)
        val fresh = g.calendar.task(t.uid)
        // The server undoes the last advance (complete or skip) on PATCH completed false.
        UiMessage(AppText.get(R.string.cal_tasks_skipped_next, DueChip.next(fresh?.due, clock(), zone) ?: AppText.get(R.string.cal_tasks_none)), AppText.get(R.string.cal_undo)) { g.calendar.setTaskDone(t.uid, false) }
    }

    /** The task editor's Undo of a tick (TaskHistory.kt): the subtask done or open again, without a message of its own. */
    suspend fun setDoneNow(uid: String, done: Boolean): Boolean {
        g.calendar.task(uid) ?: return false
        g.calendar.setTaskDone(uid, done)
        return true
    }

    /** The task editor's Undo of an added subtask: it goes (false while it is not there yet). */
    suspend fun removeSubtask(uid: String): Boolean {
        val t = g.calendar.task(uid) ?: return false
        g.calendar.deleteTask(t)
        return true
    }

    // ---------------- single-task verbs ----------------

    /** ACT task.reschedule: a repeating task with a new due asks the series question first. */
    fun requestReschedule(t: TaskItem, due: String?) {
        if (t.recurring && due != null && t.due != null && due != t.due) _duePrompt.value = DuePrompt(t, due, null)
        else reschedule(t, due)
    }

    /** The series question answered for [duePrompt]: "this" or "all"; null cancels the change. */
    fun answerDueScope(scope: String?) {
        val p = _duePrompt.value ?: return
        _duePrompt.value = null
        if (scope == null) { p.onDone(false); return }
        if (p.draft != null) save(p.task, p.draft, scope, onDone = p.onDone) else reschedule(p.task, p.due, scope)
    }

    /** ACT task.reschedule: the due (null = no date); [scope] for a repeating task. Undo puts the due and rule back. */
    fun reschedule(t: TaskItem, due: String?, scope: String? = null) = act(AppText.get(R.string.cal_tasks_fail_reschedule)) {
        g.calendar.rescheduleTask(t.uid, due, scope)
        val label = due?.let { DueChip.next(it, clock(), zone) }
        UiMessage(if (label == null) AppText.get(R.string.cal_task_no_date) else AppText.get(R.string.cal_rescheduled_to, label), AppText.get(R.string.cal_undo)) {
            g.calendar.task(t.uid)?.let { cur -> g.calendar.updateTask(cur, cur.toDraft().copy(due = t.due, recurrence = t.rrule?.takeIf { it != cur.rrule })) }
        }
    }

    /** ACT task.set_start @android.editor @android.tasks: defer (the task is hidden from Today until then); null clears it. */
    fun setStart(t: TaskItem, start: String?) = act(AppText.get(R.string.cal_tasks_fail_start)) {
        val cur = g.calendar.task(t.uid) ?: t
        g.calendar.updateTask(cur, cur.toDraft().copy(start = start, clearStart = start == null))
        UiMessage(if (start == null) AppText.get(R.string.cal_tasks_start_removed) else AppText.get(R.string.cal_qa_starts, DueChip.next(start, clock(), zone).toString()), AppText.get(R.string.cal_undo)) {
            g.calendar.task(t.uid)?.let { c -> g.calendar.updateTask(c, c.toDraft().copy(start = t.start, clearStart = t.start == null)) }
        }
    }

    /** ACT task.remind_at @android.editor @android.tasks: adds a reminder, [minutes] before the due (negative: after). */
    fun remindAt(t: TaskItem, minutes: Int) = act(AppText.get(R.string.cal_tasks_fail_reminder)) {
        val cur = g.calendar.task(t.uid) ?: t
        if (cur.due == null) return@act UiMessage(AppText.get(R.string.cal_tasks_set_due_first))
        g.calendar.updateTask(cur, cur.toDraft().copy(reminders = (cur.reminders + minutes).distinct()))
        UiMessage(AppText.get(R.string.cal_tasks_reminder_added), AppText.get(R.string.cal_undo)) {
            g.calendar.task(t.uid)?.let { c -> g.calendar.updateTask(c, c.toDraft().copy(reminders = cur.reminders)) }
        }
    }

    /** ACT task.remind_at: an absolute reminder at [at] (an instant, `reminders_at`). */
    fun remindAtTime(t: TaskItem, at: String) = act(AppText.get(R.string.cal_tasks_fail_reminder)) {
        val cur = g.calendar.task(t.uid) ?: t
        if (cur.due == null) return@act UiMessage(AppText.get(R.string.cal_tasks_set_due_first))
        val before = cur.remindersAt.orEmpty()
        g.calendar.updateTask(cur, cur.toDraft().copy(remindersAt = (before + at).distinct().sorted()))
        UiMessage(AppText.get(R.string.cal_tasks_reminder_added), AppText.get(R.string.cal_undo)) {
            g.calendar.task(t.uid)?.let { c -> g.calendar.updateTask(c, c.toDraft().copy(remindersAt = before)) }
        }
    }

    /** ACT task.set_priority @android.editor @android.tasks: level 0–3 (None, Low, Medium, High); Undo puts the old one back. */
    fun setPriority(t: TaskItem, level: Int) = act(AppText.get(R.string.cal_tasks_fail_priority)) {
        val cur = g.calendar.task(t.uid) ?: t
        val before = cur.priority
        g.calendar.updateTask(cur, cur.toDraft().copy(priority = CalendarColors.priorityValue(level)))
        UiMessage(AppText.get(R.string.cal_tasks_priority_changed), AppText.get(R.string.cal_undo)) {
            g.calendar.task(t.uid)?.let { c -> g.calendar.updateTask(c, c.toDraft().copy(priority = before)) }
        }
    }

    /**
     * The task editor closed after it saved changes (it saves by itself): one snackbar with Undo that
     * puts the task back as it was when the editor opened ([opened]).
     * ACT task.edit @android.editor
     */
    fun offerRestore(opened: TaskItem) {
        viewModelScope.launch {
            _messages.emit(UiMessage(AppText.get(R.string.cal_tasks_changes_saved), AppText.get(R.string.cal_undo)) {
                try {
                    g.calendar.task(opened.uid)?.let { c -> g.calendar.updateTask(c, full(opened)) }
                } catch (e: ApiException) { _messages.emit(UiMessage(e.userMessage())) }
            })
        }
    }

    /** ACT task.move_list @android.editor @android.tasks: its subtasks follow (the server moves them). */
    fun moveToList(t: TaskItem, url: String) = act(AppText.get(R.string.cal_tasks_fail_move)) {
        if (t.list == url) return@act null
        g.calendar.moveTask(t.uid, list = url)
        val name = collections.value.firstOrNull { it.url == url }?.name ?: AppText.get(R.string.cal_tasks_list_fallback)
        UiMessage(AppText.get(R.string.cal_moved_to, name), AppText.get(R.string.cal_undo)) { t.list?.let { g.calendar.moveTask(t.uid, list = it) } }
    }

    /** Where [t] sits now (its parent and neighbours), for the Undo of a move. */
    private suspend fun placeOf(t: TaskItem): TaskPlace = TaskPlace.of(g.calendar.task(t.uid) ?: t, g.calendar.tasksNow())

    /** Moves [uid] back to [from] through the same POST /move (the Undo of a reorder or reparent). */
    private suspend fun moveBack(uid: String, from: TaskPlace, parentChanged: Boolean) {
        val (after, before, parent) = from.moveBack(parentChanged) ?: return
        g.calendar.moveTask(uid, after = after, before = before, parent = parent)
    }

    /**
     * ACT task.reorder @android.tasks: manual order, between [after] and [before] (UIDs; either may be null).
     * Undo puts it back between its old neighbours.
     */
    fun reorder(t: TaskItem, after: String?, before: String?) = act(AppText.get(R.string.cal_tasks_fail_move)) {
        val from = placeOf(t)
        g.calendar.moveTask(t.uid, before = before, after = after)
        UiMessage(AppText.get(R.string.cal_tasks_moved), AppText.get(R.string.cal_undo)) { moveBack(t.uid, from, parentChanged = false) }
    }

    /** ACT task.reparent @android.tasks: under [parent], or "" for the top level (one level). Undo: the old parent and place. */
    fun reparent(t: TaskItem, parent: String) = act(AppText.get(R.string.cal_tasks_fail_move)) {
        val from = placeOf(t)
        g.calendar.moveTask(t.uid, parent = parent)
        UiMessage(if (parent.isEmpty()) AppText.get(R.string.cal_tasks_moved_top) else AppText.get(R.string.cal_tasks_made_subtask), AppText.get(R.string.cal_undo)) {
            moveBack(t.uid, from, parentChanged = true)
        }
    }

    /** ACT task.delete @android.editor @android.tasks: hidden at once, sent after the 7 s undo window; subtasks go with it. */
    fun delete(t: TaskItem) {
        viewModelScope.launch {
            val kids = g.calendar.subtasks(t.uid).size
            val token = g.calendar.deleteTask(t, cascade = kids > 0)
            _selection.value = _selection.value - t.uid
            _messages.emit(UiMessage(if (kids > 0) AppText.plural(R.plurals.cal_tasks_deleted_with_subtasks, kids, kids) else AppText.get(R.string.cal_qa_task_deleted), AppText.get(R.string.cal_undo)) {
                g.calendar.undoDeleteTask(token)
            })
        }
    }

    // ---------------- bulk (one undo for the batch) ----------------

    private fun selected(): List<TaskItem> {
        val ids = _selection.value
        return tasks.value.orEmpty().filter { it.uid in ids }
    }

    /** [fail]: what the snackbar says when the batch is refused ("Could not complete"). */
    private fun bulkAct(fail: String, block: suspend (List<TaskItem>) -> UiMessage?) {
        val items = selected()
        if (items.isEmpty()) return
        _selection.value = emptySet()
        act(fail) { block(items) }
    }

    /** The outcome line of a batch: "Rescheduled 40 tasks", or "11 of 12 done · 1 changed elsewhere". */
    /** [done]: the line when every task took the change ("Rescheduled 40 tasks"). */
    private fun outcome(done: String, n: Int, failed: List<String>, undo: (suspend () -> Unit)?): UiMessage {
        if (failed.isEmpty()) return UiMessage(done, undo?.let { AppText.get(R.string.cal_undo) }, undo)
        return UiMessage(AppText.get(R.string.cal_tasks_bulk_partial, n - failed.size, n, failed.size), AppText.get(R.string.cal_tasks_review)) { _selection.value = failed.toSet() }
    }

    private suspend fun bulk(uids: List<String>, op: BulkOp, due: String? = null, list: String? = null, tag: String? = null,
                             priority: Int? = null): List<String> =
        g.calendar.bulk(uids, op, due, list, tag, priority)?.failed?.map { it.uid }.orEmpty()

    /** ACT task.bulk_complete @android.tasks */
    fun bulkComplete() = bulkAct(AppText.get(R.string.cal_tasks_fail_complete)) { items ->
        val uids = items.map { it.uid }
        outcome(AppText.plural(R.plurals.cal_tasks_bulk_completed, uids.size, uids.size), uids.size, bulk(uids, BulkOp.COMPLETE)) { bulk(uids, BulkOp.REOPEN) }
    }

    /** ACT task.bulk_reschedule @android.tasks: a new due for every selected task (null: no date). */
    fun bulkReschedule(due: String?) = bulkAct(AppText.get(R.string.cal_tasks_fail_reschedule)) { items ->
        val uids = items.map { it.uid }
        outcome(AppText.plural(R.plurals.cal_tasks_bulk_rescheduled, uids.size, uids.size), uids.size, bulk(uids, BulkOp.RESCHEDULE, due = due)) {
            items.groupBy { it.due }.forEach { (old, xs) -> bulk(xs.map { it.uid }, BulkOp.RESCHEDULE, due = old) }
        }
    }

    /** ACT task.bulk_reschedule: shifted by [days] (1 or 7), times kept; tasks without a due stay as they are. */
    fun bulkShift(days: Long) = bulkAct(AppText.get(R.string.cal_tasks_fail_reschedule)) { items ->
        val dated = items.filter { it.due != null }
        val failed = dated.groupBy { shift(it.due!!, days) }.flatMap { (due, xs) -> bulk(xs.map { it.uid }, BulkOp.RESCHEDULE, due = due) }
        outcome(AppText.plural(R.plurals.cal_tasks_bulk_rescheduled, dated.size, dated.size), dated.size, failed) {
            dated.groupBy { it.due }.forEach { (old, xs) -> bulk(xs.map { it.uid }, BulkOp.RESCHEDULE, due = old) }
        }
    }

    /** ACT task.bulk_move @android.tasks */
    fun bulkMove(url: String) = bulkAct(AppText.get(R.string.cal_tasks_fail_move)) { items ->
        val name = collections.value.firstOrNull { it.url == url }?.name ?: AppText.get(R.string.cal_tasks_list_fallback)
        outcome(AppText.plural(R.plurals.cal_tasks_bulk_moved, items.size, name, items.size), items.size, bulk(items.map { it.uid }, BulkOp.MOVE, list = url)) {
            items.groupBy { it.list }.forEach { (old, xs) -> if (old != null) bulk(xs.map { it.uid }, BulkOp.MOVE, list = old) }
        }
    }

    /** ACT task.bulk_tag @android.tasks: [add] and [remove] are the tags whose tri-state box changed. */
    fun bulkTag(add: Set<String>, remove: Set<String>) = bulkAct(AppText.get(R.string.cal_tasks_fail_tags)) { items ->
        val failed = mutableSetOf<String>()
        add.forEach { tag -> failed += bulk(items.filter { tag !in it.categories }.map { it.uid }, BulkOp.TAG, tag = tag) }
        remove.forEach { tag -> failed += bulk(items.filter { tag in it.categories }.map { it.uid }, BulkOp.UNTAG, tag = tag) }
        outcome(AppText.plural(R.plurals.cal_tasks_bulk_tagged, items.size, items.size), items.size, failed.toList()) {
            add.forEach { tag -> bulk(items.filter { tag !in it.categories }.map { it.uid }, BulkOp.UNTAG, tag = tag) }
            remove.forEach { tag -> bulk(items.filter { tag in it.categories }.map { it.uid }, BulkOp.TAG, tag = tag) }
        }
    }

    /** ACT task.bulk_priority @android.tasks: level 0–3. */
    fun bulkPriority(level: Int) = bulkAct(AppText.get(R.string.cal_tasks_fail_priority)) { items ->
        outcome(AppText.plural(R.plurals.cal_tasks_bulk_changed, items.size, items.size), items.size, bulk(items.map { it.uid }, BulkOp.PRIORITY, priority = CalendarColors.priorityValue(level))) {
            items.groupBy { it.priority }.forEach { (p, xs) -> bulk(xs.map { it.uid }, BulkOp.PRIORITY, priority = p) }
        }
    }

    /** ACT task.bulk_delete @android.tasks: hidden at once and sent after the undo window, like a single delete. */
    fun bulkDelete() = bulkAct(AppText.get(R.string.cal_tasks_fail_delete)) { items ->
        val tokens = items.map { g.calendar.deleteTask(it) }
        UiMessage(AppText.plural(R.plurals.cal_tasks_bulk_deleted, items.size, items.size), AppText.get(R.string.cal_undo)) { tokens.forEach { g.calendar.undoDeleteTask(it) } }
    }

    /** ACT task.clear_completed @android.tasks: no undo (the screen confirms with the count first). */
    fun clearCompleted(listUrl: String?) = act(AppText.get(R.string.cal_tasks_fail_delete)) {
        val done = tasks.value.orEmpty().filter { it.completed && (listUrl == null || it.list == listUrl) }.map { it.uid }
        if (done.isEmpty()) return@act null
        g.calendar.bulk(done, BulkOp.DELETE)
        UiMessage(AppText.plural(R.plurals.cal_tasks_deleted_completed, done.size, done.size))
    }

    // ---------------- lists and tags ----------------

    /** ACT list.create */
    fun createList(name: String, color: String?) = act(AppText.get(R.string.cal_tasks_fail_create_list)) {
        val c = g.calendar.createCollection(name.trim(), color, kind = "tasks")
        UiMessage(AppText.get(R.string.cal_tasks_list_created, c.name))
    }

    /** ACT list.rename @android.tasks, ACT list.color @android.tasks */
    fun updateList(c: CalendarCollection, name: String? = null, color: String? = null) = act(AppText.get(R.string.cal_tasks_fail_change_list)) {
        g.calendar.updateCollection(c, name = name?.trim()?.takeIf { it.isNotEmpty() && it != c.name }, color = color)
        UiMessage(AppText.get(R.string.cal_tasks_list_updated), AppText.get(R.string.cal_undo)) {
            g.calendar.collections.value.firstOrNull { it.url == c.url }?.let { now ->
                g.calendar.updateCollection(now, name = if (name != null) c.name else null, color = if (color != null) c.color else null)
            }
        }
    }

    /** ACT list.set_default @android.tasks: new tasks (quick add without a list) go to [c]. Online only. */
    fun setDefaultList(c: CalendarCollection) = act(AppText.get(R.string.cal_tasks_fail_default_list)) {
        val before = defaults.value.taskList
        g.calendar.setDefaultTaskList(c.url)
        UiMessage(AppText.get(R.string.cal_tasks_new_go_to, c.name), before?.let { AppText.get(R.string.cal_undo) }) { before?.let { g.calendar.setDefaultTaskList(it) } }
    }

    /** ACT tag.color @android.tasks: the tag's colour, kept by mailr (null removes it). Online only. */
    fun setTagColor(tag: String, color: String?) = act(AppText.get(R.string.cal_tasks_fail_colour)) {
        val before = tags.value.firstOrNull { it.name == tag }?.color
        g.calendar.setTagColor(tag, color)
        UiMessage(if (color == null) AppText.get(R.string.cal_tasks_colour_removed, tag) else AppText.get(R.string.cal_colour_named, CalendarColors.nameOf(color) ?: color), AppText.get(R.string.cal_undo)) {
            g.calendar.setTagColor(tag, before)
        }
    }

    /** ACT list.reorder @android.tasks: the lists in this order. */
    fun reorderLists(urls: List<String>) = act(AppText.get(R.string.cal_tasks_fail_reorder)) {
        val before = collections.value.map { it.url }
        g.calendar.reorderCollections(urls)
        UiMessage(AppText.get(R.string.cal_tasks_lists_reordered), AppText.get(R.string.cal_undo)) { g.calendar.reorderCollections(before) }
    }

    /**
     * ACT list.delete @android.tasks: "Move n tasks to …" ([moveTo]) or "Delete them too" (null). Only the
     * move can be undone, so the screen asks for the list's name first.
     */
    fun deleteList(c: CalendarCollection, moveTo: String?, confirm: String, onDone: (Boolean) -> Unit = {}) {
        viewModelScope.launch {
            try {
                if (moveTo != null) {
                    val uids = tasks.value.orEmpty().filter { it.list == c.url && it.parent == null }.map { it.uid }
                    if (uids.isNotEmpty()) g.calendar.bulk(uids, BulkOp.MOVE, list = moveTo)
                }
                g.calendar.deleteCollection(c, confirm)
                if ((_view.value as? TaskView.ListView)?.url == c.url) open(TaskView.TODAY)
                _messages.emit(UiMessage(AppText.get(R.string.cal_tasks_list_deleted, c.name)))
                onDone(true)
            } catch (e: Exception) {
                _messages.emit(UiMessage(e.userMessage()))
                onDone(false)
            }
        }
    }

    /** ACT tag.rename @android.tasks: the tag is rewritten on every task that has it. */
    fun renameTag(old: String, new: String) = act(AppText.get(R.string.cal_tasks_fail_rename_tag)) {
        val name = new.trim().removePrefix("@")
        val uids = tasks.value.orEmpty().filter { old in it.categories }.map { it.uid }
        if (name.isEmpty() || name == old || uids.isEmpty()) return@act null
        g.calendar.bulk(uids, BulkOp.TAG, tag = name)
        g.calendar.bulk(uids, BulkOp.UNTAG, tag = old)
        if ((_view.value as? TaskView.TagView)?.tag == old) open(TaskView.TagView(name))
        UiMessage(AppText.get(R.string.cal_tasks_tag_renamed, name), AppText.get(R.string.cal_undo)) {
            g.calendar.bulk(uids, BulkOp.TAG, tag = old)
            g.calendar.bulk(uids, BulkOp.UNTAG, tag = name)
        }
    }

    /** ACT tag.delete @android.tasks: removed from every task; undo adds it back. */
    fun deleteTag(tag: String) = act(AppText.get(R.string.cal_tasks_fail_delete_tag)) {
        val uids = tasks.value.orEmpty().filter { tag in it.categories }.map { it.uid }
        if (uids.isNotEmpty()) g.calendar.bulk(uids, BulkOp.UNTAG, tag = tag)
        if ((_view.value as? TaskView.TagView)?.tag == tag) open(TaskView.TODAY)
        UiMessage(AppText.plural(R.plurals.cal_tasks_tag_removed, uids.size, tag, uids.size), AppText.get(R.string.cal_undo)) {
            if (uids.isNotEmpty()) g.calendar.bulk(uids, BulkOp.TAG, tag = tag)
        }
    }

    // ---------------- follow-ups (read-only rows) ----------------

    /** mail.follow_up reminders: conversations searched with `is:follow_up` (online; the last answer stays offline). */
    fun loadFollowUps() {
        viewModelScope.launch {
            catching { g.api.conversations(view = "all", q = "is:follow_up", limit = 50) }
                .onSuccess { p -> _followUps.value = p.items.filter { it.followUpAt != null }.sortedBy { it.followUpAt } }
                .onFailure { if (_followUps.value == null) _followUps.value = emptyList() }
        }
    }

    /** ACT mail.follow_up @android.tasks: a new follow-up time (ISO), or null to cancel it. */
    fun changeFollowUp(c: ConversationSummary, until: String?) = act(AppText.get(R.string.cal_tasks_fail_follow_up)) {
        val out = g.mail.act(listOf(c.id), if (until == null) "cancel_follow_up" else "follow_up", until = until)
        _followUps.value = _followUps.value?.let { xs ->
            if (until == null) xs - c else xs.map { if (it.id == c.id) it.copy(followUpAt = until) else it }.sortedBy { it.followUpAt }
        }
        val undo = out.undo
        val back: (suspend () -> Unit)? = undo?.let { u -> suspend { g.mail.undo(u); loadFollowUps() } }
        UiMessage(if (until == null) AppText.get(R.string.cal_tasks_follow_up_cancelled) else AppText.get(R.string.cal_tasks_follow_up_moved), back?.let { AppText.get(R.string.cal_undo) }, back)
    }

    /** ACT mail.to_task @android.tasks: the follow-up becomes a task linked to the conversation, due when the follow-up was. */
    fun followUpToTask(c: ConversationSummary) = act(AppText.get(R.string.cal_tasks_fail_make_task)) {
        val detail = g.api.conversation(c.id)
        val msg = detail.messages.lastOrNull { !it.draft } ?: return@act UiMessage(AppText.get(R.string.cal_tasks_no_message))
        val due = c.followUpAt?.let { ReminderPlan.instant(it, zone) }?.let { OffsetDateTime.ofInstant(Instant.ofEpochMilli(it), zone).toString() }
        val t = g.calendar.taskFromMessage(msg.id, c.id, TaskDraft(g.calendar.newUid(), AppText.get(R.string.cal_tasks_follow_up_title, c.subject.ifBlank { AppText.get(R.string.cal_prep_no_subject) }),
            due = due, list = defaults.value.taskList))
        UiMessage(AppText.get(R.string.cal_tasks_task_added), AppText.get(R.string.cal_undo)) { g.calendar.deleteTask(t) }
    }

    // ---------------- conflicts ----------------

    /** Keep theirs: the server's copy (already shown) stays. */
    fun keepTheirs(c: CalConflict) = act(AppText.get(R.string.cal_tasks_fail_resolve)) { g.calendar.keepTheirs(c.key); null }

    /** Use mine: my change is sent again against the server's version. */
    fun keepMine(c: CalConflict) = act(AppText.get(R.string.cal_tasks_fail_resolve)) { g.calendar.keepMine(c.key); UiMessage(AppText.get(R.string.cal_tasks_change_kept)) }

    /** Keep both: their copy stays and mine becomes a new task. */
    fun keepBoth(c: CalConflict) = act(AppText.get(R.string.cal_tasks_fail_resolve)) {
        val server = c.serverTask
        val title = c.mine?.get("summary")?.let { (it as? kotlinx.serialization.json.JsonPrimitive)?.content } ?: server?.summary.orEmpty()
        val due = (c.mine?.get("due") as? kotlinx.serialization.json.JsonPrimitive)?.content ?: server?.due
        val notes = (c.mine?.get("notes") as? kotlinx.serialization.json.JsonPrimitive)?.content ?: server?.notes.orEmpty()
        g.calendar.createTask(TaskDraft(g.calendar.newUid(), AppText.get(R.string.cal_tasks_copy_title, title), notes, due, server?.priority ?: 0, server?.list))
        g.calendar.keepTheirs(c.key)
        UiMessage(AppText.get(R.string.cal_kept_both))
    }

    // ---------------- helpers ----------------

    fun newUid() = g.calendar.newUid()
    suspend fun task(uid: String) = g.calendar.task(uid)
    fun nowMs() = clock()

    /** Runs a write; its message (with undo) goes to the snackbar; a refusal says why. */
    private fun act(fail: String, block: suspend () -> UiMessage?) {
        viewModelScope.launch {
            try {
                block()?.let { _messages.emit(it) }
            } catch (e: Exception) {
                _messages.emit(UiMessage(e.userMessage().ifBlank { fail }))
            }
        }
    }

    /** [due] moved by [days], keeping its time (and its date-only form). */
    private fun shift(due: String, days: Long): String =
        if (ReminderPlan.isDateOnly(due)) LocalDate.parse(due.take(10)).plusDays(days).toString()
        else catching { OffsetDateTime.parse(due).plusDays(days).toString() }.getOrElse {
            ReminderPlan.instant(due, zone)?.let { ZonedDateTime.ofInstant(Instant.ofEpochMilli(it), zone).plusDays(days).toOffsetDateTime().toString() } ?: due
        }

    /** The due string for [d] at [t] (null: date only). */
    fun dueString(d: LocalDate, t: java.time.LocalTime?): String =
        t?.let { LocalDateTime.of(d, it).atZone(zone).toOffsetDateTime().toString() } ?: d.toString()
}
