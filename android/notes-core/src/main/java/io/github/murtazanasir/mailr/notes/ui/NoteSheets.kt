package io.github.murtazanasir.mailr.notes.ui

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.notes.R
import io.github.murtazanasir.mailr.i18n.LocaleFormats
import io.github.murtazanasir.mailr.i18n.AppText
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.Label
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.Group
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.outlined.Person
import io.github.murtazanasir.mailr.look.AlertDialog
import io.github.murtazanasir.mailr.look.Checkbox
import io.github.murtazanasir.mailr.look.DropdownMenu
import io.github.murtazanasir.mailr.look.DropdownMenuItem
import io.github.murtazanasir.mailr.look.ExtendedFloatingActionButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import io.github.murtazanasir.mailr.look.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import io.github.murtazanasir.mailr.notes.data.Revision
import io.github.murtazanasir.mailr.notes.data.ShareDialog
import io.github.murtazanasir.mailr.notes.data.SharePerson
import io.github.murtazanasir.mailr.notes.data.notesMessage
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.time.OffsetDateTime
import java.time.ZoneId
import java.util.Locale

/** A sharing level's name ("view" → "View"); an unknown level shows as the server sends it. */
private fun levelName(level: String): String = when (level) {
    "view" -> AppText.get(R.string.notes_level_view)
    "edit" -> AppText.get(R.string.notes_level_edit)
    "share" -> AppText.get(R.string.notes_level_share)
    "owner" -> AppText.get(R.string.notes_level_owner)
    else -> level
}

/**
 * The people sheet: who sees the note or table and at what level; add people
 * and groups, change a level, stop sharing (ACT note.share @android.notes @android.notesapp, ACT note.unshare @android.notes @android.notesapp, ACT table.share @android.notes @android.notesapp,
 * ACT table.unshare @android.notes @android.notesapp), or remove a shared one from your own notes (ACT note.leave @android.notes @android.notesapp). Online only:
 * sharing changes who can see something, so it never waits in the queue.
 */
