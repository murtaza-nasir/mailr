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
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowLeft
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.Lock
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.outlined.PhoneAndroid
import androidx.compose.material.icons.outlined.Public
import androidx.compose.material.icons.outlined.TaskAlt
import io.github.murtazanasir.mailr.look.AlertDialog
import io.github.murtazanasir.mailr.look.DropdownMenu
import io.github.murtazanasir.mailr.look.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.OutlinedTextField
import io.github.murtazanasir.mailr.look.Switch
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import java.time.LocalDate
import java.time.format.DateTimeFormatter
import java.time.format.TextStyle
import java.util.Locale
import io.github.murtazanasir.mailr.data.cal.Zones

/**
 * The mini-month: today ringed, the shown range shaded, a dot under days with events. A tap
 * goes to that day.
 */
@Composable
fun MiniMonth(month: LocalDate, shown: Pair<LocalDate, LocalDate>?, busy: Set<LocalDate>, onMonth: (LocalDate) -> Unit,
              onPick: (LocalDate) -> Unit, modifier: Modifier = Modifier) {
    val first = weekStart(month.withDayOfMonth(1))
    val today = AppClock.today()
    Column(modifier.padding(horizontal = 12.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(month.format(LocaleFormats.date("yMMMM", "MMMM yyyy")), style = MaterialTheme.typography.titleSmall,
                modifier = Modifier.weight(1f).padding(start = 8.dp).semantics { heading() })
            IconButton(onClick = { onMonth(month.minusMonths(1)) }) { Icon(Icons.AutoMirrored.Filled.KeyboardArrowLeft, stringResource(R.string.cal_previous_month)) }
            IconButton(onClick = { onMonth(month.plusMonths(1)) }) { Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, stringResource(R.string.cal_next_month)) }
        }
        Row {
            (0L until 7L).forEach { i ->
                Text(first.plusDays(i).dayOfWeek.getDisplayName(TextStyle.NARROW, Locale.getDefault()), Modifier.weight(1f),
                    style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center)
            }
        }
        (0 until 6).forEach { w ->
            Row {
                (0 until 7).forEach { k ->
                    val d = first.plusDays((w * 7 + k).toLong())
                    val inShown = shown != null && !d.isBefore(shown.first) && d.isBefore(shown.second)
                    val dayName = d.format(LocaleFormats.date("EEEEdMMMM", "EEEE d MMMM"))
                    val label = if (d in busy) stringResource(R.string.cal_mini_has_events, dayName) else dayName
                    Box(Modifier.weight(1f).heightIn(min = 40.dp)
                        .background(if (inShown) MaterialTheme.colorScheme.secondaryContainer.copy(alpha = 0.6f) else Color.Transparent)
                        .clickable(onClickLabel = stringResource(R.string.cal_go_to_day)) { onPick(d) }
                        .semantics { contentDescription = label },
                        contentAlignment = Alignment.Center) {
                        Box(Modifier.size(30.dp).then(if (d == today) Modifier.border(1.5.dp, MaterialTheme.colorScheme.primary, CircleShape) else Modifier),
                            contentAlignment = Alignment.Center) {
                            Text(d.dayOfMonth.toString(), style = MaterialTheme.typography.labelLarge,
                                color = if (d.month == month.month) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.5f))
                        }
                        if (d in busy) Box(Modifier.align(Alignment.BottomCenter).padding(bottom = 3.dp).size(4.dp)
                            .background(MaterialTheme.colorScheme.primary, CircleShape))
                    }
                }
            }
        }
    }
}

/**
 * The drawer (≡): mini-month; calendars by account with colour and a visibility switch
 * (account-wide, or "Only on this phone"); each calendar's menu; "New calendar"; "Show tasks on
 * calendar"; the display zone.
 */
