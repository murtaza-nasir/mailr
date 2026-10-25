// ACT-SURFACES: android.settings
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.preferencesOf
import androidx.datastore.preferences.core.stringPreferencesKey
import io.github.murtazanasir.mailr.data.settings.AppLook
import io.github.murtazanasir.mailr.data.settings.AppSettings
import io.github.murtazanasir.mailr.data.settings.ColourSource
import io.github.murtazanasir.mailr.data.settings.Prefs
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.look.LocalLook
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.ui.settings.SettingsCallbacks
import io.github.murtazanasir.mailr.ui.settings.SettingsScreen
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Settings → Look → Style: the web's look by default (new and existing installs), Android's Material
 * on request, applied at once; the colours follow the look until the user picks some (ACT settings.android_style).
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class LookSettingTest {
    @get:Rule val compose = createComposeRule()

    @Test fun theWebLookIsTheDefaultAndItsColoursAreTheWebs() {
        val p = Prefs()
        assertEquals(AppLook.WEB, p.look)
        assertTrue(p.webLook)
        assertEquals(ColourSource.WEB, p.colourSource)
        val material = p.copy(look = AppLook.MATERIAL)
        assertEquals(ColourSource.WALLPAPER, material.colourSource)
        // A pick of the user's own stays when the look changes.
        assertEquals(ColourSource.MAILR, material.copy(colourChoice = ColourSource.MAILR).copy(look = AppLook.WEB).colourSource)
    }

    @Test fun olderInstallsKeepWhatTheyPicked() {
        val source = stringPreferencesKey("colour_source")
        val chosen = booleanPreferencesKey("colour_chosen")
        // Every save before 9 Oct 2026 wrote the default (the wallpaper): no choice.
        assertNull(AppSettings.colourChoiceOf(preferencesOf(source to "WALLPAPER")))
        assertNull(AppSettings.colourChoiceOf(preferencesOf()))
        // Anything else was picked.
        assertEquals(ColourSource.MAILR, AppSettings.colourChoiceOf(preferencesOf(source to "MAILR")))
        assertEquals(ColourSource.WEB, AppSettings.colourChoiceOf(preferencesOf(source to "WEB")))
        // The switch before 8 Oct 2026, off: mailr's colours.
        assertEquals(ColourSource.MAILR, AppSettings.colourChoiceOf(preferencesOf(booleanPreferencesKey("dynamic_color") to false)))
        // A pick made in Settings now, the wallpaper included.
        assertEquals(ColourSource.WALLPAPER, AppSettings.colourChoiceOf(preferencesOf(source to "WALLPAPER", chosen to true)))
    }

    @Test fun choosingMaterialAppliesAtOnce() {
        var p by mutableStateOf(Prefs(theme = ThemeMode.LIGHT))
        var web: Boolean? = null
        compose.setContent {
            MailrTheme(p.theme, p.dynamicColor, p.webColours(), p.webLook) {
                web = LocalLook.current.web
                Surface(Modifier.fillMaxSize()) {
                    SettingsScreen(p, "Alex Morgan", "me@example.com", "https://mail.example.com", emptyList(),
                        SettingsCallbacks({}, { f -> p = f(p) }, {}, {}, {}))
                }
            }
        }
        compose.runOnIdle { assertEquals(true, web) }
        compose.onNodeWithText("Web style", substring = true).performClick()
        compose.onNodeWithText("Android (Material)").performClick()
        compose.runOnIdle {
            assertEquals(AppLook.MATERIAL, p.look)
            assertFalse(web!!)
            assertNull(p.colourChoice)
        }
    }
}