@Composable
fun ShareSheet(vm: NotesViewModel, kind: String, id: String, onDismiss: () -> Unit) {
    val repo = vm.repo
    val scope = rememberCoroutineScope()
    var dialog by remember { mutableStateOf<ShareDialog?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var adding by remember { mutableStateOf(false) }
    var filter by remember { mutableStateOf("") }
    var level by remember { mutableStateOf("edit") }
    LaunchedEffect(id) {
        try { dialog = repo.api.shares(kind, id) } catch (e: CancellationException) { throw e } catch (e: Exception) { error = e.notesMessage() }
    }
    fun change(block: suspend () -> ShareDialog) {
        scope.launch {
            try {
                dialog = block()
                error = null
                if (kind == "table") repo.refreshTable(id) else repo.refreshNote(id)
            } catch (e: CancellationException) { throw e } catch (e: Exception) { error = e.notesMessage() }
        }
    }
    val d = dialog
    AlertDialog(onDismissRequest = onDismiss, title = { Text(stringResource(if (kind == "table") R.string.notes_share_table else R.string.notes_share_note)) },
        confirmButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.notes_done)) } },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()).testTag("share-sheet")) {
                error?.let { Text(it, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(bottom = 8.dp)) }
                if (d == null) {
                    if (error == null) Text(stringResource(R.string.notes_loading))
                    return@Column
                }
                d.owner?.let { o ->
                    PersonLine(if (o.kind == "group") Icons.Outlined.Group else Icons.Outlined.Person,
                        (o.name ?: "").let { if (o.kind == "group") stringResource(R.string.notes_name_group, it) else it }, stringResource(R.string.notes_level_owner))
                }
                d.people.forEach { p -> SharedLine(p, d, onLevel = { lv ->
                    change { repo.api.share(kind, id, p.id.toString(), lv) }
                }, onRemove = { change { repo.api.unshare(kind, id, p.id.toString()) } }) }
                d.groups.forEach { g -> SharedLine(g, d, onLevel = { lv ->
                    change { repo.api.share(kind, id, "group:" + g.name, lv) }
                }, onRemove = { change { repo.api.unshare(kind, id, "group:" + g.name) } }) }
                when (d.policy) {
                    "groups" -> Text(stringResource(R.string.notes_share_policy_groups), style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.padding(vertical = 8.dp))
                    "nobody" -> Text(stringResource(R.string.notes_share_policy_nobody), style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.padding(vertical = 8.dp))
                }
                if (d.canShare && d.policy != "nobody") {
                    HorizontalDivider(Modifier.padding(vertical = 8.dp))
                    if (!adding) TextButton(onClick = { adding = true }) { Icon(Icons.Filled.Add, null); Text(stringResource(R.string.notes_add_people_or_groups)) }
                    else {
                        // A filter line once there are many people; a few are listed at once.
                        if (d.candidates.size + d.groupCandidates.size > 8) OutlinedTextField(filter, { filter = it }, singleLine = true,
                            label = { Text(stringResource(R.string.notes_name_or_address)) }, modifier = Modifier.fillMaxWidth().testTag("share-filter"))
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(stringResource(R.string.notes_level_label), style = MaterialTheme.typography.labelLarge)
                            LevelMenu(level, d.levels) { level = it }
                        }
                        val f = filter.trim().lowercase(Locale.ROOT)
                        d.candidates.filter { f.isEmpty() || it.name.lowercase().contains(f) || it.email.orEmpty().lowercase().contains(f) }.take(8)
                            .forEach { c ->
                                Text(c.email?.let { stringResource(R.string.notes_name_email, c.name, it) } ?: c.name, Modifier.fillMaxWidth().heightIn(min = 48.dp)
                                    .clickable { adding = false; filter = ""; change { repo.api.share(kind, id, c.id.toString(), level) } }
                                    .padding(vertical = 14.dp))
                            }
                        d.groupCandidates.filter { f.isEmpty() || it.lowercase().contains(f) }.take(4).forEach { g ->
                            Text(stringResource(R.string.notes_name_group, g), Modifier.fillMaxWidth().heightIn(min = 48.dp)
                                .clickable { adding = false; filter = ""; change { repo.api.share(kind, id, "group:$g", level) } }
                                .padding(vertical = 14.dp))
                        }
                    }
                }
                if (d.canLeave) {
                    HorizontalDivider(Modifier.padding(vertical = 8.dp))
                    TextButton(onClick = {
                        onDismiss()
                        // ACT note.leave @android.notes @android.notesapp, ACT table.leave @android.notes @android.notesapp
                        vm.run {
                            repo.leave(id, kind == "table")
                            vm.home()
                            vm.say(AppText.get(R.string.notes_removed_from_your_notes), { repo.rejoin(id, kind == "table") })
                        }
                    }) { Text(stringResource(R.string.notes_remove_from_my_notes)) }
                }
            }
        })
}

@Composable
private fun PersonLine(icon: androidx.compose.ui.graphics.vector.ImageVector, name: String, level: String) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)) {
        Icon(icon, null)
        Spacer(Modifier.width(12.dp))
        Text(name, Modifier.weight(1f))
        Text(level, style = MaterialTheme.typography.labelLarge)
    }
}

@Composable
private fun SharedLine(p: SharePerson, d: ShareDialog, onLevel: (String) -> Unit, onRemove: () -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)) {
        Icon(if (p.kind == "group") Icons.Outlined.Group else Icons.Outlined.Person, null)
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(p.name.let { if (p.you) stringResource(R.string.notes_name_you, it) else it }.let { if (p.kind == "group") stringResource(R.string.notes_name_group, it) else it },
                textDecoration = if (p.disabled) TextDecoration.LineThrough else null)
            if (p.outsidePolicy) Text(stringResource(R.string.notes_outside_sharing_rule), style = MaterialTheme.typography.bodySmall)
        }
        if (d.canShare && !p.you) {
            LevelMenu(p.level, d.levels, onLevel)
            IconButton(onClick = onRemove) { Icon(Icons.Outlined.Delete, stringResource(R.string.notes_stop_sharing_with, p.name)) }
        } else Text(levelName(p.level), style = MaterialTheme.typography.labelLarge)
    }
}

