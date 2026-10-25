// ACT-SURFACES: android.notification
package io.github.murtazanasir.mailr

import android.Manifest
import android.app.Application
import android.app.Notification
import android.app.NotificationManager
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.Approval
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.sync.ApprovalActionReceiver
import io.github.murtazanasir.mailr.data.sync.Channels
import io.github.murtazanasir.mailr.data.sync.Notifier
import io.github.murtazanasir.mailr.data.sync.decideFromNotification
import io.github.murtazanasir.mailr.data.sync.pollApprovals
import io.github.murtazanasir.mailr.data.push.PushEvent
import io.github.murtazanasir.mailr.data.push.PushHandler
import io.github.murtazanasir.mailr.data.push.PushPayload
import io.github.murtazanasir.mailr.data.sync.ServerEvent
import io.github.murtazanasir.mailr.ui.approvals.ApprovalsTracker
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import io.github.murtazanasir.mailr.ui.MainActivity
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.JsonObject
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
import org.junit.Rule
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArrayList

/**
 * approval.approve and approval.deny on android.notification: each pending approval gets its own
 * notification with its preview, and Allow once / Deny buttons that need the device unlocked. An
 * answer POSTs /approvals/{id}/decide with scope "once" and removes that notification; a tap
 * opens the Approvals screen at that approval. approval.sync: an approval that leaves pending
 * anywhere loses its notification here (the approval.decided push or event, or a read of the
 * list), and the group's summary follows.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class ApprovalNotificationTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    private val app: Application = ApplicationProvider.getApplicationContext()
    private lateinit var server: MockWebServer
    private lateinit var g: AppGraph
    private val requests = CopyOnWriteArrayList<RecordedRequest>()
    private val bodies = ConcurrentHashMap<RecordedRequest, String>()
    @Volatile private var pending: List<Approval> = Fixtures.approvals
    @Volatile private var failDecide = false

    @Before fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                bodies[request] = request.body.readUtf8()
                requests += request
                val path = request.path.orEmpty()
                return when {
                    path == "/api/v1/approvals?status=pending" -> MockResponse().setBody(
                        """{"items":${MailrJson.encodeToString(ListSerializer(Approval.serializer()), pending)}}""")
                    path.matches(Regex("/api/v1/approvals/\\d+/decide")) && request.method == "POST" ->
                        if (failDecide) MockResponse().setResponseCode(503).setBody("""{"detail":"try later"}""")
                        else MockResponse().setBody("""{"status":"approved"}""")
                    else -> MockResponse().setResponseCode(404).setBody("""{"detail":"not here"}""")
                }
            }
        }
        server.start()
        SessionStore(app).apply { server = this@ApprovalNotificationTest.server.url("/").toString(); save("tok", "csrf", "me@example.com", "Me") }
        shadowOf(app).grantPermissions(Manifest.permission.POST_NOTIFICATIONS)
        Channels.create(app)
        g = graphs.graph(app)
    }

    @After fun tearDown() {
        graphs.close()
        server.shutdown()
    }

    private val nm get() = app.getSystemService(NotificationManager::class.java)
    private fun posted(id: Long): Notification? = shadowOf(nm).getNotification(Notifier.approvalTag("$id"), Notifier.APPROVAL_ID)

    private fun decided(id: Long): JsonObject =
        MailrJson.parseToJsonElement(bodies[requests.last { it.path == "/api/v1/approvals/$id/decide" }]!!).jsonObject

    /** One notification per pending approval: its preview's title and lines, the Approvals screen at it on a tap. */
    @Test fun eachApprovalHasItsOwnNotification() {
        runBlocking { pollApprovals(g) }
        assertEquals(3, shadowOf(nm).size())   // two approvals and the group's summary
        val n = posted(41)!!
        assertEquals("Create event", n.extras.getCharSequence(Notification.EXTRA_TITLE).toString())
        assertEquals("Wed 30 Sep, 14:00–15:00", n.extras.getCharSequence(Notification.EXTRA_TEXT).toString())
        assertEquals(listOf("Wed 30 Sep, 14:00–15:00", "Review PADS submission with Alice"),
            n.extras.getCharSequenceArray(Notification.EXTRA_TEXT_LINES)!!.map { it.toString() })
        assertEquals("Send “Re: Quarterly plan”", posted(42)!!.extras.getCharSequence(Notification.EXTRA_TITLE).toString())

        // A tap opens the Approvals screen at this approval and decides nothing.
        assertEquals(0, n.flags and Notification.FLAG_AUTO_CANCEL)
        assertTrue(shadowOf(n.contentIntent).isActivityIntent)
        val open = shadowOf(n.contentIntent).savedIntent
        assertEquals(MainActivity::class.java.name, open.component!!.className)
        assertEquals("mailr://approvals/41", open.dataString)
        assertTrue(requests.none { it.method == "POST" })
    }

    /** ACT approval.approve, ACT approval.deny: two buttons, both only after the device is unlocked. */
    @Test fun approveAndDenyNeedAnUnlockedDevice() {
        runBlocking { pollApprovals(g) }
        val n = posted(41)!!
        assertEquals(listOf("Allow once", "Deny"), n.actions.map { it.title.toString() })
        n.actions.forEach { a ->
            assertTrue("${a.title} must require authentication", a.isAuthenticationRequired)
            assertTrue(shadowOf(a.actionIntent).isBroadcastIntent)
            val i = shadowOf(a.actionIntent).savedIntent
            assertEquals(ApprovalActionReceiver::class.java.name, i.component!!.className)
            assertEquals(41L, i.getLongExtra(ApprovalActionReceiver.EXTRA_ID, -1))
            assertEquals(-1L, i.getLongExtra(ApprovalActionReceiver.EXTRA_OUTBOX, 0))
        }
        assertEquals(listOf(ApprovalActionReceiver.APPROVE, ApprovalActionReceiver.DENY),
            n.actions.map { shadowOf(it.actionIntent).savedIntent.action })
    }

    /** ACT approval.approve: what the receiver runs for Approve; the notification goes, the other stays. */
    @Test fun approveDecidesOnceAndRemovesTheNotification() {
        runBlocking { pollApprovals(g) }
        assertTrue(runBlocking { decideFromNotification(g, 41, null, approve = true) })
        val body = decided(41)
        assertEquals("approve", body["decision"]!!.jsonPrimitive.content)
        assertEquals("once", body["scope"]!!.jsonPrimitive.content)
        assertNull(posted(41))
        assertNotNull(posted(42))
    }

    /** ACT approval.deny: what the receiver runs for Deny. */
    @Test fun denyDecidesOnceAndRemovesTheNotification() {
        runBlocking { pollApprovals(g) }
        assertTrue(runBlocking { decideFromNotification(g, 42, null, approve = false) })
        val body = decided(42)
        assertEquals("deny", body["decision"]!!.jsonPrimitive.content)
        assertEquals("once", body["scope"]!!.jsonPrimitive.content)
        assertNull(posted(42))
    }

    /** A failed answer keeps the notification, so the user can answer again. */
    @Test fun failedAnswerKeepsTheNotification() {
        runBlocking { pollApprovals(g) }
        failDecide = true
        assertFalse(runBlocking { decideFromNotification(g, 41, null, approve = true) })
        assertNotNull(posted(41))
    }

    private fun summary(): Notification? = shadowOf(nm).getNotification(Notifier.APPROVAL_SUMMARY_TAG, Notifier.APPROVAL_ID)
    private fun lines(n: Notification) = n.extras.getCharSequenceArray(Notification.EXTRA_TEXT_LINES)!!.map { it.toString() }
    private fun decidedPush(vararg ids: Long) = PushPayload.decode(
        """{"title": "", "body": "", "type": "approval.decided", "outcome": "approved", "ids": [${ids.joinToString()}]}""".toByteArray())!!

    /** Each approval's notification sits in one group whose summary counts them and lists their titles. */
    @Test fun theGroupSummaryListsTheApprovalsShowing() {
        runBlocking { pollApprovals(g) }
        val s = summary()!!
        assertTrue(s.flags and Notification.FLAG_GROUP_SUMMARY != 0)
        assertEquals(Notifier.APPROVAL_GROUP, s.group)
        assertEquals(Notifier.APPROVAL_GROUP, posted(41)!!.group)
        assertEquals("2 approvals waiting", s.extras.getCharSequence(Notification.EXTRA_TITLE).toString())
        assertEquals(2, s.number)
        assertEquals(setOf("Create event", "Send “Re: Quarterly plan”"), lines(s).toSet())
        assertEquals("mailr://approvals", shadowOf(s.contentIntent).savedIntent.dataString)
    }

    /**
     * ACT approval.sync: the server's approval.decided push (answered on the web, Allow all, expired) takes
     * those notifications down at once; the summary follows, and goes with the last one.
     */
    @Test fun aDecidedPushCancelsTheNotificationAndTheSummaryFollows() {
        runBlocking { pollApprovals(g) }
        val e = decidedPush(41) as PushEvent.ApprovalDecided
        assertEquals(listOf(41L), e.ids)
        assertEquals("approved", e.outcome)
        assertEquals("", e.title)
        pending = listOf(Fixtures.approvals[1])
        runBlocking { PushHandler.handle(g, e, trusted = true) }
        assertNull(posted(41))
        assertNotNull(posted(42))
        assertEquals("1 approval waiting", summary()!!.extras.getCharSequence(Notification.EXTRA_TITLE).toString())
        assertEquals(listOf("Send “Re: Quarterly plan”"), lines(summary()!!))

        pending = emptyList()
        runBlocking { PushHandler.handle(g, decidedPush(42), trusted = true) }
        assertNull(posted(42))
        assertNull(summary())
        assertEquals(0, shadowOf(nm).size())
        // Both left the "already notified" list: if one came back (Undo of a grant), it is notified again.
        pending = Fixtures.approvals
        runBlocking { pollApprovals(g) }
        assertNotNull(posted(41))
        assertNotNull(posted(42))
    }

    /** A message that was not decrypted names nothing trusted: only the read of the list takes a notification down. */
    @Test fun anUntrustedDecidedPushOnlyTrustsTheList() {
        runBlocking { pollApprovals(g) }
        runBlocking { PushHandler.handle(g, decidedPush(41), trusted = false) }
        assertNotNull(posted(41))                                       // the server says 41 still waits
        pending = listOf(Fixtures.approvals[1])
        runBlocking { PushHandler.handle(g, decidedPush(41), trusted = false) }
        assertNull(posted(41))
        assertNotNull(posted(42))
        assertEquals(1, summary()!!.number)
    }

    /** ACT approval.sync: any read of the approvals list takes down the notifications of approvals no longer waiting. */
    @Test fun aListSyncReconcilesAndUpdatesTheSummary() {
        runBlocking { pollApprovals(g) }
        pending = listOf(Fixtures.approvals[0])                         // 42 expired while no event came
        val tracker = ApprovalsTracker(g, emptyFlow(), graphs.scope(Dispatchers.Unconfined))
        runBlocking { tracker.refresh() }
        assertNotNull(posted(41))
        assertNull(posted(42))
        assertEquals(listOf("Create event"), lines(summary()!!))
        pending = emptyList()
        runBlocking { tracker.refresh() }
        assertNull(posted(41))
        assertNull(summary())
    }

    /** ACT approval.sync: the event stream's approval.decided, while the app is open, does the same. */
    @Test fun theStreamsDecidedEventCancelsTooAndOneEventCarriesABatch() {
        runBlocking { pollApprovals(g) }
        val tracker = ApprovalsTracker(g, emptyFlow(), graphs.scope(Dispatchers.Unconfined))
        runBlocking { tracker.refresh() }
        runBlocking {
            tracker.onEvent(ServerEvent("approval.decided", buildJsonObject {
                put("type", "approval.decided"); put("outcome", "approved")
                put("ids", JsonArray(listOf(JsonPrimitive(41L), JsonPrimitive(42L))))
            }))
        }
        assertNull(posted(41))
        assertNull(posted(42))
        assertNull(summary())
        assertEquals(emptyList<Long>(), tracker.pending.value!!.map { it.id })
    }

    /** Decided on this phone: its notification goes and the summary counts one fewer. */
    @Test fun anAnswerFromTheNotificationUpdatesTheSummary() {
        runBlocking { pollApprovals(g) }
        assertTrue(runBlocking { decideFromNotification(g, 41, null, approve = true) })
        assertNull(posted(41))
        assertEquals(1, summary()!!.number)
        assertTrue(runBlocking { decideFromNotification(g, 42, null, approve = false) })
        assertNull(summary())
    }

    /** The next poll repeats nothing, and removes the notification of an approval answered elsewhere. */
    @Test fun nextPollRepeatsNothingAndClearsAnsweredOnes() {
        runBlocking { pollApprovals(g) }
        nm.cancel(Notifier.approvalTag("42"), Notifier.APPROVAL_ID)    // swiped away, still pending
        pending = listOf(Fixtures.approvals[1])                          // 41 was answered on the web
        runBlocking { pollApprovals(g) }
        assertNull(posted(41))
        assertNull(posted(42))
        // A new approval is notified.
        pending = listOf(Fixtures.approvals[1], Fixtures.approvals[0].copy(id = 43))
        runBlocking { pollApprovals(g) }
        assertNotNull(posted(43))
        assertNull(posted(42))
    }
}
