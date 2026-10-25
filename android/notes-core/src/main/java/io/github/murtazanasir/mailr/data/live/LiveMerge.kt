package io.github.murtazanasir.mailr.data.live

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * Three-way merge for live editing, a port of backend/mailr/core/merge3.py (the web app has the
 * same in frontend/src/lib/live/merge3.ts). The three share their test cases
 * (backend/tests/fixtures/merge3_cases.json, copied to the unit-test resources), so a merge gives
 * the same result everywhere.
 *
 * Text is split into units: "lines" after every line break (task notes, event descriptions), or
 * "html" after every closing block tag and every <br> (a draft's body). HTML units compare by a
 * normalised key (div and p alike, <br/> and <br> alike, class / style / dir attributes and
 * whitespace runs ignored); where a unit is unchanged the result keeps the "mine" spelling.
 *
 * The diff is a patience diff: units that occur once in both copies anchor the match, the gaps
 * between anchors are matched recursively, and a small gap without anchors by a longest common
 * subsequence table. Changes from the two sides merge when their base ranges do not overlap; an
 * insertion at the edge of the other side's change is not an overlap. Two changes to the same
 * units conflict, unless both made the same change.
 */
object LiveMerge {
    /** Gaps larger than this (units in one copy times units in the other) without a unique anchor are one change. */
    const val DP_LIMIT = 250_000

    // Python's \s differs from Java's (and ICU's): the whitespace class is spelled out.
    private const val WS = "[ \\t\\r\\n\\f]"
    private val IC = setOf(RegexOption.IGNORE_CASE)
    private val HTML_SPLIT = Regex("</(?:p|div|li|ul|ol|h[1-6]|blockquote|pre|table|tr)>|<br$WS*/?>|\\n", IC)
    private val K_NBSP = Regex("&nbsp;", IC)
    // Python's \b is Unicode-aware; the end of the word is spelled out so every engine agrees.
    private val K_DIV = Regex("<(/?)div(?![\\p{L}\\p{N}_])", IC)
    private val K_BR = Regex("<br$WS*/?>", IC)
    private val K_ATTR = Regex("$WS(?:class|style|dir)=\"[^\"]*\"", IC)
    private val K_SPACE = Regex("$WS+")

    /** The units of [text]; joined, they give [text] back. */
    fun tokens(text: String, mode: String = "lines"): List<String> {
        if (text.isEmpty()) return emptyList()
        val out = ArrayList<String>()
        var start = 0
        if (mode == "html") {
            for (m in HTML_SPLIT.findAll(text)) {
                val end = m.range.last + 1
                out += text.substring(start, end)
                start = end
            }
        } else {
            while (true) {
                val i = text.indexOf('\n', start)
                if (i < 0) break
                out += text.substring(start, i + 1)
                start = i + 1
            }
        }
        if (start < text.length) out += text.substring(start)
        return out
    }

    fun key(unit: String, mode: String = "lines"): String {
        if (mode != "html") return unit
        var s = K_NBSP.replace(unit, " ")
        s = K_DIV.replace(s) { m -> "<" + m.groupValues[1] + "p" }
        s = K_BR.replace(s, "<br>")
        s = K_ATTR.replace(s, "")
        return K_SPACE.replace(s, " ").trim(' ')
    }

    // ---------------------------------------------------------------- diff

    /** The longest increasing run of units that occur exactly once in a[a0:a1] and in b[b0:b1]. */
    private fun uniqueLcs(a: List<String>, a0: Int, a1: Int, b: List<String>, b0: Int, b1: Int): List<IntArray> {
        val ca = HashMap<String, Int>()
        val cb = HashMap<String, Int>()
        val pb = HashMap<String, Int>()
        for (i in a0 until a1) ca[a[i]] = (ca[a[i]] ?: 0) + 1
        for (j in b0 until b1) {
            cb[b[j]] = (cb[b[j]] ?: 0) + 1
            pb[b[j]] = j
        }
        val pairs = ArrayList<IntArray>()
        for (i in a0 until a1) if (ca[a[i]] == 1 && cb[a[i]] == 1) pairs += intArrayOf(i, pb[a[i]]!!)
        if (pairs.isEmpty()) return emptyList()
        // Patience: piles by the b index, each element remembers the top of the pile to its left.
        val tops = ArrayList<Int>()
        val back = IntArray(pairs.size) { -1 }
        for (k in pairs.indices) {
            val j = pairs[k][1]
            var lo = 0
            var hi = tops.size
            while (lo < hi) {
                val mid = (lo + hi) / 2
                if (pairs[tops[mid]][1] < j) lo = mid + 1 else hi = mid
            }
            back[k] = if (lo > 0) tops[lo - 1] else -1
            if (lo == tops.size) tops += k else tops[lo] = k
        }
        val out = ArrayList<IntArray>()
        var k = tops.last()
        while (k >= 0) {
            out += pairs[k]
            k = back[k]
        }
        out.reverse()
        return out
    }

    private fun dp(a: List<String>, a0: Int, a1: Int, b: List<String>, b0: Int, b1: Int, out: MutableList<IntArray>) {
        val n = a1 - a0
        val m = b1 - b0
        val w = m + 1
        val l = IntArray((n + 1) * w)
        for (i in n - 1 downTo 0) {
            for (j in m - 1 downTo 0) {
                l[i * w + j] = if (a[a0 + i] == b[b0 + j]) l[(i + 1) * w + j + 1] + 1
                else {
                    val x = l[(i + 1) * w + j]
                    val y = l[i * w + j + 1]
                    if (x >= y) x else y
                }
            }
        }
        var i = 0
        var j = 0
        while (i < n && j < m) {
            when {
                a[a0 + i] == b[b0 + j] -> { out += intArrayOf(a0 + i, b0 + j); i++; j++ }
                l[(i + 1) * w + j] >= l[i * w + j + 1] -> i++
                else -> j++
            }
        }
    }

    private fun match(a: List<String>, a0In: Int, a1In: Int, b: List<String>, b0In: Int, b1In: Int, out: MutableList<IntArray>) {
        var a0 = a0In
        var a1 = a1In
        var b0 = b0In
        var b1 = b1In
        while (a0 < a1 && b0 < b1 && a[a0] == b[b0]) {
            out += intArrayOf(a0, b0)
            a0++
            b0++
        }
        val suffix = ArrayList<IntArray>()
        while (a1 > a0 && b1 > b0 && a[a1 - 1] == b[b1 - 1]) {
            a1--
            b1--
            suffix += intArrayOf(a1, b1)
        }
        if (a0 < a1 && b0 < b1) {
            val anchors = uniqueLcs(a, a0, a1, b, b0, b1)
            if (anchors.isNotEmpty()) {
                var pa = a0
                var pb = b0
                for (p in anchors) {
                    match(a, pa, p[0], b, pb, p[1], out)
                    out += p
                    pa = p[0] + 1
                    pb = p[1] + 1
                }
                match(a, pa, a1, b, pb, b1, out)
            } else if ((a1 - a0).toLong() * (b1 - b0) <= DP_LIMIT) {
                dp(a, a0, a1, b, b0, b1, out)
            }
        }
        for (k in suffix.indices.reversed()) out += suffix[k]
    }

    /** Hunks (a0, a1, b0, b1): a[a0:a1] became b[b0:b1]. In order, never overlapping. */
    fun diff(a: List<String>, b: List<String>): List<IntArray> {
        val matches = ArrayList<IntArray>()
        match(a, 0, a.size, b, 0, b.size, matches)
        matches += intArrayOf(a.size, b.size)
        val hunks = ArrayList<IntArray>()
        var i = 0
        var j = 0
        for (mm in matches) {
            val mi = mm[0]
            val mj = mm[1]
            if (mi > i || mj > j) hunks += intArrayOf(i, mi, j, mj)
            i = mi + 1
            j = mj + 1
        }
        return hunks
    }

    // ---------------------------------------------------------------- merge

    data class Conflict(val base: String, val mine: String, val theirs: String)

    /** [text] is the merged text; where a conflict is, the "mine" side. */
    data class TextMerge(val ok: Boolean, val text: String, val conflicts: List<Conflict> = emptyList())

    private fun overlap(x: IntArray, y: IntArray): Boolean {
        val x0 = x[0]; val x1 = x[1]; val y0 = y[0]; val y1 = y[1]
        if (x0 == x1 && y0 == y1) return x0 == y0
        if (x0 == x1) return y0 < x0 && x0 < y1
        if (y0 == y1) return x0 < y0 && y0 < x1
        return maxOf(x0, y0) < minOf(x1, y1)
    }

    fun mergeText(baseIn: String?, mineIn: String?, theirsIn: String?, mode: String = "lines"): TextMerge {
        val base = baseIn ?: ""
        val mine = mineIn ?: ""
        val theirs = theirsIn ?: ""
        if (mine == theirs || theirs == base) return TextMerge(true, mine)
        if (mine == base) return TextMerge(true, theirs)
        val tb = tokens(base, mode)
        val tm = tokens(mine, mode)
        val tt = tokens(theirs, mode)
        val kb = tb.map { key(it, mode) }
        val km = tm.map { key(it, mode) }
        val kt = tt.map { key(it, mode) }
        // (a0, a1, b0, b1, side): side 0 is mine, 1 theirs.
        val hunks = (diff(kb, km).map { it + 0 } + diff(kb, kt).map { it + 1 })
            .sortedWith(compareBy<IntArray>({ it[0] }, { it[1] }, { it[4] }))
        val groups = ArrayList<MutableList<IntArray>>()
        for (h in hunks) {
            val last = groups.lastOrNull()
            if (last != null && last.any { g -> g[4] != h[4] && overlap(g, h) }) last += h else groups += mutableListOf(h)
        }
        val out = StringBuilder()
        val conflicts = ArrayList<Conflict>()
        var pos = 0
        var dm = 0  // mine / theirs index minus base index, outside the hunks seen so far
        var dt = 0
        for (g in groups) {
            val g0 = g.minOf { it[0] }
            val g1 = g.maxOf { it[1] }
            for (k in pos until g0) out.append(tm[k + dm])
            val em = dm + g.filter { it[4] == 0 }.sumOf { (it[3] - it[2]) - (it[1] - it[0]) }
            val et = dt + g.filter { it[4] == 1 }.sumOf { (it[3] - it[2]) - (it[1] - it[0]) }
            val m0 = g0 + dm; val m1 = g1 + em; val t0 = g0 + dt; val t1 = g1 + et
            val sides = g.map { it[4] }.toSet()
            when {
                sides == setOf(0) -> tm.subList(m0, m1).forEach(out::append)
                sides == setOf(1) -> tt.subList(t0, t1).forEach(out::append)
                km.subList(m0, m1) == kt.subList(t0, t1) -> tm.subList(m0, m1).forEach(out::append)
                else -> {
                    conflicts += Conflict(tb.subList(g0, g1).joinToString(""), tm.subList(m0, m1).joinToString(""),
                        tt.subList(t0, t1).joinToString(""))
                    tm.subList(m0, m1).forEach(out::append)
                }
            }
            pos = g1; dm = em; dt = et
        }
        for (k in pos until tb.size) out.append(tm[k + dm])
        return TextMerge(conflicts.isEmpty(), out.toString(), conflicts)
    }

    /** Equal values; a missing value, null and "" are the same, and so are null and []. */
    fun same(a: JsonElement?, b: JsonElement?): Boolean = norm(a) == norm(b)

    private fun norm(v: JsonElement?): JsonElement? = when {
        v == null || v is JsonNull -> null
        v is JsonPrimitive && v.isString && v.content.isEmpty() -> null
        v is JsonArray && v.isEmpty() -> null
        else -> v
    }

    /** [merged]: where a field conflicts, the "mine" value. [conflicts]: the fields both sides changed differently. */
    data class FieldMerge(
        val ok: Boolean,
        val merged: JsonObject,
        val conflicts: List<String> = emptyList(),
        val texts: Map<String, List<Conflict>> = emptyMap(),
    )

    private fun isText(v: JsonElement?) = v == null || v is JsonNull || (v is JsonPrimitive && v.isString)
    private fun str(v: JsonElement?): String? = (v as? JsonPrimitive)?.takeIf { it.isString }?.content

    /**
     * Per field: one side's change wins over an unchanged field; equal changes agree; text fields
     * named in [textFields] (field -> "lines" | "html") merge by units; any other field both sides
     * changed differently is a conflict.
     */
    fun mergeFields(baseIn: JsonObject?, mine: JsonObject, theirs: JsonObject, textFields: Map<String, String> = emptyMap()): FieldMerge {
        val base = baseIn ?: JsonObject(emptyMap())
        val merged = LinkedHashMap<String, JsonElement>()
        val conflicts = ArrayList<String>()
        val texts = LinkedHashMap<String, List<Conflict>>()
        for (k in LinkedHashSet<String>().apply { addAll(base.keys); addAll(mine.keys); addAll(theirs.keys) }) {
            val b = base[k]
            val m = mine[k]
            val t = theirs[k]
            val v: JsonElement? = when {
                same(m, b) -> if (k in theirs || !same(t, m)) t else m
                same(t, b) || same(m, t) -> m
                k in textFields && isText(b) && isText(m) && isText(t) -> {
                    val r = mergeText(str(b), str(m), str(t), textFields.getValue(k))
                    if (!r.ok) {
                        conflicts += k
                        texts[k] = r.conflicts
                    }
                    JsonPrimitive(r.text)
                }
                else -> {
                    conflicts += k
                    m
                }
            }
            if (k in mine || k in theirs) merged[k] = v ?: JsonNull
        }
        return FieldMerge(conflicts.isEmpty(), JsonObject(merged), conflicts, texts)
    }
}
