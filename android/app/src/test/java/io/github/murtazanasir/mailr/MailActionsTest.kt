// ACT-SURFACES: android.list android.editor android.reader
package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.time.AppClock
import android.app.Application
import android.os.Looper
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.ConversationDetail
import io.github.murtazanasir.mailr.data.api.ConversationSummary
import io.github.murtazanasir.mailr.data.api.Label
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.db.ListItemEntity
import io.github.murtazanasir.mailr.data.db.RemoteKeyEntity
import io.github.murtazanasir.mailr.data.repo.MailView
import io.github.murtazanasir.mailr.data.settings.SwipeAction
import io.github.murtazanasir.mailr.ui.compose.ComposeArgs
import io.github.murtazanasir.mailr.ui.compose.ComposeViewModel
import io.github.murtazanasir.mailr.ui.compose.Field
import io.github.murtazanasir.mailr.ui.conversation.ConversationViewModel
import io.github.murtazanasir.mailr.ui.mail.MailboxViewModel
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.JsonNull
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
import java.time.ZonedDateTime
import java.util.concurrent.CopyOnWriteArrayList

/**
 * The mail actions of the Android list, reader and composer, driven through the same view-model
 * entry points the UI calls, against a fake server. Each test checks the request that reached the
 * server (or the offline queue) and the change in the Room cache.
 *
 * Interaction ids covered here:
 *  - android.list: mail.archive, mail.delete, mail.read, mail.unread, mail.star, mail.important, mail.mute
 *  - android.reader: mail.archive, mail.delete, mail.spam, mail.snooze, mail.label, mail.important, mail.mute,
 *    mail.reply, mail.forward,
 *    draft.save, draft.send, draft.schedule, draft.discard
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class MailActionsTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    private lateinit var server: MockWebServer
    private lateinit var g: AppGraph
    private val requests = CopyOnWriteArrayList<RecordedRequest>()
    private val bodies = java.util.concurrent.ConcurrentHashMap<RecordedRequest, String>()
    @Volatile private var offline = false

    private val inbox = MailView()

    private val me = """{"user":{"id":1,"email":"me@example.com","name":"Me"},
        "identities":[{"id":7,"email":"me@example.com","name":"Me","is_default":true}]}"""

    @Before fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun peek(): MockResponse =
                if (offline) MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AT_START) else super.peek()

            override fun dispatch(request: RecordedRequest): MockResponse {
                if (offline) return MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AT_START)
                bodies[request] = request.body.readUtf8()
                requests += request
                val path = request.path.orEmpty()
                val m = request.method
                return when {
                    path == "/api/v1/me" -> MockResponse().setBody(me)
                    path.startsWith("/api/v1/labels") -> MockResponse().setBody(
                        MailrJson.encodeToString(ListSerializer(Label.serializer()), Fixtures.labels))
                    path.startsWith("/api/v1/conversations/actions") ->
                        MockResponse().setBody("""{"ok":true,"affected":1,"undo":{"action":"inbox"}}""")
                    path == "/api/v1/conversations/3066" ->
                        MockResponse().setBody(MailrJson.encodeToString(ConversationDetail.serializer(), Fixtures.detail))
                    path.startsWith("/api/v1/compose/reply") -> {
                        val fwd = "mode=forward" in path
                        MockResponse().setBody(
                            if (fwd) """{"payload":{"subject":"Fwd: PADS","html":"<p></p><blockquote>cfp</blockquote>","forward_of_message_id":502}}"""
                            else """{"payload":{"to":[{"email":"chair@pads.example","name":"Program Chair"}],"subject":"Re: PADS",
                                "html":"<p></p><blockquote>cfp</blockquote>","in_reply_to_message_id":502}}""")
                    }
                    path == "/api/v1/drafts" && m == "POST" -> MockResponse().setBody("""{"draft":{"id":900,"draft":true}}""")
                    path == "/api/v1/drafts/77" && m == "GET" -> MockResponse().setBody(
                        """{"payload":{"to":[{"email":"alice@example.com"}],"subject":"Quarterly plan","text":"Friday?"}}""")
                    path == "/api/v1/drafts/77" && m == "DELETE" -> MockResponse().setBody("{}")
                    path == "/api/v1/send" -> MockResponse().setBody("""{"outbox":{"id":55,"status":"pending"}}""")
                    else -> MockResponse().setResponseCode(404).setBody("""{"detail":"not here"}""")
                }
            }
        }
        server.start()
        val ctx = ApplicationProvider.getApplicationContext<Application>()
        SessionStore(ctx).apply {
            server = this@MailActionsTest.server.url("/").toString()
            save("tok-123", "csrf-456", "me@example.com", "Me")
        }
        g = graphs.graph(ctx)
        // The inbox list as the pager would have cached it.
        runBlocking {
            val rows = Fixtures.conversations.mapIndexed { i, c ->
                ListItemEntity(inbox.key, c.id, i.toLong(), MailrJson.encodeToString(ConversationSummary.serializer(), c))
            }
            g.db.lists().replacePage(inbox.key, rows, RemoteKeyEntity(inbox.key, null, rows.size, System.currentTimeMillis()))
        }
    }

    @After fun tearDown() {
        idle()
        graphs.close()
        server.shutdown()
    }

    // ---------- helpers ----------

    private fun idle() = shadowOf(Looper.getMainLooper()).idle()

    private fun actionCalls(action: String): List<JsonObject> = requests
        .filter { it.path == "/api/v1/conversations/actions" }
        .map { MailrJson.parseToJsonElement(bodies[it]!!).jsonObject }
        .filter { it["action"]?.jsonPrimitive?.content == action }

    private fun awaitAction(action: String): JsonObject {
        eventually("POST conversations/actions $action") { actionCalls(action).isNotEmpty() }
        return actionCalls(action).single()
    }

    private fun JsonObject.ids() = this["ids"]!!.jsonArray.map { it.jsonPrimitive.long }

    private fun inboxIds(): List<Long> = runBlocking { g.db.lists().first(inbox.key, 50).map { it.conversationId } }

    private fun cached(id: Long): ConversationSummary = runBlocking {
        g.db.lists().rowsFor(listOf(id)).first { it.viewKey == inbox.key }
            .let { MailrJson.decodeFromString(ConversationSummary.serializer(), it.json) }
    }

    private fun request(method: String, path: String): RecordedRequest {
        eventually("$method $path") { requests.any { it.method == method && it.path == path } }
        return requests.last { it.method == method && it.path == path }
    }

    private fun jsonOf(r: RecordedRequest) = MailrJson.parseToJsonElement(bodies[r]!!).jsonObject

    // ---------- android.list ----------

    /** mail.archive on android.list: selection bar Archive (and the archive swipe, see SwipeTest). */
    @Test fun listArchiveFromSelectionBar() {
        val vm = graphs.vm { MailboxViewModel(g) }
        vm.toggle(3075); vm.toggle(3074)
        vm.act(vm.selection.value.toList(), "archive")
        val call = awaitAction("archive")
        assertEquals(setOf(3075L, 3074L), call.ids().toSet())
        assertTrue(vm.selection.value.isEmpty())
        eventually("archived rows leave the inbox") { 3075L !in inboxIds() && 3074L !in inboxIds() }
    }

    /** mail.archive on android.list by swipe: SwipeAction.ARCHIVE reaches the server. */
    @Test fun listArchiveBySwipe() {
        val vm = graphs.vm { MailboxViewModel(g) }
        vm.swipe(Fixtures.conversations[2], SwipeAction.ARCHIVE) {}
        assertEquals(listOf(3074L), awaitAction("archive").ids())
        eventually("row leaves the inbox") { 3074L !in inboxIds() }
    }

    /** mail.delete on android.list: the Delete button and the trash swipe send "trash". */
    @Test fun listDelete() {
        val vm = graphs.vm { MailboxViewModel(g) }
        vm.swipe(Fixtures.conversations[4], SwipeAction.TRASH) {}
        assertEquals(listOf(3060L), awaitAction("trash").ids())
        eventually("row leaves the inbox") { 3060L !in inboxIds() }
    }

    /** mail.read and mail.unread on android.list: the read swipe toggles by the row's state. */
    @Test fun listReadAndUnread() {
        val vm = graphs.vm { MailboxViewModel(g) }
        val unread = Fixtures.conversations[0]      // 3075, one unread message
        assertTrue(unread.unread)
        vm.swipe(unread, SwipeAction.READ) {}
        assertEquals(listOf(3075L), awaitAction("read").ids())
        eventually("row shows read") { cached(3075).unreadCount == 0 }

        val read = Fixtures.conversations[3]        // 3070, all read
        assertFalse(read.unread)
        vm.swipe(read, SwipeAction.READ) {}
        assertEquals(listOf(3070L), awaitAction("unread").ids())
        eventually("row shows unread") { cached(3070).unreadCount >= 1 }
    }

    /** mail.star on android.list: the row's star (App.kt wires it to act "star"/"unstar"). */
    @Test fun listStar() {
        val vm = graphs.vm { MailboxViewModel(g) }
        vm.act(listOf(3074), "star")
        assertEquals(listOf(3074L), awaitAction("star").ids())
        eventually("row shows the star") { cached(3074).starred }
    }

    /** mail.important and mail.mute on android.list: the selection bar's overflow menu. */
    @Test fun listImportantAndMute() {
        val vm = graphs.vm { MailboxViewModel(g) }
        val plain = Fixtures.conversations.first { !it.important && it.id in inboxIds() }
        vm.act(listOf(plain.id), "important")
        assertEquals(listOf(plain.id), awaitAction("important").ids())
        eventually("row shows important") { cached(plain.id).important }
        vm.act(listOf(plain.id), "mute")
        assertEquals(listOf(plain.id), awaitAction("mute").ids())
        eventually("muted row leaves the inbox") { plain.id !in inboxIds() }
    }

    /** mail.archive on android.list while offline: queued in pending_ops, optimistic change stands. */
    @Test fun listArchiveOfflineIsQueued() {
        offline = true
        val vm = graphs.vm { MailboxViewModel(g) }
        vm.act(listOf(3070), "archive")
        eventually("queued op") { runBlocking { g.db.pending().next(10) }.isNotEmpty() }
        val op = runBlocking { g.db.pending().next(10) }.single()
        assertEquals("POST", op.method)
        assertEquals("conversations/actions", op.path)
        val body = MailrJson.parseToJsonElement(op.body).jsonObject
        assertEquals("archive", body["action"]!!.jsonPrimitive.content)
        assertEquals(listOf(3070L), body.ids())
        assertTrue(3070L !in inboxIds())
    }

    // ---------- android.reader ----------

    private fun reader(): ConversationViewModel {
        val vm = graphs.vm { ConversationViewModel(g, 3066, null, null) }
        eventually("conversation loaded") { vm.detail.value != null }
        // Opening an unread conversation marks it read first; let that settle before acting.
        awaitAction("read")
        idle()
        return vm
    }

    /** mail.archive on android.reader: toolbar Archive. */
    @Test fun readerArchive() {
        val vm = reader()
        vm.act("archive", closes = true)
        assertEquals(listOf(3066L), awaitAction("archive").ids())
        eventually("row leaves the inbox") { 3066L !in inboxIds() }
    }

    /** Archive straight after opening an unread conversation: the mark-read must not bring the row back. */
    @Test fun readerArchiveWhileMarkingRead() {
        val vm = graphs.vm { ConversationViewModel(g, 3066, null, null) }
        vm.act("archive", closes = true)
        // Each action writes its change to the cached rows before it sends (MailRepository.act): once the
        // server has both requests, both writes are in, and none comes later.
        awaitAction("archive"); awaitAction("read")
        idle()
        assertFalse("the mark-read brought the row back", 3066L in inboxIds())
    }

    /** ACT mail.follow_up on android.reader: the "Remind me if no reply" choice sends follow_up with its time; cancel too. */
    @Test fun readerFollowUp() {
        val vm = reader()
        val at = AppClock.now().plusDays(3).withHour(9).withMinute(0).toOffsetDateTime().toString()
        vm.act("follow_up", until = at)
        val sent = awaitAction("follow_up")
        assertEquals(listOf(3066L), sent.ids())
        assertEquals(at, sent["until"]!!.jsonPrimitive.content)
        vm.act("cancel_follow_up")
        assertEquals(listOf(3066L), awaitAction("cancel_follow_up").ids())
    }

    /** mail.delete on android.reader: toolbar Delete sends "trash". */
    @Test fun readerDelete() {
        val vm = reader()
        vm.act("trash", closes = true)
        assertEquals(listOf(3066L), awaitAction("trash").ids())
        eventually("row leaves the inbox") { 3066L !in inboxIds() }
    }

    /** mail.spam on android.reader: menu "Report spam". */
    @Test fun readerSpam() {
        val vm = reader()
        vm.act("spam", closes = true)
        assertEquals(listOf(3066L), awaitAction("spam").ids())
        eventually("row leaves the inbox") { 3066L !in inboxIds() }
    }

    /**
     * mail.snooze on android.reader: the menu's Snooze opens the snooze sheet, whose pick App.kt
     * sends to MailboxViewModel.snooze for the open conversation.
     */
    @Test fun readerSnooze() {
        val vm = graphs.vm { MailboxViewModel(g) }
        val at = AppClock.now().plusDays(1).withHour(8).withMinute(0).withSecond(0).withNano(0)
        vm.snooze(listOf(3066), at)
        val call = awaitAction("snooze")
        assertEquals(listOf(3066L), call.ids())
        assertEquals(at.toOffsetDateTime(), java.time.OffsetDateTime.parse(call["until"]!!.jsonPrimitive.content))
        eventually("row leaves the inbox") { 3066L !in inboxIds() }
    }

    /** mail.important and mail.mute on android.reader: the reader's menu. */
    @Test fun readerImportantAndMute() {
        val vm = reader()
        vm.act("important")
        assertEquals(listOf(3066L), awaitAction("important").ids())
        vm.act("mute", closes = true)
        assertEquals(listOf(3066L), awaitAction("mute").ids())
        eventually("row leaves the inbox") { 3066L !in inboxIds() }
    }

    /** mail.label on android.reader: "Label as…" opens the label sheet, which adds and removes labels. */
    @Test fun readerLabel() {
        val vm = graphs.vm { MailboxViewModel(g) }
        vm.act(listOf(3066), "add_labels", listOf(13L))
        assertEquals(listOf(13L), awaitAction("add_labels")["label_ids"]!!.jsonArray.map { it.jsonPrimitive.long })
        eventually("label shows on the row") { 13L in cached(3066).labelIds }
        vm.act(listOf(3066), "remove_labels", listOf(11L))
        assertEquals(listOf(11L), awaitAction("remove_labels")["label_ids"]!!.jsonArray.map { it.jsonPrimitive.long })
        eventually("label leaves the row") { 11L !in cached(3066).labelIds }
    }

    private fun composer(args: ComposeArgs): ComposeViewModel {
        val vm = graphs.vm { ComposeViewModel(g, args) }
        eventually("composer loaded") { !vm.state.value.loading }
        return vm
    }

    /** mail.reply on android.reader: Reply opens the composer with the server's reply prefill. */
    @Test fun readerReply() {
        val vm = composer(ComposeArgs(replyToMessageId = 502, mode = "reply"))
        val call = request("GET", "/api/v1/compose/reply?message_id=502&mode=reply")
        assertNotNull(call)
        val s = vm.state.value
        assertEquals("Reply", s.title)
        assertEquals(listOf("chair@pads.example"), s.to.map { it.email })
        assertEquals("Re: PADS", s.subject)
        assertEquals(502L, s.inReplyTo)
        assertEquals(502L, vm.payload().inReplyToMessageId)
    }

    /** mail.forward on android.reader: Forward opens the composer in forward mode, no recipients. */
    @Test fun readerForward() {
        val vm = composer(ComposeArgs(replyToMessageId = 502, mode = "forward"))
        request("GET", "/api/v1/compose/reply?message_id=502&mode=forward")
        val s = vm.state.value
        assertEquals("Forward", s.title)
        assertTrue(s.to.isEmpty())
        assertEquals(502L, s.forwardOf)
        assertEquals(502L, vm.payload().forwardOfMessageId)
    }

    /** draft.save: closing a composer with content saves it as a draft (POST /drafts). */
    @Test fun draftSaveOnClose() {
        val vm = composer(ComposeArgs(replyToMessageId = 502, mode = "reply"))
        vm.setBody("Friday at 10 works.")
        vm.close()
        val call = jsonOf(request("POST", "/api/v1/drafts"))["payload"]!!.jsonObject
        assertEquals(502L, call["in_reply_to_message_id"]!!.jsonPrimitive.long)
        assertEquals("Re: PADS", call["subject"]!!.jsonPrimitive.content)
        assertTrue(call["html"]!!.jsonPrimitive.content.contains("Friday at 10 works."))
        eventually("draft id stored") { vm.state.value.draftId == 900L }
        assertEquals("Saved", vm.state.value.saveStatus)
    }

    /** draft.send: Send posts the payload to /send with no send_at. */
    @Test fun draftSend() {
        val vm = composer(ComposeArgs())
        assertTrue(vm.addRecipient(Field.TO, "alice@example.com"))
        vm.setSubject("Quarterly plan")
        vm.setBody("See you Friday.")
        vm.send()
        val call = jsonOf(request("POST", "/api/v1/send"))
        val payload = call["payload"]!!.jsonObject
        assertEquals("alice@example.com", payload["to"]!!.jsonArray.single().jsonObject["email"]!!.jsonPrimitive.content)
        assertEquals("Quarterly plan", payload["subject"]!!.jsonPrimitive.content)
        assertTrue(call["send_at"] == null || call["send_at"] is JsonNull)
    }

    /** draft.schedule: Schedule send posts to /send with the chosen send_at. */
    @Test fun draftSchedule() {
        val vm = composer(ComposeArgs(draftId = 77))
        assertEquals(listOf("alice@example.com"), vm.state.value.to.map { it.email })
        val at = AppClock.now().plusDays(1).withHour(8).withMinute(0).withSecond(0).withNano(0)
        vm.send(at)
        val call = jsonOf(request("POST", "/api/v1/send"))
        assertEquals(77L, call["draft_id"]!!.jsonPrimitive.long)
        assertEquals(at.toOffsetDateTime(), java.time.OffsetDateTime.parse(call["send_at"]!!.jsonPrimitive.content))
    }

    /** draft.discard: Discard deletes the saved draft on the server. */
    @Test fun draftDiscard() {
        val vm = composer(ComposeArgs(draftId = 77))
        vm.discard()
        request("DELETE", "/api/v1/drafts/77")
        assertNull(requests.firstOrNull { it.path == "/api/v1/send" })
    }

}
