package io.github.murtazanasir.mailr.ui.present

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Archive
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.CalendarMonth
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.material.icons.outlined.FilterAlt
import androidx.compose.material.icons.outlined.GraphicEq
import androidx.compose.material.icons.automirrored.outlined.Label
import androidx.compose.material.icons.outlined.MailOutline
import androidx.compose.material.icons.outlined.People
import androidx.compose.material.icons.outlined.Psychology
import androidx.compose.material.icons.outlined.Report
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material.icons.automirrored.outlined.Send
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material.icons.automirrored.outlined.StickyNote2
import androidx.compose.material.icons.outlined.TaskAlt
import androidx.compose.material.icons.outlined.Unsubscribe
import androidx.compose.material.icons.outlined.VerifiedUser
import androidx.compose.material.icons.automirrored.outlined.Rule
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.Label
import io.github.murtazanasir.mailr.ui.components.LabelChip
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText

/*
 * The body of an approval card, drawn from the server's display model (PresentModels.kt): a summary
 * sentence, then one row per field with a typed value (label chips in their colours, people, search
 * conditions, lists, quotes, before and after). The Approvals screen, the assistant's chat and the
 * notification use the same model; JUnit runs this renderer on spec/fixtures/agent-blocks-fixtures.json.
 * The decision itself is the card's (ApprovalCard in ui/more/Screens.kt): approval.approve and approval.deny.
 */

/** The card's header icon for the display's icon name. */
fun displayIcon(name: String?): ImageVector = when (name) {
    "filter" -> Icons.Outlined.FilterAlt
    "order" -> Icons.AutoMirrored.Outlined.Rule
    "trash", "delete" -> Icons.Outlined.Delete
    "spam" -> Icons.Outlined.Report
    "archive" -> Icons.Outlined.Archive
    "label" -> Icons.AutoMirrored.Outlined.Label
    "snooze" -> Icons.Outlined.Schedule
    "mail" -> Icons.Outlined.MailOutline
    "send" -> Icons.AutoMirrored.Outlined.Send
    "event" -> Icons.Outlined.CalendarMonth
    "task" -> Icons.Outlined.TaskAlt
    "memory" -> Icons.Outlined.Psychology
    "skill" -> Icons.Outlined.AutoAwesome
    "settings" -> Icons.Outlined.Settings
    "people" -> Icons.Outlined.People
    "recording" -> Icons.Outlined.GraphicEq
    "unsubscribe" -> Icons.Outlined.Unsubscribe
    "draft" -> Icons.Outlined.Edit
    "note" -> Icons.AutoMirrored.Outlined.StickyNote2
    else -> Icons.Outlined.VerifiedUser
}

@Composable
fun ApprovalBody(d: Display, modifier: Modifier = Modifier) {
    Column(modifier.fillMaxWidth()) {
        d.summary?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
        d.reason?.let {
            Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 2.dp))
        }
        if (d.fields.isNotEmpty()) {
            Surface(shape = RoundedCornerShape(12.dp), color = MaterialTheme.colorScheme.surfaceContainerHigh,
                modifier = Modifier.fillMaxWidth().padding(top = if (d.summary != null || d.reason != null) 8.dp else 0.dp)) {
                Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    d.fields.forEach { FieldRow(it) }
                }
            }
        }
    }
}

@Composable
private fun FieldRow(f: DisplayField) {
    if (f.label.isBlank()) {
        ValueView(f.value)
        return
    }
    // Narrow screens: the label above its value. The label column is too narrow for queries.
    Column(Modifier.fillMaxWidth()) {
        Text(f.label, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        ValueView(f.value)
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ValueView(v: DisplayValue) {
    val body = MaterialTheme.typography.bodyMedium
    val muted = MaterialTheme.colorScheme.onSurfaceVariant
    when (v.t) {
        "labels" -> FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(4.dp),
            modifier = Modifier.padding(top = 2.dp)) {
            v.labels.forEach { LabelChip(Label(id = 0, name = it.name, color = it.color)) }
        }
        "people" -> Column {
            v.people.forEach { p ->
                Text(buildAnnotatedString {
                    if (p.name.isNullOrBlank()) append(p.email) else {
                        append(p.name)
                        withStyle(SpanStyle(color = muted)) { append(AppText.get(R.string.present_person_email, p.email)) }
                    }
                }, style = body)
            }
            if (v.more > 0) Text(stringResource(R.string.present_and_more, v.more), style = body, color = muted)
        }
        "query" -> QueryView(v)
        "list" -> Column {
            v.strings.forEach { Text(stringResource(R.string.present_bullet, it), style = body) }
            if (v.more > 0) Text(stringResource(R.string.present_and_more, v.more), style = body, color = muted)
        }
        "quote" -> QuoteView(v.text)
        "diff" -> Text(buildAnnotatedString {
            if (!v.before.isNullOrBlank()) {
                withStyle(SpanStyle(color = muted, textDecoration = TextDecoration.LineThrough)) { append(v.before) }
                append("  →  ")
            }
            withStyle(SpanStyle(fontWeight = FontWeight.SemiBold)) { append(v.after ?: v.text) }
        }, style = body)
        "choices" -> Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            v.choices.forEach { c ->
                Column {
                    Text(c.name, style = body.copy(fontWeight = FontWeight.SemiBold))
                    if (c.description.isNotBlank()) Text(c.description, style = MaterialTheme.typography.bodySmall, color = muted)
                    c.then.forEach { f ->
                        Row {
                            Text(f.label, style = MaterialTheme.typography.bodySmall, color = muted)
                            androidx.compose.foundation.layout.Spacer(Modifier.width(6.dp))
                            ValueView(f.value)
                        }
                    }
                }
            }
        }
        else -> Text(v.text, style = body)
    }
}

@Composable
private fun QueryView(v: DisplayValue) {
    val body = MaterialTheme.typography.bodyMedium
    if (v.conditions.isEmpty()) {
        Text(v.raw ?: v.text, style = body.copy(fontFamily = FontFamily.Monospace))
        return
    }
    var syntax by remember { mutableStateOf(false) }
    Column {
        v.conditions.forEach { c ->
            var all by remember { mutableStateOf(false) }
            val long = c.values.size > 3 && c.field !in setOf("any", "all", "label")
            Text(stringResource(R.string.present_bullet, c.text), style = body)
            if (long) {
                TextButton(onClick = { all = !all }, modifier = Modifier.padding(start = 4.dp)) {
                    Text(if (all) stringResource(R.string.present_show_fewer) else stringResource(R.string.present_show_all, c.values.size))
                }
                if (all) Text(c.values.joinToString("\n"), style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace),
                    modifier = Modifier.padding(start = 12.dp))
            }
        }
        if (!v.raw.isNullOrBlank()) {
            TextButton(onClick = { syntax = !syntax }) { Text(if (syntax) stringResource(R.string.present_hide_syntax) else stringResource(R.string.present_search_syntax)) }
            if (syntax) Text(v.raw, style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace),
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun QuoteView(text: String) {
    var open by remember { mutableStateOf(false) }
    val long = text.length > 280 || text.count { it == '\n' } > 4
    Column(Modifier.widthIn(max = 640.dp)) {
        Text(text, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = if (long && !open) 5 else Int.MAX_VALUE, overflow = TextOverflow.Ellipsis)
        if (long) TextButton(onClick = { open = !open }) { Text(if (open) stringResource(R.string.present_show_less) else stringResource(R.string.present_show_more)) }
    }
}
