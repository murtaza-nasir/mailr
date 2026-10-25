// ACT-SURFACES: android.notes
package io.github.murtazanasir.mailr.notes

import android.app.Application
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextReplacement
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.TestGraph
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.notes.data.NoteRepeat
import io.github.murtazanasir.mailr.notes.data.NotesRepository
import io.github.murtazanasir.mailr.notes.data.OpState
import io.github.murtazanasir.mailr.notes.data.RepeatRule
import io.github.murtazanasir.mailr.notes.ui.NotesPage
import io.github.murtazanasir.mailr.notes.ui.NotesRoute
import io.github.murtazanasir.mailr.notes.ui.NotesViewModel
import io.github.murtazanasir.mailr.notes.ui.localOnlyLock
import io.github.murtazanasir.mailr.notes.ui.presenceLine
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.testing.serverSees
import io.github.murtazanasir.mailr.testing.waitForNode
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.getUnclippedBoundsInRoot
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.height
import androidx.compose.ui.unit.width
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.time.DayOfWeek
import java.time.Instant
import java.time.ZoneId

/**
 * Household lists in the mailr app's Notes (`android.notes`): note.local_only and table.local_only
 * (the switch in the ⋮ menus, on and locked for an editor who is not the owner, and the server's 403),
 * note.repeat (the repeat dialog, the quiet line, the rule's own logic), note.presence and
 * table.presence (heartbeats, "Cleo is in this list", the answer to a hello, "left" on close), the
 * name on a row someone else just checked, and the lists offline (the usual items kept on the phone,
 * the editor's offline line).
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class HouseholdListsTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()
    private lateinit var fake: FakeNotesServer
    private lateinit var g: AppGraph
    private lateinit var vm: NotesViewModel
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
        vm = graphs.vm { NotesViewModel(repo) }
    }

    private fun show() {
        compose.setContent { MailrTheme(ThemeMode.LIGHT, dynamic = false) { NotesRoute(vm) } }
        compose.waitForIdle()
    }

    private fun waitText(t: String) = compose.waitForNode(hasText(t, substring = true))

    private fun serverSees(what: String, check: () -> Boolean) = compose.serverSees(repo, what, check)

    private fun openMenu() {
        compose.onNodeWithContentDescription("More options").performClick()
        compose.waitForIdle()
    }

    // ================================================================ Local models only

    @Test fun theOwnerTurnsLocalModelsOnlyOnAndOff() {
        val shop = fake.note("checklist", "Shopping", listOf("milk"))
        runBlocking { repo.pull() }
        show()
        compose.runOnIdle { vm.open(NotesPage.OpenNote(shop.id)) }
        waitText("milk")
        // note.local_only: on from the ⋮ menu, through the queue.
        openMenu()
        compose.onNodeWithText("Keep away from hosted models").assertExists()
        compose.onNodeWithTag("local-only").performClick()
        serverSees("note.local_only on") { fake.notes[shop.id]!!.localOnly }
        assertTrue(runBlocking { repo.noteNow(shop.id)!!.localOnly })
        // And off again: the owner may.
        openMenu()
        compose.onNodeWithText("Never sent to hosted models").assertExists()
        compose.onNodeWithTag("local-only").performClick()
        serverSees("note.local_only off") { !fake.notes[shop.id]!!.localOnly }
    }

    @Test fun anEditorSeesItLockedOnWithTheReason() {
        val shop = fake.note("checklist", "Shopping", listOf("milk"))
        shop.level = "edit"; shop.localOnly = true; shop.canLowerLocalOnly = false
        runBlocking { repo.pull() }
        assertFalse(runBlocking { repo.noteNow(shop.id)!!.canLowerLocalOnly })
        assertEquals("Only the owner can turn this off", localOnlyLock(on = true, canLower = false))
        assertNull(localOnlyLock(on = false, canLower = false))
        show()
        compose.runOnIdle { vm.open(NotesPage.OpenNote(shop.id)) }
        waitText("milk")
        openMenu()
        compose.onNodeWithText("Only the owner can turn this off").assertExists()
        compose.onNodeWithTag("local-only").assertIsNotEnabled()
        // Had the phone sent "off" anyway (an older copy), the server's 403 shows as a refused write.
        runBlocking {
            repo.setLocalOnly(shop.id, false)
            repo.drain()
            val op = repo.ops.first().single()
            assertEquals(OpState.FAILED, op.state)
            assertEquals("Only the owner can turn Local models only off", op.error)
        }
        assertTrue(fake.notes[shop.id]!!.localOnly)
    }

    @Test fun aTableHasTheSwitchInItsMenu() {
        val t = fake.table("Chores", listOf("Task" to "text", "Who" to "text"))
        runBlocking { repo.pull() }
        show()
        compose.runOnIdle { vm.open(NotesPage.OpenTable(t.id)) }
        waitText("Chores")
        // table.local_only from the table's ⋮ menu.
        openMenu()
        compose.onNodeWithText("Keep away from hosted models").assertExists()
        compose.onNodeWithTag("local-only").performClick()
        serverSees("table.local_only") { fake.tables[t.id]!!.localOnly }
        // An editor of a table that is on: locked.
        t.canLowerLocalOnly = false
        runBlocking { repo.refreshTable(t.id) }
        compose.eventually("the table's lock") { runBlocking { repo.tableNow(t.id)!!.canLowerLocalOnly == false } }
        openMenu()
        compose.onNodeWithText("Only the owner can turn this off").assertExists()
        compose.onNodeWithTag("local-only").assertIsNotEnabled()
    }

    // ================================================================ repeat

    @Test fun aListRepeatsWeeklyOnADay() {
        val chores = fake.note("checklist", "Chores", listOf("vacuum", "laundry"))
        runBlocking { repo.pull() }
        show()
        compose.runOnIdle { vm.open(NotesPage.OpenNote(chores.id)) }
        waitText("vacuum")
        // note.repeat: ⋮ → Repeat… → Weekly on Sunday → Save.
        openMenu()
        compose.onNodeWithText("Repeat…").performClick()
        compose.waitForNode(hasTestTag("repeat-sheet"))
        compose.onNodeWithTag("repeat-weekly").performClick()
        compose.onNodeWithTag("repeat-day-SU").performClick()
        compose.onNodeWithTag("repeat-save").performClick()
        serverSees("note.repeat") { fake.notes[chores.id]!!.repeat == "FREQ=WEEKLY;BYDAY=SU" }
        // The quiet line under the title.
        compose.waitForNode(hasTestTag("repeat-line"))
        waitText("Repeats weekly on Sunday · next")
        // Every 3 days, then off.
        openMenu()
        compose.onNodeWithText("Repeat: weekly on Sunday").performClick()
        compose.onNodeWithTag("repeat-daily").performClick()
        compose.onNodeWithTag("repeat-days").performTextReplacement("3")
        compose.onNodeWithTag("repeat-save").performClick()
        serverSees("every 3 days") { fake.notes[chores.id]!!.repeat == "FREQ=DAILY;INTERVAL=3" }
        runBlocking { repo.setRepeat(chores.id, null); repo.drain() }
        assertNull(fake.notes[chores.id]!!.repeat)
        assertNull(runBlocking { repo.noteNow(chores.id)!!.repeat })
    }

    @Test fun theRepeatRuleReadsAndPlansAsTheServerDoes() {
        assertEquals(RepeatRule.Weekly(DayOfWeek.SUNDAY), RepeatRule.parse("FREQ=WEEKLY;BYDAY=SU"))
        assertEquals(RepeatRule.Weekly(DayOfWeek.MONDAY, 2), RepeatRule.parse("RRULE:FREQ=WEEKLY;BYDAY=MO;INTERVAL=2"))
        assertEquals(RepeatRule.Daily(3), RepeatRule.parse("FREQ=DAILY;INTERVAL=3"))
        assertNull(RepeatRule.parse("FREQ=MONTHLY"))
        assertNull(RepeatRule.parse(null))
        assertEquals("FREQ=WEEKLY;BYDAY=SU", RepeatRule.format(RepeatRule.Weekly(DayOfWeek.SUNDAY)))
        assertEquals("FREQ=DAILY;INTERVAL=3", RepeatRule.format(RepeatRule.Daily(3)))
        assertEquals("Repeats every 2 weeks on Monday", RepeatRule.describe(RepeatRule.Weekly(DayOfWeek.MONDAY, 2)))
        assertEquals("Repeats every day", RepeatRule.describe(RepeatRule.Daily(1)))
        val utc = ZoneId.of("UTC")
        // Tuesday 6 Oct 2026: the next Sunday at 04:00; never today (a Tuesday rule waits a week).
        val now = Instant.parse("2026-10-06T10:00:00Z")
        assertEquals(Instant.parse("2026-10-11T04:00:00Z"), RepeatRule.first(RepeatRule.Weekly(DayOfWeek.SUNDAY), now, utc))
        assertEquals(Instant.parse("2026-10-13T04:00:00Z"), RepeatRule.first(RepeatRule.Weekly(DayOfWeek.TUESDAY), now, utc))
        assertEquals(Instant.parse("2026-10-09T04:00:00Z"), RepeatRule.first(RepeatRule.Daily(3), now, utc))
        assertEquals("Repeats weekly on Sunday · next Sun 11 Oct",
            RepeatRule.line(NoteRepeat("FREQ=WEEKLY;BYDAY=SU", "2026-10-11T04:00:00+00:00"), utc))
    }

    // ================================================================ presence

    private fun presenceEvent(id: String, state: String, hello: Boolean = false, name: String = "Cleo", pid: Int = 2, kind: String = "note") =
        buildJsonObject {
            put("type", "presence"); put("kind", kind); put("id", id); put("state", state); put("origin", "cleo-phone")
            put("person", buildJsonObject { put("id", pid); put("name", name) })
            if (hello) put("hello", true)
        }

    @Test fun aSharedListSaysItIsOpenAndShowsWhoElseIs() {
        val shop = fake.note("checklist", "Shopping", listOf("milk", "eggs"))
        shop.sharedCount = 1
        val mine = fake.note("checklist", "Mine", listOf("socks"))
        runBlocking { repo.pull() }
        show()
        // An item nobody else sees sends nothing.
        compose.runOnIdle { vm.open(NotesPage.OpenNote(mine.id)) }
        waitText("socks")
        compose.runOnIdle { vm.back() }
        // note.presence: the shared list says "here" with hello when it opens.
        compose.runOnIdle { vm.open(NotesPage.OpenNote(shop.id)) }
        waitText("milk")
        compose.eventually("here with hello") { "note:${shop.id}:here:hello" in fake.presence }
        assertTrue(fake.presence.none { it.startsWith("note:${mine.id}") })
        // Cleo opens it too: "Cleo is in this list"; her hello gets an answer at once.
        compose.runOnIdle { repo.onEvent("presence", presenceEvent(shop.id, "here", hello = true)) }
        waitText("Cleo is in this list")
        compose.eventually("the answer to her hello") { fake.presence.count { it == "note:${shop.id}:here" } >= 1 }
        assertEquals(listOf("Cleo"), repo.presentNow(shop.id))
        compose.runOnIdle { repo.onEvent("presence", presenceEvent(shop.id, "here", name = "Dana", pid = 3)) }
        waitText("Cleo and Dana are in this list")
        // She leaves.
        compose.runOnIdle { repo.onEvent("presence", presenceEvent(shop.id, "left")) }
        waitText("Dana is in this list")
        // The list closes: "left".
        compose.runOnIdle { vm.back() }
        compose.eventually("left") { "note:${shop.id}:left" in fake.presence }
        assertEquals("Cleo, Dana and Olive are in this list", presenceLine(listOf("Cleo", "Dana", "Olive"), "list"))
    }

    @Test fun aSharedTableShowsWhoIsInIt() {
        val t = fake.table("Chores", listOf("Task" to "text"))
        t.level = "edit"
        runBlocking { repo.pull() }
        show()
        compose.runOnIdle { vm.open(NotesPage.OpenTable(t.id)) }
        waitText("Chores")
        // table.presence
        compose.eventually("here with hello") { "table:${t.id}:here:hello" in fake.presence }
        compose.runOnIdle { repo.onEvent("presence", presenceEvent(t.id, "here", kind = "table")) }
        waitText("Cleo is in this table")
    }

    @Test fun aRowSomeoneElseCheckedShowsHerName() {
        val shop = fake.note("checklist", "Shopping", listOf("milk", "eggs"))
        shop.sharedCount = 1
        runBlocking { repo.pull() }
        // Read the fold open: a checked row shows below it.
        show()
        compose.runOnIdle { vm.open(NotesPage.OpenNote(shop.id)) }
        waitText("milk")
        val milk = fake.items[shop.id]!!.first { it.text == "milk" }
        milk.checked = true; milk.version++
        compose.runOnIdle {
            repo.onEvent("item", buildJsonObject {
                put("type", "item"); put("kind", "checkitem"); put("id", milk.id); put("note", shop.id); put("origin", "cleo-phone")
                put("author", buildJsonObject { put("id", 2); put("name", "Cleo") })
            })
        }
        compose.eventually("the tick is marked") { repo.ticks.value[milk.id]?.first == "Cleo" }
        // In the closed fold: her name and the item on the fold's line.
        waitText("✓ Cleo: milk")
        // The name shows for 4 s (NotesRepository.TICK_MS) of the graph's time, which moves only here.
        graphs.time.advanceBy(NotesRepository.TICK_MS - 1)
        compose.waitForIdle()
        assertEquals("Cleo", repo.ticks.value[milk.id]?.first)
        graphs.time.advanceBy(1)
        compose.eventually("the tick's label goes after 4 s") { milk.id !in repo.ticks.value }
        compose.eventually("the fold's line no longer names her") { compose.onAllNodes(hasText("✓ Cleo", substring = true)).fetchSemanticsNodes().isEmpty() }
    }

    // ================================================================ lists offline

    @Test fun usualItemsStayOnThePhoneForOffline() = runBlocking {
        val shop = fake.note("checklist", "Shopping", listOf("milk"))
        fake.usual += listOf("coffee", "bananas", "milk")
        repo.pull()
        assertEquals(listOf("coffee", "bananas", "milk"), repo.usual(shop.id)!!.items.map { it.text })
        fake.offline = true
        // Offline: the last suggestions, without what is already on the list unchecked.
        val kept = repo.usual(shop.id)!!
        assertEquals(listOf("coffee", "bananas"), kept.items.map { it.text })
        // checkitem.usual_add offline: on the list at once, out of the kept strip, queued.
        repo.addUsual(shop.id, kept.items.first())
        assertTrue(repo.itemsNow(shop.id).any { it.text == "coffee" })
        assertEquals(listOf("bananas"), repo.cachedUsual(shop.id)!!.items.map { it.text })
        assertEquals(1, repo.ops.first().size)
        fake.offline = false
        repo.drain()
        assertTrue(fake.log.any { it.path == "notes/${shop.id}/items" && it.body!!["usual"].toString().contains("coffee") })
        assertEquals("coffee", fake.items[shop.id]!!.last().text)
        Unit
    }

    @Test fun theEditorSaysWhenItIsOffline() {
        val shop = fake.note("checklist", "Shopping", listOf("milk"))
        runBlocking { repo.pull() }
        fake.offline = true
        show()
        compose.runOnIdle { vm.open(NotesPage.OpenNote(shop.id)) }
        waitText("milk")
        runBlocking { repo.addItems(shop.id, "eggs"); repo.drain() }
        waitText("Offline · 1 change waiting")
        fake.offline = false
        runBlocking { repo.drain() }
        compose.eventually("back online") { compose.onAllNodes(hasTestTag("offline-line")).fetchSemanticsNodes().isEmpty() }
        assertEquals(listOf("milk", "eggs"), fake.items[shop.id]!!.map { it.text })
    }

    // ================================================================ touch targets

    @Test fun theUsualChipsHideButtonIsA48dpTarget() {
        // An empty list shows the usual strip without focusing the add line.
        val shop = fake.note("checklist", "Shopping", emptyList()).apply { suggestUsual = true }
        fake.usual += listOf("coffee")
        runBlocking { repo.pull() }
        show()
        compose.runOnIdle { vm.open(NotesPage.OpenNote(shop.id)) }
        compose.waitForNode(hasContentDescription("Hide coffee"))
        val b = compose.onNode(hasContentDescription("Hide coffee")).getUnclippedBoundsInRoot()
        assertTrue("hide target ${b.width} x ${b.height}", b.width >= 48.dp && b.height >= 48.dp)
    }
}
