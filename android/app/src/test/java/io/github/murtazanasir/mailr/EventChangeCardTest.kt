// ACT-SURFACES: android.reader
package io.github.murtazanasir.mailr

import android.app.Application
import android.os.Looper
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHostState
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assert
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.getUnclippedBoundsInRoot
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.unit.LayoutDirection
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.ConversationDetail
import io.github.murtazanasir.mailr.data.api.EventChange
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.answerProposal
import io.github.murtazanasir.mailr.data.api.Message
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.ui.conversation.ConversationActions
import io.github.murtazanasir.mailr.ui.conversation.ConversationScreen
import io.github.murtazanasir.mailr.ui.conversation.ConversationViewModel
import io.github.murtazanasir.mailr.ui.conversation.InviteCallbacks
import io.github.murtazanasir.mailr.ui.conversation.ReaderExtrasUi
import io.github.murtazanasir.mailr.ui.mail.UiMessage
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
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
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArrayList

/**
 * event.change_suggestion on Android (the web's EventChangeCard): a move an email announces in words
 * ("Could we move our Ops review to October 28?") shows one card in the reader with the event, its
 * time now and the sentence; Move changes the event and offers Undo, Not now closes the suggestion.
 * TalkBack hears what the card is; right to left, the quote's line sits on the start (right) side.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class EventChangeCardTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()

    private val app: Application = ApplicationProvider.getApplicationContext()
    private var server: MockWebServer? = null
    private val requests = CopyOnWriteArrayList<RecordedRequest>()
    private val bodies = ConcurrentHashMap<RecordedRequest, String>()

    @After fun tearDown() {
        idle()
        graphs.close()
        server?.shutdown()
    }

    private fun idle() = shadowOf(Looper.getMainLooper()).idle()

    private fun until(what: String, cond: () -> Boolean) =
        eventually(what, detail = { "requests: ${requests.map { "${it.method} ${it.path}" }}" }, check = cond)

    private val change = EventChange(uid = "ops@example.com", summary = "Ops Review",
        oldStart = "2026-10-21T18:00:00Z", oldEnd = "2026-10-21T20:00:00Z",
        newStart = "2026-10-28T18:00:00Z", newEnd = "2026-10-28T20:00:00Z",
        sentence = "Could we move our Ops review to October 28?")
    private val msg: Message = Fixtures.detail.messages[1].copy(id = 778, eventChange = change)
    private val detail = ConversationDetail(Fixtures.detail.conversation, listOf(Fixtures.detail.messages[0], msg))

    private var extras by mutableStateOf(ReaderExtrasUi())
    private var rtl by mutableStateOf(false)
    private var shown by mutableStateOf(detail)

    /** The reader with the card; later changes to [extras], [rtl] and [shown] recompose it. */
    private fun screen(cb: InviteCallbacks = InviteCallbacks()) {
        val actions = ConversationActions(
            back = null, act = {}, toggle = {}, expandAll = {}, star = { _, _ -> }, reply = { _, _ -> },
            openAttachment = { _, _ -> }, showImages = {}, snooze = {}, labels = {}, summarize = {}, unsubscribe = {},
            mailto = {}, retry = {}, invite = cb)
        compose.setContent {
            CompositionLocalProvider(LocalLayoutDirection provides if (rtl) LayoutDirection.Rtl else LayoutDirection.Ltr) {
                MaterialTheme {
                    ConversationScreen(shown, null, Fixtures.labels, setOf(778L), emptyMap(), null, null, tabletop = false,
                        snackbar = SnackbarHostState(), actions = actions, extras = extras)
                }
            }
        }
    }

    /** The server's field (backend api/schemas.py): present only while the suggestion is open. */
    @Test fun parsesTheServersField() {
        val m = MailrJson.decodeFromString(Message.serializer(), """{"id":5,"event_change":{"uid":"ops@example.com","summary":"Ops Review",
            "old_start":"2026-10-21T18:00:00Z","old_end":null,"new_start":"2026-10-28T18:00:00Z","new_end":"2026-10-28T20:00:00Z",
            "sentence":"Could we move our Ops review to October 28?"}}""")
        assertEquals("ops@example.com", m.eventChange!!.uid)
        assertNull(m.eventChange!!.oldEnd)
        assertEquals("Could we move our Ops review to October 28?", m.eventChange!!.sentence)
        assertNull(MailrJson.decodeFromString(Message.serializer(), """{"id":6}""").eventChange)
    }

    /** ACT event.change_suggestion: the card's words, Move and Not now; TalkBack hears "Suggested move" and a heading. */
    @Test fun cardShowsTheMoveAndAnswers() {
        val picked = mutableListOf<String>()
        screen(InviteCallbacks(moveSuggested = { m, c -> picked += "move:${m.id}:${c.uid}" },
            dismissSuggested = { m -> picked += "dismiss:${m.id}" }))
        compose.onNodeWithTag("event-change").assertIsDisplayed()
        compose.onNodeWithText("Move “Ops Review” to ", substring = true)
            .assert(SemanticsMatcher.keyIsDefined(SemanticsProperties.Heading))
        compose.onNodeWithText("Now: ", substring = true).assertExists()
        compose.onNodeWithText("Could we move our Ops review to October 28?").assertExists()
        compose.onNodeWithContentDescription("Suggested move").assertExists()
        compose.onNodeWithTag("event-change-move").performClick()
        compose.onNodeWithTag("event-change-dismiss").performClick()
        assertEquals(listOf("move:778:ops@example.com", "dismiss:778"), picked)
    }

    /** Taken or put aside: the card goes; on its way: the buttons wait; my own message: no card. */
    @Test fun cardGoesWhenDoneAndWaitsWhenBusy() {
        extras = ReaderExtrasUi(changesBusy = setOf(778L))
        screen()
        compose.onNodeWithTag("event-change-move").assertIsNotEnabled()
        compose.onNodeWithTag("event-change-dismiss").assertIsNotEnabled()
        extras = ReaderExtrasUi(changesDone = setOf(778L))
        compose.onNodeWithTag("event-change").assertDoesNotExist()
        extras = ReaderExtrasUi()
        compose.onNodeWithTag("event-change").assertExists()
        shown = detail.copy(messages = listOf(Fixtures.detail.messages[0], msg.copy(sentByMe = true)))
        compose.onNodeWithTag("event-change").assertDoesNotExist()
    }

    /** Right to left: the quote's line is on the start side, the right of the sentence. */
    @Test fun quoteLineFollowsRightToLeft() {
        screen()
        for (r in listOf(false, true)) {
            rtl = r
            compose.waitForIdle()
            val line = compose.onNodeWithTag("event-change-quote-line", useUnmergedTree = true).getUnclippedBoundsInRoot()
            val text = compose.onNodeWithText("Could we move our Ops review to October 28?", useUnmergedTree = true).getUnclippedBoundsInRoot()
            if (r) assertTrue("the line is right of the sentence", line.left >= text.right)
            else assertTrue("the line is left of the sentence", line.right <= text.left)
        }
    }

    /** Move patches the event to the new time, then closes the suggestion as applied; Undo puts the old time back. Not now closes it as dismissed. */
    @Test fun viewModelMovesUndoesAndDismisses() {
        val s = MockWebServer()
        s.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                bodies[request] = request.body.readUtf8()
                requests += request
                val path = request.path.orEmpty()
                return when {
                    path == "/api/v1/conversations/3066" -> MockResponse().setBody(MailrJson.encodeToString(ConversationDetail.serializer(), detail))
                    java.net.URLDecoder.decode(path, "UTF-8") == "/api/v1/calendar/events/ops@example.com" && request.method == "PATCH" ->
                        MockResponse().setBody("""{"uid":"ops@example.com"}""")
                    path == "/api/v1/messages/778/event-change" -> MockResponse().setBody("""{"ok":true}""")
                    path == "/api/v1/messages/779/event-change" -> MockResponse().setBody("""{"ok":true}""")
                    path.startsWith("/api/v1/outbox") -> MockResponse().setBody("[]")
                    else -> MockResponse().setResponseCode(404).setBody("""{"detail":"not here"}""")
                }
            }
        }
        s.start()
        server = s
        SessionStore(app).apply { server = s.url("/").toString(); save("tok", "csrf", "me@example.com", "Me") }
        val g = graphs.graph(app)
        val vm = graphs.vm { ConversationViewModel(g, 3066, null, null) }
        val said = CopyOnWriteArrayList<UiMessage>()
        val listen = CoroutineScope(Dispatchers.Unconfined)
        listen.launch { vm.messages.collect { said += it } }
        try {
            vm.extras.moveSuggested(778, change)
            until("moved") { 778L in vm.extras.changesDone.value && said.isNotEmpty() }
            val patches = requests.filter { it.method == "PATCH" }
            assertEquals(1, patches.size)
            val moved = MailrJson.parseToJsonElement(bodies[patches[0]]!!).jsonObject
            assertEquals("2026-10-28T18:00:00Z", moved["start"]!!.jsonPrimitive.content)
            assertEquals("2026-10-28T20:00:00Z", moved["end"]!!.jsonPrimitive.content)
            val close = requests.first { it.path == "/api/v1/messages/778/event-change" }
            assertTrue("the event moves before the suggestion closes", requests.indexOf(close) > requests.indexOf(patches[0]))
            assertEquals("applied", MailrJson.parseToJsonElement(bodies[close]!!).jsonObject["status"]!!.jsonPrimitive.content)
            val toast = said.first()
            assertEquals("Moved “Ops Review”.", toast.text)
            assertEquals("Undo", toast.actionLabel)
            // A second tap while it is done does nothing.
            vm.extras.moveSuggested(778, change)
            idle()
            // Off the main thread: the test thread is the main looper, which until() keeps turning.
            listen.launch(Dispatchers.IO) { toast.action!!.invoke() }
            until("undone") { requests.count { it.method == "PATCH" } == 2 }
            val back = MailrJson.parseToJsonElement(bodies[requests.last { it.method == "PATCH" }]!!).jsonObject
            assertEquals("2026-10-21T18:00:00Z", back["start"]!!.jsonPrimitive.content)
            assertEquals("2026-10-21T20:00:00Z", back["end"]!!.jsonPrimitive.content)
            // Not now: only the suggestion closes.
            vm.extras.dismissSuggested(779)
            until("dismissed") { 779L in vm.extras.changesDone.value }
            val dismissed = requests.first { it.path == "/api/v1/messages/779/event-change" }
            assertEquals("dismissed", MailrJson.parseToJsonElement(bodies[dismissed]!!).jsonObject["status"]!!.jsonPrimitive.content)
            assertEquals(2, requests.count { it.method == "PATCH" })
            assertFalse(778L in vm.extras.changesBusy.value)
            // The UID reaches the server once encoded ("@", not "%2540"), here and on a guest's proposal.
            assertTrue(requests.filter { it.method == "PATCH" }.all { "%25" !in it.path.orEmpty() })
            listen.launch(Dispatchers.IO) { runCatching { g.api.answerProposal("ops@example.com", 778, true) } }
            until("proposal") { requests.any { it.path.orEmpty().endsWith("/counter/accept") } }
            val counter = requests.last { it.path.orEmpty().endsWith("/counter/accept") }
            assertEquals("/api/v1/calendar/events/ops@example.com/counter/accept", java.net.URLDecoder.decode(counter.path!!, "UTF-8"))
            assertFalse("%25" in counter.path!!)
        } finally {
            listen.cancel()
        }
    }
}
