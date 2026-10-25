package io.github.murtazanasir.mailr.ui.calendar

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.time.AppClock
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.LocaleFormats
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.EventBusy
import androidx.compose.material.icons.automirrored.outlined.HelpOutline
import androidx.compose.material.icons.outlined.NotificationsNone
import androidx.compose.material.icons.outlined.Person
import androidx.compose.material.icons.outlined.Place
import androidx.compose.material.icons.outlined.Repeat
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material.icons.outlined.Videocam
import io.github.murtazanasir.mailr.look.Button
import io.github.murtazanasir.mailr.look.DropdownMenu
import io.github.murtazanasir.mailr.look.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import io.github.murtazanasir.mailr.look.InputChip
import androidx.compose.material3.InputChipDefaults
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.ModalBottomSheet
import io.github.murtazanasir.mailr.look.OutlinedTextField
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.OffsetMapping
import androidx.compose.ui.text.input.TransformedText
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.AttendeeInput
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import io.github.murtazanasir.mailr.data.api.EventDraft
import io.github.murtazanasir.mailr.data.api.Person
import kotlinx.coroutines.launch
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.util.Locale

/** Everything quick create understood, carried into the full editor by "More options". */
data class QuickSeed(
    val title: String,
    val start: LocalDateTime,
    val end: LocalDateTime,
    val allDay: Boolean,
    val rrule: String? = null,
    val location: String? = null,
    val calendar: String? = null,
    val reminders: List<Int>? = null,
    val guests: List<AttendeeInput> = emptyList(),
    val video: Boolean = false,
)

/** The colour of a highlighted token, by kind (the chips use the same). */
@Composable
private fun tokenColor(k: TokenKind): Color = when (k) {
    TokenKind.DATE, TokenKind.TIME, TokenKind.DURATION, TokenKind.SPAN, TokenKind.START -> MaterialTheme.colorScheme.primary
    TokenKind.REPEAT -> MaterialTheme.colorScheme.tertiary
    TokenKind.LOCATION -> Color(0xFF0B8043)
    TokenKind.GUEST -> Color(0xFF8E24AA)
    TokenKind.COLLECTION -> Color(0xFF039BE5)
    else -> MaterialTheme.colorScheme.secondary
}

/** Highlights each parsed token in the line (the quick-add line: every parsed token is visible). */
private class TokenHighlight(private val tokens: List<QuickToken>, private val colors: Map<TokenKind, Color>) : VisualTransformation {
    override fun filter(text: AnnotatedString): TransformedText {
        val out = buildAnnotatedString {
            append(text.text)
            tokens.forEach { t ->
                val c = colors[t.kind] ?: return@forEach
                if (t.end <= text.length) addStyle(SpanStyle(color = c, background = c.copy(alpha = 0.14f)), t.start, t.end)
            }
        }
        return TransformedText(out, OffsetMapping.Identity)
    }
}

