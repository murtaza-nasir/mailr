// ACT-SURFACES: android.assistant android.approvals
package io.github.murtazanasir.mailr

import androidx.compose.runtime.setValue
import androidx.compose.runtime.getValue
import androidx.compose.ui.test.assert
import androidx.compose.ui.test.performTextInputSelection
import androidx.compose.ui.test.performTextInput
import android.app.Application
import androidx.compose.material3.SnackbarHostState
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.hasContentDescription
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onAllNodesWithContentDescription
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onFirst
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import io.github.murtazanasir.mailr.data.agent.AskAbout
import io.github.murtazanasir.mailr.data.api.Approval
import io.github.murtazanasir.mailr.data.api.ApprovalPreview
import io.github.murtazanasir.mailr.data.voice.ClipRecorder
import io.github.murtazanasir.mailr.data.voice.Dictation
import io.github.murtazanasir.mailr.data.voice.ServerSpeechEngine
import io.github.murtazanasir.mailr.data.voice.SpeechEngine
import io.github.murtazanasir.mailr.data.voice.VoiceSource
import io.github.murtazanasir.mailr.data.voice.VoiceState
import io.github.murtazanasir.mailr.ui.approvals.ApprovalsScreen
import io.github.murtazanasir.mailr.ui.approvals.askAbout
import io.github.murtazanasir.mailr.ui.ask.AskCaptureModel
import io.github.murtazanasir.mailr.ui.ask.AskRequest
import io.github.murtazanasir.mailr.ui.ask.AskUi
import io.github.murtazanasir.mailr.ui.assistant.AssistantCallbacks
import io.github.murtazanasir.mailr.ui.assistant.AssistantScreen
import io.github.murtazanasir.mailr.ui.assistant.AssistantState
import io.github.murtazanasir.mailr.ui.assistant.ChatTurn
import io.github.murtazanasir.mailr.ui.assistant.ComposerVoice
import io.github.murtazanasir.mailr.ui.assistant.TurnItem
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.File

/**
 * Android 0.4.4. agent.dictate in the Assistant tab's message box: the ask capture's
 * model, words land in the box and wait for Send, "No speech heard" when the server returns no
 * text. agent.ask_about on approval cards: Ask on the Approvals screen and in the chat opens the
 * capture with the approval attached; the card's own buttons still decide it.
 */
