package io.github.murtazanasir.mailr.ui.tasks

import io.github.murtazanasir.mailr.i18n.AppText
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.SubdirectoryArrowRight
import androidx.compose.material.icons.outlined.Event
import androidx.compose.material.icons.outlined.Flag
import androidx.compose.material.icons.outlined.Folder
import androidx.compose.material.icons.outlined.NotificationsNone
import androidx.compose.material.icons.outlined.OpenInFull
import androidx.compose.material.icons.outlined.Repeat
import androidx.compose.material.icons.outlined.Sell
import androidx.compose.material.icons.outlined.Start
import io.github.murtazanasir.mailr.look.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import io.github.murtazanasir.mailr.look.FilterChip
import io.github.murtazanasir.mailr.look.InputChip
import androidx.compose.material3.InputChipDefaults
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.look.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.OffsetMapping
import androidx.compose.ui.text.input.TransformedText
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import io.github.murtazanasir.mailr.ui.calendar.QuickAddResult
import io.github.murtazanasir.mailr.ui.calendar.QuickToken
import io.github.murtazanasir.mailr.ui.calendar.TokenKind
import java.time.LocalDate
import java.time.ZonedDateTime

/**
 * The quick-add bar of the Tasks screen (task mode): one line through the shared
 * grammar, each parsed token highlighted, and a chip row beside it (above it when the bar is at the
 * bottom of the screen) with × on every chip (× turns
 * the token back into title text and keeps it so). Enter adds and keeps the keyboard up for the
 * next task. [prefill] is what the view adds (Today, a day section, a list, a tag); its chip has
 * a × too.
 */
@Composable
fun TaskQuickAddBar(
    lists: List<CalendarCollection>,
    prefill: QuickPrefill,
    now: () -> ZonedDateTime,
    focus: FocusRequester,
    onAdd: (text: String, keep: Set<String>) -> Unit,
    onDetails: (text: String, keep: Set<String>) -> Unit,
    onClearPrefill: () -> Unit,
    modifier: Modifier = Modifier,
    placeholder: String = stringResource(R.string.tasks_qa_placeholder),
    initialText: String = "",
    /** The title of the last task added: subtask mode makes the next lines its subtasks (the web's Tab). */
    subtaskOf: String? = null,
    subtask: Boolean = false,
    onSubtask: (Boolean) -> Unit = {},
    /** The bar sits at the bottom of the screen (the Tasks screen): chips above the line. */
    chipsAbove: Boolean = false,
) {
    var text by remember { mutableStateOf(initialText) }
    var keep by remember { mutableStateOf(setOf<String>()) }
    var pasted by remember { mutableStateOf<List<String>?>(null) }
    val parsed: QuickAddResult? = remember(text, keep, lists) { if (text.isBlank()) null else QuickTask.parse(text, now(), lists, keep) }
    val accent = tokenColors()
    val quickAddDesc = stringResource(R.string.tasks_qa_desc)

    fun send() {
        if (text.isBlank() || parsed?.title.isNullOrBlank()) return
        onAdd(text, keep)
        text = ""
        keep = emptySet()
    }

    @Composable
    fun Chips() {
        val chips = parsed?.let { chipsFor(it, now().toLocalDate()) }.orEmpty()
        val pre = prefillChip(prefill, lists, parsed, now().toLocalDate())
        AnimatedVisibility(chips.isNotEmpty() || pre != null || subtaskOf != null) {
            Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(top = if (chipsAbove) 0.dp else 6.dp, bottom = if (chipsAbove) 6.dp else 0.dp),
                horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                // ACT task.subtask_add @android.tasks: the toggle chip is the phone's form of Tab after a create.
                val subtaskDesc = if (subtask) stringResource(R.string.tasks_qa_subtask_on, subtaskOf.orEmpty()) else stringResource(R.string.tasks_qa_subtask_off)
                if (subtaskOf != null) FilterChip(selected = subtask, onClick = { onSubtask(!subtask) },
                    label = { Text(if (subtask) stringResource(R.string.tasks_qa_subtask_of, subtaskOf.take(24)) else stringResource(R.string.tasks_qa_subtask), maxLines = 1) },
                    leadingIcon = { Icon(Icons.Outlined.SubdirectoryArrowRight, null, Modifier.size(InputChipDefaults.IconSize)) },
                    modifier = Modifier.semantics { contentDescription = subtaskDesc })
                chips.forEach { (tok, label, icon) ->
                    // × (or a tap on the chip) keeps the words as title text.
                    InputChip(selected = true, onClick = { keep = QuickTask.keepAsText(keep, tok) }, label = { Text(label, maxLines = 1) },
                        leadingIcon = { Icon(icon, null, Modifier.size(InputChipDefaults.IconSize)) },
                        trailingIcon = { Icon(Icons.Outlined.Close, stringResource(R.string.tasks_qa_keep_text, tok.text), Modifier.size(InputChipDefaults.IconSize)) })
                }
                pre?.let { (label, icon) ->
                    InputChip(selected = false, onClick = onClearPrefill, label = { Text(label, maxLines = 1) },
                        leadingIcon = { Icon(icon, null, Modifier.size(InputChipDefaults.IconSize)) },
                        trailingIcon = { Icon(Icons.Outlined.Close, stringResource(R.string.tasks_qa_remove, label), Modifier.size(InputChipDefaults.IconSize)) })
                }
            }
        }
    }

    Surface(tonalElevation = 3.dp, modifier = modifier) {
        Column(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp)) {
            // At the bottom of the screen the chips sit above the line, next to the list they add to.
            if (chipsAbove) Chips()
            Row(verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(
                    text,
                    { v ->
                        val lines = v.lines().map { it.trim() }.filter { it.isNotEmpty() }
                        when {
                            // Pasting several lines: each line is its own task, after one question.
                            lines.size > 1 -> pasted = lines
                            v.endsWith("\n") -> { text = v.trimEnd('\n'); send() }
                            else -> text = v
                        }
                    },
                    placeholder = { Text(placeholder, maxLines = 1, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis) },
                    singleLine = true,
                    shape = CircleShape,
                    visualTransformation = TokenHighlight(parsed?.tokens.orEmpty(), accent),
                    keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences, imeAction = ImeAction.Send),
                    // ACT task.create @android.tasks: Enter adds; the keyboard stays up for the next one (rapid entry).
                    keyboardActions = KeyboardActions(onSend = { send() }),
                    trailingIcon = if (text.isNotBlank()) ({
                        IconButton(onClick = { onDetails(text, keep); text = ""; keep = emptySet() }) {
                            Icon(Icons.Outlined.OpenInFull, stringResource(R.string.tasks_qa_details))
                        }
                    }) else null,
                    modifier = Modifier.weight(1f).focusRequester(focus).semantics { contentDescription = quickAddDesc },
                )
                Spacer(Modifier.width(4.dp))
                IconButton(onClick = ::send, enabled = !parsed?.title.isNullOrBlank()) { Icon(Icons.AutoMirrored.Filled.Send, stringResource(R.string.tasks_qa_add)) }
            }
            if (!chipsAbove) Chips()
        }
    }

    pasted?.let { lines ->
        AlertDialog(onDismissRequest = { pasted = null },
            title = { Text(pluralStringResource(R.plurals.tasks_qa_create_many_title, lines.size, lines.size)) },
            text = { Text(lines.take(5).joinToString("\n") + if (lines.size > 5) "\n…" else "") },
            confirmButton = { TextButton(onClick = { lines.forEach { onAdd(it, emptySet()) }; pasted = null; text = "" }) { Text(pluralStringResource(R.plurals.tasks_qa_create_many, lines.size, lines.size)) } },
            dismissButton = { TextButton(onClick = { text = lines.joinToString(" "); pasted = null }) { Text(stringResource(R.string.tasks_qa_one_task)) } })
    }
}

