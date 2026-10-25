// ACT-SURFACES: android.list android.reader
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.ui.unit.dp
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.getUnclippedBoundsInRoot
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.isHeading
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.ui.conversation.ConversationActions
import io.github.murtazanasir.mailr.ui.conversation.ConversationScreen
import io.github.murtazanasir.mailr.ui.mail.MailPlace
import io.github.murtazanasir.mailr.ui.mail.SelectionBar
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureScreenRoboImage
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * The ⋮ menus of the selection bar and the reader as bottom sheets in groups, so a long
 * menu never runs off the screen. Every action is
 * still there and works from the sheet (mail.mute, mail.spam, mail.important, mail.move,
 * mail.label, mail.snooze, mail.to_task); the sheet scrolls, so on a small phone the last row is
 * reachable too. Output: app/screenshots/menu-sheet-<list|reader>-<light|dark>.png
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class MenuSheetsTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()

    private fun qualifiers(dark: Boolean, h: Int = 891) =
        RuntimeEnvironment.setQualifiers("w411dp-h${h}dp-port-${if (dark) "night" else "notnight"}-xxhdpi")

    private fun list(acted: MutableList<String>, dark: Boolean = false) {
        compose.setContent {
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize()) {
                    androidx.compose.foundation.layout.Column(Modifier.padding(top = 8.dp, start = 8.dp, end = 8.dp)) {
                    SelectionBar(8, {}, { acted += "select_all" }, { acted += it }, { acted += "snooze" }, { acted += "label" }, anyUnread = true,
                        onShowInTab = { acted += "show_in_tab" }, onCategory = { acted += "category" })
                    }
                }
            }
        }
    }

    private fun reader(acted: MutableList<String>, dark: Boolean = false) {
        val actions = ConversationActions(back = {}, act = { acted += it }, toggle = {}, expandAll = {}, star = { _, _ -> },
            reply = { _, _ -> }, openAttachment = { _, _ -> }, showImages = {}, snooze = { acted += "snooze" }, labels = { acted += "label" },
            summarize = { acted += "summarize" }, unsubscribe = {}, mailto = {}, retry = {}, ask = { acted += "ask" },
            toTask = { acted += "task" }, toEvent = { acted += "event" }, moveTo = { acted += "move" }, noteFromEmail = { acted += "note:$it" },
            linkNote = { acted += "link" }, meeting = { acted += "meeting" }, showInTab = { acted += "show_in_tab" }, category = { acted += "category" })
        compose.setContent {
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
                ConversationScreen(Fixtures.detail, null, Fixtures.labels, emptySet(), emptyMap(), null, null, false,
                    SnackbarHostState(), actions)
            }
        }
    }

    private fun headings() = compose.onAllNodes(isHeading()).fetchSemanticsNodes()
        .mapNotNull { n -> n.config.getOrElseNullable(androidx.compose.ui.semantics.SemanticsProperties.Text) { null }?.joinToString() }

    @Test fun theSelectionMenuIsAGroupedSheetLight() = selectionSheet(false)
    @Test fun theSelectionMenuIsAGroupedSheetDark() = selectionSheet(true)

    private fun selectionSheet(dark: Boolean) {
        run {
            qualifiers(dark)
            val acted = mutableListOf<String>()
            list(acted, dark)
            compose.onNodeWithContentDescription("More actions").performClick()
            compose.waitForIdle()
            assertEquals(listOf("8 selected", "Organise", "More"), headings())
            captureScreenRoboImage("screenshots/menu-sheet-list-${if (dark) "dark" else "light"}.png")
            compose.onNodeWithText("Organise").performScrollTo()
            compose.onNodeWithText("Report spam").performScrollTo().performClick()
            assertEquals(listOf("spam"), acted)
        }
    }

    /** ACT mail.mute, ACT mail.important, ACT mail.label, ACT mail.snooze: one tap each from the selection sheet. */
    @Test fun everySelectionActionIsReachable() {
        qualifiers(false, h = 600)
        val acted = mutableListOf<String>()
        list(acted)
        for (label in listOf("Select all", "Mark unread", "Star", "Mark important", "Snooze", "Label as…", "Show in tab…", "Category…", "Mute", "Report spam")) {
            compose.onNodeWithContentDescription("More actions").performClick()
            compose.onNodeWithText(label).performScrollTo().performClick()
            compose.waitForIdle()
            compose.onNodeWithTag("action-sheet").assertDoesNotExistCompat()
        }
        assertEquals(listOf("select_all", "unread", "star", "important", "snooze", "label", "show_in_tab", "category", "mute", "spam"), acted)
    }

    @Test fun theReaderMenuIsAGroupedSheetThatFitsTheScreenLight() = readerSheet(false)
    @Test fun theReaderMenuIsAGroupedSheetThatFitsTheScreenDark() = readerSheet(true)

    private fun readerSheet(dark: Boolean) {
        run {
            qualifiers(dark)
            val acted = mutableListOf<String>()
            reader(acted, dark)
            compose.onNodeWithContentDescription("More").performClick()
            compose.waitForIdle()
            val h = headings()
            assertTrue("groups in order: $h", h.containsAll(listOf("Create from email", "Organise", "More")) &&
                h.indexOf("Create from email") < h.indexOf("Organise") && h.indexOf("Organise") < h.indexOf("More"))
            captureScreenRoboImage("screenshots/menu-sheet-reader-${if (dark) "dark" else "light"}.png")
            // The sheet scrolls: its last row comes into view, inside the screen.
            val spam = compose.onNodeWithText("Report spam").performScrollTo()
            val b = spam.getUnclippedBoundsInRoot()
            assertTrue("Report spam on screen: $b", b.bottom.value <= 891f)
            spam.performClick()
            assertEquals(listOf("spam"), acted)
        }
    }

    /** ACT mail.move, ACT mail.to_task, ACT mail.mute: every reader action from the sheet on a short screen. */
    @Test fun everyReaderActionIsReachable() {
        qualifiers(false, h = 600)
        val acted = mutableListOf<String>()
        reader(acted)
        val important = Fixtures.detail.conversation.important
        val labels = listOf("Summarise", "Ask Ambrose", "Snooze", if (important) "Mark not important" else "Mark important", "Task", "Event", "Meeting with these people", "Note",
            "Checklist", "Link to a note…", "Move to…", "Label as…", "Show in tab…", "Category…", "Mute", "Report spam")
        for (label in labels) {
            compose.onNodeWithContentDescription("More").performClick()
            compose.onNode(hasText(label) and SemanticsMatcher.keyIsDefined(androidx.compose.ui.semantics.SemanticsActions.OnClick))
                .performScrollTo().performClick()
            compose.waitForIdle()
        }
        assertEquals(listOf("summarize", "ask", "snooze", if (important) "not_important" else "important", "task", "event", "meeting", "note:text", "note:checklist", "link",
            "move", "label", "show_in_tab", "category", "mute", "spam"), acted)
        assertTrue(compose.onAllNodesWithText("Report spam").fetchSemanticsNodes().isEmpty())
    }

    private fun androidx.compose.ui.test.SemanticsNodeInteraction.assertDoesNotExistCompat() = assertDoesNotExist()
}
