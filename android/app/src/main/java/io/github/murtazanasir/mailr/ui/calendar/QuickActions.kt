package io.github.murtazanasir.mailr.ui.calendar

import io.github.murtazanasir.mailr.time.AppClock
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.data.agent.AskAbout
import io.github.murtazanasir.mailr.ui.ask.AskIntents
import androidx.compose.material.icons.outlined.Mic

import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.outlined.NotificationsOff
import androidx.compose.material.icons.outlined.Checklist
import androidx.compose.material.icons.outlined.Videocam
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.HorizontalDivider
import io.github.murtazanasir.mailr.look.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import io.github.murtazanasir.mailr.look.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Surface
import androidx.compose.ui.graphics.Color
import io.github.murtazanasir.mailr.data.reminders.WhenSheet
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.AlarmAdd
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.material.icons.outlined.Event
import androidx.compose.material.icons.outlined.EventRepeat
import androidx.compose.material.icons.outlined.Mail
import androidx.compose.material.icons.outlined.Snooze
import androidx.compose.material.icons.outlined.TaskAlt
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.db.ReminderEntity
import io.github.murtazanasir.mailr.data.reminders.Kind
import io.github.murtazanasir.mailr.data.reminders.QuickChoices
import io.github.murtazanasir.mailr.data.reminders.RState
import io.github.murtazanasir.mailr.data.reminders.ReminderPlan
import io.github.murtazanasir.mailr.ui.MainActivity
import io.github.murtazanasir.mailr.data.api.Occurrence
import io.github.murtazanasir.mailr.data.api.ReminderKeys
import java.time.Instant
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.util.Locale

/** What a quick-action sheet is about: a task or an event occurrence, and the reminder that brought it up. */
data class SheetTarget(val reminder: ReminderEntity?, val task: TaskItem? = null, val event: CalendarEvent? = null) {
    val isTask get() = task != null || reminder?.kind == Kind.TASK
    val title get() = task?.summary?.ifBlank { null } ?: event?.summary?.ifBlank { null } ?: reminder?.title?.ifBlank { null }
        ?: if (isTask) AppText.get(R.string.rem_untitled_task) else AppText.get(R.string.cal_no_title)
    /** The email the task or event came from, when there is one (task.open_email, event.open_email). */
    val conversationId: Long? get() = if (isTask) task?.conversationId else event?.conversationId
}

/** The outcome of an action, for the snackbar: its message and how to undo it. */
data class Done(val message: String, val undo: (suspend () -> Unit)?)

/**
 * The reminder, task and event actions on Android. Every surface (the reminder sheet, the
 * triage queue, the in-app sheet) calls these, so they behave the same everywhere.
 */
