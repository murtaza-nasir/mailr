// ACT-SURFACES: android.recording
package io.github.murtazanasir.mailr

import android.app.Application
import io.github.murtazanasir.mailr.data.api.Attendee
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.SpeakrLabel
import io.github.murtazanasir.mailr.data.recorder.AudioCapture
import io.github.murtazanasir.mailr.data.recorder.IllegalTransition
import io.github.murtazanasir.mailr.data.recorder.RecState
import io.github.murtazanasir.mailr.data.recorder.RecorderController
import io.github.murtazanasir.mailr.data.recorder.RecorderHooks
import io.github.murtazanasir.mailr.data.recorder.RecorderMachine
import io.github.murtazanasir.mailr.data.recorder.RecordingMeta
import io.github.murtazanasir.mailr.data.recorder.RecordingSession
import io.github.murtazanasir.mailr.data.recorder.RecordingStore
import io.github.murtazanasir.mailr.data.recorder.UploadOutcome
import io.github.murtazanasir.mailr.ui.recordings.NotesMarkdown
import io.github.murtazanasir.mailr.ui.recordings.RecordingForm
import io.github.murtazanasir.mailr.ui.recordings.clock
import io.github.murtazanasir.mailr.ui.recordings.eventOptions
import io.github.murtazanasir.mailr.ui.recordings.insertTimestamp
import io.github.murtazanasir.mailr.ui.recordings.toggleCheck
import com.mohamedrejeb.richeditor.model.RichTextState
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.File
import java.nio.file.Files
import java.time.ZoneOffset
import java.time.ZonedDateTime

