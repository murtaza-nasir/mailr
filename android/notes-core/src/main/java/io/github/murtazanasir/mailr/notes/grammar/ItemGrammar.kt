package io.github.murtazanasir.mailr.notes.grammar

import java.text.Normalizer
import java.util.Locale

/**
 * The checklist item grammar: the Kotlin
 * twin of frontend/src/lib/itemadd.ts and backend/mailr/notes/itemadd.py. All three run the vectors
 * in spec/cases/notes-quickadd-cases.yaml (a copy sits in the app's test resources; vitest
 * checks the copy matches).
 *
 * One input becomes one or more checklist items. No model is involved: a word is taken only when a
 * rule matches it exactly, and every taken word comes back as a token with its span (UTF-16
 * offsets into the input, as Kotlin strings index), so the add line can highlight it.
 *
 * The parser never deletes characters while it works: a span it has used (a list token, a bullet,
 * a section prefix) is masked with spaces in a working copy, so every offset stays an offset into
 * the input.
 */
object ItemGrammar {
    enum class Mode { LIST, CAPTURE, EDIT }

    data class Token(val kind: String, val start: Int, val end: Int, val text: String)

    /** [amount] is a decimal string ("2", "0.5", "0.333"); [unit] a canonical unit or null for a count; [text] the typed form. */
    data class Qty(val amount: String, val unit: String?, val text: String)

    data class Item(
        val text: String,
        val qty: Qty?,
        /** A section name as the list spells it, or null for the top group (or none named). */
        val section: String?,
        val checked: Boolean,
        val start: Int,
        val end: Int,
    )

    data class ListInfo(val id: String, val name: String, val sections: List<String>)

    data class ListRef(val id: String?, val name: String, val create: Boolean)

    data class Result(val list: ListRef?, val items: List<Item>, val newSections: List<String>, val tokens: List<Token>)

    // ---------------------------------------------------------------- units

    private val UNIT_FORMS: Map<String, List<String>> = linkedMapOf(
        "g" to listOf("g", "gram", "grams", "gr"),
        "kg" to listOf("kg", "kilo", "kilos", "kilogram", "kilograms"),
        "mg" to listOf("mg"),
        "lb" to listOf("lb", "lbs", "pound", "pounds"),
        "oz" to listOf("oz", "ounce", "ounces"),
        "ml" to listOf("ml", "millilitre", "millilitres", "milliliter", "milliliters"),
        "l" to listOf("l", "litre", "litres", "liter", "liters"),
        "cl" to listOf("cl"),
        "dl" to listOf("dl"),
        "gal" to listOf("gal", "gallon", "gallons"),
        "qt" to listOf("qt", "quart", "quarts"),
        "pt" to listOf("pt", "pint", "pints"),
        "cup" to listOf("cup", "cups"),
        "tbsp" to listOf("tbsp", "tablespoon", "tablespoons"),
        "tsp" to listOf("tsp", "teaspoon", "teaspoons"),
        "dozen" to listOf("dozen", "doz"),
        "pack" to listOf("pack", "packs", "pk"),
        "can" to listOf("can", "cans", "tin", "tins"),
        "bottle" to listOf("bottle", "bottles"),
        "box" to listOf("box", "boxes"),
        "bag" to listOf("bag", "bags"),
        "jar" to listOf("jar", "jars"),
        "bunch" to listOf("bunch", "bunches"),
        "loaf" to listOf("loaf", "loaves"),
        "carton" to listOf("carton", "cartons"),
        "piece" to listOf("piece", "pieces", "pc", "pcs", "ct"),
    )
    private val UNIT_OF: Map<String, String> = buildMap { UNIT_FORMS.forEach { (c, fs) -> fs.forEach { put(it, c) } } }
    private val UNIT_RX = UNIT_OF.keys.sortedByDescending { it.length }.joinToString("|")

    /** The canonical unit of a typed form, or null. */
    fun unitOf(form: String): String? = UNIT_OF[form.lowercase(Locale.ROOT)]

    // ---------------------------------------------------------------- amounts

    private val VULGAR: Map<Char, Pair<Int, Int>> = mapOf(
        '½' to (1 to 2), '⅓' to (1 to 3), '⅔' to (2 to 3), '¼' to (1 to 4), '¾' to (3 to 4), '⅕' to (1 to 5),
        '⅖' to (2 to 5), '⅗' to (3 to 5), '⅘' to (4 to 5), '⅙' to (1 to 6), '⅚' to (5 to 6), '⅛' to (1 to 8),
        '⅜' to (3 to 8), '⅝' to (5 to 8), '⅞' to (7 to 8),
    )
    private val VULGAR_CLASS = VULGAR.keys.joinToString("")
    private val AMOUNT = "(?:\\d+\\s+\\d+/\\d+|\\d+/\\d+|\\d*[$VULGAR_CLASS]|\\d+(?:\\.\\d+)?)"

