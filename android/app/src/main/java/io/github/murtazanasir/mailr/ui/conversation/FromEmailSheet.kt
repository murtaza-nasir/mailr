package io.github.murtazanasir.mailr.ui.conversation

import io.github.murtazanasir.mailr.time.AppClock
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import io.github.murtazanasir.mailr.look.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.ModalBottomSheet
import androidx.compose.material3.SheetValue
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import io.github.murtazanasir.mailr.data.api.CalendarDefaults
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.EventDraft
import io.github.murtazanasir.mailr.data.api.FromMessageDraft
import io.github.murtazanasir.mailr.data.api.TaskDraft
import io.github.murtazanasir.mailr.data.reminders.ReminderPlan
import io.github.murtazanasir.mailr.ui.calendar.DiscardDialog
import io.github.murtazanasir.mailr.ui.calendar.EventEditor
import io.github.murtazanasir.mailr.ui.calendar.EventEditorState
import io.github.murtazanasir.mailr.ui.calendar.QuickDates
import io.github.murtazanasir.mailr.ui.calendar.QuickParse
import io.github.murtazanasir.mailr.ui.calendar.TaskEditor
import io.github.murtazanasir.mailr.ui.calendar.TaskEditorState
import io.github.murtazanasir.mailr.ui.util.Format
import java.time.LocalDateTime
import java.time.ZoneId
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R

/** The editors' starting values from the server's suggestion (ACT mail.to_task @android.reader, ACT mail.to_event @android.reader). */
object FromEmailPrefill {
    fun task(d: FromMessageDraft, list: String?, uid: String): TaskEditorState {
        val due = Format.parse(d.due)
        val timed = d.due != null && !ReminderPlan.isDateOnly(d.due)
        return TaskEditorState(null, list, null, uid,
            QuickParse(d.title, due?.toLocalDate(), if (timed) due?.toLocalTime() else null, notes = d.notes.orEmpty()))
    }

    /** Without a start in the email, the event starts at the next half hour, as a new one does. */
    fun event(d: FromMessageDraft, calendar: String?, defaultReminder: Int?, uid: String,
              now: LocalDateTime = AppClock.localNow(), userDefaults: ((Boolean) -> List<Int>)? = null,
              defaultsFor: (String?, Boolean) -> List<Int>? = { _, _ -> null }): EventEditorState {
        val start = Format.parse(d.start)?.toLocalDateTime() ?: QuickDates.defaultEventStart(now)
        // An all-day suggestion's end is read as one day; the user stretches it in the editor.
        val template = CalendarEvent(uid, d.title, d.start ?: start.atZone(ZoneId.systemDefault()).toOffsetDateTime().toString(),
            if (d.allDay || d.start == null) null else d.end, allDay = d.allDay && d.start != null,
            location = d.location, description = d.notes)
        return EventEditorState(null, null, start, calendar, defaultReminder, uid, template,
            defaultsFor = defaultsFor, userDefaults = userDefaults)
    }
}

/**
 * ACT mail.to_task, ACT mail.to_event on android.reader: the task or event editor in a sheet,
 * filled with what the server read from the email, for the user to adjust before saving.
 */
@Composable
fun FromEmailSheet(
    f: FromEmail,
    collections: List<CalendarCollection>,
    defaults: CalendarDefaults,
    morningHour: Int,
    defaultReminder: Int?,
    newUid: () -> String,
    onClose: () -> Unit,
    onSaveTask: (TaskDraft) -> Unit,
    onSaveEvent: (EventDraft) -> Unit,
    /** The account's default reminders (timed or all-day) for a calendar without its own. */
    userDefaults: ((Boolean) -> List<Int>)? = null,
) {
    var dirty by remember(f.kind, f.messageId) { mutableStateOf(false) }
    var confirmDiscard by remember { mutableStateOf(false) }
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true, confirmValueChange = { v ->
        if (v == SheetValue.Hidden && dirty) { confirmDiscard = true; false } else true
    })
    val close: () -> Unit = { if (dirty) confirmDiscard = true else onClose() }
    ModalBottomSheet(onDismissRequest = close, sheetState = sheet) {
        val d = f.suggestion
        when {
            d == null -> Column(Modifier.fillMaxWidth().padding(48.dp), horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(16.dp)) {
                CircularProgressIndicator()
                Text(stringResource(R.string.conv_reading_email), style = MaterialTheme.typography.bodyMedium)
            }
            f.kind == "task" -> {
                val s = remember(f.messageId, d) { FromEmailPrefill.task(d, defaults.taskList, newUid()) }
                LaunchedEffect(s) { snapshotFlow { s.dirty }.collect { dirty = it } }
                TaskEditor(s, collections.filter { it.tasks }, emptyList(), morningHour, f.saving,
                    onClose = close, onSave = { onSaveTask(s.draft()) }, onDelete = null, onAddSubtask = null,
                    onToggleSubtask = {}, onOpenSubtask = {})
            }
            else -> {
                val s = remember(f.messageId, d) { FromEmailPrefill.event(d, defaults.calendar, defaultReminder, newUid(),
                    userDefaults = userDefaults, defaultsFor = { url, ad -> collections.firstOrNull { it.url == url }?.defaultRemindersFor(ad) }).also { st ->
                    // ACT mail.schedule_meeting: the thread's people as guests.
                    f.guests.forEach { a -> if (st.guests.none { it.email.equals(a.email, true) }) st.guests.add(io.github.murtazanasir.mailr.ui.calendar.Guest(a.email, a.name.orEmpty())) }
                } }
                LaunchedEffect(s) { snapshotFlow { s.dirty }.collect { dirty = it } }
                EventEditor(s, collections.filter { it.events }, morningHour, f.saving, onClose = close,
                    onSave = { onSaveEvent(s.draft()) }, onDelete = null)
            }
        }
    }
    if (confirmDiscard) DiscardDialog({ confirmDiscard = false }) { confirmDiscard = false; onClose() }
}
