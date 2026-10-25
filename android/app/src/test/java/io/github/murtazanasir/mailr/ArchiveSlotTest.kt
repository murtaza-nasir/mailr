// ACT-SURFACES: android.list android.reader
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.onAllNodesWithContentDescription
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.performClick
import androidx.compose.material3.SnackbarHostState
import io.github.murtazanasir.mailr.ui.conversation.ConversationActions
import io.github.murtazanasir.mailr.ui.conversation.ConversationScreen
import io.github.murtazanasir.mailr.data.api.ConversationSummary
import io.github.murtazanasir.mailr.data.api.Label
import io.github.murtazanasir.mailr.data.settings.SwipeAction
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.ui.mail.ArchiveSlot
import io.github.murtazanasir.mailr.ui.mail.MailPlace
import io.github.murtazanasir.mailr.ui.mail.SelectionBar
import io.github.murtazanasir.mailr.ui.mail.archiveRemovesLabel
import io.github.murtazanasir.mailr.ui.mail.archiveSlot
import io.github.murtazanasir.mailr.ui.mail.swipeLabel
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Row actions follow the row: a conversation that is not
 * in the Inbox offers Move to Inbox where Archive sits, on the swipe and the selection bar, also in
 * an Inbox tab's label section. A label's own list keeps Archive while it removes the label.
 *
 * Interaction ids: mail.archive, mail.move (android.list, android.reader)
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class ArchiveSlotTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()

    private val labels = listOf(Label(1, "Inbox", role = "inbox"), Label(2, "Trash", role = "trash"),
        Label(3, "Spam", role = "junk"), Label(19, "Later"), Label(28, "Financial")).associateBy { it.id }
    private val inInbox = ConversationSummary(id = 10, labelIds = listOf(1, 28))
    private val archived = ConversationSummary(id = 11, labelIds = listOf(28))
    private val later = ConversationSummary(id = 12, labelIds = listOf(19))

    private fun slot(rows: List<ConversationSummary>, place: MailPlace = MailPlace.OTHER, ctx: Long? = null, removes: Boolean = true) =
        archiveSlot(rows, place, labels, ctx, removes)

    @Test fun theRowDecides() {
        assertEquals(ArchiveSlot.ARCHIVE, slot(listOf(inInbox)))
        assertEquals(ArchiveSlot.INBOX, slot(listOf(archived)))
        assertEquals(ArchiveSlot.ARCHIVE, slot(listOf(archived, inInbox)))
        assertEquals(ArchiveSlot.ARCHIVE, slot(emptyList()))
        assertEquals(ArchiveSlot.ARCHIVE, archiveSlot(listOf(archived), MailPlace.OTHER, emptyMap(), null, true)) // labels not loaded yet
    }

    @Test fun aLabelListKeepsArchiveWhileItRemovesTheLabel() {
        assertEquals(ArchiveSlot.ARCHIVE, slot(listOf(later), ctx = 19))
        assertEquals(ArchiveSlot.INBOX, slot(listOf(later), ctx = 19, removes = false))
        assertEquals(ArchiveSlot.INBOX, slot(listOf(archived), ctx = 19))
        assertFalse(archiveRemovesLabel(JsonObject(mapOf("archive_removes_current_label" to JsonPrimitive(false)))))
        assertEquals(true, archiveRemovesLabel(null))
    }

    @Test fun trashAndSpamKeepTheirActions() {
        assertEquals(ArchiveSlot.KEEP, slot(listOf(archived), MailPlace.TRASH))
        assertEquals(ArchiveSlot.KEEP, slot(listOf(archived), MailPlace.SPAM))
        assertEquals(ArchiveSlot.KEEP, slot(listOf(ConversationSummary(id = 13, labelIds = listOf(2)))))
        assertEquals(ArchiveSlot.KEEP, slot(listOf(ConversationSummary(id = 14, labelIds = listOf(3)))))
    }

    @Test fun theSwipeSaysMoveToInbox() {
        assertEquals("Move to Inbox", swipeLabel(MailPlace.OTHER, SwipeAction.ARCHIVE, ArchiveSlot.INBOX))
        assertNull(swipeLabel(MailPlace.OTHER, SwipeAction.ARCHIVE, ArchiveSlot.ARCHIVE))
        assertEquals("Restore", swipeLabel(MailPlace.TRASH, SwipeAction.ARCHIVE, ArchiveSlot.KEEP))
    }

    @Test fun selectionBarOffersMoveToInbox() {
        val acted = mutableListOf<String>()
        compose.setContent {
            MailrTheme(ThemeMode.LIGHT, dynamic = false) {
                SelectionBar(1, {}, {}, { acted += it }, {}, {}, anyUnread = true, place = MailPlace.OTHER, toInbox = true)
            }
        }
        compose.onAllNodesWithContentDescription("Archive").assertCountEquals(0)
        compose.onNodeWithContentDescription("Move to Inbox").performClick()
        assertEquals(listOf("inbox"), acted)
    }

    private fun reader(labelIds: List<Long>, acted: MutableList<String>, ctx: Long? = null) {
        val actions = ConversationActions(back = {}, act = { acted += it }, toggle = {}, expandAll = {}, star = { _, _ -> },
            reply = { _, _ -> }, openAttachment = { _, _ -> }, showImages = {}, snooze = {}, labels = {}, summarize = {},
            unsubscribe = {}, mailto = {}, retry = {})
        val d = Fixtures.detail.copy(conversation = Fixtures.detail.conversation.copy(labelIds = labelIds))
        compose.setContent {
            MailrTheme(ThemeMode.LIGHT, dynamic = false) {
                ConversationScreen(d, null, Fixtures.labels, emptySet(), emptyMap(), null, null, false,
                    SnackbarHostState(), actions, place = MailPlace.OTHER, contextLabelId = ctx)
            }
        }
    }

    @Test fun readerOffersMoveToInboxForArchivedMail() {
        val acted = mutableListOf<String>()
        reader(listOf(10), acted)
        compose.onAllNodesWithContentDescription("Archive").assertCountEquals(0)
        compose.onNodeWithContentDescription("Move to Inbox").performClick()
        assertEquals(listOf("inbox"), acted)
    }

    @Test fun readerKeepsArchiveInTheInbox() {
        val acted = mutableListOf<String>()
        reader(listOf(1, 10), acted)
        compose.onNodeWithContentDescription("Archive").performClick()
        assertEquals(listOf("archive"), acted)
    }

    @Test fun readerKeepsArchiveInTheLabelsOwnList() {
        val acted = mutableListOf<String>()
        reader(listOf(10), acted, ctx = 10)
        compose.onAllNodesWithContentDescription("Move to Inbox").assertCountEquals(0)
        compose.onNodeWithContentDescription("Archive").performClick()
        assertEquals(listOf("archive"), acted)
    }
}
