// Right to left: a swipe toward the end of a row does the same
// thing in both directions. Right to left, a finger moving left is a swipe from start to end, and
// the row follows the finger.
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SwipeToDismissBoxValue
import androidx.compose.material3.Text
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.swipeLeft
import androidx.compose.ui.test.swipeRight
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.ui.components.SwipeRow
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class RtlSwipeTest {
    @get:Rule val compose = createComposeRule()

    private fun swiped(direction: LayoutDirection, gesture: androidx.compose.ui.test.TouchInjectionScope.() -> Unit): SwipeToDismissBoxValue? {
        var got: SwipeToDismissBoxValue? = null
        compose.setContent {
            CompositionLocalProvider(LocalLayoutDirection provides direction) {
                MaterialTheme {
                    SwipeRow(enableStart = true, enableEnd = true, onSwipe = { got = it; false }, background = { _, _ -> },
                        modifier = Modifier.fillMaxWidth().height(72.dp).testTag("row")) { Text("Row") }
                }
            }
        }
        compose.onNodeWithTag("row").performTouchInput(gesture)
        compose.waitForIdle()
        return got
    }

    @Test fun towardTheEndLeftToRight() {
        assertEquals(SwipeToDismissBoxValue.StartToEnd, swiped(LayoutDirection.Ltr) { swipeRight(startX = width * 0.05f, endX = width * 0.95f) })
    }

    @Test fun towardTheEndRightToLeftIsAFingerMovingLeft() {
        assertEquals(SwipeToDismissBoxValue.StartToEnd, swiped(LayoutDirection.Rtl) { swipeLeft(startX = width * 0.95f, endX = width * 0.05f) })
    }
}
