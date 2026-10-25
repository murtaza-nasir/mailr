// ACT-SURFACES: android.settings android.reader
package io.github.murtazanasir.mailr.ui.memory

import android.app.Application
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.unit.LayoutDirection
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.testing.until
import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import com.github.takahirom.roborazzi.captureScreenRoboImage
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * The Memory page and the person card, light and dark: the page on a phone, the page with the card as
 * a pane on an unfolded Fold, the card as a sheet on a phone, and both in ar-XB (right to left).
 * Output: app/screenshots/memory-<theme>-<shot>.png. Demo names only (FakeMemoryStore).
 * memory.learning_status, memory.add and person.card, person.memories are drawn here.
 */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class MemoryScreenshotTest(private val dark: Boolean) {
    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun params(): List<Array<Any>> = listOf(arrayOf<Any>(false), arrayOf<Any>(true))

        private const val PHONE = "w411dp-h891dp-port"
        private const val FOLD = "w841dp-h701dp-land"
        private const val RTL = "ar-rXB-ldrtl-w411dp-h891dp-port"

        fun ui(store: FakeMemoryStore) = MemoryUi(loading = false, active = store.active.toList(), suggested = store.suggested.toList(),
            learning = store.learningStatus)
    }

    @get:Rule val compose = createComposeRule()
    private val mode get() = if (dark) "dark" else "light"
    private val store = FakeMemoryStore()
    private val now = AppClock.now()

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

    private val noop = MemoryCallbacks({}, {}, {}, {}, {}, {}, {}, {}, {}, {}, {}, {}, {}, openSource = {}, openChat = { _, _ -> })

    @Test fun page() {
        set(PHONE) { MemoryScreen(ui(store), "Ambrose", noop, now = now) }
        compose.onRoot().captureRoboImage("screenshots/memory-$mode-page.png")
    }

    @Test fun pageWithCardPaneOnAFold() {
        val card = runBlocking { loadPersonCard(store, PersonTarget.Entry(2)) }
        set(FOLD) {
            MemoryLayout(wide = true, cardOpen = true,
                page = { mod -> MemoryScreen(ui(store), "Ambrose", noop, modifier = mod, now = now) },
                pane = { PersonCardContent(card, "Ambrose", { it == "priya.shah@northwind.example" }, now, noop, {}, {}) })
        }
        compose.onRoot().captureRoboImage("screenshots/memory-$mode-fold.png")
    }

    @Test fun cardSheetOnAPhone() {
        set(PHONE) {
            MemoryScreen(ui(store), "Ambrose", noop, now = now)
            PersonCardSheet(PersonTarget.Entry(2), store, "Ambrose", { it == "priya.shah@northwind.example" }, onClose = {})
        }
        compose.until("\"Ambrose's memory\" shown") { compose.onAllNodesWithText("Ambrose's memory").fetchSemanticsNodes().isNotEmpty() }
        compose.mainClock.advanceTimeBy(1000)
        compose.waitForIdle()
        captureScreenRoboImage("screenshots/memory-$mode-card.png")
    }

    @Test fun aPersonWithNoMail() {
        val card = runBlocking { loadPersonCard(store, PersonTarget.Entry(3)) }
        set(PHONE) { PersonCardContent(card, "Ambrose", { false }, now, noop, {}, {}) }
        compose.onRoot().captureRoboImage("screenshots/memory-$mode-no-mail.png")
    }

    @Test fun rightToLeft() {
        var direction: LayoutDirection? = null
        set(RTL) {
            direction = LocalLayoutDirection.current
            MemoryScreen(ui(store), "Ambrose", noop, now = now)
        }
        assertEquals(LayoutDirection.Rtl, direction)
        compose.onRoot().captureRoboImage("screenshots/memory-$mode-rtl-page.png")
    }

    @Test fun cardRightToLeft() {
        val card = runBlocking { loadPersonCard(store, PersonTarget.Entry(2)) }
        set(RTL) { PersonCardContent(card, "Ambrose", { true }, now, noop, {}, {}) }
        compose.onRoot().captureRoboImage("screenshots/memory-$mode-rtl-card.png")
    }
}
