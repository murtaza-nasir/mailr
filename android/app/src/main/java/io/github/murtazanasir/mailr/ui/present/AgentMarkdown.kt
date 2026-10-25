package io.github.murtazanasir.mailr.ui.present

import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withLink
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.mikepenz.markdown.m3.Markdown
import com.mikepenz.markdown.m3.markdownColor
import com.mikepenz.markdown.m3.markdownTypography
import androidx.compose.ui.res.pluralStringResource
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText

/*
 * The assistant's Markdown on a phone. The server normalises
 * the text (### and #### headings, tables set off); here the text is split into table blocks and
 * everything else (MdBlocks). Everything else goes to the Markdown library with modest heading
 * sizes; a table is laid out for the width it has (TableLayout): a table when it fits, stacked cards
 * when it does not and has three or more columns, else a horizontally scrolling table. The web has
 * the same rule (present/tableLayout.ts); both run spec/fixtures/agent-markdown-fixtures.json.
 */

sealed interface MdBlock {
    data class Text(val md: String) : MdBlock
    data class Table(val header: List<String>, val rows: List<List<String>>) : MdBlock
}

object MdBlocks {
    private val SEP = Regex("""^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$""")
    private val FENCE = Regex("""^\s*(```|~~~)""")

    /** Cells of one table row: outer pipes dropped, `\|` and pipes inside `code` kept. */
    fun cells(line: String): List<String> {
        var s = line.trim()
        if (s.startsWith("|")) s = s.substring(1)
        if (s.endsWith("|") && !s.endsWith("\\|")) s = s.dropLast(1)
        val out = mutableListOf<String>()
        val cur = StringBuilder()
        var code = false
        var i = 0
        while (i < s.length) {
            val c = s[i]
            when {
                c == '\\' && i + 1 < s.length && s[i + 1] == '|' -> { cur.append('|'); i++ }
                c == '`' -> { code = !code; cur.append(c) }
                c == '|' && !code -> { out += cur.toString().trim(); cur.clear() }
                else -> cur.append(c)
            }
            i++
        }
        out += cur.toString().trim()
        return out
    }

    /** The text as table blocks and the Markdown between them (code fences are never split). */
    fun split(md: String): List<MdBlock> {
        val lines = md.replace("\r\n", "\n").split("\n")
        val out = mutableListOf<MdBlock>()
        val text = StringBuilder()
        fun flush() {
            if (text.isNotBlank()) out += MdBlock.Text(text.toString().trim('\n'))
            text.clear()
        }
        var fenced = false
        var i = 0
        while (i < lines.size) {
            val ln = lines[i]
            if (FENCE.containsMatchIn(ln)) fenced = !fenced
            if (!fenced && '|' in ln && i + 1 < lines.size && SEP.matches(lines[i + 1]) && !SEP.matches(ln)) {
                flush()
                val header = cells(ln)
                val rows = mutableListOf<List<String>>()
                var j = i + 2
                while (j < lines.size && lines[j].isNotBlank() && '|' in lines[j]) {
                    val r = cells(lines[j])
                    rows += List(header.size) { k -> r.getOrElse(k) { "" } }
                    j++
                }
                out += MdBlock.Table(header, rows)
                i = j
                continue
            }
            text.append(ln).append('\n')
            i++
        }
        flush()
        return out
    }
}

enum class TableLayout { TABLE, SCROLL, CARDS }

object TableLayouts {
    private const val MAX_CHARS = 40
    private const val CHAR_DP = 7
    private const val PAD_DP = 16

    /** The visible length of a cell: Markdown marks, citation links and link targets left out. */
    fun cellChars(cell: String): Int = cell
        .replace(Regex("""\[(\d+)]\(mailr-cite:\d+\)"""), "[$1]")
        .replace(Regex("""\[([^\]]*)]\([^)]*\)"""), "$1")
        .replace("**", "").replace("__", "").replace("`", "")
        .trim().length

