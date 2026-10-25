// ACT-SURFACES: android.triage
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.hasText
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performClick
import io.github.murtazanasir.mailr.data.reminders.RState
import io.github.murtazanasir.mailr.ui.calendar.QuickActions
import io.github.murtazanasir.mailr.ui.calendar.ReminderQueue
import io.github.murtazanasir.mailr.ui.calendar.SheetTarget
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
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
import java.time.OffsetDateTime
import java.time.ZoneId

/**
 * The triage queue (surface android.triage): a pager of sheet
 * cards; an action removes its card and moves on, and what was not acted on stays as it was.
 *
 * Covers android.triage: task.complete, task.snooze, task.reschedule, task.open_email,
 * event.snooze, event.remind_before, event.reschedule, event.rsvp, event.open_email,
 * reminder.dismiss.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class TriageQueueTest {
    @get:Rule val compose = createComposeRule()
    private val h = CalendarHarness()
    private var finished = false

    @Before fun setUp() { h.start() }
    @After fun tearDown() = h.stop()

    private fun queue(vararg targets: SheetTarget) {
        val actions = QuickActions(h.g, h.app)
        compose.setContent {
            MaterialTheme { ReminderQueue(load = { targets.toList() }, actions = actions, morningHour = 9, expand = null, onFinish = { finished = true }) }
        }
        compose.waitForIdle()
    }

    private val taskCard get() = SheetTarget(h.taskReminder, task = h.task)
    private val eventCard get() = SheetTarget(h.eventReminder, event = h.event)
    private fun tap(label: String) { compose.onNodeWithText(label, substring = true).performClick(); compose.waitForIdle() }
    private fun tapExact(label: String) { compose.onNodeWithText(label).performClick(); compose.waitForIdle() }
    /** A preset tile of the when sheet, by its id (the wheels below it can show the same words). */
    private fun tile(id: String) {
        compose.onNodeWithTag("tile-$id").performScrollTo().performClick()
        compose.waitForIdle()
    }
    private fun shown(key: String) = h.reminder(key)!!.state == RState.SHOWN

    /** task.complete: Done completes the task and moves to the next card; the other reminder is untouched. */
    @Test fun taskComplete() {
        queue(taskCard, eventCard)
        compose.onNodeWithText("1 of 2").assertExists()
        tap("Done")
        assertEquals(true, h.queuedBody("PATCH", "tasks/t1")["completed"]!!.jsonPrimitive.boolean)
        h.until("the card leaves the queue") { runCatching { compose.onNodeWithText("Budget review").assertExists() }.isSuccess }
        assertTrue(shown(h.eventReminder.key))
    }

    /** task.snooze: Snooze, then a preset: the reminder comes back later on every device. */
    @Test fun taskSnooze() {
        queue(taskCard, eventCard)
        tap("Snooze")
        tile("60m")
        assertEquals(h.taskReminder.key, h.queuedBody("POST", "reminders/snooze")["key"]!!.jsonPrimitive.content)
        val r = h.reminder(h.taskReminder.key)!!
        assertEquals(RState.PENDING, r.state)
        assertTrue(r.snoozed)
        assertTrue(shown(h.eventReminder.key))
    }

    /** task.reschedule: Reschedule, then Same time tomorrow: the due moves (keeping its time) and this reminder ends. */
    @Test fun taskReschedule() {
        queue(taskCard)
        tap("Reschedule")
        tile("same-tomorrow")
        val due = OffsetDateTime.parse(h.sent("PATCH", "/api/v1/tasks/t1")["due"]!!.jsonPrimitive.content).atZoneSameInstant(ZoneId.systemDefault())
        assertEquals(h.now.toLocalDate().plusDays(1), due.toLocalDate())
        assertEquals(h.now.plusHours(2).toLocalTime(), due.toLocalTime())
        h.until("the reminder ends") { h.reminder(h.taskReminder.key)!!.state == RState.DISMISSED }
    }

    /** task.open_email: Email opens the conversation and leaves the card (and its reminder) as they were. */
    @Test fun taskOpenEmail() {
        queue(taskCard, eventCard)
        tap("Email")
        assertEquals("mailr://conversation/3066", h.nextOpened()!!.dataString)
        compose.onNodeWithText("1 of 2").assertExists()
        assertTrue(shown(h.taskReminder.key))
    }

    /** event.snooze: before the start the presets are 5 min, 10 min and At start time. */
    @Test fun eventSnooze() {
        queue(eventCard, taskCard)
        tap("Snooze")
        compose.onNodeWithText("At start time").assertExists()
        tile("5m")
        assertEquals(h.eventReminder.key, h.queuedBody("POST", "reminders/snooze")["key"]!!.jsonPrimitive.content)
        assertEquals(RState.PENDING, h.reminder(h.eventReminder.key)!!.state)
        assertTrue(shown(h.taskReminder.key))
    }

    /** event.remind_before: 40 min ahead, only the offsets still in the future; one tap adds the one-off reminder. */
    @Test fun eventRemindBefore() {
        queue(eventCard)
        tap("Remind before")
        compose.onNodeWithText("1 hour before").assertDoesNotExist()
        tile("o10")
        val add = h.queuedBody("POST", "reminders/add")
        assertEquals(10, add["minutes"]!!.jsonPrimitive.int)
        assertEquals(listOf(h.eventReminder.key), add["handle"]!!.jsonArray.map { it.jsonPrimitive.content })
        assertEquals(RState.DISMISSED, h.reminder(h.eventReminder.key)!!.state)
    }

    /** event.reschedule: Reschedule, then 1 hour later: this occurrence moves and keeps its length. */
    @Test fun eventReschedule() {
        queue(eventCard)
        tap("Reschedule")
        tile("p60")
        val body = h.sent("PATCH", "/api/v1/calendar/events/e1")
        assertEquals(h.start.plusHours(1).toInstant(), OffsetDateTime.parse(body["start"]!!.jsonPrimitive.content).toInstant())
        assertEquals(h.start.plusHours(2).toInstant(), OffsetDateTime.parse(body["end"]!!.jsonPrimitive.content).toInstant())
    }

    /** event.rsvp: Yes answers the invitation and moves on. */
    @Test fun eventRsvp() {
        queue(eventCard, taskCard)
        tapExact("Yes")
        assertEquals("accepted", h.sent("POST", "/api/v1/calendar/events/e1/rsvp")["response"]!!.jsonPrimitive.content)
        h.until("the next card") { runCatching { compose.onNodeWithText(h.task.summary).assertExists() }.isSuccess }
    }

    /** event.open_email: Email opens the conversation the invitation came in. */
    @Test fun eventOpenEmail() {
        queue(eventCard)
        tap("Email")
        assertEquals("mailr://conversation/3075", h.nextOpened()!!.dataString)
        assertTrue(shown(h.eventReminder.key))
    }

    /** reminder.dismiss: the last action ends the queue with "All caught up". */
    @Test fun reminderDismiss() {
        queue(eventCard)
        tap("Dismiss")
        assertEquals(listOf(h.eventReminder.key), h.queuedBody("POST", "reminders/handle")["keys"]!!.jsonArray.map { it.jsonPrimitive.content })
        assertEquals(RState.DISMISSED, h.reminder(h.eventReminder.key)!!.state)
        compose.onNodeWithText("All caught up").assertExists()
    }

    /** Leaving without acting keeps every reminder exactly as it was. */
    @Test fun leavingChangesNothing() {
        queue(taskCard, eventCard)
        compose.onNodeWithContentDescription("Next").performClick()
        compose.waitForIdle()
        compose.onNodeWithText("2 of 2").assertExists()
        assertTrue(shown(h.taskReminder.key) && shown(h.eventReminder.key))
        assertTrue(h.queued().isEmpty() && h.sentNothing())
    }
}
