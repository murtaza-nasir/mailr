// ACT-SURFACES: android.screener
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHostState
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.ScreenerList
import io.github.murtazanasir.mailr.data.api.ScreenerSender
import io.github.murtazanasir.mailr.ui.mail.ScreenerScreen
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The Screener on Android (ACT screener.review, screener.allow, screener.block): GET /screener as the
 * server sends it, the card's words, Allow and Block on a card, and the empty states.
 */
private const val LIST = """{"enabled":true,"count":2,"senders":[
 {"id":7,"key":"kitestudio.example","kind":"domain","email":"hana@kitestudio.example","name":"Hana Ito",
  "addresses":["hana@kitestudio.example"],"messages":2,"conversations":1,"unread":2,"first_seen":"2026-10-01T09:00:00+00:00",
  "latest":{"message_id":4503599627370501,"conversation_id":4503599627370497,"subject":"A collaboration?","snippet":"Would you have twenty minutes?","date":"2026-10-01T10:00:00+00:00"},
  "auth":{"level":"verified","text":"Verified: sent by kitestudio.example"},"newsletter":false,"account":"Personal","local_only":false},
 {"id":8,"key":"leo@gmail.com","kind":"address","email":"leo@gmail.com","name":"","messages":1,"unread":0,
  "latest":{"conversation_id":4503599627370498,"subject":"","snippet":"Hi"},"auth":{"level":"failed","text":"Failed"},"new_field":1}]}"""

@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class ScreenerTest {
    @get:Rule val compose = createComposeRule()

    private fun parse() = MailrJson.decodeFromString(ScreenerList.serializer(), LIST)

    @Test fun parsesTheListAndSaysWhatTheCardShows() {
        val l = parse()
        assertEquals(2, l.senders.size)
        val (hana, leo) = l.senders
        assertEquals("Hana Ito", hana.title)
        assertEquals("Everyone at kitestudio.example", hana.scope)
        assertEquals("Verified", hana.authLabel)
        assertEquals("2 messages", hana.held)
        assertEquals(4503599627370497, hana.latest.conversationId)
        assertEquals("leo@gmail.com", leo.title)
        assertEquals("This address only", leo.scope)
        assertEquals("Failed authentication", leo.authLabel)
        assertEquals("1 message", leo.held)
    }

    @Test fun allowAndBlockOnACard() {
        val decided = mutableListOf<Pair<Long, String>>()
        val opened = mutableListOf<Long>()
        compose.setContent {
            MaterialTheme {
                ScreenerScreen(parse(), null, false, SnackbarHostState(), onBack = {}, onRefresh = {},
                    onDecide = { s: ScreenerSender, d: String -> decided += s.id to d },
                    onOpen = { opened += it.id })
            }
        }
        compose.onNodeWithText("Hana Ito").assertExists()
        compose.onNodeWithText("Everyone at kitestudio.example · Verified · 2 messages").assertExists()
        compose.onNodeWithText("A collaboration?").performClick()
        compose.onAllNodesWithText("Allow")[0].performClick()
        compose.onAllNodesWithText("Block")[1].performClick()
        assertEquals(listOf(7L to "allow", 8L to "block"), decided)
        assertEquals(listOf(7L), opened)
    }

    @Test fun emptyStates() {
        compose.setContent {
            MaterialTheme {
                ScreenerScreen(ScreenerList(enabled = false), null, false, SnackbarHostState(), {}, {}, { _, _ -> }, {})
            }
        }
        compose.onNodeWithText("The Screener is off").assertExists()
    }
}
