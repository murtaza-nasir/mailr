package io.github.murtazanasir.mailr.ui.memory

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.ArrowDropDown
import androidx.compose.material.icons.outlined.Clear
import androidx.compose.material.icons.outlined.Event
import io.github.murtazanasir.mailr.look.Button
import io.github.murtazanasir.mailr.look.Checkbox
import io.github.murtazanasir.mailr.look.DropdownMenu
import io.github.murtazanasir.mailr.look.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.ModalBottomSheet
import io.github.murtazanasir.mailr.look.OutlinedTextField
import io.github.murtazanasir.mailr.look.RadioButton
import io.github.murtazanasir.mailr.look.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import io.github.murtazanasir.mailr.look.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.api.KnownEntity
import io.github.murtazanasir.mailr.data.api.Memory
import io.github.murtazanasir.mailr.data.api.MemoryEdit
import io.github.murtazanasir.mailr.data.api.MemorySubject
import io.github.murtazanasir.mailr.i18n.LocaleFormats
import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.ui.calendar.DateDialog
import io.github.murtazanasir.mailr.ui.util.Format
import java.time.LocalDate
import java.time.ZoneId

/** What the sheet edits: an existing memory, or a new one; [fixed] pins the subject (a person's card). */
data class MemorySheetRequest(val memory: Memory? = null, val fixed: MemorySubject? = null, val fixedName: String? = null)

@Composable
internal fun entityKindLabel(kind: String): String = stringResource(when (kind) {
    "organization" -> R.string.memory_entity_organization
    "project" -> R.string.memory_entity_project
    "topic" -> R.string.memory_entity_topic
    "place" -> R.string.memory_entity_place
    else -> R.string.memory_entity_person
})

