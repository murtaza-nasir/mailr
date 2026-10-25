// ACT-SURFACES: android.assistant
package io.github.murtazanasir.mailr.ui.assistant

import android.app.Application
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import io.github.murtazanasir.mailr.data.agent.AgentEvent
import io.github.murtazanasir.mailr.data.api.MemoryAbout
import io.github.murtazanasir.mailr.data.api.MemoryNote
import io.github.murtazanasir.mailr.data.api.MemoryNotes
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.testing.until
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * memory.used: "Used memories · 2" under an answer opens to the notes that went into the prompt. Light and
 * dark screenshots: app/screenshots/used-memories-<theme>.png. Editing and deleting: UsedMemoriesActionsTest.
 */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class UsedMemoriesTest(private val dark: Boolean) {
    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun params(): List<Array<Any>> = listOf(arrayOf<Any>(false), arrayOf<Any>(true))
    }

    @get:Rule val compose = createComposeRule()

    @Before fun setUp() {
        RuntimeEnvironment.setQualifiers("w411dp-h891dp-port-${if (dark) "night" else "notnight"}-xxhdpi")
    }

    private val notes = mutableListOf(
        MemoryNote(11, "Priya runs the reading group", "fact", MemoryAbout(2, "Priya Natarajan")),
        MemoryNote(12, "You teach on Tuesday evenings", "routine", MemoryAbout(1, "Test User", isSelf = true)),
    )
    private val saved = mutableListOf<Pair<Long, String>>()
    private val deleted = mutableListOf<Long>()
    private val actions = MemoryActions(
        load = { ids -> MemoryNotes(notes.filter { it.id in ids }, ids.count { id -> notes.none { it.id == id } }) },
        save = { id, text -> saved += id to text; notes.replaceAll { if (it.id == id) it.copy(text = text) else it } },
        delete = { id -> deleted += id; notes.removeAll { it.id == id } },
    )

    private fun show() {
        compose.setContent {
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize()) {
                    AssistantScreen(AssistantState(turns = listOf(ChatTurn("user", "When is Priya free?"),
                        ChatTurn("assistant", items = ChatTimeline.apply(emptyList(),
                            AgentEvent.Final("Priya runs the reading group on Tuesdays, when you teach. Try Wednesday.", emptyList(), 0)),
                            memoryIds = listOf(11, 12)))),
                        AssistantCallbacks({}, {}, {}, {}, {}, { _, _ -> }, { _, _, _ -> }, {}, memories = actions))
                }
            }
        }
    }

    @Test fun underTheAnswerItOpensToTheNotes() {
        show()
        compose.onNodeWithText("Used memories · 2").performScrollTo().performClick()
        compose.until("tag \"used-memory\" shown 2x") { compose.onAllNodesWithTag("used-memory").fetchSemanticsNodes().size == 2 }
        compose.onNodeWithText("You").assertExists()
        compose.onRoot().captureRoboImage("screenshots/used-memories-${if (dark) "dark" else "light"}.png")
    }
}
