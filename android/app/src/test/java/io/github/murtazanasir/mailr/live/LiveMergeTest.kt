package io.github.murtazanasir.mailr.live

import io.github.murtazanasir.mailr.data.live.LiveMerge
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The three-way merge of live editing (data/live/LiveMerge.kt) against the cases it shares with
 * the server (backend/tests/fixtures/merge3_cases.json, copied byte for byte into the test
 * resources) and the web app: all three implementations must give these results.
 */
class LiveMergeTest {
    private val cases: JsonObject =
        Json.parseToJsonElement(javaClass.classLoader!!.getResource("merge3_cases.json")!!.readText()).jsonObject

    private fun JsonObject.s(k: String) = this[k]!!.jsonPrimitive.content

    @Test fun textCases() {
        val all = cases["text"]!!.jsonArray
        assertTrue(all.size >= 15)
        for (el in all) {
            val c = el.jsonObject
            val r = LiveMerge.mergeText(c.s("base"), c.s("mine"), c.s("theirs"), c.s("mode"))
            val name = c.s("name")
            assertEquals(name, c["ok"]!!.jsonPrimitive.boolean, r.ok)
            assertEquals(name, c.s("result"), r.text)
            val want = c["conflicts"]!!.jsonArray.map { x -> x.jsonObject.let { LiveMerge.Conflict(it.s("base"), it.s("mine"), it.s("theirs")) } }
            assertEquals(name, want, r.conflicts)
        }
    }

    @Test fun fieldCases() {
        val all = cases["fields"]!!.jsonArray
        assertTrue(all.isNotEmpty())
        for (el in all) {
            val c = el.jsonObject
            val tf = c["text_fields"]!!.jsonObject.mapValues { it.value.jsonPrimitive.content }
            val r = LiveMerge.mergeFields(c["base"] as? JsonObject, c["mine"]!!.jsonObject, c["theirs"]!!.jsonObject, tf)
            val name = c.s("name")
            assertEquals(name, c["ok"]!!.jsonPrimitive.boolean, r.ok)
            assertEquals(name, c["merged"]!!.jsonObject, r.merged)
            assertEquals(name, c["conflicts"]!!.jsonArray.map { it.jsonPrimitive.content }, r.conflicts)
        }
    }

    private fun long(c: JsonObject, side: String? = null): String {
        val n = c["lines"]!!.jsonPrimitive.int
        val line = c.s("line")
        val lines = (1..n).map { line.replace("{n}", it.toString()) }.toMutableList()
        for (s in if (side != null) listOf(side) else listOf("mine", "theirs")) {
            val e = c[s]!!.jsonArray
            lines[e[0].jsonPrimitive.int - 1] = e[1].jsonPrimitive.content
        }
        return lines.joinToString("")
    }

    /** This thread's CPU time (java.lang.management is not on the Android compile classpath, hence reflection). */
    private fun cpuNanos(): Long {
        val bean = Class.forName("java.lang.management.ManagementFactory").getMethod("getThreadMXBean").invoke(null)
        return Class.forName("java.lang.management.ThreadMXBean").getMethod("getCurrentThreadCpuTime").invoke(bean) as Long
    }

    @Test fun longTextCases() {
        val all = cases["long"]!!.jsonArray
        assertTrue(all.isNotEmpty())
        for (el in all) {
            val c = el.jsonObject
            val n = c["lines"]!!.jsonPrimitive.int
            val base = (1..n).joinToString("") { c.s("line").replace("{n}", it.toString()) }
            // The thread's CPU time, not the wall clock: other JVMs on a busy machine do not count.
            val t0 = cpuNanos()
            val r = LiveMerge.mergeText(base, long(c, "mine"), long(c, "theirs"), "lines")
            val ms = (cpuNanos() - t0) / 1_000_000
            val name = c.s("name")
            assertTrue("$name took $ms ms of CPU", ms < 2000)
            assertEquals(name, c["ok"]!!.jsonPrimitive.boolean, r.ok)
            if (r.ok) assertEquals(name, long(c), r.text)
            else {
                assertEquals(name, 1, r.conflicts.size)
                assertEquals(name, long(c, "mine"), r.text)
            }
        }
    }

    @Test fun tokensGiveTheTextBack() {
        for ((text, mode) in listOf("a\nb\n\nc" to "lines", "<p>a</p><div>b<br>c</div>tail" to "html", "" to "lines", "<P>Up</P><BR/>x" to "html")) {
            assertEquals(text, LiveMerge.tokens(text, mode).joinToString(""))
        }
    }

    @Test fun htmlKeyNormalisesSpelling() {
        assertEquals("<p>Hi there<br></p>", LiveMerge.key("<div class=\"a\" dir=\"ltr\">Hi&nbsp; there<br/></div>", "html"))
        // Python's \s is not Java's: a non-breaking space character is text, not whitespace.
        assertEquals("<p>a b</p>", LiveMerge.key("<p>a b</p>", "html"))
        // <divider> is not a div.
        assertEquals("<divider>", LiveMerge.key("<divider>", "html"))
    }

    @Test fun repeatedLinesStillMerge() {
        val base = "x\n".repeat(50) + "end\n"
        val mine = "start\n$base"
        val theirs = base.replace("end\n", "the end\n")
        val r = LiveMerge.mergeText(base, mine, theirs)
        assertTrue(r.ok)
        assertEquals("start\n" + "x\n".repeat(50) + "the end\n", r.text)
    }

    @Test fun sameTreatsEmptyAsMissing() {
        assertTrue(LiveMerge.same(null, JsonPrimitive("")))
        assertTrue(LiveMerge.same(JsonArray(emptyList()), JsonNull))
        assertTrue(!LiveMerge.same(JsonPrimitive("a"), null))
        assertTrue(!LiveMerge.same(JsonPrimitive(1), JsonPrimitive("1")))
    }
}
