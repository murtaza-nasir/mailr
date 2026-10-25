package io.github.murtazanasir.mailr.ui.recordings

import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.R
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.FormatListBulleted
import androidx.compose.material.icons.outlined.CheckBox
import androidx.compose.material.icons.outlined.FormatBold
import androidx.compose.material.icons.outlined.FormatItalic
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material.icons.outlined.Title
import androidx.compose.material3.FilledIconToggleButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import com.mohamedrejeb.richeditor.model.RichTextState
import com.mohamedrejeb.richeditor.ui.material3.OutlinedRichTextEditor
import kotlinx.coroutines.flow.debounce
import kotlinx.coroutines.flow.distinctUntilChanged

/**
 * Notes while recording, edited as rich text and kept as Markdown (Speakr's notes are Markdown).
 * Checklist items are "☐ " / "☑ " lines in the editor and `- [ ] ` / `- [x] ` in Markdown.
 */
object NotesMarkdown {
    private val TASK_IN = Regex("""(?m)^(\s*)[-*+]\s+\[([ xX])]\s+""")
    private val TASK_OUT = Regex("""(?m)^(\s*)(?:[-*+]\s+)?([☐☑])\s?""")

    /** Markdown → the editor's Markdown (checklist items become ☐ / ☑ paragraphs). */
    fun toEditor(md: String): String {
        if (md.isBlank()) return ""
        // A checklist item stands as its own paragraph, so the editor keeps it on its own line.
        val lines = md.replace("\r\n", "\n").lines()
        val out = StringBuilder()
        lines.forEachIndexed { i, line ->
            val m = TASK_IN.find(line)
            if (m != null && m.range.first == 0) {
                if (out.isNotEmpty() && !out.endsWith("\n\n")) out.append(if (out.endsWith("\n")) "\n" else "\n\n")
                out.append(if (m.groupValues[2].isBlank()) "☐ " else "☑ ").append(line.substring(m.range.last + 1)).append("\n\n")
            } else {
                out.append(line)
                if (i < lines.size - 1) out.append("\n")
            }
        }
        return out.toString().trimEnd()
    }

    /** The editor's Markdown → Markdown (☐ / ☑ paragraphs become a checklist). */
    fun fromEditor(md: String): String {
        val lines = TASK_OUT.replace(md.replace("\r\n", "\n")) { m ->
            m.groupValues[1] + (if (m.groupValues[2] == "☑") "- [x] " else "- [ ] ")
        }.lines()
        // Consecutive checklist items form one list (no blank lines between them).
        val out = mutableListOf<String>()
        for ((i, l) in lines.withIndex()) {
            val isTask = l.trimStart().startsWith("- [")
            if (l.isBlank() && out.lastOrNull()?.trimStart()?.startsWith("- [") == true &&
                lines.drop(i + 1).firstOrNull { it.isNotBlank() }?.trimStart()?.startsWith("- [") == true) continue
            out += if (isTask) l.trimEnd() else l
        }
        return out.joinToString("\n").trim()
    }

    fun read(state: RichTextState): String = fromEditor(state.toMarkdown())

    fun write(state: RichTextState, md: String) {
        state.setMarkdown(toEditor(md))
    }
}

private val HEADING = SpanStyle(fontSize = 1.5.em, fontWeight = FontWeight.Bold)

/** The paragraph around the cursor, as a text range. */
private fun paragraphAt(state: RichTextState): TextRange {
    val text = state.annotatedString.text
    val at = state.selection.min.coerceIn(0, text.length)
    val start = text.lastIndexOf('\n', at - 1).let { if (it < 0) 0 else it + 1 }
    val end = text.indexOf('\n', at).let { if (it < 0) text.length else it }
    return TextRange(start, end)
}

/** Heading on or off for the paragraph around the cursor. */
internal fun toggleHeading(state: RichTextState) {
    val p = paragraphAt(state)
    if (p.collapsed) return
    if (state.currentSpanStyle.fontSize == HEADING.fontSize) state.removeSpanStyle(HEADING, p) else state.addSpanStyle(HEADING, p)
}

