// ACT-SURFACES: android.reader android.calendar android.tasks android.widget android.recording android.ask android.launcher android.approvals
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.agent.AgentClient
import io.github.murtazanasir.mailr.data.agent.AskAbout
import io.github.murtazanasir.mailr.data.api.ApiException
import io.github.murtazanasir.mailr.data.voice.ClipRecorder
import io.github.murtazanasir.mailr.data.voice.Dictation
import io.github.murtazanasir.mailr.data.voice.DictationOrder
import io.github.murtazanasir.mailr.data.voice.ServerSpeechEngine
import io.github.murtazanasir.mailr.data.voice.SpeechEngine
import io.github.murtazanasir.mailr.data.voice.VoicePolicy
import io.github.murtazanasir.mailr.data.voice.VoiceSource
import io.github.murtazanasir.mailr.data.voice.VoiceState
import io.github.murtazanasir.mailr.ui.ask.AskActivity
import io.github.murtazanasir.mailr.ui.ask.AskCaptureModel
import io.github.murtazanasir.mailr.ui.ask.AskIntents
import io.github.murtazanasir.mailr.ui.ask.AskRequest
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.File

/**
 * The ask capture: agent.ask_about carries the item as structured context
 * (focused_task, focused_event, focused_recording, conversation_id); agent.dictate fills one
 * editable field, sends only on Send or, hands-free, when the recognizer finishes; a recognizer
 * that cannot serve falls back to the other source; the server clip is deleted whatever happens;
 * audio is never sent to a hosted service.
 */
