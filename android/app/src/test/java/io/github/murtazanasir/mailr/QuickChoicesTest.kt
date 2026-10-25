// ACT-SURFACES: android.sheet
package io.github.murtazanasir.mailr

import android.app.Application
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import io.github.murtazanasir.mailr.data.reminders.QuickChoices
import io.github.murtazanasir.mailr.data.reminders.SpanUnit
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.time.ZoneId
import java.time.ZonedDateTime

/** The reminder presets, exactly as specified (labels, order, conditions). */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class QuickChoicesTest {
    private val z = ZoneId.of("America/Chicago")
    private fun t(d: Int, h: Int, m: Int = 0) = ZonedDateTime.of(2026, 10, d, h, m, 0, 0, z) // 1 Oct 2026 is a Thursday

    @Test fun taskSnoozePresets() { // task.snooze
        val morning = QuickChoices.taskSnooze(t(1, 10, 7))
        assertEquals(listOf("10 min", "1 hour", "3 hours", "This evening", "Tomorrow morning", "Next week"), morning.map { it.label })
        assertEquals(t(1, 18), morning.first { it.id == "evening" }.at)
        assertEquals(t(5, 9), morning.first { it.id == "week" }.at) // Monday 9:00
        assertFalse(QuickChoices.taskSnooze(t(1, 17, 30)).any { it.id == "evening" })
    }

    @Test fun eventSnoozeDependsOnTheStart() { // event.snooze
        val before = QuickChoices.eventSnooze(t(1, 10), t(1, 10, 30))
        assertEquals(listOf("5 min", "10 min", "At start time"), before.map { it.label })
        assertEquals(listOf("5 min", "15 min"), QuickChoices.eventSnooze(t(1, 10, 45), t(1, 10, 30)).map { it.label })
        assertFalse(QuickChoices.eventSnooze(t(1, 10, 29), t(1, 10, 30)).any { it.id == "start" }) // < 2 min left
    }

    @Test fun remindBeforeOffersOnlyWhatIsAhead() { // event.remind_before
        assertEquals(listOf(0, 5, 10, 15, 30), QuickChoices.offsets(t(1, 10), t(1, 10, 40)))
        assertEquals(listOf(0, 5, 10, 15, 30, 60, 120, 1440), QuickChoices.offsets(t(1, 10), t(2, 12)))
        assertTrue(QuickChoices.offsets(t(1, 10, 29, ), t(1, 10, 29)).isEmpty())
        assertEquals("2 hours before", QuickChoices.offsetLabel(120))
        assertEquals("At start", QuickChoices.offsetLabel(0))
    }

    @Test fun taskReschedulePresetsKeepTheTime() { // task.reschedule
        val due = t(1, 16, 30)
        val c = QuickChoices.taskReschedule(t(1, 10, 7), due, dateOnly = false)
        assertEquals(listOf("Later today", "Tonight", "Tomorrow", "This weekend", "Next Monday", "In a week"), c.map { it.label })
        assertEquals(t(2, 16, 30), c.first { it.id == "tomorrow" }.at)
        assertEquals(t(3, 16, 30), c.first { it.id == "weekend" }.at) // Saturday
        assertEquals(t(1, 13, 10), c.first { it.id == "later" }.at) // +3 h, rounded up to 5 min
        // Friday evening: no Later today / Tonight / This weekend; date-only tasks land at the morning hour.
        val fri = QuickChoices.taskReschedule(t(2, 20, 30), null, dateOnly = true)
        assertEquals(listOf("Tomorrow", "Next Monday", "In a week"), fri.map { it.label })
        assertEquals(t(3, 9), fri.first().at)
    }

    @Test fun eventReschedulePresets() { // event.reschedule
        assertEquals(listOf(t(1, 15, 15), t(1, 16), t(2, 15), t(8, 15)), QuickChoices.eventReschedule(t(1, 15)).map { it.at })
    }

    @Test fun wheelUnitsKeepTheirPlace() {
        assertEquals(10, SpanUnit.MINUTES.nearest(12))
        assertEquals(15, SpanUnit.HOURS.nearest(15))
        assertEquals(30, SpanUnit.DAYS.nearest(45))
        assertEquals(t(1, 11), SpanUnit.HOURS.add(t(1, 10), 1))
        assertEquals(t(1, 10, 5), QuickChoices.now(t(1, 10, 1)))
    }

    /** The amount and unit wheels: spec/cases/duration-cases.yaml (the copy in test resources); the web runs the same file. */
    @Test fun durationCases() {
        val file = io.github.murtazanasir.mailr.ui.calendar.FlowYaml.read("interaction/duration-cases.yaml")
        val units = file["units"]!!.jsonObject
        assertEquals(SpanUnit.entries.map { it.label }, units.keys.toList())
        SpanUnit.entries.forEach { u -> assertEquals(u.label, units[u.label]!!.jsonArray.map { it.jsonPrimitive.int }, u.amounts) }
        // Every hour from 1 to 23 is on the hours wheel, 2 and 3 included.
        assertEquals((1..23).toList(), SpanUnit.HOURS.amounts)
        file["cases"]!!.jsonArray.map { it.jsonObject }.forEach { c ->
            val to = SpanUnit.entries.first { it.label == c["to"]!!.jsonPrimitive.content }
            assertEquals(c["id"]!!.jsonPrimitive.content, c["expect"]!!.jsonPrimitive.int, to.nearest(c["amount"]!!.jsonPrimitive.int))
        }
    }
}
