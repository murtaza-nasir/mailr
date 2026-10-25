// ACT-SURFACES: android.setup android.drawer android.editor
package io.github.murtazanasir.mailr

import android.app.Application
import android.os.Looper
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.lifecycle.SavedStateHandle
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.Address
import io.github.murtazanasir.mailr.data.api.Identity
import io.github.murtazanasir.mailr.data.api.MailTemplate
import io.github.murtazanasir.mailr.data.api.OutboxRow
import io.github.murtazanasir.mailr.data.api.SetupState
import io.github.murtazanasir.mailr.data.api.acceptInvite
import io.github.murtazanasir.mailr.data.auth.PendingInvite
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.repo.MailView
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.testing.until
import io.github.murtazanasir.mailr.ui.compose.ComposeArgs
import io.github.murtazanasir.mailr.ui.compose.ComposeBody
import io.github.murtazanasir.mailr.ui.compose.ComposeCallbacks
import io.github.murtazanasir.mailr.ui.compose.ComposeScreen
import io.github.murtazanasir.mailr.ui.compose.ComposeState
import io.github.murtazanasir.mailr.ui.compose.ComposeViewModel
import io.github.murtazanasir.mailr.ui.compose.insertAt
import io.github.murtazanasir.mailr.ui.compose.templateText
import io.github.murtazanasir.mailr.ui.mail.MailDrawer
import io.github.murtazanasir.mailr.ui.mail.OutboxCount
import io.github.murtazanasir.mailr.ui.mail.OutboxSheet
import io.github.murtazanasir.mailr.ui.mail.outboxLine
import io.github.murtazanasir.mailr.ui.onboarding.FinishOnWeb
import io.github.murtazanasir.mailr.ui.onboarding.FinishOnWebState
import io.github.murtazanasir.mailr.ui.onboarding.parseSetupInvite
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.jsonObject
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.util.concurrent.CopyOnWriteArrayList

