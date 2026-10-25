package io.github.murtazanasir.mailr.ui.tasks

import io.github.murtazanasir.mailr.util.catching

import androidx.annotation.StringRes
import io.github.murtazanasir.mailr.i18n.LocaleFormats
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.cal.TaskQueries
import io.github.murtazanasir.mailr.data.cal.TaskSort
import io.github.murtazanasir.mailr.data.reminders.ReminderPlan
import io.github.murtazanasir.mailr.ui.calendar.CalendarColors
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.time.temporal.ChronoUnit
import java.util.Locale

/** The smart lists; [id] is the route segment the web uses. */
enum class Smart(val id: String, @StringRes val titleRes: Int) {
    TODAY("today", R.string.tasks_smart_today),
    UPCOMING("upcoming", R.string.tasks_smart_upcoming),
    SCHEDULED("scheduled", R.string.tasks_smart_scheduled),
    NODATE("nodate", R.string.tasks_smart_nodate),
    HIGH("high", R.string.tasks_smart_high),
    EMAIL("email", R.string.tasks_smart_email),
    WAITING("waiting", R.string.tasks_smart_waiting),
    ALL("all", R.string.tasks_smart_all),
    COMPLETED("completed", R.string.tasks_smart_completed);

    val title: String get() = AppText.get(titleRes)

    companion object {
        fun of(id: String): Smart? = entries.firstOrNull { it.id == id }
    }
}

/** What the Tasks screen shows: a smart list, a user list (by URL) or a tag. */
sealed interface TaskView {
    data class SmartView(val smart: Smart) : TaskView
    data class ListView(val url: String) : TaskView
    data class TagView(val tag: String) : TaskView

    /** The web's route segment (`today`, `l/<id>`, `t/<tag>`). */
    val route: String get() = when (this) {
        is SmartView -> smart.id
        is ListView -> "l/$url"
        is TagView -> "t/$tag"
    }

    companion object {
        val TODAY: TaskView = SmartView(Smart.TODAY)

        fun parse(route: String?): TaskView? = when {
            route.isNullOrBlank() -> null
            route.startsWith("l/") -> ListView(route.removePrefix("l/"))
            route.startsWith("t/") -> TagView(route.removePrefix("t/"))
            else -> Smart.of(route)?.let(::SmartView)
        }
    }
}

/** A subtask under a top-level row. [nested] marks a deeper level from another client, shown flat. */
data class SubEntry(val task: TaskItem, val nested: Boolean = false)

/** One top-level row and its subtasks (all of them, open and done, for the n/m count); [showDone] shows the done ones. */
data class TaskEntry(val task: TaskItem, val subtasks: List<SubEntry> = emptyList(), val showDone: Boolean = false) {
    val doneCount: Int get() = subtasks.count { it.task.completed }
    /** The subtasks the row expands to. */
    val shown: List<SubEntry> get() = if (showDone) subtasks else subtasks.filterNot { it.task.completed }
    val openSubtasks: List<TaskItem> get() = subtasks.map { it.task }.filter { TaskQueries.open(it) }
}

/** A section of the list. [day] is the day a quick add in this section is due ("+ Add to Sat"). */
data class TaskGroup(val id: String, val title: String, val entries: List<TaskEntry>, val emphasis: Boolean = false,
                     val day: LocalDate? = null) {
    val count: Int get() = entries.size
}

/** How the screen is set up besides the view. */
data class GroupOptions(
    val sort: TaskSort? = null,
    val showCompleted: Boolean = false,
    val showDeferred: Boolean = false,
    val query: String? = null,
    val lists: List<CalendarCollection> = emptyList(),
)

/**
 * The grouping and order of each smart list view. Pure: the
 * screen, the tests and the counts in the drawer see the same rows.
 */
object TaskGroups {
    private fun day(at: Long, zone: ZoneId): LocalDate = Instant.ofEpochMilli(at).atZone(zone).toLocalDate()

    fun dueMs(t: TaskItem, zone: ZoneId): Long? = t.due?.let { ReminderPlan.instant(it, zone) }

    fun dueDay(t: TaskItem, zone: ZoneId): LocalDate? {
        val due = t.due ?: return null
        if (ReminderPlan.isDateOnly(due)) return catching { LocalDate.parse(due.take(10)) }.getOrNull()
        return dueMs(t, zone)?.let { day(it, zone) }
    }

