// ACT-SURFACES: android.approvals android.assistant
package io.github.murtazanasir.mailr.ui.approvals

import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.getUnclippedBoundsInRoot
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.Approval
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.ui.more.ApprovalCard
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The approval card (approval.approve, approval.deny, agent.ask_about): a head line with the kind of
 * request and who asked, the object's own title as the heading (two lines, More for the rest), the
 * facts on one line, one line of why it asks with ⓘ for the whole reason, and one row of answers with
 * the grants behind "+1 ▾" and Ask behind ⋮. On a 360 dp phone.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w360dp-h780dp")
class ApprovalCardTest {
    @get:Rule val compose = createComposeRule()

    private fun shows(s: String) = compose.onAllNodesWithText(s, substring = true, useUnmergedTree = true).fetchSemanticsNodes().isNotEmpty()

    private fun card(a: Approval, decided: MutableList<String> = mutableListOf(), asked: MutableList<Long> = mutableListOf()) =
        compose.setContent { MaterialTheme { ApprovalCard(a, onAsk = { asked += it.id }) { _, ok, scope -> decided += "$ok/$scope" } } }

    @Test fun theHeadLineAndTheHeading() {
        card(CardSamples.tasks[0])
        compose.onNodeWithTag("approval-kind", useUnmergedTree = true).assertExists()
        assertTrue(shows("New task"))
        assertTrue(shows("claude-code · "))
        // The task's own title: no quotes, no question mark.
        compose.onNodeWithText("Update the workshop RSVP form", useUnmergedTree = true).assertExists()
        assertFalse(shows("“Update"))
        assertFalse(shows("RSVP form?"))
        // The facts on one line, labels kept; the task's title is not repeated there.
        assertTrue(shows("Due Thu 5 Nov, 09:00 · List Calendar"))
        // A short heading has no More.
        compose.onNodeWithTag("approval-title-more").assertDoesNotExist()
    }

    @Test fun aLongTitleTakesTwoLinesAndMoreShowsAll() {
        card(CardSamples.tasks[1])
        compose.onNodeWithTag("approval-title-more", useUnmergedTree = true).assertExists()
        val clamped = compose.onNodeWithTag("approval-heading", useUnmergedTree = true).getUnclippedBoundsInRoot().let { it.bottom - it.top }
        compose.onNodeWithTag("approval-title-more", useUnmergedTree = true).performClick()
        compose.waitForIdle()
        val open = compose.onNodeWithTag("approval-heading", useUnmergedTree = true).getUnclippedBoundsInRoot().let { it.bottom - it.top }
        assertTrue("More shows the whole title ($clamped → $open)", open > clamped)
        assertTrue(shows("Show less"))
    }

    @Test fun theReasonIsOneLineWithTheWholeTextBehindInfo() {
        card(CardSamples.tasks[0])
        assertTrue(shows("Asks first: written through an API token"))
        assertFalse(shows(CardSamples.TAINTED))
        compose.onNodeWithContentDescription("Why it asks").performClick()
        compose.waitForIdle()
        compose.onNodeWithTag("approval-why-text", useUnmergedTree = true).assertExists()
        assertTrue(shows(CardSamples.TAINTED))
        compose.onNodeWithContentDescription("Hide why it asks").performClick()
        compose.waitForIdle()
        assertFalse(shows(CardSamples.TAINTED))
    }

    @Test fun oneRowOfAnswersWithTheGrantsAndAskInMenus() {
        val decided = mutableListOf<String>()
        val asked = mutableListOf<Long>()
        card(CardSamples.tasks[0], decided, asked)
        // Allow once, Deny, +1 ▾ and ⋮ sit on one row.
        val tops = listOf("approval-allow", "approval-deny", "approval-grants", "approval-more")
            .map { compose.onNodeWithTag(it).assertIsDisplayed().getUnclippedBoundsInRoot() }
        val row = compose.onNodeWithTag("approval-actions").getUnclippedBoundsInRoot()
        tops.forEach { assertTrue(it.top >= row.top && it.bottom <= row.bottom) }
        assertTrue(shows("+1"))
        compose.onNodeWithContentDescription("More ways to allow, also approving 1 other waiting request").assertExists()
        compose.onNodeWithTag("approval-grants").performClick()
        compose.waitForIdle()
        assertTrue(shows("Allow for 12 hours (also approves 1 other waiting request)"))
        assertTrue(shows("Always allow (also approves 1 other waiting request)"))
        compose.onNodeWithTag("approval-grant-session", useUnmergedTree = true).performClick()
        assertEquals(listOf("true/session"), decided)
        compose.onNodeWithContentDescription("More actions").performClick()
        compose.waitForIdle()
        compose.onNodeWithTag("approval-ask", useUnmergedTree = true).performClick()
        assertEquals(listOf(81L), asked)
        compose.onNodeWithTag("approval-deny").performClick()
        compose.onNodeWithTag("approval-allow").performClick()
        assertEquals(listOf("true/session", "false/once", "true/once"), decided)
    }

    @Test fun anEmailShowsItsSubjectAsTheHeadingAndEveryRecipient() {
        card(CardSamples.email)
        assertTrue(shows("Send email"))
        compose.onNodeWithText("Workshop room on Thursday", useUnmergedTree = true).assertExists()
        assertTrue(shows("To Alice Chen <alice@example.com>"))
        assertTrue(shows("The seminar room is booked"))
        // No reason, no line; nothing else waits, so the grants button carries no number.
        compose.onNodeWithTag("approval-reason").assertDoesNotExist()
        assertTrue(compose.onAllNodesWithText("+0", substring = true).fetchSemanticsNodes().isEmpty())
    }

    @Test fun theHeadFromTheServer() {
        val a = MailrJson.decodeFromString(Approval.serializer(), """{"id":5,"subject":"token:3","action":"tasks.create",
            "head":{"kind":"task_new","kind_label":"New task","kind_label_msg":{"key":"approvals.kind.task_new","params":{}},
            "name":"Dry run","reason_short":"Asks first: written through an API token",
            "reason_short_msg":{"key":"approvals.reason_short.token_text","params":{}}}}""")
        assertEquals("task_new", a.kind)
        assertEquals("New task", a.typeLabel)
        assertEquals("Dry run", a.heading)
        assertEquals("Asks first: written through an API token", a.shortReason)
        // An older server: the action, the card's title, no label.
        val old = MailrJson.decodeFromString(Approval.serializer(), """{"id":6,"subject":"token:3","action":"labels.write","preview":{"title":"Create label “Travel”"}}""")
        assertEquals("labels.write", old.kind)
        assertEquals(null, old.typeLabel)
        assertEquals("Create label “Travel”", old.heading)
        assertEquals(null, old.shortReason)
    }
}
