package io.github.murtazanasir.mailr

import android.app.Application
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.ApiClient
import io.github.murtazanasir.mailr.data.api.CalendarApi
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.MailrApi
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.cal.CalendarRepository
import io.github.murtazanasir.mailr.data.db.CalEventEntity
import io.github.murtazanasir.mailr.data.db.CalTaskEntity
import io.github.murtazanasir.mailr.data.db.MailrDb
import io.github.murtazanasir.mailr.data.db.ReminderEntity
import io.github.murtazanasir.mailr.data.reminders.Kind
import io.github.murtazanasir.mailr.data.reminders.RState
import io.github.murtazanasir.mailr.data.reminders.ReminderConfig
import io.github.murtazanasir.mailr.data.reminders.ReminderEngine
import io.github.murtazanasir.mailr.data.reminders.ReminderSink
import io.github.murtazanasir.mailr.data.reminders.SummarySpec
import io.github.murtazanasir.mailr.data.repo.MailRepository
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.time.ZoneId
import java.time.ZonedDateTime

/** The engine end to end over Room: firing, grouping, snooze, boot, idempotency. */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class ReminderEngineTest {
    private val zone = ZoneId.of("America/Chicago")
    private val t0 = ZonedDateTime.of(2026, 9, 29, 9, 0, 0, 0, zone)
    private var now = t0.toInstant().toEpochMilli()
    private lateinit var db: MailrDb
    private lateinit var engine: ReminderEngine
    private lateinit var cal: CalendarRepository

    /** The notification shade, as the engine sees it. */
    private class Shade : ReminderSink {
        val active = linkedMapOf<String, ReminderEntity>()
        val alerts = mutableListOf<String>()
        val summaries = mutableMapOf<String, SummarySpec>()
        var summaryCancels = 0
        override fun post(r: ReminderEntity, alert: Boolean) {
            active[r.key] = r
            if (alert) alerts += r.key
        }
        override fun cancel(r: ReminderEntity) { active.remove(r.key) }
        override fun postSummary(spec: SummarySpec) { summaries[spec.kind] = spec }
        override fun cancelSummary(kind: String) { if (summaries.remove(kind) != null) summaryCancels++ }
        override fun activeKeys(): Set<String> = active.keys.toSet()
    }

    private val shade = Shade()
    private var armed: Long? = null

    @Before fun setUp() {
        val ctx = ApplicationProvider.getApplicationContext<Application>()
        val sessions = SessionStore(ctx).apply { server = "http://127.0.0.1:9/"; save("t", "c", "me@example.com", "Me") }
        db = MailrDb.create(ctx, inMemory = true)
        val client = ApiClient(sessions)
        cal = CalendarRepository(CalendarApi(client), db, MailRepository(MailrApi(client), db), now = { now }, zone = { zone })
        engine = ReminderEngine(db.reminders(), cal, { ReminderConfig(defaultTimedEvent = 10) }, shade, { armed = it },
            now = { now }, zone = { zone })
    }

    @After fun tearDown() = db.close()

    private suspend fun event(uid: String, start: ZonedDateTime, reminders: List<Int> = emptyList(), title: String = "Event $uid") {
        val e = CalendarEvent(uid, title, start.toOffsetDateTime().toString(), start.plusHours(1).toOffsetDateTime().toString(),
            reminders = reminders)
        db.calendar().putEvents(listOf(CalEventEntity(e.key, uid, start.toInstant().toEpochMilli(),
            start.plusHours(1).toInstant().toEpochMilli(), false, null, MailrJson.encodeToString(CalendarEvent.serializer(), e))))
    }

    private suspend fun task(uid: String, due: ZonedDateTime) {
        val t = TaskItem(uid, "Task $uid", due.toOffsetDateTime().toString())
        db.calendar().putTasks(listOf(CalTaskEntity(uid, null, null, false, due.toInstant().toEpochMilli(),
            MailrJson.encodeToString(TaskItem.serializer(), t))))
    }

    private fun at(z: ZonedDateTime) { now = z.toInstant().toEpochMilli() }

    @Test fun simultaneousRemindersPostTogetherAndGroup() = runTest {
        event("a", t0.plusMinutes(40))
        event("b", t0.plusMinutes(40))
        event("c", t0.plusHours(3))
        engine.resync()
        assertEquals(t0.plusMinutes(30).toInstant().toEpochMilli(), armed)
        assertTrue(shade.active.isEmpty())

        at(t0.plusMinutes(30))
        engine.fire()
        assertEquals(2, shade.active.size)
        assertEquals(2, shade.alerts.size)
        assertEquals(2, shade.summaries[Kind.EVENT]!!.count)
        // The next alarm is the 3-hour event's reminder.
        assertEquals(t0.plusHours(3).minusMinutes(10).toInstant().toEpochMilli(), armed)

        // A task reminder arriving while those show: its own group; nothing replaced.
        task("t", t0.plusMinutes(31))
        engine.resync()
        at(t0.plusMinutes(31))
        engine.fire()
        assertEquals(3, shade.active.size)
        assertEquals(1, shade.summaries[Kind.TASK]!!.count)
        assertEquals(2, shade.summaries[Kind.EVENT]!!.count)
    }

    @Test fun resyncAndFireAreIdempotent() = runTest {
        event("a", t0.plusMinutes(5))
        at(t0.plusMinutes(1))
        engine.resync()
        engine.resync()
        engine.fire()
        engine.resync()
        assertEquals(listOf("e|a|${io.github.murtazanasir.mailr.data.api.Occurrence.utc(t0.plusMinutes(5).toInstant())}|10"), shade.alerts)
        assertEquals(1, shade.active.size)
    }

    @Test fun dismissingTheLastChildCancelsTheSummary() = runTest {
        event("a", t0.plusMinutes(10))
        event("b", t0.plusMinutes(10))
        engine.resync()
        at(t0)
        engine.fire()
        val (k1, k2) = shade.active.keys.toList()
        engine.dismiss(k1)
        assertEquals(1, shade.summaries[Kind.EVENT]!!.count)
        assertEquals(0, shade.summaryCancels)
        engine.dismiss(k2)
        assertNull(shade.summaries[Kind.EVENT])
        assertEquals(1, shade.summaryCancels)
    }

    @Test fun snoozeSurvivesResyncAndFiresLater() = runTest {
        event("a", t0.plusMinutes(10))
        engine.resync()
        engine.fire()
        val key = shade.active.keys.single()
        engine.snooze(key, t0.plusMinutes(15).toInstant().toEpochMilli())
        assertTrue(shade.active.isEmpty())
        assertEquals(t0.plusMinutes(15).toInstant().toEpochMilli(), armed)
        engine.resync() // a sync in between keeps the snooze
        assertEquals(t0.plusMinutes(15).toInstant().toEpochMilli(), armed)
        at(t0.plusMinutes(15))
        engine.fire()
        assertEquals(setOf(key), shade.active.keys)
        assertEquals(2, shade.alerts.size)
    }

    @Test fun userSwipeIsNoticedAndNotRepeated() = runTest {
        event("a", t0.plusMinutes(10))
        engine.resync()
        engine.fire()
        shade.active.clear() // swiped away outside our actions
        engine.fire()
        assertEquals(RState.DISMISSED, db.reminders().all().single().state)
        assertNull(shade.summaries[Kind.EVENT])
    }

    @Test fun deletedEventTakesItsReminderDown() = runTest {
        event("a", t0.plusMinutes(10))
        engine.resync()
        engine.fire()
        assertEquals(1, shade.active.size)
        db.calendar().clearEvents()
        engine.resync()
        assertTrue(shade.active.isEmpty())
        assertNull(shade.summaries[Kind.EVENT])
        assertNull(armed)
    }

    @Test fun missedWhileOffIsPostedOnBootMarkedMissed() = runTest {
        event("a", t0.plusMinutes(60))
        task("t", t0.plusMinutes(20))
        engine.resync()
        // The phone was off from 9:00 to 9:55: the task reminder (9:20) and the event reminder (9:50) were missed.
        at(t0.plusMinutes(55))
        engine.onBoot()
        val rows = db.reminders().all().associateBy { it.kind }
        assertEquals(RState.SHOWN, rows[Kind.EVENT]!!.state)
        assertTrue(rows[Kind.EVENT]!!.missed)
        assertTrue(rows[Kind.TASK]!!.missed)
        assertEquals(2, shade.active.size)
    }

    @Test fun staleMissedEventIsNotPostedAfterBoot() = runTest {
        event("a", t0.plusMinutes(20))
        engine.resync()
        at(t0.plusHours(5)) // the event is long over
        engine.onBoot()
        assertTrue(shade.active.isEmpty())
        assertTrue(db.reminders().all().isEmpty())
    }

    @Test fun shownRemindersComeBackQuietlyAfterReboot() = runTest {
        event("a", t0.plusMinutes(10))
        engine.resync()
        engine.fire()
        shade.active.clear()
        val alerts = shade.alerts.size
        engine.onBoot()
        assertEquals(1, shade.active.size)
        assertEquals(alerts, shade.alerts.size)
    }
}