    fun startDay(t: TaskItem, zone: ZoneId): LocalDate? {
        val s = t.start ?: return null
        if (ReminderPlan.isDateOnly(s)) return catching { LocalDate.parse(s.take(10)) }.getOrNull()
        return ReminderPlan.instant(s, zone)?.let { day(it, zone) }
    }

    fun overdue(t: TaskItem, now: Long, zone: ZoneId): Boolean {
        val due = t.due ?: return false
        val today = day(now, zone)
        return if (ReminderPlan.isDateOnly(due)) dueDay(t, zone)?.isBefore(today) == true else (dueMs(t, zone) ?: Long.MAX_VALUE) < now
    }

    fun high(t: TaskItem) = CalendarColors.priorityLevel(t.priority) == 3

    /** The day a task sits on in Upcoming: a deferred task on its start day, else its due (or start). */
    fun upcomingDay(t: TaskItem, now: Long, zone: ZoneId): LocalDate? =
        if (TaskQueries.deferred(t, now, zone)) startDay(t, zone) else dueDay(t, zone) ?: startDay(t, zone)

    /** Today: due by the end of today; or started with no due or a due within 7 days. */
    fun inToday(t: TaskItem, now: Long, zone: ZoneId): Boolean {
        val today = day(now, zone)
        val d = dueDay(t, zone)
        if (d != null && !d.isAfter(today)) return true
        val started = TaskQueries.startMs(t, zone)?.let { it <= now } ?: false
        return started && (d == null || !d.isAfter(today.plusDays(7)))
    }

    /** Is [t] in [smart] (open-task rules; Completed takes completed ones). Deferred tasks are judged by the caller. */
    fun inSmart(t: TaskItem, smart: Smart, now: Long, zone: ZoneId): Boolean = when (smart) {
        Smart.COMPLETED -> t.completed
        Smart.WAITING -> false
        else -> TaskQueries.open(t) && when (smart) {
            Smart.TODAY -> inToday(t, now, zone)
            Smart.UPCOMING -> upcomingDay(t, now, zone)?.isAfter(day(now, zone)) == true
            Smart.SCHEDULED -> t.due != null
            Smart.NODATE -> t.due == null && t.start == null
            Smart.HIGH -> high(t)
            Smart.EMAIL -> t.conversationId != null
            else -> true
        }
    }

    /**
     * Open counts per smart list, as the drawer shows them (Today counts its overdue tasks
     * separately for the red number). Deferred tasks count only in Upcoming and Scheduled.
     */
    fun counts(all: List<TaskItem>, now: Long, zone: ZoneId): Map<String, Int> {
        val open = all.filter { TaskQueries.open(it) }
        val live = open.filterNot { TaskQueries.deferred(it, now, zone) }
        return buildMap {
            Smart.entries.filter { it != Smart.WAITING && it != Smart.COMPLETED }.forEach { s ->
                val pool = if (s == Smart.UPCOMING || s == Smart.SCHEDULED) open else live
                put(s.id, pool.count { inSmart(it, s, now, zone) })
            }
            put("overdue", live.count { overdue(it, now, zone) })
        }
    }

