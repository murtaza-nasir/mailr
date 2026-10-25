package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.time.AppClock
import android.app.Application
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.foundation.layout.fillMaxSize
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onRoot
import androidx.paging.PagingData
import androidx.paging.compose.collectAsLazyPagingItems
import io.github.murtazanasir.mailr.data.repo.MailView
import io.github.murtazanasir.mailr.data.settings.Prefs
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.ui.conversation.BodyState
import io.github.murtazanasir.mailr.ui.conversation.ConversationActions
import io.github.murtazanasir.mailr.ui.conversation.ConversationScreen
import io.github.murtazanasir.mailr.ui.mail.ListActions
import io.github.murtazanasir.mailr.ui.mail.MailListPane
import io.github.murtazanasir.mailr.ui.mail.Section
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import kotlinx.coroutines.flow.flowOf
import org.junit.Before
import org.junit.Rule
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import io.github.murtazanasir.mailr.data.api.MessageBody

/**
 * Screenshots of every main screen on the devices that matter: a phone, the Pixel Fold
 * unfolded (inner screen, two panes) and a tablet in landscape, each in light and dark.
 * Output: app/screenshots/<device>-<theme>-<screen>.png
 *
 * The folded Fold (cover screen, 411 x 797 dp) is not among them: it is as wide as the phone, so
 * it has the same window size class and layout, and its shots differed from the phone's only in
 * height.
 *
 * The screens are split by area into several classes (Device*ShotsTest), so the test JVMs share
 * them out instead of one JVM taking all the shots; this class holds the devices and the helpers.
 */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
abstract class DeviceShots(protected val device: String, private val qualifiers: String, private val dark: Boolean) {

    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "{0}-{2}")
        fun params(): List<Array<Any>> {
            val devices = listOf(
                "phone" to "w411dp-h891dp-port|xxhdpi",
                "fold-open" to "w841dp-h701dp-land|xxhdpi",
                "tablet" to "w1280dp-h800dp-land|xhdpi",
            )
            return devices.flatMap { (n, q) -> listOf(arrayOf<Any>(n, q, false), arrayOf<Any>(n, q, true)) }
        }
    }

    @get:Rule val compose = createComposeRule()

    @Before fun setUp() {
        // Qualifier order matters: night mode sits between orientation and density.
        val (size, density) = qualifiers.split('|')
        RuntimeEnvironment.setQualifiers("$size-${if (dark) "night" else "notnight"}-$density")
    }

    protected fun shot(name: String, content: @Composable () -> Unit) {
        compose.setContent {
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize()) { content() }
            }
        }
        compose.onRoot().captureRoboImage("screenshots/$device-${if (dark) "dark" else "light"}-$name.png")
    }

    protected val noList = ListActions({}, {}, {}, {}, {}, {}, {}, {}, { _, _ -> }, {}, { _, _ -> }, {}, {}, {})
    protected val noConv = ConversationActions(back = {}, act = {}, toggle = {}, expandAll = {}, star = { _, _ -> }, reply = { _, _ -> },
        openAttachment = { _, _ -> }, showImages = {}, snooze = {}, labels = {}, summarize = {}, unsubscribe = {}, mailto = {}, retry = {})

    @Composable
    protected fun List(sections: List<Section> = emptyList(), selection: Set<Long> = emptySet(), title: String? = null) {
        val items = flowOf(PagingData.from(Fixtures.conversations.drop(if (sections.isEmpty()) 0 else 2))).collectAsLazyPagingItems()
        MailListPane(MailView(), items, sections, title, Fixtures.labels, selection, null, Prefs(),
            "Alex Morgan", "me@example.com", false, 0, SnackbarHostState(), true, noList)
    }

    @Composable
    protected fun Conversation(quote: String? = null) {
        val html = "<p>Dear colleague,</p><p>As Program Chair for the 2027 <b>Prairie Analytics and Decision Society</b> " +
            "conference, I invite you to submit your research. Award nominations close <mark class=\"mailr-hl\">Sep 30</mark>.</p>" +
            "<p>Best regards,<br>Program Chair</p>"
        ConversationScreen(Fixtures.detail, null, Fixtures.labels, setOf(502L),
            mapOf(502L to BodyState.Ready(MessageBody(html = html, source = "html", remoteImages = true), false)),
            null, quote, false, SnackbarHostState(), noConv)
    }

    protected val today = AppClock.today()
    protected val calEvents get() = io.github.murtazanasir.mailr.ui.calendar.dayEvents(Fixtures.events, emptyList())
    protected val noon = today.atTime(12, 20).atZone(java.time.ZoneId.systemDefault()).toInstant().toEpochMilli()
}
