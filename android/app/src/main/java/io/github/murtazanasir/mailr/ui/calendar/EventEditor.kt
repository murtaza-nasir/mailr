package io.github.murtazanasir.mailr.ui.calendar

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.time.AppClock
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.i18n.LocaleFormats
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.layout.Box
import androidx.compose.material.icons.outlined.EventAvailable
import androidx.compose.material.icons.outlined.EventBusy
import androidx.compose.material.icons.outlined.Lock
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.outlined.Palette
import androidx.compose.material.icons.outlined.Public
import androidx.compose.material.icons.outlined.Videocam
import io.github.murtazanasir.mailr.look.DropdownMenu
import io.github.murtazanasir.mailr.look.DropdownMenuItem
import androidx.compose.material3.IconButton
import androidx.compose.runtime.mutableStateListOf
import io.github.murtazanasir.mailr.data.api.AttendeeInput
import io.github.murtazanasir.mailr.data.api.FindTimeRequest
import io.github.murtazanasir.mailr.data.api.FindTimeResult
import io.github.murtazanasir.mailr.data.api.Person
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.Notes
import androidx.compose.material.icons.outlined.Bolt
import androidx.compose.material.icons.outlined.CalendarMonth
import androidx.compose.material.icons.outlined.DeleteOutline
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material.icons.outlined.Groups
import androidx.compose.material.icons.outlined.Place
import androidx.compose.material.icons.outlined.Repeat
import androidx.compose.material.icons.outlined.Schedule
import io.github.murtazanasir.mailr.look.AlertDialog
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.OutlinedButton
import io.github.murtazanasir.mailr.look.RadioButton
import io.github.murtazanasir.mailr.look.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import io.github.murtazanasir.mailr.look.SingleChoiceSegmentedButtonRow
import io.github.murtazanasir.mailr.look.Switch
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import io.github.murtazanasir.mailr.look.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.error
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.EventDraft
import io.github.murtazanasir.mailr.data.reminders.QuickChoices
import io.github.murtazanasir.mailr.ui.util.Format
import java.time.Duration
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.util.Locale

/**
 * Everything the event sheet edits, with the rules that keep it valid: moving the start keeps
 * the duration, all-day end dates are shown inclusive, and errors appear inline only after the
 * first Save.
 */
/** A guest in the editor: their answer, and whether they are optional. */
data class Guest(val email: String, val name: String = "", val partstat: String = "NEEDS-ACTION", val optional: Boolean = false) {
    val display: String get() = name.ifBlank { email }
}