    /** TABLE when the columns fit [widthDp], else CARDS for 3+ columns, else SCROLL. */
    fun layout(header: List<String>, rows: List<List<String>>, widthDp: Int): TableLayout {
        var est = 0
        header.forEachIndexed { i, h ->
            val longest = (listOf(h) + rows.map { it.getOrElse(i) { "" } }).maxOf { cellChars(it) }
            est += minOf(MAX_CHARS, longest) * CHAR_DP + PAD_DP
        }
        return when {
            est <= widthDp -> TableLayout.TABLE
            header.size >= 3 -> TableLayout.CARDS
            else -> TableLayout.SCROLL
        }
    }
}

private val CITE = Regex("""\[(\d+)]\(mailr-cite:(\d+)\)""")
private val MD_IMAGE = Regex("""!\[([^\]\n]{0,300})]\(([^)\s]{1,2000})[^)]*\)""")

/** Text for the Markdown library: citation markers as "[n]" links, images as plain links. */
fun libraryText(md: String): String =
    md.replace(MD_IMAGE) { m -> "[" + AppText.get(R.string.present_image_link, m.groupValues[1]) + "](" + m.groupValues[2] + ")" }.replace(CITE, "[\\[$1\\]](mailr-cite:$2)")

/**
 * Inline Markdown of a table cell as styled text: **bold**, `code`, [n](mailr-cite:n) citation links
 * and [text](url) links. Links go through LocalUriHandler (citations open the email).
 */
fun inlineCell(cell: String, link: Color): AnnotatedString = buildAnnotatedString {
    val token = Regex("""\*\*(.+?)\*\*|`([^`]+)`|\[(\d+)]\(mailr-cite:(\d+)\)|\[([^\]]+)]\(([^)\s]+)\)""")
    var at = 0
    for (m in token.findAll(cell)) {
        append(cell.substring(at, m.range.first))
        val g = m.groupValues
        when {
            g[1].isNotEmpty() -> withStyle(SpanStyle(fontWeight = FontWeight.SemiBold)) { append(g[1]) }
            g[2].isNotEmpty() -> withStyle(SpanStyle(fontFamily = FontFamily.Monospace, fontSize = 13.sp)) { append(g[2]) }
            g[3].isNotEmpty() -> withLink(LinkAnnotation.Url("mailr-cite:${g[4]}", TextLinkStyles(SpanStyle(color = link)))) {
                append("[${g[3]}]")
            }
            // A link that would not open (content:, intent:, sms:…) stays plain text.
            io.github.murtazanasir.mailr.ui.util.SafeLinks.kindOf(g[6]) == io.github.murtazanasir.mailr.ui.util.SafeLinks.Kind.REFUSED -> append(g[5])
            else -> withLink(LinkAnnotation.Url(g[6], TextLinkStyles(SpanStyle(color = link)))) { append(g[5]) }
        }
        at = m.range.last + 1
    }
    append(cell.substring(at))
}

/** Heading sizes for a phone: ### is a small title, #### a label; h1 and h2 (older text) match ###. */
@Composable
fun agentTypography() = MaterialTheme.typography.let { t ->
    val h = t.titleSmall.copy(fontSize = 16.sp, lineHeight = 22.sp, fontWeight = FontWeight.SemiBold)
    markdownTypography(
        h1 = h, h2 = h, h3 = h,
        h4 = t.titleSmall.copy(fontSize = 15.sp, lineHeight = 20.sp, fontWeight = FontWeight.SemiBold),
        h5 = t.labelLarge, h6 = t.labelLarge,
        text = t.bodyLarge, paragraph = t.bodyLarge,
    )
}

/** The numbers of the citation links in [md], in order, once each. */
fun citedNumbers(md: String): List<Int> = CITE.findAll(md).map { it.groupValues[2].toInt() }.distinct().toList()

/** Non-breaking padding on each side of a citation number. */
internal const val CHIP_PAD = "\u2007"

private val CITE_DEST = Regex("""^mailr-cite:(\d+)$""")

