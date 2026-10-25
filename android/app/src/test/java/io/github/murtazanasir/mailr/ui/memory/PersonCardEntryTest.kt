// ACT-SURFACES: android.reader android.assistant
package io.github.murtazanasir.mailr.ui.memory

import android.app.Application
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextInput
import io.github.murtazanasir.mailr.Fixtures
import io.github.murtazanasir.mailr.TestGraph
import io.github.murtazanasir.mailr.data.agent.AgentEvent
import io.github.murtazanasir.mailr.data.api.MemorySubject
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.testing.until
import io.github.murtazanasir.mailr.ui.assistant.AssistantCallbacks
import io.github.murtazanasir.mailr.ui.assistant.AssistantScreen
import io.github.murtazanasir.mailr.ui.assistant.AssistantState
import io.github.murtazanasir.mailr.ui.assistant.ChatTimeline
import io.github.murtazanasir.mailr.ui.assistant.ChatTurn
import io.github.murtazanasir.mailr.ui.conversation.ConversationActions
import io.github.murtazanasir.mailr.ui.conversation.ConversationScreen
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * person.card and person.memories from the reader and from Ambrose: the sender's picture (and the
 * message's ⋮ → Open card) opens their card; a person an answer looked up (get_person) gets a
 * "Card: …" chip; Ambrose's ⋮ opens Memory. On the card, Ambrose's memory takes a new memory about
 * that person. (The Memory page's People: MemoryScreenTest.)
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class PersonCardEntryTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()

    private fun reader(opened: MutableList<String>) {
        compose.setContent {
            MaterialTheme {
                ConversationScreen(Fixtures.detail, null, Fixtures.labels, setOf(502L), emptyMap(), null, null,
                    tabletop = false, snackbar = SnackbarHostState(), actions = ConversationActions(
                        back = null, act = {}, toggle = {}, expandAll = {}, star = { _, _ -> }, reply = { _, _ -> },
                        openAttachment = { _, _ -> }, showImages = {}, snooze = {}, labels = {}, summarize = {}, unsubscribe = {},
                        mailto = {}, retry = {}, openPerson = { opened += it }))
            }
        }
    }

    @Test fun theSendersPictureOpensTheirCard() {
        val opened = mutableListOf<String>()
        reader(opened)
        compose.onAllNodesWithTag("sender-avatar")[1].performClick()
        compose.onNodeWithContentDescription("Message actions").performClick()
        compose.onNodeWithText("Open card").performClick()
        assertEquals(listOf("chair@pads.example", "chair@pads.example"), opened)
    }

    @Test fun aPersonTheAnswerLookedUpGetsACardChip() {
        val opened = mutableListOf<String>()
        var memory = 0
        val items = listOf(
            AgentEvent.StepStarted("s1", "get_person", "Looking up Priya", segment = 0, email = "Priya.Shah@northwind.example"),
            AgentEvent.StepDone("s1", "get_person", "Looking up Priya", true, "Found", null, null, segment = 0),
            AgentEvent.Final("Priya is the art director at Northwind Studio.", emptyList(), 1),
        ).fold(emptyList<io.github.murtazanasir.mailr.ui.assistant.TurnItem>()) { acc, ev -> ChatTimeline.apply(acc, ev) }
        compose.setContent {
            MaterialTheme {
                Surface(Modifier.fillMaxSize()) {
                    AssistantScreen(AssistantState(turns = listOf(ChatTurn("user", "Who is Priya?"), ChatTurn("assistant", items = items))),
                        AssistantCallbacks({}, {}, {}, {}, {}, { _, _ -> }, { _, _, _ -> }, {},
                            openMemory = { memory++ }, openPerson = { opened += it }))
                }
            }
        }
        compose.onNodeWithText("Card: priya.shah@northwind.example").performScrollTo().performClick()
        assertEquals(listOf("priya.shah@northwind.example"), opened)
        // Ambrose's ⋮ → Memory.
        compose.onNodeWithTag("assistant-more").performClick()
        compose.onNodeWithText("Memory").performClick()
        assertEquals(1, memory)
    }

    @Test fun onTheCardANewMemoryIsAboutThatPerson() {
        val store = FakeMemoryStore()
        compose.setContent {
            MaterialTheme {
                Surface(Modifier.fillMaxSize()) {
                    PersonCardHost(PersonTarget.Email("priya.shah@northwind.example"), store, "Ambrose", { false }, onClose = {})
                }
            }
        }
        compose.until("\"Ambrose's memory\" shown") { compose.onAllNodesWithText("Ambrose's memory").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("Priya runs the Thursday design review").assertExists()
        compose.onNodeWithText("You wrote 14 · they wrote 22 · last 18 hr ago").assertExists()
        compose.onNodeWithTag("person-add-memory").performClick()
        compose.onNodeWithTag("memory-text").performTextInput("Priya likes oat milk")
        compose.onNodeWithTag("memory-save").performClick()
        compose.until("store.saved.isNotEmpty()") { store.saved.isNotEmpty() }
        assertEquals(MemorySubject.Known(2), store.saved.single().second.subject)
        compose.until("\"Priya likes oat milk\" shown") { compose.onAllNodesWithText("Priya likes oat milk").fetchSemanticsNodes().isNotEmpty() }
    }

    @Test fun aCardThatCannotLoadOffersRetry() {
        val store = FakeMemoryStore()
        compose.setContent {
            MaterialTheme { PersonCardHost(PersonTarget.Email("nobody@example.org"), store, "Ambrose", { false }, onClose = {}) }
        }
        compose.until("\"Retry\" shown") { compose.onAllNodesWithText("Retry").fetchSemanticsNodes().isNotEmpty() }
    }
}
