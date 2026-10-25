package io.github.murtazanasir.mailr.ui.assistant

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.ExpandLess
import androidx.compose.material.icons.outlined.ExpandMore
import androidx.compose.material.icons.outlined.Psychology
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.OutlinedTextField
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.api.MemoryNote
import io.github.murtazanasir.mailr.data.api.MemoryNotes
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch

/** What the "Used memories" line can do: read the notes by id, change one's text, delete one. */
data class MemoryActions(
    val load: suspend (List<Long>) -> MemoryNotes,
    val save: suspend (Long, String) -> Unit,
    val delete: suspend (Long) -> Unit,
)

/**
 * "Used memories · 2" under an answer: the notes that went into the prompt.
 * It opens to the notes; a tap edits one in place, and each can be deleted.
 * ACT memory.used @android.assistant
 */
@Composable
fun UsedMemories(ids: List<Long>, actions: MemoryActions) {
    var open by rememberSaveable { mutableStateOf(false) }
    var notes by remember(ids) { mutableStateOf<MemoryNotes?>(null) }
    var editing by remember { mutableStateOf<MemoryNote?>(null) }
    var reload by remember { mutableIntStateOf(0) }
    var error by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    LaunchedEffect(open, ids, reload) {
        if (!open) return@LaunchedEffect
        notes = try {
            actions.load(ids)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            MemoryNotes()
        }
    }
    Column(Modifier.fillMaxWidth().testTag("used-memories")) {
        TextButton(onClick = { open = !open }, contentPadding = PaddingValues(horizontal = 4.dp, vertical = 0.dp)) {
            Icon(Icons.Outlined.Psychology, null, Modifier.size(16.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.width(6.dp))
            Text(pluralStringResource(R.plurals.asst_used_memories, ids.size, ids.size), style = MaterialTheme.typography.labelLarge,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Icon(if (open) Icons.Outlined.ExpandLess else Icons.Outlined.ExpandMore, null, Modifier.size(16.dp),
                tint = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        if (open) {
            val n = notes
            if (n == null) {
                // The notes' room is kept while they load, so the reply does not jump.
                Box(Modifier.fillMaxWidth().height((ids.size * 44).dp))
            } else {
                Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    n.items.forEach { m ->
                        if (editing?.id == m.id) {
                            // Edited in place: the note's text, Save and Cancel.
                            var text by remember(m.id) { mutableStateOf(m.text) }
                            Column(Modifier.fillMaxWidth().padding(start = 4.dp, top = 4.dp)) {
                                OutlinedTextField(text, { text = it }, label = { Text(stringResource(R.string.asst_memory_text)) },
                                    modifier = Modifier.fillMaxWidth().testTag("memory-text"))
                                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                                    TextButton(onClick = { editing = null }) { Text(stringResource(R.string.ui_cancel)) }
                                    TextButton(enabled = text.isNotBlank(), onClick = {
                                        scope.launch {
                                            try {
                                                actions.save(m.id, text.trim())
                                                editing = null
                                                reload++
                                            } catch (e: CancellationException) {
                                                throw e
                                            } catch (e: Exception) {
                                                error = true
                                                editing = null
                                            }
                                        }
                                    }) { Text(stringResource(R.string.asst_memory_save)) }
                                }
                            }
                            return@forEach
                        }
                        Row(Modifier.fillMaxWidth().clickable(onClickLabel = stringResource(R.string.asst_memory_edit)) { editing = m }
                            .padding(start = 4.dp).testTag("used-memory"),
                            verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f).padding(vertical = 6.dp)) {
                                Text(m.text, style = MaterialTheme.typography.bodyMedium)
                                m.entity?.let {
                                    Text(if (it.isSelf) stringResource(R.string.asst_memory_you) else it.name,
                                        style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                }
                            }
                            IconButton(onClick = {
                                scope.launch {
                                    try {
                                        actions.delete(m.id)
                                        reload++
                                    } catch (e: CancellationException) {
                                        throw e
                                    } catch (e: Exception) {
                                        error = true
                                    }
                                }
                            }) { Icon(Icons.Outlined.Delete, stringResource(R.string.asst_memory_delete), Modifier.size(18.dp)) }
                        }
                    }
                    if (n.missing > 0) {
                        Text(pluralStringResource(R.plurals.asst_memories_deleted, n.missing, n.missing),
                            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(start = 4.dp, top = 4.dp))
                    }
                    if (error) {
                        Text(stringResource(R.string.asst_memory_not_saved), style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(start = 4.dp))
                    }
                }
            }
        }
    }
}
