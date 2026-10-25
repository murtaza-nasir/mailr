package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onRoot
import io.github.murtazanasir.mailr.data.agent.AskAbout
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.data.voice.Dictation
import io.github.murtazanasir.mailr.data.voice.VoiceSource
import io.github.murtazanasir.mailr.data.voice.VoiceState
import io.github.murtazanasir.mailr.ui.approvals.ApprovalsScreen
import io.github.murtazanasir.mailr.ui.ask.AskCallbacks
import io.github.murtazanasir.mailr.ui.ask.AskOverlay
import io.github.murtazanasir.mailr.ui.ask.AskUi
import io.github.murtazanasir.mailr.ui.assistant.AssistantCallbacks
import io.github.murtazanasir.mailr.ui.assistant.AssistantScreen
import io.github.murtazanasir.mailr.ui.assistant.AssistantState
import io.github.murtazanasir.mailr.ui.assistant.ChatTurn
import io.github.murtazanasir.mailr.ui.assistant.ComposerVoice
import io.github.murtazanasir.mailr.ui.assistant.TurnItem
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * Android 0.4.4, light and dark: the message box's microphone (agent.dictate) idle, listening,
 * transcribing and with nothing heard; Ask on approval cards (agent.ask_about) on the Approvals
 * screen and in the chat, and the capture it opens.
 * Output: app/screenshots/composer-<theme>-<name>.png
 */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class ComposerVoiceScreenshotTest(private val dark: Boolean) {
    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun params(): List<Array<Any>> = listOf(arrayOf<Any>(false), arrayOf<Any>(true))
    }

    @get:Rule val compose = createComposeRule()
    private val mode get() = if (dark) "dark" else "light"

    private fun shot(name: String, content: @Composable () -> Unit) {
        RuntimeEnvironment.setQualifiers("w411dp-h891dp-port-${if (dark) "night" else "notnight"}-xxhdpi")
        compose.setContent {
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.surface) { content() }
            }
        }
        compose.onRoot().captureRoboImage("screenshots/composer-$mode-$name.png")
    }

    private val noop = AssistantCallbacks({}, {}, {}, {}, {}, { _, _ -> }, { _, _, _ -> }, {}, askApproval = {})

    private val chat = AssistantState(turns = listOf(
        ChatTurn("user", "What's on my calendar tomorrow?"),
        ChatTurn("assistant", items = listOf(TurnItem.Text(0, "", "Tomorrow you have **CHEM 101 lecture** at 10:00 and " +
            "**office hours** at 14:00. Nothing else is booked.")), startedAt = 0, finishedAt = 4_000),
    ))

    private fun voice(ui: AskUi) = ComposerVoice(ui, {}, {}, {}, {})

    @Test fun micIdle() = shot("mic-idle") {
        AssistantScreen(chat, noop, voice(AskUi(null, source = VoiceSource.ON_DEVICE)))
    }

    @Test fun micListening() = shot("mic-listening") {
        AssistantScreen(chat, noop, voice(AskUi(null, text = "Move office hours to three and tell the",
            voice = VoiceState.Listening("Move office hours to three and tell the", 0.6f), source = VoiceSource.ON_DEVICE)))
    }

    @Test fun micTranscribing() = shot("mic-transcribing") {
        AssistantScreen(chat, noop, voice(AskUi(null, voice = VoiceState.Transcribing, source = VoiceSource.SERVER)))
    }

    @Test fun micNoSpeech() = shot("mic-no-speech") {
        AssistantScreen(chat, noop, voice(AskUi(null, voice = VoiceState.Failed(Dictation.NO_SPEECH), source = VoiceSource.SERVER)))
    }

    @Test fun approvalsWithAsk() = shot("approvals-ask") {
        ApprovalsScreen(Fixtures.approvals, Fixtures.approvalHistory, null, false, SnackbarHostState(), {}, {}, { _, _, _ -> }, onAsk = {})
    }

    @Test fun chatCardWithAsk() = shot("chat-approval-ask") {
        val card = Fixtures.approvals[1]
        AssistantScreen(AssistantState(turns = listOf(ChatTurn("user", "Tell Alice Friday at 10 works"),
            ChatTurn("assistant", items = listOf(TurnItem.Text(0, "", "I wrote the reply. Sending it needs your OK."),
                TurnItem.Approval(0, card.id)), approvals = listOf(card), startedAt = 0, finishedAt = 3_000))),
            noop, voice(AskUi(null, source = VoiceSource.ON_DEVICE)))
    }

    @Test fun captureAboutApproval() = shot("capture-approval") {
        AskOverlay(AskUi(AskAbout.Approval(42, "Send “Re: Quarterly plan”"), text = "Why does this need approval?",
            source = VoiceSource.ON_DEVICE), AskCallbacks({}, {}, {}, {}, {}, {}, {}, { false }, {}))
    }
}
