// ACT-SURFACES: android.reader
package io.github.murtazanasir.mailr

import android.app.Application
import android.os.Looper
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHostState
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.Address
import io.github.murtazanasir.mailr.data.api.Attachment
import io.github.murtazanasir.mailr.data.api.ConversationDetail
import io.github.murtazanasir.mailr.data.api.Invite
import io.github.murtazanasir.mailr.data.api.InviteAttendee
import io.github.murtazanasir.mailr.data.api.InviteConflict
import io.github.murtazanasir.mailr.data.api.InviteOrganizer
import io.github.murtazanasir.mailr.data.api.Itip
import io.github.murtazanasir.mailr.data.api.ItipWhen
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.OutboxRow
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.ui.conversation.ConversationActions
import io.github.murtazanasir.mailr.ui.conversation.ConversationScreen
import io.github.murtazanasir.mailr.ui.conversation.ConversationViewModel
import io.github.murtazanasir.mailr.ui.conversation.InviteCallbacks
import io.github.murtazanasir.mailr.ui.conversation.InviteText
import io.github.murtazanasir.mailr.ui.conversation.MoveTo
import io.github.murtazanasir.mailr.ui.conversation.ReaderExtrasUi
import io.github.murtazanasir.mailr.ui.conversation.meetingGuests
import io.github.murtazanasir.mailr.ui.conversation.plainSendError
import io.github.murtazanasir.mailr.ui.conversation.sendStateOf
import io.github.murtazanasir.mailr.ui.mail.MailPlace
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.time.ZoneId
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArrayList

