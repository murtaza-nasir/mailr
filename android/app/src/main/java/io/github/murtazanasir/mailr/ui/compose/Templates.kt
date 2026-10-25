package io.github.murtazanasir.mailr.ui.compose

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import io.github.murtazanasir.mailr.look.ListItem
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.ModalBottomSheet
import io.github.murtazanasir.mailr.look.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.MailTemplate
import io.github.murtazanasir.mailr.ui.util.Format
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText

/** A template's text for the plain-text editor: its HTML as lines, trimmed. */
fun templateText(html: String): String = Format.htmlToText(html).replace(Regex("\n{3,}"), "\n\n").trim()

/** Put [text] into [body] at [offset] (the cursor), on lines of its own. */
fun insertAt(body: String, offset: Int?, text: String): String {
    val at = (offset ?: body.length).coerceIn(0, body.length)
    val before = body.substring(0, at)
    val after = body.substring(at)
    val lead = if (before.isEmpty() || before.endsWith("\n")) "" else "\n"
    val tail = if (after.isEmpty() || after.startsWith("\n")) "" else "\n"
    return before + lead + text + tail + after
}

/**
 * ACT template.insert @android.editor: the user's templates (Settings → Mail → Writing → Templates on the web),
 * with a filter; a tap fills in the variables for the first recipient and puts the text at the cursor.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TemplatesSheet(load: suspend () -> List<MailTemplate>, onDismiss: () -> Unit, onPick: (MailTemplate) -> Unit) {
    var all by remember { mutableStateOf<List<MailTemplate>?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var q by remember { mutableStateOf("") }
    LaunchedEffect(Unit) {
        try {
            all = load()
        } catch (e: kotlinx.coroutines.CancellationException) {
            throw e
        } catch (e: Exception) {
            error = AppText.get(R.string.compose_templates_failed)
            all = emptyList()
        }
    }
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
        Column(Modifier.navigationBarsPadding().testTag("templates-sheet")) {
            Text(stringResource(R.string.compose_insert_template_title), style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(horizontal = 24.dp).semantics { heading() })
            OutlinedTextField(q, { q = it }, singleLine = true, label = { Text(stringResource(R.string.compose_search_templates)) },
                modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp))
            val rows = all?.filter { q.isBlank() || q.lowercase() in it.name.lowercase() || q.lowercase() in it.shortcut.lowercase() }
            LazyColumn(Modifier.heightIn(min = 120.dp, max = 420.dp)) {
                when {
                    rows == null -> item { Text(stringResource(R.string.compose_loading), Modifier.padding(24.dp), color = MaterialTheme.colorScheme.onSurfaceVariant) }
                    rows.isEmpty() -> item {
                        Text(error ?: if (q.isBlank()) stringResource(R.string.compose_no_templates) else stringResource(R.string.compose_no_templates_match),
                            Modifier.padding(24.dp), color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    else -> items(rows, key = { it.id }) { t ->
                        ListItem(headlineContent = { Text(t.name) },
                            supportingContent = { Text(templateText(t.bodyHtml), maxLines = 2, overflow = TextOverflow.Ellipsis) },
                            leadingContent = { Icon(Icons.Outlined.Description, null) },
                            modifier = Modifier.clickable { onPick(t) })
                    }
                }
            }
        }
    }
}
