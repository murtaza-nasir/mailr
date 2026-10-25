// ACT-SURFACES: android.notesshare android.noteswidget
package io.github.murtazanasir.mailr.notesapp

import android.app.Application
import io.github.murtazanasir.mailr.notes.data.Column
import io.github.murtazanasir.mailr.notes.data.DataTable
import io.github.murtazanasir.mailr.notes.grammar.ItemGrammar
import io.github.murtazanasir.mailr.notesapp.capture.CaptureLogic
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Quick capture and the share target without the screens: the grammar in capture mode picks the
 * list (note.add_items), shared text becomes a note (note.create) and tab- or comma-separated text
 * becomes rows (row.add).
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class CaptureLogicTest {
    private val shop = ItemGrammar.ListInfo("n1", "Shopping", listOf("Costco"))
    private val pack = ItemGrammar.ListInfo("n2", "Packing", emptyList())

    @Test fun aListTokenPicksTheListAndLeavesTheItems() {
        val p = CaptureLogic.plan("#shop 2 cans of tomatoes, milk", listOf(shop, pack), pack)
        assertEquals("n1", p.list!!.id)
        assertEquals(listOf("tomatoes", "milk"), p.items.map { it.text })
        assertTrue(!p.lines.contains("#shop"))
        assertEquals("Add 2 items to Shopping", CaptureLogic.summary(p))
    }

    @Test fun withoutATokenTheChosenListTakesThem() {
        val p = CaptureLogic.plan("Costco: eggs", listOf(shop, pack), shop)
        assertEquals("n1", p.list!!.id)
        assertEquals("Costco", p.items.single().section)
        assertEquals("Add eggs to Shopping", CaptureLogic.summary(p))
    }

    @Test fun anUnknownListIsCreated() {
        val p = CaptureLogic.plan("#camping tent, stove", listOf(shop), null)
        assertTrue(p.list!!.create)
        assertEquals("Create the list camping with 2 items", CaptureLogic.summary(p))
    }

    @Test fun recentListsComeFirst() {
        val ordered = CaptureLogic.ordered(listOf(shop, pack), listOf("note:n2", "table:t1"), null)
        assertEquals(listOf("n2", "n1"), ordered.map { it.id })
    }

    @Test fun sharedTextAndLinks() {
        val link = CaptureLogic.shared("https://example.com/recipe", "Lemon cake")
        assertEquals("Lemon cake", link.title)
        assertEquals("https://example.com/recipe", link.body)
        val titled = CaptureLogic.shared("Lemon cake https://example.com/recipe", null)
        assertEquals("Lemon cake", titled.title)
        assertEquals("https://example.com/recipe", titled.link)
        val lines = CaptureLogic.shared("Trip ideas\nLisbon\nPorto", null)
        assertEquals("Trip ideas", lines.title)
        assertEquals("Lisbon\nPorto", lines.body)
        val one = CaptureLogic.shared("Call the plumber", null)
        assertEquals("", one.title)
        assertNull(one.link)
    }

    @Test fun rowsFromTabsOrCommas() {
        val t = DataTable("t1", name = "Expenses", columns = listOf(Column("c0", "Item"), Column("c1", "Amount", "number")))
        assertEquals(listOf(listOf("Rent", "900"), listOf("Pens", "4")), CaptureLogic.rows("Item\tAmount\nRent\t900\nPens\t4\n", t))
        assertEquals(listOf(listOf("Ink, black", "12")), CaptureLogic.rows("\"Ink, black\",12", t))
        assertEquals(emptyList<List<String>>(), CaptureLogic.rows("  \n", t))
    }
}
