package io.github.murtazanasir.mailr.notes.ui

import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.notes.R
import io.github.murtazanasir.mailr.i18n.AppText
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.CloudOff
import androidx.compose.material.icons.outlined.Lock
import androidx.compose.material.icons.outlined.People
import io.github.murtazanasir.mailr.look.AlertDialog
import io.github.murtazanasir.mailr.look.Checkbox
import io.github.murtazanasir.mailr.look.DropdownMenuItem
import io.github.murtazanasir.mailr.look.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.OutlinedTextField
import io.github.murtazanasir.mailr.look.RadioButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import io.github.murtazanasir.mailr.notes.data.Note
import io.github.murtazanasir.mailr.notes.data.NotesRepository
import io.github.murtazanasir.mailr.notes.data.RepeatRule
import java.time.DayOfWeek
import java.time.format.TextStyle
import java.util.Locale

/*
 * The household-lists pieces both apps share: the Local models only switch of the ⋮ menus, the repeat dialog of a list, the
 * presence line and the offline line of an open note, list or table.
 */

/** Why the Local models only switch is locked, or null: it is on and only the owner can turn it off. */
fun localOnlyLock(on: Boolean, canLower: Boolean): String? = if (on && !canLower) AppText.get(R.string.notes_local_only_locked) else null

/**
 * "Local models only" in a ⋮ menu: a switch. Anyone who can edit turns it on; when it is on and this
 * viewer is not the owner, it shows on and locked, with the reason under it.
 */
@Composable
fun LocalOnlyMenuItem(on: Boolean, canLower: Boolean, noun: String, onToggle: (Boolean) -> Unit) {
    val lock = localOnlyLock(on, canLower)
    val on_ = stringResource(R.string.notes_on)
    val off_ = stringResource(R.string.notes_off)
    DropdownMenuItem(
        text = {
            Column {
                Text(stringResource(R.string.notes_local_only))
                Text(lock ?: if (on) stringResource(R.string.notes_local_only_on) else stringResource(when (noun) {
                    "table" -> R.string.notes_local_only_off_table
                    "list" -> R.string.notes_local_only_off_list
                    else -> R.string.notes_local_only_off_note
                }),
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.testTag("local-only-hint"))
            }
        },
        onClick = { onToggle(!on) },
        enabled = lock == null,
        leadingIcon = { Checkbox(on, null, enabled = lock == null) },
        trailingIcon = if (lock != null) ({ Icon(Icons.Outlined.Lock, null, Modifier.size(18.dp)) }) else null,
        modifier = Modifier.testTag("local-only").semantics { stateDescription = if (on) on_ else off_ },
    )
}

/**
 * A list's repeat (ACT note.repeat @android.notes @android.notesapp): Off, Weekly on a day, or Every N
 * days. On each repeat the ticked items are unticked at 4 am; their history stays.
 */
@Composable
fun RepeatDialog(vm: NotesViewModel, note: Note, onDismiss: () -> Unit) {
    val current = RepeatRule.parse(note.repeat?.rule)
    var mode by remember { mutableStateOf(when (current) { is RepeatRule.Weekly -> "weekly"; is RepeatRule.Daily -> "daily"; null -> "off" }) }
    var day by remember { mutableStateOf((current as? RepeatRule.Weekly)?.day ?: DayOfWeek.SUNDAY) }
    val weeks = (current as? RepeatRule.Weekly)?.interval ?: 1
    var days by remember { mutableStateOf(((current as? RepeatRule.Daily)?.interval ?: 3).toString()) }
    val n = days.trim().toIntOrNull()
    val daysOk = n != null && n in 1..365
    @Composable
    fun option(value: String, label: String) {
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)
            .selectable(mode == value, role = Role.RadioButton) { mode = value }.testTag("repeat-$value")) {
            RadioButton(mode == value, null)
            Text(label, Modifier.padding(start = 8.dp))
        }
    }
    AlertDialog(onDismissRequest = onDismiss, title = { Text(stringResource(R.string.notes_repeat)) },
        text = {
            Column(Modifier.testTag("repeat-sheet")) {
                Text(stringResource(R.string.notes_repeat_explainer),
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                option("off", stringResource(R.string.notes_off))
                option("weekly", if (weeks > 1) pluralStringResource(R.plurals.notes_repeat_weekly_on, weeks, weeks) else stringResource(R.string.notes_repeat_weekly_on_one))
                if (mode == "weekly") FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), modifier = Modifier.padding(start = 40.dp)) {
                    DayOfWeek.entries.forEach { d ->
                        FilterChip(selected = d == day, onClick = { day = d }, label = { Text(d.getDisplayName(TextStyle.SHORT, Locale.getDefault())) },
                            modifier = Modifier.testTag("repeat-day-${RepeatRule.code(d)}"))
                    }
                }
                option("daily", stringResource(R.string.notes_repeat_every_few_days))
                if (mode == "daily") Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(start = 40.dp)) {
                    Text(stringResource(R.string.notes_repeat_every))
                    OutlinedTextField(days, { days = it.filter(Char::isDigit).take(3) }, singleLine = true, isError = !daysOk,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                        modifier = Modifier.width(88.dp).padding(horizontal = 8.dp).testTag("repeat-days"))
                    Text(pluralStringResource(R.plurals.notes_repeat_days_unit, n ?: 0))
                }
                note.repeat?.let { r ->
                    RepeatRule.line(r)?.let { Text(it, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 8.dp)) }
                }
            }
        },
        confirmButton = {
            TextButton(enabled = mode != "daily" || daysOk, onClick = {
                val rule = when (mode) {
                    "weekly" -> RepeatRule.format(RepeatRule.Weekly(day, weeks))
                    "daily" -> RepeatRule.format(RepeatRule.Daily(n ?: 1))
                    else -> null
                }
                onDismiss()
                // ACT note.repeat @android.notes @android.notesapp: through the queue, as any edit of the list.
                if (rule != note.repeat?.rule) vm.act { vm.repo.setRepeat(note.id, rule) }
            }, modifier = Modifier.testTag("repeat-save")) { Text(stringResource(R.string.notes_save)) }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.notes_cancel)) } })
}

