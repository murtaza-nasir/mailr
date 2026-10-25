// ACT-SURFACES: android.reader
package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.time.AppClock
import android.app.Application
import android.os.Looper
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHostState
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.ConversationDetail
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.ui.conversation.ConversationActions
import io.github.murtazanasir.mailr.ui.conversation.ConversationScreen
import io.github.murtazanasir.mailr.ui.conversation.ConversationViewModel
import io.github.murtazanasir.mailr.ui.conversation.FromEmailPrefill
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.int
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
import org.junit.Assert.assertNull
import org.junit.Assert.fail
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.time.LocalDate
import java.time.LocalTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArrayList

/**
 * mail.to_task and mail.to_event on android.reader: the ⋮ menu's Create from email: "Task" and
 * "Event" ask the server for a suggestion (POST /calendar/from-message), the editor opens on it,
 * and Save sends the edited fields to POST /calendar/from-message/confirm. The saved item
 * carries the conversation (task.open_email / event.open_email).
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class FromEmailTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()

    private val app: Application = ApplicationProvider.getApplicationContext()
    private var server: MockWebServer? = null
    private var graph: AppGraph? = null
    private val requests = CopyOnWriteArrayList<RecordedRequest>()
    private val bodies = ConcurrentHashMap<RecordedRequest, String>()

    private val start: OffsetDateTime = AppClock.now(ZoneId.systemDefault()).toOffsetDateTime().plusDays(20).withHour(14).withMinute(0).withSecond(0).withNano(0)
    private val due = AppClock.today().plusDays(15)

    @After fun tearDown() {
        idle()
        graphs.close()
        server?.shutdown()
    }

    private fun idle() = shadowOf(Looper.getMainLooper()).idle()

    private fun start(): AppGraph {
        val s = MockWebServer()
        s.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val body = request.body.readUtf8()
                bodies[request] = body
                requests += request
                val path = request.path.orEmpty()
                val kind = runCatching { MailrJson.parseToJsonElement(body).jsonObject["kind"]?.jsonPrimitive?.content }.getOrNull()
                return when {
                    path == "/api/v1/conversations/3066" ->
                        MockResponse().setBody(MailrJson.encodeToString(ConversationDetail.serializer(), Fixtures.detail))
                    path == "/api/v1/conversations/actions" -> MockResponse().setBody("""{"ok":true,"affected":1}""")
                    path == "/api/v1/calendar/from-message" && kind == "task" -> MockResponse().setBody(
                        """{"kind":"task","message_id":502,"conversation_id":3066,"source":"model","draft":{"title":"Nominate for the PADS award",
                        "start":null,"end":null,"all_day":false,"location":"","notes":"Nominations close soon","due":"$due"}}""")
                    path == "/api/v1/calendar/from-message" -> MockResponse().setBody(
                        """{"kind":"event","message_id":502,"conversation_id":3066,"source":"model","draft":{"title":"PADS board call",
                        "start":"$start","end":"${start.plusHours(1)}","all_day":false,"location":"Zoom","notes":"","due":null}}""")
                    path == "/api/v1/calendar/from-message/confirm" && kind == "task" -> MockResponse().setBody(
                        """{"uid":"srv-task-1@mailr","summary":"Nominate Dana for the PADS award","due":"$due",
                        "notes":"Nominations close soon\n\nFrom email: Re: PADS"}""")
                    path == "/api/v1/calendar/from-message/confirm" -> MockResponse().setBody(
                        """{"uid":"srv-ev-1@mailr","summary":"PADS board call","start":"$start","end":"${start.plusHours(1)}",
                        "location":"Room 204"}""")
                    request.method == "PATCH" -> MockResponse().setBody("""{"ok":true}""")
                    else -> MockResponse().setResponseCode(404).setBody("""{"detail":"not here"}""")
                }
            }
        }
        s.start()
        server = s
        SessionStore(app).apply { server = s.url("/").toString(); save("tok", "csrf", "me@example.com", "Me") }
        return graphs.graph(app).also { graph = it }
    }

    private fun sent(method: String, path: String): JsonObject {
        eventually("$method $path") { requests.any { it.method == method && it.path == path } }
        return MailrJson.parseToJsonElement(bodies[requests.last { it.method == method && it.path == path }]!!).jsonObject
    }

    private fun reader(g: AppGraph): ConversationViewModel {
        val vm = graphs.vm { ConversationViewModel(g, 3066, null, null) }
        eventually("conversation loaded") { vm.detail.value != null }
        return vm
    }

    /** ACT mail.to_task, ACT mail.to_event on android.reader: both choices are in the reader's ⋮ menu. */
    @Test fun readerMenuOffersTaskAndEvent() {
        val picked = mutableListOf<String>()
        compose.setContent {
            MaterialTheme {
                ConversationScreen(Fixtures.detail, null, Fixtures.labels, emptySet(), emptyMap(), null, null,
                    tabletop = false, snackbar = SnackbarHostState(), actions = ConversationActions(
                        back = null, act = {}, toggle = {}, expandAll = {}, star = { _, _ -> }, reply = { _, _ -> },
                        openAttachment = { _, _ -> }, showImages = {}, snooze = {}, labels = {}, summarize = {}, unsubscribe = {},
                        mailto = {}, retry = {}, toTask = { picked += "task" }, toEvent = { picked += "event" }))
            }
        }
        compose.onNodeWithContentDescription("More").performClick()
        compose.onNodeWithText("Create from email").assertExists()
        compose.onNodeWithText("Task").performClick()
        compose.onNodeWithContentDescription("More").performClick()
        compose.onNodeWithText("Event").performClick()
        assertEquals(listOf("task", "event"), picked)
    }

    /** ACT mail.to_task: suggestion → editor (adjusted) → confirm; reminders follow as a PATCH; the task links to the email. */
    @Test fun emailToTask() {
        val g = start()
        val vm = reader(g)
        vm.fromEmail("task")
        val ask = sent("POST", "/api/v1/calendar/from-message")
        assertEquals(502L, ask["message_id"]!!.jsonPrimitive.long)     // the newest message that is not a draft
        assertEquals("task", ask["kind"]!!.jsonPrimitive.content)
        eventually("suggestion") { vm.fromEmail.value?.suggestion != null }

        // The editor opens on the suggestion.
        val s = FromEmailPrefill.task(vm.fromEmail.value!!.suggestion!!, null, "local-uid")
        assertEquals("Nominate for the PADS award", s.title)
        assertEquals("Nominations close soon", s.notes)
        assertEquals(due, s.dueDate)
        assertNull(s.dueTime)
        assertEquals(false, s.dirty)

        // The user adjusts it and saves.
        s.title = "Nominate Dana for the PADS award"
        s.reminders = listOf(1440)
        vm.saveFromEmail(task = s.draft())
        val confirm = sent("POST", "/api/v1/calendar/from-message/confirm")
        assertEquals(502L, confirm["message_id"]!!.jsonPrimitive.long)
        assertEquals("task", confirm["kind"]!!.jsonPrimitive.content)
        assertEquals("Nominate Dana for the PADS award", confirm["title"]!!.jsonPrimitive.content)
        assertEquals(due.toString(), confirm["due"]!!.jsonPrimitive.content)
        assertEquals("Nominations close soon", confirm["notes"]!!.jsonPrimitive.content)
        val patch = sent("PATCH", "/api/v1/tasks/srv-task-1@mailr")
        assertEquals(listOf(1440), patch["reminders"]!!.jsonArray.map { it.jsonPrimitive.int })
        assertEquals(setOf("reminders"), patch.keys)

        eventually("editor closed") { vm.fromEmail.value == null }
        val saved = runBlocking { g.calendar.task("srv-task-1@mailr") }!!
        assertEquals(3066L, saved.conversationId)
        assertEquals(listOf(1440), saved.reminders)
    }

    /** ACT mail.to_event: suggestion → editor (adjusted) → confirm; the default reminder follows as a PATCH; the event links to the email. */
    @Test fun emailToEvent() {
        val g = start()
        val vm = reader(g)
        vm.fromEmail("event")
        assertEquals("event", sent("POST", "/api/v1/calendar/from-message")["kind"]!!.jsonPrimitive.content)
        eventually("suggestion") { vm.fromEmail.value?.suggestion != null }

        val s = FromEmailPrefill.event(vm.fromEmail.value!!.suggestion!!, null, 10, "local-uid")
        assertEquals("PADS board call", s.title)
        assertEquals("Zoom", s.location)
        assertEquals(start.toLocalDate(), s.startDate)
        assertEquals(LocalTime.of(14, 0), s.startTime)
        assertEquals(60L, s.duration.toMinutes())
        assertEquals(listOf(10), s.reminders)
        assertEquals(false, s.dirty)

        s.location = "Room 204"
        vm.saveFromEmail(event = s.draft())
        val confirm = sent("POST", "/api/v1/calendar/from-message/confirm")
        assertEquals(502L, confirm["message_id"]!!.jsonPrimitive.long)
        assertEquals("event", confirm["kind"]!!.jsonPrimitive.content)
        assertEquals("PADS board call", confirm["title"]!!.jsonPrimitive.content)
        assertEquals(start.toInstant(), OffsetDateTime.parse(confirm["start"]!!.jsonPrimitive.content).toInstant())
        assertEquals(start.plusHours(1).toInstant(), OffsetDateTime.parse(confirm["end"]!!.jsonPrimitive.content).toInstant())
        assertEquals("Room 204", confirm["location"]!!.jsonPrimitive.content)
        // The default reminder chip was not touched: the confirm leaves reminders out (the server
        // writes the default) and no PATCH follows.
        assertEquals(null, confirm["reminders"])

        eventually("editor closed") { vm.fromEmail.value == null }
        assertEquals(0, requests.count { it.method == "PATCH" })
        val saved = runBlocking { g.calendar.eventsOf("srv-ev-1@mailr") }.single()
        assertEquals(3066L, saved.conversationId)
    }

    /** Closing the editor saves nothing. */
    @Test fun closingSavesNothing() {
        val g = start()
        val vm = reader(g)
        vm.fromEmail("task")
        eventually("suggestion") { vm.fromEmail.value?.suggestion != null }
        vm.closeFromEmail()
        idle()
        assertNull(vm.fromEmail.value)
        assertEquals(0, requests.count { it.path == "/api/v1/calendar/from-message/confirm" })
    }
}