class QuickActions(private val g: AppGraph, private val context: Context) {
    private val zone: ZoneId get() = ZoneId.systemDefault()
    private val time get() = DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).withLocale(Locale.getDefault())

    private fun label(at: ZonedDateTime): String = describeMoment(at, AppClock.now(zone), relative = false)

    /** ACT task.complete @android.sheet @android.triage */
    suspend fun complete(t: SheetTarget): Done {
        val uid = t.task?.uid ?: t.reminder?.itemUid ?: return Done(AppText.get(R.string.cal_qa_nothing_to_complete), null)
        val prev = t.reminder
        if (prev != null) g.reminders.done(prev.key) else g.calendar.setTaskDone(uid, true)
        return Done(AppText.get(R.string.cal_qa_done, t.title)) {
            g.calendar.setTaskDone(uid, false)
            prev?.let { g.reminders.restore(it) }
        }
    }

    /** ACT task.snooze @android.sheet @android.triage, ACT event.snooze @android.sheet @android.triage */
    suspend fun snooze(t: SheetTarget, at: ZonedDateTime): Done {
        val r = t.reminder ?: return Done(AppText.get(R.string.cal_qa_no_reminder_to_snooze), null)
        g.reminders.snooze(r.key, at.toInstant().toEpochMilli())
        return Done(AppText.get(R.string.cal_qa_snoozed_until, label(at))) { g.reminders.restore(r) }
    }

    /** ACT reminder.dismiss @android.sheet @android.triage */
    suspend fun dismiss(t: SheetTarget): Done {
        val r = t.reminder ?: return Done("", null)
        g.reminders.dismiss(r.key)
        return Done(AppText.get(R.string.cal_qa_dismissed)) { g.reminders.restore(r) }
    }

    /** ACT event.remind_before @android.sheet @android.triage */
    suspend fun remindBefore(t: SheetTarget, minutes: Int): Done {
        val r = t.reminder ?: return Done(AppText.get(R.string.cal_qa_no_reminder), null)
        val at = g.reminders.remindBefore(r.key, minutes) ?: return Done(AppText.get(R.string.cal_qa_too_soon), null)
        val newKey = ReminderKeys.event(r.itemUid, Occurrence.canonical(r.occurrence), minutes)
        return Done(AppText.get(R.string.cal_qa_reminds_at, Instant.ofEpochMilli(at).atZone(zone).format(time))) { g.reminders.restore(r, drop = newKey) }
    }

    /** ACT task.reschedule @android.sheet @android.triage */
    suspend fun rescheduleTask(t: SheetTarget, at: ZonedDateTime, dateOnly: Boolean): Done {
        val uid = t.task?.uid ?: t.reminder?.itemUid ?: return Done("", null)
        val before = g.calendar.task(uid)?.due
        val due = if (dateOnly) at.toLocalDate().toString() else at.toOffsetDateTime().toString()
        g.calendar.rescheduleTask(uid, due)
        t.reminder?.let { g.reminders.dismiss(it.key) }
        val prev = t.reminder
        return Done(AppText.get(R.string.rem_due_date, describeMoment(at, AppClock.now(zone), dateOnly, relative = false))) {
            g.calendar.rescheduleTask(uid, before)
            prev?.let { g.reminders.restore(it) }
        }
    }

    /** ACT event.reschedule @android.sheet @android.triage (this occurrence; the duration is kept) */
    suspend fun rescheduleEvent(t: SheetTarget, start: ZonedDateTime): Done {
        val e = t.event ?: return Done(AppText.get(R.string.cal_qa_event_not_found), null)
        val oldStart = ReminderPlan.instant(e.start, zone) ?: return Done("", null)
        val oldEnd = ReminderPlan.eventEnd(e, zone) ?: oldStart
        val end = start.plusNanos((oldEnd - oldStart) * 1_000_000)
        g.calendar.rescheduleEvent(e, start.toOffsetDateTime().toString(), end.toOffsetDateTime().toString(), "this")
        t.reminder?.let { g.reminders.dismiss(it.key) }
        return Done(AppText.get(R.string.cal_moved_to, label(start))) {
            val moved = g.calendar.eventsOf(e.uid).firstOrNull { ReminderPlan.instant(it.start, zone) == start.toInstant().toEpochMilli() }
            if (moved != null) g.calendar.rescheduleEvent(moved, e.start, e.end, "this")
        }
    }

    /** ACT task.delete @android.sheet */
    suspend fun deleteTask(t: SheetTarget): Done {
        val task = t.task ?: return Done("", null)
        val token = g.calendar.deleteTask(task)
        t.reminder?.let { g.reminders.dismiss(it.key) }
        return Done(AppText.get(R.string.cal_qa_task_deleted)) { g.calendar.undoDeleteTask(token) }
    }

    /** ACT event.delete @android.sheet (this occurrence) */
    suspend fun deleteEvent(t: SheetTarget): Done {
        val e = t.event ?: return Done("", null)
        val token = g.calendar.deleteEvent(e, if (e.recurring) "this" else "all")
        t.reminder?.let { g.reminders.dismiss(it.key) }
        return Done(AppText.get(R.string.cal_event_deleted)) { g.calendar.undoDeleteEvent(token) }
    }

    /** ACT event.rsvp @android.sheet @android.triage */
    suspend fun rsvp(t: SheetTarget, response: String): Done {
        val e = t.event ?: return Done("", null)
        g.calendar.rsvp(e, response)
        return Done(when (response) { "accepted" -> AppText.get(R.string.cal_partstat_going); "declined" -> AppText.get(R.string.cal_partstat_not_going); else -> AppText.get(R.string.cal_partstat_maybe) }, null)
    }

    /** ACT task.edit @android.sheet, ACT event.edit @android.sheet: the full editor in the app. */
    fun edit(t: SheetTarget) {
        val uri = if (t.isTask) Uri.parse("mailr://task").buildUpon().appendPath(t.task?.uid ?: t.reminder?.itemUid).build()
        else Uri.parse("mailr://event").buildUpon().appendPath(t.event?.uid ?: t.reminder?.itemUid)
            .appendQueryParameter("occ", t.event?.occurrence ?: t.reminder?.occurrence.orEmpty()).build()
        open(uri)
    }

    /** ACT event.prep: the event in the app with its meeting prep open (mailr://event/<uid>?occ=&prep=1). */
    fun prep(t: SheetTarget) {
        val uid = t.event?.uid ?: t.reminder?.itemUid ?: return
        open(prepUri(uid, t.event?.occurrence ?: t.reminder?.occurrence))
    }

    /** ACT task.open_email @android.sheet @android.triage, ACT event.open_email @android.sheet @android.triage: the conversation the record came from. */
    fun openEmail(t: SheetTarget) {
        t.conversationId?.let { open(Uri.parse("mailr://conversation/$it")) }
    }

    /**
     * ACT agent.ask_about @android.sheet @android.triage: the ask capture with this task or event attached. Over the lock screen
     * (the reminder sheet shows there) the phone asks to be unlocked first. The reminder stays.
     */
    fun ask(t: SheetTarget) {
        val about = if (t.isTask) (t.task?.uid ?: t.reminder?.itemUid)?.let { AskAbout.Task(it, t.title) }
            else (t.event?.uid ?: t.reminder?.itemUid)?.let { AskAbout.Event(it, t.event?.occurrence ?: t.reminder?.occurrence, t.title) }
        AskIntents.startUnlocked(context, AskIntents.capture(context, about, from = "sheet"))
    }

    /** ACT event.join @android.sheet: the conference link in a browser or the meeting app. The reminder stays. */
    fun join(url: String) {
        io.github.murtazanasir.mailr.ui.util.SafeLinks.open(context, url, customTab = false)
    }

    private fun open(uri: Uri) = context.startActivity(io.github.murtazanasir.mailr.ui.util.InternalLinks.mark(context,
        Intent(context, MainActivity::class.java).setAction(Intent.ACTION_VIEW)
            .setData(uri).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)))

    companion object {
        /** Loads the task or event behind a reminder. */
        suspend fun target(g: AppGraph, r: ReminderEntity): SheetTarget =
            if (r.kind == Kind.TASK) SheetTarget(r, task = g.calendar.task(r.itemUid))
            else SheetTarget(r, event = g.calendar.eventInstance("${r.itemUid}|${r.occurrence}")
                ?: g.calendar.eventsOf(r.itemUid).firstOrNull { it.occurrence == r.occurrence })
    }
}

