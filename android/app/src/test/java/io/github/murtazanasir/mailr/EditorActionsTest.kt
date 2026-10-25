// ACT-SURFACES: android.editor
package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.time.AppClock
import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.hasText
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextReplacement
import androidx.compose.ui.test.performScrollTo
import io.github.murtazanasir.mailr.data.reminders.QuickChoices
import io.github.murtazanasir.mailr.ui.calendar.CalPane
import io.github.murtazanasir.mailr.ui.calendar.CalendarViewModel
import io.github.murtazanasir.mailr.ui.calendar.EditorFor
import io.github.murtazanasir.mailr.ui.calendar.TaskEditorFor
import io.github.murtazanasir.mailr.ui.calendar.TaskPane
import io.github.murtazanasir.mailr.ui.calendar.TasksViewModel
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
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.util.Locale

/**
 * The task and event editors (surface android.editor), rendered as
 * the Tasks and Calendar screens host them, with Save going through their view models to a fake
 * server.
 *
 * Covers android.editor: task.reschedule, task.edit, task.delete, task.open_email,
 * event.reschedule, event.edit, event.delete, event.rsvp, event.open_email.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class EditorActionsTest {
    @get:Rule val compose = createComposeRule()
    private val h = CalendarHarness()
    private var opened: Long? = null
    private var closed = false

    @Before fun setUp() { h.start() }
    @After fun tearDown() = h.stop()

    private val zone = ZoneId.systemDefault()
    // Due in 20 days at 10:05: no preset chip matches, so the editor shows the date itself.
    private val due = h.now.plusDays(20).withHour(10).withMinute(5)
    private val task = h.task.copy(due = due.toOffsetDateTime().toString())

    private fun taskEditor() {
        val vm = h.vm { TasksViewModel(h.g) }
        compose.setContent {
            MaterialTheme {
                TaskEditorFor(vm, TaskPane.Edit(task.uid), listOf(task), emptyList(), null, null, 9, saving = false, onSaving = {},
                    onClose = { closed = true }, onOpen = {}, openEmail = { opened = it })
            }
        }
        compose.waitForIdle()
    }

    private fun eventEditor(event: io.github.murtazanasir.mailr.data.api.CalendarEvent = h.event) {
        val vm = h.vm { CalendarViewModel(h.g) }
        compose.setContent {
            MaterialTheme {
                EditorFor(vm, CalPane.Edit(event, AppClock.localNow()), emptyList(), null, null, 9, saving = false,
                    onClose = { closed = true }, onDone = { closed = true },
                    onSave = { orig, d, sc, rr, lv -> vm.save(orig, d, sc, rr, live = lv) }, onDelete = { e, sc -> vm.delete(e, sc) },
                    onRsvp = vm::rsvp, openEmail = { opened = it })
            }
        }
        compose.waitForIdle()
    }

    private fun chip(label: String) {
        compose.onNode(hasText(label) and SemanticsMatcher.expectValue(SemanticsProperties.Role, Role.Checkbox)).performClick()
        compose.waitForIdle()
    }

    private fun tap(label: String) {
        compose.onNodeWithText(label).performScrollTo().performClick()
        compose.waitForIdle()
    }

    private fun save() { compose.onNodeWithText("Save").performClick(); compose.waitForIdle() }

    /** task.reschedule: the due opens the when picker with the reschedule presets, which keep the time. */
    @Test fun taskReschedule() {
        taskEditor()
        chip(due.format(DateTimeFormatter.ofPattern("MMM d", Locale.getDefault())))
        compose.onNodeWithText("Reschedule").assertExists()
        // The last preset ("In a week", or "Next Monday" on a Monday, when the two are the same day).
        val pick = QuickChoices.taskReschedule(AppClock.now(zone), due, false, 9).last()
        chip(pick.label)
        save()
        val sent = OffsetDateTime.parse(h.sent("PATCH", "/api/v1/tasks/t1")["due"]!!.jsonPrimitive.content).atZoneSameInstant(zone)
        assertEquals(pick.at.toInstant(), sent.toInstant())
        assertEquals(due.toLocalTime(), sent.toLocalTime())
    }

    /** task.edit: the fields save together through Save. */
    @Test fun taskEdit() {
        taskEditor()
        compose.onNode(hasSetTextAction() and hasText(task.summary)).performTextReplacement("Send the Q4 numbers")
        save()
        assertEquals("Send the Q4 numbers", h.sent("PATCH", "/api/v1/tasks/t1")["summary"]!!.jsonPrimitive.content)
        h.until("the editor closes") { closed }
    }

    /** task.delete: Delete task removes it after the undo window. */
    @Test fun taskDelete() {
        taskEditor()
        tap("Delete task")
        h.until("delete scheduled") { h.delayed.any { it.method == "DELETE" && it.path == "tasks/t1" } }
        assertTrue(closed)
    }

    /** task.open_email: the editor links to the conversation the task came from. */
    @Test fun taskOpenEmail() {
        taskEditor()
        tap("Open the email")
        assertEquals(3066L, opened)
    }

    /** event.reschedule: the start opens the when picker with the reschedule presets; the end keeps the duration. */
    @Test fun eventReschedule() {
        eventEditor()
        tap(h.start.format(DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).withLocale(Locale.getDefault())))
        compose.onNodeWithText("Move to").assertExists()
        chip("+1 hour")
        save()
        val body = h.sent("PATCH", "/api/v1/calendar/events/e1")
        assertEquals(h.start.plusHours(1).toInstant(), OffsetDateTime.parse(body["start"]!!.jsonPrimitive.content).toInstant())
        assertEquals(h.start.plusHours(2).toInstant(), OffsetDateTime.parse(body["end"]!!.jsonPrimitive.content).toInstant())
    }

    /** event.edit: a changed title is sent with Save (a single event: scope all). */
    @Test fun eventEdit() {
        eventEditor()
        compose.onNode(hasSetTextAction() and hasText(h.event.summary)).performTextReplacement("Budget review (moved)")
        save()
        val body = h.sent("PATCH", "/api/v1/calendar/events/e1")
        assertEquals("Budget review (moved)", body["summary"]!!.jsonPrimitive.content)
        assertEquals("all", body["scope"]!!.jsonPrimitive.content)
    }

    /** event.delete: Delete event, after the undo window. */
    @Test fun eventDelete() {
        eventEditor()
        tap("Delete event")
        h.until("delete scheduled") { h.delayed.any { it.method == "DELETE" && it.path == "calendar/events/e1" } }
    }

    /** event.rsvp: the answer is part of the form; Save sends only the answer when nothing else changed. */
    @Test fun eventRsvp() {
        eventEditor()
        compose.onNodeWithText("Going?").assertExists()
        tap("Yes")
        save()
        assertEquals("accepted", h.sent("POST", "/api/v1/calendar/events/e1/rsvp")["response"]!!.jsonPrimitive.content)
        assertTrue(closed)
        assertTrue(h.requests.none { it.first.method == "PATCH" })
    }

    @Test fun ownEventsHaveNoAnswer() {
        eventEditor(h.event.copy(myStatus = null))
        compose.onAllNodesWithText("Going?").assertCountEquals(0)
    }

    /** event.open_email: the editor links to the conversation the event came from. */
    @Test fun eventOpenEmail() {
        eventEditor()
        tap("Open the email")
        assertEquals(3075L, opened)
    }

    @Test fun eventsNotFromAnEmailHaveNoLink() {
        eventEditor(h.event.copy(conversationId = null))
        compose.onAllNodesWithText("Open the email").assertCountEquals(0)
    }
}
