package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.NavigationDrawerItem
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberDrawerState
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import io.github.murtazanasir.mailr.data.api.InboxLayout
import io.github.murtazanasir.mailr.data.api.LayoutSection
import io.github.murtazanasir.mailr.data.api.LayoutTab
import io.github.murtazanasir.mailr.data.repo.MailView
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.ui.mail.InboxTabs
import kotlinx.coroutines.launch
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * In the Classic layout on a phone, after a menu, a tap on the Promotions tab sometimes
 * did not switch tabs. Each test uses a menu the way the inbox does and taps Promotions at once,
 * before the menu's own animation has finished. Two took the tap: the drawer's scrim while the drawer
 * slid shut (the inbox now snaps it shut on a pick, as here), and the tab row while it glided to
 * centre a newly selected tab (InboxTabs now lets that tap through).
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
@OptIn(ExperimentalMaterial3Api::class)
class TabTapAfterMenuTest {
    @get:Rule val compose = createComposeRule()

    private val layout = InboxLayout("default", "Default", tabs = listOf("primary" to "Primary", "promotions" to "Promotions",
        "social" to "Social", "updates" to "Updates", "forums" to "Forums").map { (id, name) ->
        LayoutTab(id, name, "inbox", if (id == "primary") "none" else "unread", listOf(LayoutSection(id, name, category = id)))
    })
    private fun tab(id: String) = MailView("inbox", title = layout.tabs.first { it.id == id }.name, layout = "default", tab = id)

    private var view by mutableStateOf(MailView("inbox", layout = "default", tab = "primary"))

    private fun tapPromotions() = compose.onNodeWithContentDescription("Promotions", substring = true).performClick()

    private fun settle() {
        compose.mainClock.advanceTimeBy(2_000)
        compose.waitForIdle()
    }

    /** As App.kt's drawer: a pick snaps it shut (sliding shut, its scrim took this tap). */
    @Test fun afterTheDrawer() {
        compose.setContent {
            val drawer = rememberDrawerState(DrawerValue.Open)
            val scope = rememberCoroutineScope()
            MaterialTheme {
                ModalNavigationDrawer(drawerState = drawer, drawerContent = {
                    ModalDrawerSheet {
                        NavigationDrawerItem({ Text("Inbox") }, selected = false, onClick = { view = tab("primary"); scope.launch { drawer.snapTo(DrawerValue.Closed) } })
                    }
                }) { InboxTabs(layout, view, emptyMap()) { view = it } }
            }
        }
        compose.waitForIdle()
        compose.mainClock.autoAdvance = false
        compose.onNodeWithText("Inbox").performClick()
        compose.mainClock.advanceTimeBy(100)
        tapPromotions()
        settle()
        assertEquals("promotions", view.tab)
    }

    /** The drawer took the inbox from Forums back to Primary: the row glides left, and the tap still opens Promotions. */
    @Test fun afterTheTabRowScrolls() {
        view = tab("forums")
        compose.setContent {
            MaterialTheme {
                Column {
                    TextButton(onClick = { view = tab("primary") }) { Text("Go to Primary") }
                    InboxTabs(layout, view, emptyMap()) { view = it }
                }
            }
        }
        compose.waitForIdle()
        compose.mainClock.autoAdvance = false
        compose.onNodeWithText("Go to Primary").performClick()
        compose.mainClock.advanceTimeBy(50)
        tapPromotions()
        settle()
        assertEquals("promotions", view.tab)
    }

    @Test fun afterADropdownMenu() {
        compose.setContent {
            var menu by remember { mutableStateOf(false) }
            MaterialTheme {
                Column {
                    Box {
                        IconButton(onClick = { menu = true }) { Text("More") }
                        DropdownMenu(menu, { menu = false }) { DropdownMenuItem({ Text("Mark read") }, { menu = false }) }
                    }
                    InboxTabs(layout, view, emptyMap()) { view = it }
                }
            }
        }
        compose.onNodeWithText("More").performClick()
        compose.waitForIdle()
        compose.mainClock.autoAdvance = false
        compose.onNodeWithText("Mark read").performClick()
        compose.mainClock.advanceTimeBy(16)
        tapPromotions()
        settle()
        assertEquals("promotions", view.tab)
    }

    @Test fun afterASheet() {
        compose.setContent {
            var sheet by remember { mutableStateOf(true) }
            MaterialTheme {
                Column { InboxTabs(layout, view, emptyMap()) { view = it } }
                if (sheet) ModalBottomSheet({ sheet = false }, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
                    TextButton(onClick = { sheet = false }) { Text("Promotions category") }
                }
            }
        }
        compose.waitForIdle()
        compose.mainClock.autoAdvance = false
        compose.onNodeWithText("Promotions category").performClick()
        compose.mainClock.advanceTimeBy(16)
        tapPromotions()
        settle()
        assertEquals("promotions", view.tab)
    }
}