/** The colour of each token kind, in the line and on its chip. */
@Composable
private fun tokenColors(): Map<TokenKind, Color> {
    val cs = MaterialTheme.colorScheme
    return mapOf(
        TokenKind.DATE to cs.primary, TokenKind.TIME to cs.primary, TokenKind.START to cs.primary,
        TokenKind.REPEAT to cs.secondary, TokenKind.REMINDER to cs.secondary,
        TokenKind.PRIORITY to cs.error, TokenKind.TAG to cs.tertiary, TokenKind.COLLECTION to cs.tertiary,
    )
}

/** Highlights the parsed tokens in the line (the text itself is unchanged). */
private class TokenHighlight(private val tokens: List<QuickToken>, private val colors: Map<TokenKind, Color>) : VisualTransformation {
    override fun filter(text: AnnotatedString): TransformedText {
        val out = buildAnnotatedString {
            append(text)
            tokens.forEach { t ->
                val c = colors[t.kind] ?: return@forEach
                if (t.start >= 0 && t.end <= text.length && t.start < t.end) addStyle(SpanStyle(color = c, fontWeight = FontWeight.Medium), t.start, t.end)
            }
        }
        return TransformedText(out, OffsetMapping.Identity)
    }

    override fun equals(other: Any?) = other is TokenHighlight && other.tokens == tokens && other.colors == colors
    override fun hashCode() = tokens.hashCode()
}

/** One chip per parsed token (a date and its time share one chip). */
private fun chipsFor(r: QuickAddResult, today: LocalDate): List<Triple<QuickToken, String, ImageVector>> {
    val out = mutableListOf<Triple<QuickToken, String, ImageVector>>()
    var dated = false
    for (t in r.tokens.sortedBy { it.start }) {
        val icon = when (t.kind) {
            TokenKind.DATE, TokenKind.TIME -> Icons.Outlined.Event
            TokenKind.START -> Icons.Outlined.Start
            TokenKind.REPEAT -> Icons.Outlined.Repeat
            TokenKind.REMINDER -> Icons.Outlined.NotificationsNone
            TokenKind.PRIORITY -> Icons.Outlined.Flag
            TokenKind.TAG -> Icons.Outlined.Sell
            TokenKind.COLLECTION -> Icons.Outlined.Folder
            else -> continue
        }
        if (t.kind == TokenKind.DATE || t.kind == TokenKind.TIME) {
            if (dated) continue
            dated = true
        }
        out += Triple(t, QuickTask.chipLabel(t, r, today), icon)
    }
    return out
}

/** The view's prefill as a chip, unless the line already says the same thing. */
private fun prefillChip(p: QuickPrefill, lists: List<CalendarCollection>, r: QuickAddResult?, today: LocalDate): Pair<String, ImageVector>? {
    val kinds = r?.tokens?.map { it.kind }.orEmpty().toSet()
    return when {
        p.day != null && TokenKind.DATE !in kinds && TokenKind.TIME !in kinds -> DueChip.short(p.day, today) to Icons.Outlined.Event
        p.list != null && TokenKind.COLLECTION !in kinds -> (lists.firstOrNull { it.url == p.list }?.name ?: AppText.get(R.string.tasks_qa_list)) to Icons.Outlined.Folder
        p.tag != null -> "@${p.tag}" to Icons.Outlined.Sell
        p.priority > 0 && TokenKind.PRIORITY !in kinds -> AppText.get(R.string.tasks_qa_high) to Icons.Outlined.Flag
        else -> null
    }
}
