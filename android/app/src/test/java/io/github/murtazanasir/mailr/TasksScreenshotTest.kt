package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.time.AppClock
import android.app.Application
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.Sort
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.Menu
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material.icons.outlined.SelectAll
import androidx.compose.material.icons.outlined.TaskAlt
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onRoot
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import io.github.murtazanasir.mailr.data.api.ConversationSummary
import io.github.murtazanasir.mailr.data.api.Participant
import io.github.murtazanasir.mailr.data.api.TagInfo
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.cal.CalConflict
import io.github.murtazanasir.mailr.data.cal.ListCount
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.ui.calendar.TaskEditor
import io.github.murtazanasir.mailr.ui.calendar.TaskEditorState
import io.github.murtazanasir.mailr.ui.components.EmptyState
import io.github.murtazanasir.mailr.ui.tasks.ConflictSheet
import io.github.murtazanasir.mailr.ui.tasks.GroupOptions
import io.github.murtazanasir.mailr.ui.tasks.ListManager
import io.github.murtazanasir.mailr.ui.tasks.ListManagerActions
import io.github.murtazanasir.mailr.ui.tasks.QuickPrefill
import io.github.murtazanasir.mailr.ui.tasks.RescheduleSheet
import io.github.murtazanasir.mailr.ui.tasks.RowStyle
import io.github.murtazanasir.mailr.ui.tasks.SelectionBar
import io.github.murtazanasir.mailr.ui.tasks.Smart
import io.github.murtazanasir.mailr.ui.tasks.SwipeAction
import io.github.murtazanasir.mailr.ui.tasks.TaskGroupList
import io.github.murtazanasir.mailr.ui.tasks.TaskGroups
import io.github.murtazanasir.mailr.ui.tasks.TaskListActions
import io.github.murtazanasir.mailr.ui.tasks.TaskQuickAddBar
import io.github.murtazanasir.mailr.ui.tasks.TaskView
import io.github.murtazanasir.mailr.ui.tasks.TasksDrawer
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.time.ZoneId
import java.time.ZonedDateTime

