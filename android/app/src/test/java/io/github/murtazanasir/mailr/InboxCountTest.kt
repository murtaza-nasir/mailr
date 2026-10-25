// ACT-SURFACES: android.drawer
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.test.assertCountEquals
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import io.github.murtazanasir.mailr.data.api.InboxCount
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.inboxBadge
import io.github.murtazanasir.mailr.data.repo.MailView
import io.github.murtazanasir.mailr.ui.mail.DrawerFavorites
import io.github.murtazanasir.mailr.ui.mail.MailDrawer
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * settings.inbox_count in the drawer (android.drawer): the number by Inbox is the server's Inbox
 * count (GET /inbox/count), the first tab's unread mail by default, not the Inbox label's unread
 * count. A server without the route leaves the label's count.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h1600dp")
class InboxCountTest {
    @get:Rule val compose = createComposeRule()

    private val labels = Fixtures.labels  // the Inbox label has 7 unread

    @Test fun theServersCountWinsOverTheLabel() {
        assertEquals(3, inboxBadge(InboxCount(count = 3, mode = "first_tab", layout = "default", tab = "primary"), labels))
        assertEquals(0, inboxBadge(InboxCount(count = 0), labels))
        assertEquals(7, inboxBadge(null, labels))
        assertEquals(0, inboxBadge(null, emptyList()))
    }

    @Test fun readsTheRoute() {
        val c = MailrJson.decodeFromString(InboxCount.serializer(), """{"count":4,"mode":"all","layout":"mine","tab":null}""")
        assertEquals(InboxCount(4, "all", "mine", null), c)
    }

    private fun drawer(count: InboxCount?) = compose.setContent {
        MaterialTheme {
            MailDrawer(labels, MailView(), null, emptyMap(), "me@example.com", 0, {}, {}, {},
                favorites = DrawerFavorites(emptyList()), inboxCount = count)
        }
    }

    @Test fun drawerShowsTheFirstTabsCount() {
        drawer(InboxCount(count = 3, mode = "first_tab", layout = "default", tab = "primary"))
        compose.onAllNodesWithText("3").assertCountEquals(1)
        compose.onAllNodesWithText("7").assertCountEquals(0)
    }

    @Test fun drawerFallsBackToTheLabel() {
        drawer(null)
        compose.onAllNodesWithText("7").assertCountEquals(1)
    }
}
