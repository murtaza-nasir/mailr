package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import io.github.murtazanasir.mailr.data.api.Label
import io.github.murtazanasir.mailr.data.api.Signal
import io.github.murtazanasir.mailr.data.settings.SwipeAction
import io.github.murtazanasir.mailr.ui.mail.ConversationRow
import io.github.murtazanasir.mailr.ui.mail.inSpamOrTrash
import io.github.murtazanasir.mailr.ui.mail.signalChip
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.time.LocalDate

/** Triage signal chips on android.list rows: Reply needed, Waiting, Due (red within two days); none in
 *  Spam or Trash. */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class SignalChipsTest {
    @get:Rule val compose = createComposeRule()

    @Test fun deadlinesWithinTwoDaysAreUrgent() {
        val today = LocalDate.of(2026, 10, 1)
        assertEquals("Due Oct 3" to true, signalChip(Signal("deadline", date = "2026-10-03"), today))
        assertEquals("Due Sep 30" to true, signalChip(Signal("deadline", date = "2026-09-30"), today))
        assertEquals("Due Oct 4" to false, signalChip(Signal("deadline", date = "2026-10-04"), today))
        assertEquals("Reply needed" to false, signalChip(Signal("needs_reply"), today))
        assertEquals(null, signalChip(Signal("other"), today))
    }

    @Test fun theRowShowsItsSignals() {
        val c = Fixtures.conversations[4].copy(signals = listOf(Signal("needs_reply"), Signal("deadline", date = "2026-12-24")))
        compose.setContent {
            MaterialTheme {
                ConversationRow(c, emptyMap(), selected = false, selectionMode = false, activated = false, compact = false,
                    swipeStart = SwipeAction.ARCHIVE, swipeEnd = SwipeAction.TRASH, onClick = {}, onLongClick = {},
                    onToggleSelect = {}, onStar = {}, onSwipe = {})
            }
        }
        compose.onNodeWithText("Reply needed").assertExists()
        compose.onNodeWithText("Due Dec 24").assertExists()
    }

    private val roleLabels = mapOf(
        1L to Label(1, "Inbox", role = "inbox"), 2L to Label(2, "Spam", role = "junk"),
        3L to Label(3, "Trash", role = "trash"), 10L to Label(10, "Work"),
    )

    @Test fun spamAndTrashHideTheSignals() {
        assertEquals(true, inSpamOrTrash(listOf(2L), roleLabels))
        assertEquals(true, inSpamOrTrash(listOf(2L, 3L), roleLabels))
        assertEquals(false, inSpamOrTrash(listOf(1L, 3L), roleLabels))
        assertEquals(false, inSpamOrTrash(listOf(10L, 3L), roleLabels))
        assertEquals(false, inSpamOrTrash(listOf(2L), emptyMap())) // labels not loaded yet
    }

    @Test fun aRowInSpamShowsNoChipsAndBackInTheInboxItDoes() {
        val sigs = listOf(Signal("needs_reply"), Signal("deadline", date = "2026-12-24"))
        var c by mutableStateOf(Fixtures.conversations[4].copy(signals = sigs, labelIds = listOf(2L)))
        compose.setContent {
            MaterialTheme {
                ConversationRow(c, roleLabels, selected = false, selectionMode = false, activated = false, compact = false,
                    swipeStart = SwipeAction.ARCHIVE, swipeEnd = SwipeAction.TRASH, onClick = {}, onLongClick = {},
                    onToggleSelect = {}, onStar = {}, onSwipe = {})
            }
        }
        compose.onNodeWithText("Reply needed").assertDoesNotExist()
        compose.onNodeWithText("Due Dec 24").assertDoesNotExist()
        c = c.copy(labelIds = listOf(1L))
        compose.waitForIdle()
        compose.onNodeWithText("Reply needed").assertExists()
        compose.onNodeWithText("Due Dec 24").assertExists()
    }
}
