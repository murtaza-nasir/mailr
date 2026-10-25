// ACT-SURFACES: android.calendar
package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.time.AppClock
import android.app.Application
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.cal.CalendarRepository
import io.github.murtazanasir.mailr.data.reminders.ReminderConfig
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.time.LocalDate
import java.time.ZoneOffset

/**
 * ACT calendar.sync on Android: GET /calendar/delta with a stored cursor — pages, series
 * instances, tombstones, `reset`, the device visibility override, the fallback to the agenda on a
 * server without the route, live change entities and the reminder settings the planner uses. calendar.toggle_visibility is the device override.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class CalendarDeltaTest {
    private val f = FakeCalendarServer()
    private val today: LocalDate = AppClock.today(ZoneOffset.UTC)
    private fun at(days: Long, hour: Int) = "${today.plusDays(days)}T${"%02d".format(hour)}:00:00+00:00"
    private fun ms(days: Long, hour: Int) = today.plusDays(days).atTime(hour, 0).toInstant(ZoneOffset.UTC).toEpochMilli()

    private val cal = CalendarCollection(url = "https://dav/cal/", name = "Work", color = "#1a73e8", events = true, calendarId = 1)
    private val list = CalendarCollection(url = "https://dav/list/", name = "Home", color = "#188038", tasks = true, calendarId = 2)
    private val single = CalendarEvent("s1", "Dentist", at(1, 9), at(1, 10), calendar = cal.url, etag = "\"s-1\"", id = 11,
        occurrenceId = at(1, 9))
    private val series = CalendarEvent("r1", "Standup", at(0, 14), at(0, 15), calendar = cal.url, etag = "\"r-1\"", id = 12,
        recurring = true, rrule = "FREQ=DAILY", recurrenceId = at(0, 14), occurrenceId = at(0, 14))
    private val task = TaskItem("t1", "Pay rent", today.plusDays(2).toString(), dueAllDay = true, list = list.url, etag = "\"t-1\"", id = 21)

    private fun instance(day: Long, summary: String? = null, cancelled: Boolean = false, override: Boolean = false) =
        """{"occurrence":"${at(day, 14)}","start":"${at(day, 14)}","end":"${at(day, 15)}","all_day":false,
           "is_override":$override,"cancelled":$cancelled,"summary":${summary?.let { "\"$it\"" } ?: "null"},"location":null,"status":null}"""

    private fun collections(vararg cs: CalendarCollection) =
        cs.joinToString(",", "[", "]") { MailrJson.encodeToString(CalendarCollection.serializer(), it) }

    private fun objects(vararg os: JsonObject) = JsonArray(os.toList()).toString()

    private fun delta(cursor: Long, more: Boolean = false, reset: Boolean = false, collections: String = "[]", objects: String = "[]",
                      deleted: String = "[]", deletedCollections: String = "[]", instances: String = "{}") = f.json(
        """{"cursor":$cursor,"more":$more,"reset":$reset,"collections":$collections,"deleted_collections":$deletedCollections,
           "objects":$objects,"deleted":$deleted,"instances":$instances,
           "window":{"from":"${today.minusDays(30)}","to":"${today.plusDays(180)}"},"server_time":"x"}""")

    private fun cursorOf(r: Pair<okhttp3.mockwebserver.RecordedRequest, String>) = r.first.requestUrl!!.queryParameter("cursor")

    @Before fun setUp() { f.start() }
    @After fun tearDown() = f.stop()

    /** The first sync: two pages from cursor 0, then everything is in Room. */
    private fun firstSync() = runBlocking {
        f.on("GET", "calendar/delta") { r, _ ->
            when (r.requestUrl!!.queryParameter("cursor")) {
                "0" -> delta(50, more = true, collections = collections(cal, list), objects = objects(FakeCalendarServer.eventJson(single)))
                else -> delta(100, objects = objects(FakeCalendarServer.eventJson(series), FakeCalendarServer.taskJson(task)),
                    instances = """{"r1":[${instance(0)},${instance(1, summary = "Standup (moved room)", override = true)}]}""")
            }
        }
        assertTrue(f.repo.sync())
    }

    @Test fun firstSyncPagesThroughTheDelta() = runBlocking {
        firstSync()
        assertEquals(listOf("0", "50"), f.sent("GET", "calendar/delta").map(::cursorOf))
        val first = f.sent("GET", "calendar/delta").first().first.requestUrl!!
        assertEquals(today.minusDays(30).toString(), first.queryParameter("window_from"))
        assertEquals(today.plusDays(180).toString(), first.queryParameter("window_to"))
        assertEquals(true, f.repo.deltaSupported)
        assertEquals("100", f.db.cache().get(CalendarRepository.KV_DELTA_CURSOR))

        val events = f.repo.eventsNow(ms(-1, 0), ms(5, 0)).associateBy { it.key }
        assertEquals(setOf("s1|${at(1, 9)}", "r1|${at(0, 14)}", "r1|${at(1, 14)}"), events.keys)
        assertEquals("Standup (moved room)", events.getValue("r1|${at(1, 14)}").summary)
        assertEquals("\"r-1\"", events.getValue("r1|${at(1, 14)}").etag)
        assertEquals("FREQ=DAILY", f.repo.master("r1")!!.rrule)
        assertEquals("\"t-1\"", f.db.calendar().task("t1")!!.etag)
        // In the user's order, then by name.
        assertEquals(listOf("https://dav/list/", "https://dav/cal/"), f.repo.collections.value.map { it.url })
        assertEquals(true, f.repo.connected.value)
        // The collection's colour reaches the event when read.
        assertEquals("#1a73e8", f.repo.events(ms(-1, 0), ms(5, 0)).first().first { it.uid == "s1" }.color)
    }

    @Test fun tombstonesAndChangedSeriesApply() = runBlocking {
        firstSync()
        f.on("GET", "calendar/delta") { r, _ ->
            assertEquals("100", r.requestUrl!!.queryParameter("cursor"))
            delta(130, objects = objects(FakeCalendarServer.eventJson(series.copy(summary = "Daily standup", etag = "\"r-2\""))),
                instances = """{"r1":[${instance(0, cancelled = true)},${instance(1)},${instance(2)}]}""",
                deleted = """[{"id":11,"uid":"s1","kind":"event"},{"id":21,"uid":"t1","kind":"task"}]""")
        }
        assertTrue(f.repo.sync())
        val keys = f.repo.eventsNow(ms(-1, 0), ms(5, 0)).map { it.key }.toSet()
        // The cancelled instance and the deleted event are gone; the series has its new instances.
        assertEquals(setOf("r1|${at(1, 14)}", "r1|${at(2, 14)}"), keys)
        assertEquals("Daily standup", f.repo.eventInstance("r1|${at(2, 14)}")!!.summary)
        assertEquals("\"r-2\"", f.db.calendar().master("r1")!!.etag)
        assertNull(f.db.calendar().task("t1"))
        assertNull(f.db.calendar().master("s1"))
        assertEquals("130", f.db.cache().get(CalendarRepository.KV_DELTA_CURSOR))
    }

    @Test fun aDeletedCollectionTakesItsItems() = runBlocking {
        firstSync()
        f.on("GET", "calendar/delta") { _, _ -> delta(140, deletedCollections = """[{"calendar_id":2,"url":""}]""") }
        assertTrue(f.repo.sync())
        assertNull(f.db.calendar().task("t1"))
        assertEquals(listOf("https://dav/cal/"), f.repo.collections.value.map { it.url })
    }

    @Test fun resetStartsAgainAndDropsWhatTheServerNoLongerHas() = runBlocking {
        firstSync()
        f.on("GET", "calendar/delta") { r, _ ->
            when (r.requestUrl!!.queryParameter("cursor")) {
                "100" -> delta(0, reset = true)
                // The fresh full answer has only the series: no tombstones for s1 and t1.
                else -> delta(500, collections = collections(cal, list), objects = objects(FakeCalendarServer.eventJson(series)),
                    instances = """{"r1":[${instance(0)}]}""")
            }
        }
        assertTrue(f.repo.sync())
        assertEquals(listOf("100", "0"), f.sent("GET", "calendar/delta").drop(2).map(::cursorOf))
        assertEquals(setOf("r1|${at(0, 14)}"), f.repo.eventsNow(ms(-1, 0), ms(5, 0)).map { it.key }.toSet())
        assertNull(f.db.calendar().task("t1"))
        assertEquals("500", f.db.cache().get(CalendarRepository.KV_DELTA_CURSOR))
    }

    @Test fun aServerWithoutDeltaFallsBackToTheAgenda() = runBlocking {
        f.on("GET", "calendar/calendars") { _, _ -> f.json(collections(cal, list)) }
        f.on("GET", "calendar/agenda") { _, _ ->
            f.json("""{"connected":true,"events":[${MailrJson.encodeToString(CalendarEvent.serializer(), single.copy(occurrenceId = null, etag = null))}]}""")
        }
        f.on("GET", "tasks") { _, _ -> f.json("""{"connected":true,"items":[${MailrJson.encodeToString(TaskItem.serializer(), task)}]}""") }
        assertTrue(f.repo.sync())
        assertEquals(false, f.repo.deltaSupported)
        assertEquals(1, f.sent("GET", "calendar/agenda").size)
        // Without the server's `occurrence`, the key still uses the canonical string.
        assertEquals("s1|${at(1, 9)}", f.repo.eventsNow(ms(-1, 0), ms(5, 0)).single().key)
        assertEquals("Pay rent", f.repo.task("t1")!!.summary)
        // The route is not asked for again until the app restarts.
        assertTrue(f.repo.sync())
        assertEquals(1, f.sent("GET", "calendar/delta").size)
    }

    @Test fun theDeviceVisibilityOverrideSurvivesSyncs() = runBlocking {
        firstSync()
        f.repo.setVisibleOnDevice(cal.url, false)
        f.on("GET", "calendar/delta") { _, _ -> delta(150, collections = collections(cal.copy(name = "Work (renamed)"))) }
        assertTrue(f.repo.sync())
        val c = f.repo.collections.value.first { it.url == cal.url }
        assertEquals("Work (renamed)", c.name)
        assertEquals(false, c.visible)
        assertTrue(f.repo.events(ms(-1, 0), ms(5, 0)).first().isEmpty())
        f.repo.setVisibleOnDevice(cal.url, null)
        assertEquals(3, f.repo.events(ms(-1, 0), ms(5, 0)).first().size)
    }

    @Test fun liveChangeEntitiesAskForADelta() {
        assertTrue(f.repo.wantsSync(setOf("event")))
        assertTrue(f.repo.wantsSync(setOf("conversation", "calendar")))
        assertTrue(f.repo.wantsSync(setOf("task")))
        assertFalse(f.repo.wantsSync(setOf("conversation", "label", "reminder")))
    }

    @Test fun reminderSettingsComeFromTheServerAndKeepAnExplicitNull() = runBlocking {
        f.on("GET", "reminders/settings") { _, _ ->
            f.json("""{"events":true,"tasks":false,"default_timed_event":null,"default_all_day_event":-540,"default_task":true,
                "morning_hour":8,"web":{"desktop":true,"push":false}}""")
        }
        firstSync()
        val phone = ReminderConfig(defaultTimedEvent = 30)
        val cfg = f.repo.reminderConfig(phone)
        assertNull(cfg.defaultTimedEvent)
        assertEquals(-540, cfg.defaultAllDayEvent)
        assertFalse(cfg.tasks)
        assertEquals(8, cfg.morningHour)
        // The cache keeps the explicit null (a fresh repository on the same database).
        val again = CalendarRepository(f.repo.api, f.db, f.mail, now = { f.now }, zone = { f.zone })
        again.loadCached()
        assertNull(again.reminderConfig(phone).defaultTimedEvent)
        // Without server settings the phone's own apply.
        val none = CalendarRepository(f.repo.api, f.db, f.mail)
        assertEquals(30, none.reminderConfig(phone).defaultTimedEvent)
    }

    @Test fun collectionsWithNotifyOffNeverRemind() = runBlocking {
        f.on("GET", "calendar/delta") { _, _ -> delta(10, collections = collections(cal.copy(notify = false), list)) }
        assertTrue(f.repo.sync())
        assertEquals(setOf(cal.url), f.repo.reminderConfig(ReminderConfig()).silentCollections)
    }
}
