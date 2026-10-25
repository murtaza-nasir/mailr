// ACT-SURFACES: android.notesapp
package io.github.murtazanasir.mailr.notesapp

import android.app.Application
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.hasText
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.printToString
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.notes.FakeNotesServer
import io.github.murtazanasir.mailr.notes.ui.NotesHostCallbacks
import io.github.murtazanasir.mailr.notes.ui.NotesPage
import io.github.murtazanasir.mailr.notes.ui.NotesRoute
import io.github.murtazanasir.mailr.notes.ui.NotesViewModel
import io.github.murtazanasir.mailr.notesapp.data.NotesPrefs
import io.github.murtazanasir.mailr.notesapp.data.ThemeChoice
import io.github.murtazanasir.mailr.notesapp.ui.NotesTheme
import io.github.murtazanasir.mailr.testing.eventually
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The Notes app's own entries on the shared screens: Export (note.export, Markdown) and Export CSV
 * (table.export_csv) through the share sheet, Save as template (note.save_template), Delete on the
 * user's own templates (note.delete_template), New from template (note.from_template), a table's Make a copy (table.duplicate) and "Add to
 * home screen" (note.add_to_home).
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class NotesAppExtrasTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = NotesTestGraph()
    @get:Rule val compose = createComposeRule()
    private lateinit var fake: FakeNotesServer
    private lateinit var g: NotesGraph
    private val ctx get() = ApplicationProvider.getApplicationContext<Application>()
    private val shared = mutableListOf<Triple<String, String, String>>()
    private val pinned = mutableListOf<Pair<String, String>>()

    @Before fun setUp() {
        fake = graphs.closeLast(object : AutoCloseable {
            val f = FakeNotesServer().start()
            override fun close() = f.close()
        }).f
        SessionStore(ctx).apply {
            server = fake.server.url("/").toString().trimEnd('/')
            save("tok-1", "csrf-1", "me@example.com", "Me")
        }
        g = graphs.graph(ctx)
    }

    /** The shared wait; a failure prints the screen's tree. */
    private fun eventually(what: String, check: () -> Boolean) = compose.eventually(what, detail = {
        compose.onAllNodes(androidx.compose.ui.test.isRoot()).fetchSemanticsNodes().joinToString("\n") { it.config.toString() } + "\n" +
            runCatching { compose.onAllNodes(androidx.compose.ui.test.isRoot())[0].printToString(40) }.getOrDefault("")
    }, check = check)

    private fun show(vm: NotesViewModel) {
        compose.setContent {
            NotesTheme(NotesPrefs.Look(ThemeChoice.LIGHT, false)) {
                NotesRoute(vm, NotesHostCallbacks(share = { n, t, m -> shared += Triple(n, t, m) },
                    addToHome = { k, id -> pinned += k to id }, otherAppLabel = "Open in Mailr"))
            }
        }
    }

    @Test fun theNoteMenuExportsSavesATemplateAndPins() {
        val shop = fake.note("checklist", "Shopping", listOf("milk"))
        runBlocking { g.notes.repo.pull() }
        val vm = graphs.vm { NotesViewModel(g.notes.repo) }
        show(vm)
        compose.runOnIdle { vm.open(NotesPage.OpenNote(shop.id)) }
        eventually("the editor") { compose.onAllNodes(hasContentDescription("More options")).fetchSemanticsNodes().isNotEmpty() }
        // note.export: Markdown to the share sheet.
        compose.onNodeWithContentDescription("More options").performClick()
        compose.onNodeWithText("Export").performClick()
        eventually("the export") { shared.isNotEmpty() }
        assertEquals("Shopping.md", shared.single().first)
        assertTrue(shared.single().second.contains("- [ ] milk"))
        // note.save_template
        compose.onNodeWithContentDescription("More options").performClick()
        compose.onNodeWithText("Save as template").performClick()
        eventually("the template") { fake.templates.values.contains("Shopping") }
        // note.add_to_home
        compose.onNodeWithContentDescription("More options").performClick()
        compose.onNodeWithText("Add to home screen").performClick()
        assertEquals(listOf("note" to shop.id), pinned)
    }

    @Test fun ownTemplatesCanBeDeleted() {
        fake.templates["tpl1"] = "Weekly review"
        fake.note("text", "Plan")
        runBlocking { g.notes.repo.pull() }
        val vm = graphs.vm { NotesViewModel(g.notes.repo) }
        show(vm)
        eventually("the New button") { compose.onAllNodes(hasTestTag("notes-new")).fetchSemanticsNodes().isNotEmpty() }
        compose.onNode(hasTestTag("notes-new")).performClick()
        compose.onNodeWithText("From a template").performClick()
        eventually("the user's template") { compose.onAllNodes(hasText("Weekly review")).fetchSemanticsNodes().isNotEmpty() }
        // note.delete_template: the user's own template has Delete; the starter has none.
        assertTrue(compose.onAllNodes(hasContentDescription("Delete template Packing")).fetchSemanticsNodes().isEmpty())
        compose.onNodeWithContentDescription("Delete template Weekly review").performClick()
        eventually("the template is gone") { fake.templates.isEmpty() }
        // note.from_template: the starter makes a list, opened at once.
        compose.onNodeWithText("Packing").performClick()
        eventually("the new list") { runBlocking { g.notes.repo.notes.first() }.any { it.title == "Packing" } }
        eventually("its editor") { compose.onAllNodes(hasTestTag("add-top")).fetchSemanticsNodes().isNotEmpty() }
    }

    @Test fun aTableExportsAsCsvAndCopies() = runBlocking {
        val t = fake.table("Expenses", listOf("Item" to "text", "Amount" to "number"), listOf(mapOf("c0" to "Rent", "c1" to "900")))
        g.notes.repo.pull()
        // table.export_csv
        assertEquals("Item,Amount\nRent,900\n", g.notes.repo.api.exportCsv(t.id))
        // table.duplicate: the copy and its rows arrive with the read that follows.
        g.notes.repo.duplicateTable(t.id)
        val copy = g.notes.repo.tables.first().single { it.name == "Expenses (copy)" }
        assertEquals(1, g.notes.repo.rows(copy.id).first().size)
    }
}