/**
 * The quick-add line in event mode, as a sheet: the line with highlighted tokens, chips for the parse
 * (× turns a token back into title text and keeps it that way), guest suggestions that wait for a
 * tap, the conflict line, Save (Save & invite… with guests, through the question whether to send
 * updates) and "More options" with everything carried to the editor. The text survives closing
 * the sheet.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun QuickCreateSheet(
    initialText: String,
    slot: Pair<LocalDateTime, LocalDateTime>?,
    allDaySlot: Boolean,
    calendars: List<CalendarCollection>,
    defaultCalendar: String?,
    defaultReminder: Int?,
    morningHour: Int,
    places: List<String>,
    zone: ZoneId,
    conflict: suspend (LocalDateTime, LocalDateTime) -> String?,
    people: suspend (String) -> List<Person>,
    newUid: () -> String,
    onText: (String) -> Unit,
    onDismiss: () -> Unit,
    onSave: (EventDraft) -> Unit,
    onMore: (QuickSeed) -> Unit,
    now: ZonedDateTime = AppClock.now(zone),
    /** The account's default reminders (timed or all-day) for a calendar without its own. */
    userDefaults: (Boolean) -> List<Int> = { allDay -> if (allDay) emptyList() else listOfNotNull(defaultReminder) },
) {
    var text by remember { mutableStateOf(initialText) }
    var keep by remember { mutableStateOf(emptySet<String>()) }
    var calOverride by remember { mutableStateOf<String?>(null) }
    val confirmed = remember { mutableStateMapOf<String, Person>() }
    var askSend by remember { mutableStateOf(false) }
    val writable = calendars.filter { it.events && !it.readOnly }
    val opts = QuickAddOptions(QuickMode.EVENT, now, calendars = writable.map { QuickCollection(it.url, it.name) }, places = places,
        morningHour = morningHour, keep = keep)
    val r = remember(text, keep, writable) { QuickAddGrammar.parse(text, opts) }
    val timeKinds = setOf(TokenKind.DATE, TokenKind.TIME, TokenKind.DURATION, TokenKind.SPAN)
    val parsedTime = r.tokens.any { it.kind in timeKinds }

    val baseStart = slot?.first ?: QuickDates.defaultEventStart(now.toLocalDateTime())
    val start = if (parsedTime) r.start ?: baseStart else baseStart
    val allDay = if (parsedTime) r.allDay else allDaySlot
    val end = when {
        parsedTime && r.end != null -> r.end
        parsedTime && allDay -> start.toLocalDate().plusDays(1).atStartOfDay()
        parsedTime -> start.plusMinutes((r.duration ?: 60).toLong())
        slot != null -> slot.second
        allDay -> start.toLocalDate().plusDays(1).atStartOfDay()
        else -> start.plusHours(1)
    }
    val calendar = calOverride ?: r.listOrCalendar?.id?.takeIf { id -> writable.any { it.url == id } } ?: defaultCalendar
    val reminders = r.reminders.mapNotNull { it.minutes }.takeIf { r.tokens.any { t -> t.kind == TokenKind.REMINDER } }
    val guests = r.guests.mapNotNull { g ->
        when {
            g.confirmed && g.email != null -> AttendeeInput(g.email, "")
            confirmed[g.text] != null -> confirmed[g.text]!!.let { AttendeeInput(it.email, it.name) }
            else -> null
        }
    }
    val title = r.title
    val clash by produceState<String?>(null, start, end, allDay) { value = if (allDay) null else conflict(start, end) }

    fun drop(kinds: Set<TokenKind>, only: QuickToken? = null) {
        keep = keep + r.tokens.filter { if (only != null) it == only else it.kind in kinds }.map { it.text.lowercase() }
    }
    fun draft(send: Boolean?): EventDraft {
        val tf = { t: LocalDateTime -> t.atZone(zone).toOffsetDateTime().toString() }
        return EventDraft(newUid(), title.trim(), if (allDay) start.toLocalDate().toString() else tf(start),
            if (allDay) end.toLocalDate().toString() else tf(end), allDay, r.location.orEmpty(), "", calendar, r.rrule,
            // ACT calendar.default_reminders @android.calendar: the calendar's own defaults come before the account's.
            // Without a typed reminder the create leaves `reminders` out and the server writes these
            // same defaults; the list is the phone's copy until the next sync.
            reminders ?: calendars.firstOrNull { it.url == calendar }?.defaultRemindersFor(allDay) ?: userDefaults(allDay),
            attendees = guests.takeIf { it.isNotEmpty() }, sendUpdates = send?.takeIf { guests.isNotEmpty() },
            sendReminders = reminders != null)
    }
    // ACT event.create @android.calendar: Enter saves; with guests, the question whether to send updates first.
    fun save() {
        if (title.isBlank()) return
        if (guests.isNotEmpty()) askSend = true else { onText(""); onSave(draft(null)) }
    }

    val focus = remember { FocusRequester() }
    LaunchedEffect(Unit) { catching { focus.requestFocus() } }
    val newEvent = stringResource(R.string.cal_quick_new_event_cd)
    ModalBottomSheet(onDismissRequest = { onText(text); onDismiss() }, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
        Column(Modifier.fillMaxWidth().imePadding().navigationBarsPadding().padding(horizontal = 20.dp).padding(bottom = 12.dp)) {
            val colors = TokenKind.entries.associateWith { tokenColor(it) }
            OutlinedTextField(text, { text = it; onText(it) },
                placeholder = { Text(stringResource(R.string.cal_quick_placeholder)) },
                visualTransformation = remember(r.tokens, colors) { TokenHighlight(r.tokens, colors) },
                keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences, imeAction = ImeAction.Done),
                keyboardActions = KeyboardActions(onDone = { save() }),
                singleLine = true, modifier = Modifier.fillMaxWidth().focusRequester(focus).semantics { contentDescription = newEvent })
            Spacer(Modifier.size(8.dp))
            ChipFlow {
                val df = LocaleFormats.date("EEEdMMM", "EEE d MMM")
                val tfmt = DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).withLocale(Locale.getDefault())
                val whenText = if (allDay) {
                    val last = end.toLocalDate().minusDays(1)
                    if (last.isAfter(start.toLocalDate())) stringResource(R.string.cal_quick_when_all_day_span, start.format(df), last.format(df)) else stringResource(R.string.cal_quick_when_all_day, start.format(df))
                } else stringResource(R.string.cal_quick_when_timed, start.format(df), start.toLocalTime().format(tfmt), end.toLocalTime().format(tfmt))
                ParseChip(whenText, Icons.Outlined.Schedule, onRemove = if (parsedTime) ({ drop(timeKinds) }) else null)
                r.rrule?.let { rr -> ParseChip(Recurrence.parse(rr)?.describe() ?: stringResource(R.string.cal_repeats), Icons.Outlined.Repeat) { drop(setOf(TokenKind.REPEAT)) } }
                CalendarChip(writable, calendar, r.tokens.any { it.kind == TokenKind.COLLECTION }, { calOverride = it }) {
                    calOverride = null; drop(setOf(TokenKind.COLLECTION))
                }
                r.location?.let { ParseChip(it, Icons.Outlined.Place) { drop(setOf(TokenKind.LOCATION)) } }
                r.guests.forEach { g ->
                    val tok = r.tokens.firstOrNull { it.kind == TokenKind.GUEST && it.text.contains(g.text, ignoreCase = true) }
                    GuestChip(g, confirmed[g.text], people, onConfirm = { p -> confirmed[g.text] = p }) {
                        confirmed.remove(g.text); if (tok != null) drop(emptySet(), tok) else drop(setOf(TokenKind.GUEST))
                    }
                }
                reminders?.forEach { m -> ParseChip(QuickDates.reminderLabel(m, allDay, morningHour), Icons.Outlined.NotificationsNone) { drop(setOf(TokenKind.REMINDER)) } }
                if (r.video) ParseChip(stringResource(R.string.cal_video_link), Icons.Outlined.Videocam) { drop(setOf(TokenKind.VIDEO)) }
            }
            clash?.let {
                // A time conflict line: never blocks.
                Row(Modifier.padding(top = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Outlined.EventBusy, null, Modifier.size(18.dp), tint = MaterialTheme.colorScheme.error)
                    Spacer(Modifier.width(6.dp))
                    Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
                }
            }
            Row(Modifier.fillMaxWidth().padding(top = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                TextButton(onClick = {
                    onText(text)
                    onMore(QuickSeed(title, start, end, allDay, r.rrule, r.location, calendar, reminders, guests, r.video))
                }, modifier = Modifier.heightIn(min = 48.dp)) { Text(stringResource(R.string.cal_more_options)) }
                Spacer(Modifier.weight(1f))
                Button(onClick = ::save, enabled = title.isNotBlank(), modifier = Modifier.heightIn(min = 48.dp)) {
                    Text(if (guests.isNotEmpty()) stringResource(R.string.cal_save_and_invite) else stringResource(R.string.cal_save))
                }
            }
        }
    }
    if (askSend) SendUpdatesSheet("invite", guests.size, onBack = { askSend = false }) { send ->
        askSend = false
        onText("")
        onSave(draft(send))
    }
}

