package io.github.murtazanasir.mailr.ui.assistant

import android.app.Application
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.swipeDown
import io.github.murtazanasir.mailr.data.agent.AgentEvent
import io.github.murtazanasir.mailr.data.api.Approval
import io.github.murtazanasir.mailr.ui.present.Display
import io.github.murtazanasir.mailr.ui.present.Outcome
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The chat on android: a reply is one ordered timeline, the
 * card a step asked for sits after the steps and changes in place, the continuation joins the turn,
 * and the log follows the stream until the reader scrolls up (§4). ACT approval.approve
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class ChatTimelineTest {
    @get:Rule val compose = createComposeRule()

    private fun fold(vararg evs: AgentEvent) = evs.fold(emptyList<TurnItem>()) { items, ev -> ChatTimeline.apply(items, ev) }

    private val interleaved = arrayOf(
        AgentEvent.Token("First I look for receipts.", 0),
        AgentEvent.Segment(0, "First I look for receipts.", emptyList()),
        AgentEvent.StepStarted("a", "search_conversations", "Searching your mail", "search", 0),
        AgentEvent.StepDone("a", "search_conversations", "Searching your mail", true, "12 conversations", 12, 40, "search", 0),
        AgentEvent.Token("Found them. Filing now.", 1),
        AgentEvent.Segment(1, "Found them. Filing now.", emptyList()),
        AgentEvent.StepStarted("b", "manage_filter", "Creating a filter", "label", 1),
        AgentEvent.StepDone("b", "manage_filter", "Creating a filter", true, "waiting for your approval", null, 5, "label", 1),
        AgentEvent.ApprovalRequired(41, "Create a filter", emptyList(), 1),
        AgentEvent.Final("Approve the card above.", emptyList(), 2),
    )

    /** No raw internal citation text while the answer is typing. */
    @Test fun streamedTextNeverShowsRawCitationMarkup() {
        val items = fold(
            AgentEvent.Token("Lunch [1](mailr-cite:1) and the memo [[m:12 \"bud", 0),
        )
        assertEquals("Lunch [1](mailr-cite:1) and the memo", (items[0] as TurnItem.Text).shown)
        val done = ChatTimeline.apply(items, AgentEvent.Final("A walk [[event:abc@2026-05-20T13:00:00+00:00 \"Chinatown walk\"]], then lunch.", emptyList(), 0))
        assertEquals("A walk, then lunch.", (done[0] as TurnItem.Text).shown)
        // A cite event does not change the timeline; the view model keeps the citation.
        assertEquals(items, ChatTimeline.apply(items, AgentEvent.Cite(io.github.murtazanasir.mailr.data.api.Citation(1), 0)))
    }

    @Test fun textStaysWhereItWasWrittenAndStepsFollowIt() {
        val items = fold(*interleaved)
        assertEquals(listOf("Text", "Steps", "Text", "Steps", "Approval", "Text"), items.map { it::class.simpleName })
        assertEquals("First I look for receipts.", (items[0] as TurnItem.Text).shown)
        assertEquals("Approve the card above.", (items[5] as TurnItem.Text).shown)
        val asked = (items[3] as TurnItem.Steps).steps.single()
        assertTrue(asked.waiting && asked.approvalId == 41L)
        assertEquals("First I look for receipts.\n\nFound them. Filing now.\n\nApprove the card above.", ChatTimeline.text(items))
    }

    @Test fun theContinuationJoinsTheTurnAfterTheCard() {
        var items = fold(*interleaved)
        items = ChatTimeline.decided(items, 41, "executed")
        items = ChatTimeline.apply(items, AgentEvent.Session("s", null, continuation = true, segment = 3))
        items = ChatTimeline.apply(items, AgentEvent.Final("Done: the filter is live.", emptyList(), 3))
        assertEquals(listOf("Text", "Steps", "Text", "Steps", "Approval", "Text", "Resume", "Text"), items.map { it::class.simpleName })
        val asked = (items[3] as TurnItem.Steps).steps.single()
        assertFalse(asked.waiting)
        assertEquals("approved", asked.summary)
    }

    @Test fun thinkingSitsAtTheEndOnlyWhileWaitingOnTheModel() {
        assertTrue(ChatTimeline.thinking(emptyList(), true) { false })
        assertFalse(ChatTimeline.thinking(listOf(TurnItem.Text(0, "typing")), true) { false })
        assertTrue(ChatTimeline.thinking(listOf(TurnItem.Text(0, "done", "done")), true) { false })
        assertFalse(ChatTimeline.thinking(listOf(TurnItem.Steps(0, listOf(Step("a", "x", "x")))), true) { false })
        assertFalse(ChatTimeline.thinking(listOf(TurnItem.Approval(0, 1)), true) { true })
        assertFalse(ChatTimeline.thinking(emptyList(), false) { false })
    }

    /** One search asked for 16 times used to show 16 lines. Now one line, ×16. */
    @Test fun repeatedCallsFoldIntoOneLine() {
        val evs = mutableListOf<AgentEvent>(
            AgentEvent.StepStarted("a", "search_tasks", "Searching your tasks", "task", 0),
            AgentEvent.StepDone("a", "search_tasks", "Searching your tasks", true, "0 tasks", 0, 30, "task", 0),
        )
        for (i in 1..15) {
            evs += AgentEvent.StepStarted("r$i", "search_tasks", "Searching your tasks", "task", i)
            evs += AgentEvent.StepDone("r$i", "search_tasks", "Searching your tasks", true, "same result as before", null, 0,
                "task", i, repeatOf = "a")
        }
        val items = fold(*evs.toTypedArray()) + TurnItem.Text(16, "Nothing is due.", "Nothing is due.")
        val shown = ChatTimeline.foldRepeatGroups(items)
        assertEquals(listOf("Steps", "Text"), shown.map { it::class.simpleName })
        val rows = ChatTimeline.foldRepeats((shown[0] as TurnItem.Steps).steps)
        assertEquals(listOf(StepRow(rows[0].step, 16)), rows)
        assertEquals("0 tasks", rows[0].step.summary)
        // Text between keeps the groups apart.
        val apart = ChatTimeline.foldRepeatGroups(listOf(items[0], TurnItem.Text(1, "Again.", "Again."), items[1]))
        assertEquals(3, apart.size)
        compose.setContent {
            MaterialTheme { AssistantScreen(AssistantState(turns = listOf(ChatTurn("user", "Deadlines next week?"),
                ChatTurn("assistant", items = items))), noop) }
        }
        compose.waitForIdle()
        compose.onNodeWithText("Searching your tasks ×16 · 0 tasks").assertExists()
    }

    private val noop = AssistantCallbacks({}, {}, {}, {}, {}, { _, _ -> }, { _, _, _ -> }, {})

    @Test fun theScreenShowsTheTimelineInOrderWithTheCardResolvedInPlace() {
        val display = Display(icon = "filter", title = "Create a filter", summary = "Mail from 6 senders gets the label Finance.")
        val items = ChatTimeline.decided(fold(*interleaved), 41, "executed").let {
            ChatTimeline.apply(ChatTimeline.apply(it, AgentEvent.Session("s", null, true, 3)), AgentEvent.Final("Done: the filter is live.", emptyList(), 3))
        }
        val card = Approval(41, action = "rules.write", actor = "Ambrose", status = "executed", display = display,
            outcome = Outcome("executed", "Approved · created the filter"))
        compose.setContent {
            MaterialTheme {
                AssistantScreen(AssistantState(turns = listOf(ChatTurn("user", "File my finance mail"),
                    ChatTurn("assistant", items = items, approvals = listOf(card)))), noop)
            }
        }
        compose.onNodeWithText("Approved · created the filter").assertExists()
        compose.onNodeWithText("After your approval").assertExists()
        compose.onNodeWithText("Done: the filter is live.", substring = true).assertExists()
    }

    @Test fun scrollingUpDetachesAndJumpToLatestReturns() {
        val long = (1..60).joinToString("\n\n") { "Paragraph $it of a long streamed answer about receipts and filters." }
        val turn = ChatTurn("assistant", items = listOf(TurnItem.Text(0, long)), streaming = true)
        compose.setContent {
            MaterialTheme { AssistantScreen(AssistantState(turns = listOf(ChatTurn("user", "Long please"), turn), busy = true), noop) }
        }
        compose.waitForIdle()
        compose.onNodeWithTag("chat-jump").assertDoesNotExist()   // following: at the bottom
        compose.onNodeWithTag("chat-log").performTouchInput { swipeDown() }
        compose.waitForIdle()
        compose.onNodeWithTag("chat-jump").assertExists()
        compose.onNodeWithTag("chat-jump").performClick()
        compose.waitForIdle()
        compose.onNodeWithTag("chat-jump").assertDoesNotExist()
    }
}
