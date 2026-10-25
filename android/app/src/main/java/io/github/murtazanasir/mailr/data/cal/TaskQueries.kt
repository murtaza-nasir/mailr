package io.github.murtazanasir.mailr.data.cal

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.data.api.TaskCounts
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.reminders.ReminderPlan
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId

/** The server's smart lists (GET /tasks?smart=); [api] is the query value and the key in /tasks/counts. */
enum class SmartList(val api: String) {
    TODAY("today"), OVERDUE("overdue"), UPCOMING("upcoming"), NO_DATE("no_date"), SCHEDULED("scheduled"), ALL("all");

    companion object {
        fun of(api: String): SmartList? = entries.firstOrNull { it.api == api }
    }
}

enum class TaskSort(val api: String) { MANUAL("manual"), DUE("due"), PRIORITY("priority"), CREATED("created"), TITLE("title") }

/**
 * What a task screen shows. Null fields do not filter. [parent] "" means top-level tasks only.
 * Tasks whose start lies in the future are left out unless [includeFuture].
 */
data class TaskFilter(
    val smart: SmartList? = null,
    val list: String? = null,
    val tag: String? = null,
    val parent: String? = null,
    val q: String? = null,
    val includeCompleted: Boolean = false,
    val includeFuture: Boolean = false,
    val sort: TaskSort = TaskSort.MANUAL,
)

/**
 * The server's smart-list rules, run on the tasks in Room, so the lists and
 * their counts work offline and change the moment a task does. "Today" is a due on or before
 * today in the user's zone, or a start that has come. Counts are of open tasks.
 */
object TaskQueries {
    private fun dueDate(t: TaskItem, zone: ZoneId): LocalDate? {
        val due = t.due ?: return null
        if (ReminderPlan.isDateOnly(due)) return catching { LocalDate.parse(due) }.getOrNull()
        return ReminderPlan.instant(due, zone)?.let { Instant.ofEpochMilli(it).atZone(zone).toLocalDate() }
    }

    fun startMs(t: TaskItem, zone: ZoneId): Long? = t.start?.let { ReminderPlan.instant(it, zone) }

    /** Deferred: its start is still ahead. */
    fun deferred(t: TaskItem, now: Long, zone: ZoneId): Boolean = startMs(t, zone)?.let { it > now } ?: false

    fun open(t: TaskItem): Boolean = !t.completed && !t.cancelled

    fun inSmart(t: TaskItem, smart: SmartList, now: Long, zone: ZoneId): Boolean {
        val today = Instant.ofEpochMilli(now).atZone(zone).toLocalDate()
        val d = dueDate(t, zone)
        return when (smart) {
            SmartList.TODAY -> (d != null && !d.isAfter(today)) || (startMs(t, zone)?.let { it <= now } ?: false)
            SmartList.OVERDUE -> t.due != null && if (ReminderPlan.isDateOnly(t.due)) d != null && d.isBefore(today)
                else (ReminderPlan.instant(t.due, zone)?.let { it < now } ?: false)
            SmartList.UPCOMING -> d != null && d.isAfter(today)
            SmartList.NO_DATE -> t.due == null
            SmartList.SCHEDULED -> t.due != null
            SmartList.ALL -> true
        }
    }

    fun filter(tasks: List<TaskItem>, f: TaskFilter, now: Long, zone: ZoneId): List<TaskItem> {
        val q = f.q?.trim()?.lowercase()?.takeIf { it.isNotEmpty() }
        val out = tasks.filter { t ->
            (f.includeCompleted || open(t)) &&
                (f.includeFuture || !deferred(t, now, zone)) &&
                (f.smart == null || inSmart(t, f.smart, now, zone)) &&
                (f.list == null || t.list == f.list) &&
                (f.tag == null || f.tag in t.categories) &&
                (f.parent == null || (if (f.parent.isEmpty()) t.parent == null else t.parent == f.parent)) &&
                (q == null || t.summary.lowercase().contains(q) || t.notes.orEmpty().lowercase().contains(q))
        }
        return sort(out, f.sort, zone)
    }

    fun sort(tasks: List<TaskItem>, sort: TaskSort, zone: ZoneId): List<TaskItem> {
        val title = compareBy<TaskItem> { it.summary.lowercase() }
        val due = compareBy<TaskItem, Long?>(nullsLast()) { it.due?.let { d -> ReminderPlan.instant(d, zone) } }
        return when (sort) {
            TaskSort.MANUAL -> tasks.sortedWith(compareBy<TaskItem, Long?>(nullsLast()) { it.sortOrder }.then(due).then(title))
            TaskSort.DUE -> tasks.sortedWith(due.then(title))
            // 1 is the highest priority; 0 (none) sorts last.
            TaskSort.PRIORITY -> tasks.sortedWith(compareBy<TaskItem> { if (it.priority == 0) 10 else it.priority }.then(due).then(title))
            TaskSort.CREATED -> tasks.sortedWith(compareByDescending<TaskItem> { it.created ?: "" }.then(title))
            TaskSort.TITLE -> tasks.sortedWith(title)
        }
    }

