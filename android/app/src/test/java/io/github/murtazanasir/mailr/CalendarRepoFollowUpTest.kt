// ACT-SURFACES: android.calendar
package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.time.AppClock
import android.app.Application
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.cal.WriteResult
import io.github.murtazanasir.mailr.data.db.CalCollectionEntity
import io.github.murtazanasir.mailr.data.db.CalEventEntity
import io.github.murtazanasir.mailr.data.db.CalTaskEntity
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.time.ZonedDateTime

/**
 * The repository calls the calendar screens use instead of going around it: the default
 * calendar and task list (PUT /calendar/defaults), RSVP with a note and a scope (shown at once,
 * queued offline), event.move_calendar, the pending keys and the waiting count, the last sync
 * time and the accounts (GET /calendar/sync-status), per-calendar default reminders, and
 * absolute task reminders kept through a delta.
 *
 * Covers: calendar.set_default, list.set_default, event.rsvp, event.move_calendar, calendar.sync, calendar.default_reminders.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class CalendarRepoFollowUpTest {
    private val f = FakeCalendarServer()
    private val start: ZonedDateTime = AppClock.now(ZoneOffset.UTC).plusDays(1).withHour(9).withMinute(0).withSecond(0).withNano(0)
    private val cal = CalendarCollection("https://dav/work/", "Work", "#039be5", events = true, calendarId = 1)
    private val home = CalendarCollection("https://dav/home/", "Home", "#0b8043", events = true, calendarId = 2)
    private val ev = CalendarEvent("e1", "Budget", start.toOffsetDateTime().toString(), start.plusHours(1).toOffsetDateTime().toString(),
        calendar = cal.url, etag = "\"v1\"", recurring = true, rrule = "FREQ=WEEKLY", organizer = "alice@example.com",
        myStatus = "NEEDS-ACTION", occurrenceId = start.toOffsetDateTime().toString())
    private val next = ev.copy(start = start.plusWeeks(1).toOffsetDateTime().toString(), end = start.plusWeeks(1).plusHours(1).toOffsetDateTime().toString(),
        occurrenceId = start.plusWeeks(1).toOffsetDateTime().toString())

    @Before fun setUp() {
        f.start()
        runBlocking {
            f.db.calendar().putCollections(listOf(cal, home).mapIndexed { i, c ->
                CalCollectionEntity(c.url, c.calendarId, c.name, c.color, true, false, false, i, null, MailrJson.encodeToString(CalendarCollection.serializer(), c))
            })
            f.db.calendar().putEvents(listOf(ev, next).map { e ->
                CalEventEntity(e.key, e.uid, OffsetDateTime.parse(e.start).toInstant().toEpochMilli(), OffsetDateTime.parse(e.end!!).toInstant().toEpochMilli(),
                    false, e.calendar, MailrJson.encodeToString(CalendarEvent.serializer(), e))
            })
            f.repo.loadCached()
        }
    }

    @After fun tearDown() = f.stop()

    /** calendar.set_default, list.set_default: PUT /calendar/defaults, kept for the next start. */
    @Test fun defaultsGoThroughTheRepository() = runBlocking {
        f.on("PUT", "calendar/defaults") { _, body ->
            val o = MailrJson.parseToJsonElement(body).jsonObject
            f.json("""{"calendar":${o["calendar"] ?: "\"https://dav/work/\""},"task_list":${o["task_list"] ?: "null"}}""")
        }
        f.repo.setDefaultCalendar(home.url)
        assertEquals(home.url, f.repo.defaults.value.calendar)
        f.repo.setDefaultTaskList("https://dav/list/")
        assertEquals("https://dav/list/", f.repo.defaults.value.taskList)
        assertTrue(f.db.cache().get("cal_defaults")!!.contains("https://dav/list/"))
    }

    /** event.rsvp: "this" changes only that occurrence here at once; the note, scope and occurrence go to the server. */
    @Test fun rsvpShowsAtOnceForTheScope() = runBlocking {
        assertEquals(WriteResult.SENT, f.repo.rsvp(ev, "declined", "Out that week", "this"))
        val body = f.body(f.sent("POST", "calendar/events/e1/rsvp").single())
        assertEquals("declined", body["response"]!!.jsonPrimitive.content)
        assertEquals("Out that week", body["comment"]!!.jsonPrimitive.content)
        assertEquals("this", body["scope"]!!.jsonPrimitive.content)
        assertEquals(ev.occurrence, body["occurrence"]!!.jsonPrimitive.content)
        assertEquals("DECLINED", f.repo.eventInstance(ev.key)!!.myStatus)
        assertEquals("NEEDS-ACTION", f.repo.eventInstance(next.key)!!.myStatus)
    }

    /** event.rsvp offline: queued with the same body; the answer still shows. */
    @Test fun rsvpOfflineIsQueued() = runBlocking {
        f.server.shutdown()
        assertEquals(WriteResult.QUEUED, f.repo.rsvp(ev, "accepted", scope = "all"))
        val op = f.db.pending().next(10).single()
        assertEquals("calendar/events/e1/rsvp", op.path)
        assertEquals("ACCEPTED", f.repo.eventInstance(next.key)!!.myStatus)
        assertEquals(1, f.repo.waiting.first())
    }

    /** event.move_calendar: POST …/move with the version; every occurrence shows the new calendar at once and is pending. */
    @Test fun moveEventMovesEveryOccurrence() = runBlocking {
        f.on("POST", "calendar/events/e1/move") { _, _ -> f.json(MailrJson.encodeToString(CalendarEvent.serializer(), ev.copy(calendar = home.url, etag = "\"v2\""))) }
        val keys = f.repo.pendingKeys(0, Long.MAX_VALUE / 2)
        f.repo.moveEvent(ev, home.url)
        val body = f.body(f.sent("POST", "calendar/events/e1/move").single())
        assertEquals(home.url, body["calendar"]!!.jsonPrimitive.content)
        assertEquals("\"v1\"", body["etag"]!!.jsonPrimitive.content)
        assertEquals(setOf(home.url), f.repo.eventsOf("e1").map { it.calendar }.toSet())
        // The server's answer carries the new version, so the rows are confirmed.
        assertEquals(emptySet<String>(), keys.first())
        assertEquals("\"v2\"", f.repo.eventsOf("e1").first().etag)
    }

    /** calendar.sync: the accounts and "Synced n min ago" come from GET /calendar/sync-status. */
    @Test fun syncStatusGivesTheAccountsAndTheLastSync() = runBlocking {
        f.on("GET", "calendar/sync-status") { _, _ ->
            f.json("""{"state":"idle","last_sync_at":"2026-10-01T12:00:00+00:00","connectors":[{"id":3,"name":"Fastmail","status":"error",
                "detail":"401 Unauthorized","collections":[]}]}""")
        }
        f.repo.refreshSyncStatus()
        assertEquals("Fastmail", f.repo.accounts.value.single().name)
        assertEquals(OffsetDateTime.parse("2026-10-01T12:00:00+00:00").toInstant().toEpochMilli(), f.repo.lastSyncAt.value)
    }

    /** calendar.default_reminders: PATCH with {timed, all_day}; the collection keeps them for new events. */
    @Test fun defaultRemindersPerCalendar() = runBlocking {
        f.repo.setDefaultReminders(cal, listOf(10), listOf(-540))
        val body = f.body(f.sent("PATCH", "calendar/collections/1").single())
        assertEquals("[10]", body["default_reminders"]!!.jsonObject["timed"].toString())
        assertEquals(listOf(10), f.repo.collections.value.first { it.url == cal.url }.defaultRemindersFor(false))
        assertEquals(listOf(-540), f.repo.collections.value.first { it.url == cal.url }.defaultRemindersFor(true))
        f.repo.setDefaultReminders(f.repo.collections.value.first { it.url == cal.url }, null, null)
        assertEquals("true", f.body(f.sent("PATCH", "calendar/collections/1").last())["clear_default_reminders"]!!.jsonPrimitive.content)
    }

    /** task.remind_at: absolute reminders the phone knew stay absolute when the delta's copy counts them as minutes. */
    @Test fun absoluteRemindersSurviveTheDelta() = runBlocking {
        val due = start.toOffsetDateTime().toString()
        val known = TaskItem("t1", "Report", due, reminders = listOf(10), remindersAt = listOf(start.minusHours(2).toInstant().toString()), etag = "\"t1\"")
        f.db.calendar().putTasks(listOf(CalTaskEntity("t1", null, null, false, start.toInstant().toEpochMilli(),
            MailrJson.encodeToString(TaskItem.serializer(), known), etag = "\"t1\"")))
        val fromDelta = known.copy(reminders = listOf(10, 120), remindersAt = null, etag = "\"t2\"")
        f.on("GET", "calendar/delta") { _, _ ->
            f.json("""{"cursor":5,"more":false,"reset":false,"collections":[],"deleted_collections":[],
                "objects":[${FakeCalendarServer.taskJson(fromDelta)}],"deleted":[],"instances":{},"window":null,"server_time":"x"}""")
        }
        assertTrue(f.repo.sync())
        val t = f.repo.task("t1")!!
        assertEquals(listOf(10), t.reminders)
        assertEquals(known.remindersAt, t.remindersAt)
        assertEquals("\"t2\"", t.etag)
    }
}
