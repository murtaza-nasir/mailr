package io.github.murtazanasir.mailr

import android.app.Application
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import io.github.murtazanasir.mailr.data.reminders.SpanUnit
import io.github.murtazanasir.mailr.data.reminders.WhenSheet
import io.github.murtazanasir.mailr.data.reminders.WhenSheet.Base
import io.github.murtazanasir.mailr.data.reminders.WhenSheet.Spec
import io.github.murtazanasir.mailr.data.reminders.WhenSheet.Verb
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.ZoneId
import java.time.ZonedDateTime

/**
 * The when sheet's presets and custom value, the same cases as frontend/src/lib/whenSheet.test.ts.
 * Covers task.snooze, task.reschedule, event.snooze, event.reschedule, event.remind_before.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class WhenSheetTest {
    private val z = ZoneId.of("America/Chicago")
    private fun t(mo: Int, d: Int, h: Int, m: Int = 0) = ZonedDateTime.of(2026, mo, d, h, m, 0, 0, z) // 1 Oct 2026 is a Thursday
    private fun ids(s: Spec) = WhenSheet.groups(s).map { g -> "${g.id}: " + g.tiles.joinToString(" ") { it.id } }
    private fun tile(s: Spec, id: String) = WhenSheet.groups(s).flatMap { it.tiles }.first { it.id == id }

    /** task.snooze: Later, Same time from the original due time, Next. */
    @Test fun taskSnoozeGroups() {
        // "Record the class", due today 12:00 PM, now 4:00 PM (overdue 4 h).
        val s = Spec(Verb.SNOOZE, true, t(10, 1, 16), t(10, 1, 12), morningHour = 8)
        assertEquals(listOf("later: 10m 60m 180m evening", "same: same-tomorrow same-week same-month", "next: morning monday weekend"), ids(s))
        assertEquals(listOf("Later", "Same time", "Next"), WhenSheet.groups(s).map { it.label })
        assertEquals(t(10, 2, 12), tile(s, "same-tomorrow").at)
        assertEquals(t(10, 8, 12), tile(s, "same-week").at)
        assertEquals(t(11, 1, 12), tile(s, "same-month").at)
        assertEquals(t(10, 2, 8), tile(s, "morning").at)
        assertEquals(t(10, 5, 8), tile(s, "monday").at)
        assertEquals("This weekend", tile(s, "weekend").label)
        assertEquals(t(10, 3, 8), tile(s, "weekend").at)
        assertEquals("In 1 hour", tile(s, "60m").label)
        assertEquals("In 3 hours", tile(s, "180m").label)
    }

    /** Presets in the past and duplicates are left out. */
    @Test fun pastAndDuplicatesHidden() {
        val s = Spec(Verb.SNOOZE, true, t(10, 1, 17, 30), t(10, 1, 8), morningHour = 8)
        assertEquals(listOf("later: 10m 60m 180m", "same: same-tomorrow same-week same-month", "next: monday weekend"), ids(s))
        assertEquals(listOf("later: 5m 10m"), ids(Spec(Verb.SNOOZE, false, t(10, 1, 10), t(10, 1, 10, 1))))
    }

    @Test fun noSameTimeWithoutATimedDue() {
        assertEquals(listOf("later: 10m 60m 180m evening", "next: morning monday weekend"), ids(Spec(Verb.SNOOZE, true, t(10, 1, 10), null)))
        assertFalse(ids(Spec(Verb.SNOOZE, true, t(10, 1, 10), t(10, 1, 9), dateOnly = true)).any { it.startsWith("same") })
    }

    /** task.reschedule: a date-only due stays date-only for Same time and Next. */
    @Test fun rescheduleDateOnly() {
        val s = Spec(Verb.RESCHEDULE, true, t(10, 1, 10), t(9, 29, 9), dateOnly = true)
        assertEquals(listOf("later: 60m 180m evening", "same: same-tomorrow same-week same-month", "next: monday weekend"), ids(s))
        assertTrue(tile(s, "same-tomorrow").dateOnly)
        assertFalse(tile(s, "60m").dateOnly)
    }

    @Test fun weekendOnSaturday() {
        val s = Spec(Verb.SNOOZE, true, t(10, 3, 10), null)
        assertEquals("Next weekend", tile(s, "weekend").label)
        assertEquals(t(10, 10, 9), tile(s, "weekend").at)
    }

    /** event.snooze, event.reschedule. */
    @Test fun eventGroups() {
        assertEquals(listOf("later: 5m 10m start"), ids(Spec(Verb.SNOOZE, false, t(10, 1, 10), t(10, 1, 10, 30))))
        assertEquals(listOf("later: 5m 15m"), ids(Spec(Verb.SNOOZE, false, t(10, 1, 10), t(10, 1, 9))))
        val r = Spec(Verb.RESCHEDULE, false, t(10, 1, 10), t(10, 1, 15))
        assertEquals(t(10, 1, 15, 15), tile(r, "p15").at)
        assertEquals(t(10, 8, 15), tile(r, "same-week").at)
    }

    /** event.remind_before: only the offsets ahead, each with its minutes. */
    @Test fun remindBefore() {
        val g = WhenSheet.groups(Spec(Verb.REMIND, false, t(10, 1, 10), t(10, 1, 10, 40)))
        assertEquals(listOf("Before it starts"), g.map { it.label })
        assertEquals(listOf(0, 5, 10, 15, 30), g[0].tiles.map { it.minutes })
        assertEquals(t(10, 2, 13), WhenSheet.custom(Spec(Verb.REMIND, false, t(10, 1, 10), t(10, 2, 15)), Base.NOW, 2, SpanUnit.HOURS).first)
    }

    @Test fun sameTimeNextMonthClampsToTheMonthEnd() {
        assertEquals(t(11, 30, 14), tile(Spec(Verb.SNOOZE, true, t(10, 31, 8), t(10, 30, 14)), "same-month").at)
    }

    /** From now | From due time. */
    @Test fun customBase() {
        val s = Spec(Verb.RESCHEDULE, true, t(10, 1, 16, 7), t(10, 1, 12))
        assertEquals(t(10, 9, 12), WhenSheet.custom(s, Base.DUE, 8, SpanUnit.DAYS).first)
        assertEquals(t(10, 9, 16, 7), WhenSheet.custom(s, Base.NOW, 8, SpanUnit.DAYS).first)
        assertEquals(t(10, 1, 19, 7), WhenSheet.custom(s, Base.NOW, 3, SpanUnit.HOURS).first)
        assertTrue(WhenSheet.resultLine(t(10, 9, 12), s.now).endsWith(" · in 8 days"))
        assertTrue(WhenSheet.resultLine(t(10, 9, 12), s.now).startsWith("→ Fri 9 Oct, "))
    }

    @Test fun defaults() {
        val task = Spec(Verb.SNOOZE, true, t(10, 1, 10), t(10, 1, 9))
        assertEquals(Base.NOW, WhenSheet.defaultBase(task))
        assertEquals(Base.DUE, WhenSheet.defaultBase(task.copy(verb = Verb.RESCHEDULE)))
        assertFalse(WhenSheet.hasBaseToggle(task.copy(task = false)))
        assertFalse(WhenSheet.hasBaseToggle(task.copy(verb = Verb.REMIND, task = false)))
        assertFalse(WhenSheet.hasBaseToggle(task.copy(verb = Verb.RESCHEDULE, anchor = null)))
        assertEquals(t(10, 1, 10, 10), WhenSheet.custom(task.copy(task = false), Base.DUE, 10, SpanUnit.MINUTES).first)
    }

    @Test fun dateOnlyStaysDateOnlyForDaysAndWeeks() {
        val s = Spec(Verb.RESCHEDULE, true, t(10, 1, 10), t(10, 1, 9), dateOnly = true)
        assertEquals(t(10, 15, 9) to true, WhenSheet.custom(s, Base.DUE, 2, SpanUnit.WEEKS))
        assertFalse(WhenSheet.custom(s, Base.DUE, 2, SpanUnit.HOURS).second)
    }

    /** DST: days keep the time of day, hours are exact (fall back on 1 Nov 2026). */
    @Test fun dst() {
        val s = Spec(Verb.RESCHEDULE, true, t(10, 31, 12), t(10, 31, 12))
        assertEquals(t(11, 1, 12), WhenSheet.custom(s, Base.DUE, 1, SpanUnit.DAYS).first)
        assertEquals(t(11, 1, 11), WhenSheet.custom(s, Base.DUE, 24, SpanUnit.HOURS).first)
        assertEquals(t(11, 1, 12), tile(s.copy(verb = Verb.SNOOZE), "same-tomorrow").at)
        // Spring forward (8 Mar 2026): a same-time tile at 2:30 lands on the first valid time.
        val spring = Spec(Verb.SNOOZE, true, t(3, 7, 1), t(3, 7, 2, 30))
        assertEquals(t(3, 8, 3, 30), tile(spring, "same-tomorrow").at)
    }

    @Test fun relativeAndOverdue() {
        assertEquals("2 days ago", WhenSheet.relative(t(9, 29, 12), t(10, 1, 16)))
        assertEquals("in 3 h 20 min", WhenSheet.relative(t(10, 1, 19, 20), t(10, 1, 16)))
        // The custom result counts from the current minute: 12 hours from 4:00:42 PM reads "in 12 h" (it read "in 11 h 59 min").
        assertEquals("in 12 h", WhenSheet.relative(t(10, 2, 4), t(10, 1, 16).withSecond(42)))
        assertEquals("Overdue 4 h", WhenSheet.overdue(t(10, 1, 12), t(10, 1, 16)))
        assertEquals("Overdue 25 min", WhenSheet.overdue(t(10, 1, 15, 35), t(10, 1, 16)))
        assertEquals("Overdue 2 d", WhenSheet.overdue(t(9, 29, 0), t(10, 1, 16), true))
        assertNull(WhenSheet.overdue(t(10, 1, 17), t(10, 1, 16)))
        assertTrue(WhenSheet.dueLine(t(10, 1, 12), t(10, 1, 16), false).startsWith("Due today "))
    }

    @Test fun hints() {
        val s = Spec(Verb.SNOOZE, true, t(10, 1, 16), t(10, 1, 12))
        assertEquals("Only the reminder moves. The task stays due today 12:00 PM.", WhenSheet.hint(s, "today 12:00 PM"))
        assertEquals("Moves the due date and its reminders.", WhenSheet.hint(s.copy(verb = Verb.RESCHEDULE), null))
    }
}
