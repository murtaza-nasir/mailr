// ACT-SURFACES: android.notesapp
package io.github.murtazanasir.mailr.notesapp

import android.app.Application
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.notes.FakeNotesServer
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.notes.data.NotesRepository
import io.github.murtazanasir.mailr.notes.data.NotesRepository.DrainResult
import io.github.murtazanasir.mailr.notes.data.OpState
import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.testing.settle
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The Notes module's data layer in the Notes app (its own Room database, `android.notesapp`), offline use: the delta sync, the item-level
 * replay queue with client ids and device tap times, conflicts, temporary ids and live events, against
 * a fake server ([FakeNotesServer]).
 *
 * Covers note.create, note.edit, note.color, note.pin, note.archive, note.label, note.delete,
 * note.restore, note.purge, note.clear_checked, note.uncheck_all, note.add_items, note.duplicate,
 * note.to_checklist, checkitem.add, checkitem.check, checkitem.uncheck, checkitem.edit, checkitem.move,
 * checkitem.delete, checkitem.usual_add, checkitem.usual_dismiss, checksection.create, checksection.rename,
 * notelabel.create, notelabel.rename, notelabel.delete, table.rename, row.add, row.edit, row.delete,
 * row.bulk_delete and the phone's side of the sync.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class AppNotesSyncTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = NotesTestGraph()
    private lateinit var fake: FakeNotesServer
    private lateinit var g: NotesGraph
    private val repo: NotesRepository get() = g.notes.repo

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

    @Test fun pullKeepsNotesItemsTablesAndRowsAndDropsWhatIsNoLongerVisible() = runBlocking {
        val shop = fake.note("checklist", "Shopping", listOf("milk", "eggs"), sections = listOf("Costco"))
        val other = fake.note("text", "Ideas")
        val t = fake.table("Expenses", listOf("Item" to "text", "Amount" to "number"), listOf(mapOf("c0" to "Rent", "c1" to "900")))
        fake.labels += "Home"
        assertTrue(repo.pull())
        assertEquals(setOf("Shopping", "Ideas"), repo.notes.first().map { it.title }.toSet())
        assertEquals(listOf("milk", "eggs"), repo.itemsNow(shop.id).map { it.text })
        assertEquals(listOf("Costco"), repo.sectionsNow(shop.id).map { it.name })
        assertEquals("Expenses", repo.tableNow(t.id)!!.name)
        assertEquals("Rent", repo.rows(t.id).first().single().row.cells["c0"]!!.jsonPrimitive.content)
        assertEquals(listOf("Home"), repo.labels.first().map { it.name })
        // The second sync sends the server's `now` back as `since`.
        val since = fake.log.last { it.path.startsWith("notes/sync") }
        repo.pull()
        assertTrue(fake.log.last { it.path.startsWith("notes/sync") }.path.contains("since="))
        assertFalse(since.path.contains("since="))
        // note.unshare / note.leave / note.purge: no longer visible, so it leaves the phone; a changed level shows.
        fake.notes.remove(other.id)
        shop.level = "view"
        repo.pull()
        assertNull(repo.noteNow(other.id))
        assertEquals("view", repo.noteNow(shop.id)!!.level)
        // note.delete elsewhere: the sync lists it in Trash.
        shop.deleted = "2026-10-05T09:00:00+00:00"
        repo.pull()
        assertNotNull(repo.noteNow(shop.id)!!.deletedAt)
    }

    @Test fun offlineAddsAndChecksShowAtOnceAndReplayInOrderWithTheTapTime() = runBlocking {
        val shop = fake.note("checklist", "Shopping", listOf("milk"))
        repo.pull()
        fake.offline = true
        // note.add_items / checkitem.add: through the grammar, with the phone's own item ids.
        val r = repo.addItems(shop.id, "2 cans of tomatoes, bread")!!
        assertEquals(2, r.added)
        val items = repo.itemsNow(shop.id)
        val tomatoes = items.first { it.text == "tomatoes" }
        assertEquals("2", tomatoes.qty!!.amount)
        assertEquals("can", tomatoes.qty!!.unit)
        // checkitem.check offline: the device time of the tap is kept.
        val milk = items.first { it.text == "milk" }
        repo.check(milk.id, true)
        val tapAt = repo.itemsNow(shop.id).first { it.id == milk.id }.checkedAt!!
        assertEquals(DrainResult.RETRY, repo.drain())
        assertTrue(repo.offline.value)
        assertEquals(2, repo.ops.first().size)
        fake.offline = false
        assertEquals(DrainResult.DONE, repo.drain())
        val sent = writes()
        assertEquals(listOf("POST notes/${shop.id}/items", "PATCH notes/${shop.id}/items/${milk.id}"), sent.map { "${it.method} ${it.path}" })
        assertEquals(tomatoes.id, sent[0].body!!["items"]!!.let { (it as kotlinx.serialization.json.JsonArray)[0].jsonObject["id"]!!.jsonPrimitive.content })
        assertEquals(tapAt, sent[1].body!!["device_at"]!!.jsonPrimitive.content)
        assertEquals(2, sent.map { it.key }.toSet().size)
        assertTrue(sent.all { it.origin == g.client.clientId })
        assertTrue(repo.ops.first().isEmpty())
        assertEquals(setOf("milk", "tomatoes", "bread"), fake.items[shop.id]!!.map { it.text }.toSet())
        assertTrue(fake.items[shop.id]!!.first { it.text == "milk" }.checked)
    }

    @Test fun aWriteWhoseAnswerWasLostReplaysOnceWithTheSameKey() = runBlocking {
        val shop = fake.note("checklist", "Shopping")
        repo.pull()
        fake.loseReplyTo = "notes/${shop.id}/items"
        repo.addItems(shop.id, "coffee")
        // The answer never arrives; the write goes again (OkHttp's retry or the next drain) with the same key.
        var tries = 0
        while (repo.drain() != DrainResult.DONE && tries++ < 3) Unit
        val adds = writes().filter { it.path == "notes/${shop.id}/items" }
        assertTrue(adds.size >= 2)
        assertEquals(1, adds.map { it.key }.toSet().size)
        assertEquals(1, fake.items[shop.id]!!.count { it.text == "coffee" })
        assertTrue(repo.ops.first().isEmpty())
    }

    @Test fun twoEditsMadeOfflineAreOneChangeFromOneVersion() = runBlocking {
        val shop = fake.note("checklist", "Shopping", listOf("milk"))
        repo.pull()
        val milk = repo.itemsNow(shop.id).single()
        fake.offline = true
        // checkitem.edit twice: the second joins the first, with the first's base_version.
        repo.editItem(milk.id, text = "oat milk")
        repo.editItem(milk.id, extra = "the big one")
        repo.drain()
        fake.offline = false
        repo.drain()
        val patch = writes().single { it.method == "PATCH" }
        assertEquals("oat milk", patch.body!!["text"]!!.jsonPrimitive.content)
        assertEquals("the big one", patch.body!!["extra"]!!.jsonPrimitive.content)
        assertEquals(1L, patch.body!!["base_version"]!!.jsonPrimitive.longOrNull)
    }

    @Test fun aConflictWaitsOnTheItemAndUseMineSendsAgainFromTheirVersion() = runBlocking {
        val shop = fake.note("checklist", "Shopping", listOf("milk"))
        repo.pull()
        val milk = repo.itemsNow(shop.id).single()
        fake.conflictOn[milk.id] = "goat milk"
        repo.editItem(milk.id, text = "oat milk")
        repo.drain()
        val state = repo.note(shop.id).first()!!
        assertNotNull(state.items.single().conflict)
        assertEquals(OpState.CONFLICT, repo.ops.first().single().state)
        repo.resolve(milk.id, NotesRepository.Choice.MINE)
        repo.drain()
        val last = writes().last()
        assertEquals(2L, last.body!!["base_version"]!!.jsonPrimitive.longOrNull)
        assertEquals("oat milk", fake.items[shop.id]!!.single().text)
        assertNull(repo.note(shop.id).first()!!.items.single().conflict)
    }

    @Test fun keepTheirsTakesTheServerCopyAndKeepBothAddsMine() = runBlocking {
        val shop = fake.note("checklist", "Shopping", listOf("milk", "eggs"))
        repo.pull()
        val (milk, eggs) = repo.itemsNow(shop.id).sortedBy { it.order }
        fake.conflictOn[milk.id] = "goat milk"
        repo.editItem(milk.id, text = "oat milk")
        repo.drain()
        repo.resolve(milk.id, NotesRepository.Choice.THEIRS)
        repo.drain()
        assertEquals("goat milk", repo.itemsNow(shop.id).first { it.id == milk.id }.text)
        assertTrue(repo.ops.first().isEmpty())
        fake.conflictOn[eggs.id] = "duck eggs"
        repo.editItem(eggs.id, text = "brown eggs")
        repo.drain()
        repo.resolve(eggs.id, NotesRepository.Choice.BOTH)
        repo.drain()
        repo.refreshNote(shop.id)
        assertEquals(setOf("goat milk", "duck eggs", "brown eggs"), repo.itemsNow(shop.id).map { it.text }.toSet())
    }

    @Test fun aNoteMadeOfflineGetsItsServerIdAndLaterWritesFollow() = runBlocking {
        fake.offline = true
        // note.create offline: a temporary id until the server names it.
        val (tmp, _) = repo.createNote("checklist", title = "Packing")
        repo.addItems(tmp, "socks")
        // checksection.create offline, then an item into it.
        repo.createSection(tmp, "Toiletries")
        val sec = repo.sectionsNow(tmp).single()
        repo.addItems(tmp, "toothbrush", sectionId = sec.id)
        repo.drain()
        fake.offline = false
        repo.drain()
        val real = repo.realId(tmp)
        assertTrue(real.startsWith("n"))
        assertNull(repo.noteNow(tmp))
        assertEquals("Packing", repo.noteNow(real)!!.title)
        val paths = writes().map { it.path }
        assertEquals(listOf("notes", "notes/$real/items", "notes/$real/sections", "notes/$real/items"), paths)
        val realSec = fake.sections[real]!!.single().id
        assertEquals(realSec, writes().last().body!!["items"]!!.let { (it as kotlinx.serialization.json.JsonArray)[0].jsonObject["section_id"]!!.jsonPrimitive.content })
        assertEquals(realSec, fake.items[real]!!.first { it.text == "toothbrush" }.sectionId)
    }

    @Test fun undoBeforeTheWriteLeavesTakesItOutOfTheQueue() = runBlocking {
        val shop = fake.note("checklist", "Shopping", listOf("milk"))
        repo.pull()
        val milk = repo.itemsNow(shop.id).single()
        fake.offline = true
        val h = repo.check(milk.id, true)
        assertTrue(repo.itemsNow(shop.id).single().checked)
        h.undo()
        assertFalse(repo.itemsNow(shop.id).single().checked)
        assertTrue(repo.ops.first().isEmpty())
        // After the write reached the server, the undo is the server's own undo, queued.
        fake.offline = false
        val h2 = repo.check(milk.id, true)
        repo.drain()
        h2.undo()
        repo.drain()
        assertEquals("PATCH", writes().last().method)
    }

    @Test fun aRefusedWriteIsKeptAsFailedUntilDiscarded() = runBlocking {
        val shop = fake.note("text", "Plan")
        repo.pull()
        fake.refuse["notes/${shop.id}"] = 422
        // note.edit refused: FAILED with Retry and Discard, the server's copy read again on Discard.
        repo.editNote(shop.id, title = "Plan B")
        repo.drain()
        val op = repo.ops.first().single()
        assertEquals(OpState.FAILED, op.state)
        assertEquals("Plan B", repo.noteNow(shop.id)!!.title)
        fake.refuse.clear()
        repo.discard(op)
        assertEquals("Plan", repo.noteNow(shop.id)!!.title)
        assertTrue(repo.ops.first().isEmpty())
    }

    @Test fun noteWritesStateAndTrash() = runBlocking {
        val n = fake.note("text", "Ideas")
        repo.pull()
        // note.color, note.pin, note.archive, note.label (per viewer)
        repo.editNote(n.id, color = "sage")
        repo.setState(n.id, pinned = true)
        repo.setState(n.id, labels = listOf("Home"))
        repo.drain()
        assertEquals("sage", fake.notes[n.id]!!.color)
        assertTrue(fake.notes[n.id]!!.pinned)
        assertEquals(listOf("Home"), fake.notes[n.id]!!.labels)
        repo.setState(n.id, archived = true)
        assertFalse(repo.noteNow(n.id)!!.state.pinned)
        repo.drain()
        assertTrue(fake.notes[n.id]!!.archived)
        // note.delete, then note.restore, then note.purge.
        repo.trash(n.id)
        repo.drain()
        assertNotNull(fake.notes[n.id]!!.deleted)
        repo.restore(n.id, isTable = false)
        repo.drain()
        assertNull(fake.notes[n.id]!!.deleted)
        repo.purge(n.id, isTable = false)
        repo.drain()
        assertNull(fake.notes[n.id])
        assertNull(repo.noteNow(n.id))
    }

    @Test fun listWritesItemsAndSections() = runBlocking {
        val shop = fake.note("checklist", "Shopping", listOf("milk", "eggs", "bread"), sections = listOf("Costco"))
        repo.pull()
        val (milk, eggs, bread) = repo.itemsNow(shop.id).sortedBy { it.order }
        val costco = repo.sectionsNow(shop.id).single()
        // checkitem.move into a section, checkitem.delete, note.uncheck_all, note.clear_checked
        repo.moveItem(milk.id, sectionId = costco.id, toSection = true)
        assertEquals(costco.id, repo.itemsNow(shop.id).first { it.id == milk.id }.sectionId)
        repo.deleteItem(bread.id)
        assertFalse(repo.itemsNow(shop.id).any { it.id == bread.id })
        repo.check(eggs.id, true)
        repo.uncheckAll(shop.id)
        assertFalse(repo.itemsNow(shop.id).any { it.checked })
        repo.check(eggs.id, true)
        repo.clearChecked(shop.id)
        assertFalse(repo.itemsNow(shop.id).any { it.id == eggs.id })
        // checksection.rename
        repo.editSection(shop.id, costco.id, name = "Costco Wholesale")
        repo.drain()
        assertEquals(costco.id, fake.items[shop.id]!!.first { it.id == milk.id }.sectionId)
        assertEquals(listOf("milk"), fake.items[shop.id]!!.map { it.text })
        assertEquals("Costco Wholesale", fake.sections[shop.id]!!.single().name)
        // checkitem.usual_add, checkitem.usual_dismiss
        val usual = repo.usual(shop.id)!!.items.single()
        repo.addUsual(shop.id, usual)
        repo.dismissUsual(shop.id, "tea", never = true)
        repo.drain()
        assertTrue(fake.items[shop.id]!!.any { it.text == "coffee" })
        assertTrue(writes().any { it.path == "notes/${shop.id}/usual/tea/dismiss" && it.body!!["for"]!!.jsonPrimitive.content == "never" })
    }

    @Test fun duplicatesMergeOnThePhoneAsOnTheServer() = runBlocking {
        val shop = fake.note("checklist", "Shopping", listOf("milk"))
        repo.pull()
        val r = repo.addItems(shop.id, "Milk")!!
        assertEquals(0, r.added)
        assertEquals(1, r.merged.size)
        assertEquals("2", repo.itemsNow(shop.id).single().qty!!.amount)
        val milk = repo.itemsNow(shop.id).single()
        repo.check(milk.id, true)
        val again = repo.addItems(shop.id, "milk")!!
        assertEquals(listOf(milk.id), again.rechecked)
        assertFalse(repo.itemsNow(shop.id).single().checked)
    }

    @Test fun labelsRenameAndDeleteOnEveryNote() = runBlocking {
        val n = fake.note("text", "Ideas")
        n.labels = listOf("Home")
        fake.labels += "Home"
        repo.pull()
        repo.createLabel("Work")
        repo.editLabel("Home", newName = "House")
        assertEquals(listOf("House"), repo.noteNow(n.id)!!.state.labels)
        assertEquals(setOf("House", "Work"), repo.labels.first().map { it.name }.toSet())
        val h = repo.deleteLabel("House")
        assertTrue(repo.noteNow(n.id)!!.state.labels.isEmpty())
        h.undo()
        assertEquals(listOf("House"), repo.noteNow(n.id)!!.state.labels)
        assertEquals(listOf("POST notes/labels", "PATCH notes/labels/Home"), repo.ops.first().map { "${it.method} ${it.path}" })
    }

    @Test fun rowsAddEditAndDeleteThroughTheQueue() = runBlocking {
        val t = fake.table("Expenses", listOf("Item" to "text", "Amount" to "number"), listOf(mapOf("c0" to "Rent", "c1" to "900")))
        repo.pull()
        val rent = repo.rows(t.id).first().single().row
        fake.offline = true
        // row.add with the phone's id; row.edit twice joins into one PATCH with both cells.
        val (id, _) = repo.addRow(t.id, JsonObject(mapOf("c0" to JsonPrimitive("Pens"))))
        repo.editRow(t.id, rent.id, JsonObject(mapOf("c1" to JsonPrimitive("950"))))
        repo.editRow(t.id, rent.id, JsonObject(mapOf("c0" to JsonPrimitive("Rent (Oct)"))))
        assertEquals("950", repo.rows(t.id).first().first { it.row.id == rent.id }.row.cells["c1"]!!.jsonPrimitive.content)
        repo.drain()
        fake.offline = false
        repo.drain()
        assertEquals("Pens", fake.rows[id]!!.cells["c0"]!!.jsonPrimitive.content)
        val patch = writes().single { it.method == "PATCH" }
        assertEquals(setOf("c0", "c1"), patch.body!!["cells"]!!.jsonObject.keys)
        // row.delete and row.bulk_delete
        repo.deleteRows(t.id, listOf(id))
        repo.deleteRows(t.id, listOf(rent.id, id))
        repo.drain()
        assertTrue(fake.rows[rent.id]!!.deleted)
        assertTrue(writes().any { it.path == "tables/${t.id}/rows/bulk" })
        assertTrue(repo.rows(t.id).first().isEmpty())
        // table.rename
        repo.editTable(t.id, name = "Costs")
        repo.drain()
        assertEquals("Costs", fake.tables[t.id]!!.name)
    }

    @Test fun liveEventsRefreshWhatOthersChangedAndSkipOwnEchoes() = runBlocking {
        val shop = fake.note("checklist", "Shopping", listOf("milk"))
        repo.pull()
        fake.items[shop.id]!!.add(FakeNotesServer.I("0192aaaa-0000-7000-8000-000000000001", "eggs", order = "z"))
        val before = fake.log.size
        // Its own echo: whatever the event starts in the graph's scope has finished, and asked nothing.
        settle(g.scope, what = "its own echo") {
            repo.onEvent("item", JsonObject(mapOf("kind" to JsonPrimitive("checkitem"), "id" to JsonPrimitive("x"),
                "note" to JsonPrimitive(shop.id), "origin" to JsonPrimitive(g.client.clientId))))
        }
        assertEquals(before, fake.log.size)
        repo.onEvent("item", JsonObject(mapOf("kind" to JsonPrimitive("checkitem"), "id" to JsonPrimitive("x"),
            "note" to JsonPrimitive(shop.id), "origin" to JsonPrimitive("tab-9"),
            "author" to JsonObject(mapOf("id" to JsonPrimitive(2), "name" to JsonPrimitive("Dana Example"))))))
        eventually("the other tab's item arrives") { runBlocking { repo.itemsNow(shop.id).size >= 2 } }
        assertEquals(setOf("milk", "eggs"), repo.itemsNow(shop.id).map { it.text }.toSet())
        assertEquals("Dana Example", repo.editingNow(shop.id))
        // "unshared": the note leaves the phone, with a notice.
        repo.onEvent("notes", JsonObject(mapOf("what" to JsonPrimitive("unshared"), "item_kind" to JsonPrimitive("note"),
            "id" to JsonPrimitive(shop.id), "by_name" to JsonPrimitive("Dana Example"), "name" to JsonPrimitive("Shopping"))))
        eventually("the unshared note leaves the phone") { runBlocking { repo.noteNow(shop.id) == null } }
    }
}