    /** The sidebar counts, in the shape of GET /tasks/counts. */
    fun counts(tasks: List<TaskItem>, now: Long, zone: ZoneId): TaskCounts {
        val live = tasks.filter { open(it) && !deferred(it, now, zone) }
        return TaskCounts(
            smart = SmartList.entries.associate { s -> s.api to live.count { inSmart(it, s, now, zone) } },
            lists = live.filter { it.list != null }.groupingBy { it.list!! }.eachCount(),
            tags = live.flatMap { it.categories.distinct() }.groupingBy { it }.eachCount(),
        )
    }

    /**
     * The manual order for a task placed after [after] and before [before] (either may be
     * missing), the way the server picks it: the midpoint between the neighbours, or a step
     * past the one neighbour there is.
     */
    fun orderBetween(after: Long?, before: Long?): Long = when {
        after != null && before != null -> if (before - after > 1) after + (before - after) / 2 else after + 1
        after != null -> after + ORDER_STEP
        before != null -> before - ORDER_STEP
        else -> 0
    }

    const val ORDER_STEP = 1024L

    /**
     * A task from the delta ([new]) has no `reminders_at`: it counts absolute reminders in
     * `reminders` as minutes. When the phone knew them ([old], same due), they stay absolute.
     */
    fun keepAbsolute(old: TaskItem?, new: TaskItem, zone: ZoneId): TaskItem {
        if (new.remindersAt != null) return new
        val abs = old?.remindersAt?.takeIf { it.isNotEmpty() && old.due == new.due } ?: return new
        val mins = ReminderPlan.absoluteMinutes(new.copy(remindersAt = abs), zone).toSet()
        return new.copy(reminders = new.reminders.filterNot { it in mins }, remindersAt = abs)
    }

    /**
     * The repeat rule after the due of a repeating task moved from [oldDue] to [newDue].
     * "this" moves only this occurrence: a weekly or monthly rule
     * that took its day from the due is pinned to the old day, so the next ones stay where they
     * were. "all" moves the series: a single BYDAY or BYMONTHDAY that matched the old day
     * follows the new one. Returns null when the rule needs no change.
     */
    fun ruleAfterMove(rrule: String?, oldDue: String?, newDue: String?, scope: String, zone: ZoneId): String? {
        if (rrule.isNullOrBlank() || oldDue == null || newDue == null) return null
        val from = localDate(oldDue, zone) ?: return null
        val to = localDate(newDue, zone) ?: return null
        if (from == to) return null
        val parts = rrule.split(';').filter { it.isNotBlank() }.map { it.substringBefore('=').uppercase() to it.substringAfter('=', "") }
            .toMutableList()
        fun get(k: String) = parts.firstOrNull { it.first == k }?.second
        fun set(k: String, v: String) {
            val i = parts.indexOfFirst { it.first == k }
            if (i >= 0) parts[i] = k to v else parts += k to v
        }
        val freq = get("FREQ")?.uppercase() ?: return null
        val day = { d: LocalDate -> d.dayOfWeek.name.take(2) }
        when (freq) {
            "WEEKLY" -> {
                val by = get("BYDAY")
                if (scope == "this") { if (by == null) set("BYDAY", day(from)) else return null }
                else if (by != null && by.equals(day(from), true)) set("BYDAY", day(to)) else return null
            }
            "MONTHLY" -> {
                if (get("BYDAY") != null || get("BYSETPOS") != null) return null
                val by = get("BYMONTHDAY")
                if (scope == "this") { if (by == null) set("BYMONTHDAY", from.dayOfMonth.toString()) else return null }
                else if (by != null && by == from.dayOfMonth.toString()) set("BYMONTHDAY", to.dayOfMonth.toString()) else return null
            }
            else -> return null
        }
        return parts.joinToString(";") { (k, v) -> "$k=$v" }
    }

    private fun localDate(iso: String, zone: ZoneId): LocalDate? =
        if (ReminderPlan.isDateOnly(iso)) catching { LocalDate.parse(iso) }.getOrNull()
        else ReminderPlan.instant(iso, zone)?.let { Instant.ofEpochMilli(it).atZone(zone).toLocalDate() }
}