@Composable
fun CalendarDrawer(
    vm: CalendarViewModel, anchor: LocalDate, shown: Pair<LocalDate, LocalDate>, busy: Set<LocalDate>,
    accounts: List<AccountGroup>, defaultCalendar: String?, onGo: (LocalDate) -> Unit, morningHour: Int = 9,
) {
    val deviceOnly by vm.prefs.deviceOnly.collectAsState()
    val showTasks by vm.prefs.showTasks.collectAsState()
    var month by remember(anchor) { mutableStateOf(anchor.withDayOfMonth(1)) }
    var creating by remember { mutableStateOf(false) }
    var renaming by remember { mutableStateOf<CalendarCollection?>(null) }
    var coloring by remember { mutableStateOf<CalendarCollection?>(null) }
    var deleting by remember { mutableStateOf<CalendarCollection?>(null) }
    var remindering by remember { mutableStateOf<CalendarCollection?>(null) }
    Column(Modifier.verticalScroll(rememberScrollState()).padding(vertical = 12.dp)) {
        MiniMonth(month, shown, busy, { month = it }, onGo)
        HorizontalDivider(Modifier.padding(vertical = 8.dp))
        Row(Modifier.fillMaxWidth().padding(start = 24.dp, end = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(stringResource(R.string.cal_calendars), style = MaterialTheme.typography.titleSmall, modifier = Modifier.weight(1f).semantics { heading() })
        }
        // "Only on this phone": the switches below change this phone, not the account.
        DrawerSwitchRow(Icons.Outlined.PhoneAndroid, stringResource(R.string.cal_drawer_only_phone),
            if (deviceOnly) stringResource(R.string.cal_drawer_only_phone_on) else stringResource(R.string.cal_drawer_only_phone_off), deviceOnly) {
            vm.prefs.setDeviceOnly(it)
        }
        val calendarsWord = stringResource(R.string.cal_calendars)
        accounts.forEach { a ->
            if (accounts.size > 1 || a.name != calendarsWord) Text(a.name, style = MaterialTheme.typography.labelLarge,
                color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(start = 24.dp, top = 12.dp, bottom = 4.dp))
            a.error?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error,
                modifier = Modifier.padding(start = 24.dp, end = 16.dp, bottom = 4.dp)) }
            a.collections.forEach { c ->
                CalendarRow(c, c.url == defaultCalendar, deviceOnly, vm,
                    onRename = { renaming = c }, onColor = { coloring = c }, onDelete = { deleting = c },
                    onDefaultReminders = { remindering = c })
            }
        }
        TextButton(onClick = { creating = true }, modifier = Modifier.padding(start = 12.dp).heightIn(min = 48.dp)) {
            Icon(Icons.Outlined.Add, null)
            Spacer(Modifier.width(8.dp))
            Text(stringResource(R.string.cal_new_calendar))
        }
        HorizontalDivider(Modifier.padding(vertical = 8.dp))
        DrawerSwitchRow(Icons.Outlined.TaskAlt, stringResource(R.string.cal_drawer_show_tasks), null, showTasks) { vm.prefs.setShowTasks(it) }
        ZoneRow(vm)
    }

    if (creating) NewCalendarDialog(accounts, onDismiss = { creating = false }) { name, color, account ->
        creating = false
        vm.createCalendar(name, color, account)
    }
    renaming?.let { c ->
        RenameDialog(c.name, onDismiss = { renaming = null }) { renaming = null; vm.renameCalendar(c, it) }
    }
    coloring?.let { c ->
        // "Calendar colour" (null) goes back to the colour on the server (`color: ""`).
        ColorSheet(stringResource(R.string.cal_colour_for, c.name), c.color.takeIf { c.colorLocal || it != null }, null, onDismiss = { coloring = null }) { hex ->
            coloring = null
            vm.colorCalendar(c, hex ?: "")
        }
    }
    deleting?.let { c -> DeleteCalendarDialog(c, vm, onDismiss = { deleting = null }) }
    remindering?.let { c ->
        DefaultRemindersDialog(c, morningHour, onDismiss = { remindering = null }) { timed, allDay ->
            remindering = null
            vm.setDefaultReminders(c, timed, allDay)
        }
    }
}

