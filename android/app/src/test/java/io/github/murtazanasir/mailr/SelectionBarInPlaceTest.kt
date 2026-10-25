package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.getUnclippedBoundsInRoot
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.unit.Dp
import androidx.paging.PagingData
import androidx.paging.compose.collectAsLazyPagingItems
import io.github.murtazanasir.mailr.data.api.InboxLayout
import io.github.murtazanasir.mailr.data.api.LayoutSection
import io.github.murtazanasir.mailr.data.api.LayoutTab
import io.github.murtazanasir.mailr.data.repo.MailView
import io.github.murtazanasir.mailr.data.settings.Prefs
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.ui.mail.InboxTabs
import io.github.murtazanasir.mailr.ui.mail.ListActions
import io.github.murtazanasir.mailr.ui.mail.MailListPane
import io.github.murtazanasir.mailr.ui.mail.Section
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import kotlinx.coroutines.flow.flowOf
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * Selecting items must not hide the top bar and move everything. The
 * selection bar takes the search bar's place at the same height, and the inbox tabs and section
 * headers stay where they were, so the list does not jump when a conversation is selected.
 * Output: app/screenshots/selection-inplace-<before|after>.png
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class SelectionBarInPlaceTest {
    @get:Rule val compose = createComposeRule()

    private val layout = InboxLayout("priority-later", "Priority + Later", tabs = listOf(
        LayoutTab("inbox", "Inbox", "inbox", "none", listOf(LayoutSection("important", "Important"))),
        LayoutTab("promos", "Promos", "tag", "unread", listOf(LayoutSection("all", "All", base = "label:19"))),
    ))
    private val noList = ListActions({}, {}, {}, {}, {}, {}, {}, {}, { _, _ -> }, {}, { _, _ -> }, {}, {}, {})

    private fun top(text: String): Dp = compose.onNodeWithText(text, substring = true, useUnmergedTree = true).getUnclippedBoundsInRoot().top

    @Test fun selectingKeepsTabsHeadersAndRowsInPlace() {
        RuntimeEnvironment.setQualifiers("w411dp-h891dp-port-night-xxhdpi")
        val selection = mutableStateOf(emptySet<Long>())
        val view = MailView("inbox", layout = "priority-later", tab = "promos")
        compose.setContent {
            // Material's look: the web's look writes the section name as it is, not in capitals.
            MailrTheme(ThemeMode.DARK, dynamic = false, webLook = false) {
                Surface(Modifier.fillMaxSize()) {
                    val items = flowOf(PagingData.from(Fixtures.conversations.drop(3))).collectAsLazyPagingItems()
                    MailListPane(view, items, listOf(Section("promos", "Promos", Fixtures.conversations.take(3))), null,
                        Fixtures.labels, selection.value, null, Prefs(), "Alex Morgan", "me@example.com", false, 0,
                        SnackbarHostState(), true, noList.copy(toggle = { id -> selection.value = selection.value.let { if (id in it) it - id else it + id } }),
                        tabs = { InboxTabs(layout, view, emptyMap()) {} })
                }
            }
        }
        compose.waitForIdle()
        val subject = Fixtures.conversations[0].subject
        val tabs = top("Promos")
        val header = top("PROMOS")
        val row = top(subject)
        compose.onRoot().captureRoboImage("screenshots/selection-inplace-before.png")

        selection.value = setOf(Fixtures.conversations[0].id)
        compose.waitForIdle()
        compose.onNodeWithContentDescription("Clear selection").assertIsDisplayed()
        compose.onNodeWithContentDescription("Archive").assertIsDisplayed()
        compose.onRoot().captureRoboImage("screenshots/selection-inplace-after.png")
        assertEquals("the tabs stay", tabs, top("Promos"))
        assertEquals("the section header stays", header, top("PROMOS"))
        assertEquals("the row stays", row, top(subject))

        selection.value = emptySet()
        compose.waitForIdle()
        assertEquals("and stays when the selection ends", row, top(subject))
    }
}