    private val MIXED = Regex("^(\\d+)\\s+(\\d+)/(\\d+)$")
    private val FRACTION = Regex("^(\\d+)/(\\d+)$")
    private val VULGAR_RX = Regex("^(\\d*)([$VULGAR_CLASS])$")
    private val DECIMAL = Regex("^\\d+(?:\\.\\d+)?$")
    private val INTEGER = Regex("^\\d+$")

    /** An amount as a decimal string: integers as they are, others to 3 decimals without trailing zeros. */
    fun amountValue(typed: String): String? {
        val t = typed.trim()
        MIXED.find(t)?.let { m ->
            val d = m.groupValues[3].toDouble()
            if (d == 0.0) return null
            return finish(m.groupValues[1].toDouble() + m.groupValues[2].toDouble() / d)
        }
        FRACTION.find(t)?.let { m ->
            val d = m.groupValues[2].toDouble()
            if (d == 0.0) return null
            return finish(m.groupValues[1].toDouble() / d)
        }
        VULGAR_RX.find(t)?.let { m ->
            val (n, d) = VULGAR.getValue(m.groupValues[2][0])
            return finish((m.groupValues[1].toDoubleOrNull() ?: 0.0) + n.toDouble() / d)
        }
        if (DECIMAL.matches(t)) {
            if (INTEGER.matches(t)) return t.toBigInteger().toString()
            return finish(t.toDouble())
        }
        return null
    }

    private fun finish(value: Double): String? {
        if (value.isNaN() || value.isInfinite() || value <= 0) return null
        return formatAmount(value)
    }

    fun formatAmount(value: Double): String {
        if (value % 1.0 == 0.0 && Math.abs(value) < 1e15) return value.toLong().toString()
        return String.format(Locale.ROOT, "%.3f", value).trimEnd('0').trimEnd('.')
    }

    private fun isBareInteger(typed: String) = INTEGER.matches(typed.trim())

    // ---------------------------------------------------------------- the item key

    private val MARKS = Regex("\\p{M}+")
    private val EDGES = Regex("^[^\\p{L}\\p{N}]+|[^\\p{L}\\p{N}]+$")
    private val SPACES = Regex("\\s+")
    private val FOLD_ES = Regex("(xes|ches|shes|sses)$")
    private val KEEP_S = Regex("(ss|us|is)$")

    /** The item key: duplicates, learned order and usual items match on it. */
    fun itemKey(text: String): String {
        var s = Normalizer.normalize(text, Normalizer.Form.NFKC).lowercase(Locale.ROOT)
        s = Normalizer.normalize(MARKS.replace(Normalizer.normalize(s, Normalizer.Form.NFD), ""), Normalizer.Form.NFC)
        s = SPACES.replace(EDGES.replace(s, ""), " ")
        if (s.isEmpty()) return ""
        val words = s.split(' ').toMutableList()
        words[words.size - 1] = foldPlural(words.last())
        return words.joinToString(" ")
    }

    private fun foldPlural(w: String): String = when {
        w.length >= 5 && w.endsWith("ies") -> w.dropLast(3) + "y"
        FOLD_ES.containsMatchIn(w) -> w.dropLast(2)
        w.length >= 7 && w.endsWith("oes") -> w.dropLast(2)
        w.length >= 4 && w.endsWith("s") && !KEEP_S.containsMatchIn(w) -> w.dropLast(1)
        else -> w
    }

    // ---------------------------------------------------------------- sections and lists

    private fun squash(s: String) = SPACES.replace(s.lowercase(Locale.ROOT), "")

    /** A typed section name against the list's sections: exact (case and spaces ignored), else the only one that starts with it (3 characters or more). */
    fun matchSection(typed: String, sections: List<String>): String? {
        val t = squash(typed)
        if (t.isEmpty()) return null
        sections.firstOrNull { squash(it) == t }?.let { return it }
        if (t.length < 3) return null
        val hits = sections.filter { squash(it).startsWith(t) }
        return hits.singleOrNull()
    }