@Composable
private fun ParseChip(label: String, icon: androidx.compose.ui.graphics.vector.ImageVector, onRemove: (() -> Unit)?) {
    InputChip(selected = false, onClick = { onRemove?.invoke() }, label = { Text(label, maxLines = 1) },
        leadingIcon = { Icon(icon, null, Modifier.size(InputChipDefaults.IconSize)) },
        trailingIcon = onRemove?.let { { Icon(Icons.Outlined.Close, stringResource(R.string.cal_quick_keep_as_text_cd, label), Modifier.size(InputChipDefaults.IconSize)) } },
        modifier = Modifier.heightIn(min = 48.dp))
}

@Composable
private fun CalendarChip(calendars: List<CalendarCollection>, selected: String?, parsed: Boolean, onPick: (String) -> Unit, onRemove: () -> Unit) {
    var menu by remember { mutableStateOf(false) }
    val c = calendars.firstOrNull { it.url == selected }
    val calendarCd = stringResource(R.string.cal_quick_calendar_cd, c?.name ?: stringResource(R.string.cal_quick_calendar_default))
    Box {
        InputChip(selected = false, onClick = { menu = true }, label = { Text(c?.name ?: stringResource(R.string.cal_default_calendar), maxLines = 1) },
            leadingIcon = { ColorDot(CalendarColors.of(selected, c?.color)) },
            modifier = Modifier.heightIn(min = 48.dp).semantics { contentDescription = calendarCd })
        DropdownMenu(menu, { menu = false }) {
            calendars.forEach { col ->
                DropdownMenuItem({ Text(col.name) }, { menu = false; onPick(col.url) },
                    leadingIcon = { ColorDot(CalendarColors.of(col.url, col.color)) })
            }
            if (parsed) DropdownMenuItem({ Text(stringResource(R.string.cal_quick_keep_calendar_word)) }, { menu = false; onRemove() })
        }
    }
}

