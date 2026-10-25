package io.github.murtazanasir.mailr.ui.tasks

import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import io.github.murtazanasir.mailr.data.api.TaskDraft
import io.github.murtazanasir.mailr.ui.calendar.QuickAddGrammar
import io.github.murtazanasir.mailr.ui.calendar.QuickAddOptions
import io.github.murtazanasir.mailr.ui.calendar.QuickAddResult
import io.github.murtazanasir.mailr.ui.calendar.QuickCollection
import io.github.murtazanasir.mailr.ui.calendar.QuickMode
import io.github.murtazanasir.mailr.ui.calendar.QuickToken
import io.github.murtazanasir.mailr.ui.calendar.TokenKind
import java.time.Duration
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZonedDateTime

/**
 * What the current view adds to a quick-add line (the prefill): Today sets today,
 * a day section that day, a list view its list, a tag view its tag, High priority the priority.
 * The line itself wins over every one of these.
 */
data class QuickPrefill(
    val list: String? = null,
    val tag: String? = null,
    val day: LocalDate? = null,
    val priority: Int = 0,
    val parent: String? = null,
) {
    companion object {
        fun of(view: TaskView, today: LocalDate, day: LocalDate? = null): QuickPrefill = when (view) {
            is TaskView.ListView -> QuickPrefill(list = view.url, day = day)
            is TaskView.TagView -> QuickPrefill(tag = view.tag, day = day)
            is TaskView.SmartView -> when (view.smart) {
                Smart.TODAY -> QuickPrefill(day = day ?: today)
                Smart.UPCOMING, Smart.SCHEDULED -> QuickPrefill(day = day ?: today.plusDays(1))
                Smart.HIGH -> QuickPrefill(priority = 1, day = day)
                else -> QuickPrefill(day = day)
            }
        }
    }
}

/** A parsed line ready to save: the draft, plus a list to create first when the line named a new one ("#Errands"). */
data class QuickTaskPlan(val draft: TaskDraft, val createList: String? = null, val result: QuickAddResult)

/** The shared grammar, task mode, turned into a [TaskDraft]. */
object QuickTask {
    fun options(now: ZonedDateTime, lists: List<CalendarCollection>, keep: Set<String>, morningHour: Int = 9) =
        QuickAddOptions(QuickMode.TASK, now, lists = lists.map { QuickCollection(it.url, it.name) }, morningHour = morningHour, keep = keep)

    fun parse(text: String, now: ZonedDateTime, lists: List<CalendarCollection>, keep: Set<String> = emptySet(), morningHour: Int = 9) =
        QuickAddGrammar.parse(text, options(now, lists, keep, morningHour))

    /**
     * The draft for [r]. Due: the line's, else the prefill day (date only). Reminders: minutes
     * before the due as the grammar gives them; an absolute one ("remind me at 4pm") becomes the
     * minutes between it and the due, since a task's reminders are offsets from its due.
     */
    fun plan(r: QuickAddResult, uid: String, zone: ZoneId, pre: QuickPrefill = QuickPrefill()): QuickTaskPlan {
        val due: String? = r.due?.let { d -> if (r.dueAllDay) d.toLocalDate().toString() else d.atZone(zone).toOffsetDateTime().toString() }
            ?: pre.day?.toString()
        val dueAt = r.due?.let { if (r.dueAllDay) it.toLocalDate().atStartOfDay() else it } ?: pre.day?.atStartOfDay()
        val reminders = if (dueAt == null) emptyList() else r.reminders.mapNotNull { q ->
            q.minutes ?: q.at?.let { at -> Duration.between(at, dueAt).toMinutes().toInt() }
        }.distinct()
        val tags = (r.tags + listOfNotNull(pre.tag)).distinctBy { it.lowercase() }
        val ref = r.listOrCalendar
        val draft = TaskDraft(
            uid = uid,
            summary = r.title,
            due = due,
            priority = if (r.priority > 0) r.priority else pre.priority,
            list = ref?.id ?: pre.list,
            parent = pre.parent,
            reminders = reminders,
            start = r.startDate?.toString(),
            recurrence = r.rrule,
            repeatFrom = r.repeatFrom,
            categories = tags.ifEmpty { null },
            tz = if (due != null && due.length > 10) zone.id else null,
        )
        return QuickTaskPlan(draft, ref?.takeIf { it.create && it.id == null }?.name, r)
    }

    /** The title once [token] is turned back into text: the line keeps the word and the parse skips it. */
    fun keepAsText(keep: Set<String>, token: QuickToken): Set<String> = keep + token.text.lowercase()

    /** The chip text for a parsed token. */
    fun chipLabel(t: QuickToken, r: QuickAddResult, today: LocalDate): String = when (t.kind) {
        TokenKind.DATE, TokenKind.TIME -> r.due?.let { d ->
            DueChip.short(d.toLocalDate(), today) + if (!r.dueAllDay) " " + d.toLocalTime().format(
                java.time.format.DateTimeFormatter.ofLocalizedTime(java.time.format.FormatStyle.SHORT)) else ""
        } ?: t.text
        TokenKind.START -> r.startDate?.let { AppText.get(R.string.tasks_chip_starts, DueChip.short(it, today)) } ?: t.text
        TokenKind.REPEAT -> io.github.murtazanasir.mailr.ui.calendar.Recurrence.parse(r.rrule)?.describe()
            ?.let { if (r.repeatFrom == "completion") AppText.get(R.string.tasks_qa_after_done, it) else it } ?: t.text
        TokenKind.PRIORITY -> AppText.get(R.string.tasks_qa_priority, io.github.murtazanasir.mailr.ui.calendar.CalendarColors.priorityNames[
            io.github.murtazanasir.mailr.ui.calendar.CalendarColors.priorityLevel(r.priority)])
        TokenKind.TAG -> t.text.removePrefix("@")
        TokenKind.COLLECTION -> r.listOrCalendar?.let { if (it.create) AppText.get(R.string.tasks_qa_new_list, it.name) else it.name } ?: t.text
        TokenKind.REMINDER -> AppText.get(R.string.tasks_qa_remind, t.text.removePrefix("remind ").removePrefix("me ")) // i18n-ignore: "remind", "me": parser keywords
        else -> t.text
    }
}
