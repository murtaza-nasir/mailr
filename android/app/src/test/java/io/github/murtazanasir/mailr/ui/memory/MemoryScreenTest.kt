// ACT-SURFACES: android.settings
// Covers ACT memory.open_chat ("From a chat") with the page's other actions below.
package io.github.murtazanasir.mailr.ui.memory

import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.getValue
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assert
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertTextEquals
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollToNode
import androidx.compose.ui.test.performSemanticsAction
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.test.performTextReplacement
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import io.github.murtazanasir.mailr.TestGraph
import io.github.murtazanasir.mailr.data.api.MemorySubject
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.testing.until
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.IOException

/**
 * The Memory page on a phone (Robolectric): search, the kind chips with their examples, Keep and
 * Dismiss (memory.accept_suggestion, memory.dismiss_suggestion), Add memory and its kind picker
 * (memory.add), edit, delete with Undo, Create task (memory.make_task), Learn now
 * (memory.learn_now, memory.learning_status), the person card from People (person.card,
 * person.memories), and the empty, error and offline states.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class MemoryScreenTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()

    /** The chats "From a chat" asked to open (session, message), and what the opener answers. */
    private val chats = mutableListOf<Pair<String, Long?>>()
    private var chatAnswer: String? = null

    private fun show(store: FakeMemoryStore = FakeMemoryStore(), vip: (String) -> Boolean = { false }): FakeMemoryStore {
        val vm = graphs.vm { MemoryViewModel(store) }
        compose.setContent {
            MaterialTheme {
                val ui by vm.ui.collectAsStateWithLifecycle()
                MemoryPage(ui, store, "Ambrose", vm, vip, onBack = {}, openConversation = {}, wideOverride = false,
                    openChat = { s, m -> chats += s to m; chatAnswer })
            }
        }
        compose.until("\"learning\" in store.calls") { "learning" in store.calls }
        compose.waitForIdle()
        return store
    }

    /** A tap through the node's click action: the Add memory button may float over a row's end. */
    private fun androidx.compose.ui.test.SemanticsNodeInteraction.tap() =
        performSemanticsAction(androidx.compose.ui.semantics.SemanticsActions.OnClick)

    private fun scrollTo(text: String) {
        compose.onNodeWithTag("memory-list").performScrollToNode(hasText(text))
    }

    @Test fun searchAndKindChipsFilterEverySection() {
        show()
        compose.onNodeWithTag("memory-search").performTextInput("priya")
        compose.waitForIdle()
        scrollTo("Priya runs the Thursday design review")
        compose.onAllNodesWithText("You prefer short replies").assertCountEquals(0)
        compose.onNodeWithTag("memory-search").performTextReplacement("")
        // Each chip says its example (TalkBack and the tooltip).
        compose.onNode(hasContentDescription("Routine. Example: Teaches Tuesday evenings")).assertExists()
        compose.onNodeWithTag("kind-routine").performClick()
        compose.waitForIdle()
        compose.onAllNodesWithTag("memory-row").assertCountEquals(1)
        compose.onNodeWithText("You teach Tuesday evenings").assertExists()
        compose.onNodeWithTag("memory-search").performTextInput("zzz")
        compose.onNodeWithTag("memory-no-match").assertExists()
    }

    /** memory.open_chat: "From a chat" opens the chat a memory came from, before and after Keep; a
     *  memory whose chat is gone says "From a chat" without a link. */
    @Test fun fromAChatOpensTheChat() {
        val store = show()
        // The suggestion links; the commitment's chat was deleted, so its words are plain.
        compose.onAllNodesWithTag("memory-open-chat").assertCountEquals(1)
        compose.onNodeWithTag("memory-open-chat").assertTextEquals("From a chat").performClick()
        compose.until("chats.isNotEmpty()") { chats.isNotEmpty() }
        assertEquals(listOf("chat-dinner" to 7002L), chats.toList())
        compose.onAllNodesWithText("Keep")[1].performClick()
        compose.until("\"accept:22\" in store.calls") { "accept:22" in store.calls }
        compose.until("\"You are vegetarian\" shown 1x") { compose.onAllNodesWithText("You are vegetarian").fetchSemanticsNodes().size == 1 }
        scrollTo("You are vegetarian")
        compose.onAllNodesWithTag("memory-open-chat").assertCountEquals(1)
        // An answer that could not open it is said on the page.
        chatAnswer = "Couldn't open the chat. Check the connection."
        compose.onNodeWithTag("memory-open-chat").performClick()
        compose.until("\"Couldn't open the chat. Check the connection.\" shown") { compose.onAllNodesWithText("Couldn't open the chat. Check the connection.").fetchSemanticsNodes().isNotEmpty() }
    }

    /** The kind chips are one line that scrolls sideways on a narrow phone; they never wrap. */
    @Config(qualifiers = "w320dp-h700dp")
    @Test fun kindChipsAreOneScrollingLine() {
        show()
        val kinds = compose.onNodeWithTag("memory-kinds")
        kinds.assert(SemanticsMatcher.keyIsDefined(SemanticsProperties.HorizontalScrollAxisRange))
        fun chip(kind: String) = compose.onNodeWithTag("kind-$kind").fetchSemanticsNode()
        // One line: every chip at the same height.
        assertEquals(chip("fact").positionInRoot.y, chip("commitment").positionInRoot.y, 0.5f)
        // The last chip lies past the screen's edge until the row scrolls to it.
        val width = compose.onRoot().fetchSemanticsNode().size.width
        assertTrue(chip("commitment").let { it.positionInRoot.x + it.size.width } > width)
        kinds.performScrollToNode(hasTestTag("kind-commitment"))
        compose.waitForIdle()
        assertTrue(chip("commitment").let { it.positionInRoot.x + it.size.width } <= width + 0.5f)
        compose.onNodeWithTag("kind-commitment").performClick()
        compose.waitForIdle()
        compose.onNodeWithText("You promised Priya the brand guide by Friday").assertExists()
    }

    @Test fun keepDismissAndKeepAll() {
        val store = show()
        compose.onNodeWithText("To review").assertExists()
        compose.onNodeWithText("86% confidence").assertExists()
        compose.onAllNodesWithText("Dismiss")[0].performClick()
        compose.until("\"dismiss:21\" in store.calls") { "dismiss:21" in store.calls }
        compose.until("\"Dana is your accountant\" gone") { compose.onAllNodesWithText("Dana is your accountant").fetchSemanticsNodes().isEmpty() }
        // One left: no Keep all.
        compose.onAllNodesWithText("Keep all").assertCountEquals(0)
        compose.onNodeWithText("Keep").performClick()
        compose.until("\"accept:22\" in store.calls") { "accept:22" in store.calls }
        compose.until("\"To review\" gone") { compose.onAllNodesWithText("To review").fetchSemanticsNodes().isEmpty() }
        compose.until("\"1 memory kept.\" shown") { compose.onAllNodesWithText("1 memory kept.").fetchSemanticsNodes().isNotEmpty() }
    }

    @Test fun keepAllKeepsEverySuggestion() {
        val store = show()
        compose.onNodeWithTag("keep-all").performClick()
        compose.until("\"accept:21\" in store.calls && \"accept:22\" in store.calls") { "accept:21" in store.calls && "accept:22" in store.calls }
        compose.until("\"2 memories kept.\" shown") { compose.onAllNodesWithText("2 memories kept.").fetchSemanticsNodes().isNotEmpty() }
    }

    @Test fun addMemoryShowsEachKindsExampleAndSaves() {
        val store = show()
        compose.onNodeWithTag("memory-add").performClick()
        compose.onNodeWithText("Example: Promised João a licence quote").assertExists()
        compose.onNodeWithText("Example: Prefers short replies").assertExists()
        compose.onNodeWithTag("memory-text").performTextInput("You promised Sam a quote")
        compose.onNodeWithTag("pick-commitment").performClick()
        compose.onNodeWithTag("memory-save").performClick()
        compose.until("store.saved.isNotEmpty()") { store.saved.isNotEmpty() }
        val (id, edit) = store.saved.single()
        assertEquals(null, id)
        assertEquals("commitment", edit.kind)
        assertEquals(MemorySubject.You, edit.subject)
        compose.until("\"Saved to Ambrose's memory.\" shown") { compose.onAllNodesWithText("Saved to Ambrose's memory.").fetchSemanticsNodes().isNotEmpty() }
    }

    @Test fun aNewPersonOrTopicFromTheAboutPicker() {
        val store = show()
        compose.onNodeWithTag("memory-add").performClick()
        compose.onNodeWithTag("memory-text").performTextInput("Sam is the accountant")
        compose.onNodeWithTag("memory-subject").performClick()
        compose.until("\"entities\" in store.calls") { "entities" in store.calls }
        compose.onNodeWithText("Priya Shah (Person)").assertExists()
        compose.onNodeWithText("New person or topic…").performClick()
        compose.onNodeWithTag("memory-new-name").performTextInput("Sam Okafor")
        compose.onNodeWithTag("memory-save").performClick()
        compose.until("store.saved.isNotEmpty()") { store.saved.isNotEmpty() }
        assertEquals(MemorySubject.New("Sam Okafor", "person"), store.saved.single().second.subject)
    }

    @Test fun editThenDeleteWithUndo() {
        val store = show()
        scrollTo("You prefer short replies")
        compose.onNodeWithText("You prefer short replies").performClick()
        compose.onNodeWithText("Edit memory").assertExists()
        compose.onNodeWithTag("memory-text").performTextReplacement("You prefer very short replies")
        compose.onNodeWithTag("memory-save").performClick()
        compose.until("store.saved.isNotEmpty()") { store.saved.isNotEmpty() }
        assertEquals(11L, store.saved.single().first)
        assertTrue(store.saved.single().second.pinned)
        compose.until("\"You prefer very short replies\" shown") { compose.onAllNodesWithText("You prefer very short replies").fetchSemanticsNodes().isNotEmpty() }
        scrollTo("You teach Tuesday evenings")
        compose.onNodeWithTag("memory-more-12").tap()
        compose.onNodeWithText("Delete memory").performClick()
        compose.until("\"delete:12\" in store.calls") { "delete:12" in store.calls }
        compose.until("\"Undo\" shown") { compose.onAllNodesWithText("Undo").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("Undo").performClick()
        compose.until("store.restored.isNotEmpty()") { store.restored.isNotEmpty() }
        assertEquals(12L, store.restored.single().id)
    }

    @Test fun aCommitmentMakesATask() {
        val store = show()
        scrollTo("You promised Priya the brand guide by Friday")
        compose.onNodeWithTag("memory-more-14").tap()
        compose.onNodeWithText("Create task").performClick()
        compose.until("store.tasks.isNotEmpty()") { store.tasks.isNotEmpty() }
        assertEquals(14L, store.tasks.single().id)
        compose.until("\"Task added.\" shown") { compose.onAllNodesWithText("Task added.").fetchSemanticsNodes().isNotEmpty() }
        // Only a commitment offers it.
        compose.onNodeWithTag("memory-more-13").tap()
        compose.onAllNodesWithText("Create task").assertCountEquals(0)
    }

    @Test fun learningLineAndLearnNow() {
        val store = show()
        compose.onNodeWithTag("learning-status").assertExists()
        compose.onNodeWithText("Last learned", substring = true).assertExists()
        compose.onNodeWithText("· 2 new", substring = true).assertExists()
        compose.onNodeWithTag("learn-now").performClick()
        compose.until("\"learn\" in store.calls") { "learn" in store.calls }
    }

    @Test fun peopleOpenTheirCard() {
        val store = show(vip = { it == "priya.shah@northwind.example" })
        scrollTo("Priya runs the Thursday design review")
        compose.onNodeWithContentDescription("Open Priya Shah's card").tap()
        compose.waitForIdle()
        compose.until("\"person:priya.shah@northwind.example\" in store.calls") { "person:priya.shah@northwind.example" in store.calls }
        compose.until("\"Ambrose's memory\" shown") { compose.onAllNodesWithText("Ambrose's memory").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("Art director").assertExists()
        compose.onNodeWithText("Studio team").assertExists()
        compose.onNodeWithText("VIP").assertExists()
        compose.onNodeWithText("+1 312 555 0142").assertExists()
    }

    @Test fun aPersonWithNoMailSaysSo() {
        val store = show()
        scrollTo("Theo prefers calls to email")
        compose.onNodeWithContentDescription("Open Theo Martin's card").tap()
        compose.waitForIdle()
        compose.until("\"entity:3\" in store.calls") { "entity:3" in store.calls }
        compose.until("\"No mail\" shown") { compose.onAllNodesWithText("No mail").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("Studio neighbour, runs the print shop").assertExists()
    }

    @Test fun emptyOfflineAndErrorStates() {
        val empty = FakeMemoryStore().apply { active.clear(); suggested.clear() }
        show(empty)
        compose.onNodeWithText("No memories yet").assertExists()
        compose.onNodeWithText("Tell Ambrose “remember that Sam is my accountant”, or add one here.").assertExists()
    }

    @Test fun offlineShowsRetry() {
        val store = FakeMemoryStore(failWith = IOException("offline"))
        show(store)
        compose.onNodeWithText("You're offline").assertExists()
        store.failWith = null
        compose.onNodeWithText("Retry").performClick()
        compose.until("\"To review\" shown") { compose.onAllNodesWithText("To review").fetchSemanticsNodes().isNotEmpty() }
    }

    @Test fun aFailedLoadSaysSo() {
        show(FakeMemoryStore(failWith = IllegalStateException("500")))
        compose.onNodeWithText("Couldn't load memories.").assertExists()
        compose.onNodeWithText("Retry").assertExists()
    }
}