@Stable
class EventEditorState(
    original: CalendarEvent?,
    master: CalendarEvent?,
    start: LocalDateTime,
    calendar: String?,
    defaultReminder: Int?,
    val uid: String,
    /** A new event's starting values (title, times, place, notes), such as one suggested from an email. */
    template: CalendarEvent? = null,
    /** What quick create understood ("More options"): every field it parsed. */
    seed: QuickSeed? = null,
    /** The zone the calendar is shown in (the phone's, or home when travelling): the times are edited in it. */
    val displayZone: ZoneId = ZoneId.systemDefault(),
    /** A calendar's own default reminders for a timed or all-day event (calendar.default_reminders), or null. */
    private val defaultsFor: (String?, Boolean) -> List<Int>? = { _, _ -> null },
    /**
     * The account's default reminders for a timed or all-day event (GET /reminders/settings), for a
     * calendar without its own; null uses [defaultReminder] for timed events and none for all-day.
     */
    private val userDefaults: ((Boolean) -> List<Int>)? = null,
) {
    /** The event as the server has it (live editing moves it to the copy that came in: [rebase]). */
    var original: CalendarEvent? by mutableStateOf(original)
        private set
    private val o: CalendarEvent? get() = this.original
    /** Where the fields start from: the event being edited, else the template, else the quick-create line. */
    private val t = original ?: template ?: seed?.let { q ->
        CalendarEvent(uid, q.title, if (q.allDay) q.start.toLocalDate().toString() else q.start.atZone(displayZone).toOffsetDateTime().toString(),
            if (q.allDay) q.end.toLocalDate().toString() else q.end.atZone(displayZone).toOffsetDateTime().toString(), q.allDay,
            location = q.location)
    }
    /**
     * The zone of the wall-clock fields ("Time zones…"): the display zone, as the grid
     * shows the event. Another zone picked here is written as the event's zone.
     */
    var zone: ZoneId by mutableStateOf(displayZone)
    private val initialZone = zone

    var title by mutableStateOf(t?.summary.orEmpty())
    var allDay by mutableStateOf(t?.allDay ?: false)
    /** A stored time as wall-clock time in [zone]; a date stays the same date. */
    private fun local(iso: String): LocalDateTime? = if (iso.length == 10) catching { LocalDate.parse(iso).atStartOfDay() }.getOrNull()
        else Format.parse(iso)?.withZoneSameInstant(zone)?.toLocalDateTime()

    var startDate by mutableStateOf(t?.let { local(it.start)?.toLocalDate() } ?: start.toLocalDate())
    var startTime by mutableStateOf(t?.takeUnless { it.allDay }?.let { local(it.start)?.toLocalTime() } ?: start.toLocalTime())
    var endDate by mutableStateOf(initialEnd(start).toLocalDate())
    var endTime by mutableStateOf(initialEnd(start).toLocalTime())
    var location by mutableStateOf(t?.location.orEmpty())
    var notes by mutableStateOf(t?.description.orEmpty())
    var calendar by mutableStateOf(o?.calendar ?: seed?.calendar ?: calendar)
    private val accountReminder = defaultReminder
    /** The reminders a new event in [cal] starts with: the calendar's defaults, else the account's. */
    private fun defaultReminders(cal: String?, allDay: Boolean): List<Int> =
        defaultsFor(cal, allDay) ?: userDefaults?.invoke(allDay) ?: if (allDay) emptyList() else listOfNotNull(accountReminder)
    var reminders by mutableStateOf(o?.reminders ?: seed?.reminders ?: defaultReminders(calendar, t?.allDay ?: false))
    /** The reminders were set by hand (or came with the event), so a new calendar keeps them. */
    var remindersTouched by mutableStateOf(o != null || seed?.reminders != null)
    var recurrence by mutableStateOf(Recurrence.parse(master?.rrule ?: o?.rrule ?: seed?.rrule))
    /** The guest field: the guest list with each one's answer and the Optional flag. */
    val guests = mutableStateListOf<Guest>().apply {
        addAll((master?.attendeeList?.takeIf { it.isNotEmpty() } ?: o?.attendeeList).orEmpty()
            .map { Guest(it.email, it.name, it.partstat, it.role.equals("OPT-PARTICIPANT", true)) })
        seed?.guests?.forEach { g -> if (none { it.email.equals(g.email, true) }) add(Guest(g.email, g.name, "NEEDS-ACTION", false)) }
    }
    private var initialGuests = guests.toList()
    /** The colour palette: a per-event colour, or null for the calendar's. */
    var color by mutableStateOf(o?.let { e -> e.color?.takeIf { it != e.calendarColor } })
    /** Show as free (TRANSP:TRANSPARENT). */
    var free by mutableStateOf(o?.transparent ?: false)
    var private by mutableStateOf(o?.isPrivate ?: false)
    /** The conference link ("Add video link"). */
    var video by mutableStateOf(o?.url.orEmpty())
    /** Whether to send updates to the guests, answered before Save; null when nobody needs telling. */
    var sendUpdates by mutableStateOf<Boolean?>(null)
    var recurrenceTouched by mutableStateOf(false)
    var showErrors by mutableStateOf(false)
    /** An invitation's answer ("accepted", "tentative", "declined"); sent with Save, apart from the fields. */
    val invite: Boolean = o?.isInvite == true
    private val initialResponse = when (o?.myStatus?.uppercase()) { "ACCEPTED" -> "accepted"; "TENTATIVE" -> "tentative"; "DECLINED" -> "declined"; else -> null }
    var response by mutableStateOf(initialResponse)
    private var initial = snapshot()

    /** Live editing: what is in the fields now is what the server has ([dirty] compares with it). */
    fun markClean() {
        initial = snapshot()
        initialGuests = guests.toList()
        initialWhat = what()
    }

    /** Live editing: the server's copy is now [item] (the fields came from it). */
    fun rebase(item: CalendarEvent) { original = item }

    private fun initialEnd(start: LocalDateTime): LocalDateTime {
        val e = t ?: return start.plusHours(1)
        val s = local(e.start) ?: start
        val end = e.end?.let { local(it) }
        return when {
            e.allDay -> (end?.toLocalDate()?.minusDays(1)?.takeIf { !it.isBefore(s.toLocalDate()) } ?: s.toLocalDate()).atTime(s.toLocalTime().plusHours(1))
            end != null -> end
            else -> s.plusHours(1)
        }
    }

    val isNew: Boolean get() = original == null
    val start: LocalDateTime get() = startDate.atTime(startTime)
    val end: LocalDateTime get() = endDate.atTime(endTime)
    val duration: Duration get() = Duration.between(start, end)

    val titleError: String? get() = if (showErrors && title.isBlank()) AppText.get(R.string.cal_error_add_title) else null
    val timeError: String? get() = when {
        allDay && endDate.isBefore(startDate) -> AppText.get(R.string.cal_error_last_day_before)
        !allDay && !end.isAfter(start) -> AppText.get(R.string.cal_error_ends_before)
        else -> null
    }
    val valid: Boolean get() = title.isNotBlank() && timeError == null
    /** The event's own fields changed (a PATCH is needed). */
    val fieldsDirty: Boolean get() = snapshot() != initial
    /** The new answer to an invitation, when it changed. */
    val responseChanged: String? get() = response?.takeIf { it != initialResponse }
    val dirty: Boolean get() = fieldsDirty || responseChanged != null

    private fun snapshot() = listOf(title, allDay, startDate, startTime, endDate, endTime, location, notes, calendar, reminders, recurrence?.toRRule(),
        guests.toList(), color, free, private, video, zone)

    val guestsChanged: Boolean get() = guests.toList() != initialGuests
    /** Guests added since the editor opened (for "Send invitation to 2 new guests?"). */
    val newGuests: Int get() = guests.count { g -> initialGuests.none { it.email.equals(g.email, true) } }
    private fun what() = listOf(title, allDay, startDate, startTime, endDate, endTime, location, recurrence?.toRRule(), video, zone)
    private var initialWhat = what()
    /**
     * Whether to send updates is asked when I organize (not an invitation), there are guests, and what
     * they see changed: time, place, conference, title, rule or the guest list. A new event with
     * guests asks too.
     */
    val needsSendUpdates: Boolean get() = !invite && guests.isNotEmpty() &&
        (isNew || guestsChanged || listOf(title, allDay, startDate, startTime, endDate, endTime, location, recurrence?.toRRule(), video, zone) != initialWhat)
    /** "invite" when the only news is new guests (or a new event), else "update". */
    val sendWhat: String get() = if (isNew || (guestsChanged && listOf(title, allDay, startDate, startTime, endDate, endTime, location,
            recurrence?.toRRule(), video, zone) == initialWhat)) "invite" else "update"

    /** New start date: the end moves with it (same length). */
    fun moveStartDate(d: LocalDate) {
        val len = duration
        val days = java.time.temporal.ChronoUnit.DAYS.between(startDate, endDate)
        startDate = d
        if (allDay) endDate = d.plusDays(days) else end(start.plus(len))
    }

    fun moveStartTime(t: LocalTime) {
        val len = duration
        startTime = t
        end(start.plus(len))
    }

    fun setDuration(minutes: Int) = end(start.plusMinutes(minutes.toLong()))

    private fun end(e: LocalDateTime) {
        endDate = e.toLocalDate()
        endTime = e.toLocalTime()
    }

    fun toggleAllDay(on: Boolean) {
        allDay = on
        // Reminders mean different things for timed and all-day events; swap to the equivalent default.
        reminders = if (!remindersTouched) defaultReminders(calendar, on)
        else if (on) reminders.map { if (it >= 0 && it < 1440) -540 else it }.distinct()
        else reminders.map { if (it < 0) 10 else it }.distinct()
        if (!on && !end.isAfter(start)) end(start.plusHours(1))
    }

    /** Another calendar; a new event whose reminders were not changed takes that calendar's defaults. */
    fun pickCalendar(url: String) {
        calendar = url
        if (!remindersTouched) reminders = defaultReminders(url, allDay)
    }

    /** ACT event.move_calendar @android.editor: the calendar an existing event moves to (POST …/move), or null. */
    val moveTo: String? get() = o?.let { e -> calendar?.takeIf { it != e.calendar } }
    /** Fields other than the calendar changed: a PATCH is needed besides a move. */
    val otherFieldsDirty: Boolean get() = snapshot().filterIndexed { i, _ -> i != 8 } != initial.filterIndexed { i, _ -> i != 8 }

    fun draft(): EventDraft {
        val rrule = recurrence?.toRRule()
        val base = if (allDay) EventDraft(uid, title.trim(), startDate.toString(), endDate.plusDays(1).toString(), true,
            location.trim(), notes.trim(), calendar, rrule, reminders)
        else EventDraft(uid, title.trim(), start.atZone(zone).toOffsetDateTime().toString(),
            end.atZone(zone).toOffsetDateTime().toString(), false, location.trim(), notes.trim(), calendar, rrule, reminders)
        // The fields new in the calendar build go only when they were set or changed (null leaves them alone).
        val orig = o
        return base.copy(
            // Times edited in the display zone keep the event's own zone; another zone picked here becomes it.
            tz = if (allDay) null else if (zone != displayZone) zone.id else orig?.tz,
            transparent = free.takeIf { orig == null && it || orig != null && it != orig.transparent },
            classification = (if (private) "PRIVATE" else "PUBLIC").takeIf { orig == null && private || orig != null && private != orig.isPrivate },
            color = (color ?: "").takeIf { orig == null && color != null || orig != null && color != orig.color?.takeIf { it != orig.calendarColor } },
            url = video.trim().takeIf { orig == null && it.isNotEmpty() || orig != null && it != orig.url.orEmpty() },
            attendees = guests.map { AttendeeInput(it.email, it.name, if (it.optional) "OPT-PARTICIPANT" else "REQ-PARTICIPANT") }
                .takeIf { guestsChanged || (orig == null && guests.isNotEmpty()) },
            sendUpdates = sendUpdates,
            // A new event whose chips nobody changed leaves `reminders` out: the server writes the
            // same defaults the chips show. Changed chips go as they are ([] is "no reminders").
            // An edit sends them only when they changed.
            sendReminders = if (orig == null) remindersTouched else reminders != orig.reminders,
        )
    }
}

