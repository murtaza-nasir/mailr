// ACT-SURFACES: android.notes
package io.github.murtazanasir.mailr.notes

import android.app.Application
import android.content.Intent
import android.content.pm.ShortcutManager
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
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
import io.github.murtazanasir.mailr.ui.MainActivity
import io.github.murtazanasir.mailr.ui.notes.notesHost
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/**
 * The Notes tab's menu entries that match the Notes app's, through the mailr app's own host
 * (notesHost): Export of a note (note.export, Markdown) and of a table (table.export_csv) to the share
 * sheet, Save as template (note.save_template), Delete on the user's own templates
 * (note.delete_template), a table's Make a copy (table.duplicate), and "Add to home screen"
 * (note.add_to_home), a pinned shortcut that opens the item in this app's Notes tab.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class NotesTabActionsTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()
    private lateinit var fake: FakeNotesServer
    private lateinit var g: AppGraph
    private lateinit var vm: NotesViewModel
    private val ctx get() = ApplicationProvider.getApplicationContext<Application>()

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
        vm = graphs.vm { NotesViewModel(g.notes.repo) }
    }

    private fun show() {
        compose.setContent {
            MailrTheme(ThemeMode.LIGHT, dynamic = false) { NotesRoute(vm, notesHost(ctx, g, false, null, {}, {})) }
        }
        compose.waitForIdle()
    }

    private fun menu(entry: String) {
        compose.eventually("the ⋮ button") { compose.onAllNodes(hasContentDescription("More options")).fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithContentDescription("More options").performClick()
        compose.onNodeWithText(entry).performClick()
    }

    /** What the share sheet was handed: the chooser's text and its subject (the file name). */
    private fun shared(): Pair<String, String> {
        var chooser: Intent? = null
        compose.eventually("the share sheet") { chooser = shadowOf(ctx).nextStartedActivity; chooser != null }
        assertEquals(Intent.ACTION_CHOOSER, chooser!!.action)
        @Suppress("DEPRECATION") val send = chooser!!.getParcelableExtra<Intent>(Intent.EXTRA_INTENT)!!
        return send.getStringExtra(Intent.EXTRA_SUBJECT)!! to send.getStringExtra(Intent.EXTRA_TEXT)!!
    }

    private fun pinned() = ctx.getSystemService(ShortcutManager::class.java).pinnedShortcuts.associateBy { it.id }

    @Test fun theNoteMenuExportsSavesATemplateAndPins() {
        val shop = fake.note("checklist", "Shopping", listOf("milk"))
        runBlocking { g.notes.repo.pull() }
        show()
        compose.runOnIdle { vm.open(NotesPage.OpenNote(shop.id)) }
        // ACT note.export @android.notes: Markdown to the share sheet.
        menu("Export")
        val (name, text) = shared()
        assertEquals("Shopping.md", name)
        assertTrue(text.contains("- [ ] milk"))
        // ACT note.save_template @android.notes
        menu("Save as template")
        compose.eventually("the template") { fake.templates.values.contains("Shopping") }
        // ACT note.add_to_home @android.notes: the shortcut opens the note in mailr's Notes tab.
        menu("Add to home screen")
        compose.eventually("the pinned shortcut") { pinned().containsKey("note:${shop.id}") }
        val intent = pinned()["note:${shop.id}"]!!.intent!!
        assertEquals("mailr://notes/n/${shop.id}", intent.dataString)
        assertEquals(MainActivity::class.java.name, intent.component!!.className)
        assertEquals("Shopping", pinned()["note:${shop.id}"]!!.shortLabel)
    }

    @Test fun ownTemplatesCanBeDeleted() {
        fake.templates["tpl1"] = "Weekly review"
        fake.note("text", "Plan")
        runBlocking { g.notes.repo.pull() }
        show()
        compose.eventually("the New button") { compose.onAllNodes(hasTestTag("notes-new")).fetchSemanticsNodes().isNotEmpty() }
        compose.onNode(hasTestTag("notes-new")).performClick()
        compose.onNodeWithText("From a template").performClick()
        compose.eventually("the user's template") { compose.onAllNodes(hasText("Weekly review")).fetchSemanticsNodes().isNotEmpty() }
        // ACT note.delete_template @android.notes: the user's own template has Delete; the starter has none.
        assertTrue(compose.onAllNodes(hasContentDescription("Delete template Packing")).fetchSemanticsNodes().isEmpty())
        compose.onNodeWithContentDescription("Delete template Weekly review").performClick()
        compose.eventually("the template is gone") { fake.templates.isEmpty() }
    }

    @Test fun theTableMenuExportsCsvCopiesAndPins() {
        val t = fake.table("Expenses", listOf("Item" to "text", "Amount" to "number"), listOf(mapOf("c0" to "Rent", "c1" to "900")))
        runBlocking { g.notes.repo.pull() }
        show()
        compose.runOnIdle { vm.open(NotesPage.OpenTable(t.id)) }
        // ACT table.export_csv @android.notes
        menu("Export CSV")
        assertEquals("Expenses.csv" to "Item,Amount\nRent,900\n", shared())
        // ACT table.duplicate @android.notes: the copy, with its rows.
        menu("Make a copy")
        compose.eventually("the copy") { runBlocking { g.notes.repo.tables.first() }.any { it.name == "Expenses (copy)" } }
        val copy = runBlocking { g.notes.repo.tables.first() }.single { it.name == "Expenses (copy)" }
        assertEquals(1, runBlocking { g.notes.repo.rows(copy.id).first() }.size)
        // ACT note.add_to_home @android.notes (a table)
        menu("Add to home screen")
        compose.eventually("the pinned shortcut") { pinned().containsKey("table:${t.id}") }
        assertEquals("mailr://tables/t/${t.id}", pinned()["table:${t.id}"]!!.intent!!.dataString)
    }
}
