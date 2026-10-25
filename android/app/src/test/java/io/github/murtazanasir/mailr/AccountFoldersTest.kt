// ACT-SURFACES: android.drawer
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollToNode
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.hasScrollAction
import io.github.murtazanasir.mailr.data.api.Label
import io.github.murtazanasir.mailr.data.api.MailAccountInfo
import io.github.murtazanasir.mailr.data.api.ViewCount
import io.github.murtazanasir.mailr.data.repo.MailView
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.ui.mail.AccountFolderPrefs
import io.github.murtazanasir.mailr.ui.mail.DrawerAccountPins
import io.github.murtazanasir.mailr.ui.mail.MailDrawer
import io.github.murtazanasir.mailr.ui.mail.accountLabelView
import io.github.murtazanasir.mailr.ui.mail.accountLabels
import io.github.murtazanasir.mailr.ui.mail.accountView
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * Folders inside each account (ui/mail/MailAccounts.kt accountLabels): with
 * "Show each account separately" on, each account's group in the drawer lists, below Trash, the
 * labels holding its mail and those made in it, as a tree; each opens the label narrowed to the
 * account (ACT mail.account_label_view) with its own unread number. The unified Labels list shows a
 * label made in an account with that account's dot. Making a folder from the drawer: NewFolderTest.
 * Screenshots: app/screenshots/account-folders-<theme>.png
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h2400dp-xxhdpi")
class AccountFoldersTest {
    @get:Rule val compose = createComposeRule()

    private val home = MailAccountInfo(1, "me@example.com", "Home", "blue")
    private val work = MailAccountInfo(2, "work@example.org", "Work", "orange")
    private val labels = Fixtures.labels + Label(20, "Invoices", color = "#188038", homeAccountId = 2)
    private val counts = mapOf(
        accountView(2, "inbox") to ViewCount(1, 3), accountView(1, "inbox") to ViewCount(0, 4),
        // Research/PADS 2027 holds one unread conversation of Work's (its parent counts it too).
        accountLabelView(2, 11) to ViewCount(1, 1), accountLabelView(2, 10) to ViewCount(1, 1),
        accountLabelView(1, 12) to ViewCount(0, 2),
    )

    private fun drawer(dark: Boolean = false, onOpen: (MailView) -> Unit = {}) {
        compose.setContent {
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize()) {
                    // More folders open in both groups (AccountFolderPinsTest covers it closed).
                    MailDrawer(labels, MailView(), null, emptyMap(), "me@example.com", 0, onOpen, {}, {},
                        accounts = listOf(home, work), accountCounts = counts,
                        accountPins = DrawerAccountPins(AccountFolderPrefs(moreOpen = setOf(1L, 2L))))
                }
            }
        }
    }

    @Test fun anAccountListsItsLabelsAndTheirParents() {
        assertEquals(listOf("Invoices", "Research", "Research/PADS 2027"), accountLabels(labels, 2, counts).map { it.name })
        assertEquals(listOf("Job Applications"), accountLabels(labels, 1, counts).map { it.name })
        assertEquals(emptyList<String>(), accountLabels(labels, 3, counts).map { it.name })
    }

    @Test fun aFolderOpensTheLabelThroughItsAccount() {
        var opened: MailView? = null
        drawer { opened = it }
        compose.onNodeWithTag("drawer-account-2").assertExists()
        // Invoices: Work's folder (empty, its home), and the unified label with Work's dot.
        val invoices = compose.onAllNodesWithText("Invoices")
        invoices.assertCountEquals(2)
        invoices[0].performClick()
        assertEquals("acct:2:label:20", opened?.view)
        assertEquals("Invoices · work@example.org", opened?.title)
        compose.onAllNodesWithTag("label-home-2", useUnmergedTree = true).assertCountEquals(1)
        compose.onAllNodesWithText("PADS 2027")[0].performClick()
        assertEquals("acct:2:label:11", opened?.view)
    }

    @Test fun drawerLight() = shot(false)

    @Test fun drawerDark() = shot(true)

    private fun shot(dark: Boolean) {
        drawer(dark)
        compose.onNode(hasScrollAction()).performScrollToNode(hasText("Invoices"))
        compose.onRoot().captureRoboImage("screenshots/account-folders-${if (dark) "dark" else "light"}.png")
    }
}