    /** A typed `#list` name: exact first, else the lists whose name starts with it, shortest first. */
    fun matchList(typed: String, lists: List<ListInfo>): ListInfo? {
        val t = squash(typed)
        if (t.isEmpty()) return null
        lists.firstOrNull { squash(it.name) == t }?.let { return it }
        return lists.filter { squash(it.name).startsWith(t) }.minByOrNull { it.name.length }
    }

    // ---------------------------------------------------------------- the parser

    private data class Span(val start: Int, val end: Int)

    private fun mask(work: CharArray, start: Int, end: Int) {
        for (i in start until end) work[i] = ' '
    }

    private val SECTION_NAME = Regex("^\\p{L}[^\\s:]*(?: [^\\s:]+){0,3}$")

    private fun splitPieces(work: String, from: Int, to: Int): List<Span> {
        val out = ArrayList<Span>()
        var depth = 0
        var quote = false
        var start = from
        for (i in from until to) {
            val c = work[i]
            if (c == '"' || c == '“' || c == '”') quote = !quote
            else if (!quote && c == '(') depth++
            else if (!quote && c == ')') depth = maxOf(0, depth - 1)
            else if (!quote && depth == 0 && (c == ',' || c == ';')) {
                out += Span(start, i)
                start = i + 1
            }
        }
        out += Span(start, to)
        return out
    }

    private val QUOTES = Regex("\"[^\"]*\"?|“[^”]*”?")

    private fun blankQuotes(s: String): String = QUOTES.replace(s) { m -> "\u0001".repeat(m.value.length) }

    private class QtyHit(val qty: Qty, val token: Span, val text: Span)

    private fun rx(src: String) = Regex(src, setOf(RegexOption.IGNORE_CASE))

    private val LEAD_UNIT = rx("^($AMOUNT)\\s*($UNIT_RX)(?![\\p{L}\\p{N}])(?:\\s+of(?=\\s))?(?=\\s)")
    private val LEAD_TIMES = rx("^($AMOUNT)\\s*[x×](?=\\s)")
    private val LEAD_BARE = rx("^($AMOUNT)(?=\\s)")
    private val TRAIL_TIMES = rx("(?<=\\s)[x×]\\s*($AMOUNT)$")
    private val TRAIL_PAREN_UNIT = rx("(?<=\\s|^)\\(\\s*($AMOUNT)\\s*($UNIT_RX)\\s*\\)$")
    private val TRAIL_UNIT = rx("(?<=\\s)($AMOUNT)\\s*($UNIT_RX)$")
    private val TRAIL_PAREN = rx("(?<=\\s|^)\\(\\s*($AMOUNT)\\s*\\)$")
    private val UNIT_ONLY = rx("^(?:$UNIT_RX)$")
    private val BETWEEN_NUMBERS = rx("\\d\\s*[x×]\\s*\\d")

    private fun readQty(s: String, at: Int, keep: Set<String>): QtyHit? {
        val b = blankQuotes(s)
        // "2 x 4 lumber": an x between two numbers takes every quantity off the line.
        if (BETWEEN_NUMBERS.containsMatchIn(b)) return null
        fun hit(tokStart: Int, tokEnd: Int, amount: String, unit: String?, textStart: Int, textEnd: Int): QtyHit? {
            val tokText = s.substring(tokStart, tokEnd)
            if (tokText in keep) return null
            val value = amountValue(amount) ?: return null
            val body = s.substring(textStart, textEnd)
            if (body.trim().isEmpty()) return null
            val lead = body.length - body.trimStart().length
            val tail = body.length - body.trimEnd().length
            return QtyHit(Qty(value, unit, tokText), Span(at + tokStart, at + tokEnd), Span(at + textStart + lead, at + textEnd - tail))
        }
        val tries: List<() -> QtyHit?> = listOf(
            { LEAD_UNIT.find(b)?.let { m -> hit(0, m.value.length, m.groupValues[1], unitOf(m.groupValues[2]), m.value.length, s.length) } },
            { LEAD_TIMES.find(b)?.let { m -> hit(0, m.value.length, m.groupValues[1], null, m.value.length, s.length) } },
            {
                LEAD_BARE.find(b)?.let { m ->
                    when {
                        isBareInteger(m.groupValues[1]) && m.groupValues[1].toBigInteger() >= 100.toBigInteger() -> null
                        UNIT_ONLY.matches(b.substring(m.value.length).trim()) -> null
                        else -> hit(0, m.value.length, m.groupValues[1], null, m.value.length, s.length)
                    }
                }
            },
            { TRAIL_TIMES.find(b)?.let { m -> hit(m.range.first, s.length, m.groupValues[1], null, 0, m.range.first) } },
            { TRAIL_PAREN_UNIT.find(b)?.let { m -> hit(m.range.first, s.length, m.groupValues[1], unitOf(m.groupValues[2]), 0, m.range.first) } },
            { TRAIL_UNIT.find(b)?.let { m -> hit(m.range.first, s.length, m.groupValues[1], unitOf(m.groupValues[2]), 0, m.range.first) } },
            { TRAIL_PAREN.find(b)?.let { m -> hit(m.range.first, s.length, m.groupValues[1], null, 0, m.range.first) } },
        )
        for (t in tries) t()?.let { return it }
        return null
    }

