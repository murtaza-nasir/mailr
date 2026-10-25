package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.width
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.test.assertCountEquals
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.click
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.ConversationSummary
import io.github.murtazanasir.mailr.data.api.Participant
import io.github.murtazanasir.mailr.data.repo.MailView
import io.github.murtazanasir.mailr.data.settings.SwipeAction
import io.github.murtazanasir.mailr.ui.mail.ConversationRow
import io.github.murtazanasir.mailr.ui.mail.rowSender
import io.github.murtazanasir.mailr.ui.mail.rowSnippet
import io.github.murtazanasir.mailr.ui.mail.viewLabelIds
import io.github.murtazanasir.mailr.ui.mail.contextLabelOf
import io.github.murtazanasir.mailr.data.api.InboxLayout
import io.github.murtazanasir.mailr.data.api.LayoutSection
import io.github.murtazanasir.mailr.data.api.LayoutTab
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** android.list row anatomy: labels on the top line, no repeated label, snippet or empty line. */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class ConversationRowTest {
    @get:Rule val compose = createComposeRule()

    private val labelMap = RowFixtures.labels.associateBy { it.id }

    private fun row(c: ConversationSummary, hidden: Set<Long> = emptySet(), compact: Boolean = false) = compose.setContent {
        MaterialTheme {
            ConversationRow(c, labelMap, selected = false, selectionMode = false, activated = false, compact = compact,
                swipeStart = SwipeAction.ARCHIVE, swipeEnd = SwipeAction.TRASH, onClick = {}, onLongClick = {},
                onToggleSelect = {}, onStar = {}, onSwipe = {}, hiddenLabelIds = hidden)
        }
    }

    private fun bounds(text: String): Rect = compose.onNodeWithText(text, useUnmergedTree = true).fetchSemanticsNode().boundsInRoot

    @Test fun snippetThatRepeatsTheSubjectIsDropped() {
        assertEquals("", rowSnippet("Screenshot (Oct 1, 2026 15:49:33)", "Screenshot (Oct 1, 2026 15:49:33)"))
        assertEquals("", rowSnippet("mailr", "  "))
        assertEquals("https://docs.vllm.ai", rowSnippet("Read later: vLLM notes", "Read later: vLLM notes. https://docs.vllm.ai"))
        assertEquals("Lunchtime works for me", rowSnippet("Lunch", "Lunchtime works for me"))
        assertEquals("Added my comments", rowSnippet("Re: Quarterly plan", "Added my comments"))
        assertEquals("Title body", rowSnippet("Title", "# Title **Title** body"))
    }

    @Test fun repeatedSubjectGoesOnlyAtASentenceOrLineBoundary() {
        // A sentence that starts with the subject stays whole (it used to show "is 715204. It expires…").
        val code = "Your Tandem Bank sign-in code is 715204. It expires in 10 minutes."
        assertEquals(code, rowSnippet("Your Tandem Bank sign-in code", code))
        assertEquals("Your code, as requested: 1234", rowSnippet("Your code", "Your code, as requested: 1234"))
        assertEquals("Version 2.5 is out", rowSnippet("Version 2", "Version 2.5 is out"))
        assertEquals("Sign-in-code: 1234", rowSnippet("Sign-in", "Sign-in-code: 1234"))
        for (sep in listOf("\n", ": ", " — ", " – ", " - ", " | ", ". ")) {
            assertEquals("bring the slides", rowSnippet("Weekly notes", "Weekly notes${sep}bring the slides"))
        }
        assertEquals("it arrives Friday", rowSnippet("Order shipped", "Order shipped- it arrives Friday"))
        assertEquals("body", rowSnippet("Title", "Title Title - body"))
        assertEquals("", rowSnippet("Title", "# Title\n**Title**"))
        assertEquals("Title Titleist rules", rowSnippet("Title", "Title Titleist rules"))
    }

    @Test fun noteToSelfIsNamedSo() {
        val me = Participant("me@example.com", "Alex", me = true)
        assertEquals("Note to self", rowSender(ConversationSummary(1, participants = listOf(me), toSelf = true)))
        // A draft nobody has been sent is not a note.
        assertEquals("me", rowSender(ConversationSummary(1, participants = listOf(me), toSelf = true, draftCount = 1)))
        assertEquals("Alice, me", rowSender(ConversationSummary(1, participants = listOf(Participant("a@x", "Alice Chen"), me), messageCount = 2)))
    }

    /** A sent email with no reply yet names its recipients in the Sent list, never "Note to self". */
    @Test fun aSentEmailWithoutAReplyNamesItsRecipients() {
        val me = Participant("me@example.com", "Alex", me = true)
        val dana = Participant("dana@outside.example", "Dana Whitfield")
        assertEquals("To: Dana Whitfield", rowSender(ConversationSummary(1, participants = listOf(me), recipients = listOf(dana))))
        assertEquals("To: Dana, Lee", rowSender(ConversationSummary(1, participants = listOf(me), messageCount = 2,
            recipients = listOf(dana, Participant("lee@outside.example", "Lee Park")))))
        // Only the user, but nothing says to whom (an older server): the senders, never "Note to self".
        assertEquals("me", rowSender(ConversationSummary(1, participants = listOf(me))))
        // A reply from Dana: the senders again.
        assertEquals("me, Dana", rowSender(ConversationSummary(1, participants = listOf(me, dana), messageCount = 2)))
    }

    @Test fun theViewsOwnLabelIsHidden() {
        assertEquals(setOf(20L), viewLabelIds(RowFixtures.laterView, RowFixtures.labels))
        assertEquals(setOf(21L), viewLabelIds(MailView("label:21", title = "Projects"), RowFixtures.labels))
        assertEquals(emptySet<Long>(), viewLabelIds(MailView(), RowFixtures.labels))
        // A search inside a label lists mail from anywhere; nothing is implied.
        assertEquals(emptySet<Long>(), viewLabelIds(MailView("label:21", q = "x", title = "Search"), RowFixtures.labels))
    }

    /** A tab named "Read later" built on the label "Later": the label goes by id, not by the tab's name. */
    @Test fun tabBuiltOnALabelHidesThatLabelById() {
        val layout = InboxLayout("custom", "Mine", tabs = listOf(
            LayoutTab("main", "Inbox", sections = listOf(LayoutSection("all", "Everything"))),
            LayoutTab("rl", "Read later", sections = listOf(LayoutSection("rl", "Read later", base = "label:20"))),
            LayoutTab("proj", "Projects", sections = listOf(LayoutSection("p", "Projects", base = "label:20"))),
        ))
        val readLater = MailView("inbox", title = "Read later", layout = "custom", tab = "rl")
        assertEquals(20L, contextLabelOf(readLater, layout))
        assertEquals(setOf(20L), viewLabelIds(readLater, RowFixtures.labels, contextLabelOf(readLater, layout)))
        // A tab named after another label still hides only the label it is built on.
        val proj = MailView("inbox", title = "Projects", layout = "custom", tab = "proj")
        assertEquals(setOf(20L), viewLabelIds(proj, RowFixtures.labels, contextLabelOf(proj, layout)))
        assertEquals(null, contextLabelOf(MailView("inbox", layout = "custom", tab = "main"), layout))
        assertEquals(21L, contextLabelOf(MailView("label:21", title = "Projects"), layout))

        row(RowFixtures.later[2].copy(labelIds = listOf(1, 20, 21)), hidden = viewLabelIds(proj, RowFixtures.labels, 20L))
        compose.onAllNodesWithText("Later").assertCountEquals(0)
        compose.onNodeWithText("Projects").assertExists()
    }

    @Test fun laterRowHidesTheLaterLabel() {
        row(RowFixtures.later[2], hidden = viewLabelIds(RowFixtures.laterView, RowFixtures.labels))
        compose.onAllNodesWithText("Later").assertCountEquals(0)
        compose.onNodeWithText("Projects").assertExists()
        compose.onNodeWithText("Note to self").assertExists()
    }

    @Test fun labelsSitOnTheTopLineBeforeTheDate() {
        row(RowFixtures.inbox[0])
        val sender = bounds("Dana, me")
        val chip = bounds("Projects")
        val subject = bounds("Module 6 deck review before Thursday")
        // Same line as the sender, above the subject; no row of its own.
        assertTrue(chip.top < subject.top && chip.bottom <= subject.top + 2)
        assertTrue(chip.left > sender.left)
        // Two chips, then +n for the rest (Projects, CHEM 101, Research).
        compose.onNodeWithText("CHEM 101").assertExists()
        compose.onAllNodesWithText("Research").assertCountEquals(0)
        compose.onNodeWithText("+1").assertExists()
    }

    @Test fun narrowRowsShowOneLabel() {
        compose.setContent {
            MaterialTheme {
                Box(Modifier.width(320.dp)) {
                    ConversationRow(RowFixtures.inbox[0], labelMap, selected = false, selectionMode = false, activated = false,
                        compact = false, swipeStart = SwipeAction.ARCHIVE, swipeEnd = SwipeAction.TRASH, onClick = {},
                        onLongClick = {}, onToggleSelect = {}, onStar = {}, onSwipe = {})
                }
            }
        }
        compose.onNodeWithText("Projects").assertExists()
        compose.onNodeWithText("+2").assertExists()
    }

    @Test fun duplicateSnippetIsNotShownTwice() {
        row(RowFixtures.later[0])
        compose.onAllNodesWithText("Screenshot (Oct 1, 2026 15:49:33)").assertCountEquals(1)
    }

    @Test fun missingSnippetLeavesNoGap() {
        val withSnippet = RowFixtures.later[4]
        row(withSnippet.copy(snippet = ""))
        val short = compose.onRoot().fetchSemanticsNode().boundsInRoot.height
        val subject = bounds(withSnippet.subject)
        // The row ends one subject line below the top line (plus padding); no blank snippet line.
        val density = compose.density.density
        assertTrue("row ${short / density}dp", short / density < 72f)
        assertTrue(subject.bottom / density < 60f)
    }

    @Test fun attachmentsJoinTheSnippetLine() {
        row(RowFixtures.inbox[2])
        // One text: the paperclip, the first file (+ the rest) and the snippet, on the snippet line.
        val line = compose.onNodeWithText("PADS_2027_CFP.pdf +1", substring = true, useUnmergedTree = true).fetchSemanticsNode()
        val text = line.config[androidx.compose.ui.semantics.SemanticsProperties.Text].joinToString("")
        assertTrue(text, text.contains("PADS 2027 CALL FOR PAPERS"))
        assertTrue(line.boundsInRoot.top >= bounds("PADS Invites Submissions for 2027 Conference").bottom - 2)
    }

    @Test fun attachmentsWithoutSnippetGoToTheTopLine() {
        row(RowFixtures.later[0])
        val att = compose.onNodeWithContentDescription("Attachment Screenshot_20261001_154933.png", useUnmergedTree = true)
            .fetchSemanticsNode().boundsInRoot
        val subject = bounds("Screenshot (Oct 1, 2026 15:49:33)")
        assertTrue(att.bottom <= subject.top + 2)
    }

    @Test fun starHasA48dpTouchTarget() {
        var starred = 0
        compose.setContent {
            MaterialTheme {
                ConversationRow(RowFixtures.inbox[3], labelMap, selected = false, selectionMode = false, activated = false,
                    compact = false, swipeStart = SwipeAction.ARCHIVE, swipeEnd = SwipeAction.TRASH, onClick = {},
                    onLongClick = {}, onToggleSelect = {}, onStar = { starred++ }, onSwipe = {})
            }
        }
        val star = compose.onNodeWithContentDescription("Star")
        val touch = star.fetchSemanticsNode().touchBoundsInRoot
        val density = compose.density.density
        assertTrue("star ${touch.width / density}x${touch.height / density}dp", touch.width / density >= 47.9f && touch.height / density >= 47.9f)
        // A tap near the top of the drawn star still lands.
        star.performTouchInput { click(androidx.compose.ui.geometry.Offset(centerX, 2f)) }
        star.performClick()
        assertEquals(2, starred)
    }

    @Test fun signalsAndActionKeepTheirPlace() {
        row(RowFixtures.inbox[1].copy(signals = listOf(io.github.murtazanasir.mailr.data.api.Signal("needs_reply"))))
        val subject = bounds("Your order has shipped")
        val signal = bounds("Reply needed")
        assertTrue(signal.top < subject.bottom && signal.bottom > subject.top)
        // ACT mail.primary_action: the chip stays under the snippet.
        assertTrue(bounds("Track package").top > bounds(RowFixtures.inbox[1].snippet).bottom - 2)
    }
}
