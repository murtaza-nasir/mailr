package io.github.murtazanasir.mailr.ui.compose

import io.github.murtazanasir.mailr.ui.util.Format

/**
 * The compose body as blocks. The phone editor is plain text, so a body is a list of blocks:
 * text the user edits, inline images (ACT mail.insert_image @android.editor), and HTML kept as it was written
 * (formatting from the web, a table, a styled paragraph). Kept HTML goes back to the server
 * unchanged, so a draft or reply never loses its formatting on the phone.
 */
sealed interface BodyBlock {
    val key: Long

    /** Text the user edits. [original] is the HTML it came from; while the text is unchanged
     *  that HTML goes out again, byte for byte. */
    data class Text(override val key: Long, val text: String, val original: Pair<String, String>? = null) : BodyBlock

    /** An inline image: an upload shown from /api/v1/uploads/<blob>. [blob] is null while the
     *  upload runs; such an image is left out of saves. [original] is the HTML it came from,
     *  written again while nothing about the image changed. */
    data class Image(
        override val key: Long,
        val blob: String?,
        val width: Int,
        val height: Int,
        val alt: String = "",
        val original: String? = null,
    ) : BodyBlock {
        val uploading: Boolean get() = blob == null
    }

    /** HTML kept as written. Shown read-only; "Edit as plain text" turns it into text. */
    data class Html(override val key: Long, val html: String) : BodyBlock
}

/** The parts of a draft's HTML the phone keeps apart: the user's blocks, the signature and the
 *  quoted (or forwarded) history. */
data class ParsedBody(val blocks: List<BodyBlock>, val signature: String?, val quote: String?)

object ComposeBody {
    private var seq = 1L
    @Synchronized fun nextKey(): Long = seq++

    const val SIGNATURE_MARK = "<div class=\"mailr_signature\""
    private const val QUOTE_MARK = "<div class=\"mailr_quote\""
    private const val FORWARD_MARK = "---------- Forwarded message ---------"

    /** The server's form of an uploaded image (core/compose.py UPLOAD_SRC). */
    val UPLOAD_SRC = Regex("""^(?:https?://[^/\s]+)?/api/v1/uploads/([0-9a-f]{32})$""")

    fun uploadSrc(blob: String) = "/api/v1/uploads/$blob"

    /** A body of plain text. */
    fun ofText(text: String): List<BodyBlock> = listOf(BodyBlock.Text(nextKey(), text))

    // ---------- HTML → blocks ----------

    /** Split a draft's HTML into blocks, the signature and the quoted history. Nothing that
     *  cannot be edited as plain text is flattened: it stays as an [BodyBlock.Html] block. */
    fun parse(html: String?): ParsedBody {
        if (html.isNullOrEmpty()) return ParsedBody(ofText(""), null, null)
        val qi = html.indexOf(QUOTE_MARK)
        val mine = if (qi < 0) html else html.substring(0, qi)
        var quote: String? = if (qi < 0) null else html.substring(qi)
        val nodes = topLevelNodes(mine)
        if (nodes == null) {
            // Not well-formed enough to split: keep all of it as written.
            return ParsedBody(withTextEnds(listOf(BodyBlock.Html(nextKey(), mine))), null, quote)
        }
        var list = nodes
        // A forward has no quote marker; the forwarded message starts at its header.
        if (quote == null) {
            val fi = list.indexOfFirst { FORWARD_MARK in it }
            if (fi >= 0) {
                var start = fi
                while (start > 0 && isBreaks(list[start - 1])) start--
                quote = list.subList(start, list.size).joinToString("")
                list = list.subList(0, start)
            }
        }
        // The signature: the last signature node, with the line breaks before it.
        var signature: String? = null
        val si = list.indexOfLast { it.startsWith(SIGNATURE_MARK) }
        if (si >= 0) {
            var start = si
            while (start > 0 && isBreaks(list[start - 1])) start--
            signature = list.subList(start, si + 1).joinToString("")
            list = list.subList(0, start) + list.subList(si + 1, list.size)
        }
        return ParsedBody(withTextEnds(blocksOf(list)), signature, quote)
    }

    private fun blocksOf(nodes: List<String>): List<BodyBlock> {
        val out = mutableListOf<BodyBlock>()
        val run = mutableListOf<String>()
        fun flush() {
            if (run.isEmpty()) return
            val html = run.joinToString("")
            val text = run.map { Format.htmlToText(it) }.filter { it.isNotBlank() }.joinToString("\n\n")
            out += BodyBlock.Text(nextKey(), text, html to text)
            run.clear()
        }
        for (n in nodes) {
            val img = imageOf(n)
            when {
                img != null -> { flush(); out += img }
                isPlain(n) -> run += n
                else -> { flush(); out += BodyBlock.Html(nextKey(), n) }
            }
        }
        flush()
        return out
    }

