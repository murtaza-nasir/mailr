package io.github.murtazanasir.mailr.ui.calendar

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.time.AppClock
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.i18n.LocaleFormats
import io.github.murtazanasir.mailr.ui.components.refuseObscuredTouches
import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.heightIn
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.ContentCopy
import androidx.compose.material.icons.outlined.SwapHoriz
import androidx.compose.material.icons.outlined.MailOutline
import io.github.murtazanasir.mailr.look.DropdownMenu
import io.github.murtazanasir.mailr.look.DropdownMenuItem
import androidx.compose.material.icons.outlined.ExpandLess
import androidx.compose.material.icons.outlined.ExpandMore
import androidx.compose.material.icons.outlined.Link
import androidx.compose.material.icons.outlined.Mic
import androidx.compose.material.icons.outlined.Palette
import androidx.compose.material.icons.outlined.Videocam
import io.github.murtazanasir.mailr.look.AlertDialog
import io.github.murtazanasir.mailr.look.AssistChip
import io.github.murtazanasir.mailr.look.Button
import io.github.murtazanasir.mailr.look.OutlinedTextField
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import java.time.ZoneId
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.Notes
import androidx.compose.material.icons.outlined.CalendarMonth
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.DeleteOutline
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.material.icons.outlined.Groups
import androidx.compose.material.icons.outlined.NotificationsNone
import androidx.compose.material.icons.outlined.Place
import androidx.compose.material.icons.outlined.Repeat
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import io.github.murtazanasir.mailr.look.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.ui.util.Format
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.util.Locale
import io.github.murtazanasir.mailr.data.cal.Zones

fun whenLine(e: CalendarEvent, zone: ZoneId = ZoneId.systemDefault()): String {
    val loc = Locale.getDefault()
    val md = LocaleFormats.date("MMMd", "MMM d", loc)
    val s = (if (e.allDay) Format.parse(e.start) else Format.parse(e.start)?.withZoneSameInstant(zone)) ?: return ""
    val end = e.end?.let { (if (e.allDay) Format.parse(it) else Format.parse(it)?.withZoneSameInstant(zone)) }
    val day = LocaleFormats.date("EEEEMMMMd", "EEEE, MMMM d", loc)
    if (e.allDay) {
        val last = end?.toLocalDate()?.minusDays(1)
        return if (last == null || !last.isAfter(s.toLocalDate())) AppText.get(R.string.cal_quick_when_all_day, s.format(day))
        else AppText.get(R.string.cal_quick_when_all_day_span, s.format(md), last.format(md))
    }
    val tf = DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).withLocale(loc)
    return when {
        end == null -> "${s.format(day)} · ${s.format(tf)}"
        end.toLocalDate() == s.toLocalDate() -> "${s.format(day)} · ${s.format(tf)} – ${end.format(tf)}"
        else -> AppText.get(R.string.cal_when_span, s.format(md), s.format(tf), end.format(md), end.format(tf))
    }
}

/**
 * The when line with both zones when the event's own zone differs from the one shown:
 * "Tue 6 Oct · 3:00 – 4:00 PM EDT · 9:00 – 10:00 PM Lisbon".
 */
fun whenLineZones(e: CalendarEvent, shown: ZoneId): String {
    val base = whenLine(e, shown)
    val own = Zones.of(e.tz) ?: return base
    if (e.allDay || own.rules == shown.rules) return base
    val tf = DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).withLocale(Locale.getDefault())
    val s = Format.parse(e.start)?.withZoneSameInstant(own) ?: return base
    val end = e.end?.let { Format.parse(it)?.withZoneSameInstant(own) }
    val abbr = DateTimeFormatter.ofPattern("zzz", Locale.getDefault())
    return "$base ${Format.parse(e.start)?.withZoneSameInstant(shown)?.format(abbr).orEmpty()} · ${s.format(tf)}${end?.let { " – " + it.format(tf) }.orEmpty()} ${zoneLabel(own, false)}"
}

