package io.github.murtazanasir.mailr.ui.assistant

import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.test.getBoundsInRoot
import androidx.compose.ui.test.getUnclippedBoundsInRoot
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performSemanticsAction
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.test.performScrollToIndex
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.swipeDown
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.Approval
import io.github.murtazanasir.mailr.ui.present.Display
import io.github.murtazanasir.mailr.ui.present.DisplayField
import io.github.murtazanasir.mailr.ui.present.DisplayValue
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The Android assistant must follow the reply while the user is at the bottom.
 * At the bottom, the chat keeps the newest output in view while the reply streams, steps
 * run and an approval card opens; scrolled up, nothing moves and "Jump to latest" shows, also once
 * the turn waits on the card.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class ChatFollowTest {
    @get:Rule val compose = createComposeRule()

    private val noop = AssistantCallbacks({}, {}, {}, {}, {}, { _, _ -> }, { _, _, _ -> }, {})

    private fun para(n: Int) = (1..n).joinToString("\n\n") { "Paragraph $it: the meeting moved and the notes say why, in some detail." }

    private val card = Approval(7, action = "calendar.update", actor = "Ambrose", status = "pending",
        display = Display(icon = "event", title = "Change “Weekly sync”", fields = listOf(
            DisplayField("Applies to", DisplayValue(t = "text", text = "Tue 6 Oct, 10:00–11:00 AM (this occurrence only)")),
            DisplayField("Time", DisplayValue(t = "diff", text = "a → b", before = "Tue 6 Oct, 10:00–11:00 AM", after = "Tue 6 Oct, 2:00–3:00 PM")))))

    private fun state(text: String, steps: Boolean = false, approval: Boolean = false, busy: Boolean = true): AssistantState {
        val items = buildList {
            add(TurnItem.Text(0, text))
            if (steps) add(TurnItem.Steps(0, listOf(Step("a", "update_event", "Changing a calendar event", waiting = approval, approvalId = 7))))
            if (approval) add(TurnItem.Approval(0, 7))
        }
        return AssistantState(turns = listOf(ChatTurn("user", "Move Tuesday's sync to 2pm"),
            ChatTurn("assistant", items = items, approvals = if (approval) listOf(card) else emptyList(), streaming = busy)), busy = busy)
    }

    /** The bottom of the node with [text] is inside the chat log (the newest output is in view). */
    private fun inView(text: String): Boolean {
        val log = compose.onNodeWithTag("chat-log").getBoundsInRoot()
        val node = compose.onNodeWithText(text, substring = true).getUnclippedBoundsInRoot()
        return node.bottom <= log.bottom + 1.dp && node.bottom > log.top
    }

    @Test fun atTheBottomTheChatFollowsStreamingStepsAndTheApprovalCard() {
        val s = mutableStateOf(state(para(4)))
        compose.setContent { MaterialTheme { AssistantScreen(s.value, noop) } }
        compose.waitForIdle()
        // The reply grows in place (one list item) while it streams.
        for (n in 5..40 step 5) {
            s.value = state(para(n) + "\n\nTyping $n")
            compose.waitForIdle()
            assertTrue("streamed text $n is in view", inView("Typing $n"))
        }
        s.value = state(para(40), steps = true)
        compose.waitForIdle()
        assertTrue("the step is in view", inView("Changing a calendar event"))
        // The turn stops and waits on the card: the card's buttons are in view.
        s.value = state(para(40), steps = true, approval = true, busy = false)
        compose.waitForIdle()
        assertTrue("the approval card is in view", inView("Allow once"))
        compose.onNodeWithTag("chat-jump").assertDoesNotExist()
    }

    /** TalkBack scrolls with an accessibility action, not a drag: the chat must not pull it back down. */
    @Test fun anAccessibilityScrollUpIsNotUndone() {
        val s = mutableStateOf(state(para(40)))
        compose.setContent { MaterialTheme { AssistantScreen(s.value, noop) } }
        compose.waitForIdle()
        compose.onNodeWithTag("chat-log").performScrollToIndex(0)
        compose.waitForIdle()
        compose.onNodeWithText("Move Tuesday's sync to 2pm").assertIsDisplayed()
        s.value = state(para(42))
        compose.waitForIdle()
        compose.onNodeWithText("Move Tuesday's sync to 2pm").assertIsDisplayed()
        compose.onNodeWithTag("chat-jump").assertExists()
    }

    @Test fun scrolledUpTheChatStaysAndOffersJumpToLatestAlsoWhileTheCardWaits() {
        val s = mutableStateOf(state(para(40)))
        compose.setContent { MaterialTheme { AssistantScreen(s.value, noop) } }
        compose.waitForIdle()
        compose.onNodeWithTag("chat-log").performTouchInput { swipeDown() }
        compose.waitForIdle()
        val before = compose.onNodeWithText("Paragraph 1:", substring = true).getUnclippedBoundsInRoot().top
        s.value = state(para(40), steps = true, approval = true, busy = false)
        compose.waitForIdle()
        // Not yanked down: the paragraph the reader is on stays where it was.
        val after = compose.onNodeWithText("Paragraph 1:", substring = true).getUnclippedBoundsInRoot().top
        assertTrue("not moved: $before vs $after", before == after)
        compose.onNodeWithTag("chat-jump").assertExists()
        compose.onNodeWithTag("chat-jump").performClick()
        compose.waitForIdle()
        assertTrue("jump shows the card", inView("Allow once"))
        compose.onNodeWithTag("chat-jump").assertDoesNotExist()
    }

    /** A reply that streams faster than the frames: the follow's own scrolls never detach the log (7 Oct 2026). */
    @Test fun aFastStreamAtTheBottomNeverDetaches() {
        val s = mutableStateOf(state(para(30)))
        compose.setContent { MaterialTheme { AssistantScreen(s.value, noop) } }
        compose.waitForIdle()
        compose.mainClock.autoAdvance = false
        for (n in 31..60) {
            s.value = state(para(n) + "\n\nTyping $n")
            compose.mainClock.advanceTimeByFrame()
        }
        compose.mainClock.autoAdvance = true
        compose.waitForIdle()
        assertTrue("the newest line is in view", inView("Typing 60"))
        compose.onNodeWithTag("chat-jump").assertDoesNotExist()
    }

    /** Like the web: a small scroll up detaches, but the pill waits until the latest output is out of sight. */
    @Test fun thePillShowsOnlyWhenTheLatestOutputIsOutOfSight() {
        val s = mutableStateOf(state(para(40)))
        compose.setContent { MaterialTheme { AssistantScreen(s.value, noop) } }
        compose.waitForIdle()
        val px = with(compose.density) { 100.dp.toPx() }
        compose.onNodeWithTag("chat-log").performSemanticsAction(SemanticsActions.ScrollBy) { it(0f, -px) }
        compose.waitForIdle()
        // 100 dp up: past the 48 dp slack (detached), short of the 150 dp jump distance (no pill).
        compose.onNodeWithTag("chat-jump").assertDoesNotExist()
        val before = compose.onNodeWithText("Paragraph 40:", substring = true).getUnclippedBoundsInRoot().top
        s.value = state(para(46))
        compose.waitForIdle()
        // Detached: the new paragraphs do not move the log, and now the latest output is far below.
        assertTrue("not moved", before == compose.onNodeWithText("Paragraph 40:", substring = true).getUnclippedBoundsInRoot().top)
        compose.onNodeWithTag("chat-jump").assertExists()
    }
}
