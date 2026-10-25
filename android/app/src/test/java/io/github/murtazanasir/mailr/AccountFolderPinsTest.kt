// ACT-SURFACES: android.drawer
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.hasScrollAction
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.longClick
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollToNode
import androidx.compose.ui.test.performTouchInput
import io.github.murtazanasir.mailr.data.api.Label
import io.github.murtazanasir.mailr.data.api.MailAccountInfo
import io.github.murtazanasir.mailr.data.api.ViewCount
import io.github.murtazanasir.mailr.data.repo.MailView
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.ui.mail.AccountFolderPrefs
import io.github.murtazanasir.mailr.ui.mail.DrawerAccountPins
import io.github.murtazanasir.mailr.ui.mail.MailDrawer
import io.github.murtazanasir.mailr.ui.mail.accountFolderPrefs
import io.github.murtazanasir.mailr.ui.mail.accountLabelView
import io.github.murtazanasir.mailr.ui.mail.accountView
import io.github.murtazanasir.mailr.ui.mail.movePin
import io.github.murtazanasir.mailr.ui.mail.pinFolder
import io.github.murtazanasir.mailr.ui.mail.pinsJson
import io.github.murtazanasir.mailr.ui.mail.splitAccountFolders
import io.github.murtazanasir.mailr.ui.mail.unpinFolder
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * Account groups stay short (ui/mail/AccountFolderPins.kt): an account's group
 * in the drawer shows its system folders, the folders pinned to it (the setting
 * `account_folder_pins`, the same as the web), and "More folders" with the unread mail of the
 * folders inside it, closed until tapped (ACT mail.account_folders_more). A long press on a folder
 * pins it (ACT mail.account_folder_pin) or, pinned, unpins it (ACT mail.account_folder_unpin) and moves
 * it among the pinned ones (ACT mail.account_folder_reorder).
 * Screenshots: app/screenshots/account-folder-pins-<closed|open>-<theme>.png
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h2400dp-xxhdpi")
class AccountFolderPinsTest {
    @get:Rule val compose = createComposeRule()

    private val home = MailAccountInfo(1, "me@example.com", "Home", "blue")
    private val work = MailAccountInfo(2, "work@example.org", "Work", "orange")
    private val labels = Fixtures.labels + Label(20, "Invoices", color = "#188038", homeAccountId = 2) +
        Label(21, "Receipts", color = "#d93025")
    private val counts = mapOf(
        accountView(2, "inbox") to ViewCount(1, 3), accountView(1, "inbox") to ViewCount(0, 4),
        accountLabelView(2, 11) to ViewCount(1, 1), accountLabelView(2, 10) to ViewCount(1, 1),
        accountLabelView(2, 13) to ViewCount(2, 3), accountLabelView(2, 21) to ViewCount(0, 1),
        accountLabelView(1, 12) to ViewCount(0, 2),
    )

    @Test fun theSettingsReadAndWrite() {
        val s = Json.parseToJsonElement(
            """{"account_folder_pins":{"2":[20,13],"x":[1],"3":"no"},"account_folders_more_open":[2],"account_folders_show_unread":true}""",
        ) as JsonObject
        val p = accountFolderPrefs(s)
        assertEquals(mapOf(2L to listOf(20L, 13L)), p.pins)
        assertEquals(setOf(2L), p.moreOpen)
        assertEquals(true, p.showUnread)
        assertEquals(AccountFolderPrefs(), accountFolderPrefs(null))
        assertEquals("""{"2":[20,13]}""", pinsJson(p.pins + (5L to emptyList())).toString())
    }

    @Test fun pinsAreKeptPerAccountInOrder() {
        var p = pinFolder(emptyMap(), 2, 20)
        p = pinFolder(p, 2, 13)
        p = pinFolder(p, 2, 21, before = 20)
        p = pinFolder(p, 1, 12)
        assertEquals(mapOf(2L to listOf(21L, 20L, 13L), 1L to listOf(12L)), p)
        assertEquals(listOf(20L, 21L, 13L), movePin(p, 2, 20, -1)[2])
        assertEquals(p, movePin(p, 2, 21, -1))
        assertEquals(mapOf(2L to listOf(21L, 20L, 13L)), unpinFolder(p, 1, 12))
    }