@Composable
fun EventEditor(
    s: EventEditorState,
    collections: List<CalendarCollection>,
    morningHour: Int,
    saving: Boolean,
    onClose: () -> Unit,
    onSave: (scope: String) -> Unit,
    onDelete: ((scope: String) -> Unit)?,
    onOpenEmail: (() -> Unit)? = null,
    /** Guest autocomplete (People and recent recipients). */
    people: (suspend (String) -> List<Person>)? = null,
    /** Find a time (online only). */
    findTime: (suspend (FindTimeRequest) -> FindTimeResult)? = null,
    onDuplicate: (() -> Unit)? = null,
    /** My busy time (GET /calendar/freebusy), drawn in find a time. */
    freebusy: (suspend (String, String) -> io.github.murtazanasir.mailr.data.api.FreeBusy)? = null,
    /** The live-editing bars (LiveBars), under the header. */
    live: (@Composable () -> Unit)? = null,
) {
    var pickStartDate by remember { mutableStateOf(false) }
    var pickStart by remember { mutableStateOf(false) }
    var pickEndDate by remember { mutableStateOf(false) }
    var pickEndTime by remember { mutableStateOf(false) }
    var pickRepeat by remember { mutableStateOf(false) }
    var calMenu by remember { mutableStateOf(false) }
    var askScope by remember { mutableStateOf<String?>(null) }
    var askSend by remember { mutableStateOf<String?>(null) }
    var editGuests by remember { mutableStateOf(false) }
    var finding by remember { mutableStateOf(false) }
    var pickColor by remember { mutableStateOf(false) }
    var pickZone by remember { mutableStateOf(false) }
    var showVideo by remember { mutableStateOf(s.video.isNotEmpty()) }
    var menu by remember { mutableStateOf(false) }
    val titleFocus = remember { FocusRequester() }
    val recurring = s.original?.recurring == true
    LaunchedEffect(Unit) { if (s.isNew) catching { titleFocus.requestFocus() } }

    // After the scope: the question whether to send updates when guests see the change, then the write.
    fun afterScope(scope: String) {
        if (s.needsSendUpdates) askSend = scope else { s.sendUpdates = null; onSave(scope) }
    }

    // ACT event.edit @android.editor: every field saves together; a recurring event asks for the scope first.
    fun save() {
        s.showErrors = true
        if (!s.valid) return
        if (recurring && s.otherFieldsDirty) askScope = "save" else afterScope("all")
    }

    Column(Modifier.fillMaxWidth().imePadding()) {
        Box {
            EditorHeader(if (s.isNew) stringResource(R.string.cal_quick_new_event_cd) else stringResource(R.string.cal_edit_event), if (saving) stringResource(R.string.cal_saving) else stringResource(R.string.cal_save), enabled = !saving,
                onClose = onClose, onAction = ::save)
            if (onDuplicate != null || onDelete != null) Box(Modifier.align(Alignment.CenterEnd).padding(end = 96.dp)) {
                IconButton(onClick = { menu = true }) { Icon(Icons.Outlined.MoreVert, stringResource(R.string.cal_more)) }
                DropdownMenu(menu, { menu = false }) {
                    // ACT event.duplicate @android.editor
                    if (onDuplicate != null) DropdownMenuItem({ Text(stringResource(R.string.cal_duplicate)) }, { menu = false; onDuplicate() })
                    if (onDelete != null) DropdownMenuItem({ Text(stringResource(R.string.cal_delete), color = MaterialTheme.colorScheme.error) },
                        { menu = false; if (recurring) askScope = "delete" else onDelete("all") })
                }
            }
        }
        HorizontalDivider()
        live?.invoke()
        Column(Modifier.weight(1f, fill = false).verticalScroll(rememberScrollState()).padding(bottom = 24.dp)) {
            TextField(
                s.title, { s.title = it },
                placeholder = { Text(stringResource(R.string.cal_add_title), style = MaterialTheme.typography.headlineSmall) },
                textStyle = MaterialTheme.typography.headlineSmall,
                isError = s.titleError != null,
                supportingText = s.titleError?.let { e -> { Text(e) } },
                singleLine = true,
                keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences, imeAction = ImeAction.Done),
                colors = TextFieldDefaults.colors(focusedContainerColor = Color.Transparent, unfocusedContainerColor = Color.Transparent,
                    errorContainerColor = Color.Transparent),
                modifier = Modifier.fillMaxWidth().padding(start = 40.dp, end = 8.dp).focusRequester(titleFocus),
            )
            // ACT event.rsvp @android.editor: an invitation's answer, sent with Save (POST /calendar/events/{uid}/rsvp).
            if (s.invite) FieldRow(Icons.Outlined.Groups) {
                Column {
                    Text(stringResource(R.string.cal_going_question), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    val opts = listOf("accepted" to stringResource(R.string.cal_rsvp_yes), "tentative" to stringResource(R.string.cal_partstat_maybe), "declined" to stringResource(R.string.cal_rsvp_no))
                    SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth().padding(top = 4.dp)) {
                        opts.forEachIndexed { i, (id, label) ->
                            SegmentedButton(selected = s.response == id, onClick = { s.response = id },
                                shape = SegmentedButtonDefaults.itemShape(i, opts.size)) { Text(label) }
                        }
                    }
                }
            }
            if (s.isNew) {
                FieldRow(Icons.Outlined.Bolt) {
                    ChipFlow {
                        QuickDates.eventStartPresets(AppClock.localNow(), morningHour).forEach { p ->
                            PresetChip(p.label, !s.allDay && s.startDate == p.date && s.startTime == p.time) {
                                if (s.allDay) s.toggleAllDay(false)
                                s.moveStartDate(p.date)
                                p.time?.let(s::moveStartTime)
                            }
                        }
                    }
                }
            }
            FieldRow(Icons.Outlined.Schedule, onClick = { s.toggleAllDay(!s.allDay) }, onClickLabel = stringResource(R.string.cal_toggle_all_day)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(stringResource(R.string.cal_all_day), Modifier.weight(1f))
                    Switch(s.allDay, { s.toggleAllDay(it) })
                }
            }
            val df = LocaleFormats.date("EEEMMMd", "EEE, MMM d")
            val tf = DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).withLocale(Locale.getDefault())
            // ACT event.reschedule @android.editor: a timed start is picked with the when picker; the end keeps the duration.
            DateTimeLine(if (s.allDay) stringResource(R.string.cal_from) else stringResource(R.string.cal_starts), s.startDate.format(df), s.startTime.format(tf).takeUnless { s.allDay },
                { if (s.allDay) pickStartDate = true else pickStart = true }, { pickStart = true })
            DateTimeLine(if (s.allDay) stringResource(R.string.cal_to) else stringResource(R.string.cal_ends), s.endDate.format(df), s.endTime.format(tf).takeUnless { s.allDay },
                { pickEndDate = true }, { pickEndTime = true }, error = s.timeError != null)
            AnimatedVisibility(s.timeError != null) {
                Row(Modifier.padding(start = 56.dp, bottom = 4.dp).semantics { error(s.timeError ?: "") }, verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Outlined.ErrorOutline, null, tint = MaterialTheme.colorScheme.error, modifier = Modifier.padding(end = 6.dp))
                    Text(s.timeError.orEmpty(), color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
                }
            }
            if (!s.allDay) {
                FieldRow(null) {
                    ChipFlow {
                        QuickDates.durations.forEach { (m, label) ->
                            PresetChip(label, s.duration.toMinutes() == m.toLong()) { s.setDuration(m) }
                        }
                    }
                }
            }
            // "Time zones…": a collapsed row.
            if (!s.allDay) FieldRow(Icons.Outlined.Public, onClick = { pickZone = true }, onClickLabel = stringResource(R.string.cal_change_time_zone)) {
                Text(if (s.zone == s.displayZone) stringResource(R.string.cal_time_zone_line, zoneLabel(s.zone)) else stringResource(R.string.cal_time_zone_line_here, zoneLabel(s.zone),
                    s.start.atZone(s.zone).withZoneSameInstant(s.displayZone).toLocalTime().format(tf)),
                    style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            HorizontalDivider(Modifier.padding(vertical = 4.dp))
            FieldRow(Icons.Outlined.Repeat, onClick = { pickRepeat = true }, onClickLabel = stringResource(R.string.cal_change_repeat)) {
                Column {
                    Text(s.recurrence?.describe() ?: stringResource(R.string.cal_rec_does_not_repeat))
                    if (recurring && s.original?.rrule == null && s.recurrence == null)
                        Text(stringResource(R.string.cal_part_of_series), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            ReminderPicker(
                s.reminders,
                if (s.allDay) QuickDates.allDayReminders(morningHour) else QuickDates.timedReminders,
                label = { QuickDates.reminderLabel(it, s.allDay, morningHour) },
                onChange = { s.reminders = it; s.remindersTouched = true },
            )
            if (collections.size > 1 || (collections.size == 1 && s.calendar == null)) {
                val col = collections.firstOrNull { it.url == s.calendar }
                FieldRow(Icons.Outlined.CalendarMonth, onClick = { calMenu = true }, onClickLabel = stringResource(R.string.cal_change_calendar)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        ColorDot(CalendarColors.of(s.calendar, col?.color))
                        Spacer(Modifier.width(10.dp))
                        Text(col?.name ?: stringResource(R.string.cal_default_calendar))
                        // ACT event.move_calendar: another calendar for an existing event moves the whole series on Save.
                        CollectionMenu(collections, s.calendar, calMenu, { calMenu = false }) { s.pickCalendar(it); calMenu = false }
                    }
                }
            }
            // ACT event.set_color @android.editor
            FieldRow(Icons.Outlined.Palette, onClick = { pickColor = true }, onClickLabel = stringResource(R.string.cal_change_colour)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    val col = collections.firstOrNull { it.url == s.calendar }
                    ColorDot(s.color?.let { CalendarColors.of(null, it) } ?: CalendarColors.of(s.calendar, col?.color))
                    Spacer(Modifier.width(10.dp))
                    Text(s.color?.let { CalendarColors.nameOf(it) ?: stringResource(R.string.cal_custom_colour) } ?: stringResource(R.string.cal_calendar_colour))
                }
            }
            HorizontalDivider(Modifier.padding(vertical = 4.dp))
            // ACT event.invite @android.editor, ACT event.uninvite @android.editor: the guest field, a full-screen picker.
            FieldRow(Icons.Outlined.Groups, onClick = { editGuests = true }, onClickLabel = stringResource(R.string.cal_edit_guests)) {
                Column {
                    if (s.guests.isEmpty()) Text(stringResource(R.string.cal_add_guests), color = MaterialTheme.colorScheme.onSurfaceVariant)
                    else {
                        Text(guestSummary(s.guests), style = MaterialTheme.typography.bodyLarge)
                        val names = s.guests.take(3).joinToString(", ") { it.display }
                        Text(if (s.guests.size > 3) pluralStringResource(R.plurals.cal_guests_and_more, s.guests.size - 3, names, s.guests.size - 3) else names,
                            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1)
                    }
                }
            }
            // ACT event.find_time @android.editor
            if (findTime != null && !s.allDay) FieldRow(Icons.Outlined.EventAvailable, onClick = { finding = true }, onClickLabel = stringResource(R.string.cal_find_title)) {
                Text(stringResource(R.string.cal_find_title), color = MaterialTheme.colorScheme.primary)
            }
            HorizontalDivider(Modifier.padding(vertical = 4.dp))
            FieldRow(Icons.Outlined.Place) {
                PlainField(s.location, { s.location = it }, stringResource(R.string.cal_add_location))
            }
            // ACT event.add_video @android.editor
            if (showVideo) FieldRow(Icons.Outlined.Videocam) {
                PlainField(s.video, { s.video = it.trim() }, stringResource(R.string.cal_paste_meeting_link))
            } else FieldRow(Icons.Outlined.Videocam, onClick = { showVideo = true }, onClickLabel = stringResource(R.string.cal_add_video_link)) {
                Text(stringResource(R.string.cal_add_video_link), color = MaterialTheme.colorScheme.primary)
            }
            HorizontalDivider(Modifier.padding(vertical = 4.dp))
            FieldRow(Icons.Outlined.EventBusy) {
                Column {
                    Text(stringResource(R.string.cal_show_as), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth().padding(top = 4.dp)) {
                        SegmentedButton(selected = !s.free, onClick = { s.free = false }, shape = SegmentedButtonDefaults.itemShape(0, 2)) { Text(stringResource(R.string.cal_busy)) }
                        SegmentedButton(selected = s.free, onClick = { s.free = true }, shape = SegmentedButtonDefaults.itemShape(1, 2)) { Text(stringResource(R.string.cal_free)) }
                    }
                }
            }
            FieldRow(Icons.Outlined.Lock, onClick = { s.private = !s.private }, onClickLabel = stringResource(R.string.cal_toggle_private)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(stringResource(R.string.cal_private))
                        Text(stringResource(R.string.cal_private_body), style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    Switch(s.private, { s.private = it })
                }
            }
            FieldRow(Icons.AutoMirrored.Outlined.Notes) {
                PlainField(s.notes, { s.notes = it }, stringResource(R.string.cal_add_notes), singleLine = false)
            }
            // ACT event.open_email @android.editor
            if (onOpenEmail != null) SourceEmailRow(onOpenEmail)
            // ACT event.delete @android.editor: a repeating event asks for the scope.
            if (onDelete != null) {
                Spacer(Modifier.height(8.dp))
                OutlinedButton(onClick = { if (recurring) askScope = "delete" else onDelete("all") },
                    modifier = Modifier.padding(start = 56.dp)) {
                    Icon(Icons.Outlined.DeleteOutline, null, tint = MaterialTheme.colorScheme.error)
                    Spacer(Modifier.width(8.dp))
                    Text(stringResource(R.string.cal_delete_event), color = MaterialTheme.colorScheme.error)
                }
            }
        }
    }

    if (pickStart) {
        val at = s.start.atZone(s.zone)
        WhenDialog(if (s.isNew) stringResource(R.string.cal_starts) else stringResource(R.string.cal_move_to), at, if (s.isNew) emptyList() else QuickChoices.eventReschedule(at),
            allowNoTime = false, initiallyDateOnly = false, onDismiss = { pickStart = false },
            onOtherDate = { pickStart = false; pickStartDate = true }) { moved, _ ->
            s.moveStartDate(moved.toLocalDate())
            s.moveStartTime(moved.toLocalTime())
            pickStart = false
        }
    }
    if (pickStartDate) DateDialog(s.startDate, { pickStartDate = false }) { s.moveStartDate(it); pickStartDate = false }
    if (pickEndDate) DateDialog(s.endDate, { pickEndDate = false }) { s.endDate = it; pickEndDate = false }
    if (pickEndTime) TimeDialog(s.endTime, { pickEndTime = false }) { s.endTime = it; pickEndTime = false }
    if (pickRepeat) RecurrenceDialog(s.startDate, s.recurrence, { pickRepeat = false }) {
        s.recurrence = it
        s.recurrenceTouched = true
        pickRepeat = false
    }
    askScope?.let { what ->
        ScopeSheet(
            title = if (what == "save") stringResource(R.string.cal_scope_change_event) else stringResource(R.string.cal_scope_delete_event),
            allowThis = !(what == "save" && s.recurrenceTouched),
            onDismiss = { askScope = null },
        ) { scope ->
            askScope = null
            if (what == "save") afterScope(scope) else onDelete?.invoke(scope)
        }
    }
    askSend?.let { scope ->
        SendUpdatesSheet(s.sendWhat, if (s.sendWhat == "invite" && !s.isNew) s.newGuests else s.guests.size, onBack = { askSend = null }) { send ->
            askSend = null
            s.sendUpdates = send
            onSave(scope)
        }
    }
    if (editGuests) AttendeeEditor(s.guests, people, onDone = { editGuests = false })
    if (finding && findTime != null) FindTimeSheet(s.duration.toMinutes().toInt().coerceAtLeast(15), s.zone, findTime, s.guests.size,
        onDismiss = { finding = false }, freebusy = freebusy) { start, end ->
        finding = false
        s.moveStartDate(start.toLocalDate()); s.moveStartTime(start.toLocalTime()); s.setDuration(java.time.Duration.between(start, end).toMinutes().toInt())
    }
    if (pickColor) {
        val col = collections.firstOrNull { it.url == s.calendar }
        ColorSheet(stringResource(R.string.cal_event_colour), s.color, CalendarColors.of(s.calendar, col?.color), onDismiss = { pickColor = false }) { pickColor = false; s.color = it }
    }
    if (pickZone) ZonePickerDialog(s.zone, onDismiss = { pickZone = false }) { pickZone = false; s.zone = it }
}

