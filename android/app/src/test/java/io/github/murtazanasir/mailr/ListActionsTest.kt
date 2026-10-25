// ACT-SURFACES: android.calendar android.tasks
package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.time.AppClock
import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.hasText
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onFirst
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextReplacement
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.swipeLeft
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.ui.calendar.AgendaView
import io.github.murtazanasir.mailr.ui.calendar.CalPane
import io.github.murtazanasir.mailr.ui.calendar.CalendarViewModel
import io.github.murtazanasir.mailr.ui.calendar.EditorFor
import io.github.murtazanasir.mailr.ui.calendar.EventDetail
import io.github.murtazanasir.mailr.ui.calendar.TasksViewModel
import io.github.murtazanasir.mailr.ui.tasks.RowStyle
import io.github.murtazanasir.mailr.ui.tasks.SwipeAction
import io.github.murtazanasir.mailr.ui.tasks.TaskGroupList
import io.github.murtazanasir.mailr.ui.tasks.TaskGroups
import io.github.murtazanasir.mailr.ui.tasks.TaskListActions
import io.github.murtazanasir.mailr.ui.tasks.TaskView
import io.github.murtazanasir.mailr.ui.calendar.dayEvents
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.jsonPrimitive
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId

/**
 * Rows in the Tasks and Calendar screens (surface android.list): the check, the swipes and a tap,
 * on the screens' own list composables (the screens themselves need the app's graph), against a
 * fake server.
 *
 * Covers android.list: task.complete, task.reopen, task.edit, task.delete, event.edit
 * (mail.primary_action on android.list is in PrimaryActionTest).
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class ListActionsTest {
    @get:Rule val compose = createComposeRule()
    private val h = CalendarHarness()
    private val opened = mutableListOf<TaskItem>()

    @Before fun setUp() { h.start() }
    @After fun tearDown() = h.stop()

    private fun tasks(vararg items: TaskItem, showCompleted: Boolean = false) {
        val vm = h.vm { TasksViewModel(h.g) }
        val now = AppClock.millis()
        val view = if (showCompleted) TaskView.SmartView(io.github.murtazanasir.mailr.ui.tasks.Smart.COMPLETED) else TaskView.SmartView(io.github.murtazanasir.mailr.ui.tasks.Smart.ALL)
        val groups = TaskGroups.build(view, items.toList(), now, ZoneId.systemDefault())
        // The screen's wiring (TasksScreen.kt): the check completes or reopens, a left swipe here deletes (a setting).
        val actions = TaskListActions(open = { opened += it }, toggle = { if (it.completed) vm.reopen(it) else vm.complete(it) }, select = {},
            swipe = { t, a -> if (a == SwipeAction.DELETE) vm.delete(t) }, reschedule = {}, reorder = { _, _, _ -> }, reparent = { _, _ -> },
            openEmail = {}, openTag = {}, addToDay = {})
        compose.setContent {
            MaterialTheme { TaskGroupList(groups, now, emptyList(), emptySet(), SwipeAction.COMPLETE to SwipeAction.DELETE, RowStyle(), actions) }
        }
        compose.waitForIdle()
    }

    private val check = SemanticsMatcher.expectValue(SemanticsProperties.Role, Role.Checkbox)

    /** task.complete: the row's check completes the task (after its animation). */
    @Test fun taskComplete() {
        tasks(h.task)
        compose.onNode(check).performClick()
        compose.mainClock.advanceTimeBy(1_000)
        assertEquals(true, h.sent("PATCH", "/api/v1/tasks/t1")["completed"]!!.jsonPrimitive.boolean)
    }

    /** task.reopen: the check of a completed task (Show completed) reopens it. */
    @Test fun taskReopen() {
        tasks(h.task.copy(status = "completed"), showCompleted = true)
        compose.onNode(check).performClick()
        assertEquals(false, h.sent("PATCH", "/api/v1/tasks/t1")["completed"]!!.jsonPrimitive.boolean)
    }

    /** task.delete: a deliberate swipe to the left deletes (when the left swipe is set to Delete), after the undo window. */
    @Test fun taskDelete() {
        tasks(h.task)
        compose.onNodeWithText(h.task.summary).performTouchInput { swipeLeft(startX = width * 0.95f, endX = width * 0.1f, durationMillis = 400) }
        compose.waitForIdle()
        h.until("delete scheduled") { h.delayed.any { it.method == "DELETE" && it.path == "tasks/t1" } }
    }

    /** task.edit: a tap on the row opens that task (the sheet, then its editor: QuickSheetTest, EditorActionsTest). */
    @Test fun taskEdit() {
        tasks(h.task)
        compose.onNodeWithText(h.task.summary).performClick()
        assertEquals(listOf("t1"), opened.map { it.uid })
        assertTrue(h.sentNothing())
    }

    /** event.edit: a tap on the event opens it; Edit opens the editor; Save sends the change. */
    @Test fun eventEdit() {
        val vm = h.vm { CalendarViewModel(h.g) }
        var detail by mutableStateOf<CalendarEvent?>(null)
        var editing by mutableStateOf<CalendarEvent?>(null)
        compose.setContent {
            MaterialTheme {
                // What CalendarRoute shows in turn: the schedule, the event, the editor.
                val d = detail
                val e = editing
                when {
                    e != null -> EditorFor(vm, CalPane.Edit(e, AppClock.localNow()), emptyList(), null, null, 9, saving = false,
                        onClose = {}, onSave = { o, draft, sc, rr, lv -> vm.save(o, draft, sc, rr, live = lv) }, onDelete = { _, _ -> })
                    d != null -> EventDetail(d, null, emptyList(), 9, null, onEdit = { editing = d }, onDelete = {}, onRsvp = {})
                    else -> AgendaView(dayEvents(listOf(h.event), emptyList()), AppClock.today(), AppClock.millis(), null,
                        onOpen = { detail = it }, onCreateOn = {})
                }
            }
        }
        // An evening event can span midnight and show on both days; either row opens it.
        compose.onAllNodesWithText(h.event.summary, substring = true).onFirst().performClick()
        compose.onNodeWithContentDescription("Edit event").performClick()
        compose.onNode(hasSetTextAction() and hasText(h.event.summary)).performTextReplacement("Budget review with Dana")
        compose.onNodeWithText("Save").performClick()
        assertEquals("Budget review with Dana", h.sent("PATCH", "/api/v1/calendar/events/e1")["summary"]!!.jsonPrimitive.content)
    }
}
