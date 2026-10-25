// ACT-SURFACES: android.assistant
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.activity.OnBackPressedDispatcher
import androidx.activity.compose.BackHandler
import androidx.activity.compose.LocalOnBackPressedDispatcherOwner
import androidx.compose.material3.Text
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import io.github.murtazanasir.mailr.data.api.Citation
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.ui.BackToAssistant
import io.github.murtazanasir.mailr.ui.Destination
import io.github.murtazanasir.mailr.ui.Tab
import io.github.murtazanasir.mailr.ui.recordTarget
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Items opened from Ambrose's chat: an event or task citation opens the record in its
 * tab (recordTarget), and back from an item opened from the chat returns to the chat before the tab's own
 * back (an open conversation would otherwise close to its list).
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class AssistantLinksTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()

    @Test fun eventAndTaskCitationsOpenTheirRecord() {
        assertEquals(Destination.Event("ev-1", "2026-10-02T15:00:00Z"), recordTarget(Citation(1, kind = "event", uid = "ev-1", occurrence = "2026-10-02T15:00:00Z")))
        assertEquals(Destination.Task("t-1"), recordTarget(Citation(2, kind = "task", uid = "t-1")))
        assertNull(recordTarget(Citation(3, kind = "task", uid = null)))
        assertNull(recordTarget(Citation(4, kind = "message", conversationId = 7)))
    }

    @Test fun backFromAnItemOpenedFromTheChatReturnsToTheChat() {
        var tab by mutableStateOf(Tab.ASSISTANT)
        var fromAssistant by mutableStateOf(false)
        var tabBack = 0
        lateinit var dispatcher: OnBackPressedDispatcher
        compose.setContent {
            dispatcher = LocalOnBackPressedDispatcherOwner.current!!.onBackPressedDispatcher
            // The tab's own back, as the Mail tab's open conversation has it (composed before the shell's).
            if (tab == Tab.MAIL) BackHandler(true) { tabBack++ }
            Text(tab.name)
            BackToAssistant(fromAssistant && tab != Tab.ASSISTANT, tab) { fromAssistant = false; tab = Tab.ASSISTANT }
        }
        // A citation opens its conversation in the Mail tab.
        compose.runOnIdle { fromAssistant = true; tab = Tab.MAIL }
        compose.runOnIdle { dispatcher.onBackPressed() }
        compose.runOnIdle {
            assertEquals(Tab.ASSISTANT, tab)
            assertEquals(0, tabBack)
        }
        // Chosen by hand, the tab keeps its own back.
        compose.runOnIdle { tab = Tab.MAIL }
        compose.runOnIdle { dispatcher.onBackPressed() }
        compose.runOnIdle {
            assertEquals(Tab.MAIL, tab)
            assertEquals(1, tabBack)
        }
    }
}