private enum class Mode { ACTIONS, SNOOZE, RESCHEDULE, OFFSET }

/**
 * The quick-action sheet: the compact card for one task or event. [expand] opens straight into the when sheet
 * ("snooze", "offset", "reschedule"). [run] performs an action and reports its outcome.
 *
 * Layout: the header (kind icon, title, when line with an inline status badge,
 * Close or Back), then either the actions (equal tiles for Done, Snooze and Reschedule; one
 * secondary row with Delete at its end in the error colour) or the when sheet.
 */
@Composable
fun QuickActionCard(target: SheetTarget, actions: QuickActions, morningHour: Int, expand: String?,
                    run: (suspend () -> Done) -> Unit, onClose: (() -> Unit)? = null, onEdit: (() -> Unit)? = null) {
    var mode by remember(target) { mutableStateOf(when (expand) { "snooze" -> Mode.SNOOZE; "offset" -> Mode.OFFSET; "reschedule" -> Mode.RESCHEDULE; else -> Mode.ACTIONS }) }
    var full by remember(target) { mutableStateOf(false) }
    val z = ZoneId.systemDefault()
    val now = remember(target, mode) { AppClock.now(z) }
    val r = target.reminder
    val start: ZonedDateTime? = (target.event?.let { ReminderPlan.instant(it.start, z) } ?: r?.startAt)?.let { Instant.ofEpochMilli(it).atZone(z) }
    val due: ZonedDateTime? = target.task?.due?.let { ReminderPlan.instant(it, z) }?.let { Instant.ofEpochMilli(it).atZone(z) }
    val dateOnly = target.task?.due?.let { ReminderPlan.isDateOnly(it) } ?: false
    val inProgress = !target.isTask && start != null && !start.isAfter(now)
    val line = whenLine(target, now, start, due, dateOnly)

    Column(Modifier.fillMaxWidth().padding(bottom = 8.dp)) {
        // ACT agent.ask_about: the microphone in the header, on the actions and on the when sheet alike.
        SheetHeader(target, line, status(target, now, due, dateOnly, inProgress),
            back = if (mode != Mode.ACTIONS) ({ mode = Mode.ACTIONS }) else null, onClose = onClose, ask = { actions.ask(target) })
        when (mode) {
            Mode.ACTIONS -> Column(Modifier.weight(1f, fill = false).verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(12.dp)) {
                // The sheet (and each triage card): ACT task.complete, ACT task.snooze, ACT task.reschedule,
                // ACT event.snooze, ACT event.remind_before, ACT event.reschedule, ACT reminder.dismiss.
                val doneLabel = stringResource(R.string.cal_done)
                val snoozeLabel = stringResource(R.string.cal_qa_snooze)
                val rescheduleLabel = stringResource(R.string.cal_qa_reschedule)
                val remindBeforeLabel = stringResource(R.string.cal_qa_remind_before)
                val dismissLabel = stringResource(R.string.rem_action_dismiss)
                val primary = buildList {
                    if (target.isTask) {
                        add(Triple(doneLabel, Icons.Outlined.CheckCircle) { run { actions.complete(target) } })
                        if (r != null) add(Triple(snoozeLabel, Icons.Outlined.Snooze) { mode = Mode.SNOOZE })
                        add(Triple(rescheduleLabel, Icons.Outlined.EventRepeat) { mode = Mode.RESCHEDULE })
                    } else {
                        if (r != null) add(Triple(snoozeLabel, Icons.Outlined.Snooze) { mode = Mode.SNOOZE })
                        if (!inProgress && r != null && start != null && QuickChoices.offsets(now, start).isNotEmpty())
                            add(Triple(remindBeforeLabel, Icons.Outlined.AlarmAdd) { mode = Mode.OFFSET })
                        if (!inProgress && target.event != null) add(Triple(rescheduleLabel, Icons.Outlined.EventRepeat) { mode = Mode.RESCHEDULE })
                        if (inProgress && r != null) add(Triple(dismissLabel, Icons.Outlined.Close) { run { actions.dismiss(target) } })
                    }
                }
                Row(Modifier.fillMaxWidth().padding(horizontal = SHEET_PAD), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    primary.forEachIndexed { i, (label, icon, onClick) -> ActionTile(label, icon, i == 0, Modifier.weight(1f), onClick) }
                }
                HorizontalDivider(Modifier.padding(horizontal = SHEET_PAD), color = MaterialTheme.colorScheme.outlineVariant)
                // Secondary: ACT task.edit, ACT event.edit, ACT task.delete, ACT event.delete, ACT task.open_email,
                // ACT event.open_email; invitations add ACT event.rsvp.
                Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                    Row(Modifier.weight(1f).horizontalScroll(rememberScrollState())) {
                        if (r != null && (target.isTask || !inProgress)) SecondaryButton(dismissLabel, Icons.Outlined.NotificationsOff) { run { actions.dismiss(target) } }
                        SecondaryButton(stringResource(R.string.cal_edit), Icons.Outlined.Edit) { onEdit?.invoke() ?: actions.edit(target) }
                        if (target.conversationId != null) SecondaryButton(stringResource(R.string.cal_qa_email), Icons.Outlined.Mail) { actions.openEmail(target) }
                        // ACT event.join: the event's conference link.
                        target.event?.let(::joinLink)?.let { link -> SecondaryButton(joinLabel(link), Icons.Outlined.Videocam) { actions.join(link) } }
                        // ACT event.prep @android.sheet: an event with guests opens with its meeting prep.
                        if (target.event?.attendeeList?.isNotEmpty() == true) SecondaryButton(stringResource(R.string.rem_action_prep), Icons.Outlined.Checklist) { actions.prep(target) }
                    }
                    if (target.task != null || target.event != null) SecondaryButton(stringResource(R.string.cal_delete), Icons.Outlined.Delete, MaterialTheme.colorScheme.error) {
                        run { if (target.isTask) actions.deleteTask(target) else actions.deleteEvent(target) }
                    }
                }
                if (target.event?.isInvite == true) Row(Modifier.fillMaxWidth().padding(horizontal = SHEET_PAD),
                    verticalAlignment = Alignment.CenterVertically) {
                    Text(stringResource(R.string.cal_going_question), style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(end = 12.dp))
                    SingleChoiceSegmentedButtonRow(Modifier.weight(1f)) {
                        val answers = listOf("accepted" to stringResource(R.string.cal_rsvp_yes), "tentative" to stringResource(R.string.cal_partstat_maybe), "declined" to stringResource(R.string.cal_rsvp_no))
                        answers.forEachIndexed { i, (v, l) ->
                            SegmentedButton(selected = target.event.myStatus?.lowercase() == v, onClick = { run { actions.rsvp(target, v) } },
                                shape = SegmentedButtonDefaults.itemShape(i, answers.size)) { Text(l) }
                        }
                    }
                }
            }
            // ACT task.snooze, ACT event.snooze: only the reminder moves.
            Mode.SNOOZE -> WhenSheetPanel(WhenSheet.Spec(WhenSheet.Verb.SNOOZE, target.isTask, now, if (target.isTask) due else start, dateOnly, morningHour),
                stringResource(R.string.cal_qa_snooze), due?.let { WhenSheet.dueText(it, now, dateOnly) }, Modifier.weight(1f, fill = false),
                onFull = { full = true }) { at, _, _ -> run { actions.snooze(target, at) } }
            // ACT event.remind_before: a one-off reminder for this occurrence.
            Mode.OFFSET -> if (start != null) WhenSheetPanel(WhenSheet.Spec(WhenSheet.Verb.REMIND, false, now, start), stringResource(R.string.cal_remind_me), null,
                Modifier.weight(1f, fill = false), onFull = null) { at, _, tile ->
                val m = tile?.minutes ?: java.time.Duration.between(at, start).toMinutes().toInt().coerceAtLeast(0)
                run { actions.remindBefore(target, m) }
            }
            // ACT task.reschedule (the due moves), ACT event.reschedule (the length stays).
            Mode.RESCHEDULE -> if (target.isTask) {
                WhenSheetPanel(WhenSheet.Spec(WhenSheet.Verb.RESCHEDULE, true, now, due, dateOnly, morningHour), stringResource(R.string.cal_qa_reschedule), null,
                    Modifier.weight(1f, fill = false), onFull = { full = true }) { at, only, _ -> run { actions.rescheduleTask(target, at, only) } }
            } else if (start != null) {
                WhenSheetPanel(WhenSheet.Spec(WhenSheet.Verb.RESCHEDULE, false, now, start, false, morningHour), stringResource(R.string.cal_qa_move), null,
                    Modifier.weight(1f, fill = false), onFull = { full = true }) { at, _, _ -> run { actions.rescheduleEvent(target, at) } }
            }
        }
    }
    // "Pick date & time…": the full day / hour / minute wheels (the full picker).
    if (full) {
        val initial = when (mode) {
            Mode.SNOOZE -> now.plusHours(1).truncatedTo(java.time.temporal.ChronoUnit.HOURS)
            else -> (if (target.isTask) due else start) ?: now.plusDays(1).withHour(morningHour).withMinute(0)
        }
        var otherDate by remember { mutableStateOf(false) }
        val pick: (ZonedDateTime, Boolean) -> Unit = { at, only ->
            full = false
            when {
                mode == Mode.SNOOZE -> run { actions.snooze(target, at) }
                target.isTask -> run { actions.rescheduleTask(target, at, only) }
                else -> run { actions.rescheduleEvent(target, at) }
            }
        }
        if (otherDate) DateDialog(initial.toLocalDate(), onDismiss = { otherDate = false }) { d ->
            otherDate = false
            pick(d.atTime(initial.toLocalTime()).atZone(z), dateOnly && mode == Mode.RESCHEDULE)
        } else WhenDialog(if (mode == Mode.SNOOZE) stringResource(R.string.cal_qa_snooze_until) else if (target.isTask) stringResource(R.string.cal_qa_reschedule) else stringResource(R.string.cal_move_to), initial, emptyList(),
            allowNoTime = target.isTask && mode == Mode.RESCHEDULE, initiallyDateOnly = dateOnly && mode == Mode.RESCHEDULE,
            onDismiss = { full = false }, onOtherDate = { otherDate = true }, onPick = pick)
    }
}

