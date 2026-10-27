package io.github.murtazanasir.mailr

import android.app.Application
import android.os.Looper
import androidx.lifecycle.SavedStateHandle
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.ComposePayload
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.ui.compose.ComposeArgs
import io.github.murtazanasir.mailr.ui.compose.ComposeResult
import io.github.murtazanasir.mailr.ui.compose.ComposeViewModel
import io.github.murtazanasir.mailr.ui.compose.Field
import io.github.murtazanasir.mailr.ui.compose.RestoreStore
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okhttp3.mockwebserver.SocketPolicy
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Rule
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.util.concurrent.CopyOnWriteArrayList

/**
 * The composer never loses text (Android). Back offline keeps a local copy and says
 * "Saved on this phone. Waiting to sync."; "Saved to Drafts" only after the server saved; the copy
 * survives process death and syncs when the server is back; send offline keeps the text.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class ComposeOfflineTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    private lateinit var server: MockWebServer
    private lateinit var g: AppGraph
    private val requests = CopyOnWriteArrayList<Pair<String, String>>()
    private val draftKeys = CopyOnWriteArrayList<String>()
    @Volatile private var offline = false
    @Volatile private var draftStatus = 200
    private val sendKeys = CopyOnWriteArrayList<String>()
    /** The next sends reach the server, which drops the connection before it answers. */
    @Volatile private var loseSendAnswers = 0
    /** Every send reaches the server, which drops the connection before it answers, until cleared:
     *  OkHttp may retry a dropped request by itself (same key), and that retry must not get through. */
    @Volatile private var dropSendAnswers = false
    private var nextDraft = 900L

    private val me = """{"user":{"id":1,"email":"me@example.com","name":"Me"},
        "identities":[{"id":7,"email":"me@example.com","name":"Me","is_default":true}]}"""

    @Before fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun peek(): MockResponse =
                if (offline) MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AT_START) else super.peek()

            override fun dispatch(request: RecordedRequest): MockResponse {
                // A kept-alive connection reaches dispatch() even "offline": drop it after the request.
                if (offline) return MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AFTER_REQUEST)
                val path = request.path.orEmpty()
                requests += "${request.method} $path" to request.body.readUtf8()
                if (path.startsWith("/api/v1/drafts") && request.method != "DELETE") draftKeys += request.getHeader("Idempotency-Key").orEmpty()
                if (path == "/api/v1/send") {
                    sendKeys += request.getHeader("Idempotency-Key").orEmpty()
                    if (loseSendAnswers > 0) {
                        loseSendAnswers--
                        return MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AFTER_REQUEST)
                    }
                    if (dropSendAnswers) return MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AFTER_REQUEST)
                }
                return when {
                    path == "/api/v1/me" -> MockResponse().setBody(me)
                    path.startsWith("/api/v1/drafts") && request.method in setOf("POST", "PUT") ->
                        if (draftStatus != 200) MockResponse().setResponseCode(draftStatus).setBody("""{"detail":"no"}""")
                        else MockResponse().setBody("""{"draft":{"id":${nextDraft++},"draft":true}}""")
                    path.startsWith("/api/v1/drafts/") && request.method == "DELETE" -> MockResponse().setBody("{}")
                    path == "/api/v1/send" -> MockResponse().setBody("""{"outbox":{"id":55,"status":"pending"}}""")
                    else -> MockResponse().setResponseCode(404).setBody("""{"detail":"not here"}""")
                }
            }
        }
        server.start()
        val ctx = ApplicationProvider.getApplicationContext<Application>()
        SessionStore(ctx).apply {
            server = this@ComposeOfflineTest.server.url("/").toString()
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

    /** The main looper's clock is Robolectric's: delays on Main run only when it moves. */
    private fun advance(ms: Long) = shadowOf(Looper.getMainLooper()).idleFor(java.time.Duration.ofMillis(ms))

    private fun composer(args: ComposeArgs = ComposeArgs(), saved: SavedStateHandle = SavedStateHandle()): ComposeViewModel {
        val vm = graphs.vm { ComposeViewModel(g, args, saved) }
        eventually("composer loaded") { !vm.state.value.loading }
        return vm
    }

    private fun result(vm: ComposeViewModel, act: () -> Unit): ComposeResult {
        var r: ComposeResult? = null
        // Undispatched: the collector has subscribed to the SharedFlow when launch returns.
        val job = g.scope.launch(start = kotlinx.coroutines.CoroutineStart.UNDISPATCHED) { r = vm.done.first() }
        act()
        eventually("composer done") { r != null }
        job.cancel()
        return r!!
    }

    private fun localDrafts() = runBlocking { g.db.localDrafts().allNow() }
    private fun draftPosts() = requests.filter { it.first.startsWith("POST /api/v1/drafts") || it.first.startsWith("PUT /api/v1/drafts") }

    @Test fun backOfflineKeepsTheTextAndSaysItIsOnThisPhone() {
        val vm = composer()
        offline = true
        vm.addRecipient(Field.TO, "alice@example.com")
        vm.setSubject("Quarterly plan")
        vm.setBody("Friday at 10 works.")
        val r = result(vm) { vm.close() }
        assertEquals(ComposeResult.SavedLocally, r)
        assertEquals("Saved on this phone. Waiting to sync.", ComposeResult.message(r))
        val row = localDrafts().single()
        assertFalse(row.synced)
        assertTrue(row.closed)
        assertTrue(row.payload.contains("Friday at 10 works."))
        assertEquals("Saved on this phone", vm.state.value.saveStatus)
        // Back online: the worker's sync makes it a server draft, and the local copy goes.
        offline = false
        assertTrue(runBlocking { g.drafts.syncAll() })
        val post = draftPosts().single()
        assertTrue(post.second.contains("Friday at 10 works."))
        assertTrue(localDrafts().isEmpty())
    }

    @Test fun savedToDraftsOnlyWhenTheServerSaved() {
        val vm = composer()
        vm.setSubject("Lunch")
        vm.setBody("Noon?")
        val r = result(vm) { vm.close() }
        assertEquals(ComposeResult.SavedDraft, r)
        assertEquals("Saved to Drafts", ComposeResult.message(r))
        assertEquals("Saved", vm.state.value.saveStatus)
        assertTrue(localDrafts().isEmpty())
    }

    @Test fun aRefusedSaveIsNotReportedAsSaved() {
        draftStatus = 500
        val vm = composer()
        vm.setSubject("Lunch")
        vm.setBody("Noon?")
        assertEquals(ComposeResult.SavedLocally, result(vm) { vm.close() })
        assertEquals("Server error. Try again.", localDrafts().single().error)
    }

    @Test fun anEmptyComposerIsDiscarded() {
        val vm = composer()
        assertEquals(ComposeResult.Discarded, result(vm) { vm.close() })
        assertTrue(localDrafts().isEmpty())
    }

    @Test fun theTextSurvivesProcessDeath() {
        offline = true
        val saved = SavedStateHandle()
        val first = composer(saved = saved)
        first.setSubject("Draft after a crash")
        first.setBody("Half a thought")
        // The local copy follows the text within a moment, before any server save.
        advance(ComposeViewModel.LOCAL_DELAY_MS + 50)
        eventually("local copy written") { localDrafts().singleOrNull()?.payload?.contains("Half a thought") == true }
        // The process dies (no close, no onCleared); the nav entry's saved state comes back.
        val second = composer(saved = SavedStateHandle(mapOf(ComposeViewModel.KEY_LOCAL to saved.get<String>(ComposeViewModel.KEY_LOCAL))))
        assertEquals("Draft after a crash", second.state.value.subject)
        assertEquals("Half a thought", second.state.value.body)
        // Back still knows the server has none of it.
        assertEquals(ComposeResult.SavedLocally, result(second) { second.close() })
        assertEquals(1, localDrafts().size)
    }

    @Test fun aLocalDraftReopensFromPendingChanges() {
        runBlocking { g.drafts.write("local-1", ComposePayload(subject = "From the sheet", text = "Kept words"), null) }
        val row = localDrafts().single()
        val key = RestoreStore.put(runBlocking { g.drafts.payloadOf(row) }, row.draftId, row.id)
        val vm = composer(ComposeArgs(restoreKey = key))
        assertEquals("From the sheet", vm.state.value.subject)
        assertEquals("Kept words", vm.state.value.body)
        // Online now: Back saves it and the copy goes.
        assertEquals(ComposeResult.SavedDraft, result(vm) { vm.close() })
        assertTrue(localDrafts().isEmpty())
        assertEquals(1, draftPosts().size)
    }

    @Test fun sendOfflineKeepsTheTextAndSaysSo() {
        val vm = composer()
        offline = true
        vm.addRecipient(Field.TO, "alice@example.com")
        vm.setSubject("Report")
        vm.setBody("Attached below.")
        vm.send()
        eventually("send failed") { !vm.state.value.sending && vm.state.value.error != null }
        assertTrue(vm.state.value.error!!.contains("Saved on this phone"))
        assertTrue(localDrafts().single().payload.contains("Attached below."))
        // Online: the send goes and the local copy with it.
        offline = false
        vm.clearError()
        val r = result(vm) { vm.send() }
        assertTrue(r is ComposeResult.Sent)
        assertTrue(localDrafts().isEmpty())
    }

    @Test fun aSendRetriedAfterALostAnswerRepeatsItsKey() {
        val vm = composer()
        vm.addRecipient(Field.TO, "alice@example.com")
        vm.setSubject("Report")
        vm.setBody("Attached below.")
        dropSendAnswers = true
        vm.send()
        vm.send()  // a double tap while the first is on its way does nothing
        eventually("send failed") { !vm.state.value.sending && vm.state.value.error != null }
        // One attempt: OkHttp may have retried it on its own, always with the same key.
        val first = sendKeys.toList()
        assertEquals(1, first.toSet().size)
        // Send again: the server gets the same key, so it answers with the first request's result.
        dropSendAnswers = false
        vm.clearError()
        val r = result(vm) { vm.send() }
        assertTrue(r is ComposeResult.Sent)
        assertTrue(sendKeys.size > first.size)
        assertEquals(1, sendKeys.toSet().size)
        assertTrue(sendKeys[0].isNotBlank())
    }

    @Test fun discardDropsTheLocalCopy() {
        offline = true
        val vm = composer()
        vm.setSubject("Never mind")
        advance(ComposeViewModel.LOCAL_DELAY_MS + 50)
        eventually("local copy") { localDrafts().isNotEmpty() }
        // The result carries what the composer held, for the snackbar's Undo.
        val r = result(vm) { vm.discard() }
        assertEquals("Never mind", (r as ComposeResult.DiscardedDraft).payload.subject)
        assertTrue(localDrafts().isEmpty())
    }

    @Test fun aSyncRetriedAfterALostAnswerCreatesOneDraft() = runBlocking {
        g.drafts.write("d1", ComposePayload(subject = "Once"), null)
        g.drafts.sync("d1")
        g.db.localDrafts().put(g.db.localDrafts().get("d1")!!.copy(syncedVersion = 0, draftId = null))  // the answer was lost
        g.drafts.sync("d1")
        // Both tries carried the same Idempotency-Key, so the server applies the create once.
        assertEquals(2, draftPosts().size)
        assertEquals(1, draftKeys.toSet().size)
        val row = g.db.localDrafts().get("d1")
        assertNotNull(row)
        assertTrue(row!!.synced)
        assertNull(row.error)
    }

    @Test fun localCopyRecordsTheSubjectForPendingChanges() = runBlocking {
        g.drafts.write("d2", ComposePayload(subject = "Budget"), null)
        assertEquals("Budget", g.db.localDrafts().get("d2")!!.subject)
        assertEquals("Budget", MailrJson.parseToJsonElement(g.db.localDrafts().get("d2")!!.payload).jsonObject["subject"]!!.jsonPrimitive.content)
    }
}
