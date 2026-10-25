package io.github.murtazanasir.mailr.ui.calendar

import io.github.murtazanasir.mailr.time.AppClock
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.i18n.LocaleFormats
import io.github.murtazanasir.mailr.ui.components.refuseObscuredTouches
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowLeft
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.outlined.CloudOff
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material.icons.outlined.FlightTakeoff
import androidx.compose.material.icons.outlined.SyncProblem
import androidx.compose.material.icons.outlined.VisibilityOff
import io.github.murtazanasir.mailr.look.AlertDialog
import io.github.murtazanasir.mailr.look.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import io.github.murtazanasir.mailr.look.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.ModalBottomSheet
import io.github.murtazanasir.mailr.look.OutlinedButton
import io.github.murtazanasir.mailr.look.RadioButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.cal.CalConflict
import io.github.murtazanasir.mailr.ui.util.Format
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.time.format.TextStyle
import java.util.Locale

/** The heading and body of every calendar sheet, so they share spacing and type. */
@Composable
private fun SheetBody(title: String, content: @Composable () -> Unit) {
    Column(Modifier.fillMaxWidth().navigationBarsPadding().padding(start = 24.dp, end = 24.dp, bottom = 16.dp)) {
        Text(title, style = MaterialTheme.typography.titleLarge, modifier = Modifier.padding(bottom = 12.dp).semantics { heading() })
        content()
    }
}

/**
 * The series question on Android: a sheet with "This event" (the default), "This and following events"
 * and "All events". [allowThis] is false when the change is to the rule itself.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ScopeSheet(title: String, onDismiss: () -> Unit, allowThis: Boolean = true, forTask: Boolean = false, onPick: (String) -> Unit) {
    // A repeating task has one open occurrence at a time: "This occurrence" (the default) or "All".
    val options = if (forTask) listOf("this" to stringResource(R.string.cal_scope_this_occurrence), "all" to stringResource(R.string.cal_scope_all))
    else listOfNotNull("this" to stringResource(R.string.cal_scope_this_event).takeIf { allowThis }, "following" to stringResource(R.string.cal_scope_following),
        "all" to stringResource(R.string.cal_scope_all_events))
        .filter { it.second != null }
    var sel by remember { mutableStateOf(options.first().first) }
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
        SheetBody(title) {
            options.forEach { (id, label) ->
                Row(Modifier.fillMaxWidth().heightIn(min = 52.dp).selectable(sel == id, role = Role.RadioButton) { sel = id },
                    verticalAlignment = Alignment.CenterVertically) {
                    RadioButton(sel == id, null)
                    Spacer(Modifier.width(12.dp))
                    Text(label!!, style = MaterialTheme.typography.bodyLarge)
                }
            }
            if (sel == "all") Text(if (forTask) stringResource(R.string.cal_scope_all_task_hint) else stringResource(R.string.cal_scope_all_event_hint), style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(start = 52.dp, bottom = 4.dp))
            Row(Modifier.fillMaxWidth().padding(top = 12.dp), horizontalArrangement = Arrangement.End) {
                TextButton(onClick = onDismiss, modifier = Modifier.heightIn(min = 48.dp)) { Text(stringResource(R.string.cal_cancel)) }
                Spacer(Modifier.width(8.dp))
                Button(onClick = { onPick(sel) }, modifier = Modifier.heightIn(min = 48.dp)) { Text(stringResource(R.string.cal_ok)) }
            }
        }
    }
}

/** The question whether to send updates, for a change ([what] = "update", "cancel" or "invite"). */
fun sendUpdatesText(what: String, guests: Int): String {
    return when (what) {
        "cancel" -> AppText.plural(R.plurals.cal_send_cancellation, guests, guests)
        "invite" -> AppText.plural(R.plurals.cal_send_invitation, guests, guests)
        else -> AppText.plural(R.plurals.cal_send_update, guests, guests)
    }
}

