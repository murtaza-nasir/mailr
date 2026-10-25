// ACT-SURFACES: android.notes
package io.github.murtazanasir.mailr.notes

import io.github.murtazanasir.mailr.time.AppClock
import android.app.Application
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.TestGraph
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.reminders.Kind
import io.github.murtazanasir.mailr.data.reminders.RState
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.time.Instant
import java.time.temporal.ChronoUnit

/**
 * Note reminders on the phone (keys `n|<note>|abs|<instant>`): note.remind from the editor's
 * calls, the mailr app firing them only while the Notes app is not installed, the shared states asked
 * with `kinds=e,t,n` only then, and note.from_template.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class NoteRemindersTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    private lateinit var fake: FakeNotesServer
    private lateinit var g: AppGraph

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

    @Test fun setRemindersQueuesTheViewersTimesInTheSharedKeyForm() = runBlocking {
        val n = fake.note("text", "Call the plumber")
        g.notes.repo.pull()
        val at = Instant.parse("2030-03-04T15:30:00Z")
        // note.remind: the reminder shows at once and the PUT carries canonical instants.
        g.notes.repo.setReminders(n.id, listOf(at))
        assertEquals("n|${n.id}|abs|2030-03-04T15:30:00+00:00", g.notes.repo.noteNow(n.id)!!.reminders.single().key)
        g.notes.repo.drain()
        val put = fake.log.single { it.method == "PUT" }
        assertEquals("notes/${n.id}/reminders", put.path)
        assertEquals(listOf("2030-03-04T15:30:00+00:00"), put.body!!["at"]!!.jsonArray.map { it.jsonPrimitive.content })
        assertEquals(listOf("2030-03-04T15:30:00+00:00"), fake.notes[n.id]!!.reminders)
        // Removing them: an empty list.
        val h = g.notes.repo.setReminders(n.id, emptyList())
        assertTrue(g.notes.repo.noteNow(n.id)!!.reminders.isEmpty())
        h.undo()
        assertEquals(1, g.notes.repo.noteNow(n.id)!!.reminders.size)
    }

    @Test fun theMailrAppFiresNoteRemindersOnlyWithoutTheNotesApp() = runBlocking {
        val at = AppClock.instant().plus(2, ChronoUnit.HOURS).truncatedTo(ChronoUnit.SECONDS)
        val n = fake.note("checklist", "Shopping").also { it.reminders = listOf(at.toString().replace("Z", "+00:00")) }
        g.notes.repo.pull()
        g.notesRemindHere = { true }
        g.reminders.resync()
        val row = g.db.reminders().all().single { it.kind == Kind.NOTE }
        assertEquals("n|${n.id}|abs|${n.reminders.single()}", row.key)
        assertEquals("Shopping", row.title)
        assertEquals("List", row.detail)
        assertEquals(at.toEpochMilli(), row.fireAt)
        assertEquals(RState.PENDING, row.state)
        // reminder.sync: with note keys asked for, `kinds=e,t,n`.
        g.pullReminders()
        assertTrue(fake.log.last { it.path.startsWith("reminders/state") }.path.contains("kinds=e%2Ct%2Cn"))
        // The Notes app installed: it fires them, this app drops its rows and asks for e and t only.
        g.notesRemindHere = { false }
        g.reminders.resync()
        assertTrue(g.db.reminders().all().none { it.kind == Kind.NOTE })
        g.pullReminders()
        assertTrue(!fake.log.last { it.path.startsWith("reminders/state") }.path.contains("kinds="))
    }

    @Test fun aNoteKeyHandledElsewhereEndsHere() = runBlocking {
        val at = AppClock.instant().plus(2, ChronoUnit.HOURS).truncatedTo(ChronoUnit.SECONDS)
        val n = fake.note("text", "Water the plants").also { it.reminders = listOf(at.toString().replace("Z", "+00:00")) }
        g.notes.repo.pull()
        g.notesRemindHere = { true }
        g.reminders.resync()
        val key = "n|${n.id}|abs|${n.reminders.single()}"
        fake.stateItems += buildJsonObject {
            put("key", key); put("kind", "n"); put("uid", n.id); put("occurrence", n.reminders.single()); put("state", "handled")
        }
        g.pullReminders()
        assertEquals(RState.DISMISSED, g.db.reminders().get(key)!!.state)
    }

    @Test fun aNoteFromATemplateOpensFromTheServersCopy() = runBlocking {
        // note.from_template
        val list = g.notes.repo.templates()
        assertEquals("Packing", list.single().name)
        val id = g.notes.repo.fromTemplate(list.single().id)!!
        assertEquals(listOf("socks", "charger"), g.notes.repo.itemsNow(id).map { it.text })
        assertEquals(JsonPrimitive("b-packing"), fake.log.last { it.path == "notes/from-template" }.body!!["template"])
    }
}
