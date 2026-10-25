// ACT-SURFACES: android.noteswidget
package io.github.murtazanasir.mailr.notesapp

import io.github.murtazanasir.mailr.time.AppClock
import android.app.Application
import androidx.compose.ui.unit.DpSize
import androidx.compose.ui.unit.dp
import androidx.glance.action.actionParametersOf
import androidx.glance.appwidget.testing.unit.hasRunCallbackClickAction
import androidx.glance.appwidget.testing.unit.runGlanceAppWidgetUnitTest
import androidx.glance.testing.unit.hasAnyDescendant
import androidx.glance.testing.unit.hasClickAction
import androidx.glance.testing.unit.hasContentDescription
import androidx.glance.testing.unit.hasText
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.notes.FakeNotesServer
import io.github.murtazanasir.mailr.notesapp.widget.AddUsualAction
import io.github.murtazanasir.mailr.notesapp.widget.CaptureContent
import io.github.murtazanasir.mailr.notesapp.widget.CheckItemAction
import io.github.murtazanasir.mailr.notesapp.widget.ChecklistContent
import io.github.murtazanasir.mailr.notesapp.widget.KEY_CHECKED
import io.github.murtazanasir.mailr.notesapp.widget.KEY_ITEM
import io.github.murtazanasir.mailr.notesapp.widget.KEY_LIST
import io.github.murtazanasir.mailr.notesapp.widget.KEY_USUAL
import io.github.murtazanasir.mailr.notesapp.widget.NoteContent
import io.github.murtazanasir.mailr.notesapp.widget.TableContent
import io.github.murtazanasir.mailr.notesapp.widget.UndoCheckAction
import io.github.murtazanasir.mailr.notesapp.widget.WidgetActions
import io.github.murtazanasir.mailr.notesapp.widget.WidgetModel
import io.github.murtazanasir.mailr.notesapp.widget.WidgetModels
import io.github.murtazanasir.mailr.notesapp.widget.WidgetPickActivity
import io.github.murtazanasir.mailr.notesapp.widget.WidgetKind
import io.github.murtazanasir.mailr.notesapp.widget.WidgetUndo
import io.github.murtazanasir.mailr.notesapp.data.WidgetTarget
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
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
 * The home-screen widgets: the checklist widget shows one list in display order
 * with "n checked", checks in place through the replay queue (checkitem.check, offline, then
 * replayed) with the header's 7 s Undo (checkitem.uncheck); "+" adds to the list (checkitem.add,
 * note.add_items through quick capture); the table widget shows its first rows and footer
 * (table.stats) with "+" for a row (row.add); the capture widget's buttons (note.create); the picker.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class WidgetsTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = NotesTestGraph()
    private lateinit var fake: FakeNotesServer
    private lateinit var g: NotesGraph
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
    }

    @Test fun theChecklistWidgetShowsTheListInOrder() = runBlocking {
        val shop = fake.note("checklist", "Shopping", listOf("milk", "eggs", "bread"), sections = listOf("Costco"))
        fake.items[shop.id]!![2].checked = true
        fake.items[shop.id]!![1].sectionId = fake.sections[shop.id]!!.single().id
        g.notes.repo.pull()
        val m = WidgetModels.checklist(g.notes.repo, shop.id, null, withUsual = false) as WidgetModel.ChecklistModel
        assertEquals(listOf(null to listOf("milk"), "Costco" to listOf("eggs")), m.groups.map { (s, rows) -> s to rows.map { it.text } })
        assertEquals(1, m.checked)
        val milk = fake.items[shop.id]!![0].id
        runGlanceAppWidgetUnitTest {
            setContext(ctx)
            setAppWidgetSize(DpSize(260.dp, 300.dp))
            provideComposable { ChecklistContent(m, 7) }
            onNode(hasText("Shopping")).assertExists()
            onNode(hasText("Costco")).assertExists()
            onNode(hasText("1 checked")).assertExists()
            // checkitem.check: the box runs the check callback for this item of this list.
            onNode(hasContentDescription("Check milk").and(hasRunCallbackClickAction<CheckItemAction>(
                actionParametersOf(KEY_LIST to shop.id, KEY_ITEM to milk, KEY_CHECKED to true)))).assertExists()
            // "+" opens a one-line add sheet bound to the list (checkitem.add, note.add_items).
            onNode(hasContentDescription("Add to Shopping").and(hasClickAction())).assertExists()
        }
    }

    @Test fun theUsualChipsAddThroughTheQueue() = runBlocking {
        // checkitem.usual_add: a "Usual" chip adds that item; the chips hide when the list has none.
        val m = WidgetModel.ChecklistModel("n1", "Shopping", null, listOf(null to listOf(WidgetModel.ChecklistModel.Row("i1", "milk", "2"))),
            checked = 2, usualItems = listOf("coffee", "eggs", "bananas", "tea").map { WidgetModel.ChecklistModel.UsualChip(it, it) },
            undo = null, readOnly = false, trashed = false, waiting = 1)
        runGlanceAppWidgetUnitTest {
            setContext(ctx)
            provideComposable { ChecklistContent(m, 7) }
            onNode(hasText("+ coffee").and(hasRunCallbackClickAction<AddUsualAction>(actionParametersOf(KEY_LIST to "n1", KEY_USUAL to "coffee"))))
                .assertExists()
            onNode(hasContentDescription("Add eggs to Shopping")).assertExists()
            onNode(hasText("1 more").and(hasClickAction())).assertExists()
            // Offline, the foot says what waits.
            onNode(hasText("2 checked · 1 pending change")).assertExists()
            onNode(hasContentDescription("Check 2 milk")).assertExists()
            // checkitem.voice_add: the microphone opens voice add for this list.
            onNode(hasContentDescription("Dictate items for Shopping").and(hasClickAction())).assertExists()
        }
        runGlanceAppWidgetUnitTest {
            setContext(ctx)
            provideComposable { ChecklistContent(m.copy(usualItems = emptyList(), waiting = 0), 7) }
            onNode(hasText("Usual")).assertDoesNotExist()
            onNode(hasText("2 checked")).assertExists()
        }
        // The chip's action: the item lands on the list from the suggestions kept on the phone, offline too.
        val shop = fake.note("checklist", "Shopping", listOf("milk"))
        fake.usual += listOf("coffee", "eggs")
        val repo = g.notes.repo
        repo.pull()
        repo.editNote(shop.id, suggestUsual = true)
        repo.drain()
        val model = WidgetModels.checklist(repo, shop.id, null) as WidgetModel.ChecklistModel
        assertEquals(listOf("coffee", "eggs"), model.usualItems.map { it.text })
        fake.offline = true
        assertEquals(listOf("coffee", "eggs"),
            (WidgetModels.checklist(repo, shop.id, null) as WidgetModel.ChecklistModel).usualItems.map { it.text })
        assertTrue(WidgetActions.addUsual(g, shop.id, "coffee"))
        assertTrue(repo.itemsNow(shop.id).any { it.text == "coffee" })
        val after = WidgetModels.checklist(repo, shop.id, null) as WidgetModel.ChecklistModel
        assertEquals(listOf("eggs"), after.usualItems.map { it.text })
        assertEquals(1, after.waiting)
        fake.offline = false
        repo.drain()
        assertTrue(fake.items[shop.id]!!.any { it.text == "coffee" })
    }

    @Test fun aCheckInTheWidgetWorksOfflineAndReplaysWithAnUndo() = runBlocking {
        val shop = fake.note("checklist", "Shopping", listOf("milk", "eggs"))
        g.notes.repo.pull()
        val milk = fake.items[shop.id]!![0].id
        fake.offline = true
        // checkitem.check from the widget, with no signal: on the phone at once, queued for the server.
        WidgetActions.check(g, shop.id, milk, true)
        assertTrue(g.notes.repo.itemsNow(shop.id).first { it.id == milk }.checked)
        assertEquals(1, g.notes.repo.ops.first().size)
        g.notes.repo.drain()
        assertFalse(fake.items[shop.id]!![0].checked)
        // The header's Undo chip, for 7 s.
        val m = WidgetModels.checklist(g.notes.repo, shop.id, WidgetUndo.get(shop.id), withUsual = false) as WidgetModel.ChecklistModel
        assertNotNull(m.undo)
        assertEquals(1, m.checked)
        runGlanceAppWidgetUnitTest {
            setContext(ctx)
            provideComposable { ChecklistContent(m, 7) }
            onNode(hasText("Undo").and(hasRunCallbackClickAction<UndoCheckAction>(actionParametersOf(KEY_LIST to shop.id))))
                .assertExists()
        }
        assertNull(WidgetUndo.get(shop.id, now = AppClock.millis() + 8_000))
        // Back online: the queued check lands, with the device time of the tap.
        fake.offline = false
        g.notes.repo.drain()
        assertTrue(fake.items[shop.id]!![0].checked)
        val patch = fake.log.last { it.method == "PATCH" }
        assertTrue(patch.body!!.containsKey("device_at"))
        // checkitem.uncheck through Undo.
        WidgetActions.undo(g, shop.id)
        g.notes.repo.drain()
        assertFalse(fake.items[shop.id]!![0].checked)
    }

    @Test fun theNoteAndTableWidgets() = runBlocking {
        val plan = fake.note("text", "Plan").also { it.body = "paint the shed" }
        val t = fake.table("Expenses", listOf("Item" to "text", "Amount" to "number", "Paid" to "checkbox"),
            listOf(mapOf("c0" to "Rent", "c1" to "900"), mapOf("c0" to "Pens", "c1" to "4")))
        g.notes.repo.pull()
        val note = WidgetModels.note(g.notes.repo, plan.id)
        val table = WidgetModels.table(g.notes.repo, t.id, listOf("c1")) as WidgetModel.TableModel
        assertEquals(listOf("Item", "Amount"), table.columns)
        assertEquals(listOf("Rent", "Pens"), table.rows.map { it.second[0] })
        runGlanceAppWidgetUnitTest {
            setContext(ctx)
            provideComposable { NoteContent(note, 8) }
            onNode(hasText("paint the shed")).assertExists()
        }
        runGlanceAppWidgetUnitTest {
            setContext(ctx)
            provideComposable { TableContent(table, 9) }
            onNode(hasText("Rent")).assertExists()
            onNode(hasText("900")).assertExists()
            // table.stats: the footer (the row count until a view's aggregates have been read online).
            onNode(hasText("2 rows")).assertExists()
            // row.add: "+" opens the row form.
            onNode(hasContentDescription("Add row to Expenses").and(hasClickAction())).assertExists()
        }
    }

    @Test fun theCaptureWidgetAndThePicker() {
        runGlanceAppWidgetUnitTest {
            setContext(ctx)
            provideComposable { CaptureContent(true, "n1", "Shopping") }
            // note.create: each button opens quick capture.
            onNode(hasContentDescription("New note").and(hasClickAction())).assertExists()
            onNode(hasContentDescription("New list").and(hasClickAction())).assertExists()
            onNode(hasContentDescription("Add to Shopping").and(hasClickAction())).assertExists()
            onNode(hasContentDescription("Dictate").and(hasClickAction())).assertExists()
        }
        runGlanceAppWidgetUnitTest {
            setContext(ctx)
            provideComposable { ChecklistContent(WidgetModel.Message("Tap to choose a list", true), 3) }
            onNode(hasClickAction().and(hasAnyDescendant(hasText("Tap to choose a list")))).assertExists()
        }
        assertEquals(WidgetKind.CHECKLIST, WidgetPickActivity.kindOf("io.github.murtazanasir.mailr.notesapp.widget.ChecklistWidgetReceiver"))
        assertEquals(WidgetKind.TABLE, WidgetPickActivity.kindOf("io.github.murtazanasir.mailr.notesapp.widget.TableWidgetReceiver"))
        g.prefs.setWidget(5, WidgetTarget("table", "t1", listOf("c1", "c2")))
        assertEquals(WidgetTarget("table", "t1", listOf("c1", "c2")), g.prefs.widget(5))
    }
}
