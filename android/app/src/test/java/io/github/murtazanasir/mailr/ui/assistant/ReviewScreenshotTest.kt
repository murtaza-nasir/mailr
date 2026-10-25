// ACT-SURFACES: android.assistant
package io.github.murtazanasir.mailr.ui.assistant

import android.app.Application
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.Approval
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.ReviewState
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.ui.more.ApprovalCard
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * agent.review_mail in the Ambrose chat, light and dark: a review running in its turn (the progress line
 * with its bar and Stop), and the review card with one sender moved to Keep in its move menu (agent.review_move),
 * one sender's "Always move to Trash" (agent.review_filter) and the unsure rows open.
 * Output: app/screenshots/review-<theme>-<shot>.png. Demo names only.
 */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class ReviewScreenshotTest(private val dark: Boolean) {
    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun params(): List<Array<Any>> = listOf(arrayOf<Any>(false), arrayOf<Any>(true))

        private const val PHONE = "w411dp-h891dp-port"
        private const val TALL = "w411dp-h1100dp-port"
    }

    @get:Rule val compose = createComposeRule()
    private val mode get() = if (dark) "dark" else "light"

    private fun set(qualifiers: String, content: @Composable () -> Unit) {
        RuntimeEnvironment.setQualifiers("$qualifiers-${if (dark) "night" else "notnight"}-xxhdpi")
        compose.setContent {
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize()) { content() }
            }
        }
        compose.mainClock.advanceTimeBy(1000)
        compose.waitForIdle()
    }

    @Test fun running() {
        val s = AssistantState(turns = listOf(ChatTurn("user", "/sweep keep anything about running"),
            ChatTurn("assistant", items = listOf(TurnItem.Text(0, "I'm reviewing all of Promotions. You can stop the review at any time.",
                "I'm reviewing all of Promotions. You can stop the review at any time.")), reviews = listOf(5L), finishedAt = 1)),
            sessionId = "c7", title = "Clean up promotions",
            reviews = mapOf(5L to ReviewState(5, "running", "Promotions", total = 143, done = 50, setAside = 9)))
        set(PHONE) { AssistantScreen(s, ChatsScreenshotTest.chatNoop.copy(followReview = {}, reviewAction = { _, _ -> })) }
        compose.onRoot().captureRoboImage("screenshots/review-$mode-running.png")
    }

    @Test fun card() {
        val card = MailrJson.decodeFromString(Approval.serializer(), REVIEW_CARD)
        set(TALL) { Box(Modifier.padding(16.dp)) { ApprovalCard(card, onApplyReview = { _, _ -> }, reviewAlways = ReviewAlways(emptySet(), emptySet()) { _, _, _ -> }) { _, _, _ -> } } }
        compose.onAllNodesWithTag("review-move")[1].performClick()
        compose.onNodeWithTag("review-move-keep").performClick()
        compose.onNodeWithText("3 left alone: not sure").performClick()
        compose.waitForIdle()
        compose.onRoot().captureRoboImage("screenshots/review-$mode-card.png")
    }
}