    fun build(view: TaskView, all: List<TaskItem>, now: Long, zone: ZoneId, o: GroupOptions = GroupOptions()): List<TaskGroup> {
        val today = day(now, zone)
        val visible = all.filterNot { it.cancelled }
        val q = o.query?.trim()?.takeIf { it.isNotEmpty() }
        if (q != null) {
            val hits = TaskSearch.filter(visible, q, o.lists, now, zone)
            return listOf(TaskGroup("results", AppText.get(R.string.tasks_group_results), entries(hits, all, false))).filter { it.entries.isNotEmpty() }
        }
        fun deferredOk(t: TaskItem) = o.showDeferred || !TaskQueries.deferred(t, now, zone)
        val byDue = compareBy<TaskItem, Long?>(nullsLast()) { dueMs(it, zone) }
        val manual = compareBy<TaskItem, Long?>(nullsLast()) { it.sortOrder }
        val title = compareBy<TaskItem> { it.summary.lowercase() }
        val prio = compareBy<TaskItem> { if (it.priority == 0) 10 else it.priority }
        fun sorted(xs: List<TaskItem>, default: Comparator<TaskItem>) =
            if (o.sort != null) TaskQueries.sort(xs, o.sort, zone) else xs.sortedWith(default)

        return when (view) {
            is TaskView.SmartView -> when (view.smart) {
                Smart.TODAY -> {
                    val pool = visible.filter { TaskQueries.open(it) && deferredOk(it) && inToday(it, now, zone) }
                    val overdue = pool.filter { overdue(it, now, zone) }
                    val dueToday = pool.filter { it !in overdue && dueDay(it, zone) == today }
                    val started = pool.filter { it !in overdue && it !in dueToday }
                    listOf(
                        TaskGroup("overdue", AppText.get(R.string.tasks_group_overdue), entries(overdue.sortedWith(byDue.then(title)), all, o.showCompleted), emphasis = true),
                        TaskGroup("today", AppText.get(R.string.tasks_group_today), entries(sorted(dueToday,
                            compareBy<TaskItem> { if (it.dueAllDay || ReminderPlan.isDateOnly(it.due ?: "")) 1 else 0 }
                                .then(byDue).then(prio).then(manual).then(title)), all, o.showCompleted), day = today),
                        TaskGroup("started", AppText.get(R.string.tasks_group_started), entries(started.sortedWith(byDue.then(prio).then(title)), all, o.showCompleted)),
                    ).roots()
                }
                Smart.UPCOMING -> {
                    val pool = visible.filter { inSmart(it, Smart.UPCOMING, now, zone) }
                    val when_ = { t: TaskItem -> upcomingDay(t, now, zone)!! }
                    val groups = linkedMapOf<String, Pair<TaskGroup, MutableList<TaskItem>>>()
                    pool.sortedWith(compareBy<TaskItem> { when_(it) }.then(byDue).then(prio).then(title)).forEach { t ->
                        val d = when_(t)
                        val (id, label, gday) = when {
                            !d.isAfter(today.plusDays(7)) -> Triple("d-$d", dayLabel(d, today), d)
                            d.year == today.year && d.month == today.month -> Triple("month-rest", AppText.get(R.string.tasks_group_later_month), null)
                            else -> Triple("m-${d.year}-${d.monthValue}", monthLabel(d, today), null)
                        }
                        groups.getOrPut(id) { TaskGroup(id, label, emptyList(), day = gday) to mutableListOf() }.second += t
                    }
                    groups.values.map { (g, xs) -> g.copy(entries = entries(xs, all, o.showCompleted)) }.roots()
                }
                Smart.SCHEDULED -> {
                    val pool = visible.filter { inSmart(it, Smart.SCHEDULED, now, zone) }
                    pool.sortedWith(byDue.then(title)).groupBy { dueDay(it, zone)!! }.toSortedMap().map { (d, xs) ->
                        TaskGroup("d-$d", dayLabel(d, today), entries(xs, all, o.showCompleted), emphasis = d.isBefore(today), day = d)
                    }.roots()
                }
                Smart.NODATE -> {
                    val pool = visible.filter { inSmart(it, Smart.NODATE, now, zone) }
                    byList(pool, all, o, manual.then(title))
                }
                Smart.HIGH -> {
                    val order = o.lists.mapIndexed { i, c -> c.url to i }.toMap()
                    val pool = visible.filter { deferredOk(it) && inSmart(it, Smart.HIGH, now, zone) }
                    listOf(TaskGroup("high", "", entries(sorted(pool, byDue.then(compareBy { order[it.list] ?: Int.MAX_VALUE }).then(title)),
                        all, o.showCompleted))).roots()
                }
                Smart.EMAIL -> {
                    val pool = visible.filter { deferredOk(it) && inSmart(it, Smart.EMAIL, now, zone) }
                    listOf(TaskGroup("email", "", entries(sorted(pool, byDue.then(compareByDescending { it.created ?: "" })),
                        all, o.showCompleted))).roots()
                }
                Smart.WAITING -> emptyList()
                Smart.ALL -> {
                    val pool = visible.filter { TaskQueries.open(it) && deferredOk(it) }
                    if (o.sort != null && o.sort != TaskSort.MANUAL) listOf(TaskGroup("all", "", entries(sorted(pool, manual), all, o.showCompleted))).roots()
                    else byList(pool, all, o, manual.then(byDue).then(title))
                }
                Smart.COMPLETED -> completed(visible.filter { it.completed }, all, now, zone)
            }
            is TaskView.ListView -> {
                val inList = visible.filter { it.list == view.url }
                val open = inList.filter { TaskQueries.open(it) }
                val out = mutableListOf(TaskGroup("list", "", entries(sorted(open, manual.then(byDue).then(title)), all, o.showCompleted)))
                if (o.showCompleted) {
                    val done = inList.filter { it.completed && (it.parent == null || all.none { p -> p.uid == it.parent }) }
                        .sortedByDescending { it.completedAt ?: "" }
                    out += TaskGroup("done", AppText.get(R.string.tasks_group_completed), entries(done, all, true))
                }
                out.roots()
            }
            is TaskView.TagView -> {
                val pool = visible.filter { TaskQueries.open(it) && deferredOk(it) && view.tag in it.categories }
                listOf(TaskGroup("tag", "", entries(sorted(pool, byDue.then(title)), all, o.showCompleted))).roots()
            }
        }
    }