    private val TRAILING_PUNCT = Regex("[\\s,;.]+$")

    /** Item text: trimmed, single inner spaces, a trailing `, ; .` removed. Case kept. */
    fun cleanText(s: String): String = TRAILING_PUNCT.replace(SPACES.replace(s, " ").trim(), "").trim()

    private fun trimTrailingPunct(s: String): String = TRAILING_PUNCT.replace(s, "")

    private val CHECKBOX = Regex("^(\\s*)((?:[-*•–]\\s+)?\\[([ xX])\\]|[✓✔])(\\s+|$)")
    private val CHECKED_BOX = Regex("\\[[xX]\\]$")
    private val BULLET = Regex("^(\\s*)([-*•–]|\\d+[.)])(\\s+)")
    private val LIST_TOKEN = Regex("(^|\\s)(#\\p{L}[\\p{L}\\p{N}_\\-/]*)")
    private val MD_HEADER = Regex("^(\\s*)(#{1,2}\\s+(\\S.*?))\\s*$")
    private val HEADER_PREFIX = Regex("^(\\s*)([^:]+):\\s*$")
    private val PREFIX = Regex("^(\\s*)([^:\\n]+?):(?=\\s|$)")

    /** Parse one input with the item grammar. */
    fun parse(
        input: String,
        mode: Mode,
        list: ListInfo? = null,
        lists: List<ListInfo> = emptyList(),
        defaultList: ListInfo? = null,
        keep: Set<String> = emptySet(),
        /** List mode: an unknown `Name:` prefix makes that section, as a header line does (the new-list composer). */
        prefixCreates: Boolean = false,
    ): Result {
        val tokens = ArrayList<Token>()
        val work = input.toCharArray()
        var listRef: ListRef? = null
        val sections = ArrayList<String>()

        if (mode == Mode.CAPTURE) {
            val m = LIST_TOKEN.find(input)
            if (m != null && m.groupValues[2] !in keep) {
                val start = m.range.first + m.groupValues[1].length
                val end = start + m.groupValues[2].length
                val typed = m.groupValues[2].substring(1)
                val found = matchList(typed, lists)
                listRef = if (found != null) ListRef(found.id, found.name, false) else ListRef(null, typed, true)
                found?.let { sections += it.sections }
                tokens += Token("list", start, end, m.groupValues[2])
                mask(work, start, end)
            } else if (defaultList != null) {
                listRef = ListRef(defaultList.id, defaultList.name, false)
                sections += defaultList.sections
            }
        } else {
            list?.let { sections += it.sections }
        }

        val items = ArrayList<Item>()
        val newSections = ArrayList<String>()

        if (mode == Mode.EDIT) {
            val t = String(work)
            val lead = t.length - t.trimStart().length
            val piece = trimTrailingPunct(t.trim())
            val q = readQty(input.substring(lead, lead + piece.length), lead, keep)
            if (q != null) tokens += Token("qty", q.token.start, q.token.end, q.qty.text)
            val span = q?.text ?: Span(lead, lead + piece.length)
            val text = cleanText(input.substring(span.start, span.end))
            if (text.isNotEmpty()) items += Item(text, q?.qty, null, false, span.start, span.end)
            return Result(null, items, emptyList(), tokens.sortedBy { it.start })
        }

        fun known() = sections + newSections
        var header: String? = null
        var lineStart = 0
        for (rawLine in input.split('\n')) {
            val from = lineStart
            val to = from + rawLine.length
            lineStart = to + 1
            var line = String(work, from, to - from)
            if (line.isBlank()) continue

            // A header line: "Name:" alone, or "## Name".
            val md = MD_HEADER.find(line)
            if (md != null && md.groupValues[2] !in keep) {
                val name = md.groupValues[3].trim()
                val start = from + md.groupValues[1].length
                tokens += Token("section", start, start + md.groupValues[2].length,
                    input.substring(start, start + md.groupValues[2].length))
                header = headerSection(name, known(), newSections)
                continue
            }
            val hp = HEADER_PREFIX.find(line)
            if (hp != null && SECTION_NAME.matches(hp.groupValues[2].trim()) && hp.groupValues[2] == hp.groupValues[2].trimStart()) {
                val typed = hp.groupValues[2].trim()
                val start = from + hp.groupValues[1].length
                val tokText = input.substring(start, start + hp.groupValues[2].length + 1)
                if (tokText !in keep && tokText == "$typed:") {
                    tokens += Token("section", start, start + tokText.length, tokText)
                    header = headerSection(typed, known(), newSections)
                    continue
                }
            }

            // A checkbox or a bullet at the start of the line.
            var checked = false
            val cb = CHECKBOX.find(line)
            if (cb != null) {
                val start = from + cb.groupValues[1].length
                val mark = cb.groupValues[2]
                val isCheck = mark == "✓" || mark == "✔" || CHECKED_BOX.containsMatchIn(mark)
                if (isCheck && mark !in keep) {
                    checked = true
                    tokens += Token("check", start, start + mark.length, mark)
                }
                if (mark !in keep) mask(work, start, start + mark.length)
            } else {
                BULLET.find(line)?.let { bl ->
                    mask(work, from + bl.groupValues[1].length, from + bl.groupValues[1].length + bl.groupValues[2].length)
                }
            }
            line = String(work, from, to - from)

            // A section prefix: "Costco: milk, eggs".
            var section = header
            val pf = PREFIX.find(line)
            if (pf != null && SECTION_NAME.matches(pf.groupValues[2]) && line.substring(pf.value.length).isNotBlank()) {
                val hit = matchSection(pf.groupValues[2], known())
                val start = from + pf.groupValues[1].length
                val tokText = input.substring(start, start + pf.groupValues[2].length + 1)
                if ((hit != null || (prefixCreates && mode == Mode.LIST)) && tokText !in keep) {
                    section = hit ?: headerSection(pf.groupValues[2], known(), newSections)
                    tokens += Token("section", start, start + tokText.length, tokText)
                    mask(work, start, start + tokText.length)
                }
            }

            val w = String(work)
            for (p in splitPieces(w, from, to)) {
                val raw = w.substring(p.start, p.end)
                if (raw.isBlank()) continue
                val lead = raw.length - raw.trimStart().length
                val body = trimTrailingPunct(raw.trim())
                if (body.isEmpty()) continue
                val at = p.start + lead
                val original = input.substring(at, at + body.length)
                val q = readQty(original, at, keep)
                if (q != null) tokens += Token("qty", q.token.start, q.token.end, q.qty.text)
                val span = q?.text ?: Span(at, at + body.length)
                val text = cleanText(input.substring(span.start, span.end))
                // A masked list token inside the piece leaves its text out.
                val shown = cleanText(w.substring(span.start, span.end))
                val finalText = shown.ifEmpty { text }
                if (finalText.isEmpty()) continue
                items += Item(finalText, q?.qty, section, checked, span.start, span.end)
            }
        }
        return Result(listRef, items, newSections, tokens.sortedBy { it.start })
    }

    // ---------------------------------------------------------------- quick note

    /** What "New note…" makes of a text: items for a list, or a text note. */
    sealed interface Quick {
        data class Items(val result: Result) : Quick
        data class Note(val title: String, val body: String) : Quick
    }

    private val QUICK_LIST = Regex("^\\s*#\\p{L}[\\p{L}\\p{N}_\\-/]*")

    /**
     * "New note…" from anywhere (twin of backend/mailr/notes/itemadd.py `quick`): text that starts with
     * a `#List` token adds items to that list (capture mode with no default list; an unknown name makes
     * the list), anything else is a text note: the first line its title (500 characters at most), the
     * other lines, each trimmed, its text. A `#name` later in the text stays text.
     */
    fun quick(text: String, lists: List<ListInfo> = emptyList()): Quick {
        if (QUICK_LIST.containsMatchIn(text)) return Quick.Items(parse(text, Mode.CAPTURE, lists = lists))
        val lines = text.trim().split('\n').map { it.trim() }
        return Quick.Note(lines.first().take(500), lines.drop(1).joinToString("\n").trim())
    }

    private fun headerSection(typed: String, known: List<String>, created: MutableList<String>): String {
        matchSection(typed, known)?.let { return it }
        created += typed
        return typed
    }
}
