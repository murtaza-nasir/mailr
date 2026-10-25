package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.longClick
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performTouchInput
import io.github.murtazanasir.mailr.data.repo.MailView
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.ui.mail.DrawerFavorites
import io.github.murtazanasir.mailr.ui.mail.MailDrawer
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import com.github.takahirom.roborazzi.captureScreenRoboImage
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * The drawer's Favorites (android.drawer) on a phone, light and dark: a list with a
 * label and a category, the empty list's hint, and the long-press menu.
 * Output: app/screenshots/favorites-<theme>-<name>.png
 */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class FavoritesScreenshotTest(private val dark: Boolean) {
    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun params(): List<Array<Any>> = listOf(arrayOf<Any>(false), arrayOf<Any>(true))
    }

    @get:Rule val compose = createComposeRule()

    @Before fun setUp() {
        RuntimeEnvironment.setQualifiers("w411dp-h891dp-port-${if (dark) "night" else "notnight"}-xxhdpi")
    }

    private fun name(n: String) = "screenshots/favorites-${if (dark) "dark" else "light"}-$n.png"

    private fun drawer(keys: List<String>, current: MailView = MailView()) {
        compose.setContent {
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize()) { Drawer(keys, current) }
            }
        }
    }

    @Composable
    private fun Drawer(keys: List<String>, current: MailView) =
        MailDrawer(Fixtures.labels, current, null, emptyMap(), "me@example.com", 2, {}, {}, {}, favorites = DrawerFavorites(keys))

    @Test fun list() {
        drawer(listOf("inbox", "label:10", "drafts", "category:social", "label:13"), MailView("label:10", title = "Research"))
        compose.onRoot().captureRoboImage(name("list"))
    }

    @Test fun empty() {
        drawer(emptyList())
        compose.onRoot().captureRoboImage(name("empty"))
    }

    @OptIn(com.github.takahirom.roborazzi.ExperimentalRoborazziApi::class)
    @Test fun menu() {
        drawer(listOf("inbox", "label:10", "drafts"))
        compose.onAllNodesWithText("Research")[0].performTouchInput { longClick() }
        compose.waitForIdle()
        captureScreenRoboImage(name("menu"))
    }
}
