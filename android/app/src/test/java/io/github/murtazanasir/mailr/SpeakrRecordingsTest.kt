// ACT-SURFACES: android.reader android.calendar android.recording
package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.testing.noCallsInFlight
import io.github.murtazanasir.mailr.time.AppClock
import android.app.Application
import android.os.Looper
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHostState
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.ExperimentalTestApi
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.hasClickAction
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.hasScrollAction
import androidx.compose.ui.test.performScrollToNode
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.printToString
import io.github.murtazanasir.mailr.data.agent.AgentClient
import io.github.murtazanasir.mailr.data.agent.AskAbout
import io.github.murtazanasir.mailr.data.api.Attendee
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.Citation
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.ui.assistant.AssistantCallbacks
import io.github.murtazanasir.mailr.ui.assistant.AssistantScreen
import io.github.murtazanasir.mailr.ui.assistant.AssistantState
import io.github.murtazanasir.mailr.ui.assistant.ChatTurn
import io.github.murtazanasir.mailr.ui.calendar.EventDetail
import io.github.murtazanasir.mailr.ui.conversation.BodyState
import io.github.murtazanasir.mailr.ui.conversation.ConversationActions
import io.github.murtazanasir.mailr.ui.conversation.ConversationScreen
import io.github.murtazanasir.mailr.ui.recordings.ActionItemsReview
import io.github.murtazanasir.mailr.ui.recordings.EventRecordings
import io.github.murtazanasir.mailr.ui.recordings.LocalRecordings
import io.github.murtazanasir.mailr.ui.recordings.RecordingMailCard
import io.github.murtazanasir.mailr.ui.recordings.RecordingView
import io.github.murtazanasir.mailr.ui.recordings.Recordings
import io.github.murtazanasir.mailr.ui.recordings.TasksTarget
import io.github.murtazanasir.mailr.ui.recordings.fmtT
import io.github.murtazanasir.mailr.ui.recordings.speakrAt
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.cancel
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.time.ZonedDateTime

/**
 * Recordings (Speakr) on Android, behind the Settings switch: off asks nothing of /speakr; the
 * recording sheet (recording.view, recording.open_speakr); the event detail's Recording section
 * with Yes / No (recording.link, recording.unlink) and Last time (event.last_recording); citation
 * chips; Ask assistant (recording.ask); the follow-up draft (recording.follow_up) and the action
 * items (recording.to_tasks); the reader's card for a Speakr notification email.
 */
