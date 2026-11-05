package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHostState
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import io.github.murtazanasir.mailr.data.api.Message
import io.github.murtazanasir.mailr.data.api.MessageAccount
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.ui.conversation.ConversationActions
import io.github.murtazanasir.mailr.ui.conversation.ConversationScreen
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The message details say which of my accounts and addresses received a message, and "(Bcc)" when
 * To and Cc leave me out (backend core/delivery.py); the folded line adds "· Bcc to you".
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class MessageDeliveryTest {
    @get:Rule val compose = createComposeRule()

    private val json = Json { ignoreUnknownKeys = true }

    private fun reader(edit: (Message) -> Message) {
        val detail = Fixtures.detail.copy(messages = Fixtures.detail.messages.map { if (it.id == 502L) edit(it) else it })
        compose.setContent {
            MaterialTheme {
                ConversationScreen(detail, null, Fixtures.labels, setOf(502L), emptyMap(), null, null,
                    tabletop = false, snackbar = SnackbarHostState(), actions = ConversationActions(
                        back = null, act = {}, toggle = {}, expandAll = {}, star = { _, _ -> }, reply = { _, _ -> },
                        openAttachment = { _, _ -> }, showImages = {}, snooze = {}, labels = {}, summarize = {}, unsubscribe = {},
                        mailto = {}, retry = {}))
            }
        }
    }

    @Test fun decodesTheFields() {
        val m = json.decodeFromString(Message.serializer(), """{"id": 7, "delivered_to": ["sam@northwind.example"], "received_bcc": true,
            "account": [{"id": 3, "name": "Work", "address": "sam@northwind.example", "kind": "imap"},
                        {"id": 4, "name": "Old archive", "address": null, "kind": "file"}]}""")
        assertEquals(listOf("sam@northwind.example"), m.deliveredTo)
        assertTrue(m.receivedBcc)
        assertEquals("Work <sam@northwind.example>", m.account[0].display)
        assertNull(m.account[1].address)
        assertEquals("Old archive", m.account[1].display)
        // An older server sends none of them.
        val old = json.decodeFromString(Message.serializer(), """{"id": 8}""")
        assertEquals(emptyList<String>(), old.deliveredTo)
        assertFalse(old.receivedBcc)
        assertEquals(emptyList<MessageAccount>(), old.account)
    }

    @Test fun accountDisplay() {
        assertEquals("sam@northwind.example", MessageAccount(1, "sam@northwind.example", "sam@northwind.example").display)
        assertEquals("sam@northwind.example", MessageAccount(1, "", "sam@northwind.example").display)
        // A bidi control in the address is removed before it is shown.
        assertEquals("Home <sam@home.example>", MessageAccount(1, "Home", "sam\u202E@home.example").display)
    }

    @Test fun detailsShowDeliveredToWithBccAndTheAccount() {
        reader { it.copy(receivedBcc = true, deliveredTo = listOf("sam@northwind.example"),
            account = listOf(MessageAccount(3, "Work", "sam@northwind.example", "imap"))) }
        compose.onNodeWithText("· Bcc to you", substring = true).assertExists()
        compose.onNodeWithText("to Alex", substring = true).performClick()
        compose.onNodeWithTag("detail-delivered-to", useUnmergedTree = true).assertExists()
        compose.onNodeWithText("delivered to", substring = true, useUnmergedTree = true).assertExists()
        compose.onNodeWithText("(Bcc)", substring = true, useUnmergedTree = true).assertExists()
        compose.onNodeWithTag("detail-account", useUnmergedTree = true).assertExists()
        compose.onNodeWithText("Work <sam@northwind.example>", substring = true, useUnmergedTree = true).assertExists()
    }

    @Test fun noBccMarkWhenNamed() {
        reader { it.copy(receivedBcc = false, deliveredTo = listOf("me@example.com")) }
        compose.onNodeWithText("Bcc to you", substring = true).assertDoesNotExist()
        compose.onNodeWithText("to Alex", substring = true).performClick()
        compose.onNodeWithTag("detail-delivered-to", useUnmergedTree = true).assertExists()
        compose.onNodeWithText("(Bcc)", substring = true, useUnmergedTree = true).assertDoesNotExist()
        compose.onNodeWithTag("detail-account", useUnmergedTree = true).assertDoesNotExist()
    }
}
