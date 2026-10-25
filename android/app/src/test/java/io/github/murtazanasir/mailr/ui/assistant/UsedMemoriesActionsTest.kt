// ACT-SURFACES: android.assistant
package io.github.murtazanasir.mailr.ui.assistant

import android.app.Application
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextReplacement
import io.github.murtazanasir.mailr.data.api.MemoryAbout
import io.github.murtazanasir.mailr.data.api.MemoryNote
import io.github.murtazanasir.mailr.data.api.MemoryNotes
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.testing.until
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** memory.used: a tap on a used memory edits it in place; the bin deletes it, and the line says so. */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class UsedMemoriesActionsTest {
    @get:Rule val compose = createComposeRule()

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

    /** The line on its own: the chat's follow-scroll plays no part in editing and deleting. */
    private fun showLine() {
        compose.setContent {
            MaterialTheme { Surface(Modifier.fillMaxSize()) { UsedMemories(listOf(11, 12), actions) } }
        }
        compose.onNodeWithText("Used memories · 2").performClick()
        compose.until("tag \"used-memory\" shown 2x") { compose.onAllNodesWithTag("used-memory").fetchSemanticsNodes().size == 2 }
    }

    @Test fun aTapOpensANoteToEdit() {
        showLine()
        compose.onNodeWithText("Priya runs the reading group").performClick()
        compose.onNodeWithTag("memory-text").performTextReplacement("Priya runs the Thursday reading group")
        compose.onNodeWithText("Save").performClick()
        compose.until("saved.isNotEmpty()") { saved.isNotEmpty() }
        assertEquals(11L to "Priya runs the Thursday reading group", saved.single())
        compose.until("\"Priya runs the Thursday reading group\" shown") { compose.onAllNodesWithText("Priya runs the Thursday reading group").fetchSemanticsNodes().isNotEmpty() }
    }

    @Test fun theBinDeletesOneAndSaysSo() {
        showLine()
        compose.onAllNodes(hasContentDescription("Delete"))[0].performClick()
        compose.until("deleted.isNotEmpty()") { deleted.isNotEmpty() }
        compose.until("\"1 memory was deleted.\" shown") { compose.onAllNodesWithText("1 memory was deleted.").fetchSemanticsNodes().isNotEmpty() }
        compose.onAllNodesWithTag("used-memory").assertCountEquals(1)
    }
}
