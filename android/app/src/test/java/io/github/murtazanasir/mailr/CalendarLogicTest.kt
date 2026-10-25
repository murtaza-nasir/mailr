package io.github.murtazanasir.mailr

import android.app.Application
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import io.github.murtazanasir.mailr.data.api.CalendarApi
import io.github.murtazanasir.mailr.data.api.EventDraft
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.TaskDraft
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.ui.calendar.EventLayout
import io.github.murtazanasir.mailr.ui.calendar.Freq
import io.github.murtazanasir.mailr.ui.calendar.QuickAdd
import io.github.murtazanasir.mailr.ui.calendar.QuickDates
import io.github.murtazanasir.mailr.ui.calendar.Recurrence
import io.github.murtazanasir.mailr.ui.calendar.RecurrenceEnd
import io.github.murtazanasir.mailr.data.cal.TaskQueries
import io.github.murtazanasir.mailr.data.reminders.ReminderPlan
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.DayOfWeek
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.ZoneId
import java.time.ZonedDateTime

@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class CalendarLogicTest {

    // ---------------- recurrence ----------------

    @Test fun rruleRoundTrips() {
        listOf("FREQ=DAILY", "FREQ=WEEKLY;BYDAY=MO,WE,FR", "FREQ=WEEKLY;INTERVAL=2;BYDAY=TU;COUNT=10",
            "FREQ=MONTHLY;BYDAY=-1FR", "FREQ=MONTHLY;BYMONTHDAY=15;UNTIL=20261231T235959Z", "FREQ=YEARLY").forEach { r ->
            assertEquals(r, Recurrence.parse(r)!!.toRRule())
        }
        assertNull(Recurrence.parse(null))
        assertNull(Recurrence.parse(""))
    }

    @Test fun unknownRulesArePreservedVerbatim() {
        val r = Recurrence.parse("RRULE:FREQ=MONTHLY;BYSETPOS=2;BYDAY=MO,TU")!!
        assertEquals("FREQ=MONTHLY;BYSETPOS=2;BYDAY=MO,TU", r.toRRule())
        assertEquals("Custom repeat", r.describe())
    }

    @Test fun recurrenceDescriptions() {
        assertEquals("Every weekday", Recurrence(Freq.WEEKLY, byDay = Recurrence.WEEKDAYS).describe(java.util.Locale.US))
        assertEquals("Every 2 weeks on Mon, Thu, 5 times",
            Recurrence(Freq.WEEKLY, 2, setOf(DayOfWeek.THURSDAY, DayOfWeek.MONDAY), end = RecurrenceEnd.Count(5)).describe(java.util.Locale.US))
        assertEquals("Monthly on the last Friday", Recurrence.parse("FREQ=MONTHLY;BYDAY=-1FR")!!.describe(java.util.Locale.US))
        assertEquals("Daily, until Dec 31, 2026", Recurrence.parse("FREQ=DAILY;UNTIL=20261231")!!.describe(java.util.Locale.US))
    }

    @Test fun presetsFollowTheStartDate() {
        // Tue Sep 29 2026 is the 5th Tuesday? No: 29 is in the last week, so "last Tuesday".
        val p = Recurrence.presets(LocalDate.of(2026, 9, 29), java.util.Locale.US).toMap()
        assertTrue(p.containsKey("Weekly on Tuesday"))
        assertEquals("FREQ=MONTHLY;BYDAY=-1TU", p["Monthly on the last Tuesday"]!!.toRRule())
        assertEquals("FREQ=MONTHLY;BYDAY=2TU", Recurrence.presets(LocalDate.of(2026, 9, 8), java.util.Locale.US)
            .first { it.first.startsWith("Monthly on the") }.second!!.toRRule())
    }

    // ---------------- quick add ----------------

    private val now = LocalDateTime.of(2026, 9, 29, 12, 0) // a Tuesday

    @Test fun quickAddReadsDatesTimesAndPriority() {
        QuickAdd.parse("Call the bank tomorrow 3pm", now).let {
            assertEquals("Call the bank", it.title)
            assertEquals(LocalDate.of(2026, 9, 30), it.date)
            assertEquals(LocalTime.of(15, 0), it.time)
        }
        QuickAdd.parse("Pay rent on friday !!", now).let {
            assertEquals("Pay rent", it.title)
            assertEquals(LocalDate.of(2026, 10, 2), it.date)
            assertEquals(5, it.priority)
        }
        QuickAdd.parse("standup at 9:30", now).let {
            assertEquals("standup", it.title)
            assertEquals(LocalTime.of(9, 30), it.time)
            assertEquals(LocalDate.of(2026, 9, 30), it.date) // 9:30 has passed today
        }
        QuickAdd.parse("stretch in 2 hours", now).let {
            assertEquals("stretch", it.title)
            assertEquals(LocalTime.of(14, 0), it.time)
        }
        QuickAdd.parse("report next week !!!", now).let {
            assertEquals(LocalDate.of(2026, 10, 5), it.date)
            assertEquals(1, it.priority)
        }
    }

    @Test fun quickAddLeavesOrdinaryTitlesAlone() {
        listOf("Monday.com invoice", "Read Sunday papers", "Buy 3 apples", "Fix issue #12").forEach { t ->
            val p = QuickAdd.parse(t, now)
            assertEquals(t, p.title)
            assertNull(t, p.date)
        }
        // A weekday at the end, or after "on", is a date.
        assertEquals(LocalDate.of(2026, 10, 4), QuickAdd.parse("Call mom sunday", now).date)
        assertEquals(LocalTime.of(17, 0), QuickAdd.parse("Call mom sunday 5pm", now).time)
        assertEquals("Call mom", QuickAdd.parse("Call mom sunday 5pm", now).title)
    }

    @Test fun quickPresets() {
        assertEquals(LocalDateTime.of(2026, 9, 29, 15, 0), QuickDates.defaultEventStart(LocalDateTime.of(2026, 9, 29, 14, 37)))
        assertEquals(LocalDateTime.of(2026, 9, 29, 15, 0), QuickDates.defaultEventStart(LocalDateTime.of(2026, 9, 29, 14, 30)))
        val due = QuickDates.taskDuePresets(LocalDate.of(2026, 9, 29)).associate { it.label to it.date }
        assertEquals(LocalDate.of(2026, 10, 3), due["This weekend"])
        assertEquals(LocalDate.of(2026, 10, 5), due["Next week"])
        assertFalse(QuickDates.taskDuePresets(LocalDate.of(2026, 10, 2)).any { it.label == "This weekend" })
        assertEquals("On the day, 9 am", QuickDates.reminderLabel(-540, true))
        assertEquals("Day before, 9 am", QuickDates.reminderLabel(900, true))
        assertEquals("1 hour before", QuickDates.reminderLabel(60, false))
        assertEquals("45 min before", QuickDates.reminderLabel(45, false))
    }

    // ---------------- time grid layout ----------------

    data class B(val s: Int, val e: Int)

    @Test fun overlappingEventsShareTheWidth() {
        val placed = EventLayout.layout(listOf(B(540, 600), B(570, 630), B(600, 660), B(720, 780)), { it.s }, { it.e })
            .associateBy { it.item }
        assertEquals(2, placed[B(540, 600)]!!.lanes)
        assertEquals(0, placed[B(540, 600)]!!.lane)
        assertEquals(1, placed[B(570, 630)]!!.lane)
        // 10:00 reuses lane 0 (the 9:00 block has ended).
        assertEquals(0, placed[B(600, 660)]!!.lane)
        // A separate cluster gets the full width.
        assertEquals(1, placed[B(720, 780)]!!.lanes)
    }

    @Test fun tinyEventsGetAMinimumHeight() {
        val p = EventLayout.layout(listOf(B(600, 600)), { it.s }, { it.e }).single()
        assertEquals(EventLayout.MIN_MINUTES, p.endMin - p.startMin)
    }

    // ---------------- repeating tasks and absolute reminders ----------------

    /** The series question for a new due of a repeating task (task.reschedule). */
    @Test fun repeatRuleFollowsTheScopeOfADueChange() {
        val z = ZoneId.of("America/Chicago")
        // Weekly from the due (Mon 5 Oct): "this" pins the old weekday; "all" lets it follow (no change needed).
        assertEquals("FREQ=WEEKLY;BYDAY=MO", TaskQueries.ruleAfterMove("FREQ=WEEKLY", "2026-10-05", "2026-10-07", "this", z))
        assertNull(TaskQueries.ruleAfterMove("FREQ=WEEKLY", "2026-10-05", "2026-10-07", "all", z))
        // A single BYDAY that matched the old day moves with "all" and stays with "this".
        assertEquals("FREQ=WEEKLY;BYDAY=WE", TaskQueries.ruleAfterMove("FREQ=WEEKLY;BYDAY=MO", "2026-10-05", "2026-10-07", "all", z))
        assertNull(TaskQueries.ruleAfterMove("FREQ=WEEKLY;BYDAY=MO", "2026-10-05", "2026-10-07", "this", z))
        // Monthly by day of the month; a time of day on the same date changes nothing.
        assertEquals("FREQ=MONTHLY;INTERVAL=1;BYMONTHDAY=5",
            TaskQueries.ruleAfterMove("FREQ=MONTHLY;INTERVAL=1", "2026-10-05", "2026-10-09", "this", z))
        assertEquals("FREQ=MONTHLY;BYMONTHDAY=9", TaskQueries.ruleAfterMove("FREQ=MONTHLY;BYMONTHDAY=5", "2026-10-05", "2026-10-09", "all", z))
        assertNull(TaskQueries.ruleAfterMove("FREQ=DAILY", "2026-10-05", "2026-10-09", "this", z))
        assertNull(TaskQueries.ruleAfterMove("FREQ=WEEKLY", "2026-10-05T14:00:00Z", "2026-10-05T16:00:00Z", "this", z))
    }

    /** task.remind_at: absolute reminders plan as minutes before the due, and survive a delta copy that only has minutes. */
    @Test fun absoluteRemindersKeepTheirInstant() {
        val z = ZoneId.of("UTC")
        val t = TaskItem("a", "Report", "2026-10-05T15:00:00Z", reminders = listOf(10), remindersAt = listOf("2026-10-05T13:30:00Z"))
        assertEquals(listOf(90), ReminderPlan.absoluteMinutes(t, z))
        // The delta counts the absolute one in `reminders` and has no `reminders_at`.
        val fromDelta = t.copy(reminders = listOf(10, 90), remindersAt = null)
        val kept = TaskQueries.keepAbsolute(t, fromDelta, z)
        assertEquals(listOf(10), kept.reminders)
        assertEquals(listOf("2026-10-05T13:30:00Z"), kept.remindersAt)
        // A moved due: the phone no longer knows which minute is absolute; the delta copy stands.
        assertEquals(fromDelta.copy(due = "2026-10-06T15:00:00Z"), TaskQueries.keepAbsolute(t, fromDelta.copy(due = "2026-10-06T15:00:00Z"), z))
        // The body carries reminders_at only when the draft sets it.
        val body = MailrJson.parseToJsonElement(CalendarApi.taskBody(TaskDraft("a", "Report", remindersAt = listOf("2026-10-05T13:30:00Z")), create = false)).jsonObject
        assertEquals("2026-10-05T13:30:00Z", (body["reminders_at"] as kotlinx.serialization.json.JsonArray).single().jsonPrimitive.content)
        assertNull(MailrJson.parseToJsonElement(CalendarApi.taskBody(TaskDraft("a", "Report"), create = false)).jsonObject["reminders_at"])
    }

    // ---------------- request bodies (also stored in the offline queue) ----------------

    @Test fun eventPatchSendsRecurrenceOnlyWhenChanged() {
        val d = EventDraft("u1@x", "T", "2026-09-30T10:00:00-05:00", "2026-09-30T11:00:00-05:00", false, reminders = listOf(10))
        val create = MailrJson.parseToJsonElement(CalendarApi.eventBody(d.copy(rrule = "FREQ=DAILY"), create = true)).jsonObject
        assertEquals("u1@x", create["uid"]!!.jsonPrimitive.content)
        assertEquals("FREQ=DAILY", create["recurrence"]!!.jsonPrimitive.content)
        val patch = MailrJson.parseToJsonElement(CalendarApi.eventBody(d, create = false, rruleChanged = false)).jsonObject
        assertNull(patch["uid"])
        assertNull(patch["recurrence"])
        val cleared = MailrJson.parseToJsonElement(CalendarApi.eventBody(d, create = false, rruleChanged = true)).jsonObject
        assertEquals("", cleared["recurrence"]!!.jsonPrimitive.content)
    }

    @Test fun taskPatchClearsDueAndParent() {
        val t = TaskDraft("t@x", "T", due = null, parent = null)
        val patch = MailrJson.parseToJsonElement(CalendarApi.taskBody(t, create = false)).jsonObject
        assertEquals("true", patch["clear_due"]!!.jsonPrimitive.content)
        assertEquals("", patch["parent"]!!.jsonPrimitive.content)
        val create = MailrJson.parseToJsonElement(CalendarApi.taskBody(t.copy(parent = "p@x"), create = true)).jsonObject
        assertNotNull(create["uid"])
        assertEquals("p@x", create["parent"]!!.jsonPrimitive.content)
        assertNull(create["clear_due"])
    }
}
