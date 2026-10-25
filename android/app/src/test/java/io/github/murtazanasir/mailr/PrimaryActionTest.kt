// ACT-SURFACES: android.list android.reader
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.test.core.app.ApplicationProvider
import androidx.compose.material3.SnackbarHostState
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.platform.UriHandler
import androidx.compose.ui.test.assertCountEquals
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import io.github.murtazanasir.mailr.data.api.ConversationSummary
import io.github.murtazanasir.mailr.data.api.PrimaryAction
import io.github.murtazanasir.mailr.data.settings.SwipeAction
import io.github.murtazanasir.mailr.ui.conversation.ConversationActions
import io.github.murtazanasir.mailr.ui.conversation.ConversationScreen
import io.github.murtazanasir.mailr.ui.mail.ConversationRow
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * mail.primary_action on android.list and android.reader: the email's main action as a chip
 * with its key fact; a tap opens the link in the browser. Only web links become chips (the
 * server never offers sign-in or verification links).
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class PrimaryActionTest {
    @get:Rule val compose = createComposeRule()
    private val opened = mutableListOf<String>()
    private val browser = object : UriHandler { override fun openUri(uri: String) { opened += uri } }

    private val track = PrimaryAction("TrackAction", "Track package", "https://track.example.com/1Z999", "Arriving today 1:30–4:30 PM")
    private fun conversation(pa: PrimaryAction?): ConversationSummary = Fixtures.conversations[4].copy(primaryAction = pa)

    private fun row(c: ConversationSummary) = compose.setContent {
        CompositionLocalProvider(LocalUriHandler provides browser) {
            MaterialTheme {
                ConversationRow(c, emptyMap(), selected = false, selectionMode = false, activated = false, compact = false,
                    swipeStart = SwipeAction.ARCHIVE, swipeEnd = SwipeAction.TRASH, onClick = {}, onLongClick = {},
                    onToggleSelect = {}, onStar = {}, onSwipe = {})
            }
        }
    }

    private fun reader(c: ConversationSummary) = compose.setContent {
        CompositionLocalProvider(LocalUriHandler provides browser) {
            MaterialTheme {
                ConversationScreen(Fixtures.detail.copy(conversation = c), null, Fixtures.labels, emptySet(), emptyMap(), null, null,
                    tabletop = false, snackbar = SnackbarHostState(), actions = ConversationActions(
                        back = null, act = {}, toggle = {}, expandAll = {}, star = { _, _ -> }, reply = { _, _ -> },
                        openAttachment = { _, _ -> }, showImages = {}, snooze = {}, labels = {}, summarize = {}, unsubscribe = {},
                        mailto = {}, retry = {}))
            }
        }
    }

    /** mail.primary_action on android.list: the chip after the snippet, with its fact; it opens the link. */
    @Test fun listChipOpensTheLink() {
        row(conversation(track))
        compose.onNodeWithText("Arriving today 1:30–4:30 PM").assertExists()
        compose.onNodeWithText("Track package").performClick()
        assertEquals(listOf(track.url), opened)
    }

    @Test fun listOffersOnlyWebLinks() {
        row(conversation(track.copy(url = "javascript:alert(1)")))
        compose.onAllNodesWithText("Track package").assertCountEquals(0)
    }

    /** mail.primary_action on android.reader: the same chip at the top of the reader. */
    @Test fun readerChipOpensTheLink() {
        reader(conversation(track))
        compose.onNodeWithText("Arriving today 1:30–4:30 PM").assertExists()
        compose.onNodeWithText("Track package").performClick()
        assertEquals(listOf(track.url), opened)
    }

    @Test fun readerWithoutAnActionHasNoChip() {
        reader(conversation(null))
        compose.onAllNodesWithText("Track package").assertCountEquals(0)
    }

    /** A verification email: the chip copies its one-time code (nothing is opened). */
    @Test fun aCodeChipCopiesTheCode() {
        row(conversation(PrimaryAction("code", "Copy code", "", null, code = "480204")))
        compose.onNodeWithText("Copy 480204").performClick()
        val cm = ApplicationProvider.getApplicationContext<Application>().getSystemService(android.content.ClipboardManager::class.java)
        assertEquals("480204", cm.primaryClip?.getItemAt(0)?.text?.toString())
        assertEquals(emptyList<String>(), opened)
    }
}