/**
 * Links in the assistant's Markdown as the text's own link annotations (Compose's LinkAnnotation),
 * not the Markdown library's tap handler. The library's handler is a pointerInput that keeps the
 * text it first saw: for an answer that streamed in, that text had no links yet, so citation
 * numbers did not respond to a tap. A citation shows as its number on a tinted
 * chip, widened by figure spaces so a finger finds it. Figure spaces do not break a line, so a chip at
 * the end of a line keeps its full width (an en space there was dropped with the tint and the chip
 * lost its right side). [onCitation] opens it. Other links go to
 * LocalUriHandler, and a link that would not open safely stays plain text.
 */
@Composable
private fun linkAnnotator(onCitation: ((Int) -> Unit)?): com.mikepenz.markdown.model.MarkdownAnnotator {
    val uri = androidx.compose.ui.platform.LocalUriHandler.current
    val currentUri by androidx.compose.runtime.rememberUpdatedState(uri)
    val currentCite by androidx.compose.runtime.rememberUpdatedState(onCitation)
    // The annotator may be kept across recompositions, so it reads the current colours and callbacks.
    val link by androidx.compose.runtime.rememberUpdatedState(MaterialTheme.colorScheme.primary)
    val chip by androidx.compose.runtime.rememberUpdatedState(SpanStyle(color = MaterialTheme.colorScheme.onPrimaryContainer,
        background = MaterialTheme.colorScheme.primaryContainer, fontWeight = FontWeight.SemiBold, fontSize = 14.sp))
    return com.mikepenz.markdown.model.markdownAnnotator { content, node ->
        fun dest(): String? = node.children.firstOrNull { it.type == org.intellij.markdown.MarkdownElementTypes.LINK_DESTINATION }
            ?.let { content.substring(it.startOffset, it.endOffset) }
        when (node.type) {
            org.intellij.markdown.MarkdownElementTypes.INLINE_LINK -> {
                val to = dest() ?: return@markdownAnnotator false
                val n = CITE_DEST.find(to)?.groupValues?.get(1)?.toIntOrNull()
                if (n != null) {
                    withLink(LinkAnnotation.Clickable("cite-$n", TextLinkStyles(chip)) {
                        currentCite?.invoke(n) ?: currentUri.openUri(to)
                    }) { append("$CHIP_PAD$n$CHIP_PAD") }
                    return@markdownAnnotator true
                }
                val label = node.children.firstOrNull { it.type == org.intellij.markdown.MarkdownElementTypes.LINK_TEXT }
                    ?.let { content.substring(it.startOffset, it.endOffset).removePrefix("[").removeSuffix("]") }
                    ?.replace("**", "")?.replace("`", "") ?: to
                if (io.github.murtazanasir.mailr.ui.util.SafeLinks.kindOf(to) == io.github.murtazanasir.mailr.ui.util.SafeLinks.Kind.REFUSED) append(label)
                else withLink(LinkAnnotation.Clickable("url", TextLinkStyles(SpanStyle(color = link))) { currentUri.openUri(to) }) { append(label) }
                true
            }
            org.intellij.markdown.MarkdownElementTypes.AUTOLINK, org.intellij.markdown.flavours.gfm.GFMTokenTypes.GFM_AUTOLINK -> {
                val to = content.substring(node.startOffset, node.endOffset).removePrefix("<").removeSuffix(">")
                if (io.github.murtazanasir.mailr.ui.util.SafeLinks.kindOf(to) == io.github.murtazanasir.mailr.ui.util.SafeLinks.Kind.REFUSED) append(to)
                else withLink(LinkAnnotation.Clickable("url", TextLinkStyles(SpanStyle(color = link))) { currentUri.openUri(to) }) { append(to) }
                true
            }
            else -> false
        }
    }
}

/**
 * The assistant's Markdown, laid out for the available width. [streaming] adds a caret. Tables
 * become TABLE, SCROLL or CARDS by [TableLayouts.layout]. [onCitation] opens a cited source;
 * [citationLabel] names it for TalkBack, which offers "Open source n: …" as an action on the text.
 */