@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class AskCaptureTest {
    private val app: Application = ApplicationProvider.getApplicationContext()

    @get:Rule val tmp = TemporaryFolder()

    /** A recognizer the test drives by hand. */
    private class FakeEngine : SpeechEngine {
        var events: SpeechEngine.Events? = null
        var started = 0
        var stopped = 0
        var cancelled = 0
        override fun start(language: String?, events: SpeechEngine.Events) { this.events = events; started++ }
        override fun stop() { stopped++ }
        override fun cancel() { cancelled++ }
        override fun destroy() {}
    }

    private val task = AskAbout.Task("t1", "Send the Q3 numbers to Dana")

    @Test fun intentsCarryTheItemButNeverWords() {
        val items = listOf(task, AskAbout.Event("e1", "2026-10-02T15:00:00Z", "Budget review"), AskAbout.Event("e2", null, ""),
            AskAbout.Recording(3, 12, "Weekly sync"), AskAbout.Conversation(4_503_599_627_370_600L, "Re: numbers"),
            AskAbout.Approval(41, "Send email to Dana"))
        for (a in items) {
            val i = AskIntents.capture(app, a, from = "test")
            assertEquals(AskActivity::class.java.name, i.component!!.className)
            assertEquals(a, AskIntents.read(i))
            assertNull(AskIntents.listen(i))
            assertTrue(i.extras!!.keySet().none { it.contains("text") })
        }
        assertNull(AskIntents.read(AskIntents.capture(app, null, from = "widget")))
        assertEquals(true, AskIntents.listen(AskIntents.capture(app, null, listen = true, from = "widget")))
        assertEquals(false, AskIntents.listen(AskIntents.capture(app, null, listen = false, from = "widget")))
        assertEquals(AskIntents.ACTION_DICTATE, AskIntents.capture(app, null, listen = true, from = "widget").action)
        // Opening the app carries nothing: the request waits in AppGraph.pendingAsk.
        val open = AskIntents.openAssistant(app)
        assertEquals(AskIntents.ACTION_OPEN_ASSISTANT, open.action)
        assertTrue(open.extras == null || open.extras!!.isEmpty)
    }

    @Test fun contextGoesAsStructuredFocus() {
        fun ctx(a: AskAbout) = AgentClient.chatBody("move this to Friday", null, null, null, a)["context"]!!.jsonObject
        assertEquals("t1", ctx(task)["focused_task"]!!.jsonPrimitive.content)
        assertEquals("e1", ctx(AskAbout.Event("e1", "occ", "x"))["focused_event"]!!.jsonObject["uid"]!!.jsonPrimitive.content)
        assertEquals("12", ctx(AskAbout.Recording(3, 12, "x"))["focused_recording"]!!.jsonObject["recording_id"]!!.jsonPrimitive.content)
        assertEquals("77", ctx(AskAbout.Conversation(77, "x"))["conversation_id"]!!.jsonPrimitive.content)
        assertEquals("41", ctx(AskAbout.Approval(41, "x"))["focused_approval"]!!.jsonObject["id"]!!.jsonPrimitive.content)
    }

    @Test fun voicePolicyPrefersThePhoneAndNeverAHostedService() {
        assertEquals(VoiceSource.ON_DEVICE, VoicePolicy.source(onDevice = true, server = true, DictationOrder.PHONE_FIRST))
        assertEquals(VoiceSource.SERVER, VoicePolicy.source(onDevice = false, server = true, DictationOrder.PHONE_FIRST))
        assertEquals(VoiceSource.SERVER, VoicePolicy.source(onDevice = true, server = true, DictationOrder.SERVER))
        assertEquals(VoiceSource.ON_DEVICE, VoicePolicy.source(onDevice = true, server = false, DictationOrder.SERVER))
        assertEquals(VoiceSource.NONE, VoicePolicy.source(onDevice = false, server = false, DictationOrder.PHONE_FIRST))
        assertEquals(VoiceSource.SERVER, VoicePolicy.fallback(VoiceSource.ON_DEVICE, onDevice = true, server = true))
        assertNull(VoicePolicy.fallback(VoiceSource.ON_DEVICE, onDevice = true, server = false))
        for (d in listOf(true, false)) for (s in listOf(true, false)) for (o in DictationOrder.entries)
            assertTrue(VoicePolicy.describe(d, s, o).contains("go to a local speech server only"))
    }

    private fun model(scope: CoroutineScope, engines: Map<VoiceSource, FakeEngine>, source: VoiceSource = VoiceSource.ON_DEVICE,
                      handsFree: Boolean = false, about: AskAbout? = task, sent: MutableList<AskRequest>) =
        AskCaptureModel(about, source, handsFree, { s -> engines[s]?.let { Dictation(it) } }, scope,
            fallback = { cur -> if (cur == VoiceSource.ON_DEVICE && VoiceSource.SERVER in engines) VoiceSource.SERVER else null }) { sent += it }

    /** agent.dictate: words heard so far fill the field; the final words stay editable; Send sends them with the item. */
    @Test fun dictatedWordsAreEditableAndSentOnSend() = runTest(UnconfinedTestDispatcher()) {
        val e = FakeEngine()
        val sent = mutableListOf<AskRequest>()
        val m = model(backgroundScope, mapOf(VoiceSource.ON_DEVICE to e), sent = sent)
        m.setText("Please")
        m.listen()
        e.events!!.partial("move this")
        assertEquals("Please move this", m.ui.value.text)
        assertTrue(m.ui.value.listening)
        e.events!!.final("move this to Friday")
        assertEquals("Please move this to Friday", m.ui.value.text)
        assertTrue(sent.isEmpty())   // not hands-free: nothing goes yet
        m.setText("Move this to Friday and email Dana about it")
        m.send()
        assertEquals(listOf(AskRequest(task, "Move this to Friday and email Dana about it")), sent)
        m.send()
        assertEquals(1, sent.size)   // once only
    }

    /** "Send when I stop talking": the final words go at once. */
    @Test fun handsFreeSendsWhenTheRecognizerFinishes() = runTest(UnconfinedTestDispatcher()) {
        val e = FakeEngine()
        val sent = mutableListOf<AskRequest>()
        val m = model(backgroundScope, mapOf(VoiceSource.ON_DEVICE to e), handsFree = true, sent = sent)
        m.listen()
        e.events!!.final("what is on tomorrow")
        assertEquals(listOf(AskRequest(task, "what is on tomorrow")), sent)
        // Nothing heard: nothing sent, the field stays empty.
        val sent2 = mutableListOf<AskRequest>()
        val e2 = FakeEngine()
        val m2 = model(backgroundScope, mapOf(VoiceSource.ON_DEVICE to e2), handsFree = true, sent = sent2)
        m2.listen()
        e2.events!!.error(Dictation.ERROR_NO_MATCH)
        assertTrue(sent2.isEmpty())
        assertTrue(m2.ui.value.voice is VoiceState.Failed)
    }

    /** The on-device recognizer has no model for the language: the capture listens again through the server. */
    @Test fun unusableRecognizerFallsBackToTheServer() = runTest(UnconfinedTestDispatcher()) {
        val phone = FakeEngine()
        val server = FakeEngine()
        val sent = mutableListOf<AskRequest>()
        val m = model(backgroundScope, mapOf(VoiceSource.ON_DEVICE to phone, VoiceSource.SERVER to server), sent = sent)
        m.listen()
        phone.events!!.error(Dictation.ERROR_LANGUAGE_UNAVAILABLE)
        assertEquals(VoiceSource.SERVER, m.ui.value.source)
        assertEquals(1, server.started)
        server.events!!.transcribing()
        assertFalse(m.ui.value.canSend)
        server.events!!.final("email Dana")
        assertEquals("email Dana", m.ui.value.text)
    }

    /** No fallback: the message stays and typing still works. */
    @Test fun failureWithoutFallbackKeepsTheKeyboard() = runTest(UnconfinedTestDispatcher()) {
        val phone = FakeEngine()
        val sent = mutableListOf<AskRequest>()
        val m = model(backgroundScope, mapOf(VoiceSource.ON_DEVICE to phone), sent = sent)
        m.listen()
        phone.events!!.error(Dictation.ERROR_LANGUAGE_UNAVAILABLE)
        val v = m.ui.value.voice as VoiceState.Failed
        assertTrue(v.download)
        m.setText("typed instead")
        m.send()
        assertEquals("typed instead", sent.single().text)
    }

    /** Open chat: the Assistant tab about the item, nothing sent; the item can be removed first. */
    @Test fun openChatSendsNothingAndTheItemCanBeDropped() = runTest(UnconfinedTestDispatcher()) {
        val sent = mutableListOf<AskRequest>()
        val m = model(backgroundScope, emptyMap(), source = VoiceSource.NONE, sent = sent)
        assertEquals(VoiceSource.NONE, m.ui.value.source)
        m.openChat()
        assertEquals(AskRequest(task, null), sent.single())
        val sent2 = mutableListOf<AskRequest>()
        val m2 = model(backgroundScope, emptyMap(), source = VoiceSource.NONE, sent = sent2)
        m2.clearAbout()
        m2.setText("what is on tomorrow")
        m2.send()
        assertEquals(AskRequest(null, "what is on tomorrow"), sent2.single())
    }

    private class FakeRecorder(var amp: Int = 0) : ClipRecorder {
        var file: File? = null
        var released = false
        override fun start(file: File) { this.file = file; file.writeBytes(ByteArray(4096) { 1 }) }
        override fun amplitude() = amp
        override fun stop() = true
        override fun release() { released = true }
    }

    private class Events : SpeechEngine.Events {
        val log = mutableListOf<String>()
        override fun ready() { log += "ready" }
        override fun level(rms: Float) {}
        override fun partial(text: String) {}
        override fun final(text: String) { log += "final:$text" }
        override fun error(code: Int) { log += "error:$code" }
        override fun transcribing() { log += "transcribing" }
        override fun failed(message: String) { log += "failed:$message" }
    }

    /** The server path: Stop → Transcribing → the text; the clip is deleted afterwards. */
    @Test fun serverClipIsTranscribedThenDeleted() = runTest(StandardTestDispatcher()) {
        val rec = FakeRecorder()
        var clock = 0L
        val uploads = mutableListOf<Pair<Long, Boolean>>()
        val engine = ServerSpeechEngine(tmp.root, { rec }, { f, ms, _ -> uploads += ms to f.exists(); "move this to Friday" },
            this, handsFree = false, now = { clock })
        val ev = Events()
        engine.start(null, ev)
        clock = 3_000
        engine.stop()
        runCurrent()
        assertEquals(listOf("ready", "transcribing", "final:move this to Friday"), ev.log)
        assertEquals(listOf(3_000L to true), uploads)
        assertFalse(rec.file!!.exists())
        assertTrue(rec.released)
    }

    /** Hands-free on the server path: speech, then two seconds of quiet, ends the clip. A refusal shows the server's words. */
    @Test fun serverHandsFreeStopsOnQuietAndShowsRefusals() = runTest(StandardTestDispatcher()) {
        val rec = FakeRecorder(amp = 9_000)
        var clock = 0L
        val engine = ServerSpeechEngine(tmp.root, { rec }, { _, _, _ -> throw ApiException(413, "Clips can be up to 120 seconds.") },
            this, handsFree = true, now = { clock })
        val ev = Events()
        engine.start(null, ev)
        repeat(5) { clock += 100; advanceTimeBy(101) }
        rec.amp = 100
        repeat(25) { clock += 100; advanceTimeBy(101) }
        runCurrent()
        assertEquals("transcribing", ev.log[1])
        assertEquals("failed:Clips can be up to 120 seconds.", ev.log.last())
        assertFalse(rec.file!!.exists())
    }

    /** Cancel throws the clip away without sending it. */
    @Test fun cancelDiscardsTheClip() = runTest(StandardTestDispatcher()) {
        val rec = FakeRecorder()
        var uploaded = false
        val engine = ServerSpeechEngine(tmp.root, { rec }, { _, _, _ -> uploaded = true; "" }, this, handsFree = false, now = { 0L })
        engine.start(null, Events())
        engine.cancel()
        runCurrent()
        assertFalse(uploaded)
        assertFalse(rec.file!!.exists())
    }

}
