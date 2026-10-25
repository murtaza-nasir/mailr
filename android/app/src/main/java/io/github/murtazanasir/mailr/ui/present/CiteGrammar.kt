package io.github.murtazanasir.mailr.ui.present

/**
 * The citation marker grammar (spec/cases/citation-cases.yaml). The server turns markers into
 * numbered links `[n](mailr-cite:n)`, also while the answer streams; this is the safety net for text
 * that still holds raw markers (an older answer, an older server). A marker that can be read is left
 * out, one that names a kind but cannot be read becomes its quoted text, and raw `[[…]]` markup never
 * shows. Twins: backend/mailr/intel/citegrammar.py and frontend/…/assistant/citeGrammar.ts.
 */
object CiteGrammar {
    data class Marker(val kind: String, val id: String, val at: String? = null, val quote: String? = null)

    private val KINDS = mapOf(
        "m" to "m", "message" to "m", "msg" to "m", "p" to "p", "passage" to "p", "c" to "c",
        "conversation" to "c", "thread" to "c", "r" to "r", "recording" to "r", "event" to "event", "task" to "task",
        "web" to "web", "note" to "note",
        // Tables: [[table:<id>]] and [[table:<id>#<row_id>]].
        "table" to "table",
    )
    private val TABLE_ID = Regex("""^t[0-9a-z]{6,15}$""")
    private val ROW_ID = Regex("""^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$""")

    /** A table (`t…`) or one of its rows (`t…#<uuid>`); spaces around "#" are dropped. */
    private fun tableMarker(head: String, quote: String?): Marker? {
        val token = head.replace(Regex("""\s*#\s*"""), "#").split(Regex("""\s+""")).firstOrNull { it.isNotEmpty() } ?: return null
        val i = token.indexOf('#')
        val pid = if (i < 0) token else token.substring(0, i)
        val row = if (i < 0) "" else token.substring(i + 1)
        if (!TABLE_ID.matches(pid) || (i >= 0 && !ROW_ID.matches(row))) return null
        return Marker("table", pid, row.lowercase().ifEmpty { null }, quote)
    }
    private const val QUOTES = "\"“”"
    private const val OCC = """\d{4}-?\d{2}-?\d{2}(?:T\d{2}:?\d{2}(?::?\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?"""
    private val OCC_TAIL = Regex("""^(.+?)@($OCC)$""")
    private val REC = Regex("""^(\d{1,9})(?:@(\d{1,2}:\d{2}(?::\d{2})?|\d{1,6}(?:\.\d+)?))?""")
    private val KIND = Regex("""^\s*([A-Za-z]+)\s*:\s*""")
    private val DIGITS = Regex("""^(\d{1,12})(?![\w@])""")
    private val WEB_URL = Regex("""^https?://[^/\s]""", RegexOption.IGNORE_CASE)
    /** A note: its id (letters and digits), and after "#" one of its items. */
    private val NOTE = Regex("""^([A-Za-z0-9]{2,40})(?:#([A-Za-z0-9-]{1,64}))?$""")

    /** One marker: `[[body]]`, or a legacy `[c:12]`, `[event:uid]`, `[task:uid]` that is not a Markdown link's text. */
    val MARKER = Regex("""([ \t]*)(?:\[\[((?:(?!\]\]|\[\[)[^\n]){0,500})\]\]|\[(c:\d{1,12}|(?:event|task):[^\]\s]{1,300})\](?!\())""")
    private val OPEN = Regex("""[ \t]*\[\[\s*[A-Za-z]+\s*:[^\]\n]{0,500}$""")
    private val PARTIAL = Regex("""[ \t]*\[\[?[^\]\n]{0,500}$""")

    fun looksLikeMarker(body: String): Boolean = KIND.containsMatchIn(body) && KIND.find(body)!!.range.first == 0

    private fun firstQuote(s: String) = s.indexOfFirst { it in QUOTES }
    private fun lastQuote(s: String) = s.indexOfLast { it in QUOTES }

    /** The marker in [body] (between `[[` and `]]`), or null when it cannot be read. */
    fun parse(body: String): Marker? {
        val m = KIND.find(body)?.takeIf { it.range.first == 0 } ?: return null
        val kind = KINDS[m.groupValues[1].lowercase()] ?: return null
        val rest = body.substring(m.range.last + 1)
        val qi = firstQuote(rest)
        var quote: String? = null
        var head = rest
        if (qi >= 0) {
            val last = lastQuote(rest)
            quote = (if (last > qi) rest.substring(qi + 1, last) else rest.substring(qi + 1)).trim().ifEmpty { null }
            head = rest.substring(0, qi)
        }
        head = head.replace(Regex("""\s*@\s*"""), "@").trim().replace(Regex("""[\s|,]+$"""), "")
        when (kind) {
            "m", "p", "c" -> return DIGITS.find(head)?.let { Marker(kind, it.groupValues[1], null, quote) }
            "r" -> {
                val d = REC.find(head) ?: return null
                if (head.length > d.range.last + 1 && !head[d.range.last + 1].isWhitespace()) return null
                return Marker("r", d.groupValues[1], d.groupValues[2].ifEmpty { null }, quote)
            }
        }
        if (kind == "table") return tableMarker(head, quote)
        val token = head.split(Regex("""\s+""")).firstOrNull { it.isNotEmpty() } ?: return null
        if ('[' in token || ']' in token) return null
        if (kind == "web") return if (WEB_URL.containsMatchIn(token)) Marker("web", token, null, quote) else null
        if (kind == "note") return NOTE.matchEntire(token)?.let { Marker("note", it.groupValues[1], it.groupValues[2].ifEmpty { null }, quote) }
        if (kind == "event") OCC_TAIL.matchEntire(token)?.let { return Marker("event", it.groupValues[1], it.groupValues[2], quote) }
        return Marker(kind, token, null, quote)
    }

    /** What a marker that cannot be read shows: its quoted text, else nothing. */
    fun fallback(body: String): String {
        val qi = firstQuote(body)
        if (qi < 0) return ""
        val last = lastQuote(body)
        return (if (last > qi) body.substring(qi + 1, last) else body.substring(qi + 1)).trim()
    }

    /** Every marker in [text] that can be read, in order. */
    fun scan(text: String): List<Marker> = MARKER.findAll(text).mapNotNull { mt ->
        val legacy = mt.groups[3]?.value
        val body = mt.groups[2]?.value
        when {
            legacy != null -> parse(legacy)
            body != null && looksLikeMarker(body) -> parse(body)
            else -> null
        }
    }.toList()

    /** The text a reader sees where no numbered link replaced a marker (citation-cases.yaml `clean` and `stream`). */
    fun clean(text: String, streaming: Boolean = false): String {
        var out = MARKER.replace(text) { mt ->
            val sp = mt.groupValues[1]
            val legacy = mt.groups[3]?.value
            val body = mt.groups[2]?.value.orEmpty()
            when {
                legacy != null -> ""
                !looksLikeMarker(body) -> mt.value
                parse(body) != null -> ""
                else -> fallback(body).let { if (it.isEmpty()) "" else sp + it }
            }
        }
        out = OPEN.replace(out, "")
        if (streaming) out = PARTIAL.replace(out, "")
        return out
    }
}