/** Whether to send updates to the guests: Send (the default), Don't send, Back. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SendUpdatesSheet(what: String, guests: Int, onBack: () -> Unit, onAnswer: (Boolean) -> Unit) {
    ModalBottomSheet(onDismissRequest = onBack, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
        SheetBody(sendUpdatesText(what, guests)) {
            Text(when (what) {
                "cancel" -> stringResource(R.string.cal_send_cancellation_body)
                "invite" -> stringResource(R.string.cal_send_invitation_body)
                else -> stringResource(R.string.cal_send_update_body)
            }, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Row(Modifier.fillMaxWidth().padding(top = 20.dp), horizontalArrangement = Arrangement.End, verticalAlignment = Alignment.CenterVertically) {
                TextButton(onClick = onBack, modifier = Modifier.heightIn(min = 48.dp)) { Text(stringResource(R.string.cal_back)) }
                Spacer(Modifier.weight(1f))
                OutlinedButton(onClick = { onAnswer(false) }, modifier = Modifier.heightIn(min = 48.dp)) { Text(stringResource(R.string.cal_dont_send)) }
                Spacer(Modifier.width(8.dp))
                Button(onClick = { onAnswer(true) }, modifier = Modifier.heightIn(min = 48.dp).refuseObscuredTouches()) { Text(stringResource(R.string.cal_send)) }
            }
        }
    }
}

/**
 * The conflict sheet, same field changed elsewhere: their version and mine, with Keep theirs, Use
 * mine and Keep both. Also shown after an offline replay that met a 409.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ConflictSheet(c: CalConflict, onDismiss: () -> Unit, onTheirs: () -> Unit, onMine: () -> Unit, onBoth: () -> Unit) {
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
        SheetBody(stringResource(R.string.cal_conflict_title)) {
            val theirs = c.serverEvent
            Text(if (c.kind == "task") stringResource(R.string.cal_conflict_body_task) else stringResource(R.string.cal_conflict_body_event),
                style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(12.dp))
            VersionCard(stringResource(R.string.cal_conflict_theirs), theirs?.summary ?: c.serverTask?.summary.orEmpty(), theirs?.let { whenLine(it) }.orEmpty())
            Spacer(Modifier.height(8.dp))
            val mine = c.mine
            fun str(k: String) = (mine?.get(k) as? kotlinx.serialization.json.JsonPrimitive)?.let { if (it.isString) it.content else null }
            val mineWhen = str("start")?.let { s -> Format.parse(s)?.let { d -> d.format(LocaleFormats.date("EEEdMMMhmma", "EEE d MMM, h:mm a")) } }
            VersionCard(stringResource(R.string.cal_conflict_yours), str("summary") ?: theirs?.summary.orEmpty(), mineWhen ?: c.detail)
            Column(Modifier.fillMaxWidth().padding(top = 16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = onTheirs, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)) { Text(stringResource(R.string.cal_conflict_keep_theirs)) }
                FilledTonalButton(onClick = onMine, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)) { Text(stringResource(R.string.cal_conflict_use_mine)) }
                OutlinedButton(onClick = onBoth, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)) { Text(stringResource(R.string.cal_conflict_keep_both)) }
            }
        }
    }
}

@Composable
private fun VersionCard(label: String, title: String, detail: String) {
    Surface(color = MaterialTheme.colorScheme.surfaceContainerHigh, shape = MaterialTheme.shapes.medium, modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp)) {
            Text(label, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary)
            Text(title.ifBlank { stringResource(R.string.cal_no_title) }, style = MaterialTheme.typography.titleSmall)
            if (detail.isNotBlank()) Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

/**
 * The colour palette: 12 named swatches plus "Calendar colour" ([calendarColor] non-null, events only).
 * Every swatch carries its name, so colour is never the only signal.
 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun ColorSheet(title: String, current: String?, calendarColor: Color?, onDismiss: () -> Unit, onPick: (String?) -> Unit) {
    val dark = MaterialTheme.colorScheme.surface.let { (it.red + it.green + it.blue) / 3f < 0.5f }
    val sel = CalendarColors.swatchOf(current)
    ModalBottomSheet(onDismissRequest = onDismiss) {
        SheetBody(title) {
            if (calendarColor != null) SwatchCell(stringResource(R.string.cal_calendar_colour), calendarColor, current == null, Modifier.fillMaxWidth()) { onPick(null) }
            FlowRow(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(4.dp), maxItemsInEachRow = 3) {
                CalendarColors.swatches.forEach { s ->
                    SwatchCell(s.name, s.color(dark), sel?.id == s.id, Modifier.weight(1f)) { onPick(s.hex) }
                }
            }
        }
    }
}

@Composable
private fun SwatchCell(name: String, color: Color, selected: Boolean, modifier: Modifier, onClick: () -> Unit) {
    Row(modifier.heightIn(min = 48.dp).clip8().selectable(selected, role = Role.RadioButton, onClick = onClick).padding(horizontal = 8.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(24.dp).background(color, CircleShape)
            .then(if (selected) Modifier.border(2.dp, MaterialTheme.colorScheme.onSurface, CircleShape) else Modifier),
            contentAlignment = Alignment.Center) {
            if (selected) Icon(Icons.Filled.Check, null, Modifier.size(16.dp), tint = CalendarColors.onColor(color))
        }
        Spacer(Modifier.width(10.dp))
        Text(name, style = MaterialTheme.typography.bodyMedium)
    }
}

private fun Modifier.clip8(): Modifier = this.clip(RoundedCornerShape(8.dp))

/** The title's month picker: a year with its twelve months, and "Pick a day…". */
@Composable
fun MonthPickerDialog(anchor: LocalDate, onDismiss: () -> Unit, onPickDay: () -> Unit, onPick: (LocalDate) -> Unit) {
    var year by remember { mutableIntStateOf(anchor.year) }
    val today = AppClock.today()
    AlertDialog(onDismissRequest = onDismiss,
        title = {
            Row(verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = { year-- }) { Icon(Icons.AutoMirrored.Filled.KeyboardArrowLeft, stringResource(R.string.cal_previous_year)) }
                Text("$year", Modifier.weight(1f), textAlign = TextAlign.Center)
                IconButton(onClick = { year++ }) { Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, stringResource(R.string.cal_next_year)) }
            }
        },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                (0 until 4).forEach { r ->
                    Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                        (1..3).forEach { c ->
                            val m = r * 3 + c
                            val isAnchor = anchor.year == year && anchor.monthValue == m
                            val isNow = today.year == year && today.monthValue == m
                            val name = java.time.Month.of(m).getDisplayName(TextStyle.SHORT, Locale.getDefault())
                            Box(Modifier.weight(1f).heightIn(min = 48.dp).clip8()
                                .background(if (isAnchor) MaterialTheme.colorScheme.primary else Color.Transparent)
                                .then(if (isNow && !isAnchor) Modifier.border(1.dp, MaterialTheme.colorScheme.primary,
                                    RoundedCornerShape(8.dp)) else Modifier)
                                .clickable(onClickLabel = stringResource(R.string.cal_go_to_month, java.time.Month.of(m).getDisplayName(TextStyle.FULL, Locale.getDefault()), year)) {
                                    onPick(LocalDate.of(year, m, 1).let { if (it.year == today.year && m == today.monthValue) today else it })
                                }, contentAlignment = Alignment.Center) {
                                Text(name, color = if (isAnchor) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurface)
                            }
                        }
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = onPickDay) { Text(stringResource(R.string.cal_pick_a_day)) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.cal_cancel)) } })
}

