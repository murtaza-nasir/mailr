package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import com.github.takahirom.roborazzi.captureRoboImage
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.ui.approvals.ApprovalsScreen
import io.github.murtazanasir.mailr.ui.approvals.CardSamples
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * The approval cards on a phone, light and dark: two tasks from one token under their group header
 * ("claude-code wants to add 2 tasks", Allow all, Deny all) above an email from the assistant; and the
 * same list with the long title opened and the first card's reason shown.
 * Output: app/screenshots/approval-cards-<theme>-<state>.png
 */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class ApprovalCardsShotsTest(private val dark: Boolean) {

    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun params(): List<Array<Any>> = listOf(arrayOf<Any>(false), arrayOf<Any>(true))
    }

    @get:Rule val compose = createComposeRule()

    @Before fun setUp() {
        RuntimeEnvironment.setQualifiers("w411dp-h891dp-port-${if (dark) "night" else "notnight"}-xxhdpi")
    }

    private fun shot(name: String, then: () -> Unit = {}, content: @Composable () -> Unit) {
        compose.setContent {
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize()) { content() }
            }
        }
        then()
        compose.waitForIdle()
        compose.onRoot().captureRoboImage("screenshots/approval-cards-${if (dark) "dark" else "light"}-$name.png")
    }

    @Composable
    private fun List() = ApprovalsScreen(CardSamples.tasks + CardSamples.email, emptyList(), null, false, SnackbarHostState(), {}, {},
        { _, _, _ -> }, onAsk = {}, onDecideMany = { _, _ -> })

    @Test fun group() = shot("group") { List() }

    @Test fun opened() = shot("opened", then = {
        compose.onNodeWithTag("approval-title-more", useUnmergedTree = true).performClick()
        compose.waitForIdle()
        compose.onAllNodesWithTag("approval-why")[0].performClick()
    }) { List() }
}
