package io.github.murtazanasir.mailr

import android.app.Application
import com.github.takahirom.roborazzi.RoborazziOptions
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.rules.TestRule
import org.junit.runner.RunWith
import org.junit.runners.model.Statement
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * Android's Material look (Settings → Look → Style: "Android (Material)"), so it cannot rot while
 * every other screenshot is in the web's look: a few main screens of the docs shots ([DocsShotsTest],
 * the same demo world), each in light and dark, on a phone.
 * Output and references: app/screenshots-material/<shot>-<light|dark>.png.
 */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class MaterialLookShotsTest(private val scheme: String) : DocsShotsTest() {
    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "{0}")
        fun params(): List<Array<Any>> = listOf(arrayOf<Any>("light"), arrayOf<Any>("dark"))

        /** The docs shots taken in Material's look. */
        val SHOTS = setOf("inbox", "drawer", "conversation", "compose", "calendarWeek", "tasksToday", "assistant", "settings")
    }

    /** Only [SHOTS]. */
    @get:Rule(order = Int.MIN_VALUE + 1) val only = TestRule { base, d ->
        object : Statement() {
            override fun evaluate() {
                assumeTrue(d.methodName.substringBefore('[') in SHOTS)
                base.evaluate()
            }
        }
    }

    override val webLook: Boolean get() = false

    override fun shotFile(name: String, dark: Boolean): Pair<String, RoborazziOptions> =
        "screenshots-material/${name.removePrefix("android-")}-$scheme.png" to RoborazziOptions()

    override fun shotDark(dark: Boolean): Boolean = scheme == "dark"
}