    private fun byList(pool: List<TaskItem>, all: List<TaskItem>, o: GroupOptions, order: Comparator<TaskItem>): List<TaskGroup> {
        val names = o.lists.associate { it.url to it.name }
        val rank = o.lists.mapIndexed { i, c -> c.url to i }.toMap()
        return pool.groupBy { it.list.orEmpty() }.entries.sortedBy { rank[it.key] ?: Int.MAX_VALUE }.map { (url, xs) ->
            TaskGroup("l-$url", names[url] ?: AppText.get(R.string.tasks_group_other), entries(xs.sortedWith(order), all, o.showCompleted))
        }.roots()
    }

    private fun completed(done: List<TaskItem>, all: List<TaskItem>, now: Long, zone: ZoneId): List<TaskGroup> {
        val today = day(now, zone)
        fun at(t: TaskItem) = t.completedAt?.let { ReminderPlan.instant(it, zone) }?.let { day(it, zone) }
        val weekStart = today.minusDays(6)
        val buckets = linkedMapOf("today" to AppText.get(R.string.tasks_done_today), "yesterday" to AppText.get(R.string.tasks_done_yesterday),
            "week" to AppText.get(R.string.tasks_done_week), "earlier" to AppText.get(R.string.tasks_done_earlier))
        val items = done.sortedByDescending { t -> t.completedAt?.let { ReminderPlan.instant(it, zone) } ?: 0L }
        val grouped = items.groupBy { t ->
            val d = at(t)
            when {
                d == null -> "earlier"
                d == today -> "today"
                d == today.minusDays(1) -> "yesterday"
                !d.isBefore(weekStart) -> "week"
                else -> "earlier"
            }
        }
        // Completed subtasks stay in their own rows here: the list is a log of what was done.
        return buckets.mapNotNull { (id, label) -> grouped[id]?.let { xs -> TaskGroup(id, label, xs.map { TaskEntry(it) }) } }
    }

    /**
     * Top-level rows: a task whose parent is also in [pool] goes under it. Every row carries
     * all of its subtasks from [all], open and completed, for the n/m count (deeper levels
     * flattened and marked nested); the row shows the completed ones only when [withDone].
     */
    fun entries(pool: List<TaskItem>, all: List<TaskItem>, withDone: Boolean): List<TaskEntry> {
        val ids = pool.mapTo(HashSet()) { it.uid }
        val kids = all.filterNot { it.cancelled }.filter { it.parent != null }.groupBy { it.parent!! }
        fun descendants(uid: String, depth: Int, seen: MutableSet<String>): List<SubEntry> =
            kids[uid].orEmpty().sortedWith(compareBy<TaskItem>({ it.completed }, { it.sortOrder ?: Long.MAX_VALUE }, { it.summary.lowercase() }))
                .flatMap { c -> if (!seen.add(c.uid)) emptyList() else listOf(SubEntry(c, depth > 1)) + descendants(c.uid, depth + 1, seen) }
        return pool.filter { it.parent == null || it.parent !in ids }.map { t ->
            TaskEntry(t, descendants(t.uid, 1, mutableSetOf(t.uid)), withDone || t.completed)
        }
    }

    private fun List<TaskGroup>.roots() = filter { it.entries.isNotEmpty() }

    fun dayLabel(d: LocalDate, today: LocalDate): String = when (d) {
        today -> AppText.get(R.string.tasks_day_today)
        today.plusDays(1) -> AppText.get(R.string.tasks_day_tomorrow)
        today.minusDays(1) -> AppText.get(R.string.tasks_day_yesterday)
        else -> d.format(if (d.year == today.year) LocaleFormats.date("EEEdMMM", "EEE d MMM") else LocaleFormats.date("yMMMEEEd", "EEE d MMM yyyy"))
    }

