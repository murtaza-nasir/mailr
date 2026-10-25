// ACT-SURFACES: android.reader
package io.github.murtazanasir.mailr

import android.app.Application
import android.content.ClipboardManager
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHostState
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.test.longClick
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTouchInput
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.Address
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.ui.conversation.AddressCopy
import io.github.murtazanasir.mailr.ui.conversation.ConversationActions
import io.github.murtazanasir.mailr.ui.conversation.ConversationScreen
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * mail.copy_address on android.reader: a long-press on the open message's sender, or on an address
 * in its details, offers Copy address, Copy name and Copy name and address; the copy is marked not
 * sensitive; TalkBack has the same three choices as custom actions. A tap still collapses.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class CopyAddressTest {
    @get:Rule val compose = createComposeRule()
    private val app: Application = ApplicationProvider.getApplicationContext()
    private val toggled = mutableListOf<Long>()

    private fun reader() = compose.setContent {
        MaterialTheme {
            ConversationScreen(Fixtures.detail, null, Fixtures.labels, setOf(502L), emptyMap(), null, null,
                tabletop = false, snackbar = SnackbarHostState(), actions = ConversationActions(
                    back = null, act = {}, toggle = { toggled += it }, expandAll = {}, star = { _, _ -> }, reply = { _, _ -> },
                    openAttachment = { _, _ -> }, showImages = {}, snooze = {}, labels = {}, summarize = {}, unsubscribe = {},
                    mailto = {}, retry = {}))
        }
    }

    private fun clip(): Pair<String, Boolean?> {
        val c = app.getSystemService(ClipboardManager::class.java).primaryClip!!
        val sensitive = c.description.extras?.takeIf { it.containsKey("android.content.extra.IS_SENSITIVE") }
            ?.getBoolean("android.content.extra.IS_SENSITIVE")
        return c.getItemAt(0).text.toString() to sensitive
    }

    @Test fun choicesAndText() {
        val bich = Address("bich@example.com", "Bich Nguyen")
        assertEquals(AddressCopy.Kind.entries, AddressCopy.choices(bich))
        assertEquals(listOf(AddressCopy.Kind.ADDRESS), AddressCopy.choices(Address("bare@example.com")))
        assertEquals("bich@example.com", AddressCopy.text(bich, AddressCopy.Kind.ADDRESS))
        assertEquals("Bich Nguyen", AddressCopy.text(bich, AddressCopy.Kind.NAME))
        assertEquals("Bich Nguyen <bich@example.com>", AddressCopy.text(bich, AddressCopy.Kind.NAME_AND_ADDRESS))
        // The same quoting as the web (lib/clipboard.ts): a comma or quote in the name.
        assertEquals("\"Nguyen, Bich\" <bich@example.com>", AddressCopy.nameAndAddress(Address("bich@example.com", "Nguyen, Bich")))
        assertEquals("\"Bich \\\"B\\\" Nguyen\" <bich@example.com>", AddressCopy.nameAndAddress(Address("bich@example.com", "Bich \"B\" Nguyen")))
        assertEquals("bare@example.com", AddressCopy.nameAndAddress(Address("bare@example.com", " ")))
    }

    @Test fun longPressOnTheSenderCopiesTheAddressNotSensitive() {
        reader()
        compose.onNodeWithTag("sender-name").performTouchInput { longClick() }
        compose.onNodeWithText("Copy name").assertExists()
        compose.onNodeWithText("Copy name and address").assertExists()
        compose.onNodeWithText("Copy address").performClick()
        compose.waitForIdle()
        assertEquals("chair@pads.example" to false, clip())
        // The long-press did not also collapse the message.
        assertEquals(emptyList<Long>(), toggled)
    }

    @Test fun longPressMenuCopiesNameAndAddress() {
        reader()
        compose.onNodeWithTag("sender-name").performTouchInput { longClick() }
        compose.onNodeWithText("Copy name and address").performClick()
        compose.waitForIdle()
        assertEquals("Program Chair <chair@pads.example>", clip().first)
    }

    @Test fun aTapStillCollapses() {
        reader()
        compose.onNodeWithTag("sender-name").performClick()
        assertEquals(listOf(502L), toggled)
    }

    @Test fun talkBackOffersTheThreeCopies() {
        reader()
        val node = compose.onNodeWithTag("sender-name").fetchSemanticsNode()
        val actions = node.config.getOrNull(SemanticsActions.CustomActions).orEmpty()
        assertEquals(listOf("Copy address", "Copy name", "Copy name and address"), actions.map { it.label })
        compose.runOnUiThread { actions[1].action() }
        assertEquals("Program Chair", clip().first)
        assertFalse(clip().second!!)
    }

    @Test fun anAddressInTheDetailsCopies() {
        reader()
        compose.onNodeWithText("to Alex", substring = true).performClick()
        compose.onNodeWithText("board@pads.example", substring = true).performTouchInput { longClick() }
        compose.onNodeWithText("Copy address").performClick()
        compose.waitForIdle()
        assertEquals("board@pads.example", clip().first)
    }
}
