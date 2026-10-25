// ACT-SURFACES: android.drawer android.list
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.onAllNodesWithText
import io.github.murtazanasir.mailr.data.api.MailAccountInfo
import io.github.murtazanasir.mailr.data.api.ViewCount
import io.github.murtazanasir.mailr.data.repo.MailView
import io.github.murtazanasir.mailr.data.settings.SwipeAction
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.ui.mail.AccountColours
import io.github.murtazanasir.mailr.ui.mail.ConversationRow
import io.github.murtazanasir.mailr.ui.mail.LocalMailAccounts
import io.github.murtazanasir.mailr.ui.mail.MailDrawer
import io.github.murtazanasir.mailr.ui.mail.accountView
import io.github.murtazanasir.mailr.ui.mail.mailboxHeading
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * Several mail accounts in one mailbox (backend core/mailaccounts.py, ui/mail/MailAccounts.kt): the
 * drawer's heading says "All accounts" (one account: its address), each account's own folders are
 * listed when the server counts them (ACT mail.account_view), and rows carry their account's dot
 * (ACT mail.account_colour). Screenshots: app/screenshots/accounts-drawer-<theme>.png
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h1800dp-xxhdpi")
class MailAccountsTest {
    @get:Rule val compose = createComposeRule()

    private val home = MailAccountInfo(1, "me@example.com", "Home", "blue")
    private val work = MailAccountInfo(2, "work@example.org", "Work", "orange")
    private val counts = mapOf(accountView(2, "inbox") to ViewCount(3, 7), accountView(2, "spam") to ViewCount(1, 1),
        accountView(1, "inbox") to ViewCount(0, 4))

    private fun drawer(accounts: List<MailAccountInfo>, views: Map<String, ViewCount> = emptyMap(), dark: Boolean = false,
                       onOpen: (MailView) -> Unit = {}) {
        compose.setContent {
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize()) {
                    MailDrawer(Fixtures.labels, MailView(), null, emptyMap(), "me@example.com", 0, onOpen, {}, {},
                        accounts = accounts, accountCounts = views)
                }
            }
        }
    }

    @Test fun headingNamesTheMailbox() {
        assertEquals("All accounts", mailboxHeading(listOf(home, work), "All accounts", "x@example.com"))
        assertEquals("me@example.com", mailboxHeading(listOf(home), "All accounts", "x@example.com"))
        assertEquals("x@example.com", mailboxHeading(emptyList(), "All accounts", "x@example.com"))
        assertNotEquals(AccountColours.of("blue", false), AccountColours.of("blue", true))
        assertEquals(AccountColours.of("blue", false), AccountColours.of("nonsense", false))
    }

    @Test fun twoAccountsWithoutFolders() {
        drawer(listOf(home, work))
        compose.onNodeWithText("All accounts").assertExists()
        compose.onAllNodesWithTag("drawer-account-2").assertCountEquals(0)
    }

    @Test fun eachAccountsFoldersOpenTheirView() {
        var opened: MailView? = null
        drawer(listOf(home, work), counts) { opened = it }
        compose.onNodeWithTag("drawer-account-2").assertExists()
        compose.onNodeWithText("work@example.org").assertExists()
        // Work's Inbox shows its unread number and opens acct:2:inbox, titled with the address.
        val inboxes = compose.onAllNodesWithText("Inbox")
        inboxes[2].performClick()
        assertEquals("acct:2:inbox", opened?.view)
        assertEquals("Inbox · work@example.org", opened?.title)
    }

    @Test fun drawerLight() = shot(false)

    @Test fun drawerDark() = shot(true)

    private fun shot(dark: Boolean) {
        drawer(listOf(home, work), counts, dark)
        compose.onNodeWithTag("drawer-account-1").assertExists()
        compose.onRoot().captureRoboImage("screenshots/accounts-drawer-${if (dark) "dark" else "light"}.png")
    }

    @Test fun rowsCarryTheirAccountsDot() {
        val c = RowFixtures.inbox[0].copy(accounts = listOf(2))
        compose.setContent {
            MaterialTheme {
                CompositionLocalProvider(LocalMailAccounts provides listOf(home, work)) {
                    ConversationRow(c, emptyMap(), selected = false, selectionMode = false, activated = false, compact = false,
                        swipeStart = SwipeAction.ARCHIVE, swipeEnd = SwipeAction.TRASH, onClick = {}, onLongClick = {},
                        onToggleSelect = {}, onStar = {}, onSwipe = {})
                }
            }
        }
        compose.onNodeWithContentDescription("Account: work@example.org", useUnmergedTree = true).assertExists()
    }

    @Test fun oneAccountNoDots() {
        val c = RowFixtures.inbox[0].copy(accounts = listOf(1))
        compose.setContent {
            MaterialTheme {
                CompositionLocalProvider(LocalMailAccounts provides listOf(home)) {
                    ConversationRow(c, emptyMap(), selected = false, selectionMode = false, activated = false, compact = false,
                        swipeStart = SwipeAction.ARCHIVE, swipeEnd = SwipeAction.TRASH, onClick = {}, onLongClick = {},
                        onToggleSelect = {}, onStar = {}, onSwipe = {})
                }
            }
        }
        compose.onAllNodesWithTag("row-accounts", useUnmergedTree = true).assertCountEquals(0)
    }
}