/** ACT calendar.default_reminders @android.calendar: what new timed and all-day events in [c] start with. */
@Composable
internal fun DefaultRemindersDialog(c: CalendarCollection, morningHour: Int, onDismiss: () -> Unit, onSave: (List<Int>?, List<Int>?) -> Unit) {
    var timed by remember { mutableStateOf(c.defaultRemindersFor(false).orEmpty()) }
    var allDay by remember { mutableStateOf(c.defaultRemindersFor(true).orEmpty()) }
    AlertDialog(onDismissRequest = onDismiss, title = { Text(stringResource(R.string.cal_default_reminders_title, c.name.ifBlank { stringResource(R.string.cal_calendar_word) })) },
        text = {
            Column {
                Text(stringResource(R.string.cal_default_reminders_body), style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text(stringResource(R.string.cal_timed_events), style = MaterialTheme.typography.labelLarge, modifier = Modifier.padding(top = 12.dp))
                ReminderPicker(timed, QuickDates.timedReminders, label = { QuickDates.reminderLabel(it, false, morningHour) },
                    onChange = { timed = it })
                Text(stringResource(R.string.cal_all_day_events), style = MaterialTheme.typography.labelLarge, modifier = Modifier.padding(top = 4.dp))
                ReminderPicker(allDay, QuickDates.allDayReminders(morningHour), label = { QuickDates.reminderLabel(it, true, morningHour) },
                    onChange = { allDay = it })
            }
        },
        confirmButton = { TextButton(onClick = { onSave(timed.ifEmpty { null }, allDay.ifEmpty { null }) }) { Text(stringResource(R.string.cal_save)) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.cal_cancel)) } })
}

@Composable
private fun DrawerSwitchRow(icon: androidx.compose.ui.graphics.vector.ImageVector, title: String, sub: String?, on: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth().heightIn(min = 52.dp).clickable(onClickLabel = title) { onChange(!on) }.padding(start = 24.dp, end = 16.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Icon(icon, null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.width(16.dp))
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.bodyLarge)
            if (sub != null) Text(sub, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Switch(on, onChange)
    }
}