/**
 * Add memory or Edit memory (the web's MemoryDialog): the text, the kind with each kind's example,
 * who it is about (you, a person or topic Ambrose knows, or a new one), Forget after and Pin.
 * ACT memory.add @android.settings
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MemoryEditSheet(
    req: MemorySheetRequest,
    entities: List<KnownEntity>,
    assistantName: String,
    onDismiss: () -> Unit,
    onSave: (Long?, MemoryEdit) -> Unit,
) {
    val m = req.memory
    var text by rememberSaveable { mutableStateOf(m?.text.orEmpty()) }
    var kind by rememberSaveable { mutableStateOf(m?.kind ?: MemoryKind.FACT.id) }
    // null: you; a number: a known entry; -1: a new person or topic.
    var subject by remember { mutableStateOf<Long?>(if (m == null || m.aboutYou) null else m.entityId) }
    var newName by rememberSaveable { mutableStateOf("") }
    var newKind by rememberSaveable { mutableStateOf("person") }
    var pinned by rememberSaveable { mutableStateOf(m?.pinned ?: false) }
    var expires by remember { mutableStateOf(Format.parse(m?.expiresAt)?.toLocalDate()) }
    var picking by remember { mutableStateOf(false) }
    val state = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val canSave = text.isNotBlank() && (subject != -1L || newName.isNotBlank())
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = state) {
        Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).imePadding().navigationBarsPadding()
            .padding(horizontal = 20.dp).padding(bottom = 16.dp).testTag("memory-sheet")) {
            Text(stringResource(if (m == null) R.string.memory_add else R.string.memory_edit), style = MaterialTheme.typography.titleLarge)
            Spacer(Modifier.size(12.dp))
            OutlinedTextField(text, { if (it.length <= 1000) text = it }, label = { Text(stringResource(R.string.memory_text_label, assistantName)) },
                placeholder = { Text(stringResource(R.string.memory_text_placeholder)) }, minLines = 3,
                modifier = Modifier.fillMaxWidth().testTag("memory-text"))
            Spacer(Modifier.size(12.dp))
            Text(stringResource(R.string.memory_kind), style = MaterialTheme.typography.titleSmall)
            Column(Modifier.selectableGroup()) {
                MemoryKind.entries.forEach { k ->
                    Row(Modifier.fillMaxWidth().selectable(kind == k.id, role = Role.RadioButton) { kind = k.id }
                        .padding(vertical = 4.dp).testTag("pick-${k.id}"), verticalAlignment = Alignment.CenterVertically) {
                        RadioButton(kind == k.id, onClick = null)
                        Spacer(Modifier.width(12.dp))
                        Column {
                            Text(stringResource(k.label), style = MaterialTheme.typography.bodyLarge)
                            Text(stringResource(R.string.memory_kind_example, stringResource(k.example)), style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }
            Spacer(Modifier.size(12.dp))
            SubjectField(req, entities, subject) { subject = it }
            if (req.fixed == null && subject == -1L) {
                Spacer(Modifier.size(8.dp))
                OutlinedTextField(newName, { newName = it }, label = { Text(stringResource(R.string.memory_new_name)) }, singleLine = true,
                    modifier = Modifier.fillMaxWidth().testTag("memory-new-name"))
                Spacer(Modifier.size(8.dp))
                SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
                    listOf("person" to R.string.memory_new_person, "topic" to R.string.memory_new_topic).forEachIndexed { i, (id, label) ->
                        SegmentedButton(newKind == id, { newKind = id }, SegmentedButtonDefaults.itemShape(i, 2)) { Text(stringResource(label)) }
                    }
                }
            }
            Spacer(Modifier.size(12.dp))
            Text(stringResource(R.string.memory_forget_after), style = MaterialTheme.typography.titleSmall)
            Row(Modifier.fillMaxWidth().clickable(onClickLabel = stringResource(R.string.memory_pick_date)) { picking = true }.padding(vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Outlined.Event, null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
                Spacer(Modifier.width(12.dp))
                Text(expires?.format(LocaleFormats.date("yMMMd", "MMM d, yyyy")) ?: stringResource(R.string.memory_forget_none),
                    modifier = Modifier.weight(1f).testTag("memory-expires"))
                if (expires != null) IconButton(onClick = { expires = null }) { Icon(Icons.Outlined.Clear, stringResource(R.string.memory_forget_clear)) }
            }
            Text(stringResource(R.string.memory_forget_hint), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.size(8.dp))
            Row(Modifier.fillMaxWidth().clickable { pinned = !pinned }.padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                Checkbox(pinned, onCheckedChange = null)
                Spacer(Modifier.width(12.dp))
                Column {
                    Text(stringResource(R.string.memory_pin), style = MaterialTheme.typography.bodyLarge)
                    Text(stringResource(R.string.memory_pin_desc, assistantName), style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            Spacer(Modifier.size(12.dp))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                TextButton(onClick = onDismiss) { Text(stringResource(R.string.memory_cancel)) }
                Spacer(Modifier.width(8.dp))
                Button(enabled = canSave, modifier = Modifier.testTag("memory-save"), onClick = {
                    val subj = req.fixed ?: when (val s = subject) {
                        null -> MemorySubject.You
                        -1L -> MemorySubject.New(newName.trim(), newKind)
                        else -> MemorySubject.Known(s)
                    }
                    val at = expires?.atTime(23, 59)?.atZone(ZoneId.systemDefault())?.toInstant()?.toString()
                    onSave(m?.id, MemoryEdit(text.trim(), kind, subj, pinned, at))
                }) { Text(stringResource(R.string.memory_save)) }
            }
        }
    }
    if (picking) DateDialog(expires ?: AppClock.today().plusDays(7), onDismiss = { picking = false }) { d: LocalDate ->
        picking = false
        expires = d
    }
}

/** About: you, a person or topic Ambrose knows, or New person or topic…; fixed on a person's card. */
@Composable
private fun SubjectField(req: MemorySheetRequest, entities: List<KnownEntity>, subject: Long?, onPick: (Long?) -> Unit) {
    var open by remember { mutableStateOf(false) }
    val you = stringResource(R.string.memory_you)
    val newOne = stringResource(R.string.memory_new_subject)
    val others = entities.filter { !it.isSelf }
    val current = req.memory?.entity
    val label: String = when {
        req.fixed != null -> req.fixedName.orEmpty()
        subject == null -> you
        subject == -1L -> newOne
        else -> others.firstOrNull { it.id == subject }?.name ?: current?.takeIf { it.id == subject }?.name.orEmpty()
    }
    Text(stringResource(R.string.memory_about), style = MaterialTheme.typography.titleSmall)
    Box {
        Row(Modifier.fillMaxWidth().clickable(enabled = req.fixed == null) { open = true }.padding(vertical = 8.dp).testTag("memory-subject"),
            verticalAlignment = Alignment.CenterVertically) {
            Text(label, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f))
            if (req.fixed == null) Icon(Icons.Outlined.ArrowDropDown, null)
        }
        DropdownMenu(open, { open = false }) {
            DropdownMenuItem({ Text(you) }, { open = false; onPick(null) })
            others.forEach { e ->
                DropdownMenuItem({ Text(stringResource(R.string.memory_subject_option, e.name, entityKindLabel(e.kind))) }, { open = false; onPick(e.id) })
            }
            DropdownMenuItem({ Text(newOne) }, { open = false; onPick(-1L) })
        }
    }
}
