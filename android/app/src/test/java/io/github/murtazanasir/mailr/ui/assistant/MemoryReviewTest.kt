// ACT-SURFACES: android.assistant
package io.github.murtazanasir.mailr.ui.assistant

import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import io.github.murtazanasir.mailr.TestGraph
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.testing.until
import io.github.murtazanasir.mailr.ui.memory.FakeMemoryStore
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Seeing suggestions on the Ambrose screen (docs/design/memory-quality.md, step 3): "Remember …?"
 * under a reply (memory.chat_ask), the weekly review card with Keep (memory.accept_suggestion), Dismiss
 * (memory.dismiss_suggestion), Create task (memory.make_task), merge (memory.merge) and Not now
 * (memory.dismiss_review_card), and the "n to review" count (memory.review_count).
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class MemoryReviewTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()

    @Test fun rememberAskAnswersAndThenSaysWhatBecameOfIt() {
        val answers = mutableListOf<Pair<Long, Boolean>>()
        compose.setContent {
            MaterialTheme {
                RememberAsk(listOf(RememberItem(5, "You are vegetarian", "waiting"), RememberItem(6, "You like jazz", "dropped"))) { id, yes ->
                    answers += id to yes
                }
            }
        }
        compose.onNodeWithText("Remember: “You are vegetarian”?").assertExists()
        compose.onNodeWithText("Not remembered: “You like jazz”").assertExists()
        compose.onNodeWithText("Yes").performClick()
        assertEquals(listOf(5L to true), answers)
    }

    @Test fun theCardMergesIntoASavedNoteAndNotNowHidesIt() {
        val store = FakeMemoryStore()
        var waiting = -1
        var opened = 0
        compose.setContent { MaterialTheme { MemoryReviewCard(store, 0, { opened++ }) { waiting = it } } }
        compose.until("the card") { compose.onAllNodesWithTag("review-card").fetchSemanticsNodes().isNotEmpty() }
        assertEquals(2, waiting)
        compose.onNodeWithText("2 memories to review").assertExists()
        // One closed line per topic; a topic's notes show once it is opened.
        compose.onAllNodesWithTag("review-topic").assertCountEquals(2)
        compose.onAllNodesWithTag("review-group").assertCountEquals(0)
        compose.onNodeWithText("Review on the Memory page").performClick()
        assertEquals(1, opened)
        compose.onNodeWithText("You").performClick()
        compose.onAllNodesWithTag("review-group").assertCountEquals(1)
        // "You are vegetarian" (about you) into a saved note about you.
        compose.onNodeWithTag("review-merge-22").performClick()
        compose.onNodeWithText("You prefer short replies").performClick()
        compose.until("\"merge:22:11\" in store.calls") { "merge:22:11" in store.calls }
        compose.until("1 left") { compose.onAllNodesWithText("1 memory to review").fetchSemanticsNodes().isNotEmpty() }
        assertTrue(store.active.first { it.id == 11L }.mergedFrom.single().text == "You are vegetarian")
        compose.onNodeWithTag("review-card-dismiss").performClick()
        compose.until("\"dismiss-card\" in store.calls") { "dismiss-card" in store.calls }
        compose.until("the card is gone") { compose.onAllNodesWithTag("review-card").fetchSemanticsNodes().isEmpty() }
    }

    @Test fun theCardKeepsDropsAndMakesATaskOfACommitment() {
        val store = FakeMemoryStore().apply {
            suggested += io.github.murtazanasir.mailr.data.api.Memory(23, "You will send Dana the invoice", "commitment",
                status = "suggested", source = "extracted",
                refs = listOf(io.github.murtazanasir.mailr.data.api.MemoryRef(about = io.github.murtazanasir.mailr.data.api.RefAbout(
                    "Dana Whitfield", listOf("dana@lumencoffee.example"), "person"), due = "2026-12-16")))
        }
        compose.setContent { MaterialTheme { MemoryReviewCard(store, 0) {} } }
        compose.until("the card") { compose.onAllNodesWithTag("review-card").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("Dana Whitfield").performClick()
        compose.onNodeWithText("Create task").performClick()
        compose.until("task and drop") { "task:23" in store.calls && "dismiss:23" in store.calls }
        assertEquals("2026-12-16", store.tasks.single().due)
        compose.until("2 left") { compose.onAllNodesWithText("2 memories to review").fetchSemanticsNodes().isNotEmpty() }
        // Dana's other suggestion is still under the opened topic.
        compose.onAllNodesWithText("Keep")[0].performClick()
        compose.until("a keep") { store.calls.any { it.startsWith("accept:") } }
        compose.until("1 left") { compose.onAllNodesWithText("1 memory to review").fetchSemanticsNodes().isNotEmpty() }
        compose.onAllNodesWithTag("review-topic")[0].performClick()
        compose.onNodeWithText("Dismiss").performClick()
        compose.until("the card is gone") { compose.onAllNodesWithTag("review-card").fetchSemanticsNodes().isEmpty() }
    }

    @Test fun theCountOpensMemory() {
        var opened = 0
        compose.setContent { MaterialTheme { ReviewCountChip(3) { opened++ } } }
        compose.onNodeWithText("3 to review").performClick()
        assertEquals(1, opened)
    }

    @Test fun aReplysRememberItemsAreReadAndMatchedToItsMessage() {
        val o = JsonArray(listOf(JsonObject(mapOf("id" to JsonPrimitive(9), "text" to JsonPrimitive("You are vegetarian"),
            "state" to JsonPrimitive("kept")))))
        assertEquals(listOf(RememberItem(9, "You are vegetarian", "kept")), SessionReplay.remember(o))
        val live = listOf(ChatTurn("user", "I'm vegetarian", messageId = 40), ChatTurn("assistant"),
            ChatTurn("user", "thanks", messageId = 41), ChatTurn("assistant"))
        val fresh = listOf(ChatTurn("user", messageId = 40), ChatTurn("assistant", remember = listOf(RememberItem(9, "You are vegetarian", "waiting"))))
        val out = AssistantViewModel.withRemember(live, AssistantViewModel.rememberByMessage(fresh))
        assertEquals(listOf(9L), out[1].remember.map { it.id })
        assertTrue(out[3].remember.isEmpty())
    }
}
