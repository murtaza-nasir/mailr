// ACT-SURFACES: android.reader
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHostState
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.assertCountEquals
import io.github.murtazanasir.mailr.data.api.AssistantRun
import io.github.murtazanasir.mailr.data.api.Citation
import io.github.murtazanasir.mailr.data.api.RunApproval
import io.github.murtazanasir.mailr.ui.conversation.ConversationActions
import io.github.murtazanasir.mailr.ui.conversation.ConversationScreen
import io.github.murtazanasir.mailr.ui.conversation.briefLinks
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * ACT brief.read on android.reader: the brief a standing order wrote on the conversation shows
 * above the messages, under the assistant's name and the order's; it collapses; a citation
 * number opens the email it quotes.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class BriefCardTest {
    @get:Rule val compose = createComposeRule()
    private val opened = mutableListOf<Citation>()

    private val cite = Citation(n = 1, conversationId = 3066, messageId = 502, quote = "Data Analyst (remote)")
    private val brief = AssistantRun(id = 9, orderName = "Remote jobs", status = "done", conversationId = 3066,
        output = "**The ones that fit:**\n\n- Northwind Traders — \"Data Analyst (remote)\" [1](mailr-cite:1)",
        citations = listOf(cite), finishedAt = "2026-09-30T06:00:00+00:00")

    private fun reader(b: AssistantRun?) = compose.setContent {
        MaterialTheme {
            ConversationScreen(Fixtures.detail, null, Fixtures.labels, emptySet(), emptyMap(), null, null,
                tabletop = false, snackbar = SnackbarHostState(), actions = ConversationActions(
                    back = null, act = {}, toggle = {}, expandAll = {}, star = { _, _ -> }, reply = { _, _ -> },
                    openAttachment = { _, _ -> }, showImages = {}, snooze = {}, labels = {}, summarize = {}, unsubscribe = {},
                    mailto = {}, retry = {}, openCitation = { opened += it }),
                brief = b, assistantName = "Ambrose")
        }
    }

    @Test fun showsTheBriefWithItsOrderAndCollapses() {
        reader(brief)
        compose.onNodeWithText("Ambrose").assertIsDisplayed()
        compose.onNodeWithText("Remote jobs", substring = true).assertIsDisplayed()
        compose.onNodeWithText("Northwind Traders", substring = true).assertIsDisplayed()
        compose.onNodeWithText("Ambrose").performClick()
        compose.onAllNodesWithText("Northwind Traders", substring = true).assertCountEquals(0)
    }

    @Test fun aCitationOpensTheEmailAndOtherLinksTheBrowser() {
        val urls = mutableListOf<String>()
        val links = briefLinks(brief, { opened += it }, { urls += it })
        links.openUri("mailr-cite:1")
        links.openUri("mailr-cite:7")  // not in the brief's citations: nothing
        links.openUri("https://example.org/job")
        assertEquals(listOf(cite), opened)
        assertEquals(listOf("https://example.org/job"), urls)
    }

    @Test fun noBriefNoCard() {
        reader(null)
        compose.onAllNodesWithText("Ambrose").assertCountEquals(0)
    }

    @Test fun showsTheRunsApprovalsAsTheyStandNow() {
        // The brief says the draft waits; the approval was decided after the run ended.
        val b = brief.copy(output = "Forward prepared, waiting for your approval.", approvals = listOf(
            RunApproval(title = "Save a draft to sam@example.org", status = "executed",
                url = "https://mail.example.com/approvals/aR30r5XQgO", result = "Draft saved"),
            RunApproval(title = "Send to sam@example.org", status = "pending", url = "https://mail.example.com/approvals/aB12")))
        reader(b)
        compose.onNodeWithText("Save a draft to sam@example.org · Approved: Draft saved").assertIsDisplayed()
        compose.onNodeWithText("Send to sam@example.org · Waiting for your approval").assertIsDisplayed()
        assertEquals("aB12", b.approvals[1].key)
        assertEquals(listOf(false, true), b.approvals.map { it.open })
    }

    @Test fun readsApprovalsFromTheServer() {
        val run = io.github.murtazanasir.mailr.data.api.MailrJson.decodeFromString(AssistantRun.serializer(),
            """{"id": 1, "output": "x", "approvals": [{"title": "Save a draft", "status": "pending",
               "url": "https://mail.example.com/approvals/aR30", "result": null}]}""")
        assertEquals("aR30", run.approvals.single().key)
        assertEquals(true, run.approvals.single().open)
    }
}
