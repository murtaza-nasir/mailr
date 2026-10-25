// ACT-SURFACES: android.assistant
package io.github.murtazanasir.mailr.ui.assistant

import android.app.Application
import android.os.Looper
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assert
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.test.isDialog
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextClearance
import androidx.compose.ui.test.performTextInput
import androidx.lifecycle.SavedStateHandle
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.TestGraph
import io.github.murtazanasir.mailr.data.agent.ChatSummary
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.testing.eventually
import kotlinx.coroutines.launch
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.util.Collections

/**
 * The open chat's title in the Ambrose top bar (agent.chat_rename from the top bar): it shows under the
 * name for an open chat and not for a new one, comes with the session of a first turn, and a tap on it
 * (or ⋮ → Rename) opens the Chats rename dialog; the rename goes to the server (PATCH
 * /agent/sessions/{id}), shows at once, and Undo puts the old title back; a refused rename puts it
 * back too. A rename in Chats moves the top bar's title, and the top bar's moves the Chats row.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class ChatTitleTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()

    private val app: Application = ApplicationProvider.getApplicationContext()
    private var server: MockWebServer? = null
    private val patches: MutableList<String> = Collections.synchronizedList(mutableListOf())
    /** The chat's title at the fake server, and whether the user gave it. */
    @Volatile private var title = "When is Priya free?"
    @Volatile private var renamed = false
    @Volatile private var refuse = false

    @After fun tearDown() {
        shadowOf(Looper.getMainLooper()).idle()
        graphs.close()
        server?.shutdown()
    }

    private fun idle() = shadowOf(Looper.getMainLooper()).idleFor(java.time.Duration.ofMillis(10))

    private fun until(what: String, cond: () -> Boolean) = eventually(what, idle = ::idle, detail = { "patches=$patches" }, check = cond)

    private fun stored() = """{"id":"c2","title":"$title","renamed":$renamed,"turns":[
     {"role":"user","id":7,"content":"When is Priya free?","citations":[],"steps":[]},
     {"role":"assistant","user_message_id":7,"content":"Priya is free on Tuesday afternoon.","status":"done","citations":[],"steps":[],
      "timeline":[{"type":"text","content":"Priya is free on Tuesday afternoon.","citations":[],"final":true}]}],
     "running":false,"pending_approvals":[]}"""

    private fun serve() {
        val s = MockWebServer()
        s.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse = when {
                request.method == "GET" && request.path == "/api/v1/agent/sessions/c2" -> MockResponse().setBody(stored())
                request.method == "PATCH" && request.path == "/api/v1/agent/sessions/c2" -> {
                    val body = request.body.readUtf8()
                    patches += body
                    if (refuse) MockResponse().setResponseCode(422).setBody("""{"detail":"no"}""")
                    else {
                        val typed = Regex("\"title\":\"(.*)\"").find(body)!!.groupValues[1]
                        val before = """{"title":"$title","renamed":$renamed}"""
                        if (typed.isEmpty()) { title = "When is Priya free?"; renamed = false } else { title = typed; renamed = true }
                        MockResponse().setBody("""{"id":"c2","title":"$title","renamed":$renamed,"previous":$before}""")
                    }
                }
                request.method == "POST" && request.path == "/api/v1/agent/chat" -> MockResponse().setHeader("Content-Type", "text/event-stream")
                    .setBody("event: session\ndata: {\"session_id\":\"c9\",\"title\":\"Who is Dana?\",\"seq\":1}\n\n" +
                        "event: final\ndata: {\"content\":\"Dana is your accountant.\",\"citations\":[],\"segment\":0,\"seq\":2}\n\n" +
                        "event: done\ndata: {\"stopped\":false,\"seq\":3}\n\n")
                else -> MockResponse().setResponseCode(404).setBody("""{"detail":"not here"}""")
            }
        }
        s.start()
        server = s
        SessionStore(app).apply { server = s.url("/").toString(); save("tok", "csrf", "me@example.com", "Me") }
    }

    private fun model(): AssistantViewModel {
        serve()
        val g = graphs.graph(app)
        return graphs.vm { AssistantViewModel(g, SavedStateHandle()) }
    }

    private fun opened(): AssistantViewModel = model().also { vm ->
        vm.open("c2")
        until("opened") { vm.state.value.sessionId == "c2" && vm.state.value.turns.size == 2 }
    }

    private fun show(vm: AssistantViewModel) {
        compose.setContent {
            val s by vm.state.collectAsState()
            Surface(Modifier.fillMaxSize()) {
                AssistantScreen(s, AssistantCallbacks({}, {}, vm::newChat, {}, {}, { _, _ -> }, { _, _, _ -> }, {},
                    openMemory = {}, openChats = {}, renameChat = vm::renameChat))
            }
        }
        compose.waitForIdle()
    }

    private val seen = Collections.synchronizedList(mutableListOf<ChatsMessage>())
    private val collecting = HashSet<AssistantViewModel>()

    /** The next snackbar message of a rename (collected on the test scope, so the main looper keeps running). */
    private fun nextRename(vm: AssistantViewModel): ChatsMessage {
        if (collecting.add(vm)) graphs.scope().launch { vm.renames.collect { seen += it } }
        val at = seen.size
        until("a rename message") { seen.size > at }
        return seen[at]
    }

    @Test fun anOpenChatShowsItsTitleUnderTheName() {
        val vm = opened()
        assertEquals("When is Priya free?", vm.state.value.title)
        assertFalse(vm.state.value.titleRenamed)
        show(vm)
        compose.onNodeWithText("Ambrose").assertIsDisplayed()
        // TalkBack: the title says it is the chat's, and the tap is named "Rename chat".
        compose.onNodeWithContentDescription("Chat: When is Priya free?").assertIsDisplayed()
        compose.onNodeWithTag("chat-title").assert(SemanticsMatcher("rename label") { n ->
            n.config.getOrNull(SemanticsActions.OnClick)?.label == "Rename chat"
        })
        // A new chat has no title.
        compose.runOnIdle { vm.newChat() }
        compose.waitForIdle()
        assertNull(vm.state.value.title)
        assertTrue(compose.onAllNodes(androidx.compose.ui.test.hasTestTag("chat-title")).fetchSemanticsNodes().isEmpty())
        compose.onNodeWithText("Ambrose").assertIsDisplayed()
    }

    @Test fun aFirstTurnBringsTheChatsTitle() {
        val vm = model()
        assertNull(vm.state.value.title)
        vm.send("Who is Dana?")
        until("answered") { !vm.state.value.busy && vm.state.value.turns.size == 2 }
        assertEquals("c9", vm.state.value.sessionId)
        assertEquals("Who is Dana?", vm.state.value.title)
    }

    @Test fun aTapOnTheTitleRenamesAndUndoPutsItBack() {
        val vm = opened()
        show(vm)
        compose.onNodeWithTag("chat-title").performClick()
        compose.onNode(isDialog()).assertExists()
        compose.onNodeWithTag("rename-chat-field").performTextClearance()
        compose.onNodeWithTag("rename-chat-field").performTextInput("Priya's week")
        compose.onNodeWithTag("rename-chat-save").performClick()
        compose.onNode(isDialog()).assertDoesNotExist()
        // It shows at once, and the server gets it.
        assertEquals("Priya's week", vm.state.value.title)
        val m = nextRename(vm)
        assertEquals("Chat renamed.", m.text)
        assertEquals(listOf("""{"title":"Priya's week"}"""), patches.toList())
        assertTrue(vm.state.value.titleRenamed)
        compose.onNodeWithContentDescription("Chat: Priya's week").assertIsDisplayed()
        // Undo: the first question was the automatic title, so the rename back is empty.
        vm.undoRename(m.undoRename!!)
        until("renamed back") { patches.size == 2 && vm.state.value.title == "When is Priya free?" }
        assertEquals("""{"title":""}""", patches[1])
        assertFalse(vm.state.value.titleRenamed)
    }

    @Test fun theMenuOffersRenameForAnOpenChat() {
        val vm = opened()
        show(vm)
        compose.onNodeWithTag("assistant-more").performClick()
        compose.onNodeWithTag("assistant-rename").performClick()
        compose.onNode(isDialog()).assertExists()
        compose.onNodeWithText("Leave empty to show the first question.").assertIsDisplayed()
    }

    @Test fun aRefusedRenamePutsTheOldTitleBack() {
        val vm = opened()
        refuse = true
        vm.renameChat("Priya's week")
        assertEquals("Priya's week", vm.state.value.title)
        val m = nextRename(vm)
        assertEquals("Couldn't rename. Check the connection.", m.text)
        assertNull(m.undoRename)
        assertEquals("When is Priya free?", vm.state.value.title)
        // The same title, or one too long, sends nothing.
        vm.renameChat("When is Priya free?")
        vm.renameChat("x".repeat(CHAT_TITLE_MAX + 1))
        idle()
        assertEquals(1, patches.size)
    }

    @Test fun renamesInChatsAndInTheTopBarFollowEachOther() {
        val vm = opened()
        // Chats → the top bar: a rename of the open chat there moves its title here.
        vm.titleChanged("c2", "Tuesday plans", true)
        assertEquals("Tuesday plans", vm.state.value.title)
        vm.titleChanged("c1", "Another chat", true)
        assertEquals("Tuesday plans", vm.state.value.title)
        // The top bar → Chats: the row takes the open chat's title.
        val chats = graphs.vm { ChatListViewModel({ listOf(ChatSummary("c2", "When is Priya free?"), ChatSummary("c1", "Northwind")) }, {}, graphs.scope()) }
        chats.load()
        until("loaded") { !chats.ui.value.loading }
        chats.showTitle("c2", "Tuesday plans", true)
        assertEquals(listOf("Tuesday plans", "Northwind"), chats.ui.value.chats.map { it.title })
        assertTrue(chats.ui.value.chats[0].renamed)
    }
}
