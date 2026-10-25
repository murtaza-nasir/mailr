// ACT-SURFACES: android.notification android.list
package io.github.murtazanasir.mailr

import android.Manifest
import android.app.Application
import android.app.Notification
import android.app.NotificationManager
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.test.assertCountEquals
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.ConversationSummary
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.Page
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.settings.SwipeAction
import io.github.murtazanasir.mailr.data.sync.Channels
import io.github.murtazanasir.mailr.data.sync.NotifyDecisions
import io.github.murtazanasir.mailr.data.sync.pollNudges
import io.github.murtazanasir.mailr.ui.mail.ConversationRow
import kotlinx.coroutines.runBlocking
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
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.util.concurrent.CopyOnWriteArrayList

/**
 * mail.follow_up on android.list (the "No reply" pill on a nudged row) and on
 * android.notification (the background poll of `is:nudged`: one notification per nudge, never
 * twice for the same one).
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class FollowUpTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()

    private val app: Application = ApplicationProvider.getApplicationContext()
    private var server: MockWebServer? = null
    private var g: AppGraph? = null
    private val requests = CopyOnWriteArrayList<RecordedRequest>()
    @Volatile private var nudged: List<ConversationSummary> = emptyList()

    @After fun tearDown() {
        graphs.close()
        server?.shutdown()
    }

    private fun row(c: ConversationSummary) = compose.setContent {
        MaterialTheme {
            ConversationRow(c, emptyMap(), selected = false, selectionMode = false, activated = false, compact = false,
                swipeStart = SwipeAction.ARCHIVE, swipeEnd = SwipeAction.TRASH, onClick = {}, onLongClick = {},
                onToggleSelect = {}, onStar = {}, onSwipe = {})
        }
    }

    /** ACT mail.follow_up on android.list: a conversation that came back with no reply shows "No reply". */
    @Test fun listShowsNoReplyWhenNudged() {
        row(Fixtures.conversations[1].copy(nudgedAt = "2026-09-30T09:00:00Z"))
        compose.onNodeWithText("No reply").assertExists()
    }

    @Test fun listShowsNoPillOtherwise() {
        row(Fixtures.conversations[1].copy(nudgedAt = null, followUpAt = "2026-10-03T09:00:00Z"))
        compose.onAllNodesWithText("No reply").assertCountEquals(0)
    }

    // ---------- android.notification ----------

    private fun start(): AppGraph {
        val s = MockWebServer()
        s.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                requests += request
                return if (request.path.orEmpty().startsWith("/api/v1/conversations?"))
                    MockResponse().setBody(MailrJson.encodeToString(Page.serializer(ConversationSummary.serializer()), Page(nudged)))
                else MockResponse().setResponseCode(404).setBody("""{"detail":"not here"}""")
            }
        }
        s.start()
        server = s
        SessionStore(app).apply { server = s.url("/").toString(); save("tok", "csrf", "me@example.com", "Me") }
        shadowOf(app).grantPermissions(Manifest.permission.POST_NOTIFICATIONS)
        Channels.create(app)
        return graphs.graph(app).also { g = it }
    }

    private val nm get() = app.getSystemService(NotificationManager::class.java)
    private fun posted(id: Long): Notification? = shadowOf(nm).getNotification("n$id", 4)

    /** ACT mail.follow_up on android.notification: "No reply from …" once per nudge, not again on the next poll. */
    @Test fun notifiesOncePerNudge() {
        val g = start()
        nudged = listOf(Fixtures.conversations[1].copy(nudgedAt = "2026-09-30T09:00:00Z"),
            Fixtures.conversations[5].copy(nudgedAt = "2026-09-30T09:01:00Z"))
        runBlocking { pollNudges(g) }

        // The poll asks the inbox for conversations that came back with no reply.
        val ask = requests.single { it.path.orEmpty().startsWith("/api/v1/conversations?") }.requestUrl!!
        assertEquals(NotifyDecisions.NUDGED_QUERY, ask.queryParameter("q"))
        assertEquals("is:nudged", ask.queryParameter("q"))
        assertEquals("inbox", ask.queryParameter("view"))

        assertEquals(2, shadowOf(nm).size())
        val n = posted(3066)!!
        assertEquals("No reply from Northfield Analytics", n.extras.getCharSequence(Notification.EXTRA_TITLE).toString())
        assertEquals(Fixtures.conversations[1].subject, n.extras.getCharSequence(Notification.EXTRA_TEXT).toString())
        assertEquals("No reply from Alice Chen", posted(3050)!!.extras.getCharSequence(Notification.EXTRA_TITLE).toString())

        // The user swipes them away; the next poll sees the same nudges and posts nothing.
        nm.cancelAll()
        runBlocking { pollNudges(g) }
        assertEquals(2, requests.count { it.path.orEmpty().startsWith("/api/v1/conversations?") })
        assertEquals(0, shadowOf(nm).size())

        // A later nudge on the same conversation is a new one.
        nudged = listOf(Fixtures.conversations[1].copy(nudgedAt = "2026-10-07T09:00:00Z"), nudged[1])
        runBlocking { pollNudges(g) }
        assertNotNull(posted(3066))
        assertNull(posted(3050))
    }

    @Test fun rowsWithoutANudgeAreNotNotified() {
        val (fresh, seen) = NotifyDecisions.freshNudges(listOf(Fixtures.conversations[0], Fixtures.conversations[1].copy(nudgedAt = "t1")), null)
        assertEquals(listOf(3066L), fresh.map { it.id })
        assertEquals("3066@t1", seen)
        assertTrue(NotifyDecisions.freshNudges(listOf(Fixtures.conversations[1].copy(nudgedAt = "t1")), seen).first.isEmpty())
    }
}