    /** A text block at the end (to type after kept HTML and images), and between two blocks
     *  that are not text. */
    private fun withTextEnds(blocks: List<BodyBlock>): List<BodyBlock> {
        val out = mutableListOf<BodyBlock>()
        for (b in blocks) {
            // Between two blocks that are not text; not before the first one (it would only add a gap).
            if (b !is BodyBlock.Text && out.isNotEmpty() && out.last() !is BodyBlock.Text) out += BodyBlock.Text(nextKey(), "")
            out += b
        }
        if (out.lastOrNull() !is BodyBlock.Text) out += BodyBlock.Text(nextKey(), "")
        return out
    }

    private val BREAKS = Regex("""^(?:\s|<br\s*/?>)*$""", RegexOption.IGNORE_CASE)
    private fun isBreaks(n: String) = BREAKS.matches(n)

    private val PLAIN_BLOCK = Regex("""^<(p|div)>(.*)</\1>$""", setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL))
    private val BR = Regex("""<br\s*/?>""", RegexOption.IGNORE_CASE)
    private val AUTOLINK = Regex("""<a href="([^"<>]+)">([^<]*)</a>""")

    /** Whether a node is only text, line breaks and bare links: nothing is lost when it is
     *  edited as plain text (the links come back from linkify). */
    internal fun isPlain(node: String): Boolean {
        val inner = PLAIN_BLOCK.matchEntire(node)?.groupValues?.get(2) ?: node
        val rest = AUTOLINK.replace(BR.replace(inner, "")) { m ->
            if (m.groupValues[1] == m.groupValues[2] && m.groupValues[1].startsWith("http")) "" else "<x>"
        }
        return '<' !in rest
    }

    private val IMG_ONLY = Regex("""^(?:<(p|div)>\s*)?(<img\b[^>]*>)\s*(?:</\1>)?$""", RegexOption.IGNORE_CASE)

    /** An image alone in its paragraph that points at one of the user's uploads. */
    internal fun imageOf(node: String): BodyBlock.Image? {
        val m = IMG_ONLY.matchEntire(node.trim()) ?: return null
        if (m.groupValues[1].isEmpty() && node.trim() != m.groupValues[2]) return null
        val tag = m.groupValues[2]
        val blob = UPLOAD_SRC.matchEntire(attr(tag, "src") ?: return null)?.groupValues?.get(1) ?: return null
        return BodyBlock.Image(nextKey(), blob, attr(tag, "width")?.toIntOrNull() ?: 0,
            attr(tag, "height")?.toIntOrNull() ?: 0, Format.htmlToText(attr(tag, "alt").orEmpty()), original = node)
    }

    private fun attr(tag: String, name: String): String? =
        Regex("""\s$name\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", RegexOption.IGNORE_CASE).find(tag)
            ?.groupValues?.drop(1)?.firstOrNull { it.isNotEmpty() }

    private val VOID = setOf("br", "img", "hr", "meta", "input", "wbr", "col", "area", "base", "link", "source", "embed", "param", "track")
    private val BLOCK = setOf("p", "div", "ul", "ol", "table", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "hr",
        "section", "article", "header", "footer", "center", "dl", "figure")
    private val TAG = Regex("""^<(/?)([a-zA-Z][a-zA-Z0-9-]*)""")

    /**
     * The top-level nodes of an HTML fragment: each block element on its own, and each run of
     * text and inline elements between them as one node. Null when the fragment is not
     * well-formed enough to split safely.
     */
    internal fun topLevelNodes(html: String): List<String>? {
        val out = mutableListOf<String>()
        val stack = ArrayDeque<String>()
        var i = 0
        var nodeStart = 0
        var inlineRun = false   // the current top-level node is a run of inline content
        fun endNode(at: Int) {
            if (at > nodeStart) out += html.substring(nodeStart, at)
            nodeStart = at
        }
        while (i < html.length) {
            if (html[i] != '<') {
                if (stack.isEmpty() && !inlineRun) { endNode(i); inlineRun = true }
                i++
                continue
            }
            if (html.startsWith("<!--", i)) {
                val e = html.indexOf("-->", i)
                if (e < 0) return null
                if (stack.isEmpty() && !inlineRun) { endNode(i); inlineRun = true }
                i = e + 3
                continue
            }
            val end = tagEnd(html, i) ?: return null
            val m = TAG.find(html.substring(i, minOf(end + 1, i + 64)))
            if (m == null) {
                // A stray "<" in text.
                if (stack.isEmpty() && !inlineRun) { endNode(i); inlineRun = true }
                i++
                continue
            }
            val closing = m.groupValues[1] == "/"
            val name = m.groupValues[2].lowercase()
            val selfClosing = html[end - 1] == '/'
            if (stack.isEmpty() && !closing) {
                val block = name in BLOCK
                if (block || !inlineRun) { endNode(i); inlineRun = !block }
            }
            if (closing) {
                val at = stack.lastIndexOf(name)
                if (at < 0) return null
                while (stack.size > at) stack.removeLast()
            } else if (name !in VOID && !selfClosing) {
                stack.addLast(name)
            }
            i = end + 1
            if (stack.isEmpty() && !inlineRun) endNode(i)
        }
        if (stack.isNotEmpty()) return null
        endNode(html.length)
        return out.filter { it.isNotEmpty() }
    }

    /** The index of the ">" that ends the tag starting at [start], quotes respected. */
    private fun tagEnd(html: String, start: Int): Int? {
        var q: Char? = null
        var i = start + 1
        while (i < html.length) {
            val c = html[i]
            if (q != null) { if (c == q) q = null }
            else if (c == '"' || c == '\'') q = c
            else if (c == '>') return i
            i++
        }
        return null
    }

    // ---------- blocks → HTML ----------

    /** The editor HTML for the blocks: the server's contract (an uploaded image is
     *  <img src="/api/v1/uploads/<blob>">; core/compose.py turns it into a cid: part). */
    fun toHtml(blocks: List<BodyBlock>): String = blocks.joinToString("") { b ->
        when (b) {
            is BodyBlock.Text -> when {
                b.original != null && b.original.second == b.text -> b.original.first
                b.text.isBlank() -> ""
                else -> Format.textToHtml(b.text)
            }
            is BodyBlock.Image -> when {
                b.blob == null -> ""
                b.original != null -> b.original
                else -> imageHtml(b)
            }
            is BodyBlock.Html -> b.html
        }
    }

    /** An image in its own paragraph, as the web editor writes it (inlineImages.ts imageStyle). */
    fun imageHtml(b: BodyBlock.Image): String {
        val alt = b.alt.replace("&", "&amp;").replace("\"", "&quot;").replace("<", "&lt;")
        val size = if (b.width > 0 && b.height > 0) " width=\"${b.width}\" height=\"${b.height}\"" else ""
        return "<p><img src=\"${uploadSrc(b.blob!!)}\" alt=\"$alt\"$size style=\"max-width:100%;height:auto\"></p>"
    }

    /** The plain text of the blocks (for "is there anything written" and the old body field). */
    fun text(blocks: List<BodyBlock>): String =
        blocks.filterIsInstance<BodyBlock.Text>().map { it.text }.filter { it.isNotBlank() }.joinToString("\n\n")

    fun hasContent(blocks: List<BodyBlock>): Boolean =
        blocks.any { it is BodyBlock.Image || it is BodyBlock.Html || (it is BodyBlock.Text && it.text.isNotBlank()) }

    // ---------- edits ----------

    /** Put [image] into the text block [at] at [offset] (the cursor). The text splits around
     *  it; a line break on either side of the cursor goes with the split. */
    fun insert(blocks: List<BodyBlock>, at: Long?, offset: Int, image: BodyBlock.Image): List<BodyBlock> {
        val idx = blocks.indexOfFirst { it.key == at && it is BodyBlock.Text }.takeIf { it >= 0 }
            ?: blocks.indexOfLast { it is BodyBlock.Text }.takeIf { it >= 0 }
            ?: return blocks + image + BodyBlock.Text(nextKey(), "")
        val t = blocks[idx] as BodyBlock.Text
        val cut = if (t.key == at) offset.coerceIn(0, t.text.length) else t.text.length
        val before = t.text.substring(0, cut).removeSuffix("\n")
        val after = t.text.substring(cut).removePrefix("\n")
        val parts = listOf(BodyBlock.Text(t.key, before), image, BodyBlock.Text(nextKey(), after))
        return blocks.subList(0, idx) + parts + blocks.subList(idx + 1, blocks.size)
    }

    /** Take a block out; the text blocks on either side join again. */
    fun remove(blocks: List<BodyBlock>, key: Long): List<BodyBlock> {
        val idx = blocks.indexOfFirst { it.key == key }
        if (idx < 0) return blocks
        val list = blocks.toMutableList().apply { removeAt(idx) }
        val a = list.getOrNull(idx - 1)
        val b = list.getOrNull(idx)
        if (a is BodyBlock.Text && b is BodyBlock.Text) {
            val joined = listOf(a.text, b.text).filter { it.isNotEmpty() }.joinToString("\n")
            list[idx - 1] = BodyBlock.Text(a.key, joined)
            list.removeAt(idx)
        }
        return withTextEnds(list.ifEmpty { ofText("") })
    }

    /** "Edit as plain text": a kept HTML block becomes text the user can change. */
    fun toText(blocks: List<BodyBlock>, key: Long): List<BodyBlock> = blocks.map {
        if (it is BodyBlock.Html && it.key == key) BodyBlock.Text(it.key, Format.htmlToText(it.html)) else it
    }

    fun setText(blocks: List<BodyBlock>, key: Long, text: String): List<BodyBlock> =
        blocks.map { if (it is BodyBlock.Text && it.key == key) it.copy(text = text) else it }
}
