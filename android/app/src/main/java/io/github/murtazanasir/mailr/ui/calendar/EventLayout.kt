package io.github.murtazanasir.mailr.ui.calendar

import java.time.LocalDate
import java.time.LocalDateTime
import java.time.temporal.ChronoUnit

/** An event's place in a day column: minutes from midnight, plus its lane among overlaps. */
data class Placed<T>(val item: T, val startMin: Int, val endMin: Int, val lane: Int, val lanes: Int)

enum class BlockStyle { FILL, OUTLINE, HATCHED }

/**
 * A block of the overlap layout: its drawn extent in minutes (at least [EventLayout.MIN_MINUTES] tall),
 * its column, the cluster's columns (at most [EventLayout.MAX_COLUMNS]) and the columns it spans after
 * widening right. `compact` blocks show the title only; `continued` segments show "↳".
 */
data class PlacedBlock<T>(
    val item: T, val top: Int, val bottom: Int, val col: Int, val cols: Int, val span: Int,
    val compact: Boolean, val style: BlockStyle, val continued: Boolean,
)

/** A "+n" chip in the last quarter of a cluster that needs more than four columns. */
data class OverflowChip<T>(val items: List<T>, val top: Int, val bottom: Int, val col: Int, val cols: Int)

data class DayLayout<T>(val placed: List<PlacedBlock<T>>, val chips: List<OverflowChip<T>>)

/** One day's piece of a timed event, in minutes from that day's midnight. */
data class DaySegment<T>(val item: T, val date: LocalDate, val start: Int, val end: Int, val continued: Boolean, val continues: Boolean)

/**
 * The overlap layout of timed events. The web twin is frontend/src/lib/eventLayout.ts;
 * both run spec/cases/layout-cases.yaml.
 */
object EventLayout {
    /** Events shorter than this still get a tappable block. */
    const val MIN_MINUTES = 20
    const val COMPACT_BELOW = 30
    const val MAX_COLUMNS = 4

    /**
     * Side-by-side layout for one day: events that overlap (directly or through a chain) form a
     * cluster; each takes the first free lane, and the cluster's lane count sets the widths.
     * The older form of [layoutDay]: no widening and no "+n" chips.
     */
    fun <T> layout(items: List<T>, start: (T) -> Int, end: (T) -> Int): List<Placed<T>> {
        val sorted = items.map { Triple(it, start(it).coerceIn(0, 1440), maxOf(end(it), start(it) + MIN_MINUTES).coerceIn(0, 1440)) }
            .sortedWith(compareBy({ it.second }, { -(it.third - it.second) }))
        val out = mutableListOf<Placed<T>>()
        var cluster = mutableListOf<Placed<T>>()
        var laneEnds = mutableListOf<Int>()
        var clusterEnd = -1
        fun flush() {
            val n = laneEnds.size
            out += cluster.map { it.copy(lanes = n) }
            cluster = mutableListOf()
            laneEnds = mutableListOf()
        }
        for ((item, s, e) in sorted) {
            if (s >= clusterEnd && cluster.isNotEmpty()) flush()
            var lane = laneEnds.indexOfFirst { it <= s }
            if (lane < 0) {
                lane = laneEnds.size
                laneEnds += e
            } else laneEnds[lane] = e
            cluster += Placed(item, s, e, lane, 0)
            clusterEnd = if (cluster.size == 1) e else maxOf(clusterEnd, e)
        }
        if (cluster.isNotEmpty()) flush()
        return out
    }

    private class Box<T>(val item: T, val id: String, val s: Int, val e: Int, val partstat: String?, val continued: Boolean) {
        val top = s
        val bottom = minOf(1440, maxOf(e, s + MIN_MINUTES))
        var col = 0
    }

    private fun styleOf(partstat: String?) = when (partstat?.lowercase()) {
        "declined", "needs-action" -> BlockStyle.OUTLINE
        "tentative" -> BlockStyle.HATCHED
        else -> BlockStyle.FILL
    }

