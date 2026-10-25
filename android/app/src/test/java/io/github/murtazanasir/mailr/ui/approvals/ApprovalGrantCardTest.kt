// ACT-SURFACES: android.approvals
package io.github.murtazanasir.mailr.ui.approvals

import android.app.Application
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import io.github.murtazanasir.mailr.data.api.AlsoCovers
import io.github.murtazanasir.mailr.data.api.Approval
import io.github.murtazanasir.mailr.data.api.ApprovalPreview
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.ui.more.ApprovalCard
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The grants on an approval card (approval.approve, approval.undo_grant): each says what it covers,
 * and how many other waiting requests it approves too, before the tap.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class ApprovalGrantCardTest {
    @get:Rule val compose = createComposeRule()

    private val labelCreate = Approval(41, "token:12", "claude-code", "labels.write", "Receipts",
        ApprovalPreview("Create label “Receipts”", listOf("Receipts")), alsoCovers = AlsoCovers(session = 2, always = 2, alwaysDerived = 2))

    private fun shows(s: String) = compose.onAllNodesWithText(s, substring = true, useUnmergedTree = true)
        .fetchSemanticsNodes().isNotEmpty()

    @Test fun theMenuSaysWhatEachGrantCoversAndHowManyOthers() {
        val decided = mutableListOf<String>()
        compose.setContent { MaterialTheme { ApprovalCard(labelCreate) { _, ok, scope -> decided += "$ok/$scope" } } }
        compose.onNodeWithTag("approval-more").performClick()
        compose.waitForIdle()
        assertTrue(shows("Allow for 12 hours (also approves 2 other waiting requests)"))
        assertTrue(shows("This agent may create or change labels for 12 hours, with any input, without asking."))
        assertTrue(shows("Always allow (also approves 2 other waiting requests)"))
        assertTrue(shows("A standing rule for this agent and action."))
        compose.onNodeWithTag("approval-grant-session", useUnmergedTree = true).performClick()
        assertEquals(listOf("true/session"), decided)
    }

    @Test fun noCountWhenNothingElseWaits() {
        compose.setContent { MaterialTheme { ApprovalCard(labelCreate.copy(subject = "assistant", alsoCovers = AlsoCovers())) { _, _, _ -> } } }
        compose.onNodeWithTag("approval-more").performClick()
        compose.waitForIdle()
        assertTrue(shows("Allow for this chat"))
        assertTrue(!shows("also approves"))
    }

    @Test fun theCardReadsTheServersCounts() {
        val a = MailrJson.decodeFromString(Approval.serializer(),
            """{"id":5,"subject":"token:3","action":"labels.write","also_covers":{"session":1,"always":3,"always_derived":2}}""")
        assertEquals(AlsoCovers(1, 3, 2), a.alsoCovers)
    }

    @Test fun aCardHeldByAHardRuleShowsOnlyAllowOnceAndDeny() {
        val held = MailrJson.decodeFromString(Approval.serializer(),
            """{"id":7,"subject":"session:s1","actor":"Ambrose","action":"notes.create","grants":false,
               "preview":{"title":"Create a note","lines":["Text from the email"]}}""")
        assertEquals(false, held.grants)
        val decided = mutableListOf<String>()
        compose.setContent { MaterialTheme { ApprovalCard(held) { _, ok, scope -> decided += "$ok/$scope" } } }
        compose.waitForIdle()
        assertTrue(shows("Allow once"))
        assertTrue(shows("Deny"))
        assertTrue(compose.onAllNodesWithTag("approval-more").fetchSemanticsNodes().isEmpty())
        assertTrue(!shows("Allow for this chat") && !shows("Always allow"))
        compose.onNodeWithText("Allow once").performClick()
        assertEquals(listOf("true/once"), decided)
    }

    @Test fun anOlderServerWithoutTheFieldKeepsTheGrants() {
        val a = MailrJson.decodeFromString(Approval.serializer(), """{"id":5,"subject":"token:3","action":"labels.write"}""")
        assertTrue(a.grants)
    }

    @Test fun theSnackbarWords() {
        assertEquals("Allowed for 12 hours. Also approved 2 other requests.", GrantText.granted("session", "Allow for 12 hours", 2))
        assertEquals("Rule created. Also approved 1 other request.", GrantText.granted("always", "Allow for 12 hours", 1))
        assertEquals("Undone. 2 requests are waiting again.", GrantText.undone(2, 0))
        assertEquals("Undone for 1 request. 1 request had already run.", GrantText.undone(1, 1))
        assertEquals("Rule removed. 2 requests had already run.", GrantText.undone(0, 2))
    }
}
