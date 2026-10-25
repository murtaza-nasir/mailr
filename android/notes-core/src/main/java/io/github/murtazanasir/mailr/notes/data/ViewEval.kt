package io.github.murtazanasir.mailr.notes.data

import io.github.murtazanasir.mailr.util.catching

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import java.math.BigDecimal
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.ZoneOffset
import java.time.temporal.ChronoUnit
import java.util.Locale

/**
 * A table view evaluated on the phone: the rows it shows and their order, as the server's
 * backend/mailr/tables/views.py has them (clean_spec, apply_filters, sort_rows). The table screen
 * uses it while the phone is offline, so an edit made there moves the row where the view puts it.
 * Both run the vectors in spec/cases/table-view-cases.yaml (TableViewCasesTest and
 * backend/tests/test_table_view_cases.py).
 */
object ViewEval {
    /** The reader: today and the zone (a datetime cell's day), and the order of numeric dates. */
    data class Ctx(val today: LocalDate, val tz: ZoneId, val dateOrder: String = "mdy")

    /**
     * The rows of a view: [filters] (all must match) and then [sort] (up to three keys, empty cells
     * last in either direction, then the manual order). [titles]: link titles by reference key
     * ("row:<id>"), for sorting a link column. A filter the server would refuse is left out.
     */
    fun rows(rows: List<Row>, columns: List<Column>, sort: List<JsonObject>, filters: List<JsonObject>, ctx: Ctx,
             titles: Map<String, String> = emptyMap()): List<Row> {
        val cols = columns.associateBy { it.id }
        val fs = filters.take(30).mapNotNull { f -> clean(f, cols) }
        val keys = sort.take(3).mapNotNull { k ->
            val c = cols[k.str("col")] ?: return@mapNotNull null
            c to (k.str("dir")?.lowercase() == "desc")
        }
        val kept = rows.filter { r -> fs.all { (c, op, want) -> match(c, op, want, r.cells[c.id], ctx) } }
        return kept.sortedWith { a, b ->
            for ((c, desc) in keys) {
                val va = a.cells[c.id]
                val vb = b.cells[c.id]
                val ea = isEmpty(va)
                val eb = isEmpty(vb)
                if (ea && eb) continue
                if (ea != eb) return@sortedWith if (ea) 1 else -1
                val n = compareKeys(sortKey(c, va!!, titles), sortKey(c, vb!!, titles))
                if (n != 0) return@sortedWith if (desc) -n else n
            }
            a.order.compareTo(b.order).takeIf { it != 0 } ?: a.id.compareTo(b.id)
        }
    }

    /** Rows holding every word of [q] in some cell's text (the table's own search, read on the phone). */
    fun search(rows: List<Row>, columns: List<Column>, q: String, text: (Column, JsonElement?) -> String): List<Row> {
        val words = q.split(Regex("\\s+")).filter { it.isNotEmpty() }.map { fold(it) }
        if (words.isEmpty()) return rows
        return rows.filter { r ->
            val all = fold(columns.joinToString(" ") { c -> text(c, r.cells[c.id]) })
            words.all { it in all }
        }
    }

    // ------------------------------------------------------------ the spec

    private val OPS: Map<String, Set<String>> = mapOf(
        "text" to setOf("contains", "not_contains", "is", "is_not", "starts_with", "ends_with", "empty", "not_empty"),
        "number" to setOf("eq", "ne", "lt", "lte", "gt", "gte", "between", "empty", "not_empty"),
        "date" to setOf("is", "before", "after", "on_or_before", "on_or_after", "between", "last_days", "next_days", "empty", "not_empty"),
        "checkbox" to setOf("checked", "not_checked"),
        "select" to setOf("is", "is_not", "any_of", "none_of", "empty", "not_empty"),
        "multi_select" to setOf("has_any", "has_all", "has_none", "empty", "not_empty"),
        "link" to setOf("links_to", "not_links_to", "empty", "not_empty"),
    )
    private val ALIASES: Map<String, Map<String, String>> = mapOf(
        "text" to mapOf("eq" to "is", "ne" to "is_not", "startswith" to "starts_with", "endswith" to "ends_with", "=" to "is", "!=" to "is_not"),
        "number" to mapOf("=" to "eq", "!=" to "ne", "<" to "lt", "<=" to "lte", ">" to "gt", ">=" to "gte", "is" to "eq", "is_not" to "ne"),
        "date" to mapOf("eq" to "is", "gt" to "after", "gte" to "on_or_after", "lt" to "before", "lte" to "on_or_before", "=" to "is"),
        "select" to mapOf("eq" to "is", "ne" to "is_not", "in" to "any_of", "not_in" to "none_of"),
        "multi_select" to mapOf("contains" to "has_any", "is" to "has_any", "any_of" to "has_any", "none_of" to "has_none"),
        "checkbox" to mapOf("is" to "checked", "eq" to "checked"),
        "link" to mapOf("is" to "links_to", "contains" to "links_to"),
    )

