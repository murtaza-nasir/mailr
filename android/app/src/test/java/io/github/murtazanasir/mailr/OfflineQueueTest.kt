package io.github.murtazanasir.mailr

import android.app.Application
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.ComposePayload
import io.github.murtazanasir.mailr.data.api.ConversationSummary
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.db.ListItemEntity
import io.github.murtazanasir.mailr.data.db.PendingOpEntity
import io.github.murtazanasir.mailr.data.db.PendingState
import io.github.murtazanasir.mailr.data.db.RemoteKeyEntity
import io.github.murtazanasir.mailr.data.repo.MailRepository.DrainResult
import io.github.murtazanasir.mailr.data.repo.MailRepository
import io.github.murtazanasir.mailr.data.repo.PendingScopes
import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.ui.mail.PendingChange
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
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
import org.robolectric.annotation.Config
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArrayList

/**
 * The offline queue. A 401 pauses it and drops nothing; a refused change is kept as FAILED
 * with Retry and Discard; a failing change does not hold back unrelated ones; a session expiry
 * keeps the queue and the local drafts for the same account's next sign-in.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class OfflineQueueTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    private lateinit var server: MockWebServer
    private lateinit var g: AppGraph
    private val sent = CopyOnWriteArrayList<Pair<String, List<Long>>>()
    /** Status by conversation id for POST conversations/actions (200 when absent). */
    private val statusFor = ConcurrentHashMap<Long, Int>()
    @Volatile private var everything = 0
    private val snoozes = CopyOnWriteArrayList<String>()

    @Before fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.path.orEmpty()
                if (everything != 0) return MockResponse().setResponseCode(everything).setBody("""{"detail":"session ended"}""")
                if (path == "/api/v1/conversations/actions") {
                    val body = MailrJson.parseToJsonElement(request.body.readUtf8()).jsonObject
                    val ids = body["ids"]!!.jsonArray.map { it.jsonPrimitive.long }
                    val code = ids.firstNotNullOfOrNull { statusFor[it] } ?: 200
                    if (code == 200) sent += body["action"]!!.jsonPrimitive.content to ids
                    return MockResponse().setResponseCode(code).setBody(
                        if (code == 200) """{"ok":true,"affected":${ids.size}}""" else """{"detail":"refused $code"}""")
                }
                if (path == "/api/v1/reminders/snooze") {
                    snoozes += request.body.readUtf8()
                    return MockResponse().setResponseCode(200).setBody("""{"key":"t|t1||0","state":"snoozed"}""")
                }
                return MockResponse().setResponseCode(404).setBody("""{"detail":"not here"}""")
            }
        }
        server.start()
        val ctx = ApplicationProvider.getApplicationContext<Application>()
        SessionStore(ctx).apply {
            server = this@OfflineQueueTest.server.url("/").toString()
            save("tok-123", "csrf-456", "me@example.com", "Me")
        }
        g = graphs.graph(ctx)
    }

    @After fun tearDown() {
        graphs.close()
        server.shutdown()
    }

    private fun queue(action: String, vararg ids: Long) = runBlocking {
        g.mail.enqueue("POST", "conversations/actions",
            """{"ids":[${ids.joinToString(",")}],"action":"$action"}""", "k-$action-${ids.joinToString("-")}",
            PendingScopes.describe(action, ids.size))
    }

    private fun ops() = runBlocking { g.db.pending().next(50) }

    @Test fun a401PausesTheQueueAndDropsNothing() = runBlocking {
        queue("archive", 1)
        queue("star", 2)
        everything = 401
        assertEquals(DrainResult.PAUSED, g.mail.drain())
        assertTrue(g.mail.queuePaused.value)
        assertEquals(2, ops().size)
        assertTrue(ops().all { it.state == PendingState.QUEUED && it.attempts == 0 })
        // Signed in again: both go, in order.
        everything = 0
        assertEquals(DrainResult.DONE, g.mail.drain())
        assertFalse(g.mail.queuePaused.value)
        assertEquals(listOf("archive" to listOf(1L), "star" to listOf(2L)), sent.toList())
        assertTrue(ops().isEmpty())
    }

    @Test fun aFailingChangeDoesNotHoldBackUnrelatedOnes() = runBlocking {
        statusFor[1] = 503
        queue("archive", 1)
        queue("star", 2)        // another conversation: goes
        queue("read", 1)        // the same conversation: waits behind the archive
        assertEquals(DrainResult.RETRY, g.mail.drain())
        assertEquals(listOf("star" to listOf(2L)), sent.toList())
        val left = ops()
        assertEquals(listOf("Archive 1 conversation", "Mark read: 1 conversation"), left.map { it.description })
        assertEquals(1, left[0].attempts)
        assertTrue(left[0].nextAttemptAt > System.currentTimeMillis())
        assertEquals(0, left[1].attempts)
        // The server recovers; once the backoff has passed both go, archive first.
        statusFor.clear()
        g.mail.clock = { System.currentTimeMillis() + MailRepository.backoff(1) + 1 }
        assertEquals(DrainResult.DONE, g.mail.drain())
        assertEquals(listOf("archive" to listOf(1L), "read" to listOf(1L)), sent.drop(1))
    }

    @Test fun aRefusedChangeIsKeptWithRetryAndDiscard() = runBlocking {
        statusFor[7] = 422
        queue("archive", 7)
        val failures = CopyOnWriteArrayList<PendingOpEntity>()
        // Undispatched: the collector has subscribed to the SharedFlow when launch returns.
        val job = g.scope.launch(start = kotlinx.coroutines.CoroutineStart.UNDISPATCHED) { g.mail.queueFailures.collect { failures += it } }
        assertEquals(DrainResult.DONE, g.mail.drain())
        val op = ops().single()
        assertEquals(PendingState.FAILED, op.state)
        assertTrue(op.lastError!!.contains("refused 422"))
        // The UI hears about it (a snackbar with Details).
        eventually("the UI hears about the refused change") { failures.isNotEmpty() }
        assertEquals("Archive 1 conversation", failures.single().description)
        job.cancel()
        // Failed changes do not go again by themselves.
        statusFor.clear()
        assertEquals(DrainResult.DONE, g.mail.drain())
        assertTrue(sent.isEmpty())
        // Retry: back in the queue, then sent.
        g.mail.retryPending(op.id)
        assertEquals(PendingState.QUEUED, ops().single().state)
        assertEquals(DrainResult.DONE, g.mail.drain())
        assertEquals(listOf("archive" to listOf(7L)), sent.toList())
        // Discard: another refused change goes away on request.
        statusFor[8] = 400
        queue("trash", 8)
        g.mail.drain()
        g.mail.discardPending(ops().single().id)
        assertTrue(ops().isEmpty())
    }

    @Test fun aChangeThatKeepsFailingTurnsFailedInsteadOfBeingDropped() = runBlocking {
        statusFor[3] = 500
        queue("archive", 3)
        var now = System.currentTimeMillis()
        g.mail.clock = { now }
        repeat(MailRepository.MAX_ATTEMPTS) {
            g.mail.drain()
            now += 31 * 60_000L
        }
        val op = ops().single()
        assertEquals(PendingState.FAILED, op.state)
        assertEquals(MailRepository.MAX_ATTEMPTS, op.attempts)
    }

    @Test fun pendingChangesListsFailedFirstThenWaitingThenDrafts() = runBlocking {
        statusFor[5] = 400
        queue("star", 4)
        queue("archive", 5)
        everything = 0
        statusFor[4] = 503
        g.mail.drain()
        g.drafts.write("d1", ComposePayload(subject = "Budget"), null)
        val list = PendingChange.list(g.db.pending().next(10), g.db.localDrafts().allNow(), paused = false)
        assertEquals(listOf("Archive 1 conversation", "Star 1 conversation", "Draft: Budget"), list.map { it.title })
        assertTrue(list[0].failed)
        // The reason reads as an explanation, quoted, not like the name of an item.
        assertEquals("Not synced. Rejected by the server: \u201crefused 400\u201d.", list[0].status)
        assertEquals("Retrying soon (Server error. Try again.)", list[1].status)
        assertEquals("Saved on this phone. Waiting to sync.", list[2].status)
        // A paused queue says so.
        assertEquals("Sign in again to sync", PendingChange.of(g.db.pending().next(10)[0].copy(state = PendingState.QUEUED), paused = true).status)
    }

    @Test fun legacyQueueDescriptionsReadWell() {
        val op = PendingOpEntity(idempotencyKey = "k", method = "POST", path = "conversations/actions", body = "{}",
            description = "not_spam 3", createdAt = 0)
        assertEquals("Not spam: 3 conversations", PendingScopes.title(op))
        assertEquals("Create event Lunch", PendingScopes.title(op.copy(path = "calendar/events", description = "Create event Lunch")))
    }

    // ---------------- session expiry ----------------

    private fun cacheSomething() = runBlocking {
        val c = Fixtures.conversations.first()
        g.db.lists().replacePage("inbox", listOf(ListItemEntity("inbox", c.id, 0,
            MailrJson.encodeToString(ConversationSummary.serializer(), c))), RemoteKeyEntity("inbox", null, 1, 0))
    }

    private fun waitFor(what: String, cond: suspend () -> Boolean) = eventually(what, idle = {}) { runBlocking { cond() } }

    @Test fun anExpiredSessionKeepsTheQueueAndDraftsForTheSameAccount() {
        cacheSomething()
        queue("archive", 1)
        runBlocking { g.drafts.write("d1", ComposePayload(subject = "Offline words"), null) }
        g.sessionExpired()
        assertNull(g.sessions.session.value)
        waitFor("cache wiped") { g.db.lists().first("inbox", 5).isEmpty() }
        assertEquals(1, ops().size)
        assertNotNull(runBlocking { g.db.localDrafts().get("d1") })
        // The same person signs in again: everything is still there to send.
        runBlocking { g.adoptKeptQueue("ME@example.com") }
        assertEquals(1, ops().size)
        assertNotNull(runBlocking { g.db.localDrafts().get("d1") })
    }

    @Test fun anotherAccountSigningInDropsTheKeptQueue() {
        queue("archive", 1)
        runBlocking { g.drafts.write("d1", ComposePayload(subject = "Not yours"), null) }
        g.sessionExpired()
        waitFor("wipe done") { g.db.lists().first("inbox", 5).isEmpty() }
        runBlocking { g.adoptKeptQueue("someone-else@example.com") }
        assertTrue(ops().isEmpty())
        assertNull(runBlocking { g.db.localDrafts().get("d1") })
    }

    @Test fun signOutClearsTheQueueToo() = runBlocking {
        queue("archive", 1)
        g.drafts.write("d1", ComposePayload(subject = "x"), null)
        g.wipeLocal(signOut = true)
        assertTrue(ops().isEmpty())
        assertNull(g.db.localDrafts().get("d1"))
        assertEquals(0, g.mail.pendingCount.first())
    }

    @Test fun scopesSeparateConversationsAndKeepTheCalendarInOrder() {
        fun op(path: String, body: String) = PendingOpEntity(idempotencyKey = "k", method = "POST", path = path, body = body,
            description = "", createdAt = 0)
        assertEquals(setOf("c:1", "c:2"), PendingScopes.of(op("conversations/actions", """{"ids":[1,2],"action":"star"}""")))
        assertEquals(setOf("other"), PendingScopes.of(op("calendar/events", "{}")))
        assertEquals(setOf("other"), PendingScopes.of(op("tasks/t1?scope=all", "{}")))
    }

    private fun queueSnooze(until: java.time.Instant) = runBlocking {
        g.mail.enqueue("POST", "reminders/snooze", """{"key":"t|t1||0","until":"$until"}""", "k-snooze-$until", "Snooze reminder")
    }

    /**
     * task.snooze replayed from the queue: a snooze whose
     * time passed while the phone was offline is dropped, not sent; one still ahead is sent.
     * The error this fixes: "Couldn't sync: snooze until a time in the future".
     */
    @Test fun anElapsedSnoozeIsDroppedOnReplay() = runBlocking {
        val now = System.currentTimeMillis()
        g.mail.clock = { now }
        queueSnooze(java.time.Instant.ofEpochMilli(now - 30 * 60_000L))
        queueSnooze(java.time.Instant.ofEpochMilli(now + 30 * 60_000L))
        assertEquals(DrainResult.DONE, g.mail.drain())
        assertTrue(ops().isEmpty())
        assertEquals(1, snoozes.size)
        assertTrue(snoozes.single().contains(java.time.Instant.ofEpochMilli(now + 30 * 60_000L).toString()))
        // Within a minute of its time it still goes (the server fires it at once).
        queueSnooze(java.time.Instant.ofEpochMilli(now - 20_000L))
        g.mail.drain()
        assertEquals(2, snoozes.size)
    }

    @Test fun elapsedSnoozeReadsOnlySnoozes() {
        fun op(path: String, body: String) = PendingOpEntity(idempotencyKey = "k", method = "POST", path = path, body = body,
            description = "x", createdAt = 0)
        val now = java.time.Instant.parse("2026-10-04T20:00:00Z").toEpochMilli()
        assertTrue(PendingScopes.elapsedSnooze(op("reminders/snooze", """{"key":"k","until":"2026-10-04T19:00:00Z"}"""), now))
        assertTrue(PendingScopes.elapsedSnooze(op("reminders/snooze", """{"key":"k","until":"2026-10-04T14:00:00-05:00"}"""), now))
        assertFalse(PendingScopes.elapsedSnooze(op("reminders/snooze", """{"key":"k","until":"2026-10-04T21:00:00Z"}"""), now))
        assertFalse(PendingScopes.elapsedSnooze(op("reminders/handle", """{"until":"2026-10-04T19:00:00Z"}"""), now))
        assertFalse(PendingScopes.elapsedSnooze(op("reminders/snooze", "not json"), now))
    }
}