@Composable
private fun LevelMenu(level: String, levels: List<String>, onPick: (String) -> Unit) {
    var open by remember { mutableStateOf(false) }
    Box {
        TextButton(onClick = { open = true }) { Text(levelName(level)) }
        DropdownMenu(open, { open = false }) {
            levels.forEach { lv -> DropdownMenuItem({ Text(levelName(lv)) }, { open = false; onPick(lv) }) }
        }
    }
}

/**
 * Version history (ACT note.history @android.notes @android.notesapp): revisions newest first, each with its author or source and a
 * one-line summary; one opens read-only, and "Restore this version" saves it as a new version
 * (ACT note.restore_version @android.notes @android.notesapp), with the undo.
 */
@Composable
fun HistorySheet(vm: NotesViewModel, noteId: String, onDismiss: () -> Unit) {
    val repo = vm.repo
    val scope = rememberCoroutineScope()
    var revisions by remember { mutableStateOf<List<Revision>?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var shown by remember { mutableStateOf<Revision?>(null) }
    LaunchedEffect(noteId) {
        try { revisions = repo.api.revisions(noteId) } catch (e: CancellationException) { throw e } catch (e: Exception) { error = e.notesMessage() }
    }
    val one = shown
    AlertDialog(onDismissRequest = onDismiss,
        title = { Text(if (one == null) stringResource(R.string.notes_version_history) else whenLabel(one.at)) },
        confirmButton = {
            if (one != null) TextButton(onClick = {
                onDismiss()
                vm.run {
                    val u = repo.restoreVersion(noteId, one.rev)
                    vm.say(AppText.get(R.string.notes_version_restored), u?.let { undo -> { repo.sendUndo(noteId, undo) } })
                }
            }, modifier = Modifier.testTag("restore-version")) { Text(stringResource(R.string.notes_restore_this_version)) }
            else TextButton(onClick = onDismiss) { Text(stringResource(R.string.notes_close)) }
        },
        dismissButton = { if (one != null) TextButton(onClick = { shown = null }) { Text(stringResource(R.string.notes_back)) } },
        text = {
            if (one != null) RevisionContent(one)
            else Column(Modifier.verticalScroll(rememberScrollState())) {
                error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                val list = revisions
                if (list == null && error == null) Text(stringResource(R.string.notes_loading))
                if (list != null && list.isEmpty()) Text(stringResource(R.string.notes_no_earlier_versions_yet))
                var day: String? = null
                list?.forEach { r ->
                    val d = dayLabel(r.at)
                    if (d != day) {
                        Text(d, style = MaterialTheme.typography.labelLarge, modifier = Modifier.padding(top = 8.dp).semantics { heading() })
                        day = d
                    }
                    Column(Modifier.fillMaxWidth().heightIn(min = 48.dp).clickable {
                        scope.launch {
                            try { shown = repo.api.revision(noteId, r.rev) } catch (e: CancellationException) { throw e } catch (e: Exception) { error = e.notesMessage() }
                        }
                    }.padding(vertical = 8.dp).testTag("revision-${r.rev}")) {
                        Text("${who(r)} · ${timeLabel(r.at)}", fontWeight = FontWeight.Medium)
                        r.summary?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
                    }
                }
            }
        })
}

private fun who(r: Revision): String = when (r.source) {
    "assistant" -> io.github.murtazanasir.mailr.i18n.AssistantName.now()
    "restore" -> AppText.get(R.string.notes_source_restore)
    "import" -> AppText.get(R.string.notes_source_import)
    "convert" -> r.by?.name ?: AppText.get(R.string.notes_source_convert)
    "agent" -> r.by?.name ?: AppText.get(R.string.notes_source_agent)
    else -> r.by?.name ?: AppText.get(R.string.notes_source_you)
}

private fun parse(at: String?) = at?.let { catching { OffsetDateTime.parse(it).atZoneSameInstant(ZoneId.systemDefault()) }.getOrNull() }
private fun dayLabel(at: String?) = parse(at)?.format(LocaleFormats.date("EEEdMMM", "EEE d MMM")) ?: ""
private fun timeLabel(at: String?) = parse(at)?.format(LocaleFormats.date("hmma", "h:mm a")) ?: ""
private fun whenLabel(at: String?) = parse(at)?.format(LocaleFormats.date("dMMMhmma", "d MMM, h:mm a")) ?: AppText.get(R.string.notes_version)

@Composable
private fun RevisionContent(r: Revision) {
    val c = r.content ?: JsonObject(emptyMap())
    Column(Modifier.verticalScroll(rememberScrollState())) {
        (c["title"] as? JsonPrimitive)?.contentOrNull?.takeIf { it.isNotBlank() }?.let {
            Text(it, style = MaterialTheme.typography.titleMedium)
        }
        (c["body"] as? JsonPrimitive)?.contentOrNull?.takeIf { it.isNotBlank() }?.let { Text(it) }
        val secs = (c["sections"] as? JsonArray)?.associate { s ->
            s.jsonObject["id"]?.jsonPrimitive?.contentOrNull to s.jsonObject["name"]?.jsonPrimitive?.contentOrNull
        }.orEmpty()
        (c["items"] as? JsonArray)?.forEach { e ->
            val o = e.jsonObject
            val checked = (o["checked"] as? JsonPrimitive)?.booleanOrNull == true
            val sec = (o["section_id"] as? JsonPrimitive)?.contentOrNull?.let { secs[it] }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Checkbox(checked, null, enabled = false)
                Text(((o["text"] as? JsonPrimitive)?.contentOrNull ?: "") + (sec?.let { "  ($it)" } ?: ""),
                    textDecoration = if (checked) TextDecoration.LineThrough else null)
            }
        }
    }
}