/**
 * Android reader parity: invite.view and event.rsvp on the invitation card,
 * invite.apply_update / apply_reply / apply_unverified / counter and event.accept_proposal /
 * decline_proposal, mail.reply_suggestions, mail.move, note.from_email, note.link,
 * mail.schedule_meeting, draft.retry_send and invite.apply_cancel (Remove from calendar after a cancellation),
 * invite.remove_cancelled (that removal, with Undo) and invite.link_existing (Link to this invitation).
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class ReaderParityTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()

    private val app: Application = ApplicationProvider.getApplicationContext()
    private var server: MockWebServer? = null
    private val requests = CopyOnWriteArrayList<RecordedRequest>()
    private val bodies = ConcurrentHashMap<RecordedRequest, String>()
    private val zone = ZoneId.of("America/Chicago")

    @After fun tearDown() {
        idle()
        graphs.close()
        server?.shutdown()
    }

    private fun idle() = shadowOf(Looper.getMainLooper()).idle()

    private val inviteMsg = Fixtures.detail.messages[1].copy(id = 777, attachments = listOf(Attachment(4, "invite.ics", "text/calendar")))
    private val detail = ConversationDetail(Fixtures.detail.conversation, listOf(Fixtures.detail.messages[0], inviteMsg))

    private val invite = Invite(method = "REQUEST", uid = "board@example.com", summary = "PADS board call",
        start = "2026-10-20T19:00:00Z", end = "2026-10-20T20:00:00Z", location = "Room 204",
        organizer = InviteOrganizer("chair@pads.example", "Program Chair"),
        attendees = listOf(InviteAttendee("me@example.com", "Alex", "NEEDS-ACTION"), InviteAttendee("dana@example.com", "Dana")),
        myStatus = "NEEDS-ACTION", conflicts = listOf(InviteConflict("Team sync"), InviteConflict("1:1")))

    private fun start(): AppGraph {
        val s = MockWebServer()
        s.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                bodies[request] = request.body.readUtf8()
                requests += request
                val path = request.path.orEmpty()
                return when {
                    path == "/api/v1/conversations/3066" -> MockResponse().setBody(MailrJson.encodeToString(ConversationDetail.serializer(), detail))
                    path == "/api/v1/messages/777/invite" -> MockResponse().setBody(MailrJson.encodeToString(Invite.serializer(), invite))
                    path.startsWith("/api/v1/messages/777/reply-suggestions") ->
                        MockResponse().setBody("""{"status":"ready","replies":["Sounds good.","I can make it.","Thanks!"]}""")
                    path.startsWith("/api/v1/messages/") && path.contains("/invite") -> MockResponse().setResponseCode(404).setBody("""{"detail":"no"}""")
                    path == "/api/v1/calendar/invite-reply" -> MockResponse().setBody("""{"response":"accepted","calendar":{"ok":true},"outbox":{"id":5}}""")
                    path == "/api/v1/notes/from-email" -> MockResponse().setBody("""{"note":{"id":"nAbc123","title":"PADS","kind":"checklist"}}""")
                    path == "/api/v1/notes/nXyz789/links" -> MockResponse().setBody("""{"ok":true}""")
                    path == "/api/v1/outbox/41/retry" -> MockResponse().setBody("""{"outbox":{"id":41,"status":"queued"}}""")
                    path == "/api/v1/messages/777/itip/remove" -> MockResponse().setBody("""{"ok":true,"undo":{"method":"POST","path":"/messages/777/itip/restore"}}""")
                    path == "/api/v1/messages/777/invite/link" -> MockResponse().setBody("""{"event":{"uid":"inv-1"},"undo":{"method":"POST","path":"/messages/777/invite/unlink"}}""")
                    path.startsWith("/api/v1/outbox") -> MockResponse().setBody("[]")
                    path == "/api/v1/conversations/actions" -> MockResponse().setBody("""{"ok":true,"affected":1}""")
                    else -> MockResponse().setResponseCode(404).setBody("""{"detail":"not here"}""")
                }
            }
        }
        s.start()
        server = s
        SessionStore(app).apply { server = s.url("/").toString(); save("tok", "csrf", "me@example.com", "Me") }
        return graphs.graph(app)
    }

    private fun sent(method: String, path: String): JsonObject {
        eventually("$method $path") { requests.any { it.method == method && it.path == path } }
        return MailrJson.parseToJsonElement(bodies[requests.last { it.method == method && it.path == path }]!!).jsonObject
    }

    private fun screen(extras: ReaderExtrasUi, actions: ConversationActions) {
        compose.setContent {
            MaterialTheme {
                ConversationScreen(detail, null, Fixtures.labels, setOf(777L), emptyMap(), null, null, tabletop = false,
                    snackbar = SnackbarHostState(), actions = actions, extras = extras)
            }
        }
    }

    private fun actions(picked: MutableList<String>) = ConversationActions(
        back = null, act = {}, toggle = {}, expandAll = {}, star = { _, _ -> }, reply = { _, _ -> },
        openAttachment = { _, _ -> }, showImages = {}, snooze = {}, labels = {}, summarize = {}, unsubscribe = {},
        mailto = {}, retry = {},
        invite = InviteCallbacks(answer = { m, r, send -> picked += "${m.id}:$r:$send" }),
        moveTo = { picked += "move" }, noteFromEmail = { picked += "note:$it" }, linkNote = { picked += "link" },
        meeting = { picked += "meeting" }, pickReply = { m, t -> picked += "reply:${m.id}:$t" },
        retrySend = { picked += "retry:${it.id}" })

    /** ACT invite.view, ACT event.rsvp: the card shows the invitation, the conflict line, and Yes · Maybe · No. */
    @Test fun inviteCardAnswers() {
        val picked = mutableListOf<String>()
        screen(ReaderExtrasUi(invites = mapOf(777L to invite)), actions(picked))
        compose.onNodeWithTag("invite-card").assertIsDisplayed()
        compose.onNodeWithText("PADS board call").assertExists()
        compose.onNodeWithTag("invite-conflict").assertExists()
        compose.onNodeWithText("You have Team sync and 1 more at that time", substring = true).assertExists()
        compose.onNodeWithText("Going?").assertExists()
        compose.onNodeWithTag("rsvp-accepted").performClick()
        compose.onNodeWithText("Add to calendar").performClick()
        assertEquals(listOf("777:accepted:true", "777:tentative:false"), picked)
    }

    /** ACT invite.link_existing: my copy under another UID is In your calendar, with Link to this invitation. */
    @Test fun inviteCardLinksACopy() {
        val linked = mutableListOf<String>()
        val copy = invite.copy(inCalendar = true, conflicts = emptyList(),
            calendarMatch = io.github.murtazanasir.mailr.data.api.InviteMatch("fresh-uuid", "time_title"))
        compose.setContent {
            MaterialTheme {
                ConversationScreen(detail, null, Fixtures.labels, setOf(777L), emptyMap(), null, null, tabletop = false,
                    snackbar = SnackbarHostState(), actions = actions(mutableListOf()).copy(
                        invite = InviteCallbacks(linkInvite = { m, uid -> linked += "${m.id}:$uid" })),
                    extras = ReaderExtrasUi(invites = mapOf(777L to copy)))
            }
        }
        compose.onNodeWithText("In your calendar").assertExists()
        compose.onNodeWithTag("invite-conflict").assertDoesNotExist()
        compose.onNodeWithTag("invite-link").performClick()
        assertEquals(listOf("777:fresh-uuid"), linked)
    }

    /** ACT mail.reply_suggestions, ACT draft.retry_send: the chips under the newest message, and Not sent with Retry on a draft. */
    @Test fun replyChipsAndNotSent() {
        val picked = mutableListOf<String>()
        val draft = Fixtures.detail.messages[0].copy(id = 900, draft = true, sentByMe = true)
        val withDraft = detail.copy(messages = detail.messages + draft)
        compose.setContent {
            MaterialTheme {
                ConversationScreen(withDraft, null, Fixtures.labels, setOf(900L), emptyMap(), null, null, tabletop = false,
                    snackbar = SnackbarHostState(), actions = actions(picked), extras = ReaderExtrasUi(
                        replies = 777L to listOf("Sounds good.", "Thanks!"),
                        outbox = listOf(OutboxRow(41, "failed", "Re: PADS", error = "550 5.1.1 user unknown", draftId = 900))))
            }
        }
        compose.onNodeWithTag("send-state").assertExists()
        compose.onNodeWithText("Not sent. An address was refused. Check the recipients.", substring = true).assertExists()
        compose.onNodeWithText("Retry").performClick()
        compose.onNodeWithText("Thanks!").performClick()
        assertEquals(listOf("retry:41", "reply:777:Thanks!"), picked)
    }

    /** ACT mail.move, ACT note.from_email, ACT note.link, ACT mail.schedule_meeting: the reader's ⋮ menu. */
    @Test fun menuOffersMoveNoteLinkAndMeeting() {
        val picked = mutableListOf<String>()
        screen(ReaderExtrasUi(), actions(picked))
        for (label in listOf("Meeting with these people", "Note", "Checklist", "Link to a note…", "Move to…")) {
            compose.onNodeWithContentDescription("More").performClick()
            compose.onNodeWithText(label).performClick()
        }
        assertEquals(listOf("meeting", "note:text", "note:checklist", "link", "move"), picked)
    }

    /** The view model loads the invitation and the suggested replies, and Yes posts the answer. */
    @Test fun viewModelLoadsAndAnswers() {
        val g = start()
        val vm = graphs.vm { ConversationViewModel(g, 3066, null, null) }
        eventually("invite") { vm.extras.invites.value[777L] != null }
        eventually("replies") { vm.extras.replies.value != null }
        assertEquals(777L, vm.extras.replies.value!!.first)
        vm.extras.answer(777, "accepted")
        val body = sent("POST", "/api/v1/calendar/invite-reply")
        assertEquals(777L, body["message_id"]!!.jsonPrimitive.long)
        assertEquals("accepted", body["response"]!!.jsonPrimitive.content)
        assertEquals("true", body["send"]!!.jsonPrimitive.content)
        // ACT note.from_email, ACT note.link
        var opened: String? = null
        vm.extras.note("checklist") { opened = it }
        val made = sent("POST", "/api/v1/notes/from-email")
        assertEquals(3066L, made["conversation_id"]!!.jsonPrimitive.long)
        assertEquals("checklist", made["kind"]!!.jsonPrimitive.content)
        vm.extras.link("nXyz789", "Trip")
        val link = sent("POST", "/api/v1/notes/nXyz789/links")
        assertEquals("conversation", link["target_kind"]!!.jsonPrimitive.content)
        assertEquals(3066L, link["target_ref"]!!.jsonObject["conversation_id"]!!.jsonPrimitive.long)
        assertNull(opened)
        // ACT invite.remove_cancelled: the copy is kept on the server for Undo (never a plain DELETE).
        // The answer above still holds the card busy until its reload lands (the button is disabled
        // then, and a call while busy is dropped), so wait as the screen does.
        eventually("answer done") { 777L !in vm.extras.busy.value }
        vm.extras.removeCancelled(777, invite.copy(cancelled = true, inCalendar = true))
        eventually("remove") { requests.any { it.method == "POST" && it.path == "/api/v1/messages/777/itip/remove" } }
        assertTrue(requests.none { it.method == "DELETE" })
        eventually("idle") { 777L !in vm.extras.busy.value }
        // ACT invite.link_existing
        vm.extras.linkInvite(777, "fresh-uuid")
        assertEquals("fresh-uuid", sent("POST", "/api/v1/messages/777/invite/link")["uid"]!!.jsonPrimitive.content)
        // ACT draft.retry_send
        vm.extras.retry(OutboxRow(41, "failed"))
        eventually("retry") { requests.any { it.path == "/api/v1/outbox/41/retry" } }
        // ACT mail.move: Move to a label
        vm.act("move", labelIds = listOf(13L), closes = true)
        eventually("move") { requests.any { it.path == "/api/v1/conversations/actions" && "\"move\"" in bodies[it].orEmpty() } }
        val move = MailrJson.parseToJsonElement(bodies[requests.last { it.path == "/api/v1/conversations/actions" && "\"move\"" in bodies[it].orEmpty() }]!!).jsonObject
        assertEquals("move", move["action"]!!.jsonPrimitive.content)
        assertEquals(13L, move["label_id"]!!.jsonPrimitive.long)
    }

    /** The card's words (the web's inviteLogic.ts): updates, guests' answers, proposals, kept-back changes. */
    @Test fun inviteWords() {
        assertEquals("Tue 20 Oct, 2:00 PM – 3:00 PM", InviteText.whenText("2026-10-20T19:00:00Z", "2026-10-20T20:00:00Z", zone))
        assertEquals("Tue 20 Oct · all day", InviteText.whenText("2026-10-20", "2026-10-21", zone))
        assertNull(InviteText.conflictText(emptyList()))
        // The organizer's address always shows beside its name.
        assertEquals("IT Helpdesk (helpdesk@example.com) <x@evil.test>",
            InviteText.organizerLine("IT Helpdesk (helpdesk@example.com)", "x@evil.test"))
        assertEquals("chair@example.com", InviteText.organizerLine("", "chair@example.com"))
        assertEquals("Chair <chair@example.com>", InviteText.organizerLine("Chair\u202e", "chair\u200b@example.com"))
        assertEquals("You have Team sync at that time", InviteText.conflictText(listOf("Team sync")))
        val update = invite.copy(itip = Itip(applied = true, detail = "Updated in your calendar", method = "REQUEST", timeChanged = true,
            previous = ItipWhen(start = "2026-10-19T19:00:00Z")))
        // ACT invite.apply_update
        assertEquals("Time changed: was Mon 19 Oct, 2:00 PM", InviteText.previousLine(update, zone))
        // ACT invite.apply_reply
        val reply = invite.copy(itip = Itip(applied = true, detail = "Dana accepted", method = "REPLY", attendee = "dana@example.com", partstat = "ACCEPTED"))
        assertEquals("Dana accepted — calendar updated", InviteText.outcome(reply))
        // ACT invite.counter, event.accept_proposal and event.decline_proposal answer it
        val counter = invite.copy(itip = Itip(method = "COUNTER", counter = true, proposed = ItipWhen(start = "2026-10-22T20:00:00Z")))
        assertEquals("Dana proposes Thu 22 Oct, 3:00 PM", InviteText.proposalText(counter, "dana@example.com", zone))
        assertNull(InviteText.outcome(counter))
        // ACT invite.apply_unverified: a change kept back says so (the card adds Apply anyway).
        val kept = invite.copy(itip = Itip(method = "REQUEST", detail = "Not applied: the sender could not be verified", pending = true))
        assertEquals("Not applied: the sender could not be verified", InviteText.outcome(kept))
        assertTrue(InviteText.answerable(invite))
        assertTrue(!InviteText.answerable(invite.copy(cancelled = true)))
        assertEquals("You said yes", InviteText.statusLabel("ACCEPTED"))
        assertEquals("Maybe sent to organiser. Couldn't update your calendar.", InviteText.answered("tentative", true))
    }

    @Test fun moveTargetsGuestsAndSendStates() {
        val inbox = MoveTo.targets(Fixtures.labels, MailPlace.OTHER)
        assertEquals(listOf("Archive", "Spam", "Trash"), inbox.take(3).map { it.label })
        assertEquals(listOf("Job Applications", "Research", "Research/PADS 2027", "Travel"), inbox.drop(3).map { it.label })
        assertEquals("Inbox", MoveTo.targets(Fixtures.labels, MailPlace.TRASH).first().label)
        assertEquals(listOf("Travel"), MoveTo.targets(Fixtures.labels, MailPlace.OTHER, "trav").map { it.label })
        val guests = meetingGuests(detail, setOf("me@example.com"))
        assertEquals(listOf("info@pads.example", "chair@pads.example", "board@pads.example"), guests.map { it.email })
        val draft = Fixtures.detail.messages[0].copy(id = 900, draft = true)
        assertEquals(41L, sendStateOf(draft, listOf(OutboxRow(41, "failed", draftId = 900)))!!.id)
        assertNull(sendStateOf(draft, listOf(OutboxRow(41, "sent", draftId = 900))))
        assertEquals("Couldn't reach the mail server.", plainSendError("Connection timed out"))
        assertEquals(Address("x@example.com"), Address("x@example.com"))
    }
}
