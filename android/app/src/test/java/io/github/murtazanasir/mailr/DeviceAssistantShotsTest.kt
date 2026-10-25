package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.material3.SnackbarHostState
import io.github.murtazanasir.mailr.ui.approvals.ApprovalsScreen
import io.github.murtazanasir.mailr.ui.assistant.AssistantCallbacks
import io.github.murtazanasir.mailr.ui.assistant.AssistantScreen
import io.github.murtazanasir.mailr.ui.assistant.AssistantState
import io.github.murtazanasir.mailr.ui.assistant.ChatTurn
import io.github.murtazanasir.mailr.ui.assistant.Step
import io.github.murtazanasir.mailr.ui.assistant.TurnItem
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/** The assistant (answered, working, empty) and approvals. On every device of [DeviceShots], light and dark. */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class DeviceAssistantShotsTest(device: String, qualifiers: String, dark: Boolean) : DeviceShots(device, qualifiers, dark) {
    @Test fun assistant() = shot("assistant") {
        AssistantScreen(
            AssistantState(turns = listOf(
                ChatTurn("user", "What needs a reply from me this week?"),
                ChatTurn("assistant", items = listOf(
                    TurnItem.Steps(0, listOf(Step("1", "search_conversations", "Searching your inbox", true, count = 50),
                        Step("2", "find_passages", "Finding passages about “reply”", true),
                        Step("3", "get_conversation", "Reading “SPADS 2027 award nominations”", true))),
                    TurnItem.Text(1, "", "Here's what's waiting on you this week:\n\n**Time-sensitive**\n- **SPADS award nominations** close Sep 30 [1](mailr-cite:1).\n" +
                        "- **maestro security report**: they suggest removing the fallback secret [2](mailr-cite:2).\n\n" +
                        "Want me to draft replies?")),
                    citations = Fixtures.citations, startedAt = 0, finishedAt = 11_200),
            ), contextConversation = 3066, contextTitle = "PADS Invites Submissions for 2027 Conference"),
            AssistantCallbacks({}, {}, {}, {}, {}, { _, _ -> }, { _, _, _ -> }, {}),
        )
    }

    @Test fun assistantWorking() = shot("assistant-working") {
        AssistantScreen(
            AssistantState(busy = true, turns = listOf(
                ChatTurn("user", "Find the latest invoice"),
                ChatTurn("assistant", streaming = true, items = listOf(TurnItem.Steps(0, listOf(
                    Step("1", "search_conversations", "Searching your mail for invoices", true),
                    Step("2", "get_conversation", "Reading “Invoice #2291 from Acme”"))))),
            )),
            AssistantCallbacks({}, {}, {}, {}, {}, { _, _ -> }, { _, _, _ -> }, {}),
        )
    }

    @Test fun assistantEmpty() = shot("assistant-empty") {
        AssistantScreen(AssistantState(), AssistantCallbacks({}, {}, {}, {}, {}, { _, _ -> }, { _, _, _ -> }, {}))
    }

    @Test fun approvals() = shot("approvals") {
        ApprovalsScreen(Fixtures.approvals, Fixtures.approvalHistory, null, false, SnackbarHostState(), {}, {}, { _, _, _ -> })
    }
}
