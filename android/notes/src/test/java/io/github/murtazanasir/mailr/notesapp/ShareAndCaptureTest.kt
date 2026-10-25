// ACT-SURFACES: android.notesshare android.notesapp
package io.github.murtazanasir.mailr.notesapp

import android.app.Application
import android.content.Intent
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.hasText
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.notes.FakeNotesServer
import io.github.murtazanasir.mailr.notesapp.capture.CaptureMode
import io.github.murtazanasir.mailr.notesapp.capture.CaptureSheet
import io.github.murtazanasir.mailr.notesapp.capture.ShareActivity
import io.github.murtazanasir.mailr.notesapp.capture.ShareSheet
import io.github.murtazanasir.mailr.notesapp.data.NotesPrefs
import io.github.murtazanasir.mailr.notesapp.data.ThemeChoice
import io.github.murtazanasir.mailr.notesapp.ui.NotesTheme
import io.github.murtazanasir.mailr.testing.eventually
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The share target and quick capture on screen: shared text
 * becomes a note (note.create) or items of a list through the grammar in capture mode
 * (note.add_items), with a section to pick; tab-separated text becomes rows of a table (row.add);
 * images are refused; quick capture adds to the list a `#` token names (checkitem.add).
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class ShareAndCaptureTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = NotesTestGraph()
    @get:Rule val compose = createComposeRule()
    private lateinit var fake: FakeNotesServer
    private lateinit var g: NotesGraph
    private val ctx get() = ApplicationProvider.getApplicationContext<Application>()
    private var done: String? = null

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

    private val light = NotesPrefs.Look(ThemeChoice.LIGHT, false)

    private fun waitTag(t: String) = compose.eventually(t) { compose.onAllNodes(hasTestTag(t)).fetchSemanticsNodes().isNotEmpty() }

    @Test fun incomingSharesAreReadAndImagesRefused() {
        val text = ShareActivity.read(Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, "https://example.com/a")
            .putExtra(Intent.EXTRA_SUBJECT, "A page"))!!
        assertEquals("https://example.com/a", text.text)
        assertEquals("A page", text.subject)
        assertTrue(ShareActivity.read(Intent(Intent.ACTION_SEND).setType("image/jpeg"))!!.image)
        assertNull(ShareActivity.read(Intent(Intent.ACTION_VIEW)))
        assertNull(ShareActivity.read(Intent(Intent.ACTION_SEND).setType("text/plain")))
    }

    @Test fun sharedLinkBecomesANote() {
        compose.setContent { NotesTheme(light) { ShareSheet(g.notes.repo, g.prefs, "https://example.com/recipe", "Lemon cake") { done = it } } }
        waitTag("share-save")
        compose.onNodeWithTag("share-save").performClick()
        compose.eventually("saved") { done != null }
        assertEquals("Note saved", done)
        val n = runBlocking { g.notes.repo.notes.first() }.single()
        assertEquals("Lemon cake", n.title)
        assertEquals("https://example.com/recipe", n.body)
        runBlocking { g.notes.repo.drain() }
        assertTrue(fake.notes.values.any { it.title == "Lemon cake" && it.body == "https://example.com/recipe" })
    }

    @Test fun sharedLinesGoToAListSection() {
        val shop = fake.note("checklist", "Shopping", listOf("milk"), sections = listOf("Costco"))
        fake.note("checklist", "Packing")
        runBlocking { g.notes.repo.pull() }
        g.prefs.opened("note", shop.id)
        compose.setContent { NotesTheme(light) { ShareSheet(g.notes.repo, g.prefs, "eggs\n2 cans of tomatoes, bread", null) { done = it } } }
        waitTag("share-LIST")
        compose.onNodeWithTag("share-LIST").performClick()
        // Recent lists first: Shopping is chosen.
        compose.eventually("the summary") { compose.onAllNodes(hasText("Add 3 items to Shopping")).fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("Costco").performClick()
        compose.onNodeWithTag("share-save").performClick()
        compose.eventually("added") { done != null }
        assertEquals("3 items added to Shopping", done)
        runBlocking { g.notes.repo.drain() }
        val sid = fake.sections[shop.id]!!.single().id
        val added = fake.items[shop.id]!!.filter { it.text != "milk" }
        assertEquals(setOf("eggs", "tomatoes", "bread"), added.map { it.text }.toSet())
        assertTrue(added.all { it.sectionId == sid })
    }

    @Test fun sharedTabbedTextBecomesRows() {
        val t = fake.table("Expenses", listOf("Item" to "text", "Amount" to "number"))
        runBlocking { g.notes.repo.pull() }
        compose.setContent { NotesTheme(light) { ShareSheet(g.notes.repo, g.prefs, "Item\tAmount\nRent\t900\nPens\t4", null) { done = it } } }
        waitTag("share-TABLE")
        compose.onNodeWithTag("share-TABLE").performClick()
        compose.eventually("the preview") { compose.onAllNodes(hasText("2 rows")).fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithTag("share-save").performClick()
        compose.eventually("added") { done != null }
        assertEquals("2 rows added to Expenses", done)
        runBlocking { g.notes.repo.drain() }
        val rows = fake.rows.values.filter { it.table == t.id }
        assertEquals(setOf("Rent", "Pens"), rows.map { it.cells["c0"]!!.jsonPrimitive.content }.toSet())
    }

    @Test fun quickCaptureAddsToTheListATokenNames() {
        fake.note("checklist", "Shopping", listOf("milk"))
        val pack = fake.note("checklist", "Packing")
        runBlocking { g.notes.repo.pull() }
        compose.setContent { NotesTheme(light) { CaptureSheet(g.notes.repo, g.prefs, CaptureMode.ADD, null, { }) { done = it } } }
        waitTag("capture-text")
        compose.onNodeWithTag("capture-text").performTextInput("#pack socks, charger")
        compose.eventually("the summary") { compose.onAllNodes(hasText("Add 2 items to Packing")).fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithTag("capture-save").performClick()
        compose.eventually("added") { done != null }
        assertEquals("2 items added to Packing", done)
        assertEquals(pack.id, g.prefs.defaultList)
        runBlocking { g.notes.repo.drain() }
        assertEquals(setOf("socks", "charger"), fake.items[pack.id]!!.map { it.text }.toSet())
    }

    @Test fun quickCaptureMakesANoteOrAList() {
        compose.setContent { NotesTheme(light) { CaptureSheet(g.notes.repo, g.prefs, CaptureMode.LIST, null, { }) { done = it } } }
        waitTag("capture-title")
        compose.onNodeWithTag("capture-title").performTextInput("Weekend")
        compose.onNodeWithTag("capture-text").performTextInput("rake leaves\nclean gutters")
        compose.onNodeWithTag("capture-save").performClick()
        compose.eventually("saved") { done != null }
        val n = runBlocking { g.notes.repo.notes.first() }.single()
        assertEquals("checklist", n.kind)
        assertEquals(listOf("rake leaves", "clean gutters"), runBlocking { g.notes.repo.itemsNow(n.id) }.map { it.text })
    }
}