    private fun family(t: String) = when (t) {
        "number", "currency" -> "number"
        "date", "datetime" -> "date"
        else -> t
    }

    private data class Filter(val col: Column, val op: String, val want: JsonElement?)

    private fun clean(f: JsonObject, cols: Map<String, Column>): Filter? {
        val c = cols[f.str("col")] ?: return null
        val raw = (f["op"] as? JsonPrimitive)?.contentOrNull.orEmpty().trim().lowercase().replace(" ", "_")
        val op = ALIASES[family(c.type)]?.get(raw) ?: raw
        if (op !in OPS[family(c.type)].orEmpty()) return null
        val want = f["value"]
        if (op !in setOf("empty", "not_empty", "checked", "not_checked") &&
            (want == null || want is JsonNull || (want as? JsonPrimitive)?.let { it.isString && it.content.isEmpty() } == true)) return null
        return Filter(c, op, want)
    }

    // ------------------------------------------------------------ matching

    private class Refused : Exception()

    private fun match(c: Column, op: String, want: JsonElement?, v: JsonElement?, ctx: Ctx): Boolean = try {
        matchOrRefuse(c, op, want, v, ctx)
    } catch (_: Refused) {
        true // The server refuses the whole view; the phone leaves the filter out.
    }

    private fun matchOrRefuse(c: Column, op: String, want: JsonElement?, v: JsonElement?, ctx: Ctx): Boolean {
        val empty = isEmpty(v)
        if (op == "empty") return empty
        if (op == "not_empty") return !empty
        when (c.type) {
            "checkbox" -> return if (op == "checked") truthy(v) else !truthy(v)
            "text" -> {
                val s = fold(if (empty) "" else prim(v!!))
                val w = fold(want?.let(::str).orEmpty())
                return when (op) {
                    "contains" -> w in s
                    "not_contains" -> w !in s
                    "is" -> s == w
                    "is_not" -> s != w
                    "starts_with" -> s.startsWith(w)
                    "ends_with" -> s.endsWith(w)
                    else -> true
                }
            }
            "select", "multi_select" -> {
                val have = if (empty) emptySet() else listOf(v!!).flattenArray().map(::str).toSet()
                val ids = optionIds(c, want)
                return when (op) {
                    "is", "any_of", "has_any" -> have.any { it in ids }
                    "is_not", "none_of", "has_none" -> have.none { it in ids }
                    "has_all" -> have.containsAll(ids)
                    else -> true
                }
            }
            "link" -> {
                val have = if (empty) emptySet() else (v as? JsonArray).orEmpty().mapNotNull { (it as? JsonObject)?.let(::refKey) }.toSet()
                val keys = listOfNotNull(want).flattenArray().map { if (it is JsonObject) refKey(it) else str(it) }.toSet()
                return if (op == "links_to") have.any { it in keys } else have.none { it in keys }
            }
        }
        if (empty) return op == "ne" || op == "is_not"
        when (c.type) {
            "number", "currency" -> {
                val x = prim(v!!).trim().toBigDecimalOrNull() ?: return false
                if (op == "between") {
                    val (lo, hi) = pair(want)
                    return num(lo) <= x && x <= num(hi)
                }
                val w = num(want)
                return when (op) {
                    "eq" -> x.compareTo(w) == 0
                    "ne" -> x.compareTo(w) != 0
                    "lt" -> x < w
                    "lte" -> x <= w
                    "gt" -> x > w
                    "gte" -> x >= w
                    else -> true
                }
            }
            "date", "datetime" -> {
                val d = dayOf(c, v!!, ctx.tz) ?: return false
                if (op == "last_days" || op == "next_days") {
                    val n = num(want).toLong()
                    return if (op == "last_days") !d.isBefore(ctx.today.minusDays(n)) && !d.isAfter(ctx.today)
                    else !d.isBefore(ctx.today) && !d.isAfter(ctx.today.plusDays(n))
                }
                if (op == "between") {
                    val (lo, hi) = pair(want)
                    return !d.isBefore(dateValue(lo, ctx)) && !d.isAfter(dateValue(hi, ctx))
                }
                val w = dateValue(want, ctx)
                return when (op) {
                    "is" -> d == w
                    "before" -> d.isBefore(w)
                    "after" -> d.isAfter(w)
                    "on_or_before" -> !d.isAfter(w)
                    "on_or_after" -> !d.isBefore(w)
                    else -> true
                }
            }
        }
        return true
    }