@Composable
private fun CalendarRow(c: CalendarCollection, isDefault: Boolean, deviceOnly: Boolean, vm: CalendarViewModel,
                        onRename: () -> Unit, onColor: () -> Unit, onDelete: () -> Unit, onDefaultReminders: () -> Unit = {}) {
    var menu by remember { mutableStateOf(false) }
    val color = CalendarColors.of(c.url, c.color)
    val state = when {
        c.deviceHidden != null -> if (c.visible) stringResource(R.string.cal_state_shown_phone) else stringResource(R.string.cal_state_hidden_phone)
        c.visible -> stringResource(R.string.cal_state_shown)
        else -> stringResource(R.string.cal_state_hidden)
    }
    Row(Modifier.fillMaxWidth().heightIn(min = 52.dp).padding(start = 16.dp, end = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        // ACT calendar.color @android.calendar: the swatch opens the colour sheet.
        Box(Modifier.size(48.dp).clip(CircleShape).clickable(onClickLabel = stringResource(R.string.cal_change_colour_of, c.name), onClick = onColor),
            contentAlignment = Alignment.Center) {
            Box(Modifier.size(18.dp).background(if (c.visible) color else Color.Transparent, RoundedCornerShape(4.dp))
                .border(2.dp, color, RoundedCornerShape(4.dp)))
        }
        Column(Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(c.name.ifBlank { stringResource(R.string.cal_calendar_word) }, style = MaterialTheme.typography.bodyLarge, maxLines = 1, overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f, fill = false))
                if (c.readOnly) Icon(Icons.Outlined.Lock, stringResource(R.string.cal_read_only), Modifier.padding(start = 4.dp).size(16.dp),
                    tint = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            val sub = listOfNotNull(sharedLabel(c), stringResource(R.string.cal_tag_default).takeIf { isDefault }, stringResource(R.string.cal_tag_this_phone).takeIf { c.deviceHidden != null },
                stringResource(R.string.cal_tag_local_only).takeIf { c.localOnly })
            if (sub.isNotEmpty()) Text(sub.joinToString(" · "), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        // ACT calendar.toggle_visibility @android.calendar
        val showCd = stringResource(R.string.cal_show_named, c.name)
        Switch(c.visible, { vm.setVisible(c, it) }, modifier = Modifier.semantics { contentDescription = showCd; stateDescription = state })
        Box {
            IconButton(onClick = { menu = true }) { Icon(Icons.Outlined.MoreVert, stringResource(R.string.cal_more_for, c.name)) }
            DropdownMenu(menu, { menu = false }) {
                // ACT calendar.show_only @android.calendar
                DropdownMenuItem({ Text(stringResource(R.string.cal_menu_show_only)) }, { menu = false; vm.showOnly(c) })
                if (c.deviceHidden != null) DropdownMenuItem({ Text(stringResource(R.string.cal_menu_follow_account)) }, { menu = false; vm.clearDeviceVisibility(c) })
                DropdownMenuItem({ Text(stringResource(R.string.cal_colour)) }, { menu = false; onColor() })
                // ACT calendar.default_reminders
                if (!c.readOnly) DropdownMenuItem({ Text(stringResource(R.string.cal_default_reminders)) }, { menu = false; onDefaultReminders() })
                // ACT calendar.rename @android.calendar
                if (c.canRenameOrDelete) DropdownMenuItem({ Text(stringResource(R.string.cal_rename)) }, { menu = false; onRename() })
                // ACT calendar.set_default @android.calendar
                if (!c.readOnly && !isDefault) DropdownMenuItem({ Text(stringResource(R.string.cal_set_as_default)) }, { menu = false; vm.setDefault(c) })
                // ACT calendar.sync @android.calendar
                DropdownMenuItem({ Text(stringResource(R.string.cal_refresh)) }, { menu = false; vm.refresh() })
                // ACT calendar.delete @android.calendar
                if (c.canRenameOrDelete) DropdownMenuItem({ Text(stringResource(R.string.cal_delete), color = MaterialTheme.colorScheme.error) }, { menu = false; onDelete() })
            }
        }
    }
}

@Composable
private fun ZoneRow(vm: CalendarViewModel) {
    val home by vm.prefs.homeZone.collectAsState()
    val zone by vm.displayZone.collectAsState()
    val phone = java.time.ZoneId.systemDefault()
    val h = Zones.of(home) ?: return
    if (h == phone) return
    // The travel banner's choice, also kept here as a setting.
    DrawerSwitchRow(Icons.Outlined.Public, stringResource(R.string.cal_drawer_home_zone), stringResource(R.string.cal_drawer_home_zone_sub, zoneLabel(h), zoneLabel(phone)), zone == h) {
        vm.prefs.setTravel(phone.id, if (it) "home" else "phone")
    }
}

/** ACT calendar.create @android.calendar: name, colour and account. */
@Composable
private fun NewCalendarDialog(accounts: List<AccountGroup>, onDismiss: () -> Unit, onCreate: (String, String?, Long?) -> Unit) {
    var name by remember { mutableStateOf("") }
    var color by remember { mutableStateOf(CalendarColors.swatches[6].hex) }
    var account by remember { mutableStateOf(accounts.firstOrNull()?.id) }
    var pickColor by remember { mutableStateOf(false) }
    AlertDialog(onDismissRequest = onDismiss, title = { Text(stringResource(R.string.cal_new_calendar)) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(name, { name = it.take(100) }, label = { Text(stringResource(R.string.cal_name)) }, singleLine = true,
                    keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Words), modifier = Modifier.fillMaxWidth())
                Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).clip(RoundedCornerShape(8.dp)).clickable { pickColor = true }.padding(horizontal = 4.dp),
                    verticalAlignment = Alignment.CenterVertically) {
                    ColorDot(CalendarColors.of(null, color), 20)
                    Spacer(Modifier.width(12.dp))
                    Text(stringResource(R.string.cal_colour_named, CalendarColors.nameOf(color) ?: color))
                }
                if (accounts.size > 1) {
                    Text(stringResource(R.string.cal_account), style = MaterialTheme.typography.labelLarge)
                    accounts.forEach { a ->
                        Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).clickable { account = a.id }, verticalAlignment = Alignment.CenterVertically) {
                            io.github.murtazanasir.mailr.look.RadioButton(account == a.id, { account = a.id })
                            Text(a.name)
                        }
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = { onCreate(name, color, account) }, enabled = name.isNotBlank()) { Text(stringResource(R.string.cal_create)) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.cal_cancel)) } })
    if (pickColor) ColorSheet(stringResource(R.string.cal_colour), color, null, onDismiss = { pickColor = false }) { pickColor = false; it?.let { h -> color = h } }
}

