// ACT-SURFACES: android.approvals
package io.github.murtazanasir.mailr.ui.approvals

import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHostState
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import io.github.murtazanasir.mailr.data.api.Approval
import io.github.murtazanasir.mailr.data.api.ApprovalPreview
import io.github.murtazanasir.mailr.data.api.MailrJson
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * approval.approve_many: "Allow all n" on the Approvals screen for several requests from one chat or
 * agent. It lists each one in a dialog, and only Allow all there decides them; a single request
 * from another agent gets no line.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class AllowAllTest {
    @get:Rule val compose = createComposeRule()

    private fun label(id: Long, name: String, at: String) =
        Approval(id, "token:7", "claude-code", "labels.create", name, ApprovalPreview("Create label “$name”"), createdAt = at)

    private val pending = listOf(
        label(53, "School", "2026-10-06T10:03:00Z"),
        label(52, "Travel", "2026-10-06T10:02:00Z"),
        Approval(60, "token:9", "hermes", "labels.create", "Other", ApprovalPreview("Create label “Other”")),
        label(51, "Receipts", "2026-10-06T10:01:00Z"),
    )

    @Test fun groupsByRequesterOldestFirst() {
        val groups = allowAllGroups(pending)
        assertEquals(listOf(listOf(51L, 52L, 53L)), groups.map { g -> g.map { it.id } })
        assertEquals("claude-code", groupSource(groups[0][0]))
        assertEquals("this chat", groupSource(Approval(1, "session:x", chatSessionId = "x")))
        assertEquals("chat:x", MailrJson.decodeFromString(Approval.serializer(), """{"id":1,"subject":"session:x","chat_session_id":"x"}""").requesterKey)
        assertEquals("Allowed 3 requests. 1 had already been decided.", allowedText(3, 1))
    }

    @Test fun allowAllListsEachThenDecides() {
        val decided = mutableListOf<List<Long>>()
        compose.setContent {
            MaterialTheme {
                ApprovalsScreen(pending, emptyList(), null, false, SnackbarHostState(), {}, {}, { _, _, _ -> },
                    onDecideMany = { g -> decided += g.map { it.id } })
            }
        }
        compose.onNodeWithTag("allow-all").assertExists()
        compose.onNodeWithText("3 requests from claude-code").assertExists()
        compose.onNodeWithText("Allow all 3").performClick()
        compose.onNodeWithText("Allow each of these once?").assertExists()
        compose.onNodeWithText("1. Create label “Receipts”").assertExists()
        compose.onNodeWithText("3. Create label “School”").assertExists()
        assertEquals(0, decided.size)
        // The dialog's own Allow all decides them.
        compose.onAllNodesWithText("Allow all 3").let { it[it.fetchSemanticsNodes().size - 1] }.performClick()
        assertEquals(listOf(listOf(51L, 52L, 53L)), decided)
    }
}
