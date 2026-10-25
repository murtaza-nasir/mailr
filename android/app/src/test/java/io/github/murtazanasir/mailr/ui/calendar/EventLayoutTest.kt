package io.github.murtazanasir.mailr.ui.calendar

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.LocalDateTime

/** Runs spec/cases/layout-cases.yaml (the copy in test resources) against [EventLayout]. */
class EventLayoutTest {
    private val file = FlowYaml.read("interaction/layout-cases.yaml")

    private data class Ev(val id: String, val start: Int, val end: Int, val partstat: String?, val continued: Boolean)

    private fun min(s: String) = s.substring(0, 2).toInt() * 60 + s.substring(3).toInt()
    private fun hhmm(m: Int) = "%02d:%02d".format(m / 60, m % 60)

    @Test fun everyVectorPasses() {
        val failures = mutableListOf<String>()
        for (c in file["cases"]!!.jsonArray.map { it.jsonObject }) {
            val id = c["id"]!!.jsonPrimitive.content
            c["split"]?.let { split ->
                val evs = split.jsonArray.map { it.jsonObject }
                val segs = EventLayout.splitDays(evs, { LocalDateTime.parse(it["start"]!!.jsonPrimitive.content) },
                    { LocalDateTime.parse(it["end"]!!.jsonPrimitive.content) })
                val got = segs.map {
                    listOf(it.item["id"]!!.jsonPrimitive.content, it.date.toString(), hhmm(it.start), hhmm(it.end), "${it.continued}", "${it.continues}")
                }
                val want = c["segments"]!!.jsonArray.map { s ->
                    val o = s.jsonObject
                    listOf("id", "date", "start", "end", "continued", "continues").map { o[it]!!.jsonPrimitive.content }
                }
                if (got != want) failures += "$id: segments $got != $want"
                return@let
            } ?: run {
                val evs = c["events"]!!.jsonArray.map {
                    val o = it.jsonObject
                    Ev(o["id"]!!.jsonPrimitive.content, min(o["start"]!!.jsonPrimitive.content), min(o["end"]!!.jsonPrimitive.content),
                        (o["partstat"] as? JsonPrimitive)?.content, o["continued"]?.jsonPrimitive?.booleanOrNull ?: false)
                }
                val day = EventLayout.layoutDay(evs, { it.id }, { it.start }, { it.end }, { it.partstat }, { it.continued })
                val byId = day.placed.associateBy { it.item.id }
                if (day.placed.size + day.chips.sumOf { it.items.size } != evs.size) failures += "$id: events lost"
                c["hidden"]?.jsonArray?.forEach { h -> if (h.jsonPrimitive.content in byId) failures += "$id: ${h.jsonPrimitive.content} is shown" }
                for ((eid, want) in c["expect"]!!.jsonObject) {
                    val p = byId[eid]
                    if (p == null) {
                        failures += "$id: $eid not placed"
                        continue
                    }
                    for ((k, v) in (want as JsonObject)) {
                        val got: Any = when (k) {
                            "col" -> p.col; "cols" -> p.cols; "span" -> p.span
                            "top" -> hhmm(p.top); "bottom" -> hhmm(p.bottom)
                            "compact" -> p.compact; "continued" -> p.continued
                            "style" -> p.style.name.lowercase()
                            else -> "unknown field $k"
                        }
                        val exp: Any = v.jsonPrimitive.let { pr -> pr.booleanOrNull ?: if (pr.isString) pr.content else pr.int }
                        if (got != exp) failures += "$id: $eid.$k = $got, expected $exp"
                    }
                }
                val chips = day.chips.map { ch -> Triple(ch.items.map { it.id }, hhmm(ch.top), hhmm(ch.bottom)) }
                val wantChips = c["chips"]?.jsonArray?.map { ch ->
                    val o = ch.jsonObject
                    Triple(o["ids"]!!.jsonArray.map { it.jsonPrimitive.content }, o["top"]!!.jsonPrimitive.content, o["bottom"]!!.jsonPrimitive.content)
                }.orEmpty()
                if (chips != wantChips) failures += "$id: chips $chips != $wantChips"
                if (day.chips.any { it.col != 3 || it.cols != 4 }) failures += "$id: a chip is not in the fourth quarter"
            }
        }
        assertEquals(failures.joinToString("\n"), 0, failures.size)
    }
}
