// ACT-SURFACES: android.notes
package io.github.murtazanasir.mailr.notes

import android.app.Application
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.ExperimentalTestApi
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsFocused
import androidx.compose.ui.test.performTextInputSelection
import androidx.compose.ui.test.performTextReplacement
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performKeyInput
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.test.pressKey
import androidx.compose.ui.test.withKeyDown
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.TestGraph
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.notes.ui.NotesPage
import io.github.murtazanasir.mailr.notes.ui.NotesRoute
import io.github.murtazanasir.mailr.notes.ui.NotesViewModel
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.testing.serverSees
import io.github.murtazanasir.mailr.testing.waitForNode
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Undo and redo in the note editor (`android.notes`): the top bar's buttons
 * and their disabled states, typing taken back and put again, a tick and a deleted item undone while
 * their writes still wait in the queue and once the server has them, the saved result, and Ctrl+Z on a
 * hardware keyboard. Covers note.undo_edit and note.redo_edit.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class NotesUndoTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()
    private lateinit var fake: FakeNotesServer
    private lateinit var g: AppGraph
    private lateinit var vm: NotesViewModel

    @Before fun setUp() {
        fake = graphs.closeLast(object : AutoCloseable {
            val f = FakeNotesServer().start()
            override fun close() = f.close()
        }).f
        val ctx = ApplicationProvider.getApplicationContext<Application>()
        SessionStore(ctx).apply {
            server = fake.server.url("/").toString().trimEnd('/')
            save("tok-1", "csrf-1", "me@example.com", "Me")
        }
        g = graphs.graph(ctx)
        vm = graphs.vm { NotesViewModel(g.notes.repo) }
    }

    private fun show() {
        compose.setContent { MailrTheme(ThemeMode.LIGHT, dynamic = false) { NotesRoute(vm) } }
        compose.waitForIdle()
    }

    private fun drain() = runBlocking { g.notes.repo.drain() }

    private fun serverSees(what: String, check: () -> Boolean) = compose.serverSees(g.notes.repo, what, check)

    private fun bodyIs(text: String) = compose.eventually("the body reads \"$text\"") {
        compose.onAllNodes(hasTestTag("note-body") and SemanticsMatcher.expectValue(SemanticsProperties.EditableText,
            androidx.compose.ui.text.AnnotatedString(text))).fetchSemanticsNodes().isNotEmpty()
    }

    @Test fun typingUndoesAndRedoesAndTheResultSaves() {
        val n = fake.note("text", "Plan")
        runBlocking { g.notes.repo.pull() }
        show()
        compose.runOnIdle { vm.open(NotesPage.OpenNote(n.id)) }
        compose.waitForNode(hasTestTag("note-body"))
        // The buttons are there for TalkBack by name, and off with nothing to undo.
        compose.onNodeWithContentDescription("Undo").assertIsNotEnabled()
        compose.onNodeWithContentDescription("Redo").assertIsNotEnabled()

        compose.onNodeWithTag("note-body").performTextInput("buy paint")
        compose.onNodeWithTag("editor-undo").assertIsEnabled()
        // note.undo_edit: the typing goes; note.redo_edit: it comes back.
        compose.onNodeWithTag("editor-undo").performClick()
        bodyIs("")
        compose.onNodeWithTag("editor-redo").assertIsEnabled().performClick()
        bodyIs("buy paint")
        compose.onNodeWithTag("editor-redo").assertIsNotEnabled()
        // An undo is an edit: it autosaves, and the server keeps the result.
        compose.mainClock.advanceTimeBy(2_000)
        serverSees("the redone text") { fake.notes[n.id]!!.body == "buy paint" }
        compose.onNodeWithTag("editor-undo").performClick()
        bodyIs("")
        compose.mainClock.advanceTimeBy(2_000)
        serverSees("the undone text") { fake.notes[n.id]!!.body == "" }
    }

    /**
     * An edit made while an autosave is still finishing is saved too. The undo runs in the moment after
     * the save's write and before the save returns. Before, the save then marked the editor clean, and
     * the undone text never reached the server.
     */
    @Test fun anEditWhileTheAutosaveFinishesIsSavedToo() {
        val n = fake.note("text", "Plan")
        runBlocking { g.notes.repo.pull() }
        show()
        compose.runOnIdle { vm.open(NotesPage.OpenNote(n.id)) }
        compose.waitForNode(hasTestTag("note-body"))
        val before = vm.repo.onQueued
        var undone = false
        vm.repo.onQueued = {
            before?.invoke()
            if (!undone) {
                undone = true
                vm.undoEdit(n.id)
            }
        }
        compose.onNodeWithTag("note-body").performTextInput("buy paint")
        compose.mainClock.advanceTimeBy(2_000)
        serverSees("the typed text") { fake.notes[n.id]!!.body == "buy paint" }
        compose.waitForIdle()  // the save's return, which runs the hook
        assertTrue(undone)
        bodyIs("")
        compose.mainClock.advanceTimeBy(2_000)
        serverSees("the undone text") { fake.notes[n.id]!!.body == "" }
    }

    private fun selectionIs(tag: String, at: Int) = compose.eventually("the cursor of $tag at $at") {
        compose.onAllNodes(hasTestTag(tag) and SemanticsMatcher.expectValue(SemanticsProperties.TextSelectionRange, TextRange(at)))
            .fetchSemanticsNodes().isNotEmpty()
    }

    /** Undo and Redo put the cursor where the change was (the end of what came back or went in), in that field. */
    @OptIn(ExperimentalTestApi::class)
    @Test fun undoAndRedoPutTheCursorAtTheChange() {
        val n = fake.note("text", "Plan").apply { body = "alpha beta gamma" }
        runBlocking { g.notes.repo.pull() }
        show()
        compose.runOnIdle { vm.open(NotesPage.OpenNote(n.id)) }
        bodyIs("alpha beta gamma")
        // "beta " cut from the middle, then the cursor goes to the end and the title gets a word.
        compose.onNodeWithTag("note-body").performTextReplacement("alpha gamma")
        compose.onNodeWithTag("note-body").performTextInputSelection(TextRange(11))
        compose.onNodeWithTag("note-title").performTextReplacement("Plan B")
        // Undo: the title first, its cursor after "Plan".
        compose.onNodeWithTag("editor-undo").performClick()
        selectionIs("note-title", 4)
        // Undo again: "beta " is back, the body has the focus and the cursor stands after it.
        compose.onNodeWithTag("editor-undo").performClick()
        bodyIs("alpha beta gamma")
        selectionIs("note-body", 11)
        compose.onNodeWithTag("note-body").assertIsFocused()
        // Redo cuts it again: the cursor where it was cut.
        compose.onNodeWithTag("editor-redo").performClick()
        bodyIs("alpha gamma")
        selectionIs("note-body", 6)
    }

    @OptIn(ExperimentalTestApi::class)
    @Test fun ctrlZOnAHardwareKeyboardUsesTheEditorHistory() {
        val n = fake.note("text", "Plan")
        runBlocking { g.notes.repo.pull() }
        show()
        compose.runOnIdle { vm.open(NotesPage.OpenNote(n.id)) }
        compose.waitForNode(hasTestTag("note-body"))
        compose.onNodeWithTag("note-body").performTextInput("fix shelf")
        compose.onNodeWithTag("note-body").performKeyInput { withKeyDown(Key.CtrlLeft) { pressKey(Key.Z) } }
        bodyIs("")
        compose.onNodeWithTag("note-body").performKeyInput { withKeyDown(Key.CtrlLeft) { pressKey(Key.Y) } }
        bodyIs("fix shelf")
    }

    @Test fun aTickAndADeletedItemUndoFromTheTopBarAndRedo() {
        val shop = fake.note("checklist", "Shopping", listOf("milk", "eggs"))
        runBlocking { g.notes.repo.pull() }
        show()
        compose.runOnIdle { vm.open(NotesPage.OpenNote(shop.id)) }
        compose.waitForNode(hasText("eggs"))
        val box = { t: String -> compose.onNode(hasContentDescription(t) and SemanticsMatcher.keyIsDefined(SemanticsProperties.ToggleableState)) }

        // A tick, undone while its write still waits in the queue: it never reaches the server.
        box("milk").performClick()
        compose.waitForNode(hasText("1 checked"))
        compose.onNodeWithTag("editor-undo").performClick()
        compose.eventually("milk unchecked") { compose.onAllNodes(hasText("1 checked")).fetchSemanticsNodes().isEmpty() }
        drain()
        assertTrue(fake.items[shop.id]!!.none { it.checked })
        // Redo ticks it again, and this time the server has it.
        compose.onNodeWithTag("editor-redo").performClick()
        serverSees("milk checked") { fake.items[shop.id]!!.first { it.text == "milk" }.checked }

        // An item deleted from its menu, after the server has the delete: Undo queues the server's own undo.
        compose.onNodeWithContentDescription("Options for eggs").performClick()
        compose.onNodeWithText("Delete").performClick()
        serverSees("eggs deleted") { fake.items[shop.id]!!.none { it.text == "eggs" } }
        compose.onNodeWithTag("editor-undo").performClick()
        compose.waitForNode(hasText("eggs"))
        serverSees("the server's undo of the delete") { fake.log.any { it.method == "POST" && it.path.endsWith("/revert") } }
    }
}
