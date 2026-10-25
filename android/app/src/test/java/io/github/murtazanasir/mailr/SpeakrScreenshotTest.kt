package io.github.murtazanasir.mailr

import android.app.Application
import android.os.Looper
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import io.github.murtazanasir.mailr.data.api.Citation
import io.github.murtazanasir.mailr.data.api.SpeakrLabel
import io.github.murtazanasir.mailr.data.recorder.RecorderMachine
import io.github.murtazanasir.mailr.data.recorder.RecordingMeta
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.ui.assistant.AssistantCallbacks
import io.github.murtazanasir.mailr.ui.assistant.AssistantScreen
import io.github.murtazanasir.mailr.ui.assistant.AssistantState
import io.github.murtazanasir.mailr.ui.assistant.ChatTurn
import io.github.murtazanasir.mailr.ui.recordings.ActionItemsReview
import io.github.murtazanasir.mailr.ui.recordings.EventOption
import io.github.murtazanasir.mailr.ui.recordings.EventRecordings
import io.github.murtazanasir.mailr.ui.recordings.NewRecordingScreen
import io.github.murtazanasir.mailr.ui.recordings.RecorderCallbacks
import io.github.murtazanasir.mailr.ui.recordings.RecordingForm
import io.github.murtazanasir.mailr.ui.recordings.RecordingMailCard
import io.github.murtazanasir.mailr.ui.recordings.RecordingView
import io.github.murtazanasir.mailr.ui.recordings.Recordings
import io.github.murtazanasir.mailr.ui.recordings.TasksTarget
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.cancel
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.time.ZoneOffset
import java.time.ZonedDateTime