    /** Option ids from a filter value: ids or names (any case), one or a list. */
    private fun optionIds(c: Column, want: JsonElement?): Set<String> {
        val opts = options(c)
        return listOfNotNull(want).flattenArray().map { x ->
            val s = str(x)
            (opts.firstOrNull { it.first == s } ?: opts.firstOrNull { it.second.trim().lowercase() == s.trim().lowercase() })?.first ?: s
        }.toSet()
    }

    private fun options(c: Column): List<Pair<String, String>> = (c.config["options"] as? JsonArray).orEmpty().mapNotNull { o ->
        val obj = o as? JsonObject ?: return@mapNotNull null
        (obj.str("id") ?: return@mapNotNull null) to obj.str("name").orEmpty()
    }

    private fun pair(want: JsonElement?): Pair<JsonElement?, JsonElement?> {
        val l = (want as? JsonArray)?.toList() ?: listOf(want, want)
        return l.getOrNull(0) to l.getOrNull(1)
    }

    private fun num(v: JsonElement?): BigDecimal {
        val p = v as? JsonPrimitive ?: throw Refused()
        if (!p.isString && p.booleanOrNull != null) throw Refused()
        return p.content.trim().toBigDecimalOrNull() ?: parseNumber(p.content) ?: throw Refused()
    }

    private fun dateValue(v: JsonElement?, ctx: Ctx): LocalDate = parseDate(v?.let(::str) ?: throw Refused(), ctx) ?: throw Refused()

    // ------------------------------------------------------------ sorting

    private fun sortKey(c: Column, v: JsonElement, titles: Map<String, String>): List<Comparable<*>> = when (c.type) {
        "text" -> listOf(fold(prim(v)))
        "number", "currency" -> listOf(prim(v).trim().toBigDecimalOrNull() ?: BigDecimal.ZERO)
        "date" -> listOf(prim(v))
        "datetime" -> listOf(instant(v as JsonObject)?.toEpochSecond() ?: 0L)
        "checkbox" -> listOf(if (truthy(v)) 1 else 0)
        "select", "multi_select" -> {
            val idx = options(c).mapIndexed { i, o -> o.first to i }.toMap()
            listOf(v).flattenArray().map { idx[str(it)] ?: 999 }
        }
        "link" -> (v as? JsonArray).orEmpty().map { r -> fold(titles[(r as? JsonObject)?.let(::refKey)].orEmpty()) }
        else -> listOf(v.toString())
    }

    /** Python's tuple order: element by element, a shorter tuple first when it is a prefix. */
    @Suppress("UNCHECKED_CAST")
    private fun compareKeys(a: List<Comparable<*>>, b: List<Comparable<*>>): Int {
        for (i in 0 until minOf(a.size, b.size)) {
            val n = (a[i] as Comparable<Any>).compareTo(b[i] as Any)
            if (n != 0) return n
        }
        return a.size.compareTo(b.size)
    }

    // ------------------------------------------------------------ values

