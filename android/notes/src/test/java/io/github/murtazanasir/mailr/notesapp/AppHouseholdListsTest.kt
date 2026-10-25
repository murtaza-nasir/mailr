// ACT-SURFACES: android.notesapp
package io.github.murtazanasir.mailr.notesapp

import android.app.Application
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.notes.FakeNotesServer
import io.github.murtazanasir.mailr.notes.ui.NotesPage
import io.github.murtazanasir.mailr.notes.ui.NotesRoute
import io.github.murtazanasir.mailr.notes.ui.NotesViewModel
import io.github.murtazanasir.mailr.notesapp.capture.CaptureLogic
import io.github.murtazanasir.mailr.notesapp.capture.CaptureMode
import io.github.murtazanasir.mailr.notesapp.capture.CaptureSheet
import io.github.murtazanasir.mailr.notesapp.data.NotesPrefs
import io.github.murtazanasir.mailr.notesapp.data.ThemeChoice
import io.github.murtazanasir.mailr.notesapp.ui.NotesTheme
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.testing.serverSees
import io.github.murtazanasir.mailr.testing.waitForNode
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
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
 * Household lists in the Notes app (`android.notesapp`), on its own graph: note.local_only and
 * table.local_only (locked for an editor), note.repeat, note.presence and table.presence, and
 * note.quick in quick capture ("#Shopping Costco: milk" adds milk to Costco in Shopping, offline too;
 * anything else is a text note).
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class AppHouseholdListsTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = NotesTestGraph()
    @get:Rule val compose = createComposeRule()
    private lateinit var fake: FakeNotesServer
    private lateinit var g: NotesGraph
    private lateinit var vm: NotesViewModel
    private val repo get() = g.notes.repo
    private val light = NotesPrefs.Look(ThemeChoice.LIGHT, false)
    private var done: String? = null

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
        vm = graphs.vm { NotesViewModel(repo) }
    }

    private fun show() {
        compose.setContent { NotesTheme(light) { NotesRoute(vm) } }
        compose.waitForIdle()
    }

    private fun waitText(t: String) = compose.waitForNode(hasText(t, substring = true))

    private fun serverSees(what: String, check: () -> Boolean) = compose.serverSees(repo, what, check)

    private fun openMenu() {
        compose.onNodeWithContentDescription("More options").performClick()
        compose.waitForIdle()
    }

    @Test fun localModelsOnlyOnAListAndATable() {
        val shop = fake.note("checklist", "Shopping", listOf("milk"))
        val chores = fake.note("checklist", "Chores", listOf("vacuum"))
        chores.level = "edit"; chores.localOnly = true; chores.canLowerLocalOnly = false
        val t = fake.table("Budget", listOf("Item" to "text"))
        runBlocking { repo.pull() }
        show()
        // note.local_only: the owner turns it on.
        compose.runOnIdle { vm.open(NotesPage.OpenNote(shop.id)) }
        waitText("milk")
        openMenu()
        compose.onNodeWithTag("local-only").performClick()
        serverSees("note.local_only") { fake.notes[shop.id]!!.localOnly }
        // An editor's list that is on: locked, with the reason.
        compose.runOnIdle { vm.back(); vm.open(NotesPage.OpenNote(chores.id)) }
        waitText("vacuum")
        openMenu()
        compose.onNodeWithText("Only the owner can turn this off").assertExists()
        compose.onNodeWithTag("local-only").assertIsNotEnabled()
        compose.runOnIdle { vm.home() }
        // table.local_only
        compose.runOnIdle { vm.open(NotesPage.OpenTable(t.id)) }
        waitText("Budget")
        openMenu()
        compose.onNodeWithTag("local-only").performClick()
        serverSees("table.local_only") { fake.tables[t.id]!!.localOnly }
    }

    @Test fun aListRepeatsEveryFewDays() {
        val packing = fake.note("checklist", "Packing", listOf("socks"))
        val text = fake.note("text", "Ideas")
        runBlocking { repo.pull() }
        show()
        compose.runOnIdle { vm.open(NotesPage.OpenNote(packing.id)) }
        waitText("socks")
        // note.repeat
        openMenu()
        compose.onNodeWithTag("menu-repeat").performClick()
        compose.onNodeWithTag("repeat-daily").performClick()
        compose.onNodeWithTag("repeat-save").performClick()
        serverSees("note.repeat") { fake.notes[packing.id]!!.repeat == "FREQ=DAILY;INTERVAL=3" }
        waitText("Repeats every 3 days")
        // A text note has no Repeat in its menu.
        compose.runOnIdle { vm.back(); vm.open(NotesPage.OpenNote(text.id)) }
        compose.waitForNode(hasTestTag("note-body"))
        openMenu()
        assertTrue(compose.onAllNodes(hasTestTag("menu-repeat")).fetchSemanticsNodes().isEmpty())
    }

    @Test fun presenceInASharedListAndTable() {
        val shop = fake.note("checklist", "Shopping", listOf("milk"))
        shop.sharedCount = 2
        val t = fake.table("Chores", listOf("Task" to "text"))
        t.level = "edit"
        runBlocking { repo.pull() }
        show()
        // note.presence
        compose.runOnIdle { vm.open(NotesPage.OpenNote(shop.id)) }
        waitText("milk")
        compose.eventually("here") { "note:${shop.id}:here:hello" in fake.presence }
        compose.runOnIdle {
            repo.onEvent("presence", buildJsonObject {
                put("type", "presence"); put("kind", "note"); put("id", shop.id); put("state", "here")
                put("person", buildJsonObject { put("id", 2); put("name", "Olive") })
            })
        }
        waitText("Olive is in this list")
        compose.runOnIdle { vm.back() }
        compose.eventually("left") { "note:${shop.id}:left" in fake.presence }
        // table.presence
        compose.runOnIdle { vm.open(NotesPage.OpenTable(t.id)) }
        waitText("Chores")
        compose.eventually("table here") { "table:${t.id}:here:hello" in fake.presence }
    }

    @Test fun aQuickNoteWithAListTokenAddsItems() {
        val shop = fake.note("checklist", "Shopping", listOf("bread"), sections = listOf("Costco"))
        runBlocking { repo.pull() }
        fake.offline = true
        compose.setContent { NotesTheme(light) { CaptureSheet(repo, g.prefs, CaptureMode.NOTE, null, { }) { done = it } } }
        compose.eventually("the sheet") { compose.onAllNodes(hasTestTag("capture-text")).fetchSemanticsNodes().isNotEmpty() }
        // note.quick: "#Shopping Costco: milk" in New note adds milk to Costco in Shopping, with no signal.
        compose.onNodeWithTag("capture-text").performTextInput("#Shopping Costco: milk")
        waitText("Add milk to Shopping")
        compose.onNodeWithTag("capture-save").performClick()
        compose.eventually("saved") { done != null }
        assertEquals("milk added to Shopping", done)
        val costco = fake.sections[shop.id]!!.single().id
        val milk = runBlocking { repo.itemsNow(shop.id) }.single { it.text == "milk" }
        assertEquals(costco, milk.sectionId)
        assertTrue(runBlocking { repo.notes.first() }.none { it.kind == "text" })
        fake.offline = false
        runBlocking { repo.drain() }
        assertEquals(costco, fake.items[shop.id]!!.single { it.text == "milk" }.sectionId)
        // Anything else stays a text note.
        val lists = CaptureLogic.lists(runBlocking { repo.notes.first() }, emptyMap())
        assertNull(CaptureLogic.quickPlan("Call Dana about #Shopping", lists))
        assertNull(CaptureLogic.quickPlan("#1 priority: taxes", lists))
        assertEquals("Shopping", CaptureLogic.quickPlan("  #shop eggs", lists)!!.list!!.name)
    }
}