@Composable
fun AgentMarkdown(md: String, modifier: Modifier = Modifier, streaming: Boolean = false,
                  onCitation: ((Int) -> Unit)? = null, citationLabel: (Int) -> String? = { null }) {
    val blocks = remember(md) { MdBlocks.split(md) }
    val annotator = linkAnnotator(onCitation)
    val cited = remember(md) { citedNumbers(md) }
    val actions = if (onCitation == null) emptyList() else cited.map { n ->
        androidx.compose.ui.semantics.CustomAccessibilityAction(
            citationLabel(n)?.let { AppText.get(R.string.present_open_source_named, n, it) } ?: AppText.get(R.string.present_open_source, n)) { onCitation(n); true }
    }
    BoxWithConstraints(modifier.fillMaxWidth().semantics { if (actions.isNotEmpty()) customActions = actions }) {
        val width = maxWidth.value.toInt()
        Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            blocks.forEachIndexed { i, b ->
                val last = i == blocks.lastIndex
                when (b) {
                    is MdBlock.Text -> Markdown(
                        content = libraryText(b.md) + if (streaming && last) " ▍" else "",
                        colors = markdownColor(), typography = agentTypography(),
                        modifier = Modifier.fillMaxWidth(), annotator = annotator,
                    )
                    is MdBlock.Table -> MdTable(b, TableLayouts.layout(b.header, b.rows, width))
                }
            }
        }
    }
}

@Composable
fun MdTable(t: MdBlock.Table, layout: TableLayout) {
    val link = MaterialTheme.colorScheme.primary
    val line = MaterialTheme.colorScheme.outlineVariant
    val body = MaterialTheme.typography.bodyMedium
    val head = MaterialTheme.typography.labelLarge
    val desc = pluralStringResource(R.plurals.present_table_desc, t.rows.size, t.rows.size, layout.name.lowercase())
    if (layout == TableLayout.CARDS) {
        Column(Modifier.fillMaxWidth().semantics { contentDescription = desc }, verticalArrangement = Arrangement.spacedBy(8.dp)) {
            t.rows.forEach { r ->
                Surface(shape = RoundedCornerShape(12.dp), color = MaterialTheme.colorScheme.surfaceContainerLow,
                    modifier = Modifier.fillMaxWidth().border(1.dp, line, RoundedCornerShape(12.dp))) {
                    Column(Modifier.padding(horizontal = 12.dp, vertical = 10.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        r.forEachIndexed { k, cell ->
                            if (k == 0) Text(inlineCell(cell, link), style = body.copy(fontWeight = FontWeight.SemiBold))
                            else if (cell.isNotBlank()) Column {
                                Text(t.header.getOrElse(k) { "" }, style = MaterialTheme.typography.labelMedium,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                                Text(inlineCell(cell, link), style = body)
                            }
                        }
                    }
                }
            }
        }
        return
    }
    val scroll = layout == TableLayout.SCROLL
    val cellMod = if (scroll) Modifier.widthIn(min = 96.dp, max = 260.dp) else Modifier
    @Composable
    fun row(cells: List<String>, header: Boolean) {
        Row(Modifier.height(IntrinsicSize.Min).let { if (scroll) it else it.fillMaxWidth() }) {
            cells.forEach { c ->
                Surface(color = if (header) MaterialTheme.colorScheme.surfaceContainerHigh else Color.Transparent,
                    modifier = cellMod.let { if (scroll) it else it.weight(1f) }.fillMaxHeight().border(0.5.dp, line)) {
                    Text(inlineCell(c, link), style = if (header) head else body, modifier = Modifier.padding(horizontal = 8.dp, vertical = 6.dp))
                }
            }
        }
    }
    Column(
        Modifier.semantics { contentDescription = desc }
            .let { if (scroll) it.horizontalScroll(rememberScrollState()) else it.fillMaxWidth() }
            .border(1.dp, line, RoundedCornerShape(4.dp)),
    ) {
        row(t.header, true)
        t.rows.forEach { row(it, false) }
    }
}
