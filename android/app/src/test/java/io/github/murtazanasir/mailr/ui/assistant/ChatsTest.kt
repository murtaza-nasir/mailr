// ACT-SURFACES: android.assistant
package io.github.murtazanasir.mailr.ui.assistant

import android.app.Application
import android.os.Looper
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.lifecycle.SavedStateHandle
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.TestGraph
import io.github.murtazanasir.mailr.data.agent.ChatSummary
import io.github.murtazanasir.mailr.data.agent.PreviousTitle
import io.github.murtazanasir.mailr.data.agent.RenameResult
import io.github.murtazanasir.mailr.data.agent.TextPart
import androidx.compose.ui.test.performTextClearance
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.test.isDialog
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.time.AppClock
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okhttp3.mockwebserver.SocketPolicy
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.io.IOException
import java.util.Collections

/**
 * Chats and dropped streams on Android: agent.chat_list (the list, its empty, offline and error states),
 * agent.chat_open (an earlier chat opens and goes on), agent.chat_delete (Undo sends nothing; the
 * delete goes when the snackbar closes) and agent.stream_resume (a stream cut mid-answer re-attaches
 * with after=<seq> and ends with the whole answer once, the waiting approval card included; a lost run
 * reloads the chat, where the turn shows as interrupted). agent.chat_search (the search field, the
 * results with their marked hits, a search that matches nothing) and agent.chat_rename (the dialog,
 * the snackbar's Undo, a refused rename).
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class ChatsTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()

    private val app: Application = ApplicationProvider.getApplicationContext()
    private var server: MockWebServer? = null
    // Copy-on-write: the server thread adds while a wait iterates it (ChatCommandsTest).
    private val requests: MutableList<String> = java.util.concurrent.CopyOnWriteArrayList()
    private val bodies: MutableList<String> = Collections.synchronizedList(mutableListOf())

    @Before fun fast() {
        AssistantViewModel.RETRY_MS = 20
    }

    @After fun tearDown() {
        AssistantViewModel.RETRY_MS = 1000
        shadowOf(Looper.getMainLooper()).idle()
        graphs.close()
        server?.shutdown()
    }

    /** Runs the main looper, moving its clock on a little, so the waits between attempts to re-attach end. */
    private fun idle() = shadowOf(Looper.getMainLooper()).idleFor(java.time.Duration.ofMillis(10))

    private var detail: () -> Any? = { null }

    private fun until(what: String, cond: () -> Boolean) =
        eventually(what, idle = ::idle, detail = { "${detail()} requests=$requests" }, check = cond)

    private fun serve(route: (RecordedRequest) -> MockResponse?) {
        val s = MockWebServer()
        s.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                requests += "${request.method} ${request.path}"
                if (request.method == "POST") bodies += request.body.readUtf8()
                return route(request) ?: MockResponse().setResponseCode(404).setBody("""{"detail":"not here"}""")
            }
        }
        s.start()
        server = s
        SessionStore(app).apply { server = s.url("/").toString(); save("tok", "csrf", "me@example.com", "Me") }
    }

    private fun sse(events: List<Pair<String, String>>) = MockResponse().setHeader("Content-Type", "text/event-stream")
        .setBody(events.joinToString("") { (name, data) -> "event: $name\ndata: $data\n\n" })

    // ---- the list ------------------------------------------------------------------------------

    private val northwind = ChatSummary("c1", "Find the Northwind invoice", "2026-10-02T09:12:00-05:00", "The latest invoice is INV-2041.")
    private val priya = ChatSummary("c2", "When is Priya free?", "2026-10-01T16:40:00-05:00", "Priya is free on Tuesday afternoon.")
    private val standup = ChatSummary("c3", "Agenda for Monday's standup", "2026-09-28T08:05:00-05:00", null)

    private class Fake(var chats: List<ChatSummary>) {
        var fail: Exception? = null
        var failDelete: Exception? = null
        var failRename: Exception? = null
        val deleted = mutableListOf<String>()
        val searched = mutableListOf<String>()
        val renames = mutableListOf<Pair<String, String>>()
        suspend fun find(q: String): List<ChatSummary> {
            searched += q
            return chats.filter { q.lowercase() in it.title.lowercase() || q.lowercase() in it.preview.orEmpty().lowercase() }
                .map { it.copy(snippetParts = listOf(TextPart(it.preview.orEmpty()))) }
        }
        suspend fun rename(id: String, title: String): RenameResult {
            failRename?.let { throw it }
            renames += id to title
            val c = chats.first { it.id == id }
            val first = mapOf("c1" to "Find the Northwind invoice", "c2" to "When is Priya free?", "c3" to "Agenda for Monday's standup")[id]!!
            val now = if (title.isEmpty()) c.copy(title = first, renamed = false) else c.copy(title = title, renamed = true)
            chats = chats.map { if (it.id == id) now else it }
            return RenameResult(id, now.title, now.renamed, PreviousTitle(c.title, c.renamed))
        }
        suspend fun list(): List<ChatSummary> = fail?.let { throw it } ?: chats
        suspend fun remove(id: String) {
            failDelete?.let { throw it }
            deleted += id
            chats = chats.filter { it.id != id }
        }
    }

    private fun listModel(fake: Fake, onDeleted: (String) -> Unit = {}): ChatListViewModel {
        val vm = graphs.vm { ChatListViewModel(fake::list, fake::remove, graphs.scope(), onDeleted, fake::find, fake::rename) }
        vm.load()
        until("loaded") { !vm.ui.value.loading }
        return vm
    }

    private fun next(vm: ChatListViewModel) = idle().let { runBlocking { withTimeout(2000) { vm.messages.first() } } }

    @Test fun theListLoadsNewestFirstAndSaysWhenItCannot() {
        val fake = Fake(listOf(northwind, priya, standup))
        val vm = listModel(fake)
        assertEquals(listOf("c1", "c2", "c3"), vm.ui.value.chats.map { it.id })
        assertNull(vm.ui.value.problem)

        fake.fail = IOException("offline")
        vm.load()
        until("offline") { vm.ui.value.problem == ChatsProblem.OFFLINE }
        fake.fail = IllegalStateException("500")
        vm.load()
        until("failed") { vm.ui.value.problem == ChatsProblem.FAILED }
        fake.fail = null
        vm.load()
        until("retried") { vm.ui.value.problem == null && vm.ui.value.chats.size == 3 }
    }

    @Test fun deleteHasUndoAndGoesToTheServerOnlyWhenTheSnackbarCloses() {
        val fake = Fake(listOf(northwind, priya, standup))
        val gone = mutableListOf<String>()
        val vm = listModel(fake) { gone += it }
        vm.delete(priya)
        assertEquals(listOf("c1", "c3"), vm.ui.value.chats.map { it.id })
        val m = next(vm)
        assertEquals("Chat deleted.", m.text)
        assertEquals(priya, m.undo)
        // Undo: back where it was, and nothing was sent.
        vm.undo(priya)
        assertEquals(listOf("c1", "c2", "c3"), vm.ui.value.chats.map { it.id })
        idle()
        assertTrue(fake.deleted.isEmpty())

        // The snackbar closes: the delete goes, and the open chat hears of it.
        vm.delete(priya)
        next(vm)
        vm.commit(priya)
        until("deleted") { fake.deleted == listOf("c2") && gone == listOf("c2") }
        // A list read again does not bring it back.
        vm.load()
        until("reloaded") { !vm.ui.value.loading }
        assertEquals(listOf("c1", "c3"), vm.ui.value.chats.map { it.id })
    }

    @Test fun aDeleteTheServerRefusesComesBack() {
        val fake = Fake(listOf(northwind, priya)).apply { failDelete = IOException("offline") }
        val vm = listModel(fake)
        vm.delete(northwind)
        next(vm)
        vm.commit(northwind)
        until("restored") { vm.ui.value.chats.map { it.id } == listOf("c1", "c2") }
        assertEquals("Couldn't delete. Check the connection.", next(vm).text)
    }

    @Test fun theScreenOpensAChatAndOffersDelete() {
        val opened = mutableListOf<String>()
        val deleted = mutableListOf<String>()
        val cb = ChatsCallbacks(open = { opened += it.id }, newChat = {}, delete = { deleted += it.id }, retry = {}, close = {})
        compose.setContent {
            Surface(Modifier.fillMaxSize()) { ChatListScreen(ChatsUi(loading = false, chats = listOf(northwind, priya, standup)), "Ambrose", "c2", cb) }
        }
        compose.onNodeWithText("The latest invoice is INV-2041.").assertIsDisplayed()
        compose.onNodeWithText("When is Priya free?").performClick()
        assertEquals(listOf("c2"), opened)
        compose.onAllNodesWithTag("chat-more")[0].performClick()
        compose.onNodeWithTag("chat-delete").performClick()
        assertEquals(listOf("c1"), deleted)
    }

    // ---- search and rename ---------------------------------------------------------------------

    @Test fun searchAsksTheServerOnceTheTypingStopsAndBlankShowsTheList() {
        ChatListViewModel.SEARCH_DELAY_MS = 30
        try {
            val fake = Fake(listOf(northwind, priya, standup))
            val vm = listModel(fake)
            vm.search("pri")
            vm.search("priya")
            assertTrue(vm.ui.value.searching)
            until("results") { vm.ui.value.results != null }
            assertEquals(listOf("priya"), fake.searched) // the first words never went out
            assertEquals(listOf("c2"), vm.ui.value.results!!.map { it.id })
            // Delete from the results, then Undo: it is back in both.
            vm.delete(priya)
            assertEquals(emptyList<String>(), vm.ui.value.results!!.map { it.id })
            assertEquals(listOf("c1", "c3"), vm.ui.value.chats.map { it.id })
            next(vm)
            vm.undo(priya)
            assertEquals(listOf("c2"), vm.ui.value.results!!.map { it.id })
            assertEquals(listOf("c1", "c2", "c3"), vm.ui.value.chats.map { it.id })
            vm.search("   ")
            assertNull(vm.ui.value.results)
            assertFalse(vm.ui.value.searching)
        } finally {
            ChatListViewModel.SEARCH_DELAY_MS = 250
        }
    }

    @Test fun renameShowsAtOnceAndUndoPutsTheTitleBack() {
        val fake = Fake(listOf(northwind, priya, standup))
        val vm = listModel(fake)
        vm.rename(priya, "  Priya's\n  week ")
        assertEquals("Priya's week", vm.ui.value.chats[1].title) // at once
        val m = next(vm)
        assertEquals("Chat renamed.", m.text)
        assertEquals(listOf("c2" to "Priya's week"), fake.renames)
        assertTrue(vm.ui.value.chats[1].renamed)
        // Nothing to send: the same title, or too long.
        vm.rename(vm.ui.value.chats[1], "Priya's week")
        vm.rename(vm.ui.value.chats[1], "x".repeat(CHAT_TITLE_MAX + 1))
        idle()
        assertEquals(1, fake.renames.size)
        // Undo: back to the automatic title (an empty title goes to the server).
        vm.undoRename(m.undoRename!!)
        until("undone") { fake.renames.size == 2 }
        assertEquals("c2" to "", fake.renames[1])
        until("title back") { vm.ui.value.chats[1].title == "When is Priya free?" && !vm.ui.value.chats[1].renamed }
    }

    @Test fun aRenameTheServerRefusesPutsTheOldTitleBack() {
        val fake = Fake(listOf(northwind, priya)).apply { failRename = IOException("offline") }
        val vm = listModel(fake)
        vm.rename(northwind, "Invoices")
        assertEquals("Couldn't rename. Check the connection.", next(vm).text)
        assertEquals("Find the Northwind invoice", vm.ui.value.chats[0].title)
    }

    @Test fun theRenameDialogSendsWhatWasTyped() {
        val renamed = mutableListOf<Pair<String, String>>()
        val cb = ChatsCallbacks({}, {}, {}, {}, {}, rename = { c, t -> renamed += c.id to t })
        compose.setContent {
            Surface(Modifier.fillMaxSize()) { ChatListScreen(ChatsUi(loading = false, chats = listOf(northwind, priya)), "Ambrose", null, cb) }
        }
        compose.onAllNodesWithTag("chat-more")[1].performClick()
        compose.onNodeWithTag("chat-rename").performClick()
        compose.onNode(isDialog()).assertExists()
        compose.onNodeWithText("Leave empty to show the first question.").assertIsDisplayed()
        compose.onNodeWithTag("rename-chat-field").performTextClearance()
        compose.onNodeWithTag("rename-chat-field").performTextInput("Priya's week")
        compose.onNodeWithTag("rename-chat-save").performClick()
        assertEquals(listOf("c2" to "Priya's week"), renamed)
        compose.onNode(isDialog()).assertDoesNotExist()
    }

    @Test fun theSearchFieldOpensFromTheTopBarAndShowsMarkedResults() {
        val typed = mutableListOf<String>()
        var ui by androidx.compose.runtime.mutableStateOf(ChatsUi(loading = false, chats = listOf(northwind, priya, standup)))
        val cb = ChatsCallbacks({}, {}, {}, {}, {}, search = { q -> typed += q; ui = ui.copy(query = q) })
        compose.setContent { Surface(Modifier.fillMaxSize()) { ChatListScreen(ui, "Ambrose", null, cb) } }
        compose.onNodeWithTag("chats-search").performClick()
        compose.onNodeWithTag("chats-search-field").performTextInput("invoice")
        assertEquals("invoice", typed.last())
        ui = ui.copy(results = listOf(northwind.copy(snippetParts = listOf(TextPart("The latest "), TextPart("invoice", true), TextPart(" is INV-2041.")))))
        compose.waitForIdle()
        compose.onNodeWithTag("chat-snippet", useUnmergedTree = true).assertExists()
        compose.onNodeWithText("The latest invoice is INV-2041.", useUnmergedTree = true).assertExists()
        compose.onAllNodesWithTag("chat-row").assertCountEquals(1)
        ui = ui.copy(query = "zebracorn", results = emptyList())
        compose.onNodeWithText("No chats match “zebracorn”.").assertIsDisplayed()
        // Closing the search clears it.
        compose.onNodeWithTag("chats-search-close").performClick()
        assertEquals("", typed.last())
    }

    @Test fun emptyAndOfflineStates() {
        var ui by androidx.compose.runtime.mutableStateOf(ChatsUi(loading = false))
        val cb = ChatsCallbacks({}, {}, {}, {}, {})
        compose.setContent { Surface(Modifier.fillMaxSize()) { ChatListScreen(ui, "Ambrose", null, cb) } }
        compose.onNodeWithText("No chats yet").assertIsDisplayed()
        compose.onNodeWithText("Your chats with Ambrose show here.").assertIsDisplayed()
        ui = ChatsUi(loading = false, problem = ChatsProblem.OFFLINE)
        compose.onNodeWithText("You're offline").assertIsDisplayed()
        compose.onNodeWithText("Retry").assertIsDisplayed()
        ui = ChatsUi(loading = false, problem = ChatsProblem.FAILED)
        compose.onNodeWithText("Couldn't load chats.").assertIsDisplayed()
    }

    // ---- open and continue ---------------------------------------------------------------------

    private val stored = """{"id":"c2","title":"When is Priya free?","turns":[
     {"role":"user","id":7,"content":"When is Priya free?","citations":[],"steps":[]},
     {"role":"assistant","user_message_id":7,"content":"Priya is free on Tuesday afternoon.","status":"done","citations":[],"steps":[],
      "timeline":[{"type":"text","content":"Priya is free on Tuesday afternoon.","citations":[],"final":true}]}],
     "running":false,"pending_approvals":[]}"""

    @Test fun anEarlierChatOpensAndGoesOn() {
        serve { r ->
            when {
                r.path == "/api/v1/agent/sessions/c2" -> MockResponse().setBody(stored)
                r.method == "POST" && r.path == "/api/v1/agent/chat" -> sse(listOf(
                    "session" to """{"session_id":"c2","seq":1}""",
                    "token" to """{"text":"Book 2 pm.","segment":0,"seq":2}""",
                    "final" to """{"content":"Book 2 pm.","citations":[],"segment":0,"seq":3}""",
                    "done" to """{"stopped":false,"seq":4}"""))
                else -> null
            }
        }
        val g = graphs.graph(app)
        val vm = graphs.vm { AssistantViewModel(g, SavedStateHandle()) }
        vm.open("c2")
        until("opened") { vm.state.value.sessionId == "c2" && vm.state.value.turns.size == 2 }
        assertFalse(vm.state.value.opening)
        vm.send("Then book Tuesday")
        until("answered") { !vm.state.value.busy && vm.state.value.turns.size == 4 }
        assertEquals("Book 2 pm.", vm.state.value.turns[3].answer)
        assertTrue(bodies.single().contains("\"session_id\":\"c2\""))
    }

    /** Notes the recall tool returned arrive with "done" (the prompt's came with "session"); both show. */
    @Test fun usedMemoriesTakeTheDoneEventsList() {
        serve { r ->
            when {
                r.method == "POST" && r.path == "/api/v1/agent/chat" -> sse(listOf(
                    "session" to """{"session_id":"c9","memory_ids":[3],"seq":1}""",
                    "token" to """{"text":"Dana is your accountant.","segment":0,"seq":2}""",
                    "final" to """{"content":"Dana is your accountant.","citations":[],"segment":0,"seq":3}""",
                    "done" to """{"stopped":false,"memory_ids":[3,8],"seq":4}"""))
                else -> null
            }
        }
        val g = graphs.graph(app)
        val vm = graphs.vm { AssistantViewModel(g, SavedStateHandle()) }
        vm.send("Who is Dana?")
        until("answered") { !vm.state.value.busy && vm.state.value.turns.size == 2 }
        assertEquals(listOf(3L, 8L), vm.state.value.turns[1].memoryIds)
    }

    @Test fun aChatThatCannotOpenLeavesTheScreenAsItWas() {
        serve { null }
        val g = graphs.graph(app)
        val vm = graphs.vm { AssistantViewModel(g, SavedStateHandle()) }
        val notices = mutableListOf<String>()
        graphs.scope().launch { vm.notices.collect { notices += it } }
        vm.open("gone")
        until("the notice") { notices.isNotEmpty() }
        assertEquals(listOf("Couldn't open the chat. Check the connection."), notices)
        assertFalse(vm.state.value.opening)
        assertNull(vm.state.value.sessionId)
    }

    // ---- a dropped stream --------------------------------------------------------------------------

    /** The run's buffer at the server: every event with its seq; the answer is these tokens joined. */
    private val tokens = listOf("Archived ", "the two ", "Northwind ", "newsletters. ", "One ", "move ", "needs ", "your ", "approval.")
    private val run: List<Pair<String, String>> = buildList {
        add("session" to """{"session_id":"c9","seq":1}""")
        add("tool_start" to """{"id":"s1","name":"modify_conversations","label":"Moving 1 conversation","segment":0,"seq":2}""")
        add("tool" to """{"id":"s1","name":"modify_conversations","label":"Moving 1 conversation","ok":true,"summary":"needs approval","segment":0,"approval_id":42,"seq":3}""")
        add("approval_required" to """{"approval_id":42,"title":"Move 1 message to Trash","preview":{"title":"Move 1 message to Trash","lines":["Northwind weekly"]},"segment":0,"seq":4}""")
        tokens.forEachIndexed { i, t -> add("token" to """{"text":"$t","segment":1,"seq":${5 + i}}""") }
        val n = 5 + tokens.size
        add("final" to """{"content":"${tokens.joinToString("")}","citations":[],"segment":1,"seq":$n}""")
        add("done" to """{"stopped":false,"seq":${n + 1}}""")
    }
    private fun seqOf(data: String) = Regex("\"seq\":(\\d+)").find(data)!!.groupValues[1].toInt()

    @Test fun aStreamCutMidAnswerReattachesAndShowsTheWholeAnswerOnce() {
        var follows = 0
        serve { r ->
            val path = r.path.orEmpty()
            when {
                r.method == "POST" && path == "/api/v1/agent/chat" ->
                    // The connection breaks part-way through the answer (a network change).
                    sse(run).setSocketPolicy(SocketPolicy.DISCONNECT_DURING_RESPONSE_BODY)
                path.startsWith("/api/v1/agent/chat/c9/stream") -> {
                    follows++
                    // The first try comes before the run is registered (404), as on the server.
                    if (follows == 1) null
                    else {
                        val after = r.requestUrl!!.queryParameter("after")!!.toInt()
                        sse(run.filter { seqOf(it.second) > after })
                    }
                }
                path.startsWith("/api/v1/agent/sessions?") ->
                    MockResponse().setBody("""[{"id":"c9","title":"Tidy Northwind","updated_at":"${AppClock.now().toOffsetDateTime()}"}]""")
                else -> null
            }
        }
        val g = graphs.graph(app)
        val vm = graphs.vm { AssistantViewModel(g, SavedStateHandle()) }
        detail = { vm.state.value }
        var sawReconnecting = false
        vm.send("Tidy up Northwind")
        until("finished") {
            if (vm.state.value.turns.lastOrNull()?.reconnecting == true) sawReconnecting = true
            !vm.state.value.busy && vm.state.value.turns.size == 2 && vm.state.value.turns[1].finishedAt != null
        }
        val turn = vm.state.value.turns[1]
        assertTrue(sawReconnecting)
        assertFalse(turn.reconnecting)
        assertNull(turn.error)
        assertEquals("c9", vm.state.value.sessionId)
        // Every token once: what arrived before the cut is not sent again.
        val text = turn.items.filterIsInstance<TurnItem.Text>().single()
        assertEquals(tokens.joinToString(""), text.raw)
        assertEquals(tokens.joinToString(""), turn.answer)
        // The approval the turn asked for still shows.
        assertEquals(listOf(42L), turn.approvals.map { it.id })
        assertTrue(follows >= 2)
    }

    @Test fun aLostRunReloadsTheChatWhereTheTurnIsInterrupted() {
        val replay = """{"id":"c9","title":"Tidy Northwind","turns":[
         {"role":"user","id":3,"content":"Tidy up Northwind","citations":[],"steps":[]},
         {"role":"assistant","user_message_id":3,"content":"","status":"interrupted","citations":[],"steps":[],
          "timeline":[{"type":"text","content":"Archived the two","citations":[],"final":false}]}],
         "running":false,"pending_approvals":[{"id":42,"action":"modify_conversations","status":"pending",
          "preview":{"title":"Move 1 message to Trash","lines":[]}}]}"""
        serve { r ->
            val path = r.path.orEmpty()
            when {
                r.method == "POST" && path == "/api/v1/agent/chat" -> sse(run.take(6))  // the server restarted: the stream just ends
                path == "/api/v1/agent/sessions/c9" -> MockResponse().setBody(replay)
                else -> null  // no run in the new process: 404 every time
            }
        }
        val g = graphs.graph(app)
        val vm = graphs.vm { AssistantViewModel(g, SavedStateHandle()) }
        detail = { vm.state.value }
        vm.send("Tidy up Northwind")
        until("reloaded") { !vm.state.value.busy && vm.state.value.turns.getOrNull(1)?.interrupted == true }
        val turns = vm.state.value.turns
        // The chat as the server has it: no second copy of the question, no half answer kept beside it.
        assertEquals(listOf("user", "assistant"), turns.map { it.role })
        assertEquals("Archived the two", turns[1].answer)
        assertEquals(listOf(42L), turns[1].approvals.map { it.id })
        assertEquals(3, requests.count { it.startsWith("GET /api/v1/agent/chat/c9/stream") })
    }

    @Test fun aChatReopenedWhileItsAnswerRunsFollowsIt() {
        val running = """{"id":"c9","title":"Tidy Northwind","turns":[
         {"role":"user","id":3,"content":"Tidy up Northwind","citations":[],"steps":[]},
         {"role":"assistant","user_message_id":3,"content":"","status":"running","citations":[],"steps":[],"timeline":[]}],
         "running":true,"pending_approvals":[]}"""
        serve { r ->
            val path = r.path.orEmpty()
            when {
                path == "/api/v1/agent/sessions/c9" -> MockResponse().setBody(running)
                path == "/api/v1/agent/chat/c9/stream?after=0" -> sse(run)
                else -> null
            }
        }
        val g = graphs.graph(app)
        val vm = graphs.vm { AssistantViewModel(g, SavedStateHandle(mapOf("assistant.session" to "c9"))) }
        until("followed") { !vm.state.value.busy && vm.state.value.turns.getOrNull(1)?.answer == tokens.joinToString("") }
        assertEquals(2, vm.state.value.turns.size)
        assertEquals(listOf(42L), vm.state.value.turns[1].approvals.map { it.id })
    }

    @Test fun reconnectingShowsInTheTurn() {
        val s = AssistantState(turns = listOf(ChatTurn("user", "Tidy up Northwind"),
            ChatTurn("assistant", items = listOf(TurnItem.Text(0, "Archived the two ")), streaming = true, reconnecting = true)), busy = true)
        compose.setContent {
            Surface(Modifier.fillMaxSize()) { AssistantScreen(s, AssistantCallbacks({}, {}, {}, {}, {}, { _, _ -> }, { _, _, _ -> }, {})) }
        }
        compose.onNodeWithText("Reconnecting…").assertIsDisplayed()
        compose.onAllNodesWithTag("thinking").fetchSemanticsNodes().let { assertTrue(it.isEmpty()) }
    }

    // ---- a longer outage, and Retry ------------------------------------------------------------------

    /** The server away during a deploy: the proxy in front of it answers 502. */
    private val down get() = MockResponse().setResponseCode(502).setBody("Bad Gateway")

    /** The server is away for over 20 s (a deploy): the app keeps reconnecting and the answer arrives whole. */
    @Test fun aServerAwayForTwentySecondsIsWaitedOut() {
        var follows = 0
        serve { r ->
            val path = r.path.orEmpty()
            when {
                r.method == "POST" && path == "/api/v1/agent/chat" -> sse(run.take(6))
                path.startsWith("/api/v1/agent/chat/c9/stream") -> {
                    follows++
                    // Waits of 1, 2, 3, 5 and 8 s before the sixth try (19 s), 13 s more before the seventh.
                    if (follows <= 6) down
                    else sse(run.filter { seqOf(it.second) > r.requestUrl!!.queryParameter("after")!!.toInt() })
                }
                else -> null
            }
        }
        val g = graphs.graph(app)
        val vm = graphs.vm { AssistantViewModel(g, SavedStateHandle()) }
        detail = { vm.state.value }
        var reconnectingUntil = 0
        vm.send("Tidy up Northwind")
        until("finished") {
            if (vm.state.value.turns.lastOrNull()?.reconnecting == true) reconnectingUntil = follows
            !vm.state.value.busy && vm.state.value.turns.getOrNull(1)?.finishedAt != null
        }
        val turn = vm.state.value.turns[1]
        assertNull(turn.error)
        assertFalse(turn.lost)
        assertEquals(tokens.joinToString(""), turn.answer)
        assertEquals(7, follows)
        // "Reconnecting…" showed through the outage.
        assertTrue(reconnectingUntil >= 6)
        assertFalse(AssistantViewModel.canRetry(vm.state.value.turns))
    }

    /** Away past the minute: "Connection lost" and Retry; Retry re-runs the stored message and the bubble shows once. */
    @Test fun pastTheWindowRetryRerunsTheTurnOnce() {
        var away = true
        val replay = """{"id":"c9","title":"Tidy Northwind","turns":[
         {"role":"user","id":3,"content":"Tidy up Northwind","citations":[],"steps":[]},
         {"role":"assistant","user_message_id":3,"content":"","status":"interrupted","citations":[],"steps":[],"timeline":[]}],
         "running":false,"pending_approvals":[]}"""
        serve { r ->
            val path = r.path.orEmpty()
            when {
                r.method == "POST" && path == "/api/v1/agent/chat" ->
                    if (bodies.size == 1) sse(run.take(6)) else sse(run)
                away -> down
                path == "/api/v1/agent/sessions/c9" -> MockResponse().setBody(replay)
                else -> null
            }
        }
        val g = graphs.graph(app)
        val vm = graphs.vm { AssistantViewModel(g, SavedStateHandle()) }
        detail = { vm.state.value }
        vm.send("Tidy up Northwind")
        until("lost") { !vm.state.value.busy && vm.state.value.turns.getOrNull(1)?.lost == true }
        assertEquals("Connection lost.", vm.state.value.turns[1].error)
        assertTrue(AssistantViewModel.canRetry(vm.state.value.turns))
        // Every wait was used: eight waits, nine tries.
        assertEquals(9, requests.count { it.startsWith("GET /api/v1/agent/chat/c9/stream") })

        // Retry while still offline: nothing is sent, and the turn keeps its Retry.
        vm.retry()
        until("still lost") { !vm.state.value.busy }
        assertEquals(1, bodies.size)
        assertTrue(AssistantViewModel.canRetry(vm.state.value.turns))

        away = false
        vm.retry()
        until("answered") { !vm.state.value.busy && vm.state.value.turns.getOrNull(1)?.finishedAt != null &&
            vm.state.value.turns[1].answer.isNotEmpty() }
        val turns = vm.state.value.turns
        assertEquals(listOf("user", "assistant"), turns.map { it.role })
        assertEquals("Tidy up Northwind", turns[0].text)
        assertEquals(tokens.joinToString(""), turns[1].answer)
        assertNull(turns[1].error)
        assertFalse(AssistantViewModel.canRetry(turns))
        // The web's rerun: the stored message runs again; its words are not sent a second time.
        val again = bodies.last()
        assertTrue(again, again.contains("\"rerun_from\":3"))
        assertFalse(again, again.contains("\"message\""))
        assertTrue(again, again.contains("\"session_id\":\"c9\""))
    }

    /** The server went before it stored anything (no chat, no message): Retry sends the same words again, with one bubble. */
    @Test fun retryResendsWhenTheMessageWasNotStored() {
        serve { r ->
            val path = r.path.orEmpty()
            when {
                // The first stream ends before the chat is named; the second answers.
                r.method == "POST" && path == "/api/v1/agent/chat" -> if (bodies.size == 1) sse(emptyList()) else sse(run)
                else -> null  // no chat of this time in the list (404)
            }
        }
        val g = graphs.graph(app)
        val vm = graphs.vm { AssistantViewModel(g, SavedStateHandle()) }
        detail = { vm.state.value }
        vm.send("Tidy up Northwind")
        until("lost") { !vm.state.value.busy && vm.state.value.turns.getOrNull(1)?.lost == true }
        assertNull(vm.state.value.sessionId)
        vm.retry()
        until("answered") { !vm.state.value.busy && vm.state.value.turns.getOrNull(1)?.answer?.isNotEmpty() == true }
        assertEquals(listOf("user", "assistant"), vm.state.value.turns.map { it.role })
        assertEquals(2, bodies.size)
        val body = bodies.last()
        assertTrue(body, body.contains("\"message\":\"Tidy up Northwind\""))
        assertFalse(body, body.contains("rerun_from"))
    }

    @Test fun retryShowsOnTheLastLostTurnOnly() {
        var retried = 0
        var s by androidx.compose.runtime.mutableStateOf(AssistantState(turns = listOf(
            ChatTurn("user", "First"), ChatTurn("assistant", lost = true, error = "Connection lost."),
            ChatTurn("user", "Move those items"), ChatTurn("assistant", lost = true, error = "Connection lost.")), sessionId = "c9"))
        compose.setContent {
            Surface(Modifier.fillMaxSize()) {
                AssistantScreen(s, AssistantCallbacks({}, {}, {}, {}, {}, { _, _ -> }, { _, _, _ -> }, {}, retry = { retried++ }))
            }
        }
        compose.onAllNodesWithTag("turn-retry").fetchSemanticsNodes().let { assertEquals(1, it.size) }
        compose.onNodeWithContentDescription("Retry: ask the same question again").performClick()
        assertEquals(1, retried)
        // Used: the turn runs again and the button goes.
        s = s.copy(turns = s.turns.dropLast(1) + ChatTurn("assistant", streaming = true), busy = true)
        compose.onAllNodesWithTag("turn-retry").fetchSemanticsNodes().let { assertTrue(it.isEmpty()) }
        // An interrupted turn (a reopened chat) offers it too.
        s = AssistantState(turns = listOf(ChatTurn("user", "First", messageId = 3), ChatTurn("assistant", interrupted = true)), sessionId = "c9")
        compose.onNodeWithTag("turn-retry").assertIsDisplayed()
    }
}
