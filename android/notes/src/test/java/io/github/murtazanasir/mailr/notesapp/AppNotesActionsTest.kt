// ACT-SURFACES: android.notesapp
package io.github.murtazanasir.mailr.notesapp

import android.app.Application
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.notes.FakeNotesServer
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.notes.ui.NotesViewModel
import io.github.murtazanasir.mailr.testing.eventually
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The rest of the phone's notes and tables actions, through the calls the screens make: list
 * sections (checksection.reorder, checksection.order_mode, checksection.delete), checkitem.to_task,
 * note.to_text, note.reorder, note.search, notelabel.color, notelabel.reorder, table.create,
 * table.color, table.pin, table.label, table.archive, table.delete, table.restore, table.purge,
 * table.leave, table.view_edit, table.search, table.share, table.unshare, row.duplicate and
 * note.unlink, in the Notes app (`android.notesapp`) on its own graph and database.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class AppNotesActionsTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = NotesTestGraph()
    private lateinit var fake: FakeNotesServer
    private lateinit var g: NotesGraph
    private val repo get() = g.notes.repo

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
    }

    private fun writes() = fake.log.filter { it.method != "GET" }

    @Test fun sectionsTasksAndTextOfAList() = runBlocking {
        val shop = fake.note("checklist", "Shopping", listOf("milk", "eggs"), sections = listOf("Costco", "Corner market"))
        repo.pull()
        val (costco, corner) = repo.sectionsNow(shop.id).sortedBy { it.order }
        // checksection.reorder: Corner market moves up, before Costco, on the phone at once.
        repo.editSection(shop.id, corner.id, direction = "up")
        assertEquals(listOf("Corner market", "Costco"), repo.sectionsNow(shop.id).sortedBy { it.order }.map { it.name })
        // checksection.order_mode
        repo.editSection(shop.id, costco.id, orderMode = "learned")
        assertEquals("learned", repo.sectionsNow(shop.id).first { it.id == costco.id }.orderMode)
        repo.drain()
        assertTrue(writes().any { it.path == "notes/${shop.id}/sections/${corner.id}" && it.body!!["direction"]!!.jsonPrimitive.content == "up" })
        assertTrue(writes().any { it.body?.get("order_mode")?.jsonPrimitive?.content == "learned" })
        // checksection.delete: "Keep the items" moves them to the top group.
        val milk = repo.itemsNow(shop.id).first { it.text == "milk" }
        repo.moveItem(milk.id, sectionId = costco.id, toSection = true)
        repo.deleteSection(shop.id, costco.id, keepItems = true)
        assertEquals(null, repo.itemsNow(shop.id).first { it.id == milk.id }.sectionId)
        repo.drain()
        assertTrue(fake.sections[shop.id]!!.none { it.id == costco.id })
        assertEquals(null, fake.items[shop.id]!!.first { it.id == milk.id }.sectionId)
        // checkitem.to_task: the server makes the task; the item shows it after the next read.
        repo.toTask(milk.id)
        repo.drain()
        assertEquals("task-1", repo.itemsNow(shop.id).first { it.id == milk.id }.taskUid)
        // note.to_text
        repo.convert(shop.id, "text")
        assertEquals("text", repo.noteNow(shop.id)!!.kind)
        assertTrue(repo.noteNow(shop.id)!!.body.contains("- [ ] milk"))
        repo.drain()
        assertEquals("text", fake.notes[shop.id]!!.kind)
    }

    @Test fun gridOrderSearchAndLabels() = runBlocking {
        val a = fake.note("text", "A")
        val b = fake.note("checklist", "B", listOf("oat milk"))
        fake.labels += listOf("Home", "Work")
        repo.pull()
        // note.reorder: before the other card, on the phone at once.
        repo.setState(b.id, before = a.id)
        repo.drain()
        assertEquals(b.id, writes().single { it.path == "notes/${b.id}/state" }.path.removePrefix("notes/").removeSuffix("/state"))
        assertEquals(a.id, writes().single { it.path == "notes/${b.id}/state" }.body!!["before"]!!.jsonPrimitive.content)
        // note.search: the server's full text (an item's text) joins the phone's own match.
        val vm = graphs.vm { NotesViewModel(repo) }
        vm.search("oat")
        eventually("the search shows only the server's match") { vm.grid.value.cards.map { it.id } == listOf(b.id) }
        // notelabel.color and notelabel.reorder
        repo.editLabel("Work", color = "sage")
        repo.editLabel("Work", before = "Home")
        assertEquals("sage", repo.labels.first().first { it.name == "Work" }.color)
        repo.drain()
        assertTrue(writes().any { it.path == "notes/labels/Work" && it.body!!["color"]?.jsonPrimitive?.content == "sage" })
        assertTrue(writes().any { it.path == "notes/labels/Work" && it.body!!["before"]?.jsonPrimitive?.content == "Home" })
    }

    @Test fun tablesAsAWhole() = runBlocking {
        // table.create offline-ready: a temporary id until the server names it.
        val (tmp, _) = repo.createTable("Expenses", "log")
        repo.drain()
        assertTrue(writes().any { it.path == "tables" && it.body!!["template"]!!.jsonPrimitive.content == "log" })
        assertNull(repo.tableNow(tmp))
        val t = fake.tables.values.single()
        assertEquals("Expenses", repo.tableNow(t.id)!!.name)
        // table.color, table.pin, table.label, table.archive
        repo.editTable(t.id, color = "basil")
        repo.setState(t.id, pinned = true)
        repo.setState(t.id, labels = listOf("Money"))
        repo.drain()
        assertTrue(writes().any { it.path == "tables/${t.id}" && it.body!!["color"]!!.jsonPrimitive.content == "basil" })
        assertEquals("true", fake.tableState["${t.id}.pinned"])
        assertEquals("Money", fake.tableState["${t.id}.labels"])
        repo.setState(t.id, archived = true)
        assertFalse(repo.tableNow(t.id)!!.state.pinned)
        repo.drain()
        assertEquals("true", fake.tableState["${t.id}.archived"])
        // table.delete, table.restore, table.purge
        repo.trash(t.id)
        repo.drain()
        assertTrue(fake.tables[t.id]!!.deleted != null)
        repo.restore(t.id, isTable = true)
        repo.drain()
        assertNull(fake.tables[t.id]!!.deleted)
        repo.purge(t.id, isTable = true)
        repo.drain()
        assertNull(fake.tables[t.id])
        assertNull(repo.tableNow(t.id))
    }

    @Test fun rowsViewsAndSharesOfATable() = runBlocking {
        val t = fake.table("Workouts", listOf("Exercise" to "text", "Weight" to "number"),
            listOf(mapOf("c0" to "Squat", "c1" to "80"), mapOf("c0" to "Bench", "c1" to "60")))
        repo.pull()
        // table.view_edit (a cleared filter, for this reading) and table.search (find text in the table).
        repo.loadRows(t.id, "v1", spec = JsonObject(mapOf("filters" to kotlinx.serialization.json.JsonArray(emptyList()))))
        repo.loadRows(t.id, "v1", q = "squat")
        val reads = fake.log.filter { it.path.startsWith("tables/${t.id}/rows?") }
        assertTrue(reads.any { it.path.contains("spec=") })
        assertTrue(reads.any { it.path.contains("q=squat") })
        // row.duplicate: the server makes the copy; it shows after the next sync.
        val squat = fake.rows.values.first { it.cells["c0"] == JsonPrimitive("Squat") }
        repo.duplicateRow(t.id, squat.id)
        repo.drain()
        repo.pull()
        assertEquals(2, repo.rows(t.id).first().count { it.row.cells["c0"] == JsonPrimitive("Squat") })
        // table.share and table.unshare through the people sheet's calls.
        val d = repo.api.share("table", t.id, "7", "edit")
        assertEquals("edit", d.people.single().level)
        assertTrue(repo.api.unshare("table", t.id, "7").people.isEmpty())
        // table.leave: the table leaves this phone, and the undo is the server's.
        val undo = repo.leave(t.id, isTable = true)
        assertNull(repo.tableNow(t.id))
        assertEquals("/tables/${t.id}/shares/me", undo!!.path)
    }

    @Test fun aLinkChipsCrossUnlinks() = runBlocking {
        val n = fake.note("text", "Plan").also { it.links = listOf(3L) }
        repo.pull()
        assertEquals("Re: Q4 plan", repo.noteNow(n.id)!!.links.single().title)
        // note.unlink: gone at once, then from the server.
        repo.unlink(n.id, 3)
        assertTrue(repo.noteNow(n.id)!!.links.isEmpty())
        repo.drain()
        assertTrue(fake.notes[n.id]!!.links.isEmpty())
    }
}
