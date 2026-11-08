// ACT-SURFACES: android.approvals
package io.github.murtazanasir.mailr.ui.approvals

import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHostState
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import io.github.murtazanasir.mailr.data.api.Approval
import io.github.murtazanasir.mailr.data.api.ApprovalHead
import io.github.murtazanasir.mailr.data.api.ApprovalPreview
import io.github.murtazanasir.mailr.data.api.MailrJson
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * approval.approve_many and approval.deny on the Approvals screen: consecutive requests from one
 * requester of one kind are listed under one header ("claude-code wants to add 2 tasks") with Allow
 * all and Deny all, each deciding exactly the listed requests once. Deny all asks first.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class AllowAllTest {
    @get:Rule val compose = createComposeRule()

    private fun task(id: Long, name: String, at: String, subject: String = "token:7", actor: String = "claude-code") =
        Approval(id, subject, actor, "tasks.create", "Calendar", ApprovalPreview("Add task “$name”"), createdAt = at,
            head = ApprovalHead("task_new", "New task", name = name))

    private fun label(id: Long, name: String, at: String) =
        Approval(id, "token:7", "claude-code", "labels.write", name, ApprovalPreview("Create label “$name”"), createdAt = at,
            head = ApprovalHead("label_new", "New label", name = name))

    // Newest first, as GET /approvals lists them.
    private val pending = listOf(
        task(54, "Update the workshop RSVP form", "2026-10-06T10:04:00Z"),
        task(53, "Book the seminar room", "2026-10-06T10:03:00Z"),
        label(52, "Travel", "2026-10-06T10:02:00Z"),
        task(51, "Print the handouts", "2026-10-06T10:01:00Z"),
    )

    @Test fun onlyConsecutiveRequestsOfOneRequesterAndKindGroup() {
        val runs = approvalRuns(pending)
        assertEquals(listOf(listOf(54L, 53L), listOf(52L), listOf(51L)), runs.map { r -> r.map { it.id } })
        assertEquals(listOf(listOf(54L, 53L)), approvalGroups(pending).map { g -> g.map { it.id } })
        // Oldest first when decided: the order they were asked in.
        assertEquals(listOf(53L, 54L), oldestFirst(runs[0]).map { it.id })
        // Another token breaks the run.
        val mixed = listOf(pending[0], task(60, "Other", "2026-10-06T10:03:30Z", subject = "token:9", actor = "hermes"), pending[1])
        assertTrue(approvalGroups(mixed).isEmpty())
        // A review card, an outbox row and a decided card never join a group.
        val review = pending[1].copy(preview = ApprovalPreview("Review", kind = "review"))
        assertTrue(approvalGroups(listOf(pending[0], review)).isEmpty())
        assertTrue(approvalGroups(listOf(pending[0], pending[1].copy(outboxId = 9))).isEmpty())
        assertTrue(approvalGroups(listOf(pending[0], pending[1].copy(status = "approved"))).isEmpty())
        // An older server sends no kind: the action groups.
        val old = pending.take(2).map { it.copy(head = null) }
        assertEquals(1, approvalGroups(old).size)
    }

    @Test fun theHeaderNamesTheRequesterAndCountsTheKind() {
        assertEquals("claude-code wants to add 2 tasks", groupHeader(pending.take(2)))
        assertEquals("this chat", groupSource(Approval(1, "session:x", chatSessionId = "x")))
        assertEquals("chat:x", MailrJson.decodeFromString(Approval.serializer(), """{"id":1,"subject":"session:x","chat_session_id":"x"}""").requesterKey)
        assertEquals("Allowed 3 requests. 1 had already been decided.", allowedText(3, 1))
        assertEquals("Denied 2 requests.", allowedText(2, 0, approve = false))
    }

    @Test fun allowAllDecidesTheListedCardsAtOnce() {
        val decided = mutableListOf<Pair<List<Long>, Boolean>>()
        compose.setContent {
            MaterialTheme {
                ApprovalsScreen(pending, emptyList(), null, false, SnackbarHostState(), {}, {}, { _, _, _ -> },
                    onDecideMany = { g, ok -> decided += g.map { it.id } to ok })
            }
        }
        compose.onNodeWithText("claude-code wants to add 2 tasks").assertExists()
        assertEquals(1, compose.onAllNodesWithTag("approval-group").fetchSemanticsNodes().size)
        compose.onNodeWithTag("allow-all").performClick()
        assertEquals(listOf(listOf(53L, 54L) to true), decided)
    }

    @Test fun denyAllAsksFirst() {
        val decided = mutableListOf<Pair<List<Long>, Boolean>>()
        compose.setContent {
            MaterialTheme {
                ApprovalsScreen(pending, emptyList(), null, false, SnackbarHostState(), {}, {}, { _, _, _ -> },
                    onDecideMany = { g, ok -> decided += g.map { it.id } to ok })
            }
        }
        compose.onNodeWithTag("deny-all").performClick()
        compose.onNodeWithText("Deny all 2 requests?").assertExists()
        compose.onNodeWithTag("deny-all-list").assertExists()
        assertEquals(0, decided.size)
        compose.onNodeWithTag("deny-all-confirm").performClick()
        assertEquals(listOf(listOf(53L, 54L) to false), decided)
    }

    @Test fun aLongGroupStartsFoldedAndOpens() {
        val many = (1..4).map { task(70L - it, "Task $it", "2026-10-06T10:0${9 - it}:00Z") }
        compose.setContent {
            MaterialTheme { ApprovalsScreen(many, emptyList(), null, false, SnackbarHostState(), {}, {}, { _, _, _ -> }, onDecideMany = { _, _ -> }) }
        }
        compose.onNodeWithTag("approval-group-folded").assertExists()
        assertTrue(compose.onAllNodesWithTag("approval-heading").fetchSemanticsNodes().isEmpty())
        compose.onNodeWithTag("approval-group-toggle").performClick()
        compose.waitForIdle()
        compose.onNodeWithTag("approval-group-folded").assertDoesNotExist()
        assertTrue(compose.onAllNodesWithTag("approval-heading").fetchSemanticsNodes().isNotEmpty())
    }
}