/** The Tasks screens on a phone, light and dark. Output: app/screenshots/tasks-<theme>-<screen>.png */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
@OptIn(ExperimentalMaterial3Api::class)
class TasksScreenshotTest(private val dark: Boolean) {
    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun params(): List<Array<Any>> = listOf(arrayOf<Any>(false), arrayOf<Any>(true))
    }

    @get:Rule val compose = createComposeRule()

    @Before fun setUp() {
        RuntimeEnvironment.setQualifiers("w411dp-h891dp-port-${if (dark) "night" else "notnight"}-xxhdpi")
    }

    private val zone = ZoneId.systemDefault()
    private val now: ZonedDateTime = AppClock.now(zone).withHour(10).withMinute(20).withSecond(0).withNano(0)
    private val nowMs = now.toInstant().toEpochMilli()
    private val today = now.toLocalDate()
    private fun at(days: Long, h: Int, m: Int = 0) = now.plusDays(days).withHour(h).withMinute(m).toOffsetDateTime().toString()

    private val lists = listOf(
        CalendarCollection("L1", "Work", "#039be5", tasks = true, calendarId = 1),
        CalendarCollection("L2", "Home", "#33b679", tasks = true, calendarId = 2),
        CalendarCollection("L3", "Shared groceries", "#f6bf26", tasks = true, calendarId = 3, readOnly = true),
    )
    private val tasks = listOf(
        TaskItem("a", "Submit the SPADS reviews", today.minusDays(2).toString(), list = "L1", priority = 1, reminders = listOf(-540)),
        TaskItem("b", "Send the Q3 numbers to Dana", at(0, 15), list = "L1", priority = 5, conversationId = 3066, reminders = listOf(15),
            categories = listOf("email")),
        TaskItem("c", "Water the plants", today.toString(), list = "L2", recurring = true, rrule = "FREQ=WEEKLY;BYDAY=TH"),
        TaskItem("d", "Draft the CHEM 101 week 6 slides", null, start = at(-1, 9), list = "L1", notes = "Start from the M5 outline",
            categories = listOf("teaching")),
        TaskItem("e", "Book the Denver hotel", today.plusDays(1).toString(), list = "L2", subtaskCount = 3),
        TaskItem("e1", "Compare the two downtown options", null, list = "L2", parent = "e", status = "completed"),
        TaskItem("e2", "Check the conference rate", null, list = "L2", parent = "e"),
        TaskItem("e3", "Ask about late checkout", null, list = "L2", parent = "e"),
        TaskItem("f", "Renew the passport", today.plusDays(9).toString(), list = "L2", priority = 9),
        TaskItem("g", "Read the release notes", null, list = "L1", categories = listOf("reading")),
        TaskItem("h", "Grade project drafts", today.plusDays(2).toString(), list = "L1", start = today.plusDays(1).toString()),
    )
    private val follow = ConversationSummary(3071, "Room booking for the AI Club meeting", followUpAt = at(0, 9),
        participants = listOf(Participant("events@example.edu", "Events Office")))
    private val noActions = TaskListActions({}, {}, {}, { _, _ -> }, {}, { _, _, _ -> }, { _, _ -> }, {}, {}, {})

    private fun shot(name: String, content: @Composable () -> Unit) {
        compose.setContent {
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize()) { content() }
            }
        }
        compose.onRoot().captureRoboImage("screenshots/tasks-${if (dark) "dark" else "light"}-$name.png")
    }

    @Composable
    private fun Bar(title: String, count: Int?) {
        TopAppBar(navigationIcon = { IconButton(onClick = {}) { Icon(Icons.Outlined.Menu, "Lists and tags") } },
            title = {
                Column {
                    Text(title)
                    count?.let { Text("$it tasks", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant) }
                }
            },
            actions = {
                IconButton(onClick = {}) { Icon(Icons.Outlined.Search, "Search") }
                IconButton(onClick = {}) { Icon(Icons.AutoMirrored.Outlined.Sort, "Sort") }
                IconButton(onClick = {}) { Icon(Icons.Outlined.MoreVert, "More") }
            })
    }

    @Composable
    private fun Screen(view: TaskView, title: String, style: RowStyle = RowStyle(), text: String = "", selection: Set<String> = emptySet(),
                       followUps: List<ConversationSummary> = emptyList(), subtaskOf: String? = null) {
        val groups = TaskGroups.build(view, tasks, nowMs, zone, GroupOptions(lists = lists))
        Column {
            if (selection.isEmpty()) Bar(title, groups.sumOf { it.count })
            else TopAppBar(navigationIcon = { IconButton(onClick = {}) { Icon(Icons.Outlined.Close, "Clear") } },
                title = { Text("${selection.size} selected") }, actions = { IconButton(onClick = {}) { Icon(Icons.Outlined.SelectAll, "All") } })
            TaskGroupList(groups, nowMs, lists, selection, SwipeAction.COMPLETE to SwipeAction.RESCHEDULE, style, noActions,
                followUps = followUps, followUpTitle = if (followUps.isEmpty()) null else "Follow-ups", modifier = Modifier.weight(1f))
            // Quick add at the bottom (note #17), as TasksScreen draws it; the selection bar takes its place.
            if (selection.isEmpty()) TaskQuickAddBar(lists, QuickPrefill.of(view, today), { now }, remember { FocusRequester() },
                { _, _ -> }, { _, _ -> }, {}, initialText = text, subtaskOf = subtaskOf, subtask = subtaskOf != null, chipsAbove = true)
            if (selection.isNotEmpty()) SelectionBar(selection.size, {}, {}, {}, {}, {}, {})
        }
    }

    @Test fun today() = shot("today") { Screen(TaskView.TODAY, "Today", followUps = listOf(follow)) }

    @Test fun upcoming() = shot("upcoming") { Screen(TaskView.SmartView(Smart.UPCOMING), "Upcoming") }

    @Test fun list() = shot("list") {
        Screen(TaskView.ListView("L2"), "Home", RowStyle(showList = false, manual = true, expandSubtasks = true))
    }

    @Test fun quickAdd() = shot("quickadd") { Screen(TaskView.TODAY, "Today", text = "Call Dana fri 3pm !! #Work @phone every week") }

    /** Quick add in subtask mode (the toggle chip after a create). */
    @Test fun quickAddSubtask() = shot("quickadd-subtask") { Screen(TaskView.TODAY, "Today", text = "Book flights", subtaskOf = "Plan the trip") }

    @Test fun selection() = shot("selection") { Screen(TaskView.SmartView(Smart.ALL), "All", selection = setOf("b", "c", "g")) }

    @Test fun drawer() = shot("drawer") {
        TasksDrawer(TaskView.TODAY, TaskGroups.counts(tasks, nowMs, zone) + ("waiting" to 1),
            lists.map { ListCount(it, tasks.count { t -> t.list == it.url && !t.completed }) },
            listOf(TagInfo("email", 1), TagInfo("teaching", 1), TagInfo("reading", 1)), 1, {}, {}, {}, {})
    }

    @Test fun listManager() = shot("list-manager") {
        ListManager(lists.map { ListCount(it, tasks.count { t -> t.list == it.url && !t.completed }) },
            listOf(TagInfo("email", 1, "#039be5"), TagInfo("teaching", 1)), ListManagerActions({ _, _ -> }, { _, _, _ -> }, {}, { _, _ -> }, { _, _ -> }, {}, {}),
            defaultList = lists.firstOrNull()?.url)
    }

    @Test fun reschedule() = shot("reschedule-sheet") {
        RescheduleSheet(tasks[1], 9, {}, {}, {}, onSkip = null)
    }

    @Test fun editor() = shot("editor") {
        val s = remember {
            TaskEditorState(tasks[2].copy(categories = listOf("home"), start = today.toString(), repeatFrom = "completion",
                reminders = listOf(-540), conversationId = 3066), "L2", null, "c")
        }
        TaskEditor(s, lists, listOf(tasks[5], tasks[6]), 9, false, {}, {}, {}, {}, {}, {}, onOpenEmail = {}, knownTags = listOf("home"),
            onReplyEmail = {}, onDuplicate = {}, onSkip = {})
    }

    /** task.undo_edit, task.redo_edit: the full editor's header with Undo (a step to take back) and Redo (nothing to put back yet). */
    @Test fun editorUndo() = shot("editor-undo") {
        val s = remember {
            TaskEditorState(tasks[1], "L1", null, "b").also { it.edit { priority = 3 } }
        }
        TaskEditor(s, lists, emptyList(), 9, false, {}, {}, {}, {}, {}, {}, onOpenEmail = {}, undoRedo = true)
    }

    @Test fun conflict() = shot("conflict") {
        ConflictSheet(CalConflict("task|b", "task", "b", "changed", "PATCH", JsonObject(mapOf("summary" to JsonPrimitive("Send the Q3 numbers to Dana and Lee"),
            "due" to JsonPrimitive(at(1, 12)))), null, tasks[1].copy(summary = "Send the Q4 numbers to Dana"), 0), {}, {}, {})
    }

    @Test fun emptyToday() = shot("empty-today") {
        Column {
            Bar("Today", 0)
            Box(Modifier.weight(1f)) { EmptyState(Icons.Outlined.TaskAlt, "Nothing due today", null, action = "3 tasks tomorrow →" to {}) }
            TaskQuickAddBar(lists, QuickPrefill.of(TaskView.TODAY, today), { now }, remember { FocusRequester() }, { _, _ -> }, { _, _ -> }, {},
                chipsAbove = true)
        }
    }
}
