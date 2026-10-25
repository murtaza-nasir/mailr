package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onRoot
import androidx.paging.PagingData
import androidx.paging.compose.collectAsLazyPagingItems
import io.github.murtazanasir.mailr.data.repo.MailView
import io.github.murtazanasir.mailr.data.settings.Prefs
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.ui.AppNavigation
import io.github.murtazanasir.mailr.ui.Tab
import io.github.murtazanasir.mailr.ui.approvals.ApprovalArrivalBanner
import io.github.murtazanasir.mailr.ui.approvals.ApprovalsScreen
import io.github.murtazanasir.mailr.ui.approvals.Arrival
import io.github.murtazanasir.mailr.ui.mail.ListActions
import io.github.murtazanasir.mailr.ui.mail.MailListPane
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import kotlinx.coroutines.flow.flowOf
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * The approvals indicators on a phone, light and dark: the inbox with the shield and the
 * Assistant tab's badge, the banner for a new arrival, and the Approvals screen focused on one
 * request and with nothing waiting. Output: app/screenshots/approvals-<theme>-<screen>.png
 * The Approvals screen with requests and history is DeviceAssistantShotsTest.approvals
 * (phone-<theme>-approvals and the larger devices).
 */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class ApprovalsScreenshotTest(private val dark: Boolean) {

    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun params(): List<Array<Any>> = listOf(arrayOf<Any>(false), arrayOf<Any>(true))
    }

    @get:Rule val compose = createComposeRule()

    @Before fun setUp() {
        RuntimeEnvironment.setQualifiers("w411dp-h891dp-port-${if (dark) "night" else "notnight"}-xxhdpi")
    }

    private fun shot(name: String, content: @Composable () -> Unit) {
        compose.setContent {
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize()) { content() }
            }
        }
        compose.onRoot().captureRoboImage("screenshots/approvals-${if (dark) "dark" else "light"}-$name.png")
    }

    private val noList = ListActions({}, {}, {}, {}, {}, {}, {}, {}, { _, _ -> }, {}, { _, _ -> }, {}, {}, {})

    @Composable
    private fun Inbox(waiting: Int) {
        AppNavigation(Tab.MAIL, {}, 7, assistantBadge = waiting) {
            val items = flowOf(PagingData.from(Fixtures.conversations)).collectAsLazyPagingItems()
            MailListPane(MailView(), items, emptyList(), null, Fixtures.labels, emptySet(), null, Prefs(),
                "Alex Morgan", "me@example.com", false, 0, SnackbarHostState(), true, noList, pendingApprovals = waiting)
        }
    }

    @Test fun inboxWithIndicator() = shot("inbox") { Inbox(2) }

    /** Only missed reminders are left: the bell's badge is grey. */
    @Test fun inboxWithMissedOnly() = shot("inbox-missed") {
        AppNavigation(Tab.MAIL, {}, 7, assistantBadge = 0) {
            val items = flowOf(PagingData.from(Fixtures.conversations)).collectAsLazyPagingItems()
            MailListPane(MailView(), items, emptyList(), null, Fixtures.labels, emptySet(), null, Prefs(),
                "Alex Morgan", "me@example.com", false, 0, SnackbarHostState(), true, noList,
                waiting = io.github.murtazanasir.mailr.ui.approvals.WaitingCounts(reminders = 2, missed = 2))
        }
    }

    @Test fun inboxWithArrival() = shot("arrival") {
        Box(Modifier.fillMaxSize()) {
            Inbox(3)
            ApprovalArrivalBanner(Arrival(Fixtures.approvals[0], null), {}, {})
        }
    }

    @Test fun approvalsFocused() = shot("focused") {
        ApprovalsScreen(Fixtures.approvals, Fixtures.approvalHistory, null, false, SnackbarHostState(), {}, {}, { _, _, _ -> }, focus = "42")
    }

    @Test fun nothingWaiting() = shot("empty-pending") {
        ApprovalsScreen(emptyList(), Fixtures.approvalHistory, null, false, SnackbarHostState(), {}, {}, { _, _, _ -> })
    }
}
