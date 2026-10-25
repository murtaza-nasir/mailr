// ACT-SURFACES: android.notification
package io.github.murtazanasir.mailr

import android.Manifest
import android.app.Application
import android.app.NotificationManager
import android.os.Looper
import androidx.core.app.NotificationCompat
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.ComposePayload
import io.github.murtazanasir.mailr.data.api.ConversationDetail
import io.github.murtazanasir.mailr.data.api.ConversationSummary
import io.github.murtazanasir.mailr.data.api.Label
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.Page
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.sync.Channels
import io.github.murtazanasir.mailr.data.sync.MailNotifications
import io.github.murtazanasir.mailr.data.sync.Notifier
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okhttp3.mockwebserver.SocketPolicy
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.util.concurrent.CopyOnWriteArrayList

/**
 * New-mail notifications get Archive, Mark read and Reply, and go away once their
 * conversation is read or archived, here or on another device (seen in the next poll, push or
 * live change notice).
 *
 * Interaction ids: mail.archive, mail.read, mail.reply (android.notification)
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class MailNotificationsTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    private lateinit var server: MockWebServer
    private lateinit var g: AppGraph
    private lateinit var app: Application
    private val actions = CopyOnWriteArrayList<JsonObject>()
    private val sends = CopyOnWriteArrayList<JsonObject>()
    private val sendKeys = CopyOnWriteArrayList<String>()
    /** This many POST /send requests reach the server, but their answers are lost. */
    @Volatile private var lostSendAnswers = 0
    @Volatile private var offline = false
    /** What GET /conversations?q=is:unread answers. */
    @Volatile private var unread: List<ConversationSummary> = emptyList()

    private fun page(items: List<ConversationSummary>) =
        MailrJson.encodeToString(Page.serializer(ConversationSummary.serializer()), Page(items, null, items.size))

    @Before fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun peek(): MockResponse =
                if (offline) MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AT_START) else super.peek()

            override fun dispatch(request: RecordedRequest): MockResponse {
                // A kept-alive connection reaches dispatch() even "offline": drop it after the request.
                if (offline) return MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AFTER_REQUEST)
                val path = request.path.orEmpty()
                return when {
                    path == "/api/v1/conversations/actions" -> {
                        actions += MailrJson.parseToJsonElement(request.body.readUtf8()).jsonObject
                        MockResponse().setBody("""{"ok":true,"affected":1}""")
                    }
                    path == "/api/v1/conversations/3066" -> MockResponse().setBody(
                        MailrJson.encodeToString(ConversationDetail.serializer(), Fixtures.detail))
                    path.startsWith("/api/v1/conversations?") -> MockResponse().setBody(page(unread))
                    path.startsWith("/api/v1/compose/reply") -> MockResponse().setBody(
                        """{"payload":{"to":[{"email":"chair@pads.example"}],"subject":"Re: PADS",
                            "html":"<p></p><blockquote>cfp</blockquote>","in_reply_to_message_id":502}}""")
                    path == "/api/v1/send" -> {
                        sends += MailrJson.parseToJsonElement(request.body.readUtf8()).jsonObject
                        sendKeys += request.getHeader("Idempotency-Key").orEmpty()
                        if (lostSendAnswers > 0) {
                            lostSendAnswers--
                            return MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AFTER_REQUEST)
                        }
                        MockResponse().setBody("""{"outbox":{"id":55,"status":"pending"}}""")
                    }
                    path.startsWith("/api/v1/labels") -> MockResponse().setBody(
                        MailrJson.encodeToString(ListSerializer(Label.serializer()), Fixtures.labels))
                    else -> MockResponse().setResponseCode(404).setBody("""{"detail":"not here"}""")
                }
            }
        }
        server.start()
        app = ApplicationProvider.getApplicationContext()
        shadowOf(app).grantPermissions(Manifest.permission.POST_NOTIFICATIONS)
        Channels.create(app)
        SessionStore(app).apply {
            server = this@MailNotificationsTest.server.url("/").toString()
            save("tok-123", "csrf-456", "me@example.com", "Me")
        }
        g = graphs.graph(app)
    }

    @After fun tearDown() {
        shadowOf(Looper.getMainLooper()).idle()
        nm().cancelAll()
        graphs.close()
        server.shutdown()
    }

    private fun nm() = app.getSystemService(NotificationManager::class.java)

    private fun shownIds() = MailNotifications.shown(app)

    private fun post(vararg items: ConversationSummary) = Notifier.newMail(app, items.toList())

    private val a = Fixtures.conversations[0]   // 3075
    private val b = Fixtures.conversations[1]   // 3066

    @Test fun newMailNotificationsCarryArchiveMarkReadAndReply() {
        post(a)
        val n = nm().activeNotifications.single { it.tag == "c${a.id}" }.notification
        val titles = n.actions.map { it.title.toString() }
        assertEquals(listOf("Archive", "Mark read", "Reply"), titles)
        val reply = NotificationCompat.getAction(n, 2)!!
        assertEquals(MailNotifications.KEY_REPLY, reply.remoteInputs!!.single().resultKey)
        assertTrue("Reply needs the phone unlocked", reply.isAuthenticationRequired)
        // Archive and Mark read too: from the lock screen they would hide new mail from its owner.
        assertTrue(NotificationCompat.getAction(n, 0)!!.isAuthenticationRequired)
        assertTrue(NotificationCompat.getAction(n, 1)!!.isAuthenticationRequired)
    }

    @Test fun actingInTheAppCancelsTheNotification() = runBlocking {
        post(a, b)
        assertEquals(setOf(a.id, b.id), shownIds())
        g.mail.act(listOf(a.id), "read")
        assertEquals(setOf(b.id), shownIds())
        // Offline the archive queues, and the notification still goes.
        offline = true
        g.mail.act(listOf(b.id), "archive")
        assertTrue(shownIds().isEmpty())
        assertEquals(1, g.db.pending().next(10).size)
    }

    @Test fun archiveButtonGoesThroughTheRepository() = runBlocking {
        post(a)
        assertTrue(MailNotifications.act(g, a.id, "archive"))
        assertEquals("archive", actions.single()["action"]!!.jsonPrimitive.content)
        assertEquals(listOf(a.id), actions.single()["ids"]!!.jsonArray.map { it.jsonPrimitive.long })
        assertTrue(shownIds().isEmpty())
    }

    @Test fun theMarkReadButtonReachesTheReceiver() {
        post(a)
        // A plain Application (not MailrApp) has no graph: the receiver's work is driven through
        // MailNotifications.act above; here the button's intent wiring is checked.
        val pi = nm().activeNotifications.single().notification.actions[1].actionIntent
        val intent = shadowOf(pi).savedIntent
        assertEquals(MailNotifications.READ, intent.action)
        assertEquals(a.id, intent.getLongExtra(MailNotifications.EXTRA_ID, -1))
        assertEquals(io.github.murtazanasir.mailr.data.sync.MailActionReceiver::class.java.name, intent.component!!.className)
    }

    @Test fun readOrArchivedElsewhereLeavesTheShade() = runBlocking {
        post(a, b)
        // Another device read 3075; 3066 is still unread in the inbox.
        unread = listOf(b)
        MailNotifications.reconcile(g)
        assertEquals(setOf(b.id), shownIds())
        // Then 3066 is archived there too.
        unread = emptyList()
        MailNotifications.reconcile(g)
        assertTrue(shownIds().isEmpty())
    }

    @Test fun aFullPageOnlyDropsWhatIsKnownToBeGone() {
        val many = (1..50L).map { ConversationSummary(10_000 + it, "s", unreadCount = 1) }
        val (gone, unsure) = MailNotifications.stale(setOf(10_001L, 77L), many, 50)
        assertTrue(gone.isEmpty())
        assertEquals(setOf(77L), unsure)
        val (gone2, unsure2) = MailNotifications.stale(setOf(10_001L, 77L), many.take(3), 50)
        assertEquals(setOf(77L), gone2)
        assertTrue(unsure2.isEmpty())
        assertTrue(MailNotifications.stillNew(ConversationSummary(1, unreadCount = 1, labelIds = listOf(1)), 1))
        assertFalse(MailNotifications.stillNew(ConversationSummary(1, unreadCount = 1, labelIds = listOf(10)), 1))
        assertFalse(MailNotifications.stillNew(ConversationSummary(1, unreadCount = 0, labelIds = listOf(1)), 1))
    }

    @Test fun replySendsTheTextOverTheServersPrefill() = runBlocking {
        Notifier.newMail(app, listOf(b))
        val why = MailNotifications.reply(g, b.id, "Friday works.")
        assertEquals(null, why)
        val payload = sends.single()["payload"]!!.jsonObject
        assertEquals(502L, payload["in_reply_to_message_id"]!!.jsonPrimitive.long)
        val html = payload["html"]!!.jsonPrimitive.content
        assertTrue(html.contains("Friday works."))
        assertTrue(html.indexOf("Friday works.") < html.indexOf("<blockquote>cfp</blockquote>"))
        assertEquals("read", actions.single()["action"]!!.jsonPrimitive.content)
        assertTrue(shownIds().isEmpty())
    }

    @Test fun aReplyThatCannotGoSaysSoAndKeepsTheText() = runBlocking {
        Notifier.newMail(app, listOf(b))
        offline = true
        val why = MailNotifications.reply(g, b.id, "Friday works.")
        assertTrue(why!!.contains("offline", ignoreCase = true))
        MailNotifications.replyFailed(app, b.id, "Friday works.", why)
        val n = nm().activeNotifications.single { it.tag == "c${b.id}" }.notification
        assertEquals("Reply not sent", n.extras.getString(NotificationCompat.EXTRA_TITLE))
        assertTrue(n.extras.getCharSequence(NotificationCompat.EXTRA_BIG_TEXT).toString().contains("Friday works."))
        assertTrue(sends.isEmpty())
    }

    @Test fun aRetriedReplySendsUnderTheSameKey() = runBlocking {
        Notifier.newMail(app, listOf(b))
        // OkHttp itself tries a dropped request once more; enough lost answers fail the attempt.
        lostSendAnswers = 4
        var failed = 0
        while (true) {
            val e = runCatching { MailNotifications.replyOnce(g, b.id, "See you then.", "reply-key-1") }.exceptionOrNull()
                ?: break
            assertTrue("a lost answer is worth another attempt: $e", e is Exception && MailNotifications.retryable(e))
            assertTrue(++failed < 5)
        }
        assertTrue(failed >= 1)
        // Every attempt reached the server under one key: its idempotency answers the later ones with the first.
        assertEquals(5, sendKeys.size)
        assertEquals(setOf("reply-key-1"), sendKeys.toSet())
        assertFalse(MailNotifications.retryable(io.github.murtazanasir.mailr.data.api.ApiException(422, "bad")))
    }

    @Test fun theReplyButtonQueuesOneJobPerReply() {
        androidx.work.testing.WorkManagerTestInitHelper.initializeTestWorkManager(app)
        MailNotifications.enqueueReply(app, b.id, "Friday works.", "k-1")
        MailNotifications.enqueueReply(app, b.id, "Friday works.", "k-1")
        val jobs = androidx.work.WorkManager.getInstance(app).getWorkInfosForUniqueWork("reply-k-1").get()
        assertEquals(1, jobs.size)
    }

    @Test fun replyPayloadKeepsQuoteAndSignature() {
        val p = MailNotifications.replyPayload(ComposePayload(subject = "Re: x",
            html = "<p></p><br><div class=\"mailr_signature\">--<br>M</div><blockquote>old</blockquote>"), "Yes <b>please</b>")
        val html = p.html!!
        assertTrue(html.contains("Yes &lt;b&gt;please&lt;/b&gt;"))
        assertTrue(html.contains("<blockquote>old</blockquote>"))
        assertTrue(html.indexOf("Yes") < html.indexOf("<blockquote>"))
        assertEquals(null, p.text)
    }
}
