package io.github.murtazanasir.mailr.data.cal

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.DeltaInstance
import io.github.murtazanasir.mailr.data.api.Occurrence
import io.github.murtazanasir.mailr.data.reminders.ReminderPlan
import java.time.LocalDate
import java.time.ZoneId
import java.time.temporal.ChronoUnit

/** Occurrences from a delta page: a master plus its `instances` inside the window. */
object DeltaRows {
    /**
     * The occurrences Room stores for one event. A single event is its own occurrence. A series
     * gets one per instance; override fields that are null take the master's value, an instance
     * without an end keeps the master's duration, and cancelled instances are left out.
     */
    fun occurrences(master: CalendarEvent, instances: List<DeltaInstance>?, zone: ZoneId): List<CalendarEvent> {
        if (!master.recurring) return listOf(master.copy(occurrenceId = master.occurrence))
        return instances.orEmpty().filterNot { it.cancelled }.map { i ->
            master.copy(
                start = i.start, end = i.end ?: endFor(master, i, zone), allDay = i.allDay,
                occurrenceId = i.occurrence, recurrenceId = i.occurrence, isOverride = i.isOverride,
                summary = i.summary ?: master.summary, location = i.location ?: master.location,
                status = i.status ?: master.status,
            )
        }
    }

    private fun endFor(master: CalendarEvent, i: DeltaInstance, zone: ZoneId): String? {
        val end = master.end ?: return null
        if (i.allDay) {
            val days = catching { ChronoUnit.DAYS.between(LocalDate.parse(master.start.take(10)), LocalDate.parse(end.take(10))) }
                .getOrNull() ?: return null
            return LocalDate.parse(i.start.take(10)).plusDays(days.coerceAtLeast(1)).toString()
        }
        val s = ReminderPlan.instant(master.start, zone) ?: return null
        val e = ReminderPlan.instant(end, zone) ?: return null
        val start = ReminderPlan.instant(i.start, zone) ?: return null
        return Occurrence.utc(java.time.Instant.ofEpochMilli(start + (e - s)))
    }
}
