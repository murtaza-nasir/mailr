package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.ui.test.assertCountEquals
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onAllNodesWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import io.github.murtazanasir.mailr.data.api.AssistantGate
import io.github.murtazanasir.mailr.data.api.Me
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.ui.ask.AskBlockedCard
import io.github.murtazanasir.mailr.ui.assistant.AssistantCallbacks
import io.github.murtazanasir.mailr.ui.assistant.AssistantScreen
import io.github.murtazanasir.mailr.ui.assistant.AssistantState
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
 * Access policies by identity-provider group (GET /me `access`): a user in no mapped group gets no
 * model features, so the assistant is off with the server's message. Mail works as usual. An older
 * server sends no `access` and nothing turns off.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class AssistantAccessTest {
    @get:Rule val compose = createComposeRule()

    private val user = """"user":{"id":1,"email":"a@example.com"}"""
    private val off = "Your administrator hasn't given you access to Ambrose yet."

    private fun me(json: String) = MailrJson.decodeFromString(Me.serializer(), json)

    @Test fun accessParses() {
        val m = me("""{$user,"access":{"unrestricted":false,"groups":["staff"],"source":"idp","policies":["Staff"],
            "features":{"assistant":true,"choose_models":false,"lab":false,"api_tokens":false,"hosted_models":true},
            "tasks":{"chat":{"all":false,"allowed":[{"endpoint":"local","locality":"local","models":["decider-4b"]},
                {"endpoint":"hosted","locality":"hosted","models":null}],"default":{"endpoint":"local","model":"decider-4b"}},
              "classify":{"all":true,"allowed":[],"default":null}},
            "needs_endpoints":false,"message":null,"later":"ignored"}}""")
        val a = m.access
        assertFalse(a.unrestricted)
        assertEquals(listOf("staff"), a.groups)
        assertEquals("idp", a.source)
        assertEquals(listOf("Staff"), a.policies)
        assertTrue(a.features.assistant)
        assertFalse(a.features.chooseModels)
        assertFalse(a.features.lab)
        assertFalse(a.features.apiTokens)
        val chat = a.tasks.getValue("chat")
        assertFalse(chat.all)
        assertEquals(listOf("decider-4b"), chat.allowed[0].models)
        assertNull(chat.allowed[1].models)
        assertEquals("decider-4b", chat.default?.model)
        assertTrue(a.tasks.getValue("classify").all)
        assertNull(a.tasks.getValue("classify").default)
        assertNull(AssistantGate.blocked(m))
    }

    @Test fun missingAccessIsUnrestricted() {
        val m = me("""{$user}""")
        assertTrue(m.access.unrestricted)
        assertTrue(m.access.features.assistant)
        assertNull(AssistantGate.blocked(m))
    }

    @Test fun assistantOffShowsTheServerMessage() {
        val m = me("""{$user,"access":{"unrestricted":false,"groups":[],"source":"idp","policies":[],
            "features":{"assistant":false,"choose_models":false,"lab":false,"api_tokens":false,"hosted_models":false},
            "tasks":{},"needs_endpoints":false,"message":"$off"}}""")
        assertFalse(m.access.features.assistant)
        assertEquals(off, AssistantGate.blocked(m))
    }

    @Test fun gate() {
        // No message from the server: the default one.
        val bare = me("""{$user,"access":{"unrestricted":false,"features":{"assistant":false}}}""")
        assertEquals(AssistantGate.DEFAULT_MESSAGE, AssistantGate.blocked(bare))
        // Unrestricted (an admin) is never off, whatever the features say.
        val admin = me("""{$user,"access":{"unrestricted":true,"features":{"assistant":false}}}""")
        assertNull(AssistantGate.blocked(admin))
        // Before GET /me answers: the last message the app saw, if any.
        assertNull(AssistantGate.blocked(null, null))
        assertNull(AssistantGate.blocked(null, ""))
        assertEquals(off, AssistantGate.blocked(null, off))
        // A known /me wins over the cache.
        assertNull(AssistantGate.blocked(me("""{$user}"""), off))
        // A chat error that means no access.
        assertTrue(AssistantGate.isNoAccess("no_access", null))
        assertTrue(AssistantGate.isNoAccess(null, 403))
        assertFalse(AssistantGate.isNoAccess(null, 500))
        assertFalse(AssistantGate.isNoAccess(null, null))
    }

    private val noop = AssistantCallbacks({}, {}, {}, {}, {}, { _, _ -> }, { _, _, _ -> }, {})

    @Test fun assistantTabShowsTheMessageAndNoMessageBox() {
        compose.setContent { AssistantScreen(AssistantState(blocked = off), noop) }
        compose.onNodeWithText(off).assertExists()
        compose.onNodeWithText("What needs a reply from me this week?").assertDoesNotExist()
        compose.onAllNodesWithContentDescription("Send").assertCountEquals(0)
    }

    @Test fun askCaptureShowsTheMessage() {
        var closed = 0
        compose.setContent { AskBlockedCard(off) { closed++ } }
        compose.onNodeWithText(off).assertExists()
        compose.onNodeWithText("Close").performClick()
        assertEquals(1, closed)
    }
}