@OptIn(ExperimentalTestApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class SpeakrRecordingsTest {
    @get:Rule val compose = createComposeRule()
    private val f = SpeakrFake()
    private val scope = MainScope()
    private val opened = mutableListOf<String>()
    private val drafts = mutableListOf<Long>()
    private val asked = mutableListOf<AskAbout.Recording>()
    private val said = mutableListOf<String>()
    private lateinit var rec: Recordings

    private val start = AppClock.now().plusDays(1).withHour(15).withMinute(0).withSecond(0).withNano(0)
    private val event = CalendarEvent("ev-1", "Weekly sync", start.toOffsetDateTime().toString(), start.plusHours(1).toOffsetDateTime().toString(),
        attendeeList = listOf(Attendee("dana@example.com", "Dana Ortiz", "ACCEPTED")))

    @Before fun setUp() {
        f.start()
        rec = Recordings(f.api, scope, openUrl = { opened += it }, openDraft = { drafts += it }, ask = { asked += it },
            openLink = {}, notify = { t, _ -> said += t })
        f.on("GET", "speakr/status") { _, _ -> f.json("""{"connected":true,"connectors":[{"id":3,"name":"Speakr","base_url":"https://speakr.example.org"}]}""") }
        // Newest first: the transcript's handler goes after the recording's, which is its prefix.
        f.on("GET", "speakr/recordings/3/12") { _, _ -> f.json(SpeakrFake.RECORDING) }
        f.on("GET", "speakr/recordings/3/12/transcript") { _, _ -> f.json(SpeakrFake.TRANSCRIPT) }
        f.on("GET", "speakr/folders") { _, _ -> f.json(SpeakrFake.FOLDERS) }
        f.on("GET", "speakr/tags") { _, _ -> f.json(SpeakrFake.TAGS) }
    }

    @After fun tearDown() {
        scope.cancel()
        f.stop()
    }

    /** Waits for [cond], running the main looper (network answers come back there). */
    private fun settle(what: String, cond: () -> Boolean) = eventually(what, idle = {
        compose.mainClock.advanceTimeBy(100)
        compose.waitForIdle()
        shadowOf(Looper.getMainLooper()).idle()
    }, detail = {
        val tree = compose.onRoot(useUnmergedTree = true).printToString()
        java.io.File("build/speakr-tree-${what.replace(' ', '-')}.txt").writeText(tree)
        "requests " + f.requests.map { it.first.method + " " + it.first.path } + "\n" + tree
    }, check = cond)

    private fun shown(text: String) = compose.onAllNodesWithText(text, substring = true).fetchSemanticsNodes().isNotEmpty()

    private val eventItem = SpeakrFake.RECORDING.replace("\"candidates\":[]",
        "\"candidates\":[],\"summary_excerpt\":\"## Decisions\\n- Move the budget review to Friday\"," +
            "\"link\":{\"id\":41,\"connector_id\":3,\"recording_id\":12,\"kind\":\"event\",\"target\":\"ev-1\",\"occurrence\":null,\"state\":\"suggested\",\"score\":0.8,\"source\":\"auto\"}")

    /** The switch off: the event detail and the reader show nothing about recordings and ask /speakr nothing. */
    @Test fun switchOffAsksNothing() {
        f.on("GET", "speakr/events/recordings") { _, _ -> f.json("""{"connected":true,"items":[$eventItem]}""") }
        f.on("GET", "speakr/events/last-time") { _, _ -> f.json("""{"connected":true,"item":null}""") }
        f.on("GET", "speakr/mail-card") { _, _ -> f.json("""{"connected":true,"items":[]}""") }
        var on by mutableStateOf(false)
        compose.setContent {
            MaterialTheme {
                val enabled = io.github.murtazanasir.mailr.ui.recordings.rememberSpeakrOn(on, f.api)
                CompositionLocalProvider(LocalRecordings provides if (enabled) rec else null) {
                    EventDetail(event, null, emptyList(), 9, null, {}, {}, {})
                }
            }
        }
        // A second of the screen's time (a delayed load would start in it), then every request it made has arrived.
        repeat(10) { compose.mainClock.advanceTimeBy(100); compose.waitForIdle() }
        noCallsInFlight(f.client.http, "the event detail with the switch off")
        assertTrue("no request to /speakr while the switch is off", f.speakrRequests().isEmpty())
        compose.onNodeWithText("Is this the recording of this meeting?").assertDoesNotExist()
        compose.onNodeWithText("Record").assertDoesNotExist()
        // On: the status, then the event's recordings.
        on = true
        settle("the Recording section") { shown("Is this the recording of this meeting?") }
        assertEquals(1, f.sent("GET", "speakr/status").size)
        // recording.record: the detail offers Record while the switch is on.
        compose.onNodeWithText("Record").assertExists()
        assertTrue(f.sent("GET", "speakr/events/recordings").isNotEmpty())
    }

    /** Settings: the switch shows only with a Speakr connector, and it is off by default. */
    @Test fun settingsSwitch() {
        var prefs by mutableStateOf(io.github.murtazanasir.mailr.data.settings.Prefs())
        var connected by mutableStateOf(false)
        assertEquals(false, prefs.recordings)
        compose.setContent {
            MaterialTheme {
                io.github.murtazanasir.mailr.ui.settings.SettingsScreen(prefs, "Me", "me@example.com", "https://mail.example.org", emptyList(),
                    io.github.murtazanasir.mailr.ui.settings.SettingsCallbacks(back = {}, update = { fn -> prefs = fn(prefs) }, revoke = {}, signOut = {}, openWeb = {}),
                    speakrConnected = connected)
            }
        }
        compose.onNodeWithText("Recordings (Speakr)").assertDoesNotExist()
        connected = true
        // The settings list composes rows lazily: scroll the list to the row first.
        compose.onNode(hasScrollAction()).performScrollToNode(hasText("Recordings (Speakr)"))
        compose.onNodeWithText("Recordings (Speakr)").performClick()
        assertEquals(true, prefs.recordings)
    }

    /** The reader with the switch off asks nothing; on, a Speakr notification email gets its card. */
    @Test fun readerCardOnlyWhenOn() {
        f.on("GET", "speakr/mail-card") { _, _ -> f.json("""{"connected":true,"items":[${SpeakrFake.RECORDING.replace("\"candidates\":[]", "\"candidates\":[],\"summary_excerpt\":\"Move the budget review to Friday.\"")}]}""") }
        var provide by mutableStateOf(false)
        compose.setContent {
            MaterialTheme {
                CompositionLocalProvider(LocalRecordings provides if (provide) rec else null) {
                    ConversationScreen(Fixtures.detail, null, Fixtures.labels, setOf(502L), mapOf(502L to BodyState.Loading), null, null,
                        tabletop = false, snackbar = SnackbarHostState(), actions = ConversationActions(
                            back = null, act = {}, toggle = {}, expandAll = {}, star = { _, _ -> }, reply = { _, _ -> },
                            openAttachment = { _, _ -> }, showImages = {}, snooze = {}, labels = {}, summarize = {}, unsubscribe = {},
                            mailto = {}, retry = {}))
                }
            }
        }
        repeat(5) { compose.mainClock.advanceTimeBy(100); compose.waitForIdle(); shadowOf(Looper.getMainLooper()).idle() }
        noCallsInFlight(f.client.http, "the reader without Recordings")
        assertTrue(f.speakrRequests().isEmpty())
        provide = true
        settle("the recording card") { shown("Move the budget review to Friday.") }
        assertTrue(f.sent("GET", "speakr/mail-card").first().first.path!!.contains("message_id=502"))
        compose.onNodeWithText("Open in Speakr").performScrollTo().performClick()
        assertEquals("https://speakr.example.org/recordings/12", opened.last())
    }

    /** recording.view: summary, transcript with times (a time opens Speakr there), notes, local-only; recording.open_speakr. */
    @Test fun sheetShowsTheRecording() {
        compose.setContent { MaterialTheme { RecordingView(rec, 3, 12, null) } }
        settle("the summary") { shown("Move the") }
        compose.onNodeWithText("Local only").assertIsDisplayed()
        assertTrue(shown("Dana Ortiz, Evan Li"))
        compose.onNodeWithText("Transcript").performClick()
        settle("the transcript") { shown("I'll send the Q3 numbers") }
        compose.onNodeWithText("12:34").performClick()
        assertEquals("https://speakr.example.org/recordings/12?t=754", opened.last())
        compose.onNodeWithText("Notes").performClick()
        settle("the notes") { shown("Bring the draft.") }
        compose.onNodeWithText("Open in Speakr").performClick()
        assertEquals("https://speakr.example.org/recordings/12", opened.last())
    }

    /** A citation's moment opens the transcript there, and Play in Speakr goes to that moment. */
    @Test fun sheetAtACitedMoment() {
        compose.setContent { MaterialTheme { RecordingView(rec, 3, 12, 754.0) } }
        settle("the transcript at the moment") { shown("I'll send the Q3 numbers") }
        compose.onNodeWithText("Play in Speakr at 12:34").performClick()
        assertEquals("https://speakr.example.org/recordings/12?t=754", opened.last())
        assertEquals("12:34", fmtT(754.0))
        assertEquals("https://speakr.example.org/recordings/12?t=60", speakrAt("https://speakr.example.org/recordings/12?t=3", 60.0))
    }

    /** recording.ask: Ask assistant sends focused_recording {connector_id, recording_id}. */
    @Test fun askAssistantFromTheSheet() {
        compose.setContent { MaterialTheme { RecordingView(rec, 3, 12, null) } }
        settle("the sheet") { shown("Ask Ambrose") }
        compose.onNodeWithText("Ask Ambrose").performClick()
        assertEquals(AskAbout.Recording(3, 12, "Weekly sync"), asked.single())
        val body = AgentClient.chatBody("What did we decide?", null, null, null, asked.single())
        val focused = body["context"]!!.jsonObject["focused_recording"]!!.jsonObject
        assertEquals(3L, focused["connector_id"]!!.jsonPrimitive.long)
        assertEquals(12L, focused["recording_id"]!!.jsonPrimitive.long)
    }

    /** The event section: Yes confirms the suggestion (recording.link), No dismisses it (recording.unlink); Last time (event.last_recording). */
    @Test fun eventSectionConfirm() {
        f.on("GET", "speakr/events/recordings") { _, _ -> f.json("""{"connected":true,"items":[$eventItem]}""") }
        f.on("GET", "speakr/events/last-time") { _, _ ->
            f.json("""{"connected":true,"item":${SpeakrFake.RECORDING.replace("\"candidates\":[]", "\"candidates\":[],\"summary_line\":\"The review moved to Friday.\"").replace("\"recording_id\":12", "\"recording_id\":9")}}""")
        }
        f.on("POST", "speakr/links") { _, _ -> f.json("""{"id":41,"connector_id":3,"recording_id":12,"kind":"event","target":"ev-1","state":"confirmed"}""") }
        f.on("DELETE", "speakr/links/41") { _, _ -> f.json("""{"id":41,"connector_id":3,"recording_id":12,"kind":"event","target":"ev-1","state":"dismissed"}""") }
        compose.setContent { MaterialTheme { EventRecordings(rec, "ev-1", "20260929T150000Z") } }
        settle("the suggestion") { shown("Is this the recording of this meeting?") && shown("Last time: Weekly sync") }
        assertTrue(shown("The review moved to Friday."))
        compose.onNodeWithText("Yes").performClick()
        settle("the link") { f.sent("POST", "speakr/links").isNotEmpty() && said.isNotEmpty() }
        val body = f.body(f.sent("POST", "speakr/links").single())
        assertEquals("event", body["kind"]!!.jsonPrimitive.content)
        assertEquals("ev-1", body["target"]!!.jsonPrimitive.content)
        assertEquals(12L, body["recording_id"]!!.jsonPrimitive.long)
        assertEquals("Recording linked to this meeting.", said.last())
        settle("reloaded") { f.sent("GET", "speakr/events/recordings").size >= 2 }
        compose.onNodeWithText("No").performClick()
        settle("the dismissal") { f.sent("DELETE", "speakr/links/41").isNotEmpty() }
        // Last time opens the sheet on that recording.
        compose.onNodeWithText("Last time: Weekly sync", substring = true).performClick()
        assertEquals(9L, rec.sheet?.rid)
    }

    /** recording.follow_up: a draft on the server, compose opens on it; nothing is sent. */
    @Test fun followUpDraft() {
        f.on("POST", "speakr/recordings/3/12/follow-up") { _, _ -> f.json("""{"draft_id":4503599627370777,"conversation_id":null,"to":["dana@example.com"],"reply":false}""") }
        compose.setContent { MaterialTheme { RecordingView(rec, 3, 12, null) } }
        settle("the sheet") { shown("Follow-up email") }
        compose.onNodeWithText("Follow-up email").performClick()
        settle("the draft") { drafts.isNotEmpty() }
        assertEquals(4503599627370777L, drafts.single())
        val body = f.body(f.sent("POST", "speakr/recordings/3/12/follow-up").single())
        assertEquals("summary", body["mode"]!!.jsonPrimitive.content)
        // The suggested event link goes with it.
        assertEquals("ev-1", body["event_uid"]!!.jsonPrimitive.content)
        assertEquals("Follow-up drafted. Nothing is sent until you send it.", said.last())
        assertTrue(f.requests.none { it.first.path.orEmpty().contains("/send") })
    }

    /** recording.to_tasks: the review list (mine ticked, others not, already added locked); the ticked ones become tasks. */
    @Test fun actionItemsBecomeTasks() {
        f.on("POST", "speakr/recordings/3/12/action-items") { _, _ -> f.json(SpeakrFake.ACTION_ITEMS) }
        f.on("POST", "speakr/recordings/3/12/tasks") { _, _ -> f.json("""{"created":[{"uid":"t-1","summary":"Send the Q3 numbers to Dana"},{"uid":"t-2","summary":"Book the room for Friday"}],"pending":0}""") }
        val target = TasksTarget(3, 12, "Weekly sync", io.github.murtazanasir.mailr.data.api.EventRef("ev-1", null))
        rec.showTasks(target)
        compose.setContent { MaterialTheme { ActionItemsReview(rec, target) } }
        settle("the items") { shown("Send the Q3 numbers to Dana") }
        assertTrue(shown("Already added"))
        compose.onNodeWithText("Add 1 task").assertIsDisplayed()
        compose.onNodeWithText("Book the room for Friday").performClick()
        compose.onNodeWithText("Add 2 tasks").performClick()
        settle("the tasks") { said.isNotEmpty() }
        val items = f.body(f.sent("POST", "speakr/recordings/3/12/tasks").single())["items"]!!.jsonArray
        assertEquals(listOf("Send the Q3 numbers to Dana", "Book the room for Friday"), items.map { it.jsonObject["text"]!!.jsonPrimitive.content })
        assertEquals("2 tasks added.", said.last())
        assertEquals(null, rec.tasks)
    }

    /**
     * Recording citations: chips with the title and moment; a tap opens the sheet there (recording.view).
     * ACT recording.view @android.assistant: the chip in the assistant's chat.
     */
    @Test fun citationChips() {
        val c = Citation(1, kind = "recording", subject = "Weekly sync", from = "Evan Li", quote = "I'll send the Q3 numbers by Thursday.",
            recordingId = 12, connectorId = 3, t = 754.0, title = "Weekly sync", speaker = "Evan Li",
            url = "https://speakr.example.org/recordings/12?t=754")
        val parsed = MailrJson.decodeFromString(Citation.serializer(),
            """{"n":1,"kind":"recording","recording_id":12,"connector_id":3,"title":"Weekly sync","t":754,"start":754,"speaker":"Evan Li","verified":true,"url":"x"}""")
        assertTrue(parsed.isRecording)
        val picked = mutableListOf<Citation>()
        compose.setContent {
            MaterialTheme {
                AssistantScreen(AssistantState(turns = listOf(ChatTurn("user", "What did Evan promise?"),
                    ChatTurn("assistant", "Evan will send the Q3 numbers by Thursday [1](mailr-cite:1).", citations = listOf(c)))),
                    AssistantCallbacks(send = {}, stop = {}, newChat = {}, clearContext = {}, openCitation = { picked += it; if (it.isRecording) rec.show(it.connectorId!!, it.recordingId!!, it.t) },
                        openDraft = { _, _ -> }, decide = { _, _, _ -> }, openUrl = {}))
            }
        }
        compose.onNodeWithContentDescription("Recording 1: Weekly sync at 12:34").performClick()
        assertEquals(c, picked.single())
        assertEquals(754.0, rec.sheet?.t)
        assertEquals(12L, rec.sheet?.rid)
    }

    /** "Ask about this recording…" in the composer while a recording is attached. */
    @Test fun composerNamesTheRecording() {
        compose.setContent {
            MaterialTheme {
                AssistantScreen(AssistantState(about = AskAbout.Recording(3, 12, "Weekly sync")),
                    AssistantCallbacks(send = {}, stop = {}, newChat = {}, clearContext = {}, openCitation = {}, openDraft = { _, _ -> }, decide = { _, _, _ -> }, openUrl = {}))
            }
        }
        compose.onNodeWithText("About: Weekly sync").assertIsDisplayed()
        compose.onNode(hasText("Ask about this recording…")).assertIsDisplayed()
    }

    /** The mail card of a failed transcription offers only Open in Speakr. */
    @Test fun failedCard() {
        f.on("GET", "speakr/mail-card") { _, _ -> f.json("""{"connected":true,"items":[${SpeakrFake.RECORDING.replace("\"status\":\"completed\"", "\"status\":\"failed\",\"failed\":true")}]}""") }
        compose.setContent { MaterialTheme { RecordingMailCard(rec, 502) } }
        settle("the card") { shown("Transcription failed") }
        compose.onNodeWithText("Follow-up email").assertDoesNotExist()
        compose.onNode(hasText("Open in Speakr") and hasClickAction()).assertIsDisplayed()
    }
}
