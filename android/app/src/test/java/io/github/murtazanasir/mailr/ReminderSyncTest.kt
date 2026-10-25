// ACT-SURFACES: android.notification
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.ApiClient
import io.github.murtazanasir.mailr.data.api.CalendarApi
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.MailrApi
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.RemoteReminder
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.cal.CalendarRepository
import io.github.murtazanasir.mailr.data.db.CalEventEntity
import io.github.murtazanasir.mailr.data.db.MailrDb
import io.github.murtazanasir.mailr.data.db.ReminderEntity
import io.github.murtazanasir.mailr.data.reminders.RState
import io.github.murtazanasir.mailr.data.reminders.ReminderConfig
import io.github.murtazanasir.mailr.data.reminders.ReminderEngine
import io.github.murtazanasir.mailr.data.reminders.ReminderRemote
import io.github.murtazanasir.mailr.data.reminders.ReminderSink
import io.github.murtazanasir.mailr.data.reminders.SummarySpec
import io.github.murtazanasir.mailr.data.repo.MailRepository
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.time.ZoneId
import java.time.ZonedDateTime

/**
 * Reminder actions reach the server, and what other devices did reaches this one
 * (reminder.dismiss, reminder.sync, task.snooze, event.snooze, event.remind_before).
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class ReminderSyncTest {
    private val zone = ZoneId.of("America/Chicago")
    private val t0 = ZonedDateTime.of(2026, 9, 29, 9, 0, 0, 0, zone)
    private var now = t0.toInstant().toEpochMilli()
    private lateinit var db: MailrDb
    private lateinit var engine: ReminderEngine

    private class Shade : ReminderSink {
        val active = linkedMapOf<String, ReminderEntity>()
        override fun post(r: ReminderEntity, alert: Boolean) { active[r.key] = r }
        override fun cancel(r: ReminderEntity) { active.remove(r.key) }
        override fun postSummary(spec: SummarySpec) {}
        override fun cancelSummary(kind: String) {}
        override fun activeKeys(): Set<String> = active.keys.toSet()
    }

    private class Server : ReminderRemote {
        val calls = mutableListOf<String>()
        override suspend fun snooze(key: String, until: Long) { calls += "snooze $key $until" }
        override suspend fun handle(keys: List<String>, undo: Boolean) { calls += "handle${if (undo) " undo" else ""} ${keys.joinToString(",")}" }
        override suspend fun add(uid: String, occurrence: String, minutes: Int, handle: List<String>) {
            calls += "add $uid $minutes ${handle.joinToString(",")}"
        }
    }

    private val shade = Shade()
    private val server = Server()

    @Before fun setUp() {
        val ctx = ApplicationProvider.getApplicationContext<Application>()
        val sessions = SessionStore(ctx).apply { server = "http://127.0.0.1:9/"; save("t", "c", "me@example.com", "Me") }
        db = MailrDb.create(ctx, inMemory = true)
        val client = ApiClient(sessions)
        val cal = CalendarRepository(CalendarApi(client), db, MailRepository(MailrApi(client), db), now = { now }, zone = { zone })
        engine = ReminderEngine(db.reminders(), cal, { ReminderConfig(defaultTimedEvent = 10) }, shade, {},
            now = { now }, zone = { zone }, remote = server)
    }

    @After fun tearDown() = db.close()

    private suspend fun event(uid: String, start: ZonedDateTime) {
        val e = CalendarEvent(uid, "Event $uid", start.toOffsetDateTime().toString(), start.plusHours(1).toOffsetDateTime().toString())
        db.calendar().putEvents(listOf(CalEventEntity(e.key, uid, start.toInstant().toEpochMilli(),
            start.plusHours(1).toInstant().toEpochMilli(), false, null, MailrJson.encodeToString(CalendarEvent.serializer(), e))))
    }

    private fun at(z: ZonedDateTime) { now = z.toInstant().toEpochMilli() }
    private fun ms(z: ZonedDateTime) = z.toInstant().toEpochMilli()

    private suspend fun fired(uid: String, start: ZonedDateTime): String {
        event(uid, start)
        engine.resync()
        at(start.minusMinutes(10))
        engine.fire()
        return shade.active.keys.first { it.startsWith("e|$uid|") }
    }

    @Test fun snoozeAndDismissAreSentToTheServer() = runTest {
        val k = fired("a", t0.plusMinutes(40))
        engine.snooze(k, ms(t0.plusMinutes(35)))
        assertTrue(k !in shade.active)
        assertEquals("snooze $k ${ms(t0.plusMinutes(35))}", server.calls.last())
        at(t0.plusMinutes(35)); engine.fire()
        assertTrue(k in shade.active)
        engine.dismiss(k)
        assertEquals("handle $k", server.calls.last())
    }

    @Test fun remindBeforeReplacesTheFiredReminder() = runTest {
        val start = t0.plusHours(2)
        val k = fired("b", start)
        val atMs = engine.remindBefore(k, 5)
        assertEquals(ms(start.minusMinutes(5)), atMs)
        assertTrue(k !in shade.active)
        val newKey = "e|b|${io.github.murtazanasir.mailr.data.api.Occurrence.utc(start.toInstant())}|5"
        assertEquals("add b 5 $k", server.calls.last())
        engine.resync() // survives a sync (it is a pending, snoozed row)
        at(start.minusMinutes(5)); engine.fire()
        assertTrue(newKey in shade.active)
    }

    @Test fun whatOtherDevicesDidIsApplied() = runTest {
        val start = t0.plusMinutes(40)
        val k = fired("c", start)
        engine.applyRemote(listOf(RemoteReminder(k, "e", "c", start.toOffsetDateTime().toString(), 10, "handled")))
        assertTrue(k !in shade.active)
        assertEquals(RState.DISMISSED, db.reminders().get(k)!!.state)
        // Undone elsewhere ("pending"): it shows again.
        engine.applyRemote(listOf(RemoteReminder(k, "e", "c", start.toOffsetDateTime().toString(), 10, "pending")))
        assertTrue(k in shade.active)
        // Snoozed elsewhere: it leaves the shade and comes back at that time.
        val until = start.minusMinutes(2)
        engine.applyRemote(listOf(RemoteReminder(k, "e", "c", start.toOffsetDateTime().toString(), 10, "snoozed",
            snoozedUntil = until.toOffsetDateTime().toString())))
        assertTrue(k !in shade.active)
        at(until); engine.fire()
        assertTrue(k in shade.active)
    }

    @Test fun undoRestoresEverywhereAndTheQueueOrdersMissedFirst() = runTest {
        val k = fired("d", t0.plusMinutes(40))
        val before = db.reminders().get(k)!!
        engine.dismiss(k)
        engine.restore(before)
        assertTrue(k in shade.active)
        assertEquals("handle undo $k", server.calls.last())
        val queue = engine.shownQueue()
        assertEquals(listOf(k), queue.map { it.key })
    }
}
