// ACT-SURFACES: android.notes
package io.github.murtazanasir.mailr.notes

import android.app.Application
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.state.ToggleableState
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollToKey
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.swipeUp
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.TestGraph
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.notes.data.Checklist
import io.github.murtazanasir.mailr.notes.data.Item
import io.github.murtazanasir.mailr.notes.data.TopGroup
import io.github.murtazanasir.mailr.notes.ui.LINK_HIGHLIGHT_MS
import io.github.murtazanasir.mailr.notes.ui.LinkMarked
import io.github.murtazanasir.mailr.notes.ui.NotesPage
import io.github.murtazanasir.mailr.notes.ui.NotesRoute
import io.github.murtazanasir.mailr.notes.ui.NotesViewModel
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.testing.serverSees
import io.github.murtazanasir.mailr.testing.waitForNode
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
import org.robolectric.annotation.Config

/**
 * The open checklist keeps still while the user works through it: the item a link points to is marked
 * for 3 s or until the first touch; a checked row stays in its place (ticked, struck through) until the
 * note is opened again; Clear checked and Undo cover those rows; a change from elsewhere does not move
 * the row at the top of the screen.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class ChecklistInPlaceTest {
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

    private fun open(page: NotesPage) = compose.runOnIdle { vm.open(page) }
    private fun reopen(page: NotesPage) {
        compose.runOnIdle { vm.back() }
        compose.waitForNode(hasText("Shopping"))
        open(page)
    }

    private fun id(note: FakeNotesServer.N, text: String) = fake.items[note.id]!!.first { it.text == text }.id
    private fun box(text: String) = compose.onNode(hasContentDescription(text) and SemanticsMatcher.keyIsDefined(SemanticsProperties.ToggleableState))
    private fun marked(itemId: String) = compose.onNodeWithTag("item-$itemId").fetchSemanticsNode().config.getOrNull(LinkMarked) == true
    private fun top(tag: String) = compose.onNodeWithTag(tag).fetchSemanticsNode().boundsInRoot.top
    private fun shows(tag: String) = compose.onAllNodes(hasTestTag(tag)).fetchSemanticsNodes().isNotEmpty()
    private fun serverSees(what: String, check: () -> Boolean) = compose.serverSees(g.notes.repo, what, check)

    @Test fun theLinkedItemIsMarkedForThreeSeconds() {
        val shop = fake.note("checklist", "Shopping", listOf("milk", "eggs", "bread"))
        runBlocking { g.notes.repo.pull() }
        show()
        val eggs = id(shop, "eggs")
        open(NotesPage.OpenNote(shop.id, eggs))
        compose.waitForNode(hasTestTag("item-$eggs"))
        assertTrue("marked on open", marked(eggs))
        assertTrue("only the linked row", !marked(id(shop, "milk")))
        // The graph's virtual time: the mark lasts LINK_HIGHLIGHT_MS of it, however slow the machine.
        graphs.time.advanceBy(LINK_HIGHLIGHT_MS - 1)
        compose.waitForIdle()
        assertTrue("still marked just before 3 s", marked(eggs))
        graphs.time.advanceBy(1)
        compose.eventually("the mark goes after 3 s") { !marked(eggs) }
    }

    @Test fun theLinkedItemsMarkGoesAtTheFirstTouchOrScroll() {
        val shop = fake.note("checklist", "Shopping", listOf("milk", "eggs", "bread"))
        runBlocking { g.notes.repo.pull() }
        show()
        val eggs = id(shop, "eggs")
        val page = NotesPage.OpenNote(shop.id, eggs)
        open(page)
        compose.waitForNode(hasTestTag("item-$eggs"))
        assertTrue(marked(eggs))
        // A tap anywhere in the note (here the title) clears it.
        compose.onNodeWithTag("note-title").performClick()
        compose.eventually("a tap clears the mark") { !marked(eggs) }
        // Opened again from the link: marked again; a scroll gesture clears it.
        reopen(page)
        compose.waitForNode(hasTestTag("item-$eggs"))
        assertTrue("marked again on the next opening", marked(eggs))
        compose.onNodeWithTag("checklist").performTouchInput { swipeUp() }
        compose.eventually("a scroll clears the mark") { !shows("item-$eggs") || !marked(eggs) }
    }

    @Test fun aCheckedItemStaysInPlaceUntilTheNoteIsOpenedAgain() {
        val shop = fake.note("checklist", "Shopping", listOf("milk", "eggs", "bread"))
        runBlocking { g.notes.repo.pull() }
        show()
        open(NotesPage.OpenNote(shop.id))
        val (milk, eggs, bread) = listOf("milk", "eggs", "bread").map { id(shop, it) }
        compose.waitForNode(hasTestTag("item-$bread"))
        val before = listOf(milk, eggs, bread).map { top("item-$it") }
        box("eggs").performClick()
        compose.waitForNode(hasText("1 checked"))
        serverSees("checkitem.check") { fake.items[shop.id]!!.first { it.id == eggs }.checked }
        // Ticked where it was: the same rows at the same heights, and the fold counts it.
        assertEquals(before, listOf(milk, eggs, bread).map { top("item-$it") })
        assertEquals(ToggleableState.On, box("eggs").fetchSemanticsNode().config[SemanticsProperties.ToggleableState])
        compose.onNodeWithText("1 checked").performClick()
        compose.waitForIdle()
        assertTrue("an item kept in place is not listed in the fold too", !shows("checked-$eggs"))
        // Opened again: it is in the fold.
        reopen(NotesPage.OpenNote(shop.id))
        compose.waitForNode(hasTestTag("item-$bread"))
        assertTrue(!shows("item-$eggs"))
        compose.onNodeWithText("1 checked").performClick()
        compose.waitForNode(hasTestTag("checked-$eggs"))
    }

    @Test fun anInPlaceCheckUndoesAndUnchecksWhereItIs() {
        val shop = fake.note("checklist", "Shopping", listOf("milk", "eggs", "bread"))
        runBlocking { g.notes.repo.pull() }
        show()
        open(NotesPage.OpenNote(shop.id))
        val eggs = id(shop, "eggs")
        compose.waitForNode(hasTestTag("item-$eggs"))
        val y = top("item-$eggs")
        box("eggs").performClick()
        compose.waitForNode(hasText("1 checked"))
        // Undo from the top bar: unchecked in the same place, and the fold goes.
        compose.onNodeWithTag("editor-undo").performClick()
        compose.eventually("eggs unchecked") { compose.onAllNodes(hasText("1 checked")).fetchSemanticsNodes().isEmpty() }
        assertEquals(ToggleableState.Off, box("eggs").fetchSemanticsNode().config[SemanticsProperties.ToggleableState])
        assertEquals(y, top("item-$eggs"))
        // Checked and unchecked again with its own box.
        box("eggs").performClick()
        compose.waitForNode(hasText("1 checked"))
        box("eggs").performClick()
        compose.eventually("unchecked with its box") { compose.onAllNodes(hasText("1 checked")).fetchSemanticsNodes().isEmpty() }
        assertEquals(y, top("item-$eggs"))
        serverSees("eggs unchecked on the server") { fake.items[shop.id]!!.none { it.checked } && runBlocking { g.notes.repo.ops.first() }.isEmpty() }
    }

    @Test fun clearCheckedRemovesTheItemsCheckedInPlaceToo() {
        val shop = fake.note("checklist", "Shopping", listOf("milk", "eggs", "bread", "salt"))
        fake.items[shop.id]!!.first { it.text == "salt" }.apply { checked = true; checkedAt = "2026-10-01T09:00:00Z" }
        runBlocking { g.notes.repo.pull() }
        show()
        open(NotesPage.OpenNote(shop.id))
        val eggs = id(shop, "eggs")
        compose.waitForNode(hasText("1 checked"))
        box("eggs").performClick()
        compose.waitForNode(hasText("2 checked"))
        compose.onNodeWithText("Clear checked").performClick()
        serverSees("note.clear_checked") { fake.items[shop.id]!!.map { it.text } == listOf("milk", "bread") }
        compose.eventually("the row checked in place has gone") { !shows("item-$eggs") }
        assertTrue(compose.onAllNodes(hasText("checked", substring = true)).fetchSemanticsNodes().none {
            it.config.getOrNull(SemanticsProperties.Text)?.any { t -> t.text.endsWith(" checked") } == true
        })
    }

    @Test fun aChangeFromElsewhereDoesNotMoveTheTopRow() {
        val names = (1..40).map { "item %02d".format(it) }
        val shop = fake.note("checklist", "Shopping", names)
        runBlocking { g.notes.repo.pull() }
        show()
        open(NotesPage.OpenNote(shop.id))
        val anchor = id(shop, "item 20")
        compose.waitForNode(hasTestTag("item-${id(shop, "item 01")}"))
        compose.onNodeWithTag("checklist").performScrollToKey("i-$anchor")
        compose.waitForIdle()
        val y = top("item-$anchor")
        // Another device checks two items above it and one below, deletes one above and adds one at the top.
        val list = fake.items[shop.id]!!
        list.filter { it.text in setOf("item 05", "item 12", "item 25") }.forEach { it.checked = true; it.checkedAt = "2026-10-02T10:00:00Z"; it.version++ }
        list.removeAll { it.text == "item 03" }
        list.add(0, FakeNotesServer.I("remote-new", "from Dana", order = "0"))
        runBlocking { g.notes.repo.refreshNote(shop.id) }
        compose.eventually("the change is on the phone") {
            runBlocking { g.notes.repo.itemsNow(shop.id) }.let { l -> l.any { it.text == "from Dana" } && l.count { it.checked } == 3 }
        }
        compose.waitForIdle()
        assertEquals("the row at the top stays where it was", y, top("item-$anchor"))
        // The items checked elsewhere that this screen showed stay in place, ticked.
        compose.onNodeWithTag("checklist").performScrollToKey("i-${id(shop, "item 25")}")
        assertEquals(ToggleableState.On, box("item 25").fetchSemanticsNode().config[SemanticsProperties.ToggleableState])
    }

    /** Opened from a link, the list scrolled to the linked item once; a later check elsewhere does not pull it back there. */
    @Test fun aCheckAfterALinkDoesNotScrollBackToTheLinkedItem() {
        val names = (1..40).map { "item %02d".format(it) }
        val shop = fake.note("checklist", "Shopping", names)
        runBlocking { g.notes.repo.pull() }
        show()
        val linked = id(shop, "item 30")
        open(NotesPage.OpenNote(shop.id, linked))
        compose.waitForNode(hasTestTag("item-$linked"))
        val anchor = id(shop, "item 05")
        compose.onNodeWithTag("checklist").performScrollToKey("i-$anchor")
        compose.waitForIdle()
        val y = top("item-$anchor")
        box("item 06").performClick()
        serverSees("item 06 checked") { fake.items[shop.id]!!.first { it.text == "item 06" }.checked }
        compose.waitForIdle()
        assertEquals("no jump back to the linked item", y, top("item-$anchor"))
        assertTrue(!shows("item-$linked"))
    }

    @Test fun theLayoutKeepsStayingItemsInTheirGroupAndCountsThem() {
        fun item(id: String, order: String, checked: Boolean = false) =
            Item(id = id, text = id, key = id, order = order, checked = checked, checkedAt = if (checked) "2026-10-02T10:00:00Z" else null)
        val items = listOf(item("a", "1"), item("b", "2", checked = true), item("c", "3", checked = true))
        val l = Checklist.layout(items, emptyList(), TopGroup("manual"), checkedAtBottom = true, stay = setOf("b"))
        assertEquals(listOf("a", "b"), l.groups.single().items.map { it.id })
        assertEquals(listOf("c"), l.checked.map { it.id })
        assertEquals(2, l.checkedCount)
        val plain = Checklist.layout(items, emptyList(), TopGroup("manual"), checkedAtBottom = true)
        assertEquals(listOf("a"), plain.groups.single().items.map { it.id })
        assertEquals(2, plain.checkedCount)
    }
}
