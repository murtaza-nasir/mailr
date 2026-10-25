package io.github.murtazanasir.mailr.ui.mail

import io.github.murtazanasir.mailr.util.catching

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.ArrowDropDown
import io.github.murtazanasir.mailr.look.AlertDialog
import io.github.murtazanasir.mailr.look.DropdownMenu
import io.github.murtazanasir.mailr.look.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.OutlinedTextField
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.DialogProperties
import androidx.compose.foundation.layout.padding
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.api.Label
import io.github.murtazanasir.mailr.data.api.userMessage
import io.github.murtazanasir.mailr.i18n.AppText
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch

/** The label a new folder gets: [name] inside [parent] ("Parent/Name"), or [name] alone; trimmed. */
fun newFolderName(name: String, parent: String?): String {
    val n = name.trim().trim('/').trim()
    return if (parent.isNullOrBlank() || n.isEmpty()) n else "$parent/$n"
}

/** A label of that name exists already (names compare without case, as the web's dialog does). */
fun folderNameTaken(full: String, labels: List<Label>): Boolean =
    full.isNotEmpty() && labels.any { it.name.equals(full, ignoreCase = true) }

/**
 * "New folder" in an account's group (the web's CreateLabelDialog opened from an account): a name and,
 * optionally, the folder it goes inside. [create] makes it (POST /labels with the account as its home;
 * the server pins it to the group) and returns the label; the dialog stays open with the reason when
 * the server refuses. ACT mail.account_folder_create @android.drawer
 */
@Composable
fun NewFolderDialog(
    address: String,
    labels: List<Label>,
    initialParent: String?,
    onDismiss: () -> Unit,
    create: suspend (String) -> Unit,
) {
    var name by rememberSaveable { mutableStateOf("") }
    var parent by rememberSaveable { mutableStateOf(initialParent) }
    var picking by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    val focus = remember { FocusRequester() }
    val parents = remember(labels) { labels.filter { it.role == null }.sortedBy { it.name.lowercase() } }
    val full = newFolderName(name, parent)
    val taken = folderNameTaken(full, labels)
    fun submit() {
        if (full.isEmpty() || taken || busy) return
        busy = true
        error = null
        scope.launch {
            try {
                create(full)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                error = AppText.get(R.string.mail_new_folder_failed, e.userMessage())
                busy = false
            }
        }
    }
    LaunchedEffect(Unit) { catching { focus.requestFocus() } }
    AlertDialog(
        onDismissRequest = onDismiss,
        // The dialog sizes itself (280–560 dp wide, 24 dp from the screen's edges): with the platform's
        // width a text field in a dialog never lets a Robolectric test go idle.
        properties = DialogProperties(usePlatformDefaultWidth = false),
        modifier = Modifier.padding(horizontal = 24.dp).testTag("new-folder-dialog"),
        title = { Text(stringResource(R.string.mail_new_folder_in, address)) },
        text = {
            Column {
                OutlinedTextField(
                    name, { name = it.replace("\n", ""); error = null }, singleLine = true,
                    label = { Text(stringResource(R.string.mail_new_folder_name)) },
                    isError = taken || error != null,
                    supportingText = {
                        Text(when {
                            taken -> stringResource(R.string.mail_new_folder_exists)
                            error != null -> error!!
                            else -> stringResource(R.string.mail_new_folder_hint)
                        })
                    },
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
                    keyboardActions = KeyboardActions(onDone = { submit() }),
                    modifier = Modifier.fillMaxWidth().focusRequester(focus).testTag("new-folder-name"),
                )
                Spacer(Modifier.height(8.dp))
                val top = stringResource(R.string.mail_new_folder_top)
                val inside = stringResource(R.string.mail_new_folder_parent)
                Box {
                    OutlinedTextField(
                        parent ?: top, {}, readOnly = true, singleLine = true,
                        label = { Text(inside) },
                        trailingIcon = { Icon(Icons.Outlined.ArrowDropDown, null) },
                        modifier = Modifier.fillMaxWidth(),
                    )
                    // The field only shows the choice; a tap anywhere on it opens the list.
                    Box(Modifier.matchParentSize().clickable(role = Role.DropdownList, onClickLabel = inside) { picking = true }
                        .testTag("new-folder-parent"))
                    DropdownMenu(picking, { picking = false }) {
                        DropdownMenuItem({ Text(top) }, { parent = null; picking = false })
                        parents.forEach { l ->
                            DropdownMenuItem({ Text(l.name, style = MaterialTheme.typography.bodyLarge) }, { parent = l.name; picking = false })
                        }
                    }
                }
            }
        },
        confirmButton = {
            TextButton(onClick = { submit() }, enabled = full.isNotEmpty() && !taken && !busy, modifier = Modifier.testTag("new-folder-create")) {
                Text(stringResource(R.string.mail_new_folder_create))
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.mail_cancel)) } },
    )
}
