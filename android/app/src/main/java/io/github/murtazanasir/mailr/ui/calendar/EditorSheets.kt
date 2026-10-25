package io.github.murtazanasir.mailr.ui.calendar

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.time.AppClock
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.api.shownDetail
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.i18n.LocaleFormats
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.HelpOutline
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.HighlightOff
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material.icons.outlined.WarningAmber
import io.github.murtazanasir.mailr.look.AlertDialog
import io.github.murtazanasir.mailr.look.Button
import io.github.murtazanasir.mailr.look.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import io.github.murtazanasir.mailr.look.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import io.github.murtazanasir.mailr.look.ListItem
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.ModalBottomSheet
import io.github.murtazanasir.mailr.look.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshots.SnapshotStateList
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import io.github.murtazanasir.mailr.data.api.FindTimeRequest
import io.github.murtazanasir.mailr.data.api.FreeBusy
import io.github.murtazanasir.mailr.data.api.FindTimeResult
import io.github.murtazanasir.mailr.data.api.Person
import io.github.murtazanasir.mailr.data.api.TimeSlot
import io.github.murtazanasir.mailr.ui.util.Format
import kotlinx.coroutines.delay
import java.io.IOException
import java.time.DayOfWeek
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.time.temporal.TemporalAdjusters
import java.util.Locale

private val EMAIL = Regex("[^\\s,;<>\"]+@[^\\s,;<>\"]+\\.[^\\s,;<>\"]+")

/**
 * The guest field on Android: a full-screen picker. Autocomplete from People and recent recipients;
 * a pasted list of addresses adds them all; each guest shows their answer, an Optional toggle
 * and ×.
 */
@Composable
fun AttendeeEditor(guests: SnapshotStateList<Guest>, people: (suspend (String) -> List<Person>)?, onDone: () -> Unit) {
    var q by remember { mutableStateOf("") }
    var found by remember { mutableStateOf<List<Person>>(emptyList()) }
    LaunchedEffect(q) {
        found = emptyList()
        if (q.trim().length < 2 || people == null) return@LaunchedEffect
        delay(200)
        found = catching { people(q.trim()) }.getOrDefault(emptyList()).filter { p -> guests.none { it.email.equals(p.email, true) } }
    }
    fun add(email: String, name: String = "") {
        if (guests.none { it.email.equals(email, true) }) guests += Guest(email.trim(), name.trim())
    }
    fun addTyped(): Boolean {
        val all = EMAIL.findAll(q).map { it.value }.toList()
        if (all.isEmpty()) return false
        all.forEach { add(it) }
        q = ""
        return true
    }
    Dialog(onDismissRequest = onDone, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.surface) {
            Column(Modifier.fillMaxSize().imePadding()) {
                Row(Modifier.fillMaxWidth().padding(start = 4.dp, end = 16.dp, top = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                    IconButton(onClick = onDone) { Icon(Icons.Outlined.Close, stringResource(R.string.cal_close)) }
                    Text(stringResource(R.string.cal_guests), style = MaterialTheme.typography.titleLarge, modifier = Modifier.weight(1f).semantics { heading() })
                    Button(onClick = { addTyped(); onDone() }) { Text(stringResource(R.string.cal_done)) }
                }
                OutlinedTextField(q, { q = it }, placeholder = { Text(stringResource(R.string.cal_guests_placeholder)) },
                    leadingIcon = { Icon(Icons.Outlined.Search, null) }, singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email, imeAction = ImeAction.Done),
                    keyboardActions = KeyboardActions(onDone = { addTyped() }),
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp).testTag("guest-search"))
                if (guests.size >= 50) Row(Modifier.padding(horizontal = 20.dp), verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Outlined.WarningAmber, null, tint = MaterialTheme.colorScheme.error, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(6.dp))
                    Text(stringResource(R.string.cal_guests_50_plus), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
                }
                LazyColumn(Modifier.weight(1f)) {
                    val typed = EMAIL.findAll(q).map { it.value }.toList()
                    if (typed.isNotEmpty()) item("typed") {
                        ListItem(headlineContent = { Text(if (typed.size == 1) stringResource(R.string.cal_guests_add_one, typed[0]) else pluralStringResource(R.plurals.cal_guests_add_many, typed.size, typed.size)) },
                            modifier = Modifier.clickable { addTyped() })
                    }
                    items(found, key = { "p-" + it.email }) { p ->
                        ListItem(headlineContent = { Text(p.name.ifBlank { p.email }) },
                            supportingContent = { if (p.name.isNotBlank()) Text(p.email) },
                            leadingContent = { Avatar(p.name.ifBlank { p.email }) },
                            modifier = Modifier.clickable { add(p.email, p.name); q = "" })
                    }
                    if (found.isNotEmpty() || typed.isNotEmpty()) item("div") { HorizontalDivider() }
                    if (guests.isNotEmpty()) item("sum") {
                        Text(guestSummary(guests), style = MaterialTheme.typography.titleSmall,
                            modifier = Modifier.padding(start = 20.dp, top = 12.dp, bottom = 4.dp).semantics { heading() })
                    }
                    items(guests.toList(), key = { "g-" + it.email }) { g ->
                        GuestRow(g, onOptional = { o -> guests[guests.indexOf(g)] = g.copy(optional = o) }, onRemove = { guests.remove(g) })
                    }
                    if (guests.isEmpty() && typed.isEmpty() && found.isEmpty()) item("none") {
                        Text(stringResource(R.string.cal_guests_none), color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(20.dp))
                    }
                }
            }
        }
    }
}

