// ACT-SURFACES: android.settings
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.paging.PagingData
import androidx.paging.compose.collectAsLazyPagingItems
import io.github.murtazanasir.mailr.data.repo.MailView
import io.github.murtazanasir.mailr.data.settings.ColourSource
import io.github.murtazanasir.mailr.data.settings.Prefs
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.ui.mail.ListActions
import io.github.murtazanasir.mailr.ui.mail.MailListPane
import io.github.murtazanasir.mailr.ui.settings.SettingsCallbacks
import io.github.murtazanasir.mailr.ui.settings.SettingsScreen
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import kotlinx.coroutines.flow.flowOf
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
 * Settings → Look → Colours → "Same as mailr on the web" (ACT settings.colours_follow_web): the app
 * in the web's colours, three seeds (Cards' violet preset; a colour of your own with complementary
 * partners; a mid blue whose dark primary takes white text by APCA), light and dark. Output: app/screenshots/web-colours-<seed>-<theme>-<name>.png
 */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class WebColoursScreenshotTest(private val name: String, private val seed: String, private val harmony: String, private val dark: Boolean) {
    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "{0}-{3}")
        fun params(): List<Array<Any>> = listOf(
            arrayOf<Any>("violet", "#6750a4", "single"),
            arrayOf<Any>("custom", "#b04a7a", "complementary"),
            arrayOf<Any>("midblue", "#009ccd", "complementary"),
        ).flatMap { listOf(it + false, it + true) }
    }

    @get:Rule val compose = createComposeRule()

    @Before fun setUp() {
        RuntimeEnvironment.setQualifiers("w411dp-h891dp-port-${if (dark) "night" else "notnight"}-xxhdpi")
    }

    private val prefs get() = Prefs(theme = if (dark) ThemeMode.DARK else ThemeMode.LIGHT, colourChoice = ColourSource.WEB, webSeed = seed, webHarmony = harmony)
    private val file get() = "screenshots/web-colours-$name-${if (dark) "dark" else "light"}"
    private val noList = ListActions({}, {}, {}, {}, {}, {}, {}, {}, { _, _ -> }, {}, { _, _ -> }, {}, {}, {})

    @Test fun settings() {
        var p = prefs
        compose.setContent {
            MailrTheme(p.theme, p.dynamicColor, p.webColours()) {
                Surface(Modifier.fillMaxSize()) {
                    SettingsScreen(p, "Alex Morgan", "me@example.com", "https://mail.example.com", emptyList(),
                        SettingsCallbacks({}, { f -> p = f(p) }, {}, {}, {}))
                }
            }
        }
        compose.onNodeWithText("Same as Mailr on the web\nThe web", substring = true).assertExists()
        compose.onRoot().captureRoboImage("$file-settings.png")
        // The choice is a list of the three sources; picking mailr's own colours saves it.
        compose.onNodeWithText("Colours").performClick()
        compose.onNodeWithText("Mailr's colours").performClick()
        compose.runOnIdle { assertEquals(ColourSource.MAILR, p.colourSource) }
    }

    @Test fun inbox() {
        compose.setContent {
            MailrTheme(prefs.theme, prefs.dynamicColor, prefs.webColours()) {
                Surface(Modifier.fillMaxSize()) {
                    val paged = flowOf(PagingData.from(RowFixtures.inbox)).collectAsLazyPagingItems()
                    MailListPane(MailView(), paged, emptyList(), null, RowFixtures.labels, emptySet(), null, prefs,
                        "Alex Morgan", "me@example.com", false, 0, SnackbarHostState(), true, noList)
                }
            }
        }
        compose.onRoot().captureRoboImage("$file-inbox.png")
    }
}