    /**
     * One day column:
     * 1. events that overlap (directly or through a chain, using their drawn extents) form a cluster;
     * 2. each cluster is packed greedily into columns, by start, then the longer event first, then id;
     * 3. a block widens right over the following columns while none of their blocks overlaps it;
     * 4. a cluster with more than [MAX_COLUMNS] columns shows the first three, and the blocks of the
     *    other columns go into "+n" chips in the fourth quarter (one chip per overlapping group);
     * 5. every block is at least [MIN_MINUTES] tall.
     */
    fun <T> layoutDay(
        items: List<T>, id: (T) -> String, start: (T) -> Int, end: (T) -> Int,
        partstat: (T) -> String? = { null }, continued: (T) -> Boolean = { false },
    ): DayLayout<T> {
        val boxes = items.map {
            val s = start(it).coerceIn(0, 1440)
            Box(it, id(it), s, end(it).coerceIn(s, 1440), partstat(it), continued(it))
        }.sortedWith(compareBy<Box<T>>({ it.top }, { -(it.e - it.s) }, { it.id }))
        val placed = mutableListOf<PlacedBlock<T>>()
        val chips = mutableListOf<OverflowChip<T>>()
        var cluster = mutableListOf<Box<T>>()
        var colEnds = mutableListOf<Int>()
        var clusterBottom = -1

        fun overlaps(a: Box<T>, b: Box<T>) = a.top < b.bottom && b.top < a.bottom
        fun flush() {
            val cols = colEnds.size
            val overflow = cols > MAX_COLUMNS
            val shown = if (overflow) MAX_COLUMNS - 1 else cols
            for (b in cluster) {
                if (b.col >= shown) continue
                var span = 1
                for (c in b.col + 1 until shown) {
                    if (cluster.any { it.col == c && overlaps(it, b) }) break
                    span++
                }
                placed += PlacedBlock(b.item, b.top, b.bottom, b.col, if (overflow) MAX_COLUMNS else cols, span,
                    b.e - b.s < COMPACT_BELOW, styleOf(b.partstat), b.continued)
            }
            if (overflow) {
                var group = mutableListOf<Box<T>>()
                var bottom = -1
                fun close() {
                    if (group.isNotEmpty()) chips += OverflowChip(group.map { it.item }, group.first().top, bottom, MAX_COLUMNS - 1, MAX_COLUMNS)
                    group = mutableListOf()
                }
                for (b in cluster) {
                    if (b.col < shown) continue
                    if (group.isNotEmpty() && b.top < bottom) {
                        group += b
                        bottom = maxOf(bottom, b.bottom)
                    } else {
                        close()
                        group += b
                        bottom = b.bottom
                    }
                }
                close()
            }
            cluster = mutableListOf()
            colEnds = mutableListOf()
        }

        for (b in boxes) {
            if (cluster.isNotEmpty() && b.top >= clusterBottom) flush()
            var col = colEnds.indexOfFirst { it <= b.top }
            if (col < 0) {
                col = colEnds.size
                colEnds += b.bottom
            } else colEnds[col] = b.bottom
            b.col = col
            cluster += b
            clusterBottom = if (cluster.size == 1) b.bottom else maxOf(clusterBottom, b.bottom)
        }
        if (cluster.isNotEmpty()) flush()
        return DayLayout(placed, chips)
    }

    /**
     * Splits timed events at local midnight (wall clock). An event that ends exactly at midnight stays
     * on its first day; a later segment is `continued` (drawn with "↳").
     */
    fun <T> splitDays(items: List<T>, start: (T) -> LocalDateTime, end: (T) -> LocalDateTime): List<DaySegment<T>> {
        val out = mutableListOf<DaySegment<T>>()
        for (item in items) {
            val s = start(item)
            val e = maxOf(s, end(item))
            val first = s.toLocalDate()
            val last = if (e.isAfter(s)) e.minusMinutes(1).toLocalDate() else first
            var d = first
            while (!d.isAfter(last)) {
                val dayStart = d.atStartOfDay()
                val a = maxOf(s, dayStart)
                val b = minOf(e, d.plusDays(1).atStartOfDay())
                out += DaySegment(item, d, ChronoUnit.MINUTES.between(dayStart, a).toInt(), ChronoUnit.MINUTES.between(dayStart, b).toInt(),
                    d.isAfter(first), d.isBefore(last))
                d = d.plusDays(1)
            }
        }
        return out
    }
}