@Composable
private fun Avatar(name: String) {
    Box(Modifier.size(36.dp).background(MaterialTheme.colorScheme.secondaryContainer, CircleShape), contentAlignment = Alignment.Center) {
        Text(name.take(1).uppercase(), color = MaterialTheme.colorScheme.onSecondaryContainer, style = MaterialTheme.typography.titleSmall)
    }
}

/** A guest's answer as an icon and a word (colour is never the only signal). */
fun partstatLabel(p: String): String = when (p.uppercase()) {
    "ACCEPTED" -> AppText.get(R.string.cal_partstat_going)
    "DECLINED" -> AppText.get(R.string.cal_partstat_not_going)
    "TENTATIVE" -> AppText.get(R.string.cal_partstat_maybe)
    else -> AppText.get(R.string.cal_partstat_awaiting)
}

@Composable
fun PartstatIcon(p: String) {
    val (icon, tint) = when (p.uppercase()) {
        "ACCEPTED" -> Icons.Outlined.CheckCircle to io.github.murtazanasir.mailr.ui.theme.Mail.colors.success
        "DECLINED" -> Icons.Outlined.HighlightOff to MaterialTheme.colorScheme.error
        "TENTATIVE" -> Icons.AutoMirrored.Outlined.HelpOutline to MaterialTheme.colorScheme.onSurfaceVariant
        else -> Icons.Outlined.Schedule to MaterialTheme.colorScheme.onSurfaceVariant
    }
    Icon(icon, partstatLabel(p), Modifier.size(18.dp), tint = tint)
}

@Composable
private fun GuestRow(g: Guest, onOptional: (Boolean) -> Unit, onRemove: () -> Unit) {
    Row(Modifier.fillMaxWidth().heightIn(min = 56.dp).padding(start = 16.dp, end = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        Avatar(g.display)
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(g.display, style = MaterialTheme.typography.bodyLarge, maxLines = 1)
            Row(verticalAlignment = Alignment.CenterVertically) {
                PartstatIcon(g.partstat)
                Spacer(Modifier.width(4.dp))
                Text(partstatLabel(g.partstat) + if (g.name.isNotBlank()) " · ${g.email}" else "", style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1)
            }
        }
        FilterChip(selected = g.optional, onClick = { onOptional(!g.optional) }, label = { Text(stringResource(R.string.cal_guest_optional)) },
            modifier = Modifier.heightIn(min = 48.dp))
        IconButton(onClick = onRemove) { Icon(Icons.Outlined.Close, stringResource(R.string.cal_guest_remove_cd, g.display)) }
    }
}

