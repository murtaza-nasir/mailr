package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.adaptive.layout.AnimatedPane
import androidx.compose.material3.adaptive.layout.ListDetailPaneScaffold
import androidx.compose.material3.adaptive.layout.ListDetailPaneScaffoldRole
import androidx.compose.material3.adaptive.navigation.rememberListDetailPaneScaffoldNavigator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Modifier
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onRoot
import androidx.paging.PagingData
import androidx.paging.compose.collectAsLazyPagingItems
import io.github.murtazanasir.mailr.data.api.ConversationSummary
import io.github.murtazanasir.mailr.data.repo.MailView
import io.github.murtazanasir.mailr.data.settings.Prefs
import io.github.murtazanasir.mailr.data.settings.ThemeMode
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
 * android.list rows: labels on the top line, the Later tab of notes to self, on a small phone,
 * a phone and the open Pixel Fold, light and dark. Output: app/screenshots/rows-<device>-<theme>-<name>.png
 */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class RowScreenshotTest(private val device: String, private val qualifiers: String, private val dark: Boolean) {
    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "{0}-{2}")
        fun params(): List<Array<Any>> = listOf(
            "small" to "w360dp-h780dp-port|xxhdpi",
            "phone" to "w411dp-h891dp-port|xxhdpi",
            "fold-open" to "w841dp-h701dp-land|xxhdpi",
        ).flatMap { (n, q) -> listOf(arrayOf<Any>(n, q, false), arrayOf<Any>(n, q, true)) }
    }

    @get:Rule val compose = createComposeRule()

    @Before fun setUp() {
        val (size, density) = qualifiers.split('|')
        RuntimeEnvironment.setQualifiers("$size-${if (dark) "night" else "notnight"}-$density")
    }

    private val noList = ListActions({}, {}, {}, {}, {}, {}, {}, {}, { _, _ -> }, {}, { _, _ -> }, {}, {}, {})

    private fun shot(name: String, content: @Composable () -> Unit) {
        compose.setContent {
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize()) { content() }
            }
        }
        compose.onRoot().captureRoboImage("screenshots/rows-$device-${if (dark) "dark" else "light"}-$name.png")
    }

    @Composable
    private fun List(view: MailView, items: List<ConversationSummary>) {
        val paged = flowOf(PagingData.from(items)).collectAsLazyPagingItems()
        MailListPane(view, paged, emptyList(), null, RowFixtures.labels, emptySet(), null, Prefs(),
            "Alex Morgan", "me@example.com", false, 0, SnackbarHostState(), true, noList)
    }

    @Test fun inbox() = shot("inbox") { List(MailView(), RowFixtures.inbox) }

    @Test fun later() = shot("later") { List(RowFixtures.laterView, RowFixtures.later) }

    /** The list pane beside the reader, as on the open Fold and tablets. */
    @Test fun listDetail() = shot("list-detail") {
        val nav = rememberListDetailPaneScaffoldNavigator<String>()
        LaunchedEffect(Unit) { nav.navigateTo(ListDetailPaneScaffoldRole.Detail, "1") }
        ListDetailPaneScaffold(nav.scaffoldDirective, nav.scaffoldValue,
            listPane = { AnimatedPane { List(MailView(), RowFixtures.inbox) } },
            detailPane = { AnimatedPane { Surface(Modifier.fillMaxSize()) { Text("Reader") } } })
    }
}