/** In progress, Cancelled, Tentative or Moved, when one applies. */
fun statusChip(e: CalendarEvent, now: Long): String? {
    val z = ZoneId.systemDefault()
    val s = io.github.murtazanasir.mailr.data.reminders.ReminderPlan.instant(e.start, z)
    val end = io.github.murtazanasir.mailr.data.reminders.ReminderPlan.eventEnd(e, z)
    return when {
        e.cancelled -> AppText.get(R.string.cal_status_cancelled)
        s != null && end != null && now in s until end && !e.allDay -> AppText.get(R.string.cal_status_in_progress)
        e.status.equals("TENTATIVE", true) -> AppText.get(R.string.cal_grid_tentative)
        e.isOverride -> AppText.get(R.string.cal_status_moved)
        else -> null
    }
}

/**
 * The event detail (android.sheet, the calendar form of the quick-action sheet), in this order:
 * colour and title; when (both zones) and status; repeat; place, map and Join; calendar;
 * organizer and guests; my answer with a note; reminders, editable inline; description; the
 * linked email; actions.
 */
@Composable
fun EventDetail(
    e: CalendarEvent,
    master: CalendarEvent?,
    collections: List<CalendarCollection>,
    morningHour: Int,
    onClose: (() -> Unit)?,
    onEdit: () -> Unit,
    onDelete: () -> Unit,
    onRsvp: (String) -> Unit,
    onOpenEmail: (() -> Unit)? = null,
    zone: ZoneId = ZoneId.systemDefault(),
    now: Long = AppClock.millis(),
    readOnly: Boolean = false,
    onRsvpNote: ((String, String) -> Unit)? = null,
    onReminders: ((List<Int>) -> Unit)? = null,
    onDuplicate: (() -> Unit)? = null,
    onColor: (() -> Unit)? = null,
    onCopyLink: (() -> Unit)? = null,
    /** ACT event.rsvp @android.calendar for a series: (answer, note, scope) after the series question. */
    onRsvpScoped: ((String, String, String) -> Unit)? = null,
    /** ACT event.move_calendar @android.calendar: the calendar URL picked. */
    onMove: ((String) -> Unit)? = null,
    /** ACT event.email_guests @android.editor: "all", "yes" or "awaiting". */
    onEmailGuests: ((String) -> Unit)? = null,
    onAskAssistant: (() -> Unit)? = null,
    /** ACT event.prep @android.calendar: the meeting prep card (an event with guests that has not ended). */
    prep: PrepHooks? = null,
) {
    val ctx = LocalContext.current
    var scopeFor by remember(e.key) { mutableStateOf<Pair<String, String>?>(null) }
    var moveMenu by remember(e.key) { mutableStateOf(false) }
    var guestMenu by remember(e.key) { mutableStateOf(false) }
    // A series asks the series question before the answer goes out.
    fun reply(r: String, note: String = "") {
        if (e.recurring && onRsvpScoped != null) scopeFor = r to note
        else if (note.isBlank()) onRsvp(r) else onRsvpNote?.invoke(r, note)
    }
    val col = collections.firstOrNull { it.url == e.calendar }
    val color = CalendarColors.of(e.calendar, e.color ?: col?.color)
    var guestsOpen by remember(e.key) { mutableStateOf(false) }
    var descOpen by remember(e.key) { mutableStateOf(false) }
    var noteFor by remember(e.key) { mutableStateOf<String?>(null) }
    Column(Modifier.fillMaxWidth()) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            if (onClose != null) IconButton(onClick = onClose) { Icon(Icons.Outlined.Close, stringResource(R.string.cal_close)) }
            Spacer(Modifier.weight(1f))
            // ACT event.edit @android.calendar
            if (!readOnly) IconButton(onClick = onEdit) { Icon(Icons.Outlined.Edit, stringResource(R.string.cal_edit_event)) }
            // ACT event.delete @android.calendar
            // An invitation from someone else leaves only this calendar: the organizer's event stays.
            if (!readOnly) IconButton(onClick = onDelete) { Icon(Icons.Outlined.DeleteOutline,
                stringResource(if (e.isInvite) R.string.cal_remove_from_my_calendar else R.string.cal_delete_event)) }
        }
        Column(Modifier.verticalScroll(rememberScrollState()).padding(bottom = 24.dp)) {
            // 1–2. Colour, title, when, status.
            Row(Modifier.padding(start = 20.dp, end = 20.dp, top = 4.dp), verticalAlignment = Alignment.Top) {
                Box(Modifier.padding(top = 8.dp).size(16.dp).background(color, RoundedCornerShape(4.dp)))
                Spacer(Modifier.width(20.dp))
                Column {
                    Text(e.summary.ifBlank { stringResource(R.string.cal_no_title) }, style = MaterialTheme.typography.headlineSmall,
                        textDecoration = if (e.cancelled) TextDecoration.LineThrough else null,
                        modifier = Modifier.semantics { heading() })
                    Spacer(Modifier.height(4.dp))
                    Text(whenLineZones(e, zone), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    statusChip(e, now)?.let { st ->
                        Surface(color = if (e.cancelled) MaterialTheme.colorScheme.errorContainer else MaterialTheme.colorScheme.secondaryContainer,
                            shape = RoundedCornerShape(8.dp), modifier = Modifier.padding(top = 6.dp)) {
                            Text(st, style = MaterialTheme.typography.labelMedium, modifier = Modifier.padding(horizontal = 8.dp, vertical = 3.dp),
                                color = if (e.cancelled) MaterialTheme.colorScheme.onErrorContainer else MaterialTheme.colorScheme.onSecondaryContainer)
                        }
                    }
                }
            }
            Spacer(Modifier.height(12.dp))
            // 3. Repeat.
            if (e.recurring) {
                val r = Recurrence.parse(master?.rrule ?: e.rrule)
                DetailRow(Icons.Outlined.Repeat) {
                    Column {
                        Text(r?.describe() ?: stringResource(R.string.cal_repeats))
                        if (e.isOverride) Format.parse(e.occurrence)?.withZoneSameInstant(zone)?.let { was ->
                            Text(stringResource(R.string.cal_moved_from, was.format(LocaleFormats.date("EEEhmma", "EEE h:mm a"))),
                                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }
            // 4. Place, map, Join.
            e.location?.takeIf { it.isNotBlank() }?.let { where ->
                DetailRow(Icons.Outlined.Place) {
                    TextButton(onClick = {
                        catching { ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("geo:0,0?q=" + Uri.encode(where)))) }
                    }, contentPadding = androidx.compose.foundation.layout.PaddingValues(0.dp)) { Text(where) }
                }
            }
            joinLink(e)?.let { link ->
                // ACT event.join @android.calendar
                DetailRow(Icons.Outlined.Videocam) {
                    Button(onClick = { io.github.murtazanasir.mailr.ui.util.SafeLinks.open(ctx, link, customTab = false) },
                        modifier = Modifier.heightIn(min = 48.dp)) { Text(joinLabel(link), maxLines = 1, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis) }
                }
            }
            // 5. Calendar.
            DetailRow(Icons.Outlined.CalendarMonth) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    ColorDot(color)
                    Spacer(Modifier.width(8.dp))
                    Text(col?.name ?: stringResource(R.string.cal_calendar_word), color = MaterialTheme.colorScheme.onSurfaceVariant)
                    if (readOnly) Text(stringResource(R.string.cal_detail_read_only), color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            // 6. Organizer and guests.
            if (e.attendeeList.isNotEmpty()) {
                DetailRow(Icons.Outlined.Groups) {
                    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        val gs = e.attendeeList.map { Guest(it.email, it.name, it.partstat, it.role.equals("OPT-PARTICIPANT", true)) }
                        Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).clickable(onClickLabel = if (guestsOpen) stringResource(R.string.cal_hide_guests) else stringResource(R.string.cal_show_guests)) {
                            guestsOpen = !guestsOpen
                        }, verticalAlignment = Alignment.CenterVertically) {
                            Text(guestSummary(gs), fontWeight = FontWeight.Medium, modifier = Modifier.weight(1f))
                            Icon(if (guestsOpen) Icons.Outlined.ExpandLess else Icons.Outlined.ExpandMore, null)
                        }
                        e.organizer?.let { Text(stringResource(R.string.cal_organizer, e.organizerName?.ifBlank { null } ?: it), style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant) }
                        if (guestsOpen) gs.take(60).forEach { a ->
                            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.heightIn(min = 32.dp)) {
                                PartstatIcon(a.partstat)
                                Spacer(Modifier.width(8.dp))
                                Text(if (a.optional) stringResource(R.string.cal_guest_optional_line, a.display) else a.display, style = MaterialTheme.typography.bodyMedium)
                            }
                        }
                    }
                }
            } else e.organizer?.takeIf { e.isInvite }?.let { o ->
                DetailRow(Icons.Outlined.Groups) { Text(stringResource(R.string.cal_organizer, e.organizerName?.ifBlank { null } ?: o)) }
            }
            // 7. My answer (ACT event.rsvp @android.calendar), with a note.
            if (e.isInvite) {
                Surface(color = MaterialTheme.colorScheme.surfaceContainerHigh, shape = MaterialTheme.shapes.medium,
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp)) {
                    Column(Modifier.padding(12.dp)) {
                        Text(stringResource(R.string.cal_going_question), style = MaterialTheme.typography.titleSmall)
                        Spacer(Modifier.height(8.dp))
                        val opts = listOf("accepted" to stringResource(R.string.cal_rsvp_yes), "tentative" to stringResource(R.string.cal_partstat_maybe), "declined" to stringResource(R.string.cal_rsvp_no))
                        val mine = when (e.myStatus?.uppercase()) { "ACCEPTED" -> "accepted"; "TENTATIVE" -> "tentative"; "DECLINED" -> "declined"; else -> null }
                        SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
                            opts.forEachIndexed { i, (id, label) ->
                                SegmentedButton(selected = mine == id, onClick = { if (mine != id) reply(id) },
                                    shape = SegmentedButtonDefaults.itemShape(i, opts.size)) { Text(label) }
                            }
                        }
                        if (onRsvpNote != null) TextButton(onClick = { noteFor = mine ?: "accepted" }, modifier = Modifier.heightIn(min = 48.dp)) {
                            Text(stringResource(R.string.cal_add_note))
                        }
                        e.organizer?.let { o ->
                            Text(stringResource(R.string.cal_will_get_answer, e.organizerName?.ifBlank { null } ?: o), style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }
            // 8. Reminders, editable inline.
            val reminders = master?.reminders?.takeIf { it.isNotEmpty() } ?: e.reminders
            if (onReminders != null && !readOnly) ReminderPicker(reminders,
                if (e.allDay) QuickDates.allDayReminders(morningHour) else QuickDates.timedReminders,
                label = { QuickDates.reminderLabel(it, e.allDay, morningHour) }, onChange = onReminders)
            else DetailRow(Icons.Outlined.NotificationsNone) {
                Column {
                    if (reminders.isEmpty()) Text(stringResource(R.string.cal_no_reminders), color = MaterialTheme.colorScheme.onSurfaceVariant)
                    reminders.sorted().forEach { Text(QuickDates.reminderLabel(it, e.allDay, morningHour)) }
                }
            }
            // 9. Description: six lines, then More.
            e.description?.takeIf { it.isNotBlank() }?.let { d ->
                DetailRow(Icons.AutoMirrored.Outlined.Notes) {
                    Column {
                        Text(d, style = MaterialTheme.typography.bodyMedium, maxLines = if (descOpen) Int.MAX_VALUE else 6,
                            overflow = TextOverflow.Ellipsis)
                        if (!descOpen && (d.lines().size > 6 || d.length > 360)) TextButton(onClick = { descOpen = true },
                            contentPadding = androidx.compose.foundation.layout.PaddingValues(0.dp)) { Text(stringResource(R.string.cal_more)) }
                    }
                }
            }
            // Recordings (Speakr), only while that switch is on: "Last time" and the linked recording.
            io.github.murtazanasir.mailr.ui.recordings.LocalRecordings.current?.let { rec ->
                io.github.murtazanasir.mailr.ui.recordings.EventRecordings(rec, e.uid, e.occurrence)
            }
            // Meeting prep (event.prep): the last recording, threads, open items, tasks, notes and files.
            if (prep != null && MeetingPrepRules.offers(e, now)) MeetingPrepCard(e, prep)
            // 10. ACT event.open_email @android.calendar
            if (onOpenEmail != null) SourceEmailRow(onOpenEmail)
            // 11. Actions. Edit and Delete are the top bar's, once.
            HorizontalDivider(Modifier.padding(vertical = 8.dp))
            ChipFlow(Modifier.padding(horizontal = 20.dp)) {
                // ACT event.duplicate @android.calendar
                if (onDuplicate != null) AssistChip(onClick = onDuplicate, label = { Text(stringResource(R.string.cal_duplicate)) },
                    leadingIcon = { Icon(Icons.Outlined.ContentCopy, null) }, modifier = Modifier.heightIn(min = 48.dp))
                // ACT event.set_color @android.calendar
                if (onColor != null && !readOnly) AssistChip(onClick = onColor, label = { Text(stringResource(R.string.cal_colour)) },
                    leadingIcon = { Icon(Icons.Outlined.Palette, null) }, modifier = Modifier.heightIn(min = 48.dp))
                // ACT event.email_guests
                if (onEmailGuests != null && e.attendeeList.isNotEmpty()) Box {
                    AssistChip(onClick = { guestMenu = true }, label = { Text(stringResource(R.string.cal_email_guests)) },
                        leadingIcon = { Icon(Icons.Outlined.MailOutline, null) }, modifier = Modifier.heightIn(min = 48.dp))
                    DropdownMenu(guestMenu, { guestMenu = false }) {
                        DropdownMenuItem({ Text(stringResource(R.string.cal_email_all_guests)) }, { guestMenu = false; onEmailGuests("all") })
                        DropdownMenuItem({ Text(stringResource(R.string.cal_email_guests_yes)) }, { guestMenu = false; onEmailGuests("yes") })
                        DropdownMenuItem({ Text(stringResource(R.string.cal_email_guests_awaiting)) }, { guestMenu = false; onEmailGuests("awaiting") })
                    }
                }
                // ACT event.move_calendar
                val targets = collections.filter { it.events && !it.readOnly && it.url != e.calendar &&
                    (col?.connectorId == null || it.connectorId == col.connectorId) }
                if (onMove != null && !readOnly && targets.isNotEmpty()) Box {
                    AssistChip(onClick = { moveMenu = true }, label = { Text(stringResource(R.string.cal_move_to_calendar)) },
                        leadingIcon = { Icon(Icons.Outlined.SwapHoriz, null) }, modifier = Modifier.heightIn(min = 48.dp))
                    CollectionMenu(targets, null, moveMenu, { moveMenu = false }) { url -> moveMenu = false; onMove(url) }
                }
                // ACT recording.record @android.calendar: record this meeting for Speakr (Recordings (Speakr) on only).
                io.github.murtazanasir.mailr.ui.recordings.LocalRecordings.current?.let { rec ->
                    AssistChip(onClick = { rec.newRecording(io.github.murtazanasir.mailr.data.api.EventRef(e.uid, e.occurrence)) }, label = { Text(stringResource(R.string.cal_record)) },
                        leadingIcon = { Icon(Icons.Outlined.Mic, null) }, modifier = Modifier.heightIn(min = 48.dp))
                }
                if (onAskAssistant != null) AssistChip(onClick = onAskAssistant, label = { Text(stringResource(R.string.rem_action_ask, io.github.murtazanasir.mailr.ui.assistantName())) },
                    leadingIcon = { Icon(Icons.Outlined.AutoAwesome, null) }, modifier = Modifier.heightIn(min = 48.dp))
                if (onCopyLink != null) AssistChip(onClick = onCopyLink, label = { Text(stringResource(R.string.cal_copy_link)) },
                    leadingIcon = { Icon(Icons.Outlined.Link, null) }, modifier = Modifier.heightIn(min = 48.dp))
            }
        }
    }
    noteFor?.let { resp ->
        var note by remember { mutableStateOf("") }
        var answer by remember { mutableStateOf(resp) }
        AlertDialog(onDismissRequest = { noteFor = null }, title = { Text(stringResource(R.string.cal_answer_with_note)) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    val opts = listOf("accepted" to stringResource(R.string.cal_rsvp_yes), "tentative" to stringResource(R.string.cal_partstat_maybe), "declined" to stringResource(R.string.cal_rsvp_no))
                    SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
                        opts.forEachIndexed { i, (id, label) ->
                            SegmentedButton(selected = answer == id, onClick = { answer = id }, shape = SegmentedButtonDefaults.itemShape(i, opts.size)) { Text(label) }
                        }
                    }
                    OutlinedTextField(note, { note = it.take(600) }, label = { Text(stringResource(R.string.cal_note_to_organizer)) }, minLines = 2,
                        modifier = Modifier.fillMaxWidth())
                }
            },
            confirmButton = { TextButton(onClick = { noteFor = null; reply(answer, note) }, modifier = Modifier.refuseObscuredTouches()) { Text(stringResource(R.string.cal_send)) } },
            dismissButton = { TextButton(onClick = { noteFor = null }) { Text(stringResource(R.string.cal_cancel)) } })
    }
    scopeFor?.let { (r, note) ->
        ScopeSheet(stringResource(R.string.cal_answer_for), onDismiss = { scopeFor = null }) { scope ->
            scopeFor = null
            onRsvpScoped?.invoke(r, note, scope)
        }
    }
}

