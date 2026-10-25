// ACT-SURFACES: android.approvals
package io.github.murtazanasir.mailr

import android.Manifest
import android.app.Application
import android.app.Notification
import android.app.NotificationManager
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.Approval
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.sync.Channels
import io.github.murtazanasir.mailr.data.sync.Notifier
import io.github.murtazanasir.mailr.data.sync.ServerEvent
import io.github.murtazanasir.mailr.data.sync.pollApprovals
import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.ui.approvals.ApprovalNotice
import io.github.murtazanasir.mailr.ui.approvals.ApprovalRecord
import io.github.murtazanasir.mailr.ui.approvals.ApprovalsTracker
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.net.URLDecoder
import java.util.concurrent.CopyOnWriteArrayList

/**
 * The app-wide approvals state (ApprovalsTracker): the pending list behind the badges, the
 * one-time arrival for the banner, the history, and "handled anywhere, gone everywhere" for the
 * approval notifications (approval.approve, approval.deny).
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class ApprovalsTrackerTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    private val app: Application = ApplicationProvider.getApplicationContext()
    private lateinit var server: MockWebServer
    private lateinit var g: AppGraph
    private lateinit var scope: CoroutineScope
    private val requests = CopyOnWriteArrayList<RecordedRequest>()
    @Volatile private var pending: List<Approval> = Fixtures.approvals
    @Volatile private var decided: List<ApprovalRecord> = Fixtures.approvalHistory

    @Before fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                requests += request
                val path = URLDecoder.decode(request.path.orEmpty(), "UTF-8")
                return when {
                    path == "/api/v1/approvals?status=pending" -> MockResponse().setBody(
                        """{"items":${MailrJson.encodeToString(ListSerializer(Approval.serializer()), pending)}}""")
                    path.startsWith("/api/v1/approvals?status=approved,executing,executed,failed,denied,expired") ->
                        MockResponse().setBody(MailrJson.encodeToString(ListSerializer(ApprovalRecord.serializer()), decided))
                    // A grant on 41 also approved the waiting request 43 (rule 9).
                    path == "/api/v1/approvals/41/decide" && request.method == "POST" ->
                        MockResponse().setBody("""{"status":"approved","rule_id":9,"also_decided":{"ids":[43],"count":1}}""")
                    path.matches(Regex("/api/v1/approvals/\\d+/decide")) && request.method == "POST" ->
                        MockResponse().setBody("""{"status":"approved"}""")
                    path == "/api/v1/approvals/grants/9/undo" && request.method == "POST" ->
                        MockResponse().setBody("""{"rule_id":9,"reverted":[43],"kept":[]}""")
                    else -> MockResponse().setResponseCode(404).setBody("""{"detail":"not here"}""")
                }
            }
        }
        server.start()
        SessionStore(app).apply { server = this@ApprovalsTrackerTest.server.url("/").toString(); save("tok", "csrf", "me@example.com", "Me") }
        shadowOf(app).grantPermissions(Manifest.permission.POST_NOTIFICATIONS)
        Channels.create(app)
        g = graphs.graph(app)
        // Unconfined: the tracker's own first read takes the lock before the constructor returns, so
        // the reads in a test always come after it.
        scope = graphs.scope(Dispatchers.Unconfined)
    }

    @After fun tearDown() {
        scope.cancel()
        graphs.close()
        server.shutdown()
    }

    private fun tracker() = ApprovalsTracker(g, emptyFlow(), scope).also { runBlocking { it.refresh(seed = true) } }

    private fun event(type: String, id: Long, status: String) = ServerEvent(type, buildJsonObject {
        put("type", type); put("id", id); put("status", status); put("actor", "Claude Code"); put("title", "Create event")
    })

    private val nm get() = app.getSystemService(NotificationManager::class.java)
    private fun posted(id: String): Notification? = shadowOf(nm).getNotification(Notifier.approvalTag(id), Notifier.APPROVAL_ID)

    /** The count behind the Assistant tab's badge and the inbox shield, and the history with outcomes. */
    @Test fun readsThePendingListAndTheRecentDecisions() {
        val t = tracker()
        assertEquals(listOf(41L, 42L), t.pending.value!!.map { it.id })
        assertEquals(listOf("Done", "Denied", "Failed", "Expired"), t.history.value.map { it.outcome })
        assertEquals("The event was deleted on the server.", t.history.value[2].error)
    }

    /** Only a new approval that arrives while the app is open is announced, and only once. */
    @Test fun aNewApprovalIsAnnouncedOncePerItem() {
        val t = tracker()
        // Already waiting when the app opened: the badge shows it, the banner does not.
        runBlocking { t.onEvent(event("approval", 41, "pending")) }
        assertNull(t.arrival.value)

        pending = Fixtures.approvals + Fixtures.approvals[0].copy(id = 43, preview = Fixtures.approvals[0].preview.copy(title = "Create event 2"))
        runBlocking { t.onEvent(event("approval", 43, "pending")) }
        assertEquals(43L, t.arrival.value!!.approval.id)
        assertEquals("Create event 2", t.arrival.value!!.approval.preview.title)
        assertEquals(3, t.pending.value!!.size)

        t.dismissArrival()
        runBlocking { t.onEvent(event("approval", 43, "pending")) }   // the same item again (a replayed event)
        assertNull(t.arrival.value)
        runBlocking { t.refresh(seed = true) }                         // and after a resume
        runBlocking { t.onEvent(event("approval", 43, "pending")) }
        assertNull(t.arrival.value)
    }

    /** Two arrivals in a row: the banner shows the newest and how many more came meanwhile. */
    @Test fun arrivalsInARowAreCounted() {
        val t = tracker()
        runBlocking { t.onEvent(event("approval", 50, "pending")) }
        runBlocking { t.onEvent(event("approval", 51, "pending")) }
        assertEquals(51L, t.arrival.value!!.approval.id)
        assertEquals(1, t.arrival.value!!.more)
    }

    /** Handled anywhere, gone everywhere: an approval answered on the web loses its notification here. */
    @Test fun handledElsewhereClearsTheNotification() {
        runBlocking { pollApprovals(g) }
        assertNotNull(posted("41"))
        val t = tracker()
        pending = listOf(Fixtures.approvals[1])
        runBlocking { t.onEvent(event("approval", 41, "approved")) }
        assertNull(posted("41"))
        assertNotNull(posted("42"))
        assertEquals(listOf(42L), t.pending.value!!.map { it.id })

        // Missed the event (the stream was down): the next read clears it too.
        pending = emptyList()
        runBlocking { t.refresh() }
        assertNull(posted("42"))
        assertEquals(0, t.pending.value!!.size)
    }

    /** An arrival that is answered elsewhere before its banner went away loses the banner too. */
    @Test fun anArrivalAnsweredElsewhereLeavesTheBanner() {
        val t = tracker()
        runBlocking { t.onEvent(event("approval", 60, "pending")) }
        assertNotNull(t.arrival.value)
        runBlocking { t.onEvent(event("approval", 60, "denied")) }
        assertNull(t.arrival.value)
    }

    /** ACT approval.approve, ACT approval.deny on the Approvals screen: the decision, then the notification goes. */
    @Test fun decidingOnTheScreenRemovesItsNotification() {
        runBlocking { pollApprovals(g) }
        val t = tracker()
        t.decide(Fixtures.approvals[0], approve = true, scope = "session")
        eventually("the notification for 41 goes") { posted("41") == null }
        val body = requests.last { it.path == "/api/v1/approvals/41/decide" }.body.readUtf8()
        assertTrue(body, "\"session\"" in body && "\"approve\"" in body)
    }

    /**
     * ACT approval.undo_grant: "Allow for 12 hours" that also approved another waiting request says so,
     * with Undo; Undo puts it back and says what happened.
     */
    @Test fun aGrantThatCoversOthersOffersUndo() {
        val t = tracker()
        val got = CopyOnWriteArrayList<ApprovalNotice>()
        scope.launch { t.messages.collect { got += it } }
        t.decide(Fixtures.approvals[0], approve = true, scope = "session")
        eventually("the grant's notice") { got.isNotEmpty() }
        assertEquals(ApprovalNotice("Allowed for 12 hours. Also approved 1 other request.", 9), got.first())
        t.undoGrant(9)
        eventually("the undo's notice") { got.size >= 2 }
        assertEquals(ApprovalNotice("Undone. 1 request is waiting again."), got[1])
        assertTrue(requests.any { it.path == "/api/v1/approvals/grants/9/undo" && it.method == "POST" })
    }

    /** A decision for this request only says just that: no Undo. */
    @Test fun allowOnceHasNoUndo() {
        val t = tracker()
        val got = CopyOnWriteArrayList<ApprovalNotice>()
        scope.launch { t.messages.collect { got += it } }
        t.decide(Fixtures.approvals[1], approve = true, scope = "once")
        eventually("the decision's notice") { got.isNotEmpty() }
        assertEquals(ApprovalNotice("Allowed"), got.first())
    }

    /** ACT approval.deny on the Approvals screen: Deny sends "deny" for this one action, then the notification goes. */
    @Test fun denyingOnTheScreenSendsDeny() {
        runBlocking { pollApprovals(g) }
        val t = tracker()
        t.decide(Fixtures.approvals[1], approve = false, scope = "once")
        eventually("the notification for 42 goes") { posted("42") == null }
        val body = requests.last { it.path == "/api/v1/approvals/42/decide" }.body.readUtf8()
        assertTrue(body, "\"deny\"" in body && "\"once\"" in body)
    }

    /** ACT agent.review_move on the Approvals screen: Apply on a review card sends the user's moves as `changes`, once. */
    @Test fun applyingAReviewCardSendsTheChanges() {
        val t = tracker()
        t.applyReview(Fixtures.approvals[1], mapOf(21L to "keep", 11L to "label:7"))
        eventually("decided") { requests.any { it.path == "/api/v1/approvals/42/decide" } }
        val body = requests.last { it.path == "/api/v1/approvals/42/decide" }.body.readUtf8()
        assertEquals("""{"decision":"approve","scope":"once","changes":{"21":"keep","11":"label:7"}}""", body)
    }

    /** The older outbox queue: its rows are keyed "o<id>" and announced the same way. */
    @Test fun outboxEventsUseTheirOwnKeys() {
        val t = tracker()
        runBlocking { t.onEvent(event("outbox", 7, "awaiting_approval")) }
        assertEquals(7L, t.arrival.value!!.approval.outboxId)
        assertEquals(JsonPrimitive("Claude Code").content, t.arrival.value!!.approval.actor)
    }
}