/** "Cleo is in this list", "Cleo and Dana are in this list", "Cleo, Dana and Olive are in this list". */
fun presenceLine(names: List<String>, noun: String): String? {
    val n = names.distinct()
    if (n.isEmpty()) return null
    val who = if (n.size == 1) n[0] else AppText.get(R.string.notes_names_and, n.dropLast(1).joinToString(AppText.get(R.string.notes_names_separator)), n.last())
    val id = when (noun) {
        "table" -> R.plurals.notes_presence_table
        "list" -> R.plurals.notes_presence_list
        else -> R.plurals.notes_presence_note
    }
    return AppText.plural(id, n.size, who)
}

/**
 * ACT note.presence @android.notes @android.notesapp: while a shared note, list or table is open and
 * the app is on screen, this phone says so every 15 s, and says "left" when it closes or goes to
 * the background. An item of your own that nobody else sees sends nothing.
 */
@Composable
fun PresenceEffect(repo: NotesRepository, kind: String, id: String, shared: Boolean) {
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    DisposableEffect(id, shared, lifecycle) {
        if (!shared) return@DisposableEffect onDispose { }
        val obs = LifecycleEventObserver { _, e ->
            when (e) {
                Lifecycle.Event.ON_START -> repo.openPresence(kind, id)
                Lifecycle.Event.ON_STOP -> repo.closePresence(id)
                else -> Unit
            }
        }
        lifecycle.addObserver(obs)
        onDispose {
            lifecycle.removeObserver(obs)
            repo.closePresence(id)
        }
    }
}

/** The quiet line at the top of an open shared item: who else has it open now. */
@Composable
fun PresenceBanner(names: List<String>, noun: String) {
    val text = presenceLine(names, noun) ?: return
    // notes.css .nt-banner: the dim surface and the secondary text in the web's look.
    val (bg, fg) = io.github.murtazanasir.mailr.look.bannerColors(io.github.murtazanasir.mailr.look.BannerTone.Neutral,
        MaterialTheme.colorScheme.secondaryContainer, MaterialTheme.colorScheme.onSecondaryContainer)
    Surface(color = bg, modifier = Modifier.fillMaxWidth().testTag("presence")) {
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(16.dp, 8.dp)
            .semantics { liveRegion = LiveRegionMode.Polite }) {
            Icon(Icons.Outlined.People, null, Modifier.size(18.dp), tint = fg)
            Text(text, style = MaterialTheme.typography.labelLarge, color = fg,
                modifier = Modifier.padding(start = 8.dp))
        }
    }
}

/**
 * The editor's offline line: "Offline · 2 changes waiting" when this item's edits wait on
 * the phone, "Offline" when nothing waits. Edits made now are kept and sent when the network is back.
 */
@Composable
fun OfflineLine(waiting: Int) {
    Surface(color = MaterialTheme.colorScheme.surfaceVariant, modifier = Modifier.fillMaxWidth().testTag("offline-line")) {
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(16.dp, 8.dp)) {
            Icon(Icons.Outlined.CloudOff, null, Modifier.size(18.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(if (waiting > 0) pluralStringResource(R.plurals.notes_offline_waiting, waiting, waiting) else stringResource(R.string.notes_offline_kept),
                style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(start = 8.dp))
        }
    }
}

/** A row someone else just checked: her name for a few seconds ("Cleo"). */
@Composable
fun TickChip(name: String, modifier: Modifier = Modifier) {
    Surface(color = MaterialTheme.colorScheme.tertiaryContainer, contentColor = MaterialTheme.colorScheme.onTertiaryContainer,
        shape = RoundedCornerShape(8.dp), modifier = modifier.testTag("tick-chip").semantics { liveRegion = LiveRegionMode.Polite }) {
        Text("✓ $name", style = MaterialTheme.typography.labelSmall, modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp))
    }
}

/** The quiet line under a repeating list's title; a tap opens the repeat dialog. */
@Composable
fun RepeatLine(note: Note, fg: Color, onClick: (() -> Unit)?) {
    val text = RepeatRule.line(note.repeat) ?: return
    val changeLabel = stringResource(R.string.notes_repeat_change)
    Text(text, style = MaterialTheme.typography.labelMedium, color = fg.copy(alpha = 0.7f),
        modifier = Modifier.fillMaxWidth().let { if (onClick != null) it.clickable(onClickLabel = changeLabel, onClick = onClick) else it }
            .padding(horizontal = 16.dp, vertical = 4.dp).testTag("repeat-line"))
}