    /** Empty as the server reads it: missing, null, "", [], {} or false. */
    fun isEmpty(v: JsonElement?): Boolean = when (v) {
        null, JsonNull -> true
        is JsonPrimitive -> (v.isString && v.content.isEmpty()) || (!v.isString && v.content == "false")
        is JsonArray -> v.isEmpty()
        is JsonObject -> v.isEmpty()
    }

    private fun truthy(v: JsonElement?): Boolean = !isEmpty(v)

    private fun prim(v: JsonElement): String = (v as? JsonPrimitive)?.content ?: v.toString()

    private fun str(v: JsonElement): String = (v as? JsonPrimitive)?.content ?: v.toString()

    private fun List<JsonElement>.flattenArray(): List<JsonElement> = flatMap { if (it is JsonArray) it.toList() else listOf(it) }

    private fun JsonObject.str(k: String) = (this[k] as? JsonPrimitive)?.contentOrNull

    /** Python's str.casefold, for what cells hold (ß folds to ss). */
    private fun fold(s: String) = s.lowercase(Locale.ROOT).replace("ß", "ss")

    /** The lookup key of a link reference ("row:<uuid>", "conversation:12", "event:<uid>", "note:<id>"). */
    fun refKey(ref: JsonObject): String = when (val k = ref.str("kind")) {
        "row" -> "row:${ref.str("row")}"
        "conversation" -> "conversation:${ref.str("conversation_id")}"
        "event", "task" -> "$k:${ref.str("uid")}"
        else -> "note:${ref.str("id")}"
    }

    private fun localAt(at: String): LocalDateTime? =
        catching { LocalDateTime.parse(at) }.getOrNull() ?: catching { OffsetDateTime.parse(at).toLocalDateTime() }.getOrNull()

    /** A datetime cell ({at, tz}) as an instant: its wall time in its own zone (UTC when the zone is unknown). */
    private fun instant(v: JsonObject): OffsetDateTime? {
        val at = localAt(v.str("at") ?: return null) ?: return null
        val zone = catching { ZoneId.of(v.str("tz") ?: "UTC") }.getOrDefault(ZoneOffset.UTC)
        return at.atZone(zone).toOffsetDateTime()
    }

    /** The date of a date cell, or of a datetime cell in the reader's zone. */
    private fun dayOf(c: Column, v: JsonElement, tz: ZoneId): LocalDate? {
        if (c.type == "date") return catching { LocalDate.parse(prim(v)) }.getOrNull()
        val o = v as? JsonObject ?: return null
        return instant(o)?.atZoneSameInstant(tz)?.toLocalDate() ?: localAt(o.str("at").orEmpty())?.toLocalDate()
    }

    // ------------------------------------------------------------ typed values (types.py)

    // No U inline flag: Android's ICU engine rejects it (opening a table crashed, 8 Oct 2026); ICU's \d and \s are Unicode already.
    private val NUM_TEXT = Regex("^\\(?[-+−]?\\s*[^\\d\\s.,()-]{0,4}\\s*[-+−]?[\\d.,'\\s ]*\\d[\\d.,'\\s ]*\\s*[^\\d\\s.,()]{0,4}\\)?%?$")

    /** A number from text: currency symbols and codes, thousands separators and (12) for negatives. */
    fun parseNumber(text: String): BigDecimal? {
        var t = text.trim().replace("−", "-")
        if (t.isEmpty() || !NUM_TEXT.matches(t)) return null
        val neg = t.startsWith("(") && t.endsWith(")")
        t = t.trim('(', ')')
        val pct = t.endsWith("%")
        var core = t.filter { it.isDigit() || it in ".,-+" }
        if (core.count { it == '-' } + core.count { it == '+' } > 1) return null
        val sign = if ('-' in core) -1 else 1
        core = core.replace("-", "").replace("+", "")
        if (',' in core && '.' in core) {
            core = if (core.lastIndexOf(',') > core.lastIndexOf('.')) core.replace(".", "").replace(",", ".") else core.replace(",", "")
        } else if (',' in core) {
            val parts = core.split(',')
            core = if (parts.size == 2 && parts[1].length != 3) core.replace(",", ".") else core.replace(",", "")
        }
        if (core.count { it == '.' } > 1) core = core.replace(".", "")
        var d = core.toBigDecimalOrNull() ?: return null
        if (pct) d = d.divide(BigDecimal(100))
        return d.multiply(BigDecimal(sign * if (neg) -1 else 1))
    }