/** The header of the card: the kind icon, the title, the when line with its status badge, and Close or Back. */
@Composable
private fun SheetHeader(target: SheetTarget, line: String, status: Pair<String, String>?, back: (() -> Unit)?, onClose: (() -> Unit)?,
                        ask: (() -> Unit)? = null) {
    Row(Modifier.fillMaxWidth().padding(start = SHEET_PAD, end = 4.dp, top = 12.dp, bottom = 12.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(40.dp).background(MaterialTheme.colorScheme.primaryContainer, CircleShape), contentAlignment = Alignment.Center) {
            Icon(if (target.isTask) Icons.Outlined.TaskAlt else Icons.Outlined.Event, null, Modifier.size(22.dp),
                tint = MaterialTheme.colorScheme.onPrimaryContainer)
        }
        Column(Modifier.weight(1f).padding(start = 12.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(target.title, style = MaterialTheme.typography.titleMedium, maxLines = 2, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.semantics { heading() })
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                if (line.isNotBlank()) Text(line, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.align(Alignment.CenterVertically))
                status?.let { (text, kind) -> Box(Modifier.align(Alignment.CenterVertically)) { StatusBadge(text, kind) } }
            }
        }
        if (ask != null) IconButton(onClick = ask) {
            Icon(Icons.Outlined.Mic, stringResource(R.string.cal_qa_ask_about, io.github.murtazanasir.mailr.ui.assistantName()), tint = MaterialTheme.colorScheme.primary)
        }
        when {
            back != null -> IconButton(onClick = back) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, stringResource(R.string.cal_back)) }
            onClose != null -> IconButton(onClick = onClose) { Icon(Icons.Outlined.Close, stringResource(R.string.cal_close)) }
        }
    }
}