@Composable
private fun RenameDialog(current: String, onDismiss: () -> Unit, onRename: (String) -> Unit) {
    var name by remember { mutableStateOf(current) }
    AlertDialog(onDismissRequest = onDismiss, title = { Text(stringResource(R.string.cal_rename_calendar)) },
        text = {
            OutlinedTextField(name, { name = it.take(100) }, label = { Text(stringResource(R.string.cal_name)) }, singleLine = true, modifier = Modifier.fillMaxWidth())
        },
        confirmButton = { TextButton(onClick = { onRename(name) }, enabled = name.isNotBlank() && name.trim() != current) { Text(stringResource(R.string.cal_rename)) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.cal_cancel)) } })
}

/** calendar.delete has no undo: the count is shown, a copy can be downloaded first, and the name must be typed. */
@Composable
private fun DeleteCalendarDialog(c: CalendarCollection, vm: CalendarViewModel, onDismiss: () -> Unit) {
    val why = vm.cannotDelete(c)
    val count by produceState<Int?>(null, c.url) { value = vm.eventCount(c) }
    var typed by remember { mutableStateOf("") }
    val export = rememberIcsExport { vm.say(it) }
    if (why != null) {
        AlertDialog(onDismissRequest = onDismiss, title = { Text(stringResource(R.string.cal_cannot_delete_title, c.name)) }, text = { Text(why) },
            confirmButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.cal_ok)) } })
        return
    }
    AlertDialog(onDismissRequest = onDismiss, title = { Text(stringResource(R.string.cal_delete_title, c.name)) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(stringResource(R.string.cal_delete_cannot_undo, when (val n = count) {
                    null -> stringResource(R.string.cal_delete_counting)
                    0 -> stringResource(R.string.cal_delete_no_events)
                    else -> pluralStringResource(R.plurals.cal_delete_events, n, n)
                }))
                // ACT event.export_ics @android.calendar: the whole calendar as an .ics file, before it goes.
                DownloadCopyButton(c, export)
                OutlinedTextField(typed, { typed = it }, label = { Text(stringResource(R.string.cal_delete_type_name)) }, singleLine = true,
                    modifier = Modifier.fillMaxWidth())
            }
        },
        confirmButton = {
            TextButton(onClick = { onDismiss(); vm.deleteCalendar(c, typed.trim()) }, enabled = typed.trim() == c.name,
                modifier = Modifier.refuseObscuredTouches()) {
                Text(stringResource(R.string.cal_delete), color = if (typed.trim() == c.name) MaterialTheme.colorScheme.error else Color.Unspecified)
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.cal_cancel)) } })
}

/** Days in [events] (as the views see them), for the mini-month dots. */
fun busyDays(events: List<DayEvent>): Set<LocalDate> = buildSet {
    events.forEach { d ->
        var x = d.start.toLocalDate()
        val last = if (d.end.toLocalTime() == java.time.LocalTime.MIDNIGHT && d.end.isAfter(d.start)) d.end.toLocalDate().minusDays(1) else d.end.toLocalDate()
        var n = 0
        while (!x.isAfter(last) && n++ < 62) { add(x); x = x.plusDays(1) }
    }
}

/** "Shared with the Family group" or "Shared by Rosa", or null for the user's own calendar. */
internal fun sharedLabel(c: CalendarCollection): String? = c.shared?.let {
    if (it.kind == "user") AppText.get(R.string.cal_shared_by, it.owner ?: AppText.get(R.string.cal_shared_someone))
    else AppText.get(R.string.cal_shared_with_group, it.group ?: AppText.get(R.string.cal_shared_group_fallback))
}
