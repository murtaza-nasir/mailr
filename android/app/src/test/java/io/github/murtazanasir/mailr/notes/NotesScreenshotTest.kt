package io.github.murtazanasir.mailr.notes

import io.github.murtazanasir.mailr.DOCS_SHOT
import android.app.Application
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.isDialog
import androidx.compose.ui.test.onNodeWithTag
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performSemanticsAction
import androidx.compose.ui.test.performTextInput
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.TestGraph
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.notes.ui.NotesPage
import io.github.murtazanasir.mailr.notes.ui.NotesRoute
import io.github.murtazanasir.mailr.notes.ui.NotesViewModel
import io.github.murtazanasir.mailr.testing.until
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * The Notes screens of the mailr app on a phone, light and dark, and at a large font.
 * Output: app/screenshots/notes-<theme>-<screen>.png
 */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class NotesScreenshotTest(private val dark: Boolean) {
    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun params(): List<Array<Any>> = listOf(arrayOf<Any>(false), arrayOf<Any>(true))
    }

    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()
    private lateinit var fake: FakeNotesServer
    private lateinit var g: AppGraph
    private lateinit var vm: NotesViewModel
    private val theme get() = if (dark) "dark" else "light"

    @Before fun setUp() {
        RuntimeEnvironment.setQualifiers("w411dp-h891dp-port-${if (dark) "night" else "notnight"}-xxhdpi")
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

    private fun seed(): FakeNotesServer.N {
        val shop = fake.note("checklist", "Shopping", listOf("milk", "flour", "bread"), sections = listOf("Costco", "Corner market"))
        shop.color = "sage"; shop.pinned = true; shop.shopping = true
        val items = fake.items[shop.id]!!
        items[0].qty = buildJsonObject { put("amount", "2"); put("unit", JsonPrimitive(null as String?)) }
        items[1].qty = buildJsonObject { put("amount", "500"); put("unit", "g") }
        val costco = fake.sections[shop.id]!![0].id
        val corner = fake.sections[shop.id]!![1].id
        items += FakeNotesServer.I("0192aaaa-0000-7000-8000-000000000011", "paper towels", costco, order = "b1")
        items += FakeNotesServer.I("0192aaaa-0000-7000-8000-000000000012", "avocados", costco, order = "b2",
            qty = buildJsonObject { put("amount", "3"); put("unit", JsonPrimitive(null as String?)) }, extra = "the ripe ones")
        items += FakeNotesServer.I("0192aaaa-0000-7000-8000-000000000013", "limes", corner, order = "c1")
        items += FakeNotesServer.I("0192aaaa-0000-7000-8000-000000000014", "eggs", null, order = "a9", checked = true,
            checkedAt = "2026-10-05T09:00:00+00:00")
        fake.note("text", "Trip ideas").also { it.body = "Lake Tahoe in October\nBook the cabin early\nAsk Sam about the boat"; it.labels = listOf("Travel") }
        fake.note("checklist", "Packing list", listOf("socks", "charger", "passport")).also { it.color = "peacock" }
        fake.note("text", "Gate code").also { it.body = "4417 at the side door"; it.color = "banana" }
        fake.table("Workouts", listOf("Exercise" to "text", "Weight" to "number", "Date" to "date"), listOf(
            mapOf("c0" to "Squat", "c1" to "82.5", "c2" to "2026-10-03"), mapOf("c0" to "Bench press", "c1" to "60", "c2" to "2026-10-03"),
            mapOf("c0" to "Deadlift", "c1" to "100", "c2" to "2026-10-04")))
        fake.labels += listOf("Travel", "Home")
        runBlocking { g.notes.repo.pull() }
        return shop
    }

    private fun show() {
        compose.setContent { MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) { NotesRoute(vm) } }
    }

    // 30 s: the full parallel suite (the release gate) once ran this class past 15 s (8 Oct 2026).
    private fun waitFor(m: SemanticsMatcher) = compose.until("a node: ${m.description}") { compose.onAllNodes(m).fetchSemanticsNodes().isNotEmpty() }
    @Test fun grid() {
        seed()
        show()
        waitFor(hasText("Workouts"))
        // No reference here: the grid is the Notes app's notesapp-<theme>-grid (NotesAppScreenshotTest), pixel for pixel.
        // The docs shot (site/shots.yaml android-notes-grid).
        if (!dark) compose.onRoot().captureRoboImage("screenshots/docs/android-notes-grid.png", roborazziOptions = DOCS_SHOT)
    }

    @Test fun checklist() {
        val shop = seed()
        show()
        compose.runOnIdle { vm.open(NotesPage.OpenNote(shop.id)) }
        waitFor(hasText("avocados"))
        compose.onNodeWithText("1 checked").performClick()
        waitFor(hasText("eggs"))
        compose.onRoot().captureRoboImage("screenshots/notes-$theme-checklist.png")
        if (!dark) compose.onRoot().captureRoboImage("screenshots/docs/android-notes-list.png", roborazziOptions = DOCS_SHOT)
    }

    @Test fun textNote() {
        seed()
        val id = fake.notes.values.first { it.title == "Trip ideas" }.id
        show()
        compose.runOnIdle { vm.open(NotesPage.OpenNote(id)) }
        waitFor(hasTestTag("note-body"))
        compose.onRoot().captureRoboImage("screenshots/notes-$theme-text.png")
    }

    /** The editor's top bar with Undo on and Redo off, after an edit. */
    @Test fun editorUndoBar() {
        seed()
        val id = fake.notes.values.first { it.title == "Trip ideas" }.id
        show()
        compose.runOnIdle { vm.open(NotesPage.OpenNote(id)) }
        waitFor(hasTestTag("note-body"))
        compose.onNodeWithTag("note-body").performTextInput(" Pack the tent.")
        compose.waitForIdle()
        compose.onNodeWithTag("note-top-bar").captureRoboImage("screenshots/notes-$theme-editor-undo.png")
    }

    @Test fun tableRows() {
        seed()
        val t = fake.tables.values.single()
        show()
        compose.runOnIdle { vm.open(NotesPage.OpenTable(t.id)) }
        waitFor(hasText("Squat"))
        waitFor(hasTestTag("footers"))
        compose.onRoot().captureRoboImage("screenshots/notes-$theme-table.png")
    }

    @Test fun rowForm() {
        seed()
        val t = fake.tables.values.single()
        val squat = fake.rows.values.first { it.cells["c0"] == JsonPrimitive("Squat") }
        show()
        compose.runOnIdle { vm.open(NotesPage.OpenTable(t.id)); vm.open(NotesPage.OpenRow(t.id, squat.id)) }
        waitFor(hasTestTag("field-Weight"))
        // The fields exist before the row's values arrive; under a full parallel run the shot once
        // caught them empty (title "Row"). Wait for a value.
        waitFor(hasText("82.5"))
        compose.onRoot().captureRoboImage("screenshots/notes-$theme-row.png")
    }

    @Test fun shareSheet() {
        val shop = seed()
        fake.shares[shop.id] = mutableListOf(buildJsonObject { put("kind", "user"); put("id", 7); put("name", "Sam Example"); put("level", "edit") })
        show()
        compose.runOnIdle { vm.open(NotesPage.OpenNote(shop.id)) }
        waitFor(hasText("avocados"))
        compose.onNodeWithContentDescription("Share").performSemanticsAction(androidx.compose.ui.semantics.SemanticsActions.OnClick)
        waitFor(hasText("Sam Example"))
        compose.onNode(isDialog()).captureRoboImage("screenshots/notes-$theme-share.png")
    }

    @Test fun remindSheet() {
        val shop = seed()
        show()
        compose.runOnIdle { vm.open(NotesPage.OpenNote(shop.id)) }
        waitFor(hasText("avocados"))
        compose.onNodeWithContentDescription("Remind me").performClick()
        waitFor(hasTestTag("remind-sheet"))
        compose.onNode(isDialog()).captureRoboImage("screenshots/notes-$theme-remind.png")
    }

    @Test fun offlineWithLargeFont() {
        RuntimeEnvironment.setFontScale(1.5f)
        val shop = seed()
        fake.offline = true
        show()
        waitFor(hasText("Shopping"))
        compose.runOnIdle { vm.act { g.notes.repo.check(fake.items[shop.id]!![0].id, true) } }
        compose.until("the offline banner counts 1 change") {
            compose.waitForIdle(); runBlocking { g.notes.repo.drain() }
            compose.onAllNodes(hasText("Offline · 1 change waiting")).fetchSemanticsNodes().isNotEmpty()
        }
        compose.onRoot().captureRoboImage("screenshots/notes-$theme-offline-large.png")
    }

    // ================================================================ household lists

    @OptIn(com.github.takahirom.roborazzi.ExperimentalRoborazziApi::class)
    @Test fun repeatSheet() {
        val shop = seed()
        fake.notes[shop.id]!!.apply { repeat = "FREQ=WEEKLY;BYDAY=SU"; repeatNext = "2026-10-11T04:00:00+00:00" }
        runBlocking { g.notes.repo.pull() }
        show()
        compose.runOnIdle { vm.open(NotesPage.OpenNote(shop.id)) }
        waitFor(hasText("avocados"))
        waitFor(hasTestTag("repeat-line"))
        compose.onRoot().captureRoboImage("screenshots/notes-$theme-repeat-line.png")
        compose.onNodeWithContentDescription("More options").performClick()
        // The menu item exists before the menu has opened; wait for it, then for the sheet.
        waitFor(hasTestTag("menu-repeat"))
        compose.waitForIdle()
        compose.onNodeWithTag("menu-repeat").performClick()
        waitFor(hasTestTag("repeat-sheet"))
        compose.onNode(isDialog()).captureRoboImage("screenshots/notes-$theme-repeat-sheet.png")
    }

    @OptIn(com.github.takahirom.roborazzi.ExperimentalRoborazziApi::class)
    @Test fun localOnlyLocked() {
        val shop = seed()
        fake.notes[shop.id]!!.apply { level = "edit"; localOnly = true; canLowerLocalOnly = false; ownerName = "Dana Example" }
        runBlocking { g.notes.repo.pull() }
        show()
        compose.runOnIdle { vm.open(NotesPage.OpenNote(shop.id)) }
        waitFor(hasText("avocados"))
        compose.onNodeWithContentDescription("More options").performClick()
        waitFor(hasText("Only the owner can turn this off"))
        compose.mainClock.advanceTimeBy(500)
        compose.waitForIdle()
        com.github.takahirom.roborazzi.captureScreenRoboImage("screenshots/notes-$theme-local-only-locked.png")
    }

    @Test fun presenceAndATick() {
        // The "✓ Cleo" label lasts 4 s of the graph's virtual time (TestGraph.time): it stays until a test moves it on.
        val shop = seed()
        fake.notes[shop.id]!!.sharedCount = 1
        runBlocking { g.notes.repo.pull() }
        show()
        compose.runOnIdle { vm.open(NotesPage.OpenNote(shop.id)) }
        waitFor(hasText("avocados"))
        val repo = g.notes.repo
        compose.runOnIdle {
            repo.onEvent("presence", buildJsonObject {
                put("type", "presence"); put("kind", "note"); put("id", shop.id); put("state", "here")
                put("person", buildJsonObject { put("id", 2); put("name", "Cleo") })
            })
        }
        waitFor(hasText("Cleo is in this list"))
        // Cleo checks the limes: they go to the fold, which names her for a moment.
        val limes = fake.items[shop.id]!!.first { it.text == "limes" }
        limes.checked = true; limes.version++
        compose.runOnIdle {
            repo.onEvent("item", buildJsonObject {
                put("type", "item"); put("kind", "checkitem"); put("id", limes.id); put("note", shop.id); put("origin", "cleo-phone")
                put("author", buildJsonObject { put("id", 2); put("name", "Cleo") })
            })
        }
        waitFor(hasText("✓ Cleo: limes"))
        compose.onRoot().captureRoboImage("screenshots/notes-$theme-presence.png")
    }
}