/** "4 guests · 2 yes, 1 maybe, 1 awaiting". */
fun guestSummary(guests: List<Guest>): String {
    val yes = guests.count { it.partstat.equals("ACCEPTED", true) }
    val maybe = guests.count { it.partstat.equals("TENTATIVE", true) }
    val no = guests.count { it.partstat.equals("DECLINED", true) }
    val waiting = guests.size - yes - maybe - no
    val parts = listOfNotNull(
        if (yes > 0) AppText.plural(R.plurals.cal_guests_yes, yes, yes) else null,
        if (maybe > 0) AppText.plural(R.plurals.cal_guests_maybe, maybe, maybe) else null,
        if (no > 0) AppText.plural(R.plurals.cal_guests_no, no, no) else null,
        if (waiting > 0) AppText.plural(R.plurals.cal_guests_awaiting, waiting, waiting) else null)
    val count = AppText.plural(R.plurals.cal_guests_count, guests.size, guests.size)
    return if (parts.isEmpty()) count else AppText.get(R.string.cal_guest_summary, count, parts.joinToString(", "))
}

@Composable
private fun DateTimeLine(label: String, date: String, time: String?, pickDate: () -> Unit, pickTime: () -> Unit, error: Boolean = false) {
    FieldRow(null) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(label, Modifier.width(64.dp), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            TextButton(onClick = pickDate, modifier = Modifier.weight(1f)) {
                Text(date, color = if (error) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface,
                    modifier = Modifier.fillMaxWidth())
            }
            if (time != null) TextButton(onClick = pickTime) {
                Text(time, color = if (error) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface)
            }
        }
    }
}