/**
 * New recording (recording.record): the recorder's transitions, a kill in the middle of a
 * recording and the recovery that uploads it, notes kept as Markdown, the form that becomes the
 * upload's fields, and the upload through mailr (POST /speakr/recordings) with retries.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class RecorderTest {
    private val f = SpeakrFake()
    private lateinit var dir: File
    private var t = 1_000_000L
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)

    /** The microphone, writing a few bytes per call so files have audio. */
    private class FakeCapture : AudioCapture {
        var file: File? = null
        val calls = mutableListOf<String>()
        override fun start(file: File) { this.file = file; file.writeBytes(ByteArray(2048) { 1 }); calls += "start" }
        override fun pause() { calls += "pause" }
        override fun resume() { calls += "resume" }
        override fun stop() { calls += "stop" }
        override fun level() = 0.4f
    }

    private class Hooks : RecorderHooks {
        val events = mutableListOf<String>()
        val uploads = mutableListOf<String>()
        override fun recording(session: RecordingSession) { events += "recording:${session.state}" }
        override fun idle() { events += "idle" }
        override fun enqueueUpload(id: String) { uploads += id }
    }

    private val mic = FakeCapture()
    private val hooks = Hooks()

    private fun controller(capture: AudioCapture = mic, h: Hooks = hooks) =
        RecorderController(RecordingStore(dir), { capture }, { f.api }, scope, h, now = { t }, tickMs = 60_000)

    @Before fun setUp() {
        f.start()
        dir = Files.createTempDirectory("rec").toFile()
    }

    @After fun tearDown() {
        // Snapshot state written outside a composition (the notes editor's state, the form) must not
        // stay pending: the next Compose test would never see the app idle.
        androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications()
        scope.cancel()
        f.stop()
        dir.deleteRecursively()
    }

    // ---------------- the state machine ----------------

    @Test fun transitionsKeepTheRecordedTime() {
        var s = RecorderMachine.start("a", "a.aac", RecordingMeta(title = "Weekly sync"), 0)
        assertEquals(RecState.RECORDING, s.state)
        assertEquals(5_000, s.elapsedAt(5_000))
        s = RecorderMachine.pause(s, 5_000)
        assertEquals(5_000, s.elapsedAt(60_000))                // paused time does not count
        s = RecorderMachine.resume(s, 60_000)
        assertEquals(8_000, s.elapsedAt(63_000))
        s = RecorderMachine.stop(s, 65_000)
        assertEquals(RecState.STOPPED, s.state)
        assertEquals(10_000, s.elapsedMs)
        try { RecorderMachine.pause(s, 70_000); fail("a stopped recording cannot pause") } catch (_: IllegalTransition) { }
        s = RecorderMachine.uploading(s)
        assertEquals(1, s.attempts)
        s = RecorderMachine.failed(s, "offline", final = false)
        assertTrue(s.waiting)
        s = RecorderMachine.uploading(s)
        s = RecorderMachine.uploaded(s, 3, 44)
        assertEquals(RecState.UPLOADED, s.state)
        assertEquals(44L, s.recordingId)
        assertFalse(s.waiting)
        // A refusal waits for "Try again".
        val refused = RecorderMachine.failed(RecorderMachine.uploading(RecorderMachine.stop(RecorderMachine.start("b", "b.aac", RecordingMeta(), 0), 1)), "too large", final = true)
        assertFalse(refused.waiting)
        assertTrue(RecorderMachine.retry(refused).waiting)
    }

    @Test fun recoverStopsAtTheLastHeartbeat() {
        var s = RecorderMachine.start("a", "a.aac", RecordingMeta(), 0)
        s = RecorderMachine.heartbeat(s, 42_000)
        val r = RecorderMachine.recover(s)
        assertEquals(RecState.STOPPED, r.state)
        assertEquals(42_000, r.elapsedMs)
        assertTrue(r.recovered)
        // Paused: the time recorded before the pause.
        val p = RecorderMachine.recover(RecorderMachine.pause(RecorderMachine.start("b", "b.aac", RecordingMeta(), 0), 9_000))
        assertEquals(9_000, p.elapsedMs)
        // An upload cut off by the kill goes back to waiting.
        val u = RecorderMachine.recover(RecorderMachine.uploading(RecorderMachine.stop(RecorderMachine.start("c", "c.aac", RecordingMeta(), 0), 5)))
        assertEquals(RecState.STOPPED, u.state)
    }

    // ---------------- the controller: start, pause, stop, kill and restore ----------------

    @Test fun recordPauseStopQueuesTheUpload() = runBlocking {
        val c = controller()
        val s = c.start(RecordingMeta(title = "Weekly sync"))
        assertTrue(File(dir, s.file).isFile)
        assertEquals("recording:RECORDING", hooks.events.last())
        t += 3_000; c.pause()
        assertEquals(RecState.PAUSED, c.current.value!!.state)
        t += 10_000; c.resume()
        c.setNotes("- [ ] Send the numbers")
        t += 2_000; c.stop()
        val done = c.current.value!!
        assertEquals(RecState.STOPPED, done.state)
        assertEquals(5_000, done.elapsedMs)
        assertEquals(listOf("start", "pause", "resume", "stop"), mic.calls)
        assertEquals(listOf(s.id), hooks.uploads)
        assertEquals("idle", hooks.events.last())
        // On disk, notes included.
        assertEquals("- [ ] Send the numbers", RecordingStore(dir).load(s.id)!!.meta.notes)
    }

    /** recording.record survives a kill: the next process finds the record, stops it and uploads it. */
    @Test fun killAndRestore() = runBlocking {
        val first = controller()
        val s = first.start(RecordingMeta(title = "Advising"))
        first.setNotes("Asked about the **internship**")
        // The process dies here: no stop, no idle. The disk keeps the record and the audio.
        val hooks2 = Hooks()
        t += 30_000
        val second = controller(FakeCapture(), hooks2)
        second.recover()
        val r = RecordingStore(dir).load(s.id)!!
        assertEquals(RecState.STOPPED, r.state)
        assertTrue(r.recovered)
        assertEquals("Asked about the **internship**", r.meta.notes)
        assertEquals(listOf(s.id), hooks2.uploads)
        assertEquals(s.id, second.current.value?.id)
        // A second recover in the same process changes nothing.
        second.recover()
        assertEquals(1, hooks2.uploads.size)
    }

    // ---------------- the upload ----------------

    @Test fun uploadSendsTheFieldsAndLinksTheRecording() = runBlocking {
        f.on("POST", "speakr/recordings") { _, _ -> f.json("""{"connector_id":3,"recording_id":44,"title":"Weekly sync","status":"PENDING","url":"https://speakr.example.org/recordings/44"}""", 202) }
        val c = controller()
        val s = c.start(RecordingMeta(title = "Weekly sync", participants = listOf("Dana Ortiz", "Evan Li"), meetingDate = "2026-10-01T15:00-04:00",
            folderId = 5, tagIds = listOf(7, 8), language = "en", minSpeakers = 2, maxSpeakers = 3, hotwords = "FERPA",
            eventUid = "ev-1", occurrence = "20261001T190000Z"))
        c.setNotes("## Agenda\n\n- [ ] Budget")
        t += 61_000; c.stop()
        assertEquals(UploadOutcome.DONE, c.uploadNow(s.id))
        val done = RecordingStore(dir).load(s.id)!!
        assertEquals(RecState.UPLOADED, done.state)
        assertEquals(3L, done.connectorId)
        assertEquals(44L, done.recordingId)
        assertFalse("the audio is dropped once Speakr has it", File(dir, s.file).exists())
        val body = f.sent("POST", "speakr/recordings").single().second
        listOf("name=\"file\"", "Weekly sync", "Dana Ortiz, Evan Li", "2026-10-01T15:00-04:00", "name=\"folder\"", "name=\"tags\"",
            "name=\"language\"", "name=\"min_speakers\"", "FERPA", "name=\"event_uid\"", "ev-1", "## Agenda", "- [ ] Budget")
            .forEach { assertTrue("missing $it", it in body) }
        assertTrue(Regex("name=\"tags\"[\\s\\S]{0,80}7,8").containsMatchIn(body))
        assertEquals(UploadOutcome.DONE, c.uploadNow(s.id))     // a second run does nothing
        assertEquals(1, f.sent("POST", "speakr/recordings").size)
    }

    @Test fun networkErrorsRetryRefusalsDoNot() = runBlocking {
        var code = 503
        f.on("POST", "speakr/recordings") { _, _ -> f.json("""{"detail":"busy"}""", code) }
        val c = controller()
        val s = c.start(RecordingMeta())
        t += 5_000; c.stop()
        assertEquals(UploadOutcome.RETRY, c.uploadNow(s.id))
        var r = RecordingStore(dir).load(s.id)!!
        assertEquals(RecState.FAILED, r.state)
        assertFalse(r.errorFinal)
        assertTrue(File(dir, s.file).exists())
        code = 413
        assertEquals(UploadOutcome.FINAL, c.uploadNow(s.id))
        r = RecordingStore(dir).load(s.id)!!
        assertTrue(r.errorFinal)
        assertEquals("File too large for Speakr.", r.error)
        // Try again queues it once more; discard removes it and its audio.
        c.retry(s.id)
        assertEquals(RecState.STOPPED, RecordingStore(dir).load(s.id)!!.state)
        assertEquals(2, hooks.uploads.size)
        c.discard(s.id)
        assertNull(RecordingStore(dir).load(s.id))
        assertFalse(File(dir, s.file).exists())
        // The server never saw a file after the refusal other than the two attempts.
        assertEquals(2, f.sent("POST", "speakr/recordings").size)
        assertTrue(f.requests.all { it.first.path == "/api/v1/speakr/recordings" })
        Unit
    }

    @Test fun progressIsReported() = runBlocking {
        val seen = mutableListOf<Float>()
        f.on("POST", "speakr/recordings") { _, _ ->
            seen += 1f
            MockResponse().setResponseCode(200).setHeader("Content-Type", "application/json").setBody("""{"recording":{"connector_id":3,"id":45}}""")
        }
        val c = controller()
        val s = c.start(RecordingMeta())
        c.stop()
        assertEquals(UploadOutcome.DONE, c.uploadNow(s.id))
        assertEquals(45L, RecordingStore(dir).load(s.id)!!.recordingId)
        assertTrue(c.progress.value.isEmpty())
        assertEquals(1, seen.size)
    }

    // ---------------- notes as Markdown ----------------

    @Test fun notesMarkdownRoundTrip() = androidx.compose.runtime.snapshots.Snapshot.withMutableSnapshot {
        val md = "## Agenda\n\nMove the **budget review** to *Friday*.\n\n- Dana\n- Evan\n\n- [ ] Send the numbers\n- [x] Book the room"
        val state = RichTextState()
        NotesMarkdown.write(state, md)
        val text = state.annotatedString.text
        assertTrue(text, "☐ Send the numbers" in text)
        assertTrue(text, "☑ Book the room" in text)
        val back = NotesMarkdown.read(state)
        assertTrue(back, "## Agenda" in back)
        assertTrue(back, "**budget review**" in back)
        assertTrue(back, "- [ ] Send the numbers\n- [x] Book the room" in back)
        // Stable: the second round gives the same Markdown.
        val again = RichTextState().also { NotesMarkdown.write(it, back) }
        assertEquals(back, NotesMarkdown.read(again))
    }

    @Test fun checklistAndTimestampFromTheToolbar() = androidx.compose.runtime.snapshots.Snapshot.withMutableSnapshot {
        val state = RichTextState()
        state.setText("Call Dana")
        state.selection = androidx.compose.ui.text.TextRange(4)
        toggleCheck(state)
        assertEquals("☐ Call Dana", state.annotatedString.text)
        toggleCheck(state)
        assertEquals("☑ Call Dana", state.annotatedString.text)
        assertEquals("- [x] Call Dana", NotesMarkdown.read(state))
        toggleCheck(state)
        assertEquals("Call Dana", state.annotatedString.text)
        state.selection = androidx.compose.ui.text.TextRange(state.annotatedString.text.length)
        insertTimestamp(state, 754_000)
        assertTrue(state.annotatedString.text.endsWith("[12:34] "))
        assertEquals("1:02:03", clock(3_723_000))
        assertEquals("00:07", clock(7_400))
    }

    // ---------------- the form ----------------

    @Test fun formFromTheEventAndTags() = androidx.compose.runtime.snapshots.Snapshot.withMutableSnapshot {
        val now = ZonedDateTime.of(2026, 10, 1, 15, 10, 0, 0, ZoneOffset.UTC)
        fun ev(uid: String, title: String, h: Int, people: List<Attendee> = emptyList()) = CalendarEvent(uid, title,
            now.withHour(h).withMinute(0).toOffsetDateTime().toString(), now.withHour(h + 1).withMinute(0).toOffsetDateTime().toString(),
            attendeeList = people)
        val (opts, pick) = eventOptions(listOf(
            ev("a", "Standup", 9), ev("b", "Weekly sync", 15, listOf(Attendee("me@example.com", "Me"), Attendee("dana@example.com", "Dana Ortiz"),
                Attendee("evan@example.com", ""))), ev("c", "Advising", 17)), setOf("me@example.com"), now)
        assertEquals(3, opts.size)
        assertEquals("b", pick!!.uid)                         // the one in progress
        assertEquals("c", eventOptions(listOf(ev("c", "Advising", 17)), emptySet(), now).second!!.uid)  // else the next
        val form = RecordingForm(now)
        form.pick(pick)
        assertEquals("Weekly sync", form.title)
        assertEquals("Dana Ortiz, evan@example.com", form.participants)
        // Another event replaces what the first one filled, not what the user typed.
        form.title = "Budget call"
        form.pick(opts.last())
        assertEquals("Budget call", form.title)
        assertEquals("", form.participants)
        // The first tag's ASR defaults fill the empty options.
        form.toggleTag(SpeakrLabel(7, "Advising", "#F59E0B", defaultLanguage = "en", defaultMinSpeakers = 2, defaultMaxSpeakers = 3, defaultHotwords = "FERPA"))
        form.toggleTag(SpeakrLabel(8, "Committee", defaultLanguage = "de"))
        form.folderId = 5
        form.exactSpeakers = true
        val m = form.meta("notes")
        assertEquals(listOf(7L, 8L), m.tagIds)
        assertEquals("en", m.language)
        assertEquals(2, m.minSpeakers)
        assertEquals(2, m.maxSpeakers)                        // exact: max = min
        assertEquals("FERPA", m.hotwords)
        assertEquals(5L, m.folderId)
        assertEquals("c", m.eventUid)
        assertEquals("notes", m.notes)
        // Back from the record (a recording shown again after a restart).
        val again = RecordingForm(now).apply { load(m, opts) }
        val back = again.meta("notes")
        assertEquals(java.time.OffsetDateTime.parse(m.meetingDate).toInstant(), java.time.OffsetDateTime.parse(back.meetingDate).toInstant())
        assertEquals(m, back.copy(meetingDate = m.meetingDate))
    }
}