/** Recordings (Speakr) and New recording, light and dark: the sheet, the event section, the reader card, chips, the recorder. */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class SpeakrScreenshotTest(private val dark: Boolean) {
    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun params(): List<Array<Any>> = listOf(arrayOf<Any>(false), arrayOf<Any>(true))
    }

    @get:Rule val compose = createComposeRule()
    private val f = SpeakrFake()
    private val scope = MainScope()
    private lateinit var rec: Recordings

    @Before fun setUp() {
        f.start()
        rec = Recordings(f.api, scope, openUrl = {}, openDraft = {}, ask = {}, openLink = {}, notify = { _, _ -> })
        f.on("GET", "speakr/recordings/3/12") { _, _ -> f.json(SpeakrFake.RECORDING) }
        f.on("GET", "speakr/recordings/3/12/transcript") { _, _ -> f.json(SpeakrFake.TRANSCRIPT) }
        f.on("GET", "speakr/folders") { _, _ -> f.json(SpeakrFake.FOLDERS) }
        f.on("GET", "speakr/tags") { _, _ -> f.json(SpeakrFake.TAGS) }
        f.on("POST", "speakr/recordings/3/12/action-items") { _, _ -> f.json(SpeakrFake.ACTION_ITEMS) }
        val item = SpeakrFake.RECORDING.replace("\"candidates\":[]", "\"candidates\":[],\"summary_excerpt\":\"## Decisions\\n- Move the budget review to Friday. Dana sends the Q3 numbers.\"," +
            "\"link\":{\"id\":41,\"connector_id\":3,\"recording_id\":12,\"kind\":\"event\",\"target\":\"ev-1\",\"state\":\"suggested\",\"source\":\"auto\"}")
        f.on("GET", "speakr/events/recordings") { _, _ -> f.json("""{"connected":true,"items":[$item]}""") }
        f.on("GET", "speakr/events/last-time") { _, _ ->
            f.json("""{"connected":true,"item":${SpeakrFake.RECORDING.replace("\"candidates\":[]", "\"candidates\":[],\"summary_line\":\"The review moved to Friday; Dana owns the numbers.\"")}}""")
        }
        f.on("GET", "speakr/mail-card") { _, _ -> f.json("""{"connected":true,"items":[${SpeakrFake.RECORDING.replace("\"candidates\":[]", "\"candidates\":[],\"summary_excerpt\":\"Move the budget review to Friday; Dana sends the Q3 numbers.\"")}]}""") }
    }

    @After fun tearDown() {
        scope.cancel()
        f.stop()
    }

    /**
     * Renders [content] and takes the shot once every text in [waitFor] shows (each comes from its own
     * answer) and, with [colours], Speakr's folder and tag colours are in.
     */
    private fun shot(name: String, vararg waitFor: String, colours: Boolean = false, before: () -> Unit = {}, content: @Composable () -> Unit) {
        RuntimeEnvironment.setQualifiers("w411dp-h891dp-port-${if (dark) "night" else "notnight"}-xxhdpi")
        // The recorder's clock and the progress indicators never go idle; time moves only below.
        compose.mainClock.autoAdvance = false
        compose.setContent {
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.surfaceContainerLow) { Box { content() } }
            }
        }
        val frame = {
            compose.mainClock.advanceTimeBy(100)
            compose.waitForIdle()
            shadowOf(Looper.getMainLooper()).idle()
        }
        for (text in waitFor) eventually("\"$text\" shown", idle = frame) { compose.onAllNodesWithText(text, substring = true).fetchSemanticsNodes().isNotEmpty() }
        if (colours) eventually("the folder and tag colours", idle = frame) { rec.labelsFetched }
        // The answers above are in; these frames show them.
        repeat(15) { frame() }
        before()
        compose.mainClock.advanceTimeBy(600)
        compose.onRoot().captureRoboImage("screenshots/speakr-${if (dark) "dark" else "light"}-$name.png")
    }

    @Test fun sheetSummary() = shot("sheet-summary", "Move the", colours = true) { RecordingView(rec, 3, 12, null) }
    @Test fun sheetTranscript() = shot("sheet-transcript", "I'll send the Q3", colours = true) { RecordingView(rec, 3, 12, 754.0) }
    @Test fun eventSection() = shot("event-section", "Is this the recording", "The review moved to Friday") { EventRecordings(rec, "ev-1", null) }
    @Test fun mailCard() = shot("mail-card", "Move the budget review") { RecordingMailCard(rec, 502) }
    @Test fun actionItems() = shot("action-items", "Send the Q3 numbers") {
        ActionItemsReview(rec, TasksTarget(3, 12, "Weekly sync", null))
    }

    @Test fun citationChips() = shot("citations") {
        val c = Citation(1, kind = "recording", subject = "Weekly sync", from = "Evan Li", quote = "I'll send the Q3 numbers by Thursday.",
            recordingId = 12, connectorId = 3, t = 754.0, title = "Weekly sync", speaker = "Evan Li")
        val m = Citation(2, conversationId = 3066, messageId = 502, subject = "Q3 numbers", from = "Dana Ortiz", quote = "Friday works.",
            date = "2026-09-30T10:00:00Z")
        AssistantScreen(AssistantState(turns = listOf(ChatTurn("user", "What did Evan promise in the weekly sync?"),
            ChatTurn("assistant", "Evan said he would send the Q3 numbers by Thursday [1](mailr-cite:1), and Dana confirmed Friday for the review [2](mailr-cite:2).",
                citations = listOf(c, m)))), AssistantCallbacks({}, {}, {}, {}, {}, { _, _ -> }, { _, _, _ -> }, {}))
    }

    private val now = ZonedDateTime.of(2026, 10, 1, 15, 5, 0, 0, ZoneOffset.UTC)
    private val events = listOf(EventOption("ev-1", null, "Weekly sync", now.withMinute(0), now.withHour(16).withMinute(0), listOf("Dana Ortiz", "Evan Li")))
    private val folders = listOf(SpeakrLabel(5, "Fall 26", "#10B981"), SpeakrLabel(6, "Advising", "#8B5CF6"))
    private val tags = listOf(SpeakrLabel(7, "Advising", "#F59E0B"), SpeakrLabel(8, "Committee", "#3B82F6"), SpeakrLabel(9, "1:1", "#EC4899"))
    private val noop = RecorderCallbacks({}, {}, {}, {}, {}, {}, {}, {}, { _, _ -> }, {}, {}, {}, {})

    private fun form() = RecordingForm(now).apply { pick(events.first()); folderId = 5; tagIds.add(7) }

    @Test fun newRecording() = shot("new") {
        NewRecordingScreen(form(), null, folders, tags, events, 0f, null, emptyList(), noop, timeSource = { now.toInstant().toEpochMilli() })
    }

    /** Transcription options open: the language is the web's select field. */
    @Test fun newRecordingOptions() = shot("new-options", before = {
        compose.onNodeWithText("Transcription options").performScrollTo().performClick()
        compose.mainClock.advanceTimeBy(600)
        compose.onNodeWithText("Language").performScrollTo()
    }) {
        NewRecordingScreen(form(), null, folders, tags, events, 0f, null, emptyList(), noop, timeSource = { now.toInstant().toEpochMilli() })
    }

    @Test fun recording() = shot("recording") {
        val t0 = now.toInstant().toEpochMilli()
        val s = RecorderMachine.notes(RecorderMachine.start("r1", "r1.aac", form().meta(), t0 - 754_000),
            "## Agenda\n\n- Budget review moves to **Friday**\n\n- [ ] Send the Q3 numbers\n\n[12:34] Evan owns the numbers")
        NewRecordingScreen(form(), s, folders, tags, events, 0.5f, null, emptyList(), noop, timeSource = { t0 },
            levelHistory = List(64) { i -> (0.05f + 0.6f * kotlin.math.abs(kotlin.math.sin(i / 3.0)).toFloat()) * (i / 64f) })
    }

    @Test fun uploading() = shot("uploading") {
        val t0 = now.toInstant().toEpochMilli()
        val s = RecorderMachine.uploading(RecorderMachine.stop(RecorderMachine.start("r2", "r2.aac", RecordingMeta(title = "Weekly sync", eventTitle = "Weekly sync"), t0 - 2_730_000), t0))
        NewRecordingScreen(form(), s, folders, tags, events, 0f, 0.42f, emptyList(), noop, timeSource = { t0 })
    }

    @Test fun uploaded() = shot("uploaded") {
        val t0 = now.toInstant().toEpochMilli()
        val s = RecorderMachine.uploaded(RecorderMachine.stop(RecorderMachine.start("r3", "r3.aac", RecordingMeta(title = "Weekly sync"), t0 - 2_730_000), t0), 3, 44)
        NewRecordingScreen(form(), s, folders, tags, events, 0f, null, emptyList(), noop, timeSource = { t0 })
    }
}
