package io.github.murtazanasir.mailr

import android.app.Application
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import io.github.murtazanasir.mailr.data.api.AccountPicture
import io.github.murtazanasir.mailr.data.settings.Prefs
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.ui.components.PictureCache
import io.github.murtazanasir.mailr.ui.settings.PictureUi
import io.github.murtazanasir.mailr.ui.settings.SettingsCallbacks
import io.github.murtazanasir.mailr.ui.settings.SettingsScreen
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import com.github.takahirom.roborazzi.captureScreenRoboImage
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * Settings → Your picture on a phone, light and dark: no picture yet (the sign-in picture offered),
 * an uploaded picture, and the Change menu (gallery or camera).
 * Output: app/screenshots/picture-<theme>-<name>.png
 */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class PictureSettingsScreenshotTest(private val dark: Boolean) {
    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun params(): List<Array<Any>> = listOf(arrayOf<Any>(false), arrayOf<Any>(true))
    }

    @get:Rule val compose = createComposeRule()

    @Before fun setUp() {
        RuntimeEnvironment.setQualifiers("w411dp-h891dp-port-${if (dark) "night" else "notnight"}-xxhdpi")
    }

    @After fun tearDown() = PictureCache.clear()

    private fun name(n: String) = "screenshots/picture-${if (dark) "dark" else "light"}-$n.png"

    private fun settings(pic: AccountPicture) {
        compose.setContent {
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize()) {
                    SettingsScreen(Prefs(), "Alex Example", "alex@example.com", "https://mail.example.com", emptyList(),
                        SettingsCallbacks({}, {}, {}, {}, {}, picture = PictureUi(pic)))
                }
            }
        }
    }

    /** A stand-in photo: a sky, a sun and a hill. */
    private fun photo(): Bitmap = Bitmap.createBitmap(256, 256, Bitmap.Config.ARGB_8888).also {
        Canvas(it).apply {
            drawColor(Color.rgb(120, 170, 220))
            drawCircle(180f, 80f, 36f, Paint().apply { color = Color.rgb(250, 210, 90); isAntiAlias = true })
            drawCircle(100f, 330f, 170f, Paint().apply { color = Color.rgb(70, 140, 80); isAntiAlias = true })
        }
    }

    @Test fun none() {
        settings(AccountPicture(has = false, idpAvailable = true))
        compose.onRoot().captureRoboImage(name("none"))
    }

    @Test fun uploaded() {
        PictureCache.putForTest("/api/v1/me/picture?v=7", photo().asImageBitmap())
        settings(AccountPicture(has = true, source = "upload", version = 7, idpAvailable = true))
        compose.onRoot().captureRoboImage(name("uploaded"))
    }

    @OptIn(com.github.takahirom.roborazzi.ExperimentalRoborazziApi::class)
    @Test fun changeMenu() {
        PictureCache.putForTest("/api/v1/me/picture?v=7", photo().asImageBitmap())
        settings(AccountPicture(has = true, source = "idp", version = 7, idpAvailable = true))
        compose.onNodeWithText("Change").performClick()
        compose.waitForIdle()
        captureScreenRoboImage(name("menu"))
    }
}