@Composable
fun PlainField(value: String, onChange: (String) -> Unit, placeholder: String, singleLine: Boolean = true) {
    TextField(value, onChange, placeholder = { Text(placeholder) }, singleLine = singleLine, minLines = 1, maxLines = if (singleLine) 1 else 8,
        keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences),
        colors = TextFieldDefaults.colors(focusedContainerColor = Color.Transparent, unfocusedContainerColor = Color.Transparent,
            unfocusedIndicatorColor = Color.Transparent),
        // Line the text up with the other fields (TextField pads its content by 16 dp).
        modifier = Modifier.fillMaxWidth().offset(x = (-16).dp))
}

/** "This event / This and following / All events". */
@Composable
fun ScopeDialog(title: String, allowThis: Boolean = true, onDismiss: () -> Unit, onPick: (String) -> Unit) {
    val options = listOfNotNull("this" to stringResource(R.string.cal_scope_this_event).takeIf { allowThis }, "following" to stringResource(R.string.cal_scope_following),
        "all" to stringResource(R.string.cal_scope_all_events))
        .filter { it.second != null }
    var sel by remember { mutableStateOf(options.first().first) }
    AlertDialog(onDismissRequest = onDismiss, title = { Text(title) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(0.dp)) {
                options.forEach { (id, label) ->
                    Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).selectable(sel == id, role = Role.RadioButton) { sel = id },
                        verticalAlignment = Alignment.CenterVertically) {
                        RadioButton(sel == id, null)
                        Spacer(Modifier.width(12.dp))
                        Text(label!!)
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = { onPick(sel) }) { Text(stringResource(R.string.cal_ok)) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.cal_cancel)) } })
}
