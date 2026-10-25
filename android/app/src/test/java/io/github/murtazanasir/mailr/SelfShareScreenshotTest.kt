// ACT-SURFACES: android.share
package io.github.murtazanasir.mailr

import android.app.Application
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.Shader
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.painter.BitmapPainter
import androidx.compose.ui.test.assertIsSelected
import androidx.compose.ui.test.assertIsNotSelected
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.assertTextEquals
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextReplacement
import io.github.murtazanasir.mailr.data.api.Label
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.ui.share.SelfFile
import io.github.murtazanasir.mailr.ui.share.SelfImage
import io.github.murtazanasir.mailr.ui.share.SelfLabels
import io.github.murtazanasir.mailr.ui.share.SelfLink
import io.github.murtazanasir.mailr.ui.share.SelfPhase
import io.github.murtazanasir.mailr.ui.share.SelfPreview
import io.github.murtazanasir.mailr.ui.share.SelfShareCallbacks
import io.github.murtazanasir.mailr.ui.share.SelfShareOverlay
import io.github.murtazanasir.mailr.ui.share.SelfUi
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * The Send to self card (mail.send_to_self, Android 0.5.9): what is sent, the subject, the note,
 * the quick labels (mail.send_to_self_labels) and Send, in light and dark, at font scale 1.3, on
 * a small phone, with and without labels, for a link, one image and several files; then the
 * status card after Send. The chips and the "More…" picker are driven here too.
 * Output: app/screenshots/send-to-self-<case>.png
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class SelfShareScreenshotTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()

    private val labels = listOf(
        Label(10, "Recipes", color = "#e37400", total = 12),
        Label(11, "Reading", color = "#f9ab00", total = 80),
        Label(12, "Later/Watch", color = "#1a73e8", total = 3),
        Label(13, "Travel", color = "#a142f4", total = 40),
        Label(14, "Finance", color = "#34a853", total = 60),
        Label(15, "Family", color = "#e91e63", total = 5),
        Label(16, "Studio", color = "#8e24aa", total = 1),
        Label(17, "Clients/Lumen Coffee", color = "#0f9d8a", total = 30),
    )

    private val link = SelfPreview(link = SelfLink("https://www.seriouseats.com/lentil-soup", "The best lentil soup you'll ever make",
        "seriouseats.com"))

    private fun ui(preview: SelfPreview = link, subject: String = "The best lentil soup you'll ever make",
                   selected: List<Long> = listOf(10L), withLabels: Boolean = true, phase: SelfPhase = SelfPhase.CONFIRM,
                   files: Int = 0, note: String = "") =
        SelfUi(phase = phase, subject = subject, preview = preview, files = files, note = note,
            labels = if (withLabels) labels else emptyList(),
            quickLabels = if (withLabels) SelfLabels.quick(labels, listOf(12L), selected) else emptyList(),
            selected = if (withLabels) selected else emptyList())

    private fun photo(seed: Int): BitmapPainter {
        val b = Bitmap.createBitmap(240, 240, Bitmap.Config.ARGB_8888)
        val colors = listOf(intArrayOf(0xFFFFB74D.toInt(), 0xFFE65100.toInt()), intArrayOf(0xFF4FC3F7.toInt(), 0xFF01579B.toInt()),
            intArrayOf(0xFFAED581.toInt(), 0xFF33691E.toInt()), intArrayOf(0xFFCE93D8.toInt(), 0xFF4A148C.toInt()))[seed % 4]
        Canvas(b).drawRect(0f, 0f, 240f, 240f, Paint().apply {
            shader = LinearGradient(0f, 0f, 240f, 240f, colors[0], colors[1], Shader.TileMode.CLAMP)
        })
        Canvas(b).drawCircle(170f, 70f, 34f, Paint().apply { color = 0xCCFFFFFF.toInt(); isAntiAlias = true })
        return BitmapPainter(b.asImageBitmap())
    }

    private var state by mutableStateOf(SelfUi())
    private var confirmed = 0
    private var closed = 0

    private val cb = SelfShareCallbacks(
        close = { closed++ }, confirm = { confirmed++ }, undo = {}, addNote = {},
        setNote = { state = state.copy(note = it) }, send = {}, retry = {}, openApp = {},
        setSubject = { state = state.copy(subjectEdit = it) },
        toggleLabel = { id -> state = state.copy(selected = if (id in state.selected) state.selected - id else state.selected + id) },
        pickLabels = { ids ->
            val extra = ids.filter { id -> state.quickLabels.none { it.id == id } }.mapNotNull { id -> labels.firstOrNull { it.id == id } }
            state = state.copy(selected = ids, quickLabels = extra + state.quickLabels)
        },
    )

    private fun show(s: SelfUi, dark: Boolean = false, size: String = "w411dp-h891dp", fontScale: Float = 1f) {
        RuntimeEnvironment.setQualifiers("$size-port-${if (dark) "night" else "notnight"}-xxhdpi")
        RuntimeEnvironment.setFontScale(fontScale)
        state = s
        compose.setContent {
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize()) { Card() }
            }
        }
        compose.waitForIdle()
    }

    @Composable private fun Card() = SelfShareOverlay(state, cb)

    private fun shot(name: String) = compose.onRoot().captureRoboImage("screenshots/send-to-self-$name.png")

    @Test fun linkLight() {
        show(ui())
        compose.onNodeWithText("Send to yourself").assertExists()
        compose.onNodeWithText("seriouseats.com").assertExists()
        shot("link-light")
    }

    @Test fun linkDark() {
        show(ui(selected = listOf(10L, 12L)), dark = true)
        shot("link-dark")
    }

    @Test fun linkFontScale() {
        show(ui(), fontScale = 1.3f)
        compose.onNodeWithTag("self-send-confirm").assertExists()
        shot("link-font-1.3")
    }

    @Test fun smallPhone() {
        show(ui(preview = SelfPreview(text = "Pick up the dry cleaning before 6, and call the plumber about the kitchen tap.",
            link = null), subject = "Pick up the dry cleaning before 6"), size = "w360dp-h640dp", fontScale = 1.15f)
        compose.onNodeWithTag("self-send-confirm").assertExists()
        shot("small-phone")
    }

    @Test fun withoutLabels() {
        show(ui(withLabels = false))
        compose.onNodeWithText("Pick labels…").assertExists()
        shot("no-labels")
    }

    @Test fun nothingPickedYet() {
        show(ui(selected = emptyList()))
        compose.onNodeWithText("Filters still apply").assertExists()
        shot("none-picked")
    }

    @Test fun oneImage() {
        show(ui(preview = SelfPreview(images = listOf(SelfImage(photo(0), "PXL_20261004_181331.jpg", 3_400_000))),
            subject = "PXL_20261004_181331.jpg", files = 1, selected = listOf(13L)))
        shot("image-light")
    }

    @Test fun severalFiles() {
        show(ui(preview = SelfPreview(images = (0 until 5).map { SelfImage(photo(it), "IMG_$it.jpg", 1_200_000) },
            files = listOf(SelfFile("Boarding pass.pdf", "application/pdf", 182_000), SelfFile("Itinerary.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document", 48_000))),
            subject = "IMG_0.jpg and 6 more", files = 7, selected = listOf(13L, 14L)), dark = true)
        compose.onNodeWithText("Boarding pass.pdf").assertExists()
        compose.onNodeWithText("+1").assertExists()
        shot("files-dark")
    }

    @Test fun noteToSelf() {
        show(SelfUi(phase = SelfPhase.EDITING, noteOnly = true, note = "Ask Dana about the venue deposit",
            labels = labels, quickLabels = SelfLabels.quick(labels, emptyList(), listOf(15L)), selected = listOf(15L)))
        compose.onNodeWithText("Note to self").assertExists()
        shot("note-to-self")
    }

    @Test fun sentWithLabels() {
        show(ui(phase = SelfPhase.SENT, selected = listOf(10L, 12L)))
        compose.onNodeWithText("Sent to yourself").assertExists()
        compose.onNodeWithTag("self-send-undo").assertExists()
        shot("sent")
    }

    /**
     * A note typed on the form went with the message, but the card used to
     * offer "Add a note" as if there were none. Now the card shows the note and does not ask again;
     * "Add a note" stays for a send without one.
     */
    @Test fun aNoteTypedOnTheFormIsShownAndNotAskedForAgain() {
        show(ui(phase = SelfPhase.CONFIRM))
        compose.onNodeWithTag("self-send-note").performTextInput("How was it not able to find this?")
        compose.runOnIdle { assertEquals("How was it not able to find this?", state.note) }
        state = state.copy(phase = SelfPhase.SENT)
        compose.waitForIdle()
        compose.onNodeWithText("Sent to yourself").assertExists()
        compose.onNodeWithTag("self-send-sent-note", useUnmergedTree = true).assertTextEquals("Note: How was it not able to find this?")
        compose.onNodeWithTag("self-send-add-note").assertDoesNotExist()
        compose.onNodeWithTag("self-send-undo").assertExists()
        state = state.copy(note = "")
        compose.waitForIdle()
        compose.onNodeWithTag("self-send-add-note").assertExists()
        compose.onNodeWithTag("self-send-sent-note", useUnmergedTree = true).assertDoesNotExist()
    }

    /** mail.send_to_self_labels: tapping chips picks and unpicks; "More…" opens the full picker. */
    @Test fun chipsToggleAndMoreOpensThePicker() {
        show(ui(selected = listOf(10L)))
        compose.onNodeWithTag("self-label-10").assertIsSelected()
        compose.onNodeWithTag("self-label-12").assertIsNotSelected().performClick()
        compose.onNodeWithTag("self-label-10").performClick()
        compose.runOnIdle { assertEquals(listOf(12L), state.selected) }
        compose.onNodeWithTag("self-label-10").assertIsNotSelected()
        compose.onNodeWithTag("self-label-12").assertIsSelected()
        // "More…": the full label picker with its search; Studio is not a chip yet.
        compose.onNodeWithTag("self-send-more-labels").performClick()
        compose.onNodeWithText("Labels for this note").assertExists()
        compose.onNodeWithText("Filter labels").assertExists()
        compose.onNodeWithText("Studio").performClick()
        compose.onNodeWithText("Apply").performClick()
        compose.runOnIdle { assertEquals(listOf(12L, 16L), state.selected) }
        compose.onNodeWithTag("self-label-16").assertIsSelected()
    }

    @Test fun subjectNoteAndSend() {
        show(ui())
        compose.onNodeWithTag("self-send-subject").performTextReplacement("Soup for Sunday")
        compose.onNodeWithTag("self-send-note").performTextReplacement("Double the cumin")
        compose.runOnIdle {
            assertEquals("Soup for Sunday", state.shownSubject)
            assertEquals("Double the cumin", state.note)
        }
        compose.onNodeWithTag("self-send-confirm").performClick()
        compose.runOnIdle { assertEquals(1, confirmed) }
        compose.onNodeWithTag("self-send-cancel").performClick()
        compose.runOnIdle { assertEquals(1, closed) }
    }

    @Test fun sendShowsProgressWhileItQueues() {
        show(ui().copy(submitting = true))
        compose.onNodeWithText("Sending…").assertExists()
        shot("submitting")
    }
}
