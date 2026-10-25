package io.github.murtazanasir.mailr.ui.agenda

/**
 * The glass timeline's geometry (AgendaStyle.GLASS): which hours the day column shows, and where each
 * of today's events sits in it. Pure arithmetic, so the tests check it without drawing.
 */
object AgendaTimeline {
    /** A block on the timeline: [top] and [height] in dp from the column's top; column [col] of [cols]. */
    data class Block(val timed: AgendaModel.Timed, val top: Float, val height: Float, val col: Int, val cols: Int)

    /** An hour line with its label, [y] dp from the top. */
    data class HourLine(val hour: Int, val y: Float, val label: Boolean)

    data class Layout(val startHour: Int, val endHour: Int, val blocks: List<Block>, val hours: List<HourLine>, val nowY: Float?)

    /**
     * The hours shown: [span] hours (as many as fit, GlassDims.hours; 13 at 4×3), starting at the first event or an hour
     * before now, whichever is earlier; when the day does not fit, the window moves later, but never
     * so far that now falls in its first third.
     */
    fun window(timeline: List<AgendaModel.Timed>, nowMinute: Int, span: Int): Pair<Int, Int> {
        val nowHour = nowMinute / 60
        val first = timeline.minOfOrNull { it.startMin / 60 } ?: nowHour
        val last = timeline.maxOfOrNull { (it.endMin + 59) / 60 } ?: nowHour
        var start = minOf(first, nowHour - 1)
        val need = maxOf(last, nowHour + 2)
        if (need - start > span) start = maxOf(start, minOf(nowHour - span / 3, need - span))
        start = start.coerceIn(0, 24 - span)
        return start to start + span
    }

    /**
     * The layout in a column [height] dp tall: hour lines every [step] hours (a label is left out
     * within 40 minutes of now, where the now-line sits), blocks at least [minBlock] dp tall, and
     * events that overlap split into side-by-side columns (each cluster of overlapping events as wide
     * as it needs; an event takes the first free column).
     */
    fun layout(timeline: List<AgendaModel.Timed>, nowMinute: Int, height: Float, span: Int, step: Int, minBlock: Float): Layout {
        val (start, end) = window(timeline, nowMinute, span)
        val perMin = height / ((end - start) * 60f)
        fun y(min: Int) = (min - start * 60) * perMin
        val hours = (start..end).filter { it % step == 0 }.map { h ->
            HourLine(h, y(h * 60), kotlin.math.abs(h * 60 - nowMinute) >= 40)
        }
        val shown = timeline.filter { it.endMin > start * 60 && it.startMin < end * 60 }.sortedWith(compareBy({ it.startMin }, { -it.endMin }))
        // Clusters of overlapping events, then the first free column in each.
        val blocks = mutableListOf<Block>()
        var cluster = mutableListOf<Pair<AgendaModel.Timed, Int>>()
        var clusterEnd = -1
        fun flush() {
            val cols = (cluster.maxOfOrNull { it.second } ?: -1) + 1
            cluster.forEach { (t, c) ->
                val top0 = y(maxOf(t.startMin, start * 60)).coerceAtLeast(0f)
                val bottom = y(minOf(maxOf(t.endMin, t.startMin + 1), end * 60))
                val h = minOf(maxOf(bottom - top0, minBlock), height)
                // A short block near the bottom moves up so it stays whole.
                blocks += Block(t, minOf(top0, height - h).coerceAtLeast(0f), h, c, cols)
            }
            cluster = mutableListOf()
            clusterEnd = -1
        }
        val colEnds = mutableListOf<Int>()
        val minMin = kotlin.math.ceil(minBlock / perMin).toInt()
        for (t in shown) {
            // Overlap is judged on the drawn extent, so blocks stretched to their minimum height never cover
            // each other: a short block near the bottom is drawn higher (see flush), and judged there.
            val drawnStart = minOf(t.startMin, end * 60 - minMin)
            val drawnEnd = maxOf(t.endMin, drawnStart + minMin)
            if (drawnStart >= clusterEnd) { flush(); colEnds.clear() }
            var c = colEnds.indexOfFirst { it <= drawnStart }
            if (c < 0) { c = colEnds.size; colEnds += drawnEnd } else colEnds[c] = drawnEnd
            cluster += t to c
            clusterEnd = maxOf(clusterEnd, drawnEnd)
        }
        flush()
        val nowY = if (nowMinute in start * 60..end * 60) y(nowMinute) else null
        return Layout(start, end, blocks, hours, nowY)
    }
}
