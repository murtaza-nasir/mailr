// ACT-SURFACES: android.noteslauncher android.notesapp
package io.github.murtazanasir.mailr.notesapp

import android.app.Application
import android.content.pm.ShortcutManager
import android.os.Build
import androidx.core.content.pm.ShortcutManagerCompat
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.notes.FakeNotesServer
import io.github.murtazanasir.mailr.notesapp.capture.CaptureActivity
import io.github.murtazanasir.mailr.notesapp.capture.NewNoteTile
import io.github.murtazanasir.mailr.notesapp.launcher.Shortcuts
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/**
 * Launcher shortcuts and the Quick Settings tile: the last three
 * opened as dynamic shortcuts, "Add to home screen" (note.add_to_home) as a pinned shortcut that is
 * disabled while its note is in Trash and enabled again on restore, and the tile "New note"
 * (note.create) opening quick capture.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class LauncherTest {
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

    private fun pinned() = ctx.getSystemService(ShortcutManager::class.java).pinnedShortcuts.associateBy { it.id }

    @Test fun theLastThreeOpenedAreDynamicShortcuts() = runBlocking {
        val notes = (1..4).map { fake.note("text", "Note $it") }
        g.notes.repo.pull()
        notes.forEach { g.prefs.opened("note", it.id) }
        Shortcuts.opened(ctx, io.github.murtazanasir.mailr.notesapp.ui.recentEntries(g))
        val dyn = ShortcutManagerCompat.getDynamicShortcuts(ctx).sortedBy { it.rank }
        assertEquals(listOf("Note 4", "Note 3", "Note 2"), dyn.map { it.shortLabel.toString() })
        assertEquals("mailr-notes://note/${notes[3].id}", dyn.first().intent!!.dataString)
    }

    @Test fun aPinnedShortcutIsDisabledInTrashAndEnabledOnRestore() = runBlocking {
        val shop = fake.note("checklist", "Shopping", listOf("milk")).also { it.color = "sage" }
        g.notes.repo.pull()
        // note.add_to_home: the launcher pins the kind's icon on the note's colour.
        assertTrue(Shortcuts.pin(ctx, Shortcuts.entry(g.notes.repo.noteNow(shop.id)!!)))
        val id = Shortcuts.idOf("note", shop.id)
        assertTrue(pinned()[id]!!.isEnabled)
        assertEquals("mailr-notes://note/${shop.id}", pinned()[id]!!.intent!!.dataString)
        // In Trash: disabled with "Note is in Trash" (the plan follow() hands the launcher).
        g.notes.repo.trash(shop.id)
        fun plan() = runBlocking {
            Shortcuts.plan(pinned().values.map { it.id to 0 }, g.notes.repo.notes.first(), g.notes.repo.tables.first())
        }
        assertEquals(mapOf(id to Shortcuts.TRASHED), plan().disable)
        Shortcuts.follow(ctx, g.notes.repo.notes.first(), g.notes.repo.tables.first())
        // Restored: enabled again, with the current title.
        g.notes.repo.restore(shop.id, isTable = false)
        g.notes.repo.editNote(shop.id, title = "Groceries")
        assertTrue(plan().disable.isEmpty())
        assertEquals(listOf("Groceries"), plan().enable.map { it.first.title })
        Shortcuts.follow(ctx, g.notes.repo.notes.first(), g.notes.repo.tables.first())
        assertEquals("Groceries", pinned()[id]!!.shortLabel.toString())
        // Gone for good (purged elsewhere): "no longer available".
        assertEquals(mapOf(id to Shortcuts.GONE), Shortcuts.plan(listOf(id to 0), emptyList(), emptyList()).disable)
    }

    @Test fun aTableCanBePinnedToo() = runBlocking {
        val t = fake.table("Expenses", listOf("Item" to "text"))
        g.notes.repo.pull()
        assertTrue(Shortcuts.pin(ctx, Shortcuts.entry(g.notes.repo.tableNow(t.id)!!)))
        assertEquals("mailr-notes://table/${t.id}", pinned()[Shortcuts.idOf("table", t.id)]!!.intent!!.dataString)
    }

    @Test @Config(sdk = [33]) fun theTileOpensQuickCapture() {
        val tile = Robolectric.setupService(NewNoteTile::class.java)
        tile.onClick()
        val started = shadowOf(ctx).nextStartedActivity
        assertEquals(CaptureActivity::class.java.name, started.component!!.className)
        assertEquals("note", started.getStringExtra(CaptureActivity.EXTRA_MODE))
        assertTrue(Build.VERSION.SDK_INT == 33)
    }
}