/** A zone as people say it: "Lisbon (UTC+1)". */
fun zoneLabel(z: ZoneId, withOffset: Boolean = true): String {
    val city = z.id.substringAfterLast('/').replace('_', ' ')
    if (!withOffset) return city
    val off = AppClock.now(z).offset.id.let { if (it == "Z") "" else it.removeSuffix(":00").replace("+0", "+").replace("-0", "-") }
    return "$city (UTC$off)"
}

/** One calendar banner: an icon, a sentence, and up to two actions (48 dp each). */
@Composable
fun CalBanner(icon: ImageVector, text: String, actions: List<Pair<String, () -> Unit>> = emptyList(), error: Boolean = false,
              tone: io.github.murtazanasir.mailr.look.BannerTone = if (error) io.github.murtazanasir.mailr.look.BannerTone.Danger else io.github.murtazanasir.mailr.look.BannerTone.Info) {
    // calendar.css .cal-banner: info, .error danger, .offline warning (the web's tints in its look).
    val (bg, fg) = io.github.murtazanasir.mailr.look.bannerColors(tone,
        if (error) MaterialTheme.colorScheme.errorContainer else MaterialTheme.colorScheme.secondaryContainer,
        if (error) MaterialTheme.colorScheme.onErrorContainer else MaterialTheme.colorScheme.onSecondaryContainer)
    Surface(color = bg, shape = MaterialTheme.shapes.medium, modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp)) {
        Column(Modifier.padding(start = 16.dp, end = 8.dp, top = 8.dp, bottom = if (actions.isEmpty()) 10.dp else 2.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.heightIn(min = 32.dp)) {
                Icon(icon, null, tint = fg)
                Spacer(Modifier.width(12.dp))
                Text(text, style = MaterialTheme.typography.bodyMedium, color = fg, modifier = Modifier.weight(1f))
            }
            if (actions.isNotEmpty()) Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                actions.forEach { (label, run) -> TextButton(onClick = run, modifier = Modifier.heightIn(min = 48.dp)) { Text(label, color = fg) } }
            }
        }
    }
}

