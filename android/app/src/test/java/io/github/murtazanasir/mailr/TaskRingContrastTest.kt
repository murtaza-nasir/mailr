package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import io.github.murtazanasir.mailr.ui.tasks.taskRing
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** The empty task circle stands at 3:1 or more on its background in every look (WCAG 1.4.11). */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class TaskRingContrastTest {
    private fun contrast(a: Color, b: Color): Float {
        val la = a.luminance(); val lb = b.luminance()
        return (maxOf(la, lb) + 0.05f) / (minOf(la, lb) + 0.05f)
    }

    @Test fun theEmptyCircleHasThreeToOneInEveryLook() {
        val schemes = mapOf(
            "web light" to io.github.murtazanasir.mailr.look.WebTheme.colorScheme(dark = false),
            "web dark" to io.github.murtazanasir.mailr.look.WebTheme.colorScheme(dark = true),
            "material light" to io.github.murtazanasir.mailr.ui.theme.Light,
            "material dark" to io.github.murtazanasir.mailr.ui.theme.Dark,
        )
        for ((name, s) in schemes) {
            val ring = taskRing(s)
            for ((bgName, bg) in listOf("surface" to s.surface, "background" to s.background)) {
                val c = contrast(ring, bg)
                assertTrue("$name ring on $bgName: ${"%.2f".format(c)}:1", c >= 3f)
            }
        }
    }
}
