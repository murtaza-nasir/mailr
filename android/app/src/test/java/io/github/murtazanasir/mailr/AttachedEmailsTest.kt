// ACT-SURFACES: android.reader
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import io.github.murtazanasir.mailr.data.api.Address
import io.github.murtazanasir.mailr.data.api.AttachedEmailBody
import io.github.murtazanasir.mailr.data.api.AttachedEmailSummary
import io.github.murtazanasir.mailr.data.api.Attachment
import io.github.murtazanasir.mailr.data.api.ConversationDetail
import io.github.murtazanasir.mailr.data.api.ConversationSummary
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.ui.conversation.AttachedState
import io.github.murtazanasir.mailr.ui.conversation.ConversationActions
import io.github.murtazanasir.mailr.ui.conversation.ConversationScreen
import io.github.murtazanasir.mailr.ui.conversation.LocalBodyRenderer
import io.github.murtazanasir.mailr.ui.conversation.ReaderExtrasUi
import io.github.murtazanasir.mailr.ui.conversation.attachedEmailLabel
import io.github.murtazanasir.mailr.ui.conversation.attachedKey
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * ACT mail.open_attached_email: an email attached to a message is a card in the reader.
 * Closed it shows the sender, subject and first words; open, its body
 * and files. A forward as attachment (no text of its own) opens its first card. A broken attached
 * email (no summary) stays a plain .eml file chip.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class AttachedEmailsTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()

    private val summary = AttachedEmailSummary(subject = "New access cards", fromName = "Harbor Property",
        fromEmail = "office@harborproperty.example", date = "2026-10-01T09:30:00+00:00",
        excerpt = "Old cards stop working on Friday.", attachments = 1)
    private val card = Attachment(2, "New access cards.eml", "message/rfc822", 900, email = summary)
    private val broken = Attachment(6, "attached-email.eml", "message/rfc822", 0)

    private fun detail(snippet: String) = Fixtures.detail.messages[1].copy(id = 777, snippet = snippet,
        attachments = listOf(card, broken)).let { ConversationDetail(Fixtures.detail.conversation, listOf(it)) }

    private val ready = AttachedState.Ready(AttachedEmailBody(part = 2, subject = "New access cards",
        from = Address("office@harborproperty.example", "Harbor Property"), to = listOf(Address("ops@example.org")),
        html = "<p>Old cards stop working on Friday at 6 PM.</p>",
        attachments = listOf(io.github.murtazanasir.mailr.data.api.AttachedFile(4, "map.pdf", "application/pdf", 5))), false)

    private fun screen(d: ConversationDetail, states: Map<String, AttachedState>, calls: MutableList<String>) {
        val actions = ConversationActions(
            back = null, act = {}, toggle = {}, expandAll = {}, star = { _, _ -> }, reply = { _, _ -> },
            openAttachment = { m, a -> calls += "file:${m.id}:${a.part}:${a.filename}" }, showImages = {}, snooze = {}, labels = {},
            summarize = {}, unsubscribe = {}, mailto = {}, retry = {},
            openAttachedEmail = { m, part, images -> calls += "open:${m.id}:$part:$images" })
        compose.setContent {
            MaterialTheme {
                CompositionLocalProvider(LocalBodyRenderer provides { html, modifier -> Text(html.replace(Regex("<[^>]+>"), ""), modifier) }) {
                    ConversationScreen(d, null, Fixtures.labels, setOf(777L), emptyMap(), null, null, tabletop = false,
                        snackbar = SnackbarHostState(), actions = actions, extras = ReaderExtrasUi(attached = states))
                }
            }
        }
    }

    @Test fun forwardAsAttachmentOpensTheFirstCard() {
        val calls = mutableListOf<String>()
        screen(detail(""), mapOf(attachedKey(777, 2) to ready), calls)
        compose.waitForIdle()
        assertTrue(calls.toString(), "open:777:2:null" in calls)
        // Subject and sender are bidi-isolated inside the label.
        compose.onNodeWithText("New access cards", substring = true).assertExists()
        compose.onNodeWithText("Attached email: ", substring = true).assertExists()
        compose.onNodeWithText("Old cards stop working on Friday at 6 PM.").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText("map.pdf", substring = true).performScrollTo().performClick()
        assertTrue(calls.toString(), "file:777:4:map.pdf" in calls)
    }

    @Test fun closedCardShowsTheExcerptAndOpensOnTap() {
        val calls = mutableListOf<String>()
        screen(detail("Forwarding this for the file."), emptyMap(), calls)
        compose.waitForIdle()
        assertTrue(calls.isEmpty())
        compose.onNodeWithText("Old cards stop working on Friday.").assertExists()
        // The broken one is a plain file; the readable one is not listed again as a file.
        compose.onNodeWithText("attached-email.eml", substring = true).assertExists()
        compose.onNodeWithText("New access cards.eml", substring = true).assertDoesNotExist()
        compose.onNodeWithTag("attached-email").performScrollTo().performClick()
        compose.waitForIdle()
        assertEquals(listOf("open:777:2:null"), calls)
    }

    @Test fun labelAndServerShapes() {
        val label = attachedEmailLabel(summary.copy(subject = "", fromName = ""))
        assertTrue(label, label.startsWith("Attached email: ") && "(no subject)" in label && "office@harborproperty.example" in label)
        val json = """{"part":2,"subject":"S","from":{"email":"a@example.org","name":"A"},"to":[],"cc":[],"date":null,
            "html":"<p>x</p>","text":"x","remote_images":true,"proxied":false,"junk":false,
            "attachments":[{"part":null,"filename":"f.pdf","type":"application/pdf","size":3,"inline":false}]}"""
        val b = MailrJson.decodeFromString(AttachedEmailBody.serializer(), json)
        assertTrue(b.remoteImages)
        assertNull(b.attachments.single().part)
        val row = MailrJson.decodeFromString(ConversationSummary.serializer(), """{"id":1,"match_in_attached":true}""")
        assertTrue(row.matchInAttached)
    }
}
