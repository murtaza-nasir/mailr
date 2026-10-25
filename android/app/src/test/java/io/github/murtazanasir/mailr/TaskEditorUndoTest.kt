// ACT-SURFACES: android.editor
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.ExperimentalTestApi
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsFocused
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performKeyInput
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.test.performTextInputSelection
import androidx.compose.ui.test.performTextReplacement
import androidx.compose.ui.test.pressKey
import androidx.compose.ui.test.withKeyDown
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.TextRange
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.testing.until
import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.ui.calendar.Recurrence
import io.github.murtazanasir.mailr.ui.calendar.TaskEditor
import io.github.murtazanasir.mailr.ui.calendar.TaskEditorState
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.time.LocalTime

/**
 * Undo and redo in the task editor (`android.editor`), on the
 * notes editor's history model (EditHistory, whose shared vectors EditHistoryCasesTest runs). Covers
 * task.undo_edit and task.redo_edit: the header's buttons and their disabled states, typing taken back
 * and put again with the cursor at the change, the due, priority, tags, reminders, repeat and list as
 * one step each, a change made elsewhere kept, subtask steps, and Ctrl+Z on a hardware keyboard.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class TaskEditorUndoTest {
    @get:Rule val compose = createComposeRule()

    private val today = AppClock.today()
    private val task = TaskItem("t1", "Book the hotel", today.toString(), list = "L1", notes = "Near the venue")

    private fun state() = TaskEditorState(task, null, null, task.uid)

    private fun show(s: TaskEditorState, undoRedo: Boolean = true, say: (String) -> Unit = {}) {
        compose.setContent {
            MaterialTheme {
                TaskEditor(s, emptyList(), emptyList(), 9, saving = false, onClose = {}, onSave = {}, onDelete = null,
                    onAddSubtask = null, onToggleSubtask = {}, onOpenSubtask = {}, undoRedo = undoRedo, onSay = say)
            }
        }
        compose.waitForIdle()
    }

    private fun textIs(tag: String, text: String) = compose.until("$tag reads \"$text\"") {
        compose.onAllNodes(hasTestTag(tag) and SemanticsMatcher.expectValue(SemanticsProperties.EditableText, AnnotatedString(text)))
            .fetchSemanticsNodes().isNotEmpty()
    }

    private fun selectionIs(tag: String, at: Int) = compose.until("$tag has the cursor at $at") {
        compose.onAllNodes(hasTestTag(tag) and SemanticsMatcher.expectValue(SemanticsProperties.TextSelectionRange, TextRange(at)))
            .fetchSemanticsNodes().isNotEmpty()
    }

    // ---- the model -----------------------------------------------------------------------------

    @Test fun aFieldChangeIsOneStepAndUndoPutsItsSideEffectsBackToo() {
        val s = TaskEditorState(task.copy(due = today.atTime(9, 0).atZone(java.time.ZoneId.systemDefault()).toOffsetDateTime().toString(),
            reminders = listOf(15)), null, null, "t1")
        assertEquals(LocalTime.of(9, 0), s.dueTime)
        // Taking the time off clears the reminders as well; both come back in one undo.
        s.edit { setTime(null) }
        assertNull(s.dueTime)
        assertEquals(emptyList<Int>(), s.reminders)
        assertTrue(s.history.canUndo)
        runBlocking { s.history.undo() }
        assertEquals(LocalTime.of(9, 0), s.dueTime)
        assertEquals(listOf(15), s.reminders)
        assertTrue(s.history.canRedo)
        runBlocking { s.history.redo() }
        assertNull(s.dueTime)
        assertEquals(emptyList<Int>(), s.reminders)
    }

    @Test fun eachKindOfFieldIsItsOwnStep() {
        val s = state()
        s.edit { priority = 3 }
        s.edit { addTag("home") }
        s.edit { setStart(today.plusDays(1)) }
        s.edit { recurrence = Recurrence.parse("FREQ=WEEKLY") }
        s.edit { list = "L2" }
        s.edit { reminders = listOf(60) }
        assertEquals(6, s.history.history.undoSize)
        runBlocking { repeat(6) { s.history.undo() } }
        assertEquals(0, s.priority)
        assertEquals(emptyList<String>(), s.tags)
        assertNull(s.startDate)
        assertNull(s.recurrence)
        assertEquals("L1", s.list)
        assertEquals(emptyList<Int>(), s.reminders)
        assertFalse(s.dirty)
        // Nothing changed: no step.
        s.edit { priority = 0 }
        assertFalse(s.history.canUndo)
    }

    @Test fun aChangeMadeElsewhereSinceTheStepStays() {
        val s = state()
        s.edit { priority = 3; addTag("home") }
        // Live editing: someone else set the priority meanwhile.
        s.priority = 1
        runBlocking { s.history.undo() }
        assertEquals(1, s.priority)
        assertEquals(emptyList<String>(), s.tags)
    }

    @Test fun aStepThatCannotApplyIsDroppedAndSaysSo() {
        val s = state()
        s.edit { priority = 3 }
        s.priority = 1
        val said = mutableListOf<String>()
        runBlocking { s.history.undo { said += it } }
        assertEquals(1, s.priority)
        assertFalse(s.history.canUndo)
        assertFalse(s.history.canRedo)
        assertEquals(1, said.size)
    }

    @Test fun aSubtaskStepRunsItsUndoAndRedo() {
        val s = state()
        val calls = mutableListOf<String>()
        s.history.recordAction("tick", undo = { calls += "undo"; true }, redo = { calls += "redo"; true })
        runBlocking { s.history.undo(); s.history.redo() }
        assertEquals(listOf("undo", "redo"), calls)
        // A subtask that is gone by now: the step leaves the history.
        s.history.recordAction("subtask", undo = { false }, redo = { true })
        runBlocking { s.history.undo() }
        assertEquals(1, s.history.history.undoSize)
        assertFalse(s.history.canRedo)
    }

    // ---- the editor ----------------------------------------------------------------------------

    @OptIn(ExperimentalTestApi::class)
    @Test fun theHeaderHasUndoAndRedoAndTypingGoesBackAndComesAgain() {
        val s = state()
        show(s)
        // TalkBack names; nothing to undo yet.
        compose.onNodeWithContentDescription("Undo").assertIsNotEnabled()
        compose.onNodeWithContentDescription("Redo").assertIsNotEnabled()
        compose.onNodeWithTag("task-title").performTextInputSelection(TextRange(task.summary.length))
        compose.onNodeWithTag("task-title").performTextInput(" downtown")
        textIs("task-title", "Book the hotel downtown")
        // task.undo_edit: the typing goes; task.redo_edit: it comes back.
        compose.onNodeWithTag("editor-undo").assertIsEnabled().performClick()
        textIs("task-title", "Book the hotel")
        assertEquals("Book the hotel", s.title)
        compose.onNodeWithTag("editor-redo").assertIsEnabled().performClick()
        textIs("task-title", "Book the hotel downtown")
        assertEquals("Book the hotel downtown", s.title)
        compose.onNodeWithTag("editor-redo").assertIsNotEnabled()
        // The undone text is what autosaves: the editor's state has it.
        assertTrue(s.dirty)
    }

    @OptIn(ExperimentalTestApi::class)
    @Test fun undoAndRedoPutTheCursorAtTheChange() {
        val s = state()
        show(s)
        // "the " cut from the middle of the notes, then a word on the title.
        compose.onNodeWithTag("task-notes").performTextReplacement("Near venue")
        compose.onNodeWithTag("task-title").performTextReplacement("Book the hotel now")
        compose.onNodeWithTag("editor-undo").performClick()
        selectionIs("task-title", 14)
        compose.onNodeWithTag("editor-undo").performClick()
        textIs("task-notes", "Near the venue")
        selectionIs("task-notes", 9)
        compose.onNodeWithTag("task-notes").assertIsFocused()
        compose.onNodeWithTag("editor-redo").performClick()
        textIs("task-notes", "Near venue")
        selectionIs("task-notes", 5)
    }

    @OptIn(ExperimentalTestApi::class)
    @Test fun aTapOnAFieldIsOneStep() {
        val s = state()
        show(s)
        compose.onNodeWithText("High").performScrollTo().performClick()
        compose.waitForIdle()
        assertEquals(3, s.priority)
        compose.onNodeWithText("No date").performScrollTo().performClick()
        compose.waitForIdle()
        assertNull(s.dueDate)
        compose.onNodeWithTag("editor-undo").performClick()
        compose.until("s.dueDate == today") { s.dueDate == today }
        compose.onNodeWithTag("editor-undo").performClick()
        compose.until("s.priority == 0") { s.priority == 0 }
        compose.onNodeWithTag("editor-undo").assertIsNotEnabled()
    }

    @OptIn(ExperimentalTestApi::class)
    @Test fun ctrlZOnAHardwareKeyboardUsesTheEditorHistory() {
        val s = state()
        show(s)
        compose.onNodeWithTag("task-title").performTextReplacement("Book the hotel now")
        compose.onNodeWithTag("task-title").performKeyInput { withKeyDown(Key.CtrlLeft) { pressKey(Key.Z) } }
        textIs("task-title", "Book the hotel")
        compose.onNodeWithTag("task-title").performKeyInput { withKeyDown(Key.CtrlLeft) { withKeyDown(Key.ShiftLeft) { pressKey(Key.Z) } } }
        textIs("task-title", "Book the hotel now")
        compose.onNodeWithTag("task-title").performKeyInput { withKeyDown(Key.CtrlLeft) { pressKey(Key.Z) } }
        textIs("task-title", "Book the hotel")
        compose.onNodeWithTag("task-title").performKeyInput { withKeyDown(Key.CtrlLeft) { pressKey(Key.Y) } }
        textIs("task-title", "Book the hotel now")
    }

    @Test fun editorsWithoutTheHistoryShowNoButtons() {
        show(state(), undoRedo = false)
        assertTrue(compose.onAllNodes(hasTestTag("editor-undo")).fetchSemanticsNodes().isEmpty())
    }
}