/**
 * Find a time on Android: a sheet with the inputs (duration, range, buffer), the ranked slots,
 * and a strip per day showing where they fall in the working day. A slot sets the event's time.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FindTimeSheet(duration: Int, zone: ZoneId, findTime: suspend (FindTimeRequest) -> FindTimeResult, guests: Int,
                  onDismiss: () -> Unit, freebusy: (suspend (String, String) -> FreeBusy)? = null,
                  onPick: (LocalDateTime, LocalDateTime) -> Unit) {
    var minutes by remember { mutableIntStateOf(duration) }
    var range by remember { mutableIntStateOf(2) }
    var buffer by remember { mutableIntStateOf(0) }
    var slots by remember { mutableStateOf<List<TimeSlot>?>(null) }
    /** My busy time in the range (GET /calendar/freebusy), drawn on the day strips; null until known or offline. */
    var busy by remember { mutableStateOf<List<Pair<LocalDateTime, LocalDateTime>>?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    val today = AppClock.today(zone)
    val ranges = listOf(
        stringResource(R.string.cal_find_this_week) to (today to today.with(TemporalAdjusters.next(DayOfWeek.MONDAY))),
        stringResource(R.string.cal_find_next_week) to today.with(TemporalAdjusters.next(DayOfWeek.MONDAY)).let { it to it.plusDays(7) },
        stringResource(R.string.cal_find_next_5_days) to (today to workingDaysAfter(today, 5)),
    )
    val needsConnection = stringResource(R.string.cal_find_needs_connection)
    val couldNotLook = stringResource(R.string.cal_find_failed)
    LaunchedEffect(minutes, range, buffer) {
        slots = null
        error = null
        val (from, to) = ranges[range].second
        if (freebusy != null) busy = catching {
            val fb = freebusy(from.atStartOfDay(zone).toOffsetDateTime().toString(), to.atStartOfDay(zone).toOffsetDateTime().toString())
            (fb.busy + fb.tentative).mapNotNull { iv ->
                val a = Format.parse(iv.start)?.withZoneSameInstant(zone)?.toLocalDateTime() ?: return@mapNotNull null
                val b = Format.parse(iv.end)?.withZoneSameInstant(zone)?.toLocalDateTime() ?: return@mapNotNull null
                a to b
            }
        }.getOrNull()
        try {
            slots = findTime(FindTimeRequest(minutes, from.atStartOfDay(zone).toOffsetDateTime().toString(),
                to.atStartOfDay(zone).toOffsetDateTime().toString(), tz = zone.id, bufferMinutes = buffer, limit = 8)).slots.take(8)
        } catch (e: IOException) {
            error = needsConnection
        } catch (e: Exception) {
            error = (e as? io.github.murtazanasir.mailr.data.api.ApiException)?.shownDetail() ?: couldNotLook
        }
    }
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
        Column(Modifier.fillMaxWidth().navigationBarsPadding().padding(horizontal = 20.dp).padding(bottom = 16.dp)) {
            Text(stringResource(R.string.cal_find_title), style = MaterialTheme.typography.titleLarge, modifier = Modifier.semantics { heading() })
            Text(stringResource(R.string.cal_find_subtitle), style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(8.dp))
            ChipFlow { listOf(15, 30, 45, 60, 90).forEach { m -> PresetChip(QuickDates.durationLabel(java.time.Duration.ofMinutes(m.toLong())), minutes == m) { minutes = m } } }
            ChipFlow { ranges.forEachIndexed { i, (l, _) -> PresetChip(l, range == i) { range = i } } }
            ChipFlow { listOf(0, 5, 10, 15).forEach { b -> PresetChip(if (b == 0) stringResource(R.string.cal_find_no_buffer) else pluralStringResource(R.plurals.cal_find_buffer, b, b), buffer == b) { buffer = b } } }
            if (guests > 0) Text(stringResource(R.string.cal_find_guests_not_shared), style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 4.dp))
            Spacer(Modifier.height(8.dp))
            val s = slots
            when {
                error != null -> Text(error!!, color = MaterialTheme.colorScheme.error)
                s == null -> Box(Modifier.fillMaxWidth().padding(24.dp), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
                s.isEmpty() -> Text(stringResource(R.string.cal_find_none),
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                else -> {
                    val tf = DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).withLocale(Locale.getDefault())
                    val df = LocaleFormats.date("EEEdMMM", "EEE d MMM")
                    val parsed = s.mapNotNull { slot ->
                        val a = Format.parse(slot.start)?.withZoneSameInstant(zone)?.toLocalDateTime() ?: return@mapNotNull null
                        val b = Format.parse(slot.end)?.withZoneSameInstant(zone)?.toLocalDateTime() ?: return@mapNotNull null
                        a to b
                    }
                    Column(Modifier.heightIn(max = 420.dp)) {
                        val useThisTime = stringResource(R.string.cal_find_use_this_time)
                        LazyColumn {
                            items(parsed, key = { it.first.toString() }) { (a, b) ->
                                Row(Modifier.fillMaxWidth().heightIn(min = 52.dp).clip(RoundedCornerShape(8.dp)).clickable(onClickLabel = useThisTime) {
                                    onPick(a, b)
                                }.padding(horizontal = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                                    Text(a.format(df), Modifier.width(96.dp), style = MaterialTheme.typography.bodyMedium)
                                    Text("${a.toLocalTime().format(tf)} – ${b.toLocalTime().format(tf)}", Modifier.weight(1f))
                                    TextButton(onClick = { onPick(a, b) }) { Text(stringResource(R.string.cal_find_use)) }
                                }
                            }
                        }
                        Spacer(Modifier.height(8.dp))
                        // Each day of the range: my busy time, and the free slots offered.
                        val (rf, rt) = ranges[range].second
                        val byDay = parsed.groupBy { it.first.toLocalDate() }
                        generateSequence(rf) { it.plusDays(1) }.takeWhile { it.isBefore(rt) }
                            .filter { it.dayOfWeek != DayOfWeek.SATURDAY && it.dayOfWeek != DayOfWeek.SUNDAY || byDay.containsKey(it) }
                            .forEach { d -> DayStrip(d.format(df), byDay[d].orEmpty(), busyOn(busy.orEmpty(), d)) }
                        if (busy != null) Row(Modifier.padding(top = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                            Box(Modifier.size(10.dp).clip(RoundedCornerShape(3.dp)).background(MaterialTheme.colorScheme.error.copy(alpha = 0.55f)))
                            Text(stringResource(R.string.cal_find_legend_busy), style = MaterialTheme.typography.labelSmall)
                            Box(Modifier.size(10.dp).clip(RoundedCornerShape(3.dp)).background(MaterialTheme.colorScheme.primary))
                            Text(stringResource(R.string.cal_find_legend_free), style = MaterialTheme.typography.labelSmall)
                        }
                    }
                }
            }
        }
    }
}

/** The parts of [busy] that fall on [d], clipped to the day. */
internal fun busyOn(busy: List<Pair<LocalDateTime, LocalDateTime>>, d: LocalDate): List<Pair<LocalDateTime, LocalDateTime>> {
    val s = d.atStartOfDay()
    val e = d.plusDays(1).atStartOfDay()
    return busy.filter { (a, b) -> a < e && b > s }.map { (a, b) -> maxOf(a, s) to minOf(b, e.minusMinutes(1)) }
}

/** One day from 8:00 to 18:00 with my busy time and the free slots marked. */
@Composable
private fun DayStrip(label: String, slots: List<Pair<LocalDateTime, LocalDateTime>>,
                     busy: List<Pair<LocalDateTime, LocalDateTime>> = emptyList()) {
    val cd = pluralStringResource(R.plurals.cal_find_day_cd, slots.size, label, busy.size, slots.size)
    Row(Modifier.fillMaxWidth().padding(vertical = 2.dp)
        .semantics { contentDescription = cd },
        verticalAlignment = Alignment.CenterVertically) {
        Text(label, Modifier.width(96.dp), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        BoxWithConstraints(Modifier.weight(1f).height(12.dp).clip(RoundedCornerShape(6.dp)).background(MaterialTheme.colorScheme.surfaceContainerHighest)) {
            val w = maxWidth
            busy.forEach { (a, b) ->
                val s = ((a.hour * 60 + a.minute - 480).coerceIn(0, 600)) / 600f
                val e = ((b.hour * 60 + b.minute - 480).coerceIn(0, 600)) / 600f
                if (e > s) Box(Modifier.offset(x = w * s).width(w * (e - s)).height(12.dp).background(MaterialTheme.colorScheme.error.copy(alpha = 0.55f)))
            }
            slots.forEach { (a, b) ->
                val s = ((a.hour * 60 + a.minute - 480).coerceIn(0, 600)) / 600f
                val e = ((b.hour * 60 + b.minute - 480).coerceIn(0, 600)) / 600f
                Box(Modifier.offset(x = w * s).width(maxOf(w * (e - s), 3.dp)).height(12.dp).background(MaterialTheme.colorScheme.primary))
            }
        }
    }
}

private fun workingDaysAfter(d: LocalDate, n: Int): LocalDate {
    var x = d
    var left = n
    while (left > 0) {
        if (x.dayOfWeek != DayOfWeek.SATURDAY && x.dayOfWeek != DayOfWeek.SUNDAY) left--
        x = x.plusDays(1)
    }
    return x
}

/** "Time zones…": the phone's zone first, then every zone, searchable by city. */
@Composable
fun ZonePickerDialog(current: ZoneId, onDismiss: () -> Unit, onPick: (ZoneId) -> Unit) {
    var q by remember { mutableStateOf("") }
    val all = remember {
        ZoneId.getAvailableZoneIds().filter { '/' in it && !it.startsWith("Etc") && !it.startsWith("SystemV") }.sorted()
    }
    val shown = remember(q) {
        val t = q.trim().replace(' ', '_')
        (listOf(ZoneId.systemDefault().id) + all.filter { t.isEmpty() || it.contains(t, ignoreCase = true) }).distinct().take(80)
    }
    AlertDialog(onDismissRequest = onDismiss, title = { Text(stringResource(R.string.cal_time_zone)) },
        text = {
            Column {
                OutlinedTextField(q, { q = it }, placeholder = { Text(stringResource(R.string.cal_zone_search)) }, singleLine = true, modifier = Modifier.fillMaxWidth())
                LazyColumn(Modifier.heightIn(max = 360.dp)) {
                    items(shown, key = { it }) { id ->
                        val z = ZoneId.of(id)
                        ListItem(headlineContent = { Text(zoneLabel(z)) }, supportingContent = { Text(id) },
                            trailingContent = if (z == current) ({ Icon(Icons.Outlined.CheckCircle, stringResource(R.string.cal_current)) }) else null,
                            modifier = Modifier.clickable { onPick(z) })
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.cal_cancel)) } })
}