/** A primary action: an equal tile with its icon over its label (72 dp); the first is filled. */
@Composable
private fun ActionTile(label: String, icon: ImageVector, main: Boolean, modifier: Modifier, onClick: () -> Unit) {
    val bg = if (main) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surfaceContainerHighest
    val fg = if (main) MaterialTheme.colorScheme.onPrimaryContainer else MaterialTheme.colorScheme.onSurface
    Surface(onClick = onClick, modifier = modifier.heightIn(min = 72.dp), shape = RoundedCornerShape(16.dp), color = bg, contentColor = fg) {
        Column(Modifier.padding(horizontal = 4.dp, vertical = 12.dp), horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(6.dp, Alignment.CenterVertically)) {
            Icon(icon, null, tint = if (main) fg else MaterialTheme.colorScheme.primary)
            Text(label, style = MaterialTheme.typography.labelLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
    }
}

/** A secondary action in the text row (48 dp target). */
@Composable
private fun SecondaryButton(label: String, icon: ImageVector, color: Color = Color.Unspecified, onClick: () -> Unit) {
    TextButton(onClick = onClick, contentPadding = PaddingValues(horizontal = 12.dp),
        colors = if (color != Color.Unspecified) ButtonDefaults.textButtonColors(contentColor = color) else ButtonDefaults.textButtonColors(),
        modifier = Modifier.heightIn(min = 48.dp)) {
        Icon(icon, null, Modifier.size(18.dp))
        Spacer(Modifier.width(6.dp))
        Text(label)
    }
}

private fun whenLine(t: SheetTarget, now: ZonedDateTime, start: ZonedDateTime?, due: ZonedDateTime?, dateOnly: Boolean): String {
    if (t.isTask) {
        if (due == null) return AppText.get(R.string.rem_no_due_date)
        return WhenSheet.dueLine(due, now, dateOnly)
    }
    val e = t.event
    val where = e?.location?.takeIf { it.isNotBlank() }?.let { " · $it" }.orEmpty()
    if (start == null) return t.reminder?.detail.orEmpty()
    if (e?.allDay == true) return AppText.get(R.string.cal_qa_all_day, describeMoment(start, now, true)) + where
    val mins = java.time.Duration.between(now, start).toMinutes()
    val lead = when {
        mins in 1..90 -> AppText.plural(R.plurals.cal_alert_starts_in, mins.toInt(), mins)
        mins in -600..0 -> if (mins > -2) AppText.get(R.string.cal_qa_started_now) else AppText.plural(R.plurals.cal_alert_started_ago, (-mins).toInt(), -mins)
        else -> AppText.get(R.string.cal_qa_starts, describeMoment(start, now, relative = false))
    }
    return lead + where
}

/** The status badge: its text and kind (overdue, missed, live, snoozed). */
private fun status(t: SheetTarget, now: ZonedDateTime, due: ZonedDateTime?, dateOnly: Boolean, inProgress: Boolean): Pair<String, String>? {
    val overdue = if (t.isTask && due != null && t.task?.completed != true) WhenSheet.overdue(due, now, dateOnly) else null
    return when {
    t.reminder?.missed == true -> AppText.get(R.string.cal_qa_missed) to "missed"
    overdue != null -> overdue to "overdue"
    inProgress -> AppText.get(R.string.cal_status_in_progress) to "live"
    t.reminder?.state == RState.PENDING && t.reminder.snoozed -> AppText.get(R.string.cal_qa_snoozed) to "snoozed"
    else -> null
    }
}
