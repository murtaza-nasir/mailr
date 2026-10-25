// ACT-SURFACES: android.sheet
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.test.assertCountEquals
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import io.github.murtazanasir.mailr.data.reminders.RState
import io.github.murtazanasir.mailr.ui.calendar.Done
import io.github.murtazanasir.mailr.ui.calendar.QuickActionCard
import io.github.murtazanasir.mailr.ui.calendar.QuickActions
import io.github.murtazanasir.mailr.ui.calendar.SheetTarget
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonPrimitive
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The quick-action sheet (surface android.sheet): each button, tapped
 * in the card, runs its action against a fake server.
 *
 * Covers android.sheet: task.complete, task.edit, task.delete, task.open_email, event.edit,
 * event.delete, event.rsvp, event.open_email, reminder.dismiss.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class QuickSheetTest {
    @get:Rule val compose = createComposeRule()
    private val h = CalendarHarness()
    private val runs = mutableListOf<suspend () -> Done>()

    @Before fun setUp() { h.start() }
    @After fun tearDown() = h.stop()

    private fun card(target: SheetTarget) {
        val actions = QuickActions(h.g, h.app)
        compose.setContent { MaterialTheme { QuickActionCard(target, actions, 9, null, run = { runs += it }) } }
    }

    private fun tap(label: String): Done {
        compose.onNodeWithText(label, substring = true).performClick()
        compose.waitForIdle()
        return runBlocking { runs.removeAt(runs.lastIndex).invoke() }
    }

    private val taskSheet get() = SheetTarget(h.taskReminder, task = h.task)
    private val eventSheet get() = SheetTarget(h.eventReminder, event = h.event)

    /** task.complete: Done completes the task (queued, like a notification action) and hands its reminders; Undo reopens. */
    @Test fun taskComplete() {
        card(taskSheet)
        val done = tap("Done")
        assertEquals(true, h.queuedBody("PATCH", "tasks/t1")["completed"]!!.jsonPrimitive.boolean)
        assertNotEquals(RState.SHOWN, h.reminder(h.taskReminder.key)!!.state)
        runBlocking { done.undo!!.invoke() }
        assertEquals(false, h.sent("PATCH", "/api/v1/tasks/t1")["completed"]!!.jsonPrimitive.boolean)
    }

    /** task.edit: Edit opens the full editor in the app. */
    @Test fun taskEdit() {
        card(taskSheet)
        compose.onNodeWithText("Edit", substring = true).performClick()
        assertEquals("mailr://task/t1", h.nextOpened()!!.dataString)
    }

    /** task.delete: Delete hides the task, waits out the undo window, then DELETEs; Undo cancels it. */
    @Test fun taskDelete() {
        card(taskSheet)
        val done = tap("Delete")
        assertEquals(listOf("DELETE tasks/t1"), h.delayed.map { "${it.method} ${it.path}" })
        assertEquals(RState.DISMISSED, h.reminder(h.taskReminder.key)!!.state)
        runBlocking { done.undo!!.invoke() }
        assertTrue(h.delayed.isEmpty())
    }

    /** task.open_email: offered only when the task came from an email; opens that conversation. */
    @Test fun taskOpenEmail() {
        card(taskSheet)
        compose.onNodeWithText("Email", substring = true).performClick()
        assertEquals("mailr://conversation/3066", h.nextOpened()!!.dataString)
    }

    @Test fun taskWithoutEmailHasNoEmailButton() {
        card(SheetTarget(h.taskReminder, task = h.task.copy(conversationId = null)))
        compose.onAllNodesWithText("Email", substring = true).assertCountEquals(0)
    }

    /** event.edit: Edit opens the event editor on this occurrence. */
    @Test fun eventEdit() {
        card(eventSheet)
        compose.onNodeWithText("Edit", substring = true).performClick()
        val uri = h.nextOpened()!!.data!!
        assertEquals("mailr://event/e1", "${uri.scheme}://${uri.host}${uri.path}")
        assertEquals(h.event.occurrence, uri.getQueryParameter("occ"))
    }

    /** event.delete: Delete removes the occurrence after the undo window and ends its reminder. */
    @Test fun eventDelete() {
        card(eventSheet)
        tap("Delete")
        assertEquals(listOf("DELETE calendar/events/e1"), h.delayed.map { "${it.method} ${it.path}" })
        assertEquals(RState.DISMISSED, h.reminder(h.eventReminder.key)!!.state)
    }

    /** event.rsvp: an invitation offers Yes, Maybe and No; each answers the organizer. */
    @Test fun eventRsvp() {
        card(eventSheet)
        tap("Maybe")
        assertEquals("tentative", h.sent("POST", "/api/v1/calendar/events/e1/rsvp")["response"]!!.jsonPrimitive.content)
    }

    /** event.open_email: the conversation the event was made from. */
    @Test fun eventOpenEmail() {
        card(eventSheet)
        compose.onNodeWithText("Email", substring = true).performClick()
        assertEquals("mailr://conversation/3075", h.nextOpened()!!.dataString)
    }

    /** reminder.dismiss: Dismiss hands the reminder on every device; the record is unchanged; Undo brings it back. */
    @Test fun reminderDismiss() {
        card(eventSheet)
        val done = tap("Dismiss")
        assertEquals(RState.DISMISSED, h.reminder(h.eventReminder.key)!!.state)
        val handle = h.queuedBody("POST", "reminders/handle")
        assertEquals(listOf(h.eventReminder.key), handle["keys"]!!.jsonArray.map { it.jsonPrimitive.content })
        assertTrue(h.sentNothing())
        runBlocking { done.undo!!.invoke() }
        assertEquals(RState.SHOWN, h.reminder(h.eventReminder.key)!!.state)
    }

    /** Opening the sheet changes nothing by itself. */
    @Test fun openingChangesNothing() {
        card(eventSheet)
        compose.waitForIdle()
        assertEquals(RState.SHOWN, h.reminder(h.eventReminder.key)!!.state)
        assertTrue(h.queued().isEmpty())
        assertNull(h.nextOpened())
    }
}
