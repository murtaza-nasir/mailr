// ACT-SURFACES: android.editor
package io.github.murtazanasir.mailr

import android.app.Application
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.Shader
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onRoot
import io.github.murtazanasir.mailr.data.api.Address
import io.github.murtazanasir.mailr.data.api.Identity
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.ui.compose.BodyBlock
import io.github.murtazanasir.mailr.ui.compose.ComposeBody
import io.github.murtazanasir.mailr.ui.compose.ComposeCallbacks
import io.github.murtazanasir.mailr.ui.compose.ComposeScreen
import io.github.murtazanasir.mailr.ui.compose.ComposeState
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.io.ByteArrayOutputStream

/**
 * Android 0.4.6, light and dark: compose with an inline photo (ACT mail.insert_image), one still
 * uploading, and a web draft whose formatting is kept.
 * Output: app/screenshots/compose-<theme>-<name>.png
 */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class InlineImageScreenshotTest(private val dark: Boolean) {
    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun params(): List<Array<Any>> = listOf(arrayOf<Any>(false), arrayOf<Any>(true))
    }

    @get:Rule val compose = createComposeRule()
    private val mode get() = if (dark) "dark" else "light"

    private val noop = ComposeCallbacks({}, {}, {}, {}, {}, {}, {}, {}, { _, _ -> true }, { _, _ -> }, { _, _ -> }, { _, _ -> }, {}, {}, {})

    /** A photo-like picture: sky, hills and a sun. */
    private fun photo(w: Int, h: Int): ByteArray {
        val b = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
        val c = Canvas(b)
        c.drawRect(0f, 0f, w.toFloat(), h.toFloat(), Paint().apply {
            shader = LinearGradient(0f, 0f, 0f, h.toFloat(), Color.rgb(90, 150, 220), Color.rgb(250, 210, 160), Shader.TileMode.CLAMP)
        })
        c.drawCircle(w * 0.7f, h * 0.35f, h * 0.12f, Paint().apply { color = Color.rgb(255, 236, 150) })
        c.drawOval(-w * 0.2f, h * 0.62f, w * 0.7f, h * 1.4f, Paint().apply { color = Color.rgb(70, 130, 80) })
        c.drawOval(w * 0.35f, h * 0.7f, w * 1.2f, h * 1.5f, Paint().apply { color = Color.rgb(50, 105, 65) })
        return ByteArrayOutputStream().also { b.compress(Bitmap.CompressFormat.JPEG, 90, it) }.toByteArray()
    }

    private fun shot(name: String, s: ComposeState) {
        RuntimeEnvironment.setQualifiers("w411dp-h891dp-port-${if (dark) "night" else "notnight"}-xxhdpi")
        compose.setContent {
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.surface) {
                    ComposeScreen(s, SnackbarHostState(), noop)
                }
            }
        }
        compose.onRoot().captureRoboImage("screenshots/compose-$mode-$name.png")
    }

    private fun state(blocks: List<BodyBlock>, previews: Map<Long, ByteArray>) = ComposeState(
        loading = false, identities = listOf(Identity(1, "me@example.com", "Alex Morgan", isDefault = true)),
        identityId = 1, to = listOf(Address("alice@example.com", "Alice Chen")), subject = "Site visit photos",
        blocks = blocks, previews = previews, title = "Compose", saveStatus = "Saved",
    )

    @Test fun inlineImage() {
        val img = BodyBlock.Image(ComposeBody.nextKey(), "0123456789abcdef0123456789abcdef", 600, 400)
        shot("inline-image", state(listOf(
            BodyBlock.Text(ComposeBody.nextKey(), "Hi Alice,\n\nHere is the north side of the building:"),
            img,
            BodyBlock.Text(ComposeBody.nextKey(), "The crack runs along the second floor.\n\nRowan"),
        ), mapOf(img.key to photo(1200, 800))))
    }

    @Test fun inlineUploading() {
        val img = BodyBlock.Image(ComposeBody.nextKey(), null, 300, 400)
        shot("inline-uploading", state(listOf(
            BodyBlock.Text(ComposeBody.nextKey(), "One more from the roof:"),
            img,
            BodyBlock.Text(ComposeBody.nextKey(), ""),
        ), mapOf(img.key to photo(600, 800))))
    }

    @Test fun keptHtml() {
        val parsed = ComposeBody.parse("<p>Hi <strong>Alice</strong>,</p><p>The plan:</p>" +
            "<ul><li><p>Budget by <em>Friday</em></p></li><li><p>Hiring</p></li></ul><p>Thanks</p>")
        shot("kept-html", state(parsed.blocks, emptyMap()))
    }
}
