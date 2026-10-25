package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.width
import androidx.compose.ui.Modifier
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.swipeRight
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.settings.SwipeAction
import io.github.murtazanasir.mailr.ui.mail.ConversationRow
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** Swipes must be deliberate (mail.archive by swipe): a quick short flick does nothing, a long drag acts. */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w400dp-h800dp")
class SwipeTest {
    @get:Rule val compose = createComposeRule()

    private fun row(onSwipe: (SwipeAction) -> Unit) = compose.setContent {
        ConversationRow(Fixtures.conversations.first(), emptyMap(), selected = false, selectionMode = false, activated = false,
            compact = false, swipeStart = SwipeAction.ARCHIVE, swipeEnd = SwipeAction.TRASH, onClick = {}, onLongClick = {},
            onToggleSelect = {}, onStar = {}, onSwipe = onSwipe, modifier = Modifier.width(400.dp).fillMaxWidth())
    }

    @Test fun aQuickShortFlickDoesNothing() {
        val done = mutableListOf<SwipeAction>()
        row { done += it }
        compose.onRoot().performTouchInput { swipeRight(startX = 20f, endX = width * 0.25f, durationMillis = 40) }
        compose.waitForIdle()
        assertEquals(emptyList<SwipeAction>(), done)
    }

    @Test fun aLongDragArchives() {
        val done = mutableListOf<SwipeAction>()
        row { done += it }
        compose.onRoot().performTouchInput { swipeRight(startX = 20f, endX = width * 0.8f, durationMillis = 400) }
        compose.waitForIdle()
        assertEquals(listOf(SwipeAction.ARCHIVE), done)
    }
}
