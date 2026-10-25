package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.onNodeWithText
import androidx.core.content.pm.ShortcutManagerCompat
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.ActivityItem
import io.github.murtazanasir.mailr.data.api.Approval
import io.github.murtazanasir.mailr.data.api.AssistantGate
import io.github.murtazanasir.mailr.data.live.LiveWords
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.i18n.AssistantName
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.ui.AppNavigation
import io.github.murtazanasir.mailr.ui.Tab
import io.github.murtazanasir.mailr.ui.approvals.groupSource
import io.github.murtazanasir.mailr.ui.ask.AssistantSurfaces
import io.github.murtazanasir.mailr.ui.present.Msg
import io.github.murtazanasir.mailr.ui.present.ServerText
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The user's name for the assistant (Settings → Ambrose → About you on the web) everywhere the phone
 * names it: strings, the bottom tab, Activity, approvals, live edits, server texts, the launcher
 * shortcuts and the tile. "Ambrose" until the profile names it otherwise.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class AssistantNameTest {
    @get:Rule val compose = createComposeRule()

    @Before fun setUp() = AssistantName.resetForTest()
    @After fun tearDown() = AssistantName.resetForTest()

    private fun profile(name: String?) = JsonObject(mapOf("options" to JsonObject(
        if (name == null) emptyMap() else mapOf("assistant_name" to JsonPrimitive(name)))))

    @Test fun theDefaultIsAmbroseUntilTheProfileNamesIt() {
        assertEquals("Ambrose", AssistantName.now())
        assertEquals("Ambrose", AssistantGate.DEFAULT_NAME)
        assertEquals("Ask Ambrose", AppText.get(R.string.rem_action_ask, AssistantName.now()))
        AssistantName.set(AssistantName.fromProfile(profile("  Jarvis ")))
        assertEquals("Jarvis", AssistantName.now())
        // A profile without a name, or a sign-out, goes back to the default.
        AssistantName.set(AssistantName.fromProfile(profile(null)))
        assertEquals("Ambrose", AssistantName.now())
        AssistantName.set("Jarvis")
        AssistantName.forget()
        assertEquals("Ambrose", AssistantName.now())
    }

    @Test fun theNameIsKeptForTheNextStart() {
        AssistantName.set("Jarvis")
        // A new process (widget, tile, worker) reads the saved name before the app asks the server.
        val prefs = ApplicationProvider.getApplicationContext<Application>().getSharedPreferences("mailr_assistant", 0)
        assertEquals("Jarvis", prefs.getString("name", null))
    }

    @Test fun stringsResolveWithTheName() {
        AssistantName.set("Jarvis")
        val n = AssistantName.now()
        assertEquals("Ask Jarvis", AppText.get(R.string.rem_action_ask, n))
        assertEquals("Talk to Jarvis", AppText.get(R.string.today_talk, n))
        assertEquals("Ask Jarvis about this conversation", AppText.get(R.string.conv_ask_ambrose_about, n))
        assertEquals("Draft with Jarvis", AppText.get(R.string.rec_draft_assistant, n))
        assertEquals("Sends to Jarvis after a short pause", AppText.get(R.string.settings_hands_free_sub, n))
        assertEquals("Talk to Jarvis. Tue 6 Oct", AppText.get(R.string.agenda_talk_cd, n, "Tue", "6", "Oct"))
        assertEquals("Your administrator hasn't given you access to Jarvis yet.", AssistantGate.DEFAULT_MESSAGE)
        assertEquals("Jarvis is off", AppText.get(R.string.asst_off, n))
        assertEquals("Jarvis is unavailable (503).", AppText.get(R.string.asst_unavailable_code, n, 503))
    }

    @Test fun activityApprovalsAndLiveEditsNameIt() {
        AssistantName.set("Jarvis")
        assertEquals("Jarvis", ActivityItem(who = "ambrose").actor)
        assertEquals("Jarvis", groupSource(Approval(1, subject = "assistant")))
        assertEquals("Updated by Jarvis", LiveWords.updated("assistant"))
        assertEquals("Jarvis changed this draft", LiveWords.changed("assistant", "draft"))
        assertEquals("Use Jarvis's", LiveWords.useTheirs("assistant"))
    }

    /** A forwarded note's card: the server sends the name; an older server does not, and the phone fills it. */
    @Test fun theForwardSummaryNamesIt() {
        AssistantName.set("Jarvis")
        val older = ServerText.text(Msg("approvals.forward.summary"), "fallback")
        assertTrue(older, older.contains("Jarvis carries out the request"))
        val sent = ServerText.text(Msg("approvals.forward.summary", JsonObject(mapOf("name" to JsonPrimitive("Hal")))), "fallback")
        assertTrue(sent, sent.contains("Hal carries out the request"))
    }

    @Test fun theTabCarriesTheName() {
        AssistantName.set("Jarvis")
        compose.setContent { MaterialTheme { AppNavigation(Tab.MAIL, {}, 0) { Text("content") } } }
        compose.onNodeWithText("Jarvis").assertIsDisplayed()
        compose.onNodeWithText("Mail").assertIsDisplayed()
    }

    @Test fun theTileAndTheShortcutsCarryTheName() {
        val ctx = ApplicationProvider.getApplicationContext<Application>()
        assertEquals("Talk to Ambrose", AssistantSurfaces.talkLabel())
        AssistantName.set("Jarvis")
        assertEquals("Talk to Jarvis", AssistantSurfaces.talkLabel())
        assertEquals(AssistantSurfaces.ShortcutLabels("Ask Jarvis", "Ask Jarvis"), AssistantSurfaces.askLabels())
        assertEquals(AssistantSurfaces.ShortcutLabels("Dictate", "Dictate to Jarvis"), AssistantSurfaces.dictateLabels())

        AssistantSurfaces.publishShortcuts(ctx)
        val published = ShortcutManagerCompat.getDynamicShortcuts(ctx).associateBy { it.id }
        assertEquals("Ask Jarvis", published.getValue(AssistantSurfaces.ASK_ID).shortLabel.toString())
        assertEquals("Dictate to Jarvis", published.getValue(AssistantSurfaces.DICTATE_ID).longLabel.toString())
        // A rename republishes them under the same ids.
        AssistantName.set("Hal")
        AssistantSurfaces.publishShortcuts(ctx)
        val again = ShortcutManagerCompat.getDynamicShortcuts(ctx).filter { it.id in setOf(AssistantSurfaces.ASK_ID, AssistantSurfaces.DICTATE_ID) }
        assertEquals(setOf("Ask Hal", "Dictate"), again.map { it.shortLabel.toString() }.toSet())
    }
}