    private fun monthLabel(d: LocalDate, today: LocalDate): String =
        d.format(if (d.year == today.year) LocaleFormats.date("MMMM", "MMMM") else LocaleFormats.date("yMMMM", "MMMM yyyy"))
}

/** The due chip of a row: its text and how loud it is. */
data class DueChip(val text: String, val tone: Tone) {
    enum class Tone { OVERDUE, SOON, NORMAL, DEFERRED, DONE }

    companion object {
        /**
         * "Overdue 2d", "Today 3 PM", "Tomorrow", "Fri", "12 Oct"; "Starts Thu" when deferred.
         * Due within two days, or past, is [Tone.SOON] or [Tone.OVERDUE] (both red).
         */
        fun of(t: TaskItem, now: Long, zone: ZoneId): DueChip? {
            val today = Instant.ofEpochMilli(now).atZone(zone).toLocalDate()
            if (!t.completed && TaskQueries.deferred(t, now, zone)) {
                val s = TaskGroups.startDay(t, zone) ?: return null
                return DueChip(AppText.get(R.string.tasks_chip_starts, short(s, today)), Tone.DEFERRED)
            }
            val d = TaskGroups.dueDay(t, zone) ?: return null
            val dateOnly = ReminderPlan.isDateOnly(t.due!!)
            val at = TaskGroups.dueMs(t, zone)
            val time = if (dateOnly || at == null) null else
                Instant.ofEpochMilli(at).atZone(zone).format(DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).withLocale(Locale.getDefault()))
            if (t.completed) return DueChip(short(d, today), Tone.DONE)
            if (TaskGroups.overdue(t, now, zone)) {
                val days = ChronoUnit.DAYS.between(d, today)
                val text = when {
                    days >= 1 -> AppText.get(R.string.tasks_chip_overdue_days, days.toInt())
                    else -> {
                        val h = ChronoUnit.HOURS.between(Instant.ofEpochMilli(at!!), Instant.ofEpochMilli(now))
                        if (h >= 1) AppText.get(R.string.tasks_chip_overdue_hours, h.toInt()) else AppText.get(R.string.tasks_chip_overdue)
                    }
                }
                return DueChip(text, Tone.OVERDUE)
            }
            val label = when (d) {
                today -> time?.let { AppText.get(R.string.tasks_chip_today_at, it) } ?: AppText.get(R.string.tasks_day_today)
                today.plusDays(1) -> time?.let { AppText.get(R.string.tasks_chip_tomorrow_at, it) } ?: AppText.get(R.string.tasks_day_tomorrow)
                else -> short(d, today) + (time?.takeIf { d.isBefore(today.plusDays(7)) }?.let { " $it" } ?: "")
            }
            return DueChip(label, if (!d.isAfter(today.plusDays(2))) Tone.SOON else Tone.NORMAL)
        }

        fun short(d: LocalDate, today: LocalDate): String = when {
            d == today -> AppText.get(R.string.tasks_day_today)
            d == today.plusDays(1) -> AppText.get(R.string.tasks_day_tomorrow)
            d.isAfter(today) && d.isBefore(today.plusDays(7)) -> d.format(LocaleFormats.date("EEE", "EEE"))
            d.year == today.year -> d.format(LocaleFormats.date("dMMM", "d MMM"))
            else -> d.format(LocaleFormats.date("yMMMd", "d MMM yyyy"))
        }

        /** "Fri" or "12 Oct": the next due of a repeating task, for "Next: Fri". */
        fun next(due: String?, now: Long, zone: ZoneId): String? {
            val d = due?.let { TaskGroups.dueDay(TaskItem("x", due = it), zone) } ?: return null
            return short(d, Instant.ofEpochMilli(now).atZone(zone).toLocalDate())
        }
    }
}

/**
 * task.search on the phone: free text over title, notes and tags, with the
 * operators `list:`, `tag:` (or `@tag`), `due:<fri`, `is:done`, `is:overdue`, `has:email`, `pri:high`.
 */