/**
 * "This event was deleted elsewhere" with "Restore as new", for an open detail whose event has
 * gone.
 */
@Composable
fun DeletedElsewhere(e: CalendarEvent?, onRestore: ((CalendarEvent) -> Unit)?, onClose: (() -> Unit)?) {
    Column(Modifier.fillMaxWidth().padding(24.dp), horizontalAlignment = Alignment.CenterHorizontally) {
        Icon(Icons.Outlined.CalendarMonth, null, tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(40.dp))
        Spacer(Modifier.height(12.dp))
        Text(stringResource(R.string.cal_deleted_elsewhere), style = MaterialTheme.typography.titleMedium)
        e?.let { Text(it.summary.ifBlank { stringResource(R.string.cal_no_title) }, color = MaterialTheme.colorScheme.onSurfaceVariant) }
        Spacer(Modifier.height(16.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (onClose != null) TextButton(onClick = onClose, modifier = Modifier.heightIn(min = 48.dp)) { Text(stringResource(R.string.cal_close)) }
            if (e != null && onRestore != null) Button(onClick = { onRestore(e) }, modifier = Modifier.heightIn(min = 48.dp)) { Text(stringResource(R.string.cal_restore_as_new)) }
        }
    }
}

@Composable
private fun DetailRow(icon: androidx.compose.ui.graphics.vector.ImageVector, content: @Composable () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 10.dp), verticalAlignment = Alignment.Top) {
        Icon(icon, null, tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(22.dp))
        Spacer(Modifier.width(16.dp))
        Box(Modifier.weight(1f)) { content() }
    }
}
