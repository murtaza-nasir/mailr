// ACT-SURFACES: android.notification
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
import io.github.murtazanasir.mailr.data.reminders.Agenda
import io.github.murtazanasir.mailr.data.reminders.AlertLog
import io.github.murtazanasir.mailr.data.reminders.AlertPrefs
import io.github.murtazanasir.mailr.data.reminders.AlertStyle
import io.github.murtazanasir.mailr.data.reminders.Kind
import io.github.murtazanasir.mailr.data.reminders.MorningAgenda
import io.github.murtazanasir.mailr.data.reminders.RState
import io.github.murtazanasir.mailr.data.reminders.ReminderAlerts
import io.github.murtazanasir.mailr.data.reminders.ReminderConfig
import io.github.murtazanasir.mailr.data.reminders.ReminderEngine
import io.github.murtazanasir.mailr.data.reminders.ReminderSink
import io.github.murtazanasir.mailr.data.reminders.SummarySpec
import io.github.murtazanasir.mailr.data.repo.MailRepository
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.time.ZoneId
import java.time.ZonedDateTime
import java.util.Locale

/**
 * Reminder alerts through the engine over Room: reminder.full_screen only for what
 * is happening now and never for missed or repeated alerts; reminder.repeat every few minutes, up
 * to three times, stopping once the reminder is handled here or elsewhere; the single alarm covers
 * repeats and the agenda; reminder.agenda once a day at the morning hour, not when late, nothing
 * on an empty day; agent.ask_about as the Ask button style.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class ReminderAlertsTest {
    private val zone = ZoneId.of("America/Chicago")
    private val t0 = ZonedDateTime.of(2026, 9, 29, 9, 0, 0, 0, zone)
    private var now = t0.toInstant().toEpochMilli()
    private lateinit var db: MailrDb
    private lateinit var engine: ReminderEngine
    private lateinit var cal: CalendarRepository
    private var prefs = AlertPrefs()
    private val log = AlertLog.Memory()

    private class Shade : ReminderSink {
        val active = linkedMapOf<String, ReminderEntity>()
        val posts = mutableListOf<Pair<String, AlertStyle>>()
        val agendas = mutableListOf<Agenda>()
        override fun post(r: ReminderEntity, alert: Boolean) { active[r.key] = r }
        override fun post(r: ReminderEntity, alert: Boolean, join: String?, style: AlertStyle) {
            active[r.key] = r
            if (alert) posts += r.key to style
        }
        override fun postAgenda(agenda: Agenda) { agendas += agenda }
        override fun cancel(r: ReminderEntity) { active.remove(r.key) }
        override fun postSummary(spec: SummarySpec) {}
        override fun cancelSummary(kind: String) {}
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
            now = { now }, zone = { zone }, alerts = { prefs }, log = log)
    }

    @After fun tearDown() = db.close()

    private suspend fun event(uid: String, start: ZonedDateTime, reminders: List<Int> = emptyList(), title: String = "Event $uid",
                              location: String? = null) {
        val e = CalendarEvent(uid, title, start.toOffsetDateTime().toString(), start.plusHours(1).toOffsetDateTime().toString(),
            reminders = reminders, location = location)
        db.calendar().putEvents(listOf(CalEventEntity(e.key, uid, start.toInstant().toEpochMilli(),
            start.plusHours(1).toInstant().toEpochMilli(), false, null, MailrJson.encodeToString(CalendarEvent.serializer(), e))))
    }

    private suspend fun task(uid: String, due: String, title: String = "Task $uid") {
        val t = TaskItem(uid, title, due)
        db.calendar().putTasks(listOf(CalTaskEntity(uid, null, null, false, io.github.murtazanasir.mailr.data.reminders.ReminderPlan.instant(due, zone)!!,
            MailrJson.encodeToString(TaskItem.serializer(), t))))
    }

    private fun at(z: ZonedDateTime) { now = z.toInstant().toEpochMilli() }

    /** reminder.full_screen: on only by the setting, and only for an event within 15 minutes of its start. */
    @Test fun fullScreenOnlyWhenImminentAndTurnedOn() = runTest {
        event("soon", t0.plusMinutes(10))                     // reminder at start − 10 min = now
        event("later", t0.plusHours(2), reminders = listOf(60)) // fires an hour before: not imminent
        engine.resync()
        at(t0)
        engine.fire()
        assertEquals(listOf(false), shade.posts.map { it.second.fullScreen })   // setting off

        prefs = prefs.copy(fullScreen = true)
        event("soon2", t0.plusMinutes(20))
        engine.resync()
        at(t0.plusMinutes(10))
        engine.fire()
        val soon2 = shade.posts.last { it.first.startsWith("e|soon2|") }.second
        assertTrue(soon2.fullScreen)
        at(t0.plusHours(1))
        engine.fire()
        val later = shade.posts.last { it.first.startsWith("e|later|") }.second
        assertFalse(later.fullScreen)
    }

    @Test fun fullScreenFitsNeverMissedOrAllDay() {
        val r = ReminderEntity("k", Kind.EVENT, "u", "o", "T", "9:00 – 10:00", now, now, now + 5 * 60_000L, null, RState.SHOWN, updatedAt = 0)
        assertTrue(ReminderAlerts.fullScreenFits(r, now))
        assertFalse(ReminderAlerts.fullScreenFits(r.copy(missed = true), now))
        assertFalse(ReminderAlerts.fullScreenFits(r.copy(detail = "All day"), now))
        assertFalse(ReminderAlerts.fullScreenFits(r.copy(startAt = now + 16 * 60_000L), now))
        // A date-only task: its anchor is midnight, nine hours before the morning reminder.
        val task = r.copy(kind = Kind.TASK, startAt = t0.withHour(0).toInstant().toEpochMilli())
        assertFalse(ReminderAlerts.fullScreenFits(task, now))
    }

    /** reminder.repeat: three more alerts at the interval while nobody acts; never full screen; then quiet. */
    /** reminder.fire: the alarm posts the due reminder; Remind again repeats it. */
    @Test fun repeatsUpToThreeTimesThenStops() = runTest {
        prefs = prefs.copy(remindAgain = 5, fullScreen = true)
        task("a", t0.plusMinutes(1).toOffsetDateTime().toString())
        engine.resync()
        at(t0.plusMinutes(1))
        engine.fire()
        val key = shade.posts.single().first
        assertEquals(t0.plusMinutes(6).toInstant().toEpochMilli(), armed)   // the single alarm covers the repeat
        for (i in 1..3) {
            at(t0.plusMinutes(1L + 5 * i))
            engine.fire()
        }
        val styles = shade.posts.filter { it.first == key }.map { it.second }
        assertEquals(4, styles.size)
        assertEquals(listOf(false, true, true, true), styles.map { it.again })
        assertTrue(styles.drop(1).none { it.fullScreen })
        at(t0.plusMinutes(30))
        engine.fire()
        assertEquals(4, shade.posts.count { it.first == key })
        assertTrue(log.repeats().isEmpty())
    }

    /** reminder.repeat: the server's `max` (here once) caps the phone's repeats too. */
    @Test fun repeatsStopAtTheServersLimit() = runTest {
        prefs = prefs.copy(remindAgain = 5, remindMax = 1)
        task("a", t0.plusMinutes(1).toOffsetDateTime().toString())
        engine.resync()
        at(t0.plusMinutes(1))
        engine.fire()
        val key = shade.posts.single().first
        for (i in 1..3) {
            at(t0.plusMinutes(1L + 5 * i))
            engine.fire()
        }
        assertEquals(2, shade.posts.count { it.first == key })
        assertTrue(log.repeats().isEmpty())
    }

    /** Handled here (Dismiss) or on another device: no repeat follows. */
    @Test fun handledRemindersDoNotRepeat() = runTest {
        prefs = prefs.copy(remindAgain = 10)
        task("a", t0.toOffsetDateTime().toString())
        task("b", t0.toOffsetDateTime().toString())
        engine.resync()
        at(t0)
        engine.fire()
        val (ka, kb) = shade.posts.map { it.first }
        engine.dismiss(ka)
        engine.applyRemote(listOf(io.github.murtazanasir.mailr.data.api.RemoteReminder(key = kb, kind = "t", uid = "b", state = "handled")))
        at(t0.plusMinutes(10))
        engine.fire()
        assertEquals(2, shade.posts.size)
        assertTrue(log.repeats().isEmpty())
    }

    /** Missed reminders (the phone was off) never repeat. */
    @Test fun missedRemindersDoNotRepeat() = runTest {
        prefs = prefs.copy(remindAgain = 5)
        task("a", t0.toOffsetDateTime().toString())
        at(t0.minusHours(1))
        engine.resync()
        at(t0.plusHours(2))
        engine.fire()
        assertTrue(shade.active.values.single().missed)
        assertTrue(log.repeats().isEmpty())
    }

    /** agent.ask_about: the Ask button follows its setting. */
    @Test fun askStyleFollowsTheSetting() = runTest {
        task("a", t0.toOffsetDateTime().toString())
        engine.resync()
        at(t0)
        engine.fire()
        assertTrue(shade.posts.single().second.ask)
        prefs = prefs.copy(ask = false)
        task("b", t0.plusMinutes(1).toOffsetDateTime().toString())
        engine.resync()
        at(t0.plusMinutes(1))
        engine.fire()
        assertFalse(shade.posts.last().second.ask)
    }

    /** reminder.agenda: at the morning hour, once a day, with today's events and due or overdue tasks. */
    @Test fun morningAgendaOnceADay() = runTest {
        prefs = prefs.copy(morningAgenda = true, morningHour = 8)
        at(t0.withHour(6))
        event("standup", t0.withHour(10), title = "Standup", location = "Room 2")
        task("report", t0.toLocalDate().toString(), title = "Send the report")
        task("old", t0.minusDays(2).toLocalDate().toString(), title = "Renew parking")
        task("next", t0.plusDays(3).toLocalDate().toString(), title = "Not today")
        engine.resync()
        assertEquals(t0.withHour(8).toInstant().toEpochMilli(), armed)
        assertTrue(shade.agendas.isEmpty())
        at(t0.withHour(8))
        engine.fire()
        val a = shade.agendas.single()
        assertEquals("Today: 1 event, 2 tasks", a.title)
        assertTrue(a.lines[0].endsWith("Standup · Room 2"))
        assertTrue(a.lines.any { it.startsWith("Overdue") && it.endsWith("Renew parking") })
        assertTrue(a.lines.none { it.contains("Not today") })
        // Not again today; the alarm moves to tomorrow's morning.
        at(t0.withHour(9))
        engine.fire()
        assertEquals(1, shade.agendas.size)
        assertNotNull(armed)
    }

    @Test fun agendaNotWhenLateNorOff() {
        val day = t0.toLocalDate()
        assertNull(ReminderAlerts.agendaDue(t0.withHour(15).toInstant().toEpochMilli(), zone, 8, null))
        assertNull(ReminderAlerts.agendaDue(t0.withHour(8).toInstant().toEpochMilli(), zone, 8, day.toString()))
        assertEquals(t0.withHour(8).toInstant().toEpochMilli(), ReminderAlerts.agendaDue(t0.withHour(9).toInstant().toEpochMilli(), zone, 8, null))
        assertNull(MorningAgenda.build(emptyList(), emptyList(), day, zone, Locale.US))
    }
}
