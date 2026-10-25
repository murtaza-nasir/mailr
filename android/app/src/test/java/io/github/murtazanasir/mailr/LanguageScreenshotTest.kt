// ACT-SURFACES: android.list android.drawer android.settings android.editor
// The shipped languages: the key screens in German
// (the longest words) and Simplified Chinese (another script), on a phone, light. Look for labels
// that are cut off or run out of their box. Output: app/screenshots/lang-<tag>-<screen>.png.
// Demo data only.
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.onRoot
import androidx.paging.PagingData
import androidx.paging.compose.collectAsLazyPagingItems
import io.github.murtazanasir.mailr.data.api.Address
import io.github.murtazanasir.mailr.data.api.Identity
import io.github.murtazanasir.mailr.data.repo.MailView
import io.github.murtazanasir.mailr.data.settings.Prefs
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.ui.compose.ComposeCallbacks
import io.github.murtazanasir.mailr.ui.compose.ComposeScreen
import io.github.murtazanasir.mailr.ui.compose.ComposeState
import io.github.murtazanasir.mailr.ui.mail.ListActions
import io.github.murtazanasir.mailr.ui.mail.MailDrawer
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

@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class LanguageScreenshotTest(private val tag: String, private val qualifier: String, private val inbox: String) {

    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "{0}")
        fun params(): List<Array<Any>> = listOf(
            arrayOf<Any>("de", "de", "Posteingang"),
            arrayOf<Any>("zh-Hans", "zh-rCN", "收件箱"),
        )
    }

    @get:Rule val compose = createComposeRule()

    @Before fun setUp() {
        RuntimeEnvironment.setQualifiers("$qualifier-w411dp-h891dp-port-notnight-xxhdpi")
    }

    private fun shot(name: String, content: @Composable () -> Unit) {
        compose.setContent {
            MailrTheme(ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize()) { content() }
            }
        }
        compose.onRoot().captureRoboImage("screenshots/lang-$tag-$name.png")
        assertEquals("the phone's language reaches the app's text", inbox, AppText.get(R.string.mail_folder_inbox))
    }

    private val noList = ListActions({}, {}, {}, {}, {}, {}, {}, {}, { _, _ -> }, {}, { _, _ -> }, {}, {}, {})

    @Test fun inbox() = shot("inbox") {
        val items = flowOf(PagingData.from(Fixtures.conversations)).collectAsLazyPagingItems()
        MailListPane(MailView(), items, emptyList(), null, Fixtures.labels, emptySet(), null, Prefs(),
            "Alex Morgan", "me@example.com", false, 0, SnackbarHostState(), true, noList)
    }

    @Test fun drawer() = shot("drawer") {
        MailDrawer(Fixtures.labels, MailView(), null, emptyMap(), "me@example.com", 2, {}, {}, {})
    }

    @Test fun compose() = shot("compose") {
        ComposeScreen(
            ComposeState(loading = false, identities = listOf(Identity(1, "me@example.com", "Alex Morgan", isDefault = true)),
                identityId = 1, to = listOf(Address("alice@example.com", "Alice Chen")),
                subject = "Re: Quarterly plan",
                blocks = io.github.murtazanasir.mailr.ui.compose.ComposeBody.ofText("Friday at 10 works for me."),
                title = AppText.get(R.string.compose_title_reply), saveStatus = AppText.get(R.string.compose_saved)),
            SnackbarHostState(),
            ComposeCallbacks({}, {}, {}, {}, {}, {}, {}, {}, { _, _ -> true }, { _, _ -> }, { _, _ -> }, { _, _ -> }, {}, {}, {}),
        )
    }

    @Test fun settings() = shot("settings") {
        SettingsScreen(Prefs(), "Alex Morgan", "me@example.com", "https://mail.example.com", emptyList(),
            SettingsCallbacks({}, {}, {}, {}, {}))
    }
}
