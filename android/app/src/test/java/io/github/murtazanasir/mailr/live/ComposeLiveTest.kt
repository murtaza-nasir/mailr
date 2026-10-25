// ACT-SURFACES: android.editor
// Live editing in the open editors: draft.live_update, draft.resolve_conflict, event.live_update,
// event.resolve_conflict, task.live_update, task.resolve_conflict.
package io.github.murtazanasir.mailr.live

import android.app.Application
import android.os.Looper
import androidx.lifecycle.SavedStateHandle
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.sync.ServerEvent
import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.ui.compose.ComposeArgs
import io.github.murtazanasir.mailr.ui.compose.ComposeViewModel
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import io.github.murtazanasir.mailr.TestGraph
import org.junit.Rule
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.util.concurrent.CopyOnWriteArrayList

/**
 * The composer under live editing: saves carry the base version and the install's client id, a
 * merged save rebases the editor, a 409 shows the conflict bar, a change saved elsewhere comes
 * into a clean composer (with Undo) and merges into a dirty one.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class ComposeLiveTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    private lateinit var server: MockWebServer
    private lateinit var g: AppGraph
    private val puts = CopyOnWriteArrayList<RecordedRequestCopy>()

    data class RecordedRequestCopy(val body: String, val client: String?)

    private val me = """{"user":{"id":1,"email":"me@example.com","name":"Me"},
        "identities":[{"id":7,"email":"me@example.com","name":"Me","is_default":true}]}"""

    private val stored = """{"identity_id":7,"to":[{"email":"dana@example.org"}],"subject":"Plan",
        "html":"<p>Hi Dana,</p><p>See you Thursday.</p>"}"""

    /** What PUT /drafts/42 answers. */
    @Volatile private var putAnswer: MockResponse = MockResponse().setBody("""{"draft":{"id":42,"draft":true},"version":2,"merged":false}""")
    @Volatile private var liveAnswer: String = """{"kind":"draft","id":"42","version":1,"content":$stored}"""

    @Before fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.path.orEmpty()
                return when {
                    path == "/api/v1/me" -> MockResponse().setBody(me)
                    path == "/api/v1/drafts/42" && request.method == "GET" ->
                        MockResponse().setBody("""{"draft":{"id":42,"draft":true},"payload":$stored,"version":1}""")
                    path == "/api/v1/drafts/42" && request.method == "PUT" -> {
                        puts += RecordedRequestCopy(request.body.readUtf8(), request.getHeader("X-Mailr-Client"))
                        putAnswer
                    }
                    path == "/api/v1/live/draft/42" -> MockResponse().setBody(liveAnswer)
                    path == "/api/v1/live/typing" -> MockResponse().setBody("""{"ok":true}""")
                    else -> MockResponse().setResponseCode(404).setBody("""{"detail":"not here"}""")
                }
            }
        }
        server.start()
        val ctx = ApplicationProvider.getApplicationContext<Application>()
        SessionStore(ctx).apply {
            server = this@ComposeLiveTest.server.url("/").toString()
            save("tok-123", "csrf-456", "me@example.com", "Me")
        }
        g = graphs.graph(ctx)
    }

    @After fun tearDown() {
        idle()
        graphs.close()
        server.shutdown()
    }

    private fun idle() = shadowOf(Looper.getMainLooper()).idle()

    private fun composer(): ComposeViewModel {
        val vm = graphs.vm { ComposeViewModel(g, ComposeArgs(draftId = 42), SavedStateHandle(), owner = "entry-1") }
        eventually("composer loaded") { !vm.state.value.loading && vm.live.baseVersion != null }
        return vm
    }

    private fun item(version: Int, by: String = "assistant", origin: String = "web-tab") = ServerEvent("item",
        Json.parseToJsonElement("""{"type":"item","kind":"draft","id":42,"version":$version,"by":"$by","origin":"$origin"}""").jsonObject)

    private fun html(vm: ComposeViewModel) = vm.payload().html.orEmpty()

    @Test fun aSaveCarriesItsBaseAndAMergedAnswerRebases() {
        val vm = composer()
        assertEquals(1, vm.live.baseVersion)
        putAnswer = MockResponse().setBody("""{"draft":{"id":42,"draft":true},"version":3,"merged":true,
            "payload":{"identity_id":7,"to":[{"email":"dana@example.org"}],"subject":"Plan v2",
            "html":"<p>Hi Dana,</p><p>See you Thursday.</p><p>Added by the assistant.</p>"}}""")
        vm.setSubject("Plan v2")
        vm.close()
        eventually("saved") { puts.isNotEmpty() && vm.live.baseVersion == 3 }
        val sent = Json.parseToJsonElement(puts.single().body).jsonObject
        assertEquals(1, sent["base_version"]!!.jsonPrimitive.content.toInt())
        assertEquals(g.client.clientId, puts.single().client)
        assertTrue(g.client.clientId.startsWith("android-"))
        // The editor now holds what the server stored.
        assertEquals("Plan v2", vm.state.value.subject)
        assertTrue(html(vm), "Added by the assistant." in html(vm))
    }

    @Test fun aRefusedSaveShowsTheBarAndUseMineSavesFromTheirVersion() {
        val vm = composer()
        putAnswer = MockResponse().setResponseCode(409).setBody("""{"error":"conflict","conflict":true,"kind":"draft","id":42,
            "detail":"This draft was changed by the assistant while you edited it; both changed subject.","fields":["subject"],
            "base":{"version":1,"content":$stored},
            "theirs":{"version":4,"content":{"identity_id":7,"to":[{"email":"dana@example.org"}],"subject":"Plan B",
                "html":"<p>Hi Dana,</p><p>See you Thursday.</p>"},"by":"assistant"},
            "mine":{"content":{}}}""")
        vm.setSubject("Plan A")
        vm.close()
        eventually("conflict bar") { vm.liveUi.value.conflict != null }
        val c = vm.liveUi.value.conflict!!
        assertEquals(4, c.version)
        assertEquals(listOf("subject"), c.fields)
        assertEquals("Plan A", vm.state.value.subject)
        assertEquals("Not saved", vm.state.value.saveStatus)
        putAnswer = MockResponse().setBody("""{"draft":{"id":42,"draft":true},"version":5,"merged":false}""")
        vm.useMine()
        eventually("saved over theirs") { vm.live.baseVersion == 5 }
        assertEquals(4, Json.parseToJsonElement(puts.last().body).jsonObject["base_version"]!!.jsonPrimitive.content.toInt())
        assertNull(vm.liveUi.value.conflict)
    }

    @Test fun anotherWriterUpdatesACleanComposerWithUndo() {
        val vm = composer()
        liveAnswer = """{"kind":"draft","id":"42","version":2,"by":"assistant","content":{"identity_id":7,
            "to":[{"email":"dana@example.org"}],"subject":"Plan","html":"<p>Hi Dana,</p><p>See you on Friday.</p>"}}"""
        runBlocking { vm.live.onEvent(item(2)) }
        eventually("updated") { "Friday" in html(vm) }
        assertEquals(2, vm.live.baseVersion)
        assertNotNull(vm.liveUi.value.notice?.previous)
        assertTrue(puts.isEmpty())
        // Undo puts Thursday back and saves it as a new version, from version 2.
        putAnswer = MockResponse().setBody("""{"draft":{"id":42,"draft":true},"version":3,"merged":false}""")
        vm.undoLive()
        eventually("undo saved") { vm.live.baseVersion == 3 }
        assertTrue("Thursday" in html(vm))
        assertEquals(2, Json.parseToJsonElement(puts.single().body).jsonObject["base_version"]!!.jsonPrimitive.content.toInt())
    }

    @Test fun ownWritesAreIgnoredAndADirtyComposerMerges() {
        val vm = composer()
        runBlocking { vm.live.onEvent(item(2, by = "device", origin = g.client.clientId)) }
        idle()
        assertEquals(1, vm.live.baseVersion)
        vm.setSubject("Plan for Thursday")
        liveAnswer = """{"kind":"draft","id":"42","version":2,"by":"assistant","content":{"identity_id":7,
            "to":[{"email":"dana@example.org"}],"subject":"Plan","html":"<p>Hi Dana,</p><p>See you Thursday.</p><p>Best, Me</p>"}}"""
        runBlocking { vm.live.onEvent(item(2)) }
        eventually("merged") { "Best, Me" in html(vm) }
        assertEquals("Plan for Thursday", vm.state.value.subject)
        assertEquals(2, vm.live.baseVersion)
        assertNull(vm.liveUi.value.conflict)
        // Still unsaved: Back saves it, from version 2.
        putAnswer = MockResponse().setBody("""{"draft":{"id":42,"draft":true},"version":3,"merged":false}""")
        vm.close()
        eventually("saved") { puts.isNotEmpty() }
        val sent = Json.parseToJsonElement(puts.single().body).jsonObject
        assertEquals(2, sent["base_version"]!!.jsonPrimitive.content.toInt())
        assertFalse(vm.state.value.saveStatus == "Not saved")
    }
}