/** A guest from "with Dana": unconfirmed (outlined, "?") until tapped and matched to a person. */
@Composable
private fun GuestChip(g: QuickGuest, picked: Person?, people: suspend (String) -> List<Person>, onConfirm: (Person) -> Unit, onRemove: () -> Unit) {
    var menu by remember { mutableStateOf(false) }
    var matches by remember { mutableStateOf(g.matches.map { Person(it.email, it.name) }) }
    val scope = rememberCoroutineScope()
    val done = g.confirmed || picked != null
    val guestCd = if (done) stringResource(R.string.cal_quick_guest_cd, g.text) else stringResource(R.string.cal_quick_suggested_guest_cd, g.text)
    Box {
        InputChip(selected = done, onClick = {
            if (done) return@InputChip
            if (g.email != null) { onConfirm(Person(g.email, g.text)); return@InputChip }
            menu = true
            scope.launch { if (matches.isEmpty()) matches = catching { people(g.text) }.getOrDefault(emptyList()) }
        }, label = { Text(picked?.let { it.name.ifBlank { it.email } } ?: g.email ?: g.text, maxLines = 1) },
            leadingIcon = { Icon(if (done) Icons.Outlined.Person else Icons.AutoMirrored.Outlined.HelpOutline, null, Modifier.size(InputChipDefaults.IconSize)) },
            trailingIcon = { Icon(Icons.Outlined.Close, stringResource(R.string.cal_quick_remove_guest, g.text), Modifier.size(InputChipDefaults.IconSize)) },
            modifier = Modifier.heightIn(min = 48.dp).semantics { contentDescription = guestCd })
        DropdownMenu(menu, { menu = false }) {
            if (matches.isEmpty()) DropdownMenuItem({ Text(stringResource(R.string.cal_quick_no_one_called, g.text)) }, { menu = false })
            matches.take(6).forEach { p ->
                DropdownMenuItem({ Column { Text(p.name.ifBlank { p.email }); if (p.name.isNotBlank()) Text(p.email, style = MaterialTheme.typography.bodySmall) } },
                    { menu = false; onConfirm(p) })
            }
            DropdownMenuItem({ Text(stringResource(R.string.cal_quick_keep_as_text)) }, { menu = false; onRemove() })
        }
    }
}