/**
 * Household onboarding and parity on the phone: household.join from a setup link
 * with an invite, the "Finish setup on the web" inbox, the Outbox in the mail drawer
 * (draft.outbox, draft.retry_send) and templates in the composer (template.insert).
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class HouseholdPhoneTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()
    private val app: Application = ApplicationProvider.getApplicationContext()
    private var server: MockWebServer? = null
    private val seen = CopyOnWriteArrayList<String>()
    private val token = "AbCdEfGhIjKlMnOpQrStUvWx_yz-0123456789"

    @After fun tearDown() {
        shadowOf(Looper.getMainLooper()).idle()
        graphs.close()
        server?.shutdown()
    }

    private fun until(what: String, cond: () -> Boolean) = eventually(what, detail = { "requests $seen" }, check = cond)

    private fun start(): AppGraph {
        val s = MockWebServer()
        s.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.path.orEmpty()
                seen += "${request.method} $path ${request.body.readUtf8()}"
                return when {
                    path == "/api/v1/invites/$token/accept" -> MockResponse().setBody("""{"next":"/?setup=1","applied":true}""")
                    path == "/api/v1/me" -> MockResponse().setBody("""{"user":{"id":1,"email":"me@example.com","name":"Me"},
                        "identities":[{"id":7,"email":"me@example.com","name":"Me","is_default":true}]}""")
                    path.startsWith("/api/v1/compose/reply") -> MockResponse().setBody(
                        """{"payload":{"to":[{"email":"bob@example.com","name":"Bob"}],"subject":"Re: Q3","in_reply_to_message_id":502}}""")
                    path == "/api/v1/templates/3/expand" -> MockResponse().setBody(
                        """{"id":3,"subject":"Office hours","body_html":"<p>Hi Bob,</p><p>My office hours are Tuesday 2–4.</p>"}""")
                    path.startsWith("/api/v1/drafts") -> MockResponse().setBody("""{"draft":{"id":42,"draft":true},"version":1}""")
                    else -> MockResponse().setResponseCode(404).setBody("""{"detail":"not here"}""")
                }
            }
        }
        s.start()
        server = s
        SessionStore(app).apply { this.server = s.url("/").toString(); save("tok", "csrf", "me@example.com", "Me") }
        return graphs.graph(app)
    }

    /** ACT household.join: the setup link's invite is kept for its server only, and accepted once signed in. */
    @Test fun inviteFromSetupLink() {
        val link = "mailr://setup?server=https%3A%2F%2Fmail.example.com&invite=$token"
        assertEquals(token, parseSetupInvite(link))
        assertNull(parseSetupInvite("mailr://setup?server=https%3A%2F%2Fmail.example.com&invite=short"))
        assertNull(parseSetupInvite("mailr://setup?invite=$token"))
        assertNull(parseSetupInvite("mailr://inbox?server=https%3A%2F%2Fmail.example.com&invite=$token"))
        val pending = PendingInvite(app)
        pending.save("https://mail.example.com", token)
        assertEquals(token, pending.peek("https://mail.example.com/"))
        assertNull(pending.peek("https://other.example.com"))   // single sign-on to another server never carries it
        assertNull(pending.forServer("https://other.example.com"))
        assertNull(pending.peek("https://mail.example.com"))   // a token for another server is dropped
        val g = start()
        runBlocking { g.api.acceptInvite(token) }
        assertTrue(seen.any { it.startsWith("POST /api/v1/invites/$token/accept") })
    }

    /** An inbox with no mail account says where to finish, with the address. */
    @Test fun finishOnWeb() {
        val s = SetupState(hasAccounts = false, guide = "pending", calendar = true, url = "https://mail.example.com/setup")
        assertTrue(FinishOnWeb.needed(s))
        assertFalse(FinishOnWeb.needed(s.copy(hasAccounts = true)))
        assertFalse(FinishOnWeb.needed(null))
        var opened = 0
        compose.setContent { MaterialTheme { Surface(Modifier.fillMaxSize()) { FinishOnWebState(s) { opened++ } } } }
        compose.onNodeWithText("Finish setup on the web").assertExists()
        compose.onNodeWithText("mail.example.com/setup", substring = true).assertExists()
        compose.onNodeWithText("Open setup guide").performClick()
        assertEquals(1, opened)
    }

    /** ACT draft.outbox: the drawer lists the Outbox only while something is queued or failed; ACT draft.retry_send from it. */
    @Test fun outboxInDrawer() {
        val rows = listOf(OutboxRow(41, "failed", "Re: Q3", listOf("bob@example.com"), error = "Connection timed out", conversationId = 3066),
            OutboxRow(42, "sent", "Old"))
        assertEquals(OutboxCount(1, 1), OutboxCount.of(rows))
        assertEquals(OutboxCount(0, 0), OutboxCount.of(listOf(OutboxRow(9, "sent"))))
        assertEquals("Not sent. Couldn't reach the mail server.", outboxLine(rows[0]))
        var count by androidx.compose.runtime.mutableStateOf(OutboxCount())
        val clicks = mutableListOf<String>()
        compose.setContent {
            MaterialTheme {
                MailDrawer(Fixtures.labels, MailView(), null, emptyMap(), "me@example.com", 0, onOpen = {}, onApprovals = {}, onSettings = {},
                    outbox = count, onOutbox = { clicks += "outbox" })
            }
        }
        compose.onNodeWithTag("drawer-outbox").assertDoesNotExist()
        count = OutboxCount.of(rows)
        compose.onNodeWithTag("drawer-outbox").performClick()
        assertEquals(listOf("outbox"), clicks)
    }

    /** ACT draft.retry_send: the Outbox sheet's Retry and Open. */
    @Test fun outboxSheet() {
        val rows = listOf(OutboxRow(41, "failed", "Re: Q3", listOf("bob@example.com"), error = "Connection timed out", conversationId = 3066))
        val clicks = mutableListOf<String>()
        compose.setContent { MaterialTheme { OutboxSheet(rows, onDismiss = {}, onRetry = { clicks += "retry:${it.id}" }, onOpen = { clicks += "open:${it.conversationId}" }) } }
        compose.onNodeWithText("Retry").performClick()
        compose.onNodeWithText("Open").performClick()
        assertEquals(listOf("retry:41", "open:3066"), clicks)
    }

    /** ACT template.insert: the composer's ⋮ offers the templates; a pick fills the variables and lands at the cursor. */
    @Test fun templatesInCompose() {
        assertEquals("Hi Bob,\n\nMy office hours are Tuesday 2–4.", templateText("<p>Hi Bob,</p><p>My office hours are Tuesday 2–4.</p>"))
        assertEquals("Dear all,\nINSERTED\nThanks", insertAt("Dear all,\nThanks", 10, "INSERTED"))
        assertEquals("INSERTED", insertAt("", null, "INSERTED"))
        assertEquals("Hello\nINSERTED", insertAt("Hello", null, "INSERTED"))
        val picked = mutableListOf<Long>()
        val t = MailTemplate(3, "Office hours", "oh", "Office hours", "<p>My office hours are Tuesday 2–4.</p>")
        compose.setContent {
            MaterialTheme {
                ComposeScreen(ComposeState(loading = false, identities = listOf(Identity(7, "me@example.com", "Me", isDefault = true)), identityId = 7,
                    to = listOf(Address("bob@example.com", "Bob")), blocks = ComposeBody.ofText("")), SnackbarHostState(),
                    ComposeCallbacks({}, {}, {}, {}, {}, {}, {}, {}, { _, _ -> true }, { _, _ -> }, { _, _ -> }, { _, _ -> }, {}, {}, {},
                        templates = { listOf(t) }, insertTemplate = { picked += it.id }))
            }
        }
        compose.onNodeWithContentDescription("More").performClick()
        compose.onNodeWithText("Insert template…").performClick()
        compose.until("text \"Office hours\" shown") { compose.onAllNodes(androidx.compose.ui.test.hasText("Office hours")).fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("Office hours").performClick()
        assertEquals(listOf(3L), picked)

        // The view model expands it for the reply's recipient and puts it in the body.
        val g = start()
        val vm = graphs.vm { ComposeViewModel(g, ComposeArgs(replyToMessageId = 502, mode = "reply", body = "Sounds good."), SavedStateHandle(), "test") }
        until("reply loaded") { !vm.state.value.loading }
        assertTrue(vm.state.value.body.startsWith("Sounds good."))   // ACT mail.reply_suggestions: the chip's text starts the reply
        vm.insertTemplate(t)
        until("template inserted") { "office hours are Tuesday" in vm.state.value.body }
        val expand = seen.first { it.startsWith("POST /api/v1/templates/3/expand") }
        assertTrue(expand, "bob@example.com" in expand)
    }
}