    private val MONTHS: Map<String, Int> = listOf(
        listOf("jan", "january"), listOf("feb", "february"), listOf("mar", "march"), listOf("apr", "april"), listOf("may"),
        listOf("jun", "june"), listOf("jul", "july"), listOf("aug", "august"), listOf("sep", "sept", "september"),
        listOf("oct", "october"), listOf("nov", "november"), listOf("dec", "december"),
    ).flatMapIndexed { i, ms -> ms.map { it to i + 1 } }.toMap()
    private val WEEKDAYS: Map<String, Int> = listOf(
        listOf("mon", "monday"), listOf("tue", "tues", "tuesday"), listOf("wed", "weds", "wednesday"),
        listOf("thu", "thur", "thurs", "thursday"), listOf("fri", "friday"), listOf("sat", "saturday"), listOf("sun", "sunday"),
    ).flatMapIndexed { i, ws -> ws.map { it to i } }.toMap()
    private val RELATIVE = mapOf("today" to 0, "tod" to 0, "now" to 0, "tomorrow" to 1, "tom" to 1, "tmr" to 1, "tmrw" to 1,
        "yesterday" to -1, "yest" to -1)
    private val ISO = Regex("(\\d{4})[-/.](\\d{1,2})[-/.](\\d{1,2})(?:[t ][\\d:.]+(?:z|[+-]\\d{2}:?\\d{2})?)?")
    private val NUMERIC = Regex("(\\d{1,2})[-/.](\\d{1,2})(?:[-/.](\\d{2}|\\d{4}))?")

    private fun ymd(y: Int, m: Int, d: Int): LocalDate? = catching { LocalDate.of(if (y < 100) y + 2000 else y, m, d) }.getOrNull()

    /**
     * A date from text: ISO dates, numeric dates in the date order (12/10/2026), month names (12 oct,
     * Oct 12, 2026) and the quick-add words (today, tom, yesterday, fri: the next Friday after today).
     */
    fun parseDate(text: String, ctx: Ctx): LocalDate? {
        val t = text.trim().lowercase(Locale.ROOT).trimEnd('.')
        if (t.isEmpty()) return null
        ISO.matchEntire(t)?.let { m -> return ymd(m.groupValues[1].toInt(), m.groupValues[2].toInt(), m.groupValues[3].toInt()) }
        NUMERIC.matchEntire(t)?.let { m ->
            val a = m.groupValues[1].toInt()
            val b = m.groupValues[2].toInt()
            val (mo, d) = if (ctx.dateOrder == "dmy") b to a else a to b
            val y = m.groupValues[3].takeIf { it.isNotEmpty() }?.toInt() ?: ctx.today.year
            return ymd(y, mo, d)
        }
        RELATIVE[t]?.let { return ctx.today.plusDays(it.toLong()) }
        WEEKDAYS[t]?.let { wd ->
            val delta = Math.floorMod(wd - (ctx.today.dayOfWeek.value - 1), 7).let { if (it == 0) 7 else it }
            return ctx.today.plus(delta.toLong(), ChronoUnit.DAYS)
        }
        val words = Regex("[a-z]+|\\d+").findAll(t).map { it.value }.toList()
        val nums = words.filter { w -> w.all { it.isDigit() } }.map { it.toInt() }
        val mons = words.mapNotNull { MONTHS[it] }
        val rest = words.filter { w -> !w.all { it.isDigit() } && w !in MONTHS && w !in setOf("st", "nd", "rd", "th", "of") && w !in WEEKDAYS }
        if (mons.size == 1 && nums.size in 1..2 && rest.isEmpty()) {
            var d = nums[0]
            var y = if (nums.size == 2) nums[1] else ctx.today.year
            if (d > 31 && nums.size == 2) { d = nums[1]; y = nums[0] }
            return ymd(y, mons[0], d)
        }
        return null
    }
}