/** "Your phone is in Lisbon (UTC+1). Show times in: Lisbon / Home". */
@Composable
fun TravelBanner(phone: ZoneId, home: ZoneId, onPick: (String) -> Unit) {
    CalBanner(Icons.Outlined.FlightTakeoff, stringResource(R.string.cal_travel_banner, zoneLabel(phone)),
        listOf(zoneLabel(phone, false) to { onPick("phone") }, stringResource(R.string.cal_travel_home, zoneLabel(home, false)) to { onPick("home") }))
}

@Composable
fun OfflineBanner(waiting: Int) {
    CalBanner(Icons.Outlined.CloudOff, if (waiting > 0) pluralStringResource(R.plurals.cal_offline_waiting, waiting, waiting) else
        stringResource(R.string.cal_offline_showing), tone = io.github.murtazanasir.mailr.look.BannerTone.Warning)
}

/**
 * An account that does not sync. A refused password gets the place to enter it again (on the web)
 * instead of Retry, which cannot help; the server's own words stay behind Details.
 */
@Composable
fun AccountErrorBanner(name: String, error: String, authFailed: Boolean = false, onOpenWeb: () -> Unit = {}, onRetry: () -> Unit) {
    var details by remember { mutableStateOf(false) }
    val summary = if (authFailed) stringResource(R.string.cal_account_password, name) else stringResource(R.string.cal_account_not_syncing, name)
    val first = if (authFailed) stringResource(R.string.cal_open_on_web) to onOpenWeb else stringResource(R.string.cal_retry) to onRetry
    val more = if (details) stringResource(R.string.cal_hide_details) else stringResource(R.string.cal_details)
    CalBanner(Icons.Outlined.SyncProblem, if (details) "$summary\n$error" else summary,
        listOf(more to { details = !details }, first), error = true)
}

@Composable
fun AllHiddenBanner(onShowAll: () -> Unit) {
    CalBanner(Icons.Outlined.VisibilityOff, stringResource(R.string.cal_all_hidden), listOf(stringResource(R.string.cal_grid_show_all) to onShowAll))
}

@Composable
fun SyncErrorBanner(text: String, onRetry: () -> Unit) {
    CalBanner(Icons.Outlined.ErrorOutline, text, listOf(stringResource(R.string.cal_retry) to onRetry), error = true)
}

/** "Synced 6 min ago", shown when sync is behind (more than 5 minutes). */
fun syncedAgo(last: Long?, now: Long): String? {
    last ?: return null
    val min = (now - last) / 60_000
    return when {
        min < 5 -> null
        min < 60 -> AppText.plural(R.plurals.cal_synced_minutes_ago, min.toInt(), min)
        min < 24 * 60 -> AppText.plural(R.plurals.cal_synced_hours_ago, (min / 60).toInt(), min / 60)
        else -> AppText.plural(R.plurals.cal_synced_days_ago, (min / (24 * 60)).toInt(), min / (24 * 60))
    }
}
