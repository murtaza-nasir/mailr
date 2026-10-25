// ACT-SURFACES: android.assistant
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import io.github.murtazanasir.mailr.data.api.Citation
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.ui.assistant.AssistantCallbacks
import io.github.murtazanasir.mailr.ui.assistant.AssistantScreen
import io.github.murtazanasir.mailr.ui.assistant.AssistantState
import io.github.murtazanasir.mailr.ui.assistant.ChatTurn
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * agent.web_search: a web source the assistant cites ([[web:<url>]]) is a source card with a globe, its
 * site and title; a tap hands the citation to the app, which opens the page in the browser.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class WebCitationTest {
    @get:Rule val compose = createComposeRule()

    private val json = """{"n":1,"kind":"web","url":"https://www.kernel.org/","title":"The Linux Kernel Archives",""" +
        """"site":"kernel.org","subject":"The Linux Kernel Archives","from":"kernel.org","date":"2026-10-03",""" +
        """"provider":"Exa","quote":null,"verified":true,"conversation_id":null,"message_id":null,"start":null,"end":null}"""

    @Test fun parsesAWebCitation() {
        val c = MailrJson.decodeFromString(Citation.serializer(), json)
        assertTrue(c.isWeb)
        assertEquals("kernel.org", c.site)
        assertFalse(c.copy(url = "javascript:alert(1)").isWeb)
        assertFalse(c.copy(kind = "message").isWeb)
    }

    @Test fun webSourceCardOpensThePage() {
        val c = MailrJson.decodeFromString(Citation.serializer(), json)
        val picked = mutableListOf<Citation>()
        compose.setContent {
            MaterialTheme {
                AssistantScreen(AssistantState(turns = listOf(ChatTurn("user", "Latest kernel?"),
                    ChatTurn("assistant", "Stable is 7.2.9 [1](mailr-cite:1).", citations = listOf(c)))),
                    AssistantCallbacks(send = {}, stop = {}, newChat = {}, clearContext = {}, openCitation = { picked += it },
                        openDraft = { _, _ -> }, decide = { _, _, _ -> }, openUrl = {}))
            }
        }
        compose.onNodeWithContentDescription("Web source 1: The Linux Kernel Archives, kernel.org. Opens in browser")
            .performScrollTo().performClick()
        assertEquals("https://www.kernel.org/", picked.single().url)
    }
}
