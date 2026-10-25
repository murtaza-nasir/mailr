package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.assertIsDisplayed
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.swipeRight
import androidx.paging.PagingData
import androidx.paging.compose.collectAsLazyPagingItems
import io.github.murtazanasir.mailr.data.repo.MailView
import io.github.murtazanasir.mailr.data.settings.Prefs
import io.github.murtazanasir.mailr.ui.AppNavigation
import io.github.murtazanasir.mailr.ui.Tab
import io.github.murtazanasir.mailr.ui.approvals.ARRIVAL_MILLIS
import io.github.murtazanasir.mailr.ui.approvals.ApprovalArrivalBanner
import io.github.murtazanasir.mailr.ui.approvals.ApprovalsScreen
import io.github.murtazanasir.mailr.ui.approvals.Arrival
import io.github.murtazanasir.mailr.ui.mail.ListActions
import io.github.murtazanasir.mailr.ui.mail.MailListPane
import kotlinx.coroutines.flow.flowOf
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Where a pending approval shows outside its notification: the badge on the Assistant tab, the
 * shield in the inbox's search bar (which opens the Approvals screen), the one-time banner for
 * a new arrival, and the Approvals screen with its history (approval.approve, approval.deny).
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class ApprovalsIndicatorTest {
    @get:Rule val compose = createComposeRule()

    @Test fun assistantTabShowsTheCount() {
        var n by mutableStateOf(3)
        compose.setContent { MaterialTheme { AppNavigation(Tab.MAIL, {}, 0, assistantBadge = n) { Text("content") } } }
        compose.onNodeWithContentDescription("3 agent actions waiting").assertExists()
        compose.onNodeWithTag("assistant-badge", useUnmergedTree = true).assertExists()
        compose.onNodeWithText("3", useUnmergedTree = true).assertExists()
        n = 120
        compose.onNodeWithText("99+", useUnmergedTree = true).assertExists()
        n = 0
        compose.waitForIdle()
        assertEquals(0, compose.onAllNodesWithTag("assistant-badge", useUnmergedTree = true).fetchSemanticsNodes().size)
    }

    @Test fun inboxShieldShowsTheCountAndOpensTheList() {
        var opened = 0
        var n by mutableStateOf(2)
        val actions = ListActions({}, {}, {}, {}, {}, {}, {}, {}, { _, _ -> }, {}, { _, _ -> }, {}, {}, {}, openApprovals = { opened++ })
        compose.setContent {
            MaterialTheme {
                val items = flowOf(PagingData.from(Fixtures.conversations)).collectAsLazyPagingItems()
                MailListPane(MailView(), items, emptyList(), null, Fixtures.labels, emptySet(), null, Prefs(),
                    "Alex Morgan", "me@example.com", false, 0, SnackbarHostState(), true, actions, pendingApprovals = n)
            }
        }
        compose.onNodeWithContentDescription("Waiting for you: 2 approvals").assertIsDisplayed().performClick()
        assertEquals(1, opened)
        n = 0
        compose.waitForIdle()
        assertEquals(0, compose.onAllNodesWithTag("waiting-bell").fetchSemanticsNodes().size)
    }

    @Test fun bannerShowsOnceAndGoesAfterSixSeconds() {
        compose.mainClock.autoAdvance = false
        var arrival by mutableStateOf<Arrival?>(Arrival(Fixtures.approvals[0], null))
        var dismissed = 0
        var reviewed = 0
        compose.setContent {
            MaterialTheme { ApprovalArrivalBanner(arrival, onReview = { reviewed++ }, onDismiss = { dismissed++; arrival = null }) }
        }
        compose.mainClock.advanceTimeBy(1_000)
        compose.onNodeWithText("Create event").assertExists()
        compose.onNodeWithText("Approval request from Claude Code").assertExists()
        compose.mainClock.advanceTimeBy(ARRIVAL_MILLIS - 2_000)
        assertEquals(0, dismissed)
        compose.mainClock.advanceTimeBy(1_500)
        assertEquals(1, dismissed)
        compose.mainClock.advanceTimeBy(1_000)
        assertEquals(0, compose.onAllNodesWithText("Create event").fetchSemanticsNodes().size)
        assertEquals(0, reviewed)
    }

    @Test fun bannerSwipesAwayAndReviewOpens() {
        var arrival by mutableStateOf<Arrival?>(Arrival(Fixtures.approvals[1], null, more = 2))
        var dismissed = 0
        var reviewed: Arrival? = null
        compose.setContent {
            MaterialTheme { ApprovalArrivalBanner(arrival, onReview = { reviewed = it }, onDismiss = { dismissed++; arrival = null }) }
        }
        compose.onNodeWithText("Approval request from Ambrose · 2 more").assertIsDisplayed()
        compose.onNodeWithTag("approval-banner").performTouchInput { swipeRight() }
        compose.waitForIdle()
        assertEquals(1, dismissed)

        arrival = Arrival(Fixtures.approvals[0], null)
        compose.onNodeWithText("Review").performClick()
        assertEquals(41L, reviewed!!.approval.id)
    }

    /** ACT approval.approve, ACT approval.deny: the screen lists pending items, then the history with outcomes. */
    @Test fun approvalsScreenListsPendingAndHistory() {
        val decisions = mutableListOf<Triple<Long, Boolean, String>>()
        compose.setContent {
            MaterialTheme {
                ApprovalsScreen(Fixtures.approvals, Fixtures.approvalHistory, null, false, SnackbarHostState(), {}, {},
                    { a, ok, scope -> decisions += Triple(a.id, ok, scope) }, focus = "42")
            }
        }
        compose.onNodeWithText("Approvals · 2").assertIsDisplayed()
        compose.onNodeWithTag("approval-42").assertIsDisplayed()
        compose.onAllNodesWithText("Allow once")[0].performClick()
        assertEquals(Triple(41L, true, "once"), decisions.single())
        compose.onNodeWithText("Recent decisions").assertExists()
        compose.onNodeWithText("Denied · Ambrose · 3 hr ago").assertExists()
    }
}
