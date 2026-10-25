package io.github.murtazanasir.mailr

import android.app.Application
import androidx.paging.testing.asSnapshot
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.ApiClient
import io.github.murtazanasir.mailr.data.api.ConversationSummary
import io.github.murtazanasir.mailr.data.api.MailrApi
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.Page
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.db.MailrDb
import io.github.murtazanasir.mailr.data.repo.MailRepository
import io.github.murtazanasir.mailr.data.repo.MailView
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.builtins.ListSerializer
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okhttp3.mockwebserver.SocketPolicy
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.util.concurrent.CopyOnWriteArrayList

@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class RepositoryTest {
    private lateinit var server: MockWebServer
    private lateinit var db: MailrDb
    private lateinit var repo: MailRepository
    private val requests = CopyOnWriteArrayList<RecordedRequest>()
    @Volatile private var offline = false
    @Volatile private var actionStatus = 200

    private fun page(items: List<ConversationSummary>, cursor: String?) =
        MailrJson.encodeToString(Page.serializer(ConversationSummary.serializer()), Page(items, cursor, 12))

    @Before fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            // MockWebServer asks peek() for the socket policy before reading a request.
            override fun peek(): MockResponse =
                if (offline) MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AT_START) else super.peek()

            override fun dispatch(request: RecordedRequest): MockResponse {
                if (offline) return MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AT_START)
                requests += request
                val path = request.path.orEmpty()
                return when {
                    path.startsWith("/api/v1/conversations/actions") -> MockResponse().setResponseCode(actionStatus)
                        .setBody(if (actionStatus == 200) """{"ok":true,"affected":1,"undo":{"action":"inbox","ids":[3075]}}""" else """{"detail":"nope"}""")
                    path.startsWith("/api/v1/conversations") && "cursor=p2" in path ->
                        MockResponse().setBody(page(Fixtures.conversations.drop(3), null))
                    path.startsWith("/api/v1/conversations") -> MockResponse().setBody(page(Fixtures.conversations.take(3), "p2"))
                    path.startsWith("/api/v1/labels") -> MockResponse().setBody(
                        MailrJson.encodeToString(ListSerializer(io.github.murtazanasir.mailr.data.api.Label.serializer()), Fixtures.labels))
                    else -> MockResponse().setResponseCode(404)
                }
            }
        }
        server.start()
        val ctx = ApplicationProvider.getApplicationContext<Application>()
        val sessions = SessionStore(ctx).apply {
            server = this@RepositoryTest.server.url("/").toString()
            save("tok-123", "csrf-456", "me@example.com", "Me")
        }
        db = MailrDb.create(ctx, inMemory = true)
        repo = MailRepository(MailrApi(ApiClient(sessions)), db)
    }

    @After fun tearDown() {
        db.close()
        server.shutdown()
    }

    @Test fun pagerLoadsPagesThroughTheCache() = runTest {
        val items = repo.pager(MailView()).asSnapshot { scrollTo(5) }
        assertEquals(Fixtures.conversations.map { it.id }, items.map { it.id })
        val first = requests.first { it.path!!.startsWith("/api/v1/conversations") }
        assertEquals("mailr_session=tok-123", first.getHeader("Cookie"))
    }

    @Test fun actionIsOptimisticAndCarriesIdempotencyAndCsrf() = runTest {
        repo.refreshLabels()
        repo.pager(MailView()).asSnapshot()
        val out = repo.act(listOf(3075), "archive", currentView = MailView())
        assertEquals(1, out.affected)
        assertNotNull(out.undo)
        val remaining = db.lists().first(MailView().key, 50).map { it.conversationId }
        assertTrue(3075L !in remaining)
        val call = requests.last { it.path == "/api/v1/conversations/actions" }
        assertEquals("csrf-456", call.getHeader("X-CSRF-Token"))
        assertNotNull(call.getHeader("Idempotency-Key"))
    }

    @Test fun offlineActionsQueueAndReplayWithTheSameKey() = runTest {
        offline = true
        val out = repo.act(listOf(3074), "star")
        assertTrue(out.queuedOffline)
        assertEquals(1, repo.pendingCount.first())
        assertEquals(false, repo.drainPending())       // still offline: kept for later
        offline = false
        assertTrue(repo.drainPending())
        assertEquals(0, repo.pendingCount.first())
        val replays = requests.filter { it.path == "/api/v1/conversations/actions" }
        assertEquals(1, replays.size)
        assertTrue(replays[0].body.readUtf8().contains("\"star\""))
    }

    /** A refusal is kept as FAILED for Pending changes (Retry or Discard), never dropped silently. */
    @Test fun permanentlyRejectedOperationsAreKeptAsFailed() = runTest {
        offline = true
        repo.act(listOf(3074), "trash")
        offline = false
        actionStatus = 400
        assertTrue(repo.drainPending())       // nothing left for the network to do
        assertEquals(1, repo.pendingCount.first())
        val op = db.pending().next(10).single()
        assertEquals("FAILED", op.state)
        assertTrue(op.lastError!!.contains("nope"))
    }
}