object TaskSearch {
    fun filter(tasks: List<TaskItem>, q: String, lists: List<CalendarCollection>, now: Long, zone: ZoneId): List<TaskItem> {
        val words = mutableListOf<String>()
        val tests = mutableListOf<(TaskItem) -> Boolean>()
        var done = false
        for (raw in q.trim().split(Regex("\\s+"))) {
            val w = raw.lowercase()
            when {
                w == "is:done" || w == "is:completed" -> done = true
                w == "is:overdue" -> tests += { TaskGroups.overdue(it, now, zone) }
                w == "has:email" -> tests += { it.conversationId != null }
                w.startsWith("pri:") -> {
                    val lvl = when (w.removePrefix("pri:")) { "high", "h", "3" -> 3; "medium", "med", "m", "2" -> 2; "low", "l", "1" -> 1; else -> 0 }
                    tests += { CalendarColors.priorityLevel(it.priority) == lvl }
                }
                w.startsWith("list:") -> {
                    val name = w.removePrefix("list:")
                    val urls = lists.filter { it.name.lowercase().startsWith(name) }.map { it.url }.toSet()
                    tests += { it.list in urls }
                }
                w.startsWith("tag:") || (w.startsWith("@") && w.length > 1) -> {
                    val tag = w.removePrefix("tag:").removePrefix("@")
                    tests += { t -> t.categories.any { it.lowercase() == tag } }
                }
                w.startsWith("due:<") || w.startsWith("due:>") -> {
                    val target = parseDay(w.substring(5), Instant.ofEpochMilli(now).atZone(zone))
                    if (target != null) {
                        val before = w[4] == '<'
                        tests += { t -> TaskGroups.dueDay(t, zone)?.let { if (before) it.isBefore(target) else it.isAfter(target) } ?: false }
                    }
                }
                w.isNotEmpty() -> words += w
            }
        }
        return tasks.filter { t ->
            (if (done) t.completed else TaskQueries.open(t)) && tests.all { it(t) } &&
                words.all { w -> t.summary.lowercase().contains(w) || t.notes.orEmpty().lowercase().contains(w) ||
                    t.categories.any { it.lowercase().contains(w) } }
        }.sortedWith(compareBy<TaskItem, Long?>(nullsLast()) { TaskGroups.dueMs(it, zone) }.thenBy { it.summary.lowercase() })
    }

    /** "fri", "tomorrow", "today", "2026-10-12" or "+3d" for `due:<…`. */
    fun parseDay(s: String, now: ZonedDateTime): LocalDate? {
        val today = now.toLocalDate()
        return when {
            s == "today" -> today
            s == "tomorrow" || s == "tom" -> today.plusDays(1)
            s.matches(Regex("\\+\\d+d")) -> today.plusDays(s.drop(1).dropLast(1).toLong())
            s.matches(Regex("\\+\\d+w")) -> today.plusWeeks(s.drop(1).dropLast(1).toLong())
            else -> java.time.DayOfWeek.entries.firstOrNull { it.name.lowercase().startsWith(s) && s.length >= 3 }
                ?.let { dow -> today.with(java.time.temporal.TemporalAdjusters.next(dow)) }
                ?: catching { LocalDate.parse(s) }.getOrNull()
        }
    }
}

/**
 * Where a task sat before a move (task.reorder, task.reparent): its parent (null at the top level) and
 * its neighbours there, the one above and the one below. The Undo moves it back through the same
 * POST /move: after the one above, or before the one below when it was first.
 */
data class TaskPlace(val parent: String?, val above: String?, val below: String?) {
    companion object {
        /** The place of [t] among the open tasks of its list and level in [all], in manual order. */
        fun of(t: TaskItem, all: List<TaskItem>): TaskPlace {
            val sibs = all.filter { it.list == t.list && it.parent == t.parent && (it.uid == t.uid || TaskQueries.open(it)) }
                .sortedWith(compareBy<TaskItem, Long?>(nullsLast()) { it.sortOrder }.thenBy { it.summary.lowercase() })
            val i = sibs.indexOfFirst { it.uid == t.uid }
            return TaskPlace(t.parent, sibs.getOrNull(i - 1)?.uid?.takeIf { i > 0 }, sibs.getOrNull(i + 1)?.uid?.takeIf { i >= 0 })
        }
    }

    /** The POST /move arguments that put a task back here: (after, before, parent); null when nothing would move. */
    fun moveBack(parentChanged: Boolean): Triple<String?, String?, String?>? {
        val parentArg = if (parentChanged) parent ?: "" else null
        val after = above
        val before = if (after == null) below else null
        return if (after == null && before == null && parentArg == null) null else Triple(after, before, parentArg)
    }
}
