// ACT-SURFACES: android.assistant
package io.github.murtazanasir.mailr.ui.assistant

import android.app.Application
import android.os.Looper
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.hasAnyDescendant
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.lifecycle.SavedStateHandle
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.TestGraph
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.MemoryAbout
import io.github.murtazanasir.mailr.data.api.MemoryNote
import io.github.murtazanasir.mailr.data.api.MemoryNotes
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.testing.until
import kotlinx.serialization.json.JsonObject
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.fail
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

private const val SESSION = """{"id":"chat-1","title":"Priya","turns":[
 {"role":"user","id":7,"content":"When is Priya free?","citations":[],"steps":[]},
 {"role":"assistant","user_message_id":7,"content":"Priya runs the reading group on Tuesdays.","status":"done","memory_ids":[11,12],
  "citations":[],"steps":[],"timeline":[
   {"type":"steps","steps":[{"id":"s1","name":"get_person","label":"Looking up Priya","ok":true,"summary":"Found",
     "args":{"email":"priya.shah@northwind.example"}}]},
   {"type":"text","content":"Priya runs the reading group on Tuesdays.","citations":[],"final":true}]},
 {"role":"user","id":9,"content":"Thanks","citations":[],"steps":[]},
 {"role":"assistant","user_message_id":9,"content":"You're welcome.","status":"done","citations":[],"steps":[]}],
 "running":false,"pending_approvals":[]}"""

/**
 * memory.used on turns reopened from the server (GET /agent/sessions/{id}): a chat the app shows again
 * after its process was stopped keeps each answer's "Used memories" line, as the web's replay does,
 * and a person the answer looked up keeps its card chip (person.card).
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class SessionReplayTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()

    private val app: Application = ApplicationProvider.getApplicationContext()
    private var server: MockWebServer? = null

    @After fun tearDown() {
        shadowOf(Looper.getMainLooper()).idle()
        graphs.close()
        server?.shutdown()
    }

    @Test fun storedTurnsKeepTheirMemories() {
        val turns = SessionReplay.turns(MailrJson.parseToJsonElement(SESSION) as JsonObject)
        assertEquals(listOf("user", "assistant", "user", "assistant"), turns.map { it.role })
        assertEquals(listOf(11L, 12L), turns[1].memoryIds)
        assertEquals("Priya runs the reading group on Tuesdays.", turns[1].answer)
        assertEquals(listOf("priya.shah@northwind.example"), ChatTimeline.lookedUp(turns[1].items))
        // An answer that used none has no line.
        assertEquals(emptyList<Long>(), turns[3].memoryIds)
        assertNull(turns[3].error)
    }

    @Test fun aChatReopenedAfterTheProcessStoppedShowsUsedMemories() {
        val s = MockWebServer()
        s.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse = when (request.path) {
                "/api/v1/agent/sessions/chat-1" -> MockResponse().setBody(SESSION)
                else -> MockResponse().setResponseCode(404).setBody("""{"detail":"not here"}""")
            }
        }
        s.start()
        server = s
        SessionStore(app).apply { server = s.url("/").toString(); save("tok", "csrf", "me@example.com", "Me") }
        val g = graphs.graph(app)
        val vm = graphs.vm { AssistantViewModel(g, SavedStateHandle(mapOf("assistant.session" to "chat-1"))) }
        eventually("the chat comes back") { vm.state.value.turns.isNotEmpty() }
        assertEquals("chat-1", vm.state.value.sessionId)
        assertEquals(listOf(11L, 12L), vm.state.value.turns[1].memoryIds)

        val actions = MemoryActions(
            load = { ids -> MemoryNotes(listOf(MemoryNote(11, "Priya runs the reading group", "fact", MemoryAbout(2, "Priya Shah")),
                MemoryNote(12, "You teach on Tuesday evenings", "routine", MemoryAbout(1, "Me", isSelf = true))).filter { it.id in ids }) },
            save = { _, _ -> }, delete = {})
        compose.setContent {
            Surface(Modifier.fillMaxSize()) {
                AssistantScreen(vm.state.value, AssistantCallbacks({}, {}, {}, {}, {}, { _, _ -> }, { _, _, _ -> }, {}, memories = actions))
            }
        }
        compose.onNodeWithText("Used memories · 2").performScrollTo().performClick()
        compose.until("tag \"used-memory\" shown 2x") { compose.onAllNodesWithTag("used-memory").fetchSemanticsNodes().size == 2 }
    }

    /** memory.open_chat: a memory's "From a chat" shows that chat with the user's message marked; a
     *  chat that cannot load leaves the open one alone and says so. */
    @Test fun aMemoryOpensItsChatAtTheMessage() {
        val s = MockWebServer()
        s.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse = when (request.path) {
                "/api/v1/agent/sessions/chat-1" -> MockResponse().setBody(SESSION)
                else -> MockResponse().setResponseCode(404).setBody("""{"detail":"not here"}""")
            }
        }
        s.start()
        server = s
        SessionStore(app).apply { server = s.url("/").toString(); save("tok", "csrf", "me@example.com", "Me") }
        val g = graphs.graph(app)
        val vm = graphs.vm { AssistantViewModel(g) }
        assertNull(kotlinx.coroutines.runBlocking { vm.openChat("chat-1", 9) })
        assertEquals("chat-1", vm.state.value.sessionId)
        assertEquals(9L, vm.state.value.found)
        assertEquals(listOf(7L, 9L), vm.state.value.turns.filter { it.role == "user" }.map { it.messageId })
        assertEquals("Couldn't open the chat. Check the connection.", kotlinx.coroutines.runBlocking { vm.openChat("gone", 1) })
        assertEquals("chat-1", vm.state.value.sessionId)

        compose.setContent {
            Surface(Modifier.fillMaxSize()) {
                AssistantScreen(vm.state.value, AssistantCallbacks({}, {}, {}, {}, {}, { _, _ -> }, { _, _, _ -> }, {}))
            }
        }
        compose.until("tag \"chat-found\" shown 1x") { compose.onAllNodesWithTag("chat-found").fetchSemanticsNodes().size == 1 }
        compose.onNode(hasTestTag("chat-found") and hasAnyDescendant(hasText("Thanks"))).assertExists()
    }
}