@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class ComposerVoiceTest {
    @get:Rule val compose = createComposeRule()
    @get:Rule val tmp = TemporaryFolder()

    private class FakeEngine : SpeechEngine {
        var events: SpeechEngine.Events? = null
        var started = 0
        override fun start(language: String?, events: SpeechEngine.Events) { this.events = events; started++ }
        override fun stop() {}
        override fun cancel() {}
        override fun destroy() {}
    }

    private fun composer(scope: kotlinx.coroutines.CoroutineScope, e: FakeEngine, sent: MutableList<AskRequest>) =
        AskCaptureModel(null, VoiceSource.ON_DEVICE, handsFree = false, engines = { Dictation(e) }, scope = scope) { sent += it }

    /** agent.dictate: typed and dictated words share the box; nothing is sent by dictation alone. */
    @Test fun dictatedWordsLandInTheBoxWithoutSending() = runTest(UnconfinedTestDispatcher()) {
        val e = FakeEngine()
        val sent = mutableListOf<AskRequest>()
        val m = composer(backgroundScope, e, sent)
        m.setText("Draft a reply")
        m.listen()
        e.events!!.partial("to Dana")
        assertEquals("Draft a reply to Dana", m.ui.value.text)
        e.events!!.final("to Dana saying yes")
        assertEquals("Draft a reply to Dana saying yes", m.ui.value.text)
        assertTrue(sent.isEmpty())
        // After Send the box is empty and ready again.
        m.clear()
        assertEquals("", m.ui.value.text)
        assertEquals(VoiceState.Idle, m.ui.value.voice)
    }

    /** A clip still transcribing when the words were sent lands in the empty box, without the sent words. */
    @Test fun lateWordsLandInTheEmptyBox() = runTest(UnconfinedTestDispatcher()) {
        val e = FakeEngine()
        val m = composer(backgroundScope, e, mutableListOf())
        m.setText("What is on tomorrow")
        m.listen()
        e.events!!.transcribing()
        m.clear()
        assertTrue(m.ui.value.transcribing)
        e.events!!.final("and on Friday")
        assertEquals("and on Friday", m.ui.value.text)
    }

    /** The server dropped silence or a Whisper stock phrase ({"text": "", "dropped": "no speech"}). */
    @Test fun emptyTextSaysNoSpeechHeard() = runTest(UnconfinedTestDispatcher()) {
        val e = FakeEngine()
        val m = composer(backgroundScope, e, mutableListOf())
        m.setText("Ask Dana")
        m.listen()
        e.events!!.transcribing()
        e.events!!.final("")
        assertEquals(VoiceState.Failed(Dictation.NO_SPEECH), m.ui.value.voice)
        assertEquals("Ask Dana", m.ui.value.text)
    }

    private class FakeRecorder : ClipRecorder {
        var file: File? = null
        override fun start(file: File) { this.file = file; file.writeBytes(ByteArray(4096) { 1 }) }
        override fun amplitude() = 0
        override fun stop() = true
        override fun release() {}
    }

    /** The same through the real server engine: an empty answer ends in "No speech heard", and the clip is gone. */
    @Test fun serverEmptyAnswerSaysNoSpeechHeard() = runTest(StandardTestDispatcher()) {
        val rec = FakeRecorder()
        var clock = 0L
        val d = Dictation(ServerSpeechEngine(tmp.root, { rec }, { _, _, _ -> "" }, this, handsFree = false, now = { clock }))
        d.start()
        clock = 2_000
        d.stop()
        runCurrent()
        assertEquals(VoiceState.Failed("No speech heard. Try again."), d.state.value)
        assertFalse(rec.file!!.exists())
    }

    private val noop = AssistantCallbacks({}, {}, {}, {}, {}, { _, _ -> }, { _, _, _ -> }, {})

    /** The microphone in the message box asks to listen; Send sends the box's words. */
    @Test fun composerMicListensAndSendSends() {
        var listened = 0
        var sent = 0
        compose.setContent {
            AssistantScreen(AssistantState(), noop, ComposerVoice(AskUi(null, text = "Summarize today", source = VoiceSource.ON_DEVICE),
                setText = {}, listen = { listened++ }, stop = {}, send = { sent++ }))
        }
        compose.onNodeWithContentDescription("Dictate").performClick()
        assertEquals(1, listened)
        compose.onNodeWithContentDescription("Send").performClick()
        assertEquals(1, sent)
    }

    /**
     * agent.dictate: dictated words put the cursor at their end, so the next word typed follows them.
     * After dictating, the cursor must not sit at the start of the box.
     */
    @OptIn(androidx.compose.ui.test.ExperimentalTestApi::class)
    @Test fun dictatedWordsPutTheCursorAtTheEnd() {
        var ui by androidx.compose.runtime.mutableStateOf(AskUi(null, text = "", source = VoiceSource.ON_DEVICE))
        compose.setContent {
            AssistantScreen(AssistantState(), noop, ComposerVoice(ui, setText = { ui = ui.copy(text = it) }, listen = {}, stop = {}, send = {}))
        }
        val box = compose.onNode(androidx.compose.ui.test.hasSetTextAction())
        box.performClick()
        compose.waitForIdle()
        ui = ui.copy(text = "we are typing this by voice")
        compose.waitForIdle()
        box.assert(androidx.compose.ui.test.SemanticsMatcher.expectValue(
            androidx.compose.ui.semantics.SemanticsProperties.TextSelectionRange, androidx.compose.ui.text.TextRange(27)))
        // Typing after it goes at the end, and the field keeps the cursor the keyboard sets.
        box.performTextInputSelection(androidx.compose.ui.text.TextRange(27))
        box.performTextInput(" today")
        compose.waitForIdle()
        assertEquals("we are typing this by voice today", ui.text)
        ui = ui.copy(text = ui.text + " and tomorrow")
        compose.waitForIdle()
        box.assert(androidx.compose.ui.test.SemanticsMatcher.expectValue(
            androidx.compose.ui.semantics.SemanticsProperties.TextSelectionRange, androidx.compose.ui.text.TextRange(46)))
    }

    @Test fun dictatedTextValueKeepsTypingAndMovesTheCursorForDictation() {
        val typed = androidx.compose.ui.text.input.TextFieldValue("hello", androidx.compose.ui.text.TextRange(2))
        assertEquals(typed, io.github.murtazanasir.mailr.ui.components.DictatedText.value(typed, "hello"))
        assertEquals(androidx.compose.ui.text.TextRange(11), io.github.murtazanasir.mailr.ui.components.DictatedText.value(typed, "hello world").selection)
    }

    @Test fun composerListeningShowsStop() {
        var stopped = 0
        compose.setContent {
            AssistantScreen(AssistantState(), noop, ComposerVoice(
                AskUi(null, text = "Draft a reply to", voice = VoiceState.Listening("Draft a reply to", 0.5f), source = VoiceSource.ON_DEVICE),
                setText = {}, listen = {}, stop = { stopped++ }, send = {}))
        }
        compose.onNodeWithText("Listening…").assertExists()
        compose.onNodeWithContentDescription("Stop dictation").performClick()
        assertEquals(1, stopped)
    }

    @Test fun composerTranscribingHoldsSend() {
        compose.setContent {
            AssistantScreen(AssistantState(), noop, ComposerVoice(
                AskUi(null, text = "Draft", voice = VoiceState.Transcribing, source = VoiceSource.SERVER),
                setText = {}, listen = {}, stop = {}, send = {}))
        }
        compose.onNodeWithText("Transcribing…").assertExists()
        compose.onNodeWithContentDescription("Send").assertIsNotEnabled()
        compose.onNodeWithContentDescription("Dictate").assertIsNotEnabled()
    }

    @Test fun composerNoSpeechHeard() {
        compose.setContent {
            AssistantScreen(AssistantState(), noop, ComposerVoice(
                AskUi(null, voice = VoiceState.Failed(Dictation.NO_SPEECH), source = VoiceSource.SERVER),
                setText = {}, listen = {}, stop = {}, send = {}))
        }
        compose.onNodeWithText(Dictation.NO_SPEECH).assertExists()
    }

    @Test fun noMicWithoutARecognizer() {
        compose.setContent {
            AssistantScreen(AssistantState(), noop, ComposerVoice(AskUi(null, source = VoiceSource.NONE), {}, {}, {}, {}))
        }
        compose.onAllNodesWithContentDescription("Dictate").assertCountEquals(0)
    }

    private val outboxRow = Approval(7, "token:3", "Claude Code", "mail.send", "bob@example.com",
        ApprovalPreview("Send “Lunch?”"), outboxId = 7)

    /** agent.ask_about: Ask on each pending card of the Approvals screen; not on a legacy outbox row. */
    @Test fun approvalsScreenAsksAboutACard() {
        val asked = mutableListOf<Approval>()
        compose.setContent {
            ApprovalsScreen(Fixtures.approvals, emptyList(), null, false, SnackbarHostState(), {}, {}, { _, _, _ -> },
                onAsk = { asked += it })
        }
        compose.onAllNodesWithContentDescription("Ask Ambrose about this approval").onFirst().performClick()
        assertEquals(listOf(41L), asked.map { it.id })
        assertEquals(AskAbout.Approval(41, "Create event"), askAbout(asked[0]))
        // Asking decides nothing: the decision buttons are still there.
        compose.onAllNodesWithContentDescription("Approval needed: Create event").assertCountEquals(1)
    }

    @Test fun noAskOnALegacyOutboxRow() {
        compose.setContent {
            ApprovalsScreen(listOf(outboxRow), emptyList(), null, false, SnackbarHostState(), {}, {}, { _, _, _ -> }, onAsk = {})
        }
        compose.onAllNodesWithContentDescription("Approval needed: Send “Lunch?”").assertCountEquals(1)
        compose.onAllNodesWithTag("approval-ask").assertCountEquals(0)
    }

    @Test fun noAskWithoutAHandler() {
        compose.setContent {
            ApprovalsScreen(Fixtures.approvals, emptyList(), null, false, SnackbarHostState(), {}, {}, { _, _, _ -> })
        }
        compose.onAllNodesWithTag("approval-ask").assertCountEquals(0)
    }

    /** approval.approve and approval.deny on the Approvals screen: Allow once and Deny decide the card. */
    @Test fun approvalsScreenDecides() {
        val decided = mutableListOf<Triple<Long, Boolean, String>>()
        compose.setContent {
            ApprovalsScreen(Fixtures.approvals, emptyList(), null, false, SnackbarHostState(), {}, {},
                { a, ok, scope -> decided += Triple(a.id, ok, scope) })
        }
        compose.onAllNodesWithText("Allow once").onFirst().performClick()
        compose.onAllNodesWithText("Deny").onFirst().performClick()
        assertEquals(listOf(Triple(41L, true, "once"), Triple(41L, false, "once")), decided)
    }

    /** approval.approve and approval.deny on a card in the assistant's chat. */
    @Test fun chatCardDecides() {
        val decided = mutableListOf<Triple<Long, Boolean, String>>()
        val card = Fixtures.approvals[1]
        compose.setContent {
            AssistantScreen(AssistantState(turns = listOf(ChatTurn("user", "Reply to Alice"),
                ChatTurn("assistant", items = listOf(TurnItem.Approval(0, card.id)), approvals = listOf(card)))),
                noop.copy(decide = { a, ok, scope -> decided += Triple(a.id, ok, scope) }))
        }
        compose.onNodeWithText("Deny").performScrollTo().performClick()
        compose.onNodeWithText("Allow once").performScrollTo().performClick()
        assertEquals(listOf(Triple(42L, false, "once"), Triple(42L, true, "once")), decided)
    }

    /** The same on a card in the chat. */
    @Test fun chatCardAsks() {
        val asked = mutableListOf<Long>()
        val card = Fixtures.approvals[1]
        compose.setContent {
            AssistantScreen(AssistantState(turns = listOf(ChatTurn("user", "Reply to Alice"),
                ChatTurn("assistant", items = listOf(TurnItem.Approval(0, card.id)), approvals = listOf(card)))),
                noop.copy(askApproval = { asked += it.id }))
        }
        // The chat follows to the end of a tall card: scroll back up to its header first.
        compose.onNode(hasContentDescription("Ask Ambrose about this approval")).performScrollTo().performClick()
        assertEquals(listOf(42L), asked)
    }
}
