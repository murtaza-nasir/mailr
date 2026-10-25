package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.hasScrollToIndexAction
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollToIndex
import io.github.murtazanasir.mailr.data.settings.Prefs
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.ui.settings.SettingsCallbacks
import io.github.murtazanasir.mailr.ui.settings.SettingsScreen
import io.github.murtazanasir.mailr.ui.share.SelfPhase
import io.github.murtazanasir.mailr.ui.share.SelfShareCallbacks
import io.github.murtazanasir.mailr.ui.share.SelfShareOverlay
import io.github.murtazanasir.mailr.ui.share.SelfUi
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * The new screens of the October 2026 security fixes, light and dark: the Send to self card
 * waiting for its Send tap, and Settings → Privacy.
 * Output: app/screenshots/security-<theme>-<screen>.png
 */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class SecurityScreensScreenshotTest(private val dark: Boolean) {

    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun params(): List<Array<Any>> = listOf(arrayOf<Any>(false), arrayOf<Any>(true))
    }

    @get:Rule val compose = createComposeRule()

    @Before fun setUp() {
        RuntimeEnvironment.setQualifiers("w411dp-h891dp-port-${if (dark) "night" else "notnight"}-xxhdpi")
    }

    private fun content(body: @Composable () -> Unit) = compose.setContent {
        MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
            Surface(Modifier.fillMaxSize()) { body() }
        }
    }

    private fun shot(name: String) =
        compose.onRoot().captureRoboImage("screenshots/security-${if (dark) "dark" else "light"}-$name.png")

    @Test fun sendToSelfWaitsForTheTap() {
        var confirmed = 0
        var closed = 0
        content {
            SelfShareOverlay(SelfUi(phase = SelfPhase.CONFIRM, subject = "Quarterly report draft", files = 1),
                SelfShareCallbacks(close = { closed++ }, confirm = { confirmed++ }, undo = {}, addNote = {}, setNote = {},
                    send = {}, retry = {}, openApp = {}))
        }
        compose.onNodeWithText("Send to yourself").assertExists()
        shot("send-to-self-confirm")
        compose.onNodeWithTag("self-send-confirm").performClick()
        compose.runOnIdle { assertEquals(1, confirmed) }
        compose.onNodeWithText("Cancel").performClick()
        compose.runOnIdle { assertEquals(1, closed) }
    }

    @Test fun privacySettings() {
        content {
            SettingsScreen(Prefs(), "Alex Morgan", "me@example.com", "https://mail.example.com", emptyList(),
                SettingsCallbacks({}, {}, {}, {}, {}))
        }
        // The Privacy header (index 9, after account, Look 4 and Mail 4: header, two swipes, images) at the top.
        compose.onNode(hasScrollToIndexAction()).performScrollToIndex(9)
        compose.onNodeWithText("Privacy").assertExists()
        compose.onNodeWithText("Hide in Recents and screenshots").assertExists()
        shot("settings-privacy")
    }
}