    @Test fun theGroupSplitsLikeTheWeb() {
        val unread = { l: Label -> counts[accountLabelView(2, l.id)]?.unread ?: 0 }
        val folders = labels.filter { it.id in setOf(10L, 11L, 13L, 20L, 21L) }
        val s = splitAccountFolders(folders, labels, listOf(20, 99), unread, showUnread = false, moreOpen = false)
        assertEquals(listOf("Invoices"), s.pinned.map { it.name })
        assertEquals(listOf("Receipts", "Research", "Research/PADS 2027", "Travel"), s.rest.map { it.name })
        assertEquals(4, s.restUnread)
        val lit = splitAccountFolders(folders, labels, listOf(20), unread, showUnread = true, moreOpen = false)
        assertEquals(listOf("Research", "Research/PADS 2027", "Travel"), lit.surfaced.map { it.name })
        assertEquals(0, lit.restUnread)
        // The open folder shows above More folders while it is closed.
        assertEquals(listOf(21L), splitAccountFolders(folders, labels, emptyList(), unread, false, false, current = 21).surfaced.map { it.id })
    }

    private fun drawer(dark: Boolean = false, open: Boolean = false, calls: MutableList<String> = mutableListOf()) {
        compose.setContent {
            var prefs by androidx.compose.runtime.remember {
                mutableStateOf(AccountFolderPrefs(pins = mapOf(2L to listOf(20L)), moreOpen = if (open) setOf(2L) else emptySet()))
            }
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize()) {
                    MailDrawer(labels, MailView(), null, emptyMap(), "me@example.com", 0, {}, {}, {},
                        accounts = listOf(home, work), accountCounts = counts,
                        accountPins = DrawerAccountPins(prefs,
                            pin = { a, l, address -> calls += "pin $a $l $address"; prefs = prefs.copy(pins = pinFolder(prefs.pins, a, l)) },
                            unpin = { a, l -> calls += "unpin $a $l"; prefs = prefs.copy(pins = unpinFolder(prefs.pins, a, l)) },
                            move = { a, l, d, _ -> calls += "move $a $l $d" },
                            setMoreOpen = { a, o -> calls += "more $a $o"; prefs = prefs.copy(moreOpen = if (o) prefs.moreOpen + a else prefs.moreOpen - a) },
                            // The headings carry ⋮ with New folder, as in the app (NewFolderTest).
                            create = { a, name -> Label(99, name, homeAccountId = a) }))
                }
            }
        }
    }

    @Test fun moreFoldersOpensAndALongPressPins() {
        val calls = mutableListOf<String>()
        drawer(calls = calls)
        // Closed: Invoices is pinned in Work, the rest are inside More folders, which carries their unread mail.
        compose.onNode(hasScrollAction()).performScrollToNode(hasTestTag("drawer-more-2"))
        compose.onNodeWithTag("drawer-more-2", useUnmergedTree = false).assertExists()
        compose.onNodeWithContentDescription("More folders, 4 unread").assertExists()
        compose.onAllNodesWithText("Travel").assertCountEquals(1) // only the unified Labels list
        compose.onNodeWithTag("drawer-more-2").performClick()
        compose.waitForIdle()
        assertEquals(listOf("more 2 true"), calls)
        compose.onNode(hasScrollAction()).performScrollToNode(hasTestTag("drawer-more-2"))
        compose.onAllNodesWithText("Travel").assertCountEquals(2)
        // Long-press Travel in Work's group: Pin to work@example.org.
        compose.onAllNodesWithText("Travel")[0].performTouchInput { longClick() }
        compose.onNodeWithText("Pin to work@example.org").performClick()
        compose.waitForIdle()
        assertEquals("pin 2 13 work@example.org", calls.last())
        // Pinned now, below Invoices: its long press moves it up, and offers Unpin.
        compose.onAllNodesWithText("Travel")[0].performTouchInput { longClick() }
        compose.onNodeWithText("Move up").performClick()
        compose.waitForIdle()
        assertEquals("move 2 13 -1", calls.last())
        compose.onAllNodesWithText("Travel")[0].performTouchInput { longClick() }
        compose.onNodeWithText("Unpin").performClick()
        compose.waitForIdle()
        assertEquals("unpin 2 13", calls.last())
    }

    @Test fun closedLight() = shot(false, false)

    @Test fun closedDark() = shot(true, false)

    @Test fun openLight() = shot(false, true)

    @Test fun openDark() = shot(true, true)

    private fun shot(dark: Boolean, open: Boolean) {
        drawer(dark, open)
        compose.onNode(hasScrollAction()).performScrollToNode(hasTestTag("drawer-more-2"))
        compose.onRoot().captureRoboImage("screenshots/account-folder-pins-${if (open) "open" else "closed"}-${if (dark) "dark" else "light"}.png")
    }
}