/**
 * "Edit labels" (ACT notelabel.rename @android.notes @android.notesapp, ACT notelabel.color @android.notes @android.notesapp, ACT notelabel.reorder @android.notes @android.notesapp, ACT notelabel.delete @android.notes @android.notesapp,
 * ACT notelabel.create @android.notes @android.notesapp): this user's own labels for notes and tables.
 */
@Composable
fun LabelEditor(vm: NotesViewModel) {
    val repo = vm.repo
    val labels by repo.labels.collectAsStateWithLifecycle(emptyList())
    var create by remember { mutableStateOf(false) }
    var rename by remember { mutableStateOf<String?>(null) }
    var colour by remember { mutableStateOf<String?>(null) }
    Scaffold(topBar = { TopAppBar(title = { Text(stringResource(R.string.notes_edit_labels)) }, navigationIcon = { BackButton(vm) }) },
        floatingActionButton = {
            ExtendedFloatingActionButton(onClick = { create = true }, icon = { Icon(Icons.Filled.Add, null) }, text = { Text(stringResource(R.string.notes_new_label)) })
        }) { pad ->
        if (labels.isEmpty()) Box(Modifier.padding(pad).fillMaxSize(), contentAlignment = Alignment.Center) {
            Text(stringResource(R.string.notes_no_labels_yet), color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        LazyColumn(Modifier.padding(pad).fillMaxSize()) {
            items(labels, key = { it.name }) { l ->
                var menu by remember { mutableStateOf(false) }
                val i = labels.indexOf(l)
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp)
                    .padding(start = 16.dp).testTag("label-" + l.name)) {
                    Box(Modifier.size(24.dp).clip(CircleShape).then(Modifier), contentAlignment = Alignment.Center) {
                        Icon(Icons.AutoMirrored.Outlined.Label, null, tint = if (l.color != null) NoteColors.background(l.color) else MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    Spacer(Modifier.width(16.dp))
                    Column(Modifier.weight(1f)) {
                        Text(l.name)
                        Text(stringResource(R.string.notes_label_line, pluralStringResource(R.plurals.notes_notes_count, l.count, l.count), NoteColors.name(l.color)), style = MaterialTheme.typography.bodySmall)
                    }
                    Box {
                        IconButton(onClick = { menu = true }) { Icon(Icons.Outlined.MoreVert, stringResource(R.string.notes_item_options, l.name)) }
                        DropdownMenu(menu, { menu = false }) {
                            DropdownMenuItem({ Text(stringResource(R.string.notes_rename)) }, { menu = false; rename = l.name })
                            DropdownMenuItem({ Text(stringResource(R.string.notes_colour)) }, { menu = false; colour = l.name })
                            if (i > 0) DropdownMenuItem({ Text(stringResource(R.string.notes_move_up)) }, { menu = false; vm.act { repo.editLabel(l.name, before = labels[i - 1].name) } })
                            if (i < labels.size - 1) DropdownMenuItem({ Text(stringResource(R.string.notes_move_down)) }, { menu = false; vm.act { repo.editLabel(l.name, after = labels[i + 1].name) } })
                            DropdownMenuItem({ Text(stringResource(R.string.notes_delete)) }, { menu = false; vm.act { repo.deleteLabel(l.name) } })
                        }
                    }
                }
            }
        }
    }
    if (create) TextDialog(stringResource(R.string.notes_new_label), "", stringResource(R.string.notes_create), onDismiss = { create = false }) { v ->
        create = false
        if (v.isNotBlank()) vm.act { repo.createLabel(v) }
    }
    rename?.let { name ->
        TextDialog(stringResource(R.string.notes_rename_label), name, stringResource(R.string.notes_rename), onDismiss = { rename = null }) { v ->
            rename = null
            if (v.isNotBlank() && v != name) vm.act { repo.editLabel(name, newName = v) }
        }
    }
    colour?.let { name ->
        ColorDialog(labels.firstOrNull { it.name == name }?.color, onPick = { c ->
            colour = null
            vm.act { repo.editLabel(name, color = c ?: "") }
        }, onDismiss = { colour = null })
    }
}


/**
 * Reminders on a note (ACT note.remind @android.notes @android.notesapp): the viewer's own, absolute times. The when presets:
 * Later today, Tomorrow, Next week; × removes one. Each is the key `n|<id>|abs|<instant>` on the shared
 * reminder contract, so it rings on the web and on the phone, and handled anywhere is gone everywhere.
 */
@Composable
fun RemindDialog(vm: NotesViewModel, note: io.github.murtazanasir.mailr.notes.data.Note, onDismiss: () -> Unit) {
    val zone = ZoneId.systemDefault()
    val now = java.time.Instant.ofEpochMilli(vm.now()).atZone(zone)
    val today6 = now.withHour(18).withMinute(0).withSecond(0).withNano(0)
    val later = if (now.isBefore(today6.minusHours(1))) today6 else now.plusHours(3).withMinute(0).withSecond(0).withNano(0)
    val tomorrow = now.plusDays(1).withHour(9).withMinute(0).withSecond(0).withNano(0)
    val nextWeek = now.with(java.time.temporal.TemporalAdjusters.next(java.time.DayOfWeek.MONDAY)).withHour(9).withMinute(0)
        .withSecond(0).withNano(0)
    val current = note.reminders.mapNotNull { r -> catching { OffsetDateTime.parse(r.at).toInstant() }.getOrNull() }
    fun set(list: List<java.time.Instant>) {
        onDismiss()
        vm.act { vm.repo.setReminders(note.id, list) }
    }
    AlertDialog(onDismissRequest = onDismiss, title = { Text(stringResource(R.string.notes_remind_me)) },
        confirmButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.notes_close)) } },
        text = {
            Column(Modifier.testTag("remind-sheet")) {
                listOf(stringResource(R.string.notes_later_today) to later, stringResource(R.string.notes_tomorrow) to tomorrow, stringResource(R.string.notes_next_week) to nextWeek).forEach { (label, at) ->
                    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)
                        .clickable { set(current + at.toInstant()) }) {
                        Text(label, Modifier.weight(1f))
                        Text(at.format(LocaleFormats.date("EEEhmma", "EEE h:mm a")), style = MaterialTheme.typography.bodySmall)
                    }
                }
                if (current.isNotEmpty()) {
                    HorizontalDivider(Modifier.padding(vertical = 8.dp))
                    current.forEach { at ->
                        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)) {
                            Text(reminderLabel(at.toString()), Modifier.weight(1f))
                            IconButton(onClick = { set(current - at) }) {
                                Icon(Icons.Outlined.Delete, stringResource(R.string.notes_remove_reminder_of, reminderLabel(at.toString())))
                            }
                        }
                    }
                }
            }
        })
}

