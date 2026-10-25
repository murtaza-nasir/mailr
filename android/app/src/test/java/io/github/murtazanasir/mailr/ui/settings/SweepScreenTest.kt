// ACT-SURFACES: android.settings
package io.github.murtazanasir.mailr.ui.settings

import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollToNode
import androidx.compose.ui.test.performTextClearance
import androidx.compose.ui.test.performTextInput
import io.github.murtazanasir.mailr.TestGraph
import io.github.murtazanasir.mailr.data.api.ReviewChoice
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.testing.until
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.util.concurrent.CopyOnWriteArrayList

/** The rules and choices in memory (example domains only); records every call. */
class FakeSweepStore : SweepStore {
    val calls = CopyOnWriteArrayList<String>()
    var saved = "Trash old offers."
    var items = listOf(
        ReviewChoice(4, "news@paper.example", "archive", count = 2, updatedAt = "2026-10-20T10:00:00Z"),
        ReviewChoice(5, "deals@shop.example", "label:12", label = "Shops"),
    )

    override suspend fun rules(): String { calls += "rules"; return saved }
    override suspend fun saveRules(text: String) { calls += "save:$text"; saved = text.trim() }
    override suspend fun choices(): List<ReviewChoice> { calls += "choices"; return items }
    override suspend fun remove(id: Long) { calls += "remove:$id"; items = items.filter { it.id != id } }
    override suspend fun clear() { calls += "clear"; items = emptyList() }
}

/**
 * Settings → Ambrose → Sweep on a phone (Robolectric): settings.sweep_rules (the saved rules, Save),
 * the meaning of each move, the remembered choices with agent.review_choice_remove, and
 * agent.review_choice_clear after the dialog.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class SweepScreenTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()

    private fun show(store: FakeSweepStore = FakeSweepStore()): FakeSweepStore {
        compose.setContent { MaterialTheme { SweepPage(store, "Ambrose", onBack = {}) } }
        compose.until("\"choices\" in store.calls") { "choices" in store.calls }
        compose.waitForIdle()
        return store
    }

    private fun scrollTo(tag: String) = compose.onNodeWithTag("sweep-list").performScrollToNode(hasTestTag(tag))

    @Test fun savesTheRules() {
        val store = show()
        compose.onNodeWithText("Trash old offers.").assertExists()
        compose.onNodeWithTag("sweep-rules").performTextClearance()
        compose.onNodeWithTag("sweep-rules").performTextInput("Archive only receipts.")
        compose.onNodeWithTag("sweep-save").performClick()
        compose.until("save") { store.calls.any { it.startsWith("save:") } }
        assertEquals("Archive only receipts.", store.saved)
        scrollTo("sweep-definitions")
        compose.onNodeWithText("Marked read and left where it is. Only for unread mail.").assertExists()
    }

    @Test fun listsAndRemovesAPastChoice() {
        val store = show()
        scrollTo("sweep-choice")
        compose.onAllNodesWithTag("sweep-choice").assertCountEquals(2)
        compose.onNodeWithText("news@paper.example").assertExists()
        compose.onNodeWithText("Label “Shops”").assertExists()
        compose.onAllNodesWithTag("sweep-remove")[0].performClick()
        compose.until("\"remove:4\" in store.calls") { "remove:4" in store.calls }
        compose.until("reloaded") { store.calls.count { it == "choices" } >= 2 }
        compose.waitForIdle()
        compose.onAllNodesWithTag("sweep-choice").assertCountEquals(1)
    }

    @Test fun clearsThePastChoicesAfterTheDialog() {
        val store = show()
        scrollTo("sweep-clear")
        compose.onNodeWithTag("sweep-clear").performClick()
        compose.onNodeWithText("Clear all past choices?").assertExists()
        compose.onNodeWithTag("sweep-clear-confirm").performClick()
        compose.until("\"clear\" in store.calls") { "clear" in store.calls }
        compose.until("reloaded") { store.calls.count { it == "choices" } >= 2 }
        compose.waitForIdle()
        scrollTo("sweep-none")
        compose.onNodeWithText("None yet.").assertExists()
    }
}
