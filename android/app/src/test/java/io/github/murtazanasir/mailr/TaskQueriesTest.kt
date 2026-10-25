package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.cal.SmartList
import io.github.murtazanasir.mailr.data.cal.TaskFilter
import io.github.murtazanasir.mailr.data.cal.TaskQueries
import io.github.murtazanasir.mailr.data.cal.TaskSort
import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.ZoneId
import java.time.ZonedDateTime

/** Smart lists, counts, sorting and manual order on the phone, offline. */
class TaskQueriesTest {
    private val zone = ZoneId.of("America/Chicago")
    private val now = ZonedDateTime.of(2026, 10, 1, 12, 0, 0, 0, zone)
    private val nowMs = now.toInstant().toEpochMilli()
    private fun iso(z: ZonedDateTime) = z.toOffsetDateTime().toString()

    private val tasks = listOf(
        TaskItem("late", "Late report", iso(now.minusHours(2)), categories = listOf("work"), list = "L1", sortOrder = 3000),
        TaskItem("yday", "Yesterday's", "2026-09-30", list = "L1", sortOrder = 1000),
        TaskItem("today", "Due tonight", iso(now.plusHours(6)), categories = listOf("work", "home"), list = "L2", priority = 1),
        TaskItem("todaydate", "Today all day", "2026-10-01", list = "L2", priority = 5),
        TaskItem("next", "Next week", "2026-10-08", list = "L1", sortOrder = 2000),
        TaskItem("nodate", "Someday", null, categories = listOf("home"), list = "L1"),
        TaskItem("started", "Started already", null, start = iso(now.minusDays(1)), list = "L1"),
        TaskItem("deferred", "Later", "2026-10-01", start = "2026-10-05", list = "L1"),
        TaskItem("done", "Finished", "2026-09-29", status = "completed", list = "L1"),
        TaskItem("sub", "Sub", null, parent = "nodate", list = "L1"),
    )

    private fun uids(f: TaskFilter) = TaskQueries.filter(tasks, f, nowMs, zone).map { it.uid }.toSet()

    @Test fun smartListsFollowTheServerRules() {
        // Today: due on or before today, or the start has come. A future start hides the task.
        assertEquals(setOf("late", "yday", "today", "todaydate", "started"), uids(TaskFilter(SmartList.TODAY)))
        assertEquals(setOf("late", "yday"), uids(TaskFilter(SmartList.OVERDUE)))
        assertEquals(setOf("next"), uids(TaskFilter(SmartList.UPCOMING)))
        assertEquals(setOf("nodate", "started", "sub"), uids(TaskFilter(SmartList.NO_DATE)))
        assertEquals(setOf("late", "yday", "today", "todaydate", "next"), uids(TaskFilter(SmartList.SCHEDULED)))
        assertEquals(setOf("deferred", "late", "yday", "today", "todaydate", "started"),
            uids(TaskFilter(SmartList.TODAY, includeFuture = true)))
        assertEquals(setOf("done"), uids(TaskFilter(includeCompleted = true)) - uids(TaskFilter()))
    }

    @Test fun listTagParentAndTextFilters() {
        assertEquals(setOf("today", "todaydate"), uids(TaskFilter(list = "L2")))
        assertEquals(setOf("late", "today"), uids(TaskFilter(tag = "work")))
        assertEquals(setOf("sub"), uids(TaskFilter(parent = "nodate")))
        assertEquals(false, "sub" in uids(TaskFilter(parent = "")))
        assertEquals(setOf("late"), uids(TaskFilter(q = "REPORT")))
    }

    @Test fun countsAreOfOpenTasksThatAreNotDeferred() {
        val c = TaskQueries.counts(tasks, nowMs, zone)
        assertEquals(5, c.smart["today"])
        assertEquals(2, c.smart["overdue"])
        assertEquals(8, c.smart["all"])
        assertEquals(6, c.lists["L1"])
        assertEquals(2, c.tags["work"])
        assertEquals(2, c.tags["home"])
    }

    @Test fun sortsAndManualOrder() {
        val manual = TaskQueries.filter(tasks, TaskFilter(list = "L1", sort = TaskSort.MANUAL), nowMs, zone).map { it.uid }
        assertEquals(listOf("yday", "next", "late"), manual.take(3))
        val prio = TaskQueries.filter(tasks, TaskFilter(list = "L2", sort = TaskSort.PRIORITY), nowMs, zone).map { it.uid }
        assertEquals(listOf("today", "todaydate"), prio)
        assertEquals(1500L, TaskQueries.orderBetween(1000, 2000))
        assertEquals(1001L, TaskQueries.orderBetween(1000, 1001))
        assertEquals(1000L + TaskQueries.ORDER_STEP, TaskQueries.orderBetween(1000, null))
        assertEquals(1000L - TaskQueries.ORDER_STEP, TaskQueries.orderBetween(null, 1000))
    }
}
