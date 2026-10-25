// ACT-SURFACES: android.editor
package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.time.AppClock
import android.app.Application
import io.github.murtazanasir.mailr.data.api.CalendarApi
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.EventDraft
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.ReminderSettings
import io.github.murtazanasir.mailr.data.db.CalEventEntity
import io.github.murtazanasir.mailr.data.reminders.ReminderConfig
import io.github.murtazanasir.mailr.data.reminders.ReminderPlan
import io.github.murtazanasir.mailr.ui.calendar.EventEditorState
import io.github.murtazanasir.mailr.ui.calendar.QuickSeed
import io.github.murtazanasir.mailr.ui.calendar.ownReminders
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZoneOffset
import java.time.ZonedDateTime

/**
 * Default reminders on the phone (event.create on android.editor): a new event shows the effective default
 * as chips (the calendar's, else the account's from /reminders/settings) and, left untouched,
 * creates without `reminders` so the server writes that default. Changed chips go as a list, []
 * means "no reminders" (the server marks the event), and the phone's planner gives a marked event
 * no default either. Editor state is only constructed here, never written outside a composition.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class NoReminderTest {
    private val f = FakeCalendarServer()
    private val tomorrow = AppClock.today().plusDays(1)
    private val user = { allDay: Boolean -> if (allDay) listOf(900) else listOf(1440) }

    @Before fun setUp() { f.start() }
    @After fun tearDown() = f.stop()

    private fun body(d: EventDraft, create: Boolean): JsonObject =
        MailrJson.parseToJsonElement(CalendarApi.eventBody(d, create)).jsonObject

    private fun ints(o: JsonObject) = o["reminders"]!!.jsonArray.map { it.jsonPrimitive.content.toInt() }

    @Test fun editorSeedsTheUserDefaultAndLeavesItOutWhenUntouched() {
        val s = EventEditorState(null, null, tomorrow.atTime(10, 0), "/cal/work/", 10, "n1", userDefaults = user)
        assertEquals(listOf(1440), s.reminders)
        val d = s.draft()
        assertEquals(false, d.sendReminders)
        assertFalse("reminders" in body(d, create = true))
        // All-day: the all-day default.
        val day = EventEditorState(null, null, tomorrow.atTime(9, 0), null, 10, "n2", userDefaults = user,
            template = CalendarEvent("n2", "Offsite", tomorrow.toString(), tomorrow.plusDays(1).toString(), allDay = true))
        assertEquals(listOf(900), day.reminders)
        assertFalse("reminders" in body(day.draft(), create = true))
        // The calendar's own defaults come first.
        val cal = EventEditorState(null, null, tomorrow.atTime(10, 0), "/cal/team/", 10, "n3", userDefaults = user,
            defaultsFor = { url, _ -> if (url == "/cal/team/") listOf(30) else null })
        assertEquals(listOf(30), cal.reminders)
        // Without the account's settings, the phone's own reminder (timed only).
        assertEquals(listOf(10), EventEditorState(null, null, tomorrow.atTime(10, 0), null, 10, "n4").reminders)
    }

    @Test fun chosenRemindersGoAsAListAndNoneAsEmpty() {
        val start = tomorrow.atTime(10, 0)
        val own = EventEditorState(null, null, start, null, 10, "q1", userDefaults = user,
            seed = QuickSeed("Standup", start, start.plusMinutes(15), false, reminders = listOf(30)))
        assertEquals(true, own.draft().sendReminders)
        assertEquals(listOf(30), ints(body(own.draft(), create = true)))
        val none = EventEditorState(null, null, start, null, 10, "q2", userDefaults = user,
            seed = QuickSeed("Quiet", start, start.plusMinutes(15), false, reminders = emptyList()))
        assertEquals(emptyList<Int>(), ints(body(none.draft(), create = true)))
    }

    @Test fun editingSendsRemindersOnlyWhenTheyChanged() {
        val z = AppClock.now(ZoneOffset.UTC).plusDays(1)
        val e = CalendarEvent("e1", "Call", z.toOffsetDateTime().toString(), z.plusHours(1).toOffsetDateTime().toString())
        val s = EventEditorState(e, null, z.toLocalDateTime(), null, 10, e.uid, userDefaults = user)
        assertEquals(emptyList<Int>(), s.reminders)
        assertEquals(false, s.draft().sendReminders)
        assertFalse("reminders" in body(s.draft(), create = false))
    }

    @Test fun repositoryCreatesAndPatches() = runBlocking {
        val z = AppClock.now(ZoneOffset.UTC).plusDays(1).withHour(15).withMinute(0).withSecond(0).withNano(0)
        val start = z.toOffsetDateTime().toString()
        val end = z.plusHours(1).toOffsetDateTime().toString()
        f.on("POST", "calendar/events") { _, b -> f.json(b) }
        f.repo.createEvent(EventDraft("c1", "Default", start, end, false, reminders = listOf(1440), sendReminders = false))
        f.repo.createEvent(EventDraft("c2", "Silent", start, end, false, reminders = emptyList(), sendReminders = true))
        val posts = f.sent("POST", "calendar/events")
        assertFalse("reminders" in f.body(posts[0]))
        assertEquals(emptyList<Int>(), ints(f.body(posts[1])))
        // The phone's copy: the chips until the server answers; [] is marked.
        assertFalse(f.repo.eventsOf("c1").single().noReminders)

        // A patch with the reminders unchanged leaves them out; set on purpose, [] goes.
        val e = CalendarEvent("p1", "Plain", start, end)
        f.db.calendar().putEvents(listOf(CalEventEntity(e.key, "p1", z.toInstant().toEpochMilli(), z.plusHours(1).toInstant().toEpochMilli(),
            false, null, MailrJson.encodeToString(CalendarEvent.serializer(), e))))
        f.on("PATCH", "calendar/events/p1") { _, _ -> f.json(MailrJson.encodeToString(CalendarEvent.serializer(), e)) }
        f.repo.updateEvent(e, EventDraft("p1", "Plain, moved", start, end, false), "all", rruleChanged = false)
        f.repo.updateEvent(e, EventDraft("p1", "Plain", start, end, false, sendReminders = true), "all", rruleChanged = false)
        val patches = f.sent("PATCH", "calendar/events/p1")
        assertFalse("reminders" in f.body(patches[0]))
        assertEquals(emptyList<Int>(), ints(f.body(patches[1])))
    }

    @Test fun copiesCarryRemindersOnlyWhenTheEventHasThem() {
        assertFalse(CalendarEvent("a", "x", "2026-10-05").ownReminders)
        assertTrue(CalendarEvent("a", "x", "2026-10-05", noReminders = true).ownReminders)
        assertTrue(CalendarEvent("a", "x", "2026-10-05", reminders = listOf(5)).ownReminders)
    }

    @Test fun localPlannerHonoursTheMarker() {
        val zone = ZoneId.of("America/Chicago")
        val now = ZonedDateTime.of(2026, 9, 29, 12, 0, 0, 0, zone)
        val cfg = ReminderConfig(defaultTimedEvent = 10, defaultAllDayEvent = 900)
        fun iso(t: ZonedDateTime) = t.toOffsetDateTime().toString()
        val plain = CalendarEvent("a", "Plain", iso(now.plusHours(2)), iso(now.plusHours(3)))
        val quiet = CalendarEvent("b", "Quiet", iso(now.plusHours(2)), iso(now.plusHours(3)), noReminders = true)
        val quietDay = CalendarEvent("c", "Quiet day", "2026-10-01", "2026-10-02", allDay = true, noReminders = true)
        val p = ReminderPlan.plan(listOf(plain, quiet, quietDay), emptyList(), cfg, now.toInstant().toEpochMilli(), zone)
        assertEquals(listOf("a"), p.map { it.itemUid })
        assertEquals(listOf(10), p.map { it.key.substringAfterLast('|').toInt() })
    }

    @Test fun jsonAndSettings() {
        val e = MailrJson.decodeFromString(CalendarEvent.serializer(), """{"uid":"u","start":"2026-10-05","no_reminders":true}""")
        assertTrue(e.noReminders)
        assertFalse(MailrJson.decodeFromString(CalendarEvent.serializer(), """{"uid":"u","start":"2026-10-05"}""").noReminders)
        val s = ReminderSettings(defaultTimedEvent = 1440, defaultAllDayEvent = 900)
        assertEquals(listOf(1440), s.eventDefaults(false))
        assertEquals(listOf(900), s.eventDefaults(true))
        assertEquals(emptyList<Int>(), s.copy(events = false).eventDefaults(false))
        assertEquals(emptyList<Int>(), s.copy(defaultTimedEvent = null).eventDefaults(false))
    }
}