/** "Tue 7 Oct, 9:00 AM" for a reminder time. */
fun reminderLabel(at: String): String = catching {
    val t = catching { OffsetDateTime.parse(at).toInstant() }.getOrElse { java.time.Instant.parse(at) }
    t.atZone(ZoneId.systemDefault()).format(LocaleFormats.date("EEEdMMMhmma", "EEE d MMM, h:mm a"))
}.getOrDefault(at)

/** "New from template" (ACT note.from_template @android.notes @android.notesapp): the starters and the user's own, online. */
@Composable
fun TemplatesDialog(vm: NotesViewModel, onDismiss: () -> Unit) {
    var list by remember { mutableStateOf<List<io.github.murtazanasir.mailr.notes.data.Template>?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(Unit) {
        try { list = vm.repo.templates() } catch (e: CancellationException) { throw e } catch (e: Exception) { error = e.notesMessage() }
    }
    AlertDialog(onDismissRequest = onDismiss, title = { Text(stringResource(R.string.notes_new_from_template)) },
        confirmButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.notes_cancel)) } },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()).testTag("templates")) {
                error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                if (list == null && error == null) Text(stringResource(R.string.notes_loading))
                list?.forEach { t ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f).heightIn(min = 48.dp).clickable {
                        onDismiss()
                        vm.run {
                            val id = vm.repo.fromTemplate(t.id, (vm.view.value as? NotesView.Label)?.let { listOf(it.name) }.orEmpty())
                                ?: return@run
                            vm.open(if (id.startsWith("t")) NotesPage.OpenTable(id) else NotesPage.OpenNote(id))
                        }
                    }.padding(vertical = 10.dp)) {
                        Text(t.name.ifBlank { stringResource(R.string.notes_untitled) })
                        Text(listOfNotNull(when (t.kind) { "checklist" -> stringResource(R.string.notes_kind_list); "table" -> stringResource(R.string.notes_kind_table); else -> stringResource(R.string.notes_kind_note) },
                            if (t.builtin) stringResource(R.string.notes_template_starter) else null, t.summary).joinToString(" · "),
                            style = MaterialTheme.typography.bodySmall)
                    }
                    // ACT note.delete_template @android.notes @android.notesapp: the user's own templates; the starters stay.
                    if (!t.builtin) IconButton(onClick = {
                        vm.run {
                            vm.repo.deleteTemplate(t.id)
                            list = list?.filter { it.id != t.id }
                            vm.say(AppText.get(R.string.notes_template_deleted))
                        }
                    }) { Icon(Icons.Outlined.Delete, stringResource(R.string.notes_delete_template, t.name)) }
                    }
                }
            }
        })
}