/** A checklist item: none → ☐ → ☑ → none, at the start of the paragraph around the cursor. */
internal fun toggleCheck(state: RichTextState) {
    val p = paragraphAt(state)
    val text = state.annotatedString.text
    val line = text.substring(p.start, p.end)
    val keep = state.selection
    when {
        line.startsWith("☐ ") -> { state.selection = TextRange(p.start, p.start + 1); state.replaceSelectedText("☑") }
        line.startsWith("☑ ") -> { state.removeTextRange(TextRange(p.start, p.start + 2)) }
        else -> { state.selection = TextRange(p.start); state.addTextAfterSelection("☐ ") }
    }
    val len = state.annotatedString.text.length
    val shift = when { line.startsWith("☐ ") -> 0; line.startsWith("☑ ") -> -2; else -> 2 }
    state.selection = TextRange((keep.min + shift).coerceIn(0, len))
}

/** "[12:34] " at the cursor: the recording's time when the note was written. */
internal fun insertTimestamp(state: RichTextState, elapsedMs: Long) {
    state.addTextAfterSelection("[${clock(elapsedMs)}] ")
}

/** mm:ss, or h:mm:ss from an hour on (Speakr's timer). */
fun clock(ms: Long): String {
    val s = maxOf(0L, ms / 1000)
    val h = s / 3600
    val m = (s % 3600) / 60
    val sec = (s % 60).toString().padStart(2, '0')
    return if (h > 0) "$h:${m.toString().padStart(2, '0')}:$sec" else "${m.toString().padStart(2, '0')}:$sec"
}

/**
 * The notes editor with its toolbar: bold, italic, heading, bullets, checklist and the
 * recording's time. [onMarkdown] gets the Markdown a moment after each change.
 */
@OptIn(kotlinx.coroutines.FlowPreview::class)
@Composable
fun NotesEditor(state: RichTextState, elapsedMs: () -> Long, onMarkdown: (String) -> Unit, modifier: Modifier = Modifier) {
    LaunchedEffect(state) {
        snapshotFlow { state.annotatedString }.debounce(600).distinctUntilChanged().collect { onMarkdown(NotesMarkdown.read(state)) }
    }
    Column(modifier) {
        Surface(color = MaterialTheme.colorScheme.surfaceContainer, shape = MaterialTheme.shapes.small) {
            Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 4.dp),
                horizontalArrangement = Arrangement.spacedBy(2.dp), verticalAlignment = Alignment.CenterVertically) {
                val style = state.currentSpanStyle
                FilledIconToggleButton(style.fontWeight == FontWeight.Bold && style.fontSize != HEADING.fontSize,
                    { state.toggleSpanStyle(SpanStyle(fontWeight = FontWeight.Bold)) }) { Icon(Icons.Outlined.FormatBold, stringResource(R.string.rec_bold)) }
                FilledIconToggleButton(style.fontStyle == FontStyle.Italic,
                    { state.toggleSpanStyle(SpanStyle(fontStyle = FontStyle.Italic)) }) { Icon(Icons.Outlined.FormatItalic, stringResource(R.string.rec_italic)) }
                FilledIconToggleButton(style.fontSize == HEADING.fontSize, { toggleHeading(state) }) { Icon(Icons.Outlined.Title, stringResource(R.string.rec_heading)) }
                FilledIconToggleButton(state.isUnorderedList, { state.toggleUnorderedList() }) {
                    Icon(Icons.AutoMirrored.Outlined.FormatListBulleted, stringResource(R.string.rec_bulleted))
                }
                FilledIconToggleButton(false, { toggleCheck(state) }) { Icon(Icons.Outlined.CheckBox, stringResource(R.string.rec_checklist)) }
                FilledIconToggleButton(false, { insertTimestamp(state, elapsedMs()) }) { Icon(Icons.Outlined.Schedule, stringResource(R.string.rec_insert_time)) }
            }
        }
        OutlinedRichTextEditor(state = state, modifier = Modifier.fillMaxWidth().heightIn(min = 160.dp).padding(top = 6.dp),
            placeholder = { Text(stringResource(R.string.rec_notes)) })
    }
}
