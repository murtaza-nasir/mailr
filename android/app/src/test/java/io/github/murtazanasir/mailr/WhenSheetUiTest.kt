package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.test.assertIsSelected
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assert
import androidx.compose.ui.test.assertTextContains
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.performScrollToIndex
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.hasAnyAncestor
import androidx.compose.ui.test.hasScrollToIndexAction
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import io.github.murtazanasir.mailr.ui.calendar.Done
import io.github.murtazanasir.mailr.ui.calendar.QuickActionCard
import io.github.murtazanasir.mailr.ui.calendar.QuickActions
import io.github.murtazanasir.mailr.ui.calendar.SheetTarget
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.jsonPrimitive
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneId

/**
 * The when sheet in the card: tiles in
 * groups, Same time from the original due, From now | From due time with its defaults, and the
 * verbs kept apart (a snooze moves only the reminder, a reschedule moves the due).
 *
 * Covers android.sheet: task.snooze, task.reschedule, event.snooze, event.remind_before.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class WhenSheetUiTest {
    @get:Rule val compose = createComposeRule()
    private val h = CalendarHarness()
    private val runs = mutableListOf<suspend () -> Done>()

    @Before fun setUp() {
        h.start()
        h.app.getSharedPreferences("when_sheet", 0).edit().clear().commit()
    }
    @After fun tearDown() = h.stop()

    private fun card(target: SheetTarget, expand: String) {
        val actions = QuickActions(h.g, h.app)
        compose.setContent { MaterialTheme { QuickActionCard(target, actions, 9, expand, run = { runs += it }) } }
        compose.waitForIdle()
    }

    private fun last(): Done = runBlocking { runs.removeAt(runs.lastIndex).invoke() }
    private fun tag(t: String) = compose.onNodeWithTag(t).performScrollTo()
    private val z = ZoneId.systemDefault()
    private val due get() = h.now.plusHours(2)

    /** task.snooze: Same time tomorrow is the due's time of day; the due itself does not move. */
    @Test fun snoozeSameTimeTomorrow() {
        card(SheetTarget(h.taskReminder, task = h.task), "snooze")
        compose.onNodeWithText("Same time").assertExists()
        tag("tile-same-tomorrow").performClick()
        compose.waitForIdle()
        last()
        val at = Instant.ofEpochMilli(h.reminder(h.taskReminder.key)!!.fireAt).atZone(z)
        assertEquals(h.now.toLocalDate().plusDays(1), at.toLocalDate())
        assertEquals(due.toLocalTime().withSecond(0).withNano(0), at.toLocalTime())
        assertEquals(true, h.queued().none { it.path.startsWith("tasks/") })
    }

    /** task.snooze: From now by default; From due time counts the wheels from the due and is remembered. */
    @Test fun snoozeFromDueTime() {
        card(SheetTarget(h.taskReminder, task = h.task), "snooze")
        compose.onNodeWithText("From now").assertIsSelected()
        compose.onNodeWithText("From due time").performClick()
        compose.onNodeWithTag("when-confirm").performClick()
        compose.waitForIdle()
        last()
        val at = h.reminder(h.taskReminder.key)!!.fireAt
        assertEquals(due.plusMinutes(15).toInstant().toEpochMilli() / 60_000, at / 60_000)
        assertEquals("DUE", h.app.getSharedPreferences("when_sheet", 0).getString("base.SNOOZE", null))
    }

    /** task.reschedule: From due time by default; the confirm moves the due by one day. */
    @Test fun rescheduleFromDue() {
        card(SheetTarget(h.taskReminder, task = h.task), "reschedule")
        compose.onNodeWithText("From due time").assertIsSelected()
        compose.onNodeWithTag("when-confirm").performClick()
        compose.waitForIdle()
        last()
        val sent = OffsetDateTime.parse(h.sent("PATCH", "/api/v1/tasks/t1")["due"]!!.jsonPrimitive.content).toInstant()
        assertEquals(due.plusDays(1).toInstant().epochSecond / 60, sent.epochSecond / 60)
    }

    /** event.snooze: only Later, counted from now; no toggle. */
    @Test fun eventSnoozeHasNoToggle() {
        card(SheetTarget(h.eventReminder, event = h.event), "snooze")
        compose.onNodeWithText("From now").assertDoesNotExist()
        compose.onNodeWithText("Same time").assertDoesNotExist()
        tag("tile-start").assertExists()
    }

    /** event.remind_before: the offsets ahead, then a custom value counted back from the start. */
    @Test fun remindBeforeCustom() {
        card(SheetTarget(h.eventReminder, event = h.event), "offset")
        compose.onNodeWithText("Before it starts").assertExists()
        compose.onNodeWithText("Reminds you", substring = true).assertExists()
    }

    /** Pick date & time… opens the full picker. */
    @Test fun pickDateAndTime() {
        card(SheetTarget(h.taskReminder, task = h.task), "snooze")
        compose.onNodeWithText("Pick date & time…").performClick()
        compose.onNodeWithText("Snooze until").assertExists()
    }

    private fun hasStateDescription(v: String) = SemanticsMatcher.expectValue(SemanticsProperties.StateDescription, v)
    private fun wheel(tag: String) = compose.onNode(hasScrollToIndexAction() and hasAnyAncestor(hasTestTag(tag)))

    /**
     * task.snooze, the amount and unit wheels: hours on the unit wheel, then 2 and 3 on the amount wheel. The
     * bug this guards: scrolling the amount down past 12 hours switched the unit to minutes,
     * because the amount wheel called back with the unit it had when it was first drawn.
     */
    @Test fun customReachesTwoAndThreeHours() {
        card(SheetTarget(h.taskReminder, task = h.task), "snooze")
        compose.onNodeWithTag("wheel-unit").performScrollTo()
        wheel("wheel-unit").performScrollToIndex(1) // hours; the amount stays 15
        compose.waitForIdle()
        compose.onNodeWithContentDescription("Result").assertTextContains("in 15 h", substring = true)
        for (n in listOf(3, 2, 3)) {
            wheel("wheel-amount").performScrollToIndex(n - 1)
            compose.waitForIdle()
            compose.onNodeWithContentDescription("Result").assertTextContains("in $n h", substring = true)
            compose.onNodeWithTag("wheel-unit").assert(hasStateDescription("hours"))
        }
        compose.onNodeWithTag("when-confirm").performClick()
        compose.waitForIdle()
        last()
        val at = h.reminder(h.taskReminder.key)!!.fireAt
        assertEquals(h.now.plusHours(3).toInstant().toEpochMilli() / 60_000, at / 60_000)
    }
}
