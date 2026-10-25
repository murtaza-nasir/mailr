// ACT-SURFACES: android.notesapp
package io.github.murtazanasir.mailr.notesapp

import io.github.murtazanasir.mailr.time.AppClock
import android.app.Application
import android.app.NotificationManager
import androidx.test.core.app.ApplicationProvider
import androidx.work.testing.WorkManagerTestInitHelper
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.notes.FakeNotesServer
import io.github.murtazanasir.mailr.notes.NotesApp
import io.github.murtazanasir.mailr.notesapp.reminders.NotePlanner
import io.github.murtazanasir.mailr.notesapp.reminders.NoteReminderSource
import io.github.murtazanasir.mailr.notesapp.reminders.NoteReminders
import io.github.murtazanasir.mailr.notesapp.reminders.RemoteState
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
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.time.Instant

/**
 * Note reminders in the Notes app (note.remind on the phone): it plans `n|` keys from its copy of the
 * notes and asks for the shared states of note keys only (`kinds=n`); a due reminder rings once on the
 * "Note reminders" channel; Snooze 1 h and Dismiss change the shared state. Gating between the apps:
 * the Notes app's ids are the ones the mailr app looks for (it then stops ringing `n|` keys,
 * the mailr app's NoteRemindersTest).
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class NoteRemindersAppTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = NotesTestGraph()
    private lateinit var fake: FakeNotesServer
    private lateinit var g: NotesGraph
    private val ctx get() = ApplicationProvider.getApplicationContext<Application>()

    @Before fun setUp() {
        // A phone where the user allowed notifications (Notify.post checks it, as Android 13+ does).
        org.robolectric.Shadows.shadowOf(ctx as android.app.Application).grantPermissions(android.Manifest.permission.POST_NOTIFICATIONS)
        WorkManagerTestInitHelper.initializeTestWorkManager(ctx)
        fake = graphs.closeLast(object : AutoCloseable {
            val f = FakeNotesServer().start()
            override fun close() = f.close()
        }).f
        SessionStore(ctx).apply {
            server = fake.server.url("/").toString().trimEnd('/')
            save("tok-1", "csrf-1", "me@example.com", "Me")
        }
        g = graphs.graph(ctx)
        NoteReminders.createChannel(ctx)
    }

    private val at = 1_790_000_000_000L
    private fun src(key: String, at: Long) = NoteReminderSource(key, "n1", "Pay rent", false, at)

    @Test fun thePlannerRingsOnceAndFollowsSharedStates() {
        val r = src("n|n1|abs|a", at)
        assertEquals(listOf(r), NotePlanner.plan(listOf(r), emptyMap(), emptyMap(), at + 1).due)
        // Rung already at that time: not again.
        assertTrue(NotePlanner.plan(listOf(r), emptyMap(), mapOf(r.key to at), at + 1).due.isEmpty())
        // Handled elsewhere: never.
        assertTrue(NotePlanner.plan(listOf(r), mapOf(r.key to RemoteState(r.key, "handled")), emptyMap(), at + 1).due.isEmpty())
        // Snoozed: at the snooze time, and that is the next alarm.
        val until = Instant.ofEpochMilli(at + 3_600_000).toString()
        val p = NotePlanner.plan(listOf(r), mapOf(r.key to RemoteState(r.key, "snoozed", until)), mapOf(r.key to at), at + 1)
        assertTrue(p.due.isEmpty())
        assertEquals(at + 3_600_000, p.next)
        // A day late: left quiet.
        assertTrue(NotePlanner.plan(listOf(r), emptyMap(), emptyMap(), at + NotePlanner.TOO_LATE_MS + 1).due.isEmpty())
    }

    @Test fun theAppAsksForNoteKeysAndRingsADueReminder() = runBlocking {
        val past = AppClock.instant().minusSeconds(60).toString()
        val future = AppClock.instant().plusSeconds(3_600).toString()
        val n = fake.note("text", "Pay rent").also { it.reminders = listOf(past, future) }
        g.notes.repo.pull()
        g.reminders.sync()
        assertTrue(fake.log.any { it.path.startsWith("reminders/state?") && it.path.contains("kinds=n") && !it.path.contains("kinds=e") })
        val nm = shadowOf(ctx.getSystemService(NotificationManager::class.java))
        val posted = nm.allNotifications
        assertEquals(1, posted.size)
        assertEquals("Pay rent", posted.single().extras.getString("android.title"))
        assertEquals(NoteReminders.CHANNEL, posted.single().channelId)
        assertEquals(listOf("Snooze 1 h", "Dismiss"), posted.single().actions.map { it.title.toString() })
        assertEquals(Instant.parse(future).toEpochMilli(), g.reminders.nextAlarm)
        // Planned again: it does not ring twice.
        g.reminders.plan()
        assertEquals(1, nm.allNotifications.size)
        // Snooze 1 h: the notification goes, the next alarm is the snooze (an hour away), the server is told.
        val key = "n|${n.id}|abs|$past"
        g.reminders.act(key, NoteReminders.ACTION_SNOOZE)
        assertEquals(0, nm.allNotifications.size)
        assertTrue(g.reminders.nextAlarm!! > AppClock.millis() + 3_500_000)
        assertTrue(androidx.work.WorkManager.getInstance(ctx).getWorkInfosByTag(
            io.github.murtazanasir.mailr.notesapp.reminders.ReminderSendWorker::class.java.name).get().isNotEmpty())
    }

    @Test fun aStateFromAnotherDeviceStopsTheRing() = runBlocking {
        val past = AppClock.instant().minusSeconds(60).toString()
        val n = fake.note("text", "Pay rent").also { it.reminders = listOf(past) }
        fake.stateItems += buildJsonObject {
            put("key", "n|${n.id}|abs|$past"); put("kind", "n"); put("uid", n.id); put("state", "handled")
        }
        g.notes.repo.pull()
        g.reminders.sync()
        assertEquals(0, shadowOf(ctx.getSystemService(NotificationManager::class.java)).allNotifications.size)
        assertNull(g.reminders.nextAlarm)
        // Sign-out forgets the states and the alarm.
        g.reminders.clear()
        assertNull(g.reminders.nextAlarm)
    }

    @Test fun theNotesAppIdsAreTheOnesTheMailrAppLooksFor() {
        // The mailr app stops ringing n| keys once one of these is installed (NotesApp.installed).
        assertTrue(BuildConfig.APPLICATION_ID == NotesApp.APPLICATION_ID || BuildConfig.APPLICATION_ID == NotesApp.DEBUG_APPLICATION_ID)
    }
}
