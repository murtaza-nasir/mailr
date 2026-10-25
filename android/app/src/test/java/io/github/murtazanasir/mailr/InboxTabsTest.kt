package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.time.AppClock
import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsSelected
import androidx.compose.ui.test.getUnclippedBoundsInRoot
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.InboxLayout
import io.github.murtazanasir.mailr.data.api.LayoutSection
import io.github.murtazanasir.mailr.data.api.LayoutTab
import io.github.murtazanasir.mailr.data.api.TabCounts
import io.github.murtazanasir.mailr.data.repo.MailView
import io.github.murtazanasir.mailr.ui.mail.InboxTabs
import io.github.murtazanasir.mailr.ui.mail.Tone
import io.github.murtazanasir.mailr.ui.mail.tabIndicators
import io.github.murtazanasir.mailr.ui.mail.tabTotal
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.time.LocalDate
import java.time.format.DateTimeFormatter
import java.util.Locale

/** The inbox layout's tabs across the top of the inbox, with the indicators each tab turns on:
 *  two pills on a wide screen, one compact indicator on a phone. */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class InboxTabsTest {
    @get:Rule val compose = createComposeRule()

    private val layout = InboxLayout("priority-later", "Priority + Later", tabs = listOf(
        LayoutTab("inbox", "Inbox", "inbox", "none", listOf(LayoutSection("important", "Important", limit = 10), LayoutSection("everything", "Everything else"))),
        LayoutTab("later", "Later", "laptop", "unread", listOf(LayoutSection("later", "Later", base = "label:19"))),
        LayoutTab("gh", "Github", "info", "unread", listOf(LayoutSection("all", "All", base = "label:20"))),
    ))
    private val financial = LayoutTab("fin", "Financial", "briefcase", "all", listOf(LayoutSection("all", "All", base = "label:21")),
        indicators = listOf("all", "due"))
    private val inFive = AppClock.today().plusDays(5)
    private val dayText = DateTimeFormatter.ofPattern("MMM d", Locale.US).format(inFive)

    @Test fun showsEveryTabWithItsCountAndOpensTheTabTapped() {
        val opened = mutableListOf<MailView>()
        compose.setContent {
            MaterialTheme { InboxTabs(layout, MailView("inbox", layout = "priority-later", tab = "inbox"), mapOf("later" to 0, "gh" to 3)) { opened += it } }
        }
        compose.onNodeWithContentDescription("Inbox").assertIsSelected()
        compose.onNodeWithContentDescription("Later").assertIsDisplayed()
        // A phone has room for one compact indicator: the number alone.
        compose.onNodeWithText("3", useUnmergedTree = true).assertIsDisplayed()
        compose.onNodeWithContentDescription("Github, 3 new").performClick()
        assertEquals(listOf(MailView("inbox", title = "Github", layout = "priority-later", tab = "gh")), opened)
    }

    @Test fun aPhoneShowsADeadlineAsADotWithTheWordsInTheDescription() {
        val l = layout.copy(tabs = layout.tabs + financial)
        compose.setContent {
            MaterialTheme { InboxTabs(l, MailView("inbox", layout = "priority-later", tab = "inbox"), emptyMap(),
                mapOf("fin" to TabCounts(all = 12, dueCount = 2, earliestDue = "${AppClock.today().plusDays(1)}", earliestDueFrom = "Wells Fargo"))) {} }
        }
        compose.onNodeWithTag("tab-due-dot", useUnmergedTree = true).assertIsDisplayed()
        compose.onNodeWithText("12", useUnmergedTree = true).assertDoesNotExist()
    }

    @Config(qualifiers = "w841dp-h701dp")
    @Test fun aWideScreenShowsTheDuePillAndTheTotalRightAfterTheName() {
        val l = layout.copy(tabs = layout.tabs + financial)
        compose.setContent {
            MaterialTheme { InboxTabs(l, MailView("inbox", layout = "priority-later", tab = "inbox"), emptyMap(),
                mapOf("later" to TabCounts(unread = 10), "fin" to TabCounts(all = 1234, dueCount = 2, earliestDue = "$inFive", earliestDueFrom = "Wells Fargo"))) {} }
        }
        compose.onNodeWithText("10 new", useUnmergedTree = true).assertIsDisplayed()
        compose.onNodeWithText("Due $dayText · 2", useUnmergedTree = true).assertIsDisplayed()
        compose.onNodeWithText("1,234", useUnmergedTree = true).assertIsDisplayed()
        // The pills follow the tab's name in order and never cover it.
        val name = compose.onNodeWithText("Financial", useUnmergedTree = true).getUnclippedBoundsInRoot()
        val due = compose.onNodeWithText("Due $dayText · 2", useUnmergedTree = true).getUnclippedBoundsInRoot()
        val all = compose.onNodeWithText("1,234", useUnmergedTree = true).getUnclippedBoundsInRoot()
        assertTrue("due after the name", due.left >= name.right && due.left - name.right < 24.dp)
        assertTrue("all after due", all.left >= due.right)
    }

    @Test fun ordersByImportanceAndSaysOverdueOnceTheDateHasPassed() {
        val today = LocalDate.of(2026, 10, 1)
        val all3 = financial.copy(indicators = listOf("unread", "all", "due"))
        val later = tabIndicators(all3, TabCounts(unread = 5, all = 40, dueCount = 1, earliestDue = "2026-10-09"), today)
        assertEquals(listOf("unread", "due", "all"), later.map { it.kind })
        assertEquals(Tone.Warning, later[1].tone)
        val soon = tabIndicators(all3, TabCounts(unread = 5, dueCount = 2, earliestDue = "2026-10-03", earliestDueFrom = "Wells Fargo"), today)
        assertEquals("Due Oct 3 · 2", soon[0].text)
        assertEquals(Tone.Urgent, soon[0].tone)
        assertEquals("2 due · earliest Sat 3 Oct (Wells Fargo)", soon[0].title)
        assertEquals("Overdue", tabIndicators(all3, TabCounts(dueCount = 1, earliestDue = "2026-09-29"), today)[0].text)
        assertEquals("200,000+", tabTotal(200_001))
        assertEquals("7", tabTotal(7))
    }
}
