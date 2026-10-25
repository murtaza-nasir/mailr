package io.github.murtazanasir.mailr

import android.app.Application
import android.os.Looper
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHostState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.test.performTextReplacement
import androidx.lifecycle.SavedStateHandle
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.Address
import io.github.murtazanasir.mailr.data.api.ComposePayload
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.ui.compose.ComposeArgs
import io.github.murtazanasir.mailr.ui.compose.ComposeCallbacks
import io.github.murtazanasir.mailr.ui.compose.ComposeScreen
import io.github.murtazanasir.mailr.ui.compose.ComposeViewModel
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
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
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * The composer never loses typing to a copy that arrives late: the
 * fields are not offered until the stored draft or the reply
 * prefill has arrived ("Opening draft…", "Opening reply…"); a late GET /me fills only the From
 * line; a save answered for older text neither marks newer text saved nor drops it; and a copy
 * kept on this phone stays editable while the server's newer copy merges in under the typing.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class ComposeLateLoadTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()
    private lateinit var server: MockWebServer
    private lateinit var g: AppGraph
    private val seen = CopyOnWriteArrayList<String>()
    private val answered = CopyOnWriteArrayList<String>()
    private val puts = CopyOnWriteArrayList<String>()
    /** Requests held until released, by "METHOD path" prefix. */
    private val gates = ConcurrentHashMap<String, CountDownLatch>()

    private val me = """{"user":{"id":1,"email":"me@example.com","name":"Me"},
        "identities":[{"id":7,"email":"me@example.com","name":"Me","is_default":true}]}"""

    private val storedHtml = "<p>Hi Dana,</p><p>See you Thursday.</p>"
    private val stored = """{"identity_id":7,"to":[{"email":"dana@example.org"}],"subject":"Plan","html":"$storedHtml"}"""
    private val assistantHtml = "$storedHtml<p>Added by the assistant.</p>"
    private val quote = "<div class=\"mailr_quote\"><p>On Mon, Bob wrote:</p><blockquote>Q3 numbers</blockquote></div>"

    @Volatile private var putAnswer: MockResponse = MockResponse().setBody("""{"draft":{"id":42,"draft":true},"version":2,"merged":false}""")
    @Volatile private var liveAnswer: String = """{"kind":"draft","id":"42","version":1,"content":$stored}"""

    @Before fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.path.orEmpty()
                val line = "${request.method} $path"
                val body = request.body.readUtf8()
                seen += line
                if (request.method == "PUT" && path == "/api/v1/drafts/42") puts += body
                gates.entries.firstOrNull { line.startsWith(it.key) }?.value?.await(20, TimeUnit.SECONDS)
                answered += "$line@${System.currentTimeMillis() % 100000}"
                return when {
                    path == "/api/v1/me" -> MockResponse().setBody(me)
                    path == "/api/v1/drafts/42" && request.method == "GET" ->
                        MockResponse().setBody("""{"draft":{"id":42,"draft":true},"payload":$stored,"version":1}""")
                    path == "/api/v1/drafts/42" && request.method == "PUT" -> putAnswer
                    path.startsWith("/api/v1/compose/reply") -> MockResponse().setBody(
                        """{"payload":{"to":[{"email":"bob@example.com"}],"subject":"Re: Q3","in_reply_to_message_id":502,
                            "html":${JsonPrimitive("<div><br></div>$quote")}}}""")
                    path == "/api/v1/live/draft/42" -> MockResponse().setBody(liveAnswer)
                    path == "/api/v1/live/typing" -> MockResponse().setBody("""{"ok":true}""")
                    else -> MockResponse().setResponseCode(404).setBody("""{"detail":"not here"}""")
                }
            }
        }
        server.start()
        val ctx = ApplicationProvider.getApplicationContext<Application>()
        SessionStore(ctx).apply {
            server = this@ComposeLateLoadTest.server.url("/").toString()
            save("tok-123", "csrf-456", "me@example.com", "Me")
        }
        g = graphs.graph(ctx)
    }

    @After fun tearDown() {
        gates.values.forEach { it.countDown() }
        idle()
        graphs.close()
        server.shutdown()
    }

    private fun idle() = shadowOf(Looper.getMainLooper()).idle()

    /** The main looper's clock is Robolectric's: the autosave's delay runs only when it moves. */
    private fun advance(ms: Long) = shadowOf(Looper.getMainLooper()).idleFor(java.time.Duration.ofMillis(ms))

    private fun hold(prefix: String) { gates[prefix] = CountDownLatch(1) }
    private fun release(prefix: String) { gates.remove(prefix)?.countDown() }
    private fun requested(prefix: String) = seen.any { it.startsWith(prefix) }

    private fun settle(what: String, cond: () -> Boolean) = eventually(what, idle = {
        // The rule waits long for a screen that was never set: only screens tests ask it.
        if (shown) compose.waitForIdle()
        idle()
    }, detail = { "at ${System.currentTimeMillis() % 100000}; answered $answered; requests $seen; puts $puts" }, check = cond)

    private var shown = false

    private fun show(vm: ComposeViewModel) {
        shown = true
        compose.setContent { Host(vm) }
    }

    private fun exists(description: String) =
        compose.onAllNodes(androidx.compose.ui.test.hasContentDescription(description)).fetchSemanticsNodes().isNotEmpty()

    @Composable
    private fun Host(vm: ComposeViewModel) {
        val s by vm.state.collectAsState()
        MaterialTheme {
            ComposeScreen(s, remember { SnackbarHostState() }, ComposeCallbacks(
                close = vm::close, discard = vm::discard, send = { vm.send(it) }, pickSchedule = {}, attach = {},
                setIdentity = vm::setIdentity, setSubject = vm::setSubject, setBody = vm::setBody,
                addRecipient = vm::addRecipient, addPerson = vm::addPerson, removeRecipient = vm::removeRecipient,
                query = vm::query, toggleCc = vm::toggleCc, toggleQuoted = vm::toggleQuoted,
                removeAttachment = vm::removeAttachment, setText = vm::setText, setCursor = vm::setCursor,
                removeBlock = vm::removeBlock, editAsText = vm::editAsText,
            ))
        }
    }

    private fun html(vm: ComposeViewModel) = vm.payload().html.orEmpty()
    private fun sent(i: Int) = Json.parseToJsonElement(puts[i]).jsonObject

    /** A draft whose GET answers late shows "Opening draft…" and no fields until it arrives. */
    @Test fun aDraftThatLoadsLateOffersNoFieldsUntilItArrives() {
        hold("GET /api/v1/drafts/42")
        val vm = graphs.vm { ComposeViewModel(g, ComposeArgs(draftId = 42), SavedStateHandle()) }
        show(vm)
        settle("draft requested") { requested("GET /api/v1/drafts/42") }
        compose.onNodeWithText("Opening draft…").assertExists()
        compose.onNodeWithContentDescription("Subject").assertDoesNotExist()
        compose.onNodeWithContentDescription("Send").assertIsNotEnabled()
        release("GET /api/v1/drafts/42")
        settle("draft shown") { !vm.state.value.loading && exists("Subject") }
        compose.onNodeWithText("Opening draft…").assertDoesNotExist()
        assertEquals("Plan", vm.state.value.subject)
        compose.onNodeWithContentDescription("Subject").performTextReplacement("Plan for Thursday")
        settle("typed") { vm.state.value.subject == "Plan for Thursday" }
        assertTrue(html(vm), "See you Thursday." in html(vm))
        assertEquals(1, vm.live.baseVersion)
    }

    /** A late GET /me fills the From line and nothing else: what was typed meanwhile stays. */
    @Test fun aLateMeRefreshKeepsWhatWasTyped() {
        hold("GET /api/v1/me")
        val vm = graphs.vm { ComposeViewModel(g, ComposeArgs(), SavedStateHandle()) }
        show(vm)
        settle("fields offered") { exists("Subject") && requested("GET /api/v1/me") }
        compose.onNodeWithContentDescription("Subject").performTextInput("Lunch")
        compose.onNodeWithContentDescription("Compose email").performTextInput("Noon?")
        settle("typed") { vm.state.value.subject == "Lunch" && vm.state.value.body == "Noon?" }
        release("GET /api/v1/me")
        settle("identities in") { vm.state.value.identityId == 7L }
        compose.waitForIdle()
        assertEquals("Lunch", vm.state.value.subject)
        assertEquals("Noon?", vm.state.value.body)
        compose.onNodeWithText("Me <me@example.com>").assertExists()
    }

    /** A reply prefill that answers late: "Opening reply…" until it lands, then a late /me keeps the typing. */
    @Test fun aLateReplyPrefillAndALateMeKeepWhatWasTyped() {
        hold("GET /api/v1/me")
        hold("GET /api/v1/compose/reply")
        val vm = graphs.vm { ComposeViewModel(g, ComposeArgs(replyToMessageId = 502, mode = "reply"), SavedStateHandle()) }
        show(vm)
        settle("prefill requested") { requested("GET /api/v1/compose/reply") }
        compose.onNodeWithText("Opening reply…").assertExists()
        compose.onNodeWithContentDescription("Subject").assertDoesNotExist()
        release("GET /api/v1/compose/reply")
        settle("prefill shown") { !vm.state.value.loading && exists("Compose email") }
        assertEquals("Re: Q3", vm.state.value.subject)
        assertEquals(listOf("bob@example.com"), vm.state.value.to.map { it.email })
        compose.onNodeWithContentDescription("Compose email").performTextInput("Looks good.")
        settle("typed") { vm.state.value.body == "Looks good." }
        release("GET /api/v1/me")
        settle("identities in") { vm.state.value.identityId == 7L }
        assertEquals("Looks good.", vm.state.value.body)
        assertEquals("Re: Q3", vm.state.value.subject)
        assertTrue(html(vm), html(vm).startsWith("<p>Looks good.</p>"))
        assertTrue(html(vm), html(vm).endsWith(quote))
        assertEquals(502L, vm.payload().inReplyToMessageId)
    }

    /**
     * Typing on while a save is out: the save's answer (here merged with the assistant's line)
     * rebases the editor without dropping the newer text or calling it saved, and the next save
     * goes from the version that answer gave.
     */
    @Test fun aSaveAnsweredForOlderTextNeitherMarksNorReplacesNewerText() {
        val vm = graphs.vm { ComposeViewModel(g, ComposeArgs(draftId = 42), SavedStateHandle(), owner = "entry-late") }
        settle("composer loaded") { !vm.state.value.loading && vm.live.baseVersion == 1 }
        hold("PUT /api/v1/drafts/42")
        putAnswer = MockResponse().setBody("""{"draft":{"id":42,"draft":true},"version":2,"merged":true,
            "payload":{"identity_id":7,"to":[{"email":"dana@example.org"}],"subject":"Plan A","html":"$assistantHtml"}}""")
        vm.setSubject("Plan A")
        advance(ComposeViewModel.SAVE_DELAY_MS + 50)
        settle("first save out") { puts.size == 1 }
        assertEquals("Plan A", sent(0)["payload"]!!.jsonObject["subject"]!!.jsonPrimitive.content)
        // Typed while the save is out.
        vm.setSubject("Plan AB")
        release("PUT /api/v1/drafts/42")
        settle("first answer taken") { vm.live.baseVersion == 2 }
        assertEquals("Plan AB", vm.state.value.subject)
        assertTrue(html(vm), "Added by the assistant." in html(vm))
        assertNotEquals("Saved", vm.state.value.saveStatus)
        assertNotEquals("Not saved", vm.state.value.saveStatus)
        // The newer text saves next, from version 2.
        putAnswer = MockResponse().setBody("""{"draft":{"id":42,"draft":true},"version":3,"merged":false}""")
        advance(ComposeViewModel.SAVE_DELAY_MS + 50)
        settle("second save") { puts.size == 2 && vm.state.value.saveStatus == "Saved" }
        assertEquals(2, sent(1)["base_version"]!!.jsonPrimitive.content.toInt())
        val p = sent(1)["payload"]!!.jsonObject
        assertEquals("Plan AB", p["subject"]!!.jsonPrimitive.content)
        assertTrue("Added by the assistant." in p["html"]!!.jsonPrimitive.content)
        assertEquals(3, vm.live.baseVersion)
    }

    /**
     * A copy kept on this phone (already on the server) shows at once and stays editable; the
     * server's newer copy, read afterwards, merges in under what was typed instead of being
     * overwritten by it.
     */
    @Test fun aLocalCopyStaysEditableAndALateServerCopyMergesUnderTheTyping() {
        runBlocking {
            g.drafts.write("loc-1", ComposePayload(identityId = 7, to = listOf(Address("dana@example.org")), subject = "Plan", html = storedHtml), 42)
            g.db.localDrafts().synced("loc-1", 42, 1)
        }
        hold("GET /api/v1/live/draft/42")
        liveAnswer = """{"kind":"draft","id":"42","version":3,"by":"assistant","content":{"identity_id":7,
            "to":[{"email":"dana@example.org"}],"subject":"Plan","html":"$assistantHtml"}}"""
        val saved = SavedStateHandle(mapOf(ComposeViewModel.KEY_LOCAL to "loc-1"))
        val vm = graphs.vm { ComposeViewModel(g, ComposeArgs(draftId = 42), saved, owner = "entry-local") }
        settle("local copy shown") { !vm.state.value.loading && requested("GET /api/v1/live/draft/42") }
        assertEquals("Plan", vm.state.value.subject)
        vm.setSubject("Plan v2")
        release("GET /api/v1/live/draft/42")
        settle("server copy read") { vm.live.baseVersion == 3 }
        assertEquals("Plan v2", vm.state.value.subject)
        assertTrue(html(vm), "Added by the assistant." in html(vm))
        assertNull(vm.liveUi.value.conflict)
        putAnswer = MockResponse().setBody("""{"draft":{"id":42,"draft":true},"version":4,"merged":false}""")
        vm.close()
        settle("saved") { puts.isNotEmpty() }
        assertEquals(3, sent(0)["base_version"]!!.jsonPrimitive.content.toInt())
    }
}
