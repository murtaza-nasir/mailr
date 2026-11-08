// ACT-SURFACES: android.assistant android.share
package io.github.murtazanasir.mailr.ui.assistant

import android.app.Application
import android.os.Looper
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.TestGraph
import io.github.murtazanasir.mailr.data.agent.AgentClient
import io.github.murtazanasir.mailr.data.agent.ChatFile
import io.github.murtazanasir.mailr.data.api.ApiException
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.testing.until
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonObject
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/**
 * agent.chat_attach: files and images attached to a message to the assistant. A picked file uploads at
 * once (POST /agent/attachments) and waits above the message box; Send carries the ids
 * (`attachments`), with or without words; a file the server refuses goes, with the server's reason; ✕
 * deletes an uploaded one. A reopened chat shows each message's files. A share hands its uploads to a
 * new chat, waiting for Send (ui/share/ShareAskActivity.kt).
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class ChatAttachTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()

    private val app: Application = ApplicationProvider.getApplicationContext()
    private var server: MockWebServer? = null
    private val requests: MutableList<String> = java.util.concurrent.CopyOnWriteArrayList()

    @After fun tearDown() {
        shadowOf(Looper.getMainLooper()).idle()
        graphs.close()
        server?.shutdown()
    }

    private fun idle() = shadowOf(Looper.getMainLooper()).idleFor(java.time.Duration.ofMillis(10))
    private fun wait(what: String, cond: () -> Boolean) = eventually(what, idle = ::idle, detail = { "requests=$requests" }, check = cond)

    private fun serve() {
        val s = MockWebServer()
        s.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val body = request.body.readUtf8()
                requests += "${request.method} ${request.path} $body"
                return when {
                    request.method == "POST" && request.path == "/api/v1/agent/attachments" -> when {
                        "broken.docx" in body -> MockResponse().setResponseCode(422)
                            .setBody("""{"detail":"the file could not be read: the file is damaged or not a document","code":"assistant.attachment_unreadable"}""")
                        "photo.jpg" in body -> MockResponse().setBody(
                            """{"id":"a1","name":"photo.jpg","type":"image/jpeg","size":2048,"kind":"image","thumb":true,"width":1200,"height":900}""")
                        else -> MockResponse().setBody("""{"id":"a2","name":"plan.docx","type":"application/octet-stream","size":4096,"kind":"file","thumb":false,"format":"docx"}""")
                    }
                    request.method == "DELETE" && request.path!!.startsWith("/api/v1/agent/attachments/") -> MockResponse().setBody("""{"ok":true}""")
                    request.method == "POST" && request.path == "/api/v1/agent/chat" -> MockResponse().setHeader("Content-Type", "text/event-stream")
                        .setBody("event: session\ndata: {\"session_id\":\"c1\",\"title\":\"Photo\"}\n\n" +
                            "event: final\ndata: {\"content\":\"A red card.\",\"citations\":[]}\n\n" +
                            "event: done\ndata: {\"type\":\"done\"}\n\n")
                    else -> MockResponse().setResponseCode(404).setBody("""{"detail":"not here"}""")
                }
            }
        }
        s.start()
        server = s
        SessionStore(app).apply { server = s.url("/").toString(); save("tok", "csrf", "me@example.com", "Me") }
    }

    @Test fun uploadThenSendCarriesTheIds() {
        serve()
        val g = graphs.graph(app)
        val vm = graphs.vm { AssistantViewModel(g) }
        vm.upload("photo.jpg") { g.agent.uploadAttachment("photo.jpg", "image/jpeg", ByteArray(2048)) }
        vm.upload("plan.docx") { g.agent.uploadAttachment("plan.docx", "application/octet-stream", ByteArray(4096)) }
        wait("both files ready") { vm.state.value.pending.size == 2 && !vm.state.value.uploading }
        assertTrue(requests.any { it.startsWith("POST /api/v1/agent/attachments") && "filename=\"photo.jpg\"" in it })
        vm.send("What is on these?")
        wait("the answer") { vm.state.value.turns.lastOrNull()?.answer == "A red card." }
        val chat = requests.first { it.startsWith("POST /api/v1/agent/chat") }.substringAfter("/api/v1/agent/chat ")
        val o = MailrJson.parseToJsonElement(chat) as JsonObject
        assertEquals("""["a1","a2"]""", o["attachments"].toString())
        assertEquals("\"What is on these?\"", o["message"].toString())
        val user = vm.state.value.turns.first { it.role == "user" }
        assertEquals(listOf("photo.jpg", "plan.docx"), user.attachments.map { it.name })
        assertTrue(vm.state.value.pending.isEmpty())
    }

    @Test fun filesAloneMaySendAndARefusalSaysWhy() {
        serve()
        val g = graphs.graph(app)
        val vm = graphs.vm { AssistantViewModel(g) }
        vm.send("")  // nothing to send yet
        assertFalse(requests.any { it.startsWith("POST /api/v1/agent/chat") })
        val notices = java.util.concurrent.CopyOnWriteArrayList<String>()
        val job = g.scope.launchCollect(vm, notices)
        vm.upload("broken.docx") { g.agent.uploadAttachment("broken.docx", "application/octet-stream", ByteArray(10)) }
        wait("the refusal") { notices.any { "damaged or not a document" in it } }
        assertTrue(vm.state.value.pending.isEmpty())
        vm.upload("photo.jpg") { g.agent.uploadAttachment("photo.jpg", "image/jpeg", ByteArray(2048)) }
        wait("ready") { vm.state.value.readyFiles.size == 1 }
        vm.send("")
        wait("the answer") { vm.state.value.turns.lastOrNull()?.answer == "A red card." }
        val o = MailrJson.parseToJsonElement(requests.first { it.startsWith("POST /api/v1/agent/chat") }
            .substringAfter("/api/v1/agent/chat ")) as JsonObject
        assertEquals("\"\"", o["message"].toString())
        assertEquals("""["a1"]""", o["attachments"].toString())
        job.cancel()
    }

    @Test fun removingAnUploadedFileDeletesIt() {
        serve()
        val g = graphs.graph(app)
        val vm = graphs.vm { AssistantViewModel(g) }
        vm.upload("photo.jpg") { g.agent.uploadAttachment("photo.jpg", "image/jpeg", ByteArray(2048)) }
        wait("ready") { vm.state.value.readyFiles.size == 1 }
        vm.removeAttachment(vm.state.value.pending.single().key)
        assertTrue(vm.state.value.pending.isEmpty())
        wait("deleted at the server") { requests.any { it.startsWith("DELETE /api/v1/agent/attachments/a1") } }
    }

    @Test fun tooLargeIsRefusedBeforeUpload() {
        serve()
        val g = graphs.graph(app)
        try {
            runBlocking { g.agent.uploadAttachment("big.pdf", "application/pdf", ByteArray((AgentClient.MAX_FILE_BYTES + 1).toInt())) }
            fail("a file over the limit uploaded")
        } catch (e: ApiException) {
            assertEquals(413, e.code)
            assertTrue(e.detail, "big.pdf" in e.detail && "15 MB" in e.detail)
        }
        assertFalse(requests.any { "agent/attachments" in it })
    }

    @Test fun aShareWaitsInANewChatForSend() {
        serve()
        val g = graphs.graph(app)
        val vm = graphs.vm { AssistantViewModel(g) }
        val f = ChatFile("a1", "photo.jpg", "image/jpeg", 2048, "image", thumb = true)
        vm.ask(null, "Look at this", listOf(f), sendNow = false)
        assertEquals(listOf(f), vm.state.value.readyFiles)
        assertEquals("Look at this", vm.composer.ui.value.text)
        assertTrue(vm.state.value.turns.isEmpty())
        assertFalse(requests.any { it.startsWith("POST /api/v1/agent/chat") })
    }

    @Test fun chipsAndHistoryShowTheFiles() {
        val session = """{"id":"c1","title":"Photo","turns":[
 {"role":"user","id":3,"content":"","citations":[],"steps":[],"attachments":[
   {"id":"a1","name":"photo.jpg","type":"image/jpeg","size":2048,"kind":"image","thumb":true,"width":1200,"height":900},
   {"id":"a2","name":"plan.docx","type":"application/octet-stream","size":4096,"kind":"file","thumb":false,"format":"docx"}]},
 {"role":"assistant","user_message_id":3,"content":"A red card.","status":"done","citations":[],"steps":[]}],"running":false,"pending_approvals":[]}"""
        val turns = SessionReplay.turns(MailrJson.parseToJsonElement(session) as JsonObject)
        assertEquals(listOf("photo.jpg", "plan.docx"), turns[0].attachments.map { it.name })
        var removed: String? = null
        var picked = 0
        val state = AssistantState(turns = turns, sessionId = "c1",
            pending = listOf(PendingFile("k1", "notes.txt", 120, ChatFile("a3", "notes.txt", "text/plain", 120)), PendingFile("k2", "scan.pdf")))
        compose.setContent {
            Surface(Modifier.fillMaxSize()) {
                AssistantScreen(state, AssistantCallbacks({}, {}, {}, {}, {}, { _, _ -> }, { _, _, _ -> }, {},
                    attach = { picked++ }, removeAttachment = { removed = it }, thumb = { null }))
            }
        }
        compose.until("two files in the history") { compose.onAllNodesWithTag("turn-attachment").fetchSemanticsNodes().size == 2 }
        compose.onNodeWithText("plan.docx").assertExists()
        assertEquals(2, compose.onAllNodesWithTag("pending-attachment").fetchSemanticsNodes().size)
        compose.onNodeWithText("Attaching…").assertExists()
        compose.onNodeWithContentDescription("Remove notes.txt").performClick()
        assertEquals("k1", removed)
        compose.onNodeWithTag("composer-attach").performClick()
        assertEquals(1, picked)
    }
}

/** The view model's notices, collected into [into] until the returned job is cancelled. */
private fun kotlinx.coroutines.CoroutineScope.launchCollect(vm: AssistantViewModel, into: MutableList<String>) =
    launch(kotlinx.coroutines.Dispatchers.Unconfined) { vm.notices.collect { into += it } }
