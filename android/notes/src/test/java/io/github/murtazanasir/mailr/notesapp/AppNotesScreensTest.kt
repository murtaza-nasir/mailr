// ACT-SURFACES: android.notesapp
package io.github.murtazanasir.mailr.notesapp

import android.app.Application
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assertHeightIsAtLeast
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.isDialog
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.longClick
import androidx.compose.ui.test.onAllNodesWithContentDescription
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onFirst
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performImeAction
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performSemanticsAction
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.test.performTextReplacement
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.printToString
import androidx.compose.ui.unit.dp
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.notes.FakeNotesServer
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.notesapp.data.NotesPrefs
import io.github.murtazanasir.mailr.notesapp.data.ThemeChoice
import io.github.murtazanasir.mailr.notes.ui.NotesPage
import io.github.murtazanasir.mailr.notes.ui.NotesRoute
import io.github.murtazanasir.mailr.notes.ui.NotesView
import io.github.murtazanasir.mailr.notes.ui.NotesViewModel
import io.github.murtazanasir.mailr.notesapp.ui.NotesTheme
import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.testing.serverSees
import io.github.murtazanasir.mailr.testing.until
import io.github.murtazanasir.mailr.testing.waitForNode
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The same screens in the Notes app (`android.notesapp`), on its own graph and database, against the
 * fake server: the grid and its card
 * menu, the drawer's views, the checklist editor (note.add_items and checkitem.add through the add line,
 * checkitem.check with one tap, the checked fold, note.clear_checked, checksection.create, the TalkBack
 * actions), the text editor's autosave (note.edit), note.to_checklist, the labels menu (note.label,
 * notelabel.create), the people sheet (note.share, note.unshare), version history (note.history,
 * note.restore_version), Trash (note.delete, note.restore), the offline banner, and a table's rows
 * and row form (row.add, row.edit, row.history, row.restore_version, table.stats).
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class AppNotesScreensTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = NotesTestGraph()
    @get:Rule val compose = createComposeRule()
    private lateinit var fake: FakeNotesServer
    private lateinit var g: NotesGraph
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
        compose.setContent { NotesTheme(NotesPrefs.Look(ThemeChoice.LIGHT, false)) { NotesRoute(vm) } }
        compose.waitForIdle()
    }

    private fun drain() = runBlocking { g.notes.repo.drain() }

    private fun waitText(t: String) = compose.waitForNode(hasText(t))
    private fun waitTag(t: String) = compose.waitForNode(hasTestTag(t))
    private fun waitDesc(t: String) = compose.waitForNode(hasContentDescription(t))

    /** Send the queue until the fake server shows [check] (an action's write is queued from the ViewModel's scope). */
    private fun serverSees(what: String, check: () -> Boolean) = compose.serverSees(g.notes.repo, what, check)

    private fun open(page: NotesPage) {
        compose.runOnIdle { vm.open(page) }
    }

    @Test fun gridShowsPinnedAndOthersAndTheCardMenuPins() {
        val shop = fake.note("checklist", "Shopping", listOf("milk", "eggs"))
        fake.note("text", "Ideas").also { it.body = "first line"; it.pinned = true }
        runBlocking { g.notes.repo.pull() }
        show()
        waitText("PINNED")
        compose.onNodeWithText("OTHERS").assertIsDisplayed()
        compose.onNodeWithText("Shopping").assertIsDisplayed()
        // ACT note.pin from the card's menu (long-press).
        compose.onNodeWithTag("card-${shop.id}").performTouchInput { longClick() }
        compose.onNodeWithText("Pin").performClick()
        serverSees("note.pin") { fake.notes[shop.id]!!.pinned }
        // checkitem.check on a card: the box checks in place.
        waitDesc("Check milk")
        compose.onNodeWithContentDescription("Check milk").performSemanticsAction(SemanticsActions.OnClick)
        serverSees("checkitem.check") { fake.items[shop.id]!!.first { it.text == "milk" }.checked }
    }

    @Test fun theDrawerSwitchesViewsAndTrashRestores() {
        val n = fake.note("text", "Old plan")
        runBlocking { g.notes.repo.pull() }
        show()
        waitTag("card-${n.id}")
        // note.delete from the card menu, then Trash, then note.restore.
        compose.onNodeWithTag("card-${n.id}").performTouchInput { longClick() }
        compose.onNodeWithText("Delete").performClick()
        serverSees("note.delete") { fake.notes[n.id]!!.deleted != null }
        compose.onNodeWithContentDescription("Notes menu").performClick()
        compose.onNodeWithText("Trash").performClick()
        compose.waitForIdle()
        assertEquals(NotesView.Trash, vm.view.value)
        waitText("Old plan")
        compose.onAllNodesWithText("Deleted today · 30 days left", substring = true).onFirst().assertIsDisplayed()
        compose.onNodeWithTag("card-${n.id}").performTouchInput { longClick() }
        compose.onNodeWithText("Restore").performClick()
        serverSees("note.restore") { fake.notes[n.id]!!.deleted == null }
    }

    @Test fun theChecklistEditorAddsChecksAndFoldsItems() {
        val shop = fake.note("checklist", "Shopping", listOf("milk"))
        runBlocking { g.notes.repo.pull() }
        show()
        open(NotesPage.OpenNote(shop.id))
        waitTag("add-top")
        // note.add_items: one line, several items, through the grammar.
        compose.onNodeWithTag("add-top").performTextInput("2 cans of tomatoes, bread")
        compose.onNodeWithTag("add-top").performImeAction()
        waitText("tomatoes")
        compose.onNodeWithText("2 can").assertIsDisplayed()
        // checkitem.check: one tap on the box; the row moves into the fold.
        compose.onNode(hasContentDescription("bread") and SemanticsMatcher.keyIsDefined(SemanticsProperties.ToggleableState))
            .performClick()
        waitText("1 checked")
        serverSees("note.add_items and checkitem.check") {
            fake.items[shop.id]!!.map { it.text }.toSet() == setOf("milk", "tomatoes", "bread") &&
                fake.items[shop.id]!!.first { it.text == "bread" }.checked
        }
        // note.clear_checked from the fold header.
        compose.onNodeWithText("Clear checked").performClick()
        serverSees("note.clear_checked") { fake.items[shop.id]!!.none { it.text == "bread" } }
    }

    @Test fun itemRowsHaveTalkBackActionsAndTargetsOf48dp() {
        val shop = fake.note("checklist", "Shopping", listOf("milk", "eggs"))
        runBlocking { g.notes.repo.pull() }
        show()
        open(NotesPage.OpenNote(shop.id))
        val eggs = fake.items[shop.id]!!.first { it.text == "eggs" }
        waitTag("item-${eggs.id}")
        val node = compose.onNodeWithTag("item-${eggs.id}")
        val actions = node.fetchSemanticsNode().config[SemanticsActions.CustomActions].map { it.label }
        assertEquals(listOf("Check", "Move up", "Move down", "Move to section", "Delete", "Edit quantity"), actions)
        compose.onNode(hasContentDescription("eggs") and SemanticsMatcher.keyIsDefined(SemanticsProperties.ToggleableState))
            .assertHeightIsAtLeast(48.dp)
        // The swipe's labels are not read while the row is still.
        assertTrue(compose.onAllNodesWithText("Delete").fetchSemanticsNodes().isEmpty())
        // checkitem.move through TalkBack's Move up.
        compose.runOnIdle {
            node.fetchSemanticsNode().config[SemanticsActions.CustomActions].first { it.label == "Move up" }.action()
        }
        serverSees("checkitem.move") { fake.log.any { it.path == "notes/${shop.id}/items/${eggs.id}/move" } }
    }

    @Test fun aTextNoteAutosavesAndBecomesAList() {
        val n = fake.note("text", "Plan")
        runBlocking { g.notes.repo.pull() }
        show()
        open(NotesPage.OpenNote(n.id))
        waitTag("note-body")
        // note.edit: the body saves 1.5 s after the last change.
        compose.onNodeWithTag("note-body").performTextInput("buy paint\nfix shelf")
        compose.mainClock.advanceTimeBy(2_000)
        serverSees("note.edit") { fake.notes[n.id]!!.body == "buy paint\nfix shelf" }
        // note.to_checklist from ⋮.
        compose.onNodeWithContentDescription("More options").performClick()
        compose.onNodeWithText("Make it a list").performClick()
        waitText("fix shelf")
        serverSees("note.to_checklist") { fake.notes[n.id]!!.kind == "checklist" }
    }

    @Test fun labelsMenuSetsALabel() {
        val n = fake.note("text", "Plan")
        fake.labels += listOf("Garden", "Home")
        runBlocking { g.notes.repo.pull() }
        show()
        open(NotesPage.OpenNote(n.id))
        waitDesc("Labels")
        // note.label: the labels menu with a box per label.
        compose.onNodeWithContentDescription("Labels").performClick()
        waitText("Garden")
        compose.onNodeWithText("Garden").performClick()
        compose.onNodeWithText("Done").performClick()
        serverSees("note.label") { fake.notes[n.id]!!.labels == listOf("Garden") }
        // notelabel.create: the menu's "New label" makes it through the same call (a text field in a
        // dialog never goes idle under Robolectric, so the field itself is not typed into here).
        compose.runOnIdle { vm.act { g.notes.repo.createLabel("Shed") } }
        serverSees("notelabel.create") { "Shed" in fake.labels }
    }

    @Test fun thePeopleSheetSharesChangesAndStops() {
        val n = fake.note("checklist", "Groceries")
        runBlocking { g.notes.repo.pull() }
        show()
        open(NotesPage.OpenNote(n.id))
        waitDesc("Share")
        compose.onNodeWithContentDescription("Share").performSemanticsAction(androidx.compose.ui.semantics.SemanticsActions.OnClick)
        waitText("Add people or groups")
        // note.share: add Sam at View.
        compose.onNodeWithText("Add people or groups").performClick()
        compose.onNodeWithText("Edit").performClick()
        compose.onAllNodesWithText("View").onFirst().performClick()
        compose.onNodeWithText("Sam Example · sam@example.com").performClick()
        compose.eventually("note.share") { fake.shares[n.id]?.isNotEmpty() == true }
        assertEquals("view", fake.shares[n.id]!!.single()["level"]!!.jsonPrimitive.content)
        // note.unshare: stop sharing.
        waitDesc("Stop sharing with Sam Example")
        compose.onNodeWithContentDescription("Stop sharing with Sam Example").performClick()
        compose.eventually("note.unshare") { fake.shares[n.id]?.isEmpty() == true }
        assertTrue(fake.log.any { it.method == "DELETE" && it.path == "notes/${n.id}/shares/7" })
    }

    @Test fun versionHistoryRestoresAVersion() {
        val n = fake.note("text", "New title")
        runBlocking { g.notes.repo.pull() }
        show()
        open(NotesPage.OpenNote(n.id))
        waitDesc("More options")
        compose.onNodeWithContentDescription("More options").performClick()
        compose.onNodeWithText("Version history").performClick()
        // note.history: the list, then one version read-only; note.restore_version.
        compose.waitForNode(hasText("Dana Example", substring = true))
        compose.onNodeWithTag("revision-1").performClick()
        waitText("old body")
        compose.onNodeWithTag("restore-version").performClick()
        compose.eventually("note.restore_version") { fake.notes[n.id]!!.title == "Old title" }
        assertTrue(fake.log.any { it.path == "notes/${n.id}/revisions/1/restore" })
    }

    @Test fun theOfflineBannerCountsWaitingChanges() {
        val shop = fake.note("checklist", "Shopping", listOf("milk"))
        runBlocking { g.notes.repo.pull() }
        fake.offline = true
        show()
        waitDesc("Check milk")
        compose.onNodeWithContentDescription("Check milk").performSemanticsAction(SemanticsActions.OnClick)
        compose.eventually("the check is queued") { runBlocking { g.notes.repo.ops.first().isNotEmpty() } }
        drain()
        try { waitText("Offline · 1 change waiting") } catch (e: Throwable) {
            throw AssertionError("offline=${g.notes.repo.offline.value} grid=${vm.grid.value.offline}/${vm.grid.value.pending}\n" +
                compose.onAllNodes(androidx.compose.ui.test.isRoot()).printToString(30), e)
        }
        fake.offline = false
        serverSees("the queued check") { fake.items[shop.id]!!.single().checked }
        try {
            compose.until("\"Offline\" gone") { compose.onAllNodesWithText("Offline", substring = true).fetchSemanticsNodes().isEmpty() }
        } catch (e: Throwable) {
            throw AssertionError("offline=${g.notes.repo.offline.value} grid=${vm.grid.value.offline}/${vm.grid.value.pending}\n" +
                compose.onAllNodes(androidx.compose.ui.test.isRoot()).printToString(30), e)
        }
    }

    @Test fun aTableListsRowsWithFootersAndTheRowFormSaves() {
        val t = fake.table("Expenses", listOf("Item" to "text", "Amount" to "number"),
            listOf(mapOf("c0" to "Rent", "c1" to "900"), mapOf("c0" to "Pens", "c1" to "4")))
        runBlocking { g.notes.repo.pull() }
        show()
        open(NotesPage.OpenTable(t.id))
        waitText("Rent")
        compose.onNodeWithText("Amount: 900").assertIsDisplayed()
        // table.stats: the footer bar.
        waitText("Amount sum: 904.0")
        // row.edit in the row form: a field saves when it is done.
        compose.onNodeWithText("Rent").performClick()
        waitTag("field-Amount")
        compose.onNodeWithTag("field-Amount").performTextReplacement("950")
        compose.onNodeWithTag("field-Amount").performImeAction()
        val rent = fake.rows.values.first { it.cells["c0"]!!.jsonPrimitive.content == "Rent" }
        serverSees("row.edit") { rent.cells["c1"]!!.jsonPrimitive.content == "950" }
        // row.history and row.restore_version from the History tab.
        compose.onNodeWithText("History").performClick()
        waitText("Restore row to this point")
        compose.onNodeWithText("Restore row to this point").performClick()
        compose.eventually("row.restore_version") { rent.cells["c1"]!!.jsonPrimitive.content == "80" }
        // row.add: + opens an empty form; Save sends it with the phone's id.
        compose.runOnIdle { vm.back() }
        waitDesc("New row")
        compose.onNodeWithContentDescription("New row").performClick()
        waitTag("field-Item")
        compose.onNodeWithTag("field-Item").performTextInput("Ink")
        compose.onNodeWithTag("row-save").performClick()
        serverSees("row.add") { fake.rows.values.any { it.cells["c0"]?.jsonPrimitive?.content == "Ink" } }
    }

    @Test fun aHeaderLineMakesASection() {
        val shop = fake.note("checklist", "Shopping", listOf("milk"))
        runBlocking { g.notes.repo.pull() }
        show()
        open(NotesPage.OpenNote(shop.id))
        waitTag("add-top")
        // checksection.create: a line that is only "Name:" makes the section, and the items under it go in.
        compose.onNodeWithTag("add-top").performTextInput("Costco:")
        compose.onNodeWithTag("add-top").performImeAction()
        waitText("COSTCO")
        serverSees("checksection.create") { fake.sections[shop.id]!!.map { it.name } == listOf("Costco") }
        val sid = fake.sections[shop.id]!!.single().id
        waitTag("add-$sid")
        compose.onNodeWithTag("add-$sid").performTextInput("paper towels")
        compose.onNodeWithTag("add-$sid").performImeAction()
        serverSees("an item in the new section") { fake.items[shop.id]!!.any { it.text == "paper towels" && it.sectionId == sid } }
    }

    private fun hasAnyAncestorDialog() = androidx.compose.ui.test.hasAnyAncestor(isDialog())
}
