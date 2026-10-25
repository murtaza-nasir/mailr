// ACT-SURFACES: android.assistant
package io.github.murtazanasir.mailr.ui.assistant

import android.app.Application
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.unit.LayoutDirection
import io.github.murtazanasir.mailr.data.agent.ChatSummary
import io.github.murtazanasir.mailr.data.agent.TextPart
import androidx.compose.ui.test.isDialog
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * Chats (agent.chat_list), light and dark: the list on a phone, the list as a pane beside the open chat
 * on an unfolded Fold, the list in ar-XB (right to left), the empty and offline states, and a turn
 * that is reconnecting after its stream dropped (agent.stream_resume), and one that lost the connection
 * with Retry under it (agent.rerun). Search (agent.chat_search): the field in the top bar and the results
 * with the text around each hit, the hits marked; Rename (agent.chat_rename): the dialog, and the open
 * chat's long title in the Ambrose top bar. Slash commands (agent.commands): the picker open over the message box.
 * Output: app/screenshots/chats-<theme>-<shot>.png. Demo names only.
 */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class ChatsScreenshotTest(private val dark: Boolean) {
    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun params(): List<Array<Any>> = listOf(arrayOf<Any>(false), arrayOf<Any>(true))

        private const val PHONE = "w411dp-h891dp-port"
        private const val FOLD = "w841dp-h701dp-land"
        private const val RTL = "ar-rXB-ldrtl-w411dp-h891dp-port"

        /** The demo's chats, around the pinned test time (Fri 2 Oct 2026, 10:40 in Chicago). */
        val CHATS = listOf(
            ChatSummary("c1", "Find the latest Northwind invoice", "2026-10-02T09:12:00-05:00",
                "The latest invoice is INV-2041 from Northwind Traders, for $1,240, due on 15 October."),
            ChatSummary("c2", "When is Priya free next week?", "2026-10-02T08:03:00-05:00",
                "Priya is free on Tuesday afternoon and Thursday morning. She runs the reading group on Tuesdays at 4."),
            ChatSummary("c3", "Agenda for Monday's standup", "2026-10-01T16:40:00-05:00",
                "Here is a short agenda: the Lumen Coffee launch, the open invoices, and who covers support on Friday."),
            ChatSummary("c4", "What needs a reply from me this week?", "2026-09-30T11:20:00-05:00",
                "Three conversations wait for you: Dana Whitfield about the lease, Theo Martin about the photos, and the school trip form."),
            ChatSummary("c5", "Summarise the board pack", "2026-09-27T14:05:00-05:00", null),
        )

        val CHAT = AssistantState(turns = listOf(
            ChatTurn("user", "When is Priya free next week?"),
            ChatTurn("assistant", items = listOf(TurnItem.Text(0, "Priya is free on Tuesday afternoon and Thursday morning. She runs the reading group on Tuesdays at 4.",
                "Priya is free on Tuesday afternoon and Thursday morning. She runs the reading group on Tuesdays at 4.")), finishedAt = 1),
        ), sessionId = "c2")

        /** Results for "northwind": a hit in an answer and one in a title. */
        val RESULTS = listOf(
            CHATS[0].copy(match = "answer", titleParts = listOf(TextPart("Find the latest "), TextPart("Northwind", true), TextPart(" invoice")),
                snippetParts = listOf(TextPart("The latest invoice is INV-2041 from "), TextPart("Northwind", true),
                    TextPart(" Traders, for $1,240, due on 15 October."))),
            CHATS[2].copy(match = "answer", snippetParts = listOf(TextPart("…the open invoices, and the "), TextPart("Northwind", true),
                TextPart(" renewal. Who covers support on Friday?"))),
        )

        val noop = ChatsCallbacks({}, {}, {}, {}, {})
        val chatNoop = AssistantCallbacks({}, {}, {}, {}, {}, { _, _ -> }, { _, _, _ -> }, {}, openChats = {})
    }

    @get:Rule val compose = createComposeRule()
    private val mode get() = if (dark) "dark" else "light"
    private val now = AppClock.now()

    private fun set(qualifiers: String, content: @Composable () -> Unit) {
        RuntimeEnvironment.setQualifiers("$qualifiers-${if (dark) "night" else "notnight"}-xxhdpi")
        compose.setContent {
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize()) { content() }
            }
        }
        compose.mainClock.advanceTimeBy(1000)
        compose.waitForIdle()
    }

    @Test fun list() {
        set(PHONE) { ChatListScreen(ChatsUi(loading = false, chats = CHATS), "Ambrose", "c2", noop, now = now) }
        compose.onRoot().captureRoboImage("screenshots/chats-$mode-list.png")
    }

    @Test fun paneOnAFold() {
        set(FOLD) {
            ChatsLayout(wide = true, listOpen = true,
                list = { mod -> ChatListScreen(ChatsUi(loading = false, chats = CHATS), "Ambrose", "c2", noop, modifier = mod, wide = true, now = now) },
                chat = { mod -> AssistantScreen(CHAT, chatNoop, modifier = mod) })
        }
        compose.onRoot().captureRoboImage("screenshots/chats-$mode-fold.png")
    }

    /** agent.chat_rename from the top bar: the open chat's title under the name, cut to one line when it is long. */
    @Test fun titleInTheTopBar() {
        set(PHONE) {
            AssistantScreen(CHAT.copy(title = "When is Priya free next week, and can the reading group move to Thursday afternoon?"),
                chatNoop.copy(renameChat = {}, openMemory = {}))
        }
        compose.onRoot().captureRoboImage("screenshots/chats-$mode-title.png")
    }

    /** agent.commands: "/re" typed in the message box lists the commands that match, then the skills. */
    @Test fun commandPicker() {
        val typed = io.github.murtazanasir.mailr.ui.ask.AskUi(null, text = "/re")
        set(PHONE) {
            AssistantScreen(CHAT.copy(commands = ChatCommandsTest.COMMANDS, skills = ChatCommandsTest.SKILLS), chatNoop,
                voice = ComposerVoice(typed, {}, {}, {}, {}))
        }
        compose.onRoot().captureRoboImage("screenshots/chats-$mode-commands.png")
    }

    @Test fun rightToLeft() {
        var direction: LayoutDirection? = null
        set(RTL) {
            direction = LocalLayoutDirection.current
            ChatListScreen(ChatsUi(loading = false, chats = CHATS), "Ambrose", "c2", noop, now = now)
        }
        assertEquals(LayoutDirection.Rtl, direction)
        compose.onRoot().captureRoboImage("screenshots/chats-$mode-rtl.png")
    }

    @Test fun search() {
        set(PHONE) { ChatListScreen(ChatsUi(loading = false, chats = CHATS, query = "northwind", results = RESULTS), "Ambrose", "c2", noop, now = now) }
        compose.onRoot().captureRoboImage("screenshots/chats-$mode-search.png")
    }

    @Test fun searchWithoutMatch() {
        set(PHONE) { ChatListScreen(ChatsUi(loading = false, chats = CHATS, query = "zebracorn", results = emptyList()), "Ambrose", null, noop, now = now) }
        compose.onRoot().captureRoboImage("screenshots/chats-$mode-no-match.png")
    }

    @Test fun renameDialog() {
        set(PHONE) { RenameChatDialog(CHATS[1], onDismiss = {}) {} }
        compose.onNode(isDialog()).captureRoboImage("screenshots/chats-$mode-rename.png")
    }

    @Test fun empty() {
        set(PHONE) { ChatListScreen(ChatsUi(loading = false), "Ambrose", null, noop, now = now) }
        compose.onRoot().captureRoboImage("screenshots/chats-$mode-empty.png")
    }

    @Test fun offline() {
        set(PHONE) { ChatListScreen(ChatsUi(loading = false, problem = ChatsProblem.OFFLINE), "Ambrose", null, noop, now = now) }
        compose.onRoot().captureRoboImage("screenshots/chats-$mode-offline.png")
    }

    @Test fun reconnecting() {
        val s = AssistantState(turns = listOf(ChatTurn("user", "Tidy up the Northwind newsletters"),
            ChatTurn("assistant", items = listOf(TurnItem.Text(0, "Archived the two Northwind newsletters. One more ")),
                streaming = true, reconnecting = true)), busy = true, sessionId = "c9")
        set(PHONE) { AssistantScreen(s, chatNoop) }
        compose.onRoot().captureRoboImage("screenshots/chats-$mode-reconnecting.png")
    }

    /** Re-attaching gave up: "Connection lost" and Retry under the last turn. */
    @Test fun connectionLostWithRetry() {
        val s = AssistantState(turns = listOf(ChatTurn("user", "Move those items from my to-do list to the 3D printing list"),
            ChatTurn("assistant", lost = true, error = "Connection lost.", finishedAt = 1)), sessionId = "c9")
        set(PHONE) { AssistantScreen(s, chatNoop.copy(retry = {})) }
        compose.onRoot().captureRoboImage("screenshots/chats-$mode-retry.png")
    }
}
