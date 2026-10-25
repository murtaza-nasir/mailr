package io.github.murtazanasir.mailr.ui.calendar

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime
import java.time.ZoneId

/** Runs spec/cases/quickadd-cases.yaml (the copy in test resources) against [QuickAddGrammar]. */
class QuickAddGrammarTest {
    private val file = FlowYaml.read("interaction/quickadd-cases.yaml")

    private fun str(e: JsonElement?) = (e as? JsonPrimitive)?.takeIf { it.isString }?.content

    private fun collections(e: JsonElement?) =
        e?.jsonArray?.map { QuickCollection(it.jsonObject["id"]!!.jsonPrimitive.content, it.jsonObject["name"]!!.jsonPrimitive.content) }

    private fun options(case: JsonObject): QuickAddOptions {
        val o = JsonObject(file["options"]!!.jsonObject + (case["opts"]?.jsonObject ?: JsonObject(emptyMap())))
        val zone = ZoneId.of(str(file["tz"])!!)
        val now = LocalDateTime.parse(str(case["now"]) ?: str(file["now"])!!).atZone(zone)
        return QuickAddOptions(
            mode = if (str(case["mode"]) == "event") QuickMode.EVENT else QuickMode.TASK,
            now = now,
            lists = collections(o["lists"] ?: file["lists"]).orEmpty(),
            calendars = collections(o["calendars"] ?: file["calendars"]).orEmpty(),
            people = file["people"]!!.jsonArray.map {
                QuickPerson(it.jsonObject["name"]!!.jsonPrimitive.content, it.jsonObject["email"]!!.jsonPrimitive.content)
            },
            places = o["places"]?.jsonArray?.map { it.jsonPrimitive.content }.orEmpty(),
            dayFirst = str(o["dateOrder"]) == "dmy",
            defaultDuration = o["defaultDuration"]?.jsonPrimitive?.int ?: 60,
            morningHour = o["morningHour"]?.jsonPrimitive?.int ?: 9,
            afternoonHour = o["afternoonHour"]?.jsonPrimitive?.int ?: 14,
            eveningHour = o["eveningHour"]?.jsonPrimitive?.int ?: 18,
            keep = o["keep"]?.jsonArray?.map { it.jsonPrimitive.content }?.toSet().orEmpty(),
        )
    }

    private fun s(v: String?): JsonElement = if (v == null) JsonNull else JsonPrimitive(v)
    private fun when_(t: LocalDateTime?, dateOnly: Boolean): JsonElement =
        if (t == null) JsonNull else JsonPrimitive(if (dateOnly) t.toLocalDate().toString() else t.toString())

    /** The result in the vectors' field names and shapes. */
    private fun fields(r: QuickAddResult): Map<String, JsonElement> = mapOf(
        "start" to when_(r.start, r.allDay),
        "end" to when_(r.end, r.allDay),
        "all_day" to JsonPrimitive(r.allDay),
        "duration" to (r.duration?.let { JsonPrimitive(it) } ?: JsonNull),
        "due" to when_(r.due, r.dueAllDay),
        "due_all_day" to JsonPrimitive(r.dueAllDay),
        "start_date" to s(r.startDate?.toString()),
        "rrule" to s(r.rrule),
        "repeat_from" to s(r.repeatFrom),
        "location" to s(r.location),
        "guests" to JsonArray(r.guests.map { g ->
            JsonObject(mapOf(
                "text" to JsonPrimitive(g.text), "email" to s(g.email), "confirmed" to JsonPrimitive(g.confirmed),
                "matches" to JsonArray(g.matches.map { JsonObject(mapOf("name" to JsonPrimitive(it.name), "email" to JsonPrimitive(it.email))) }),
            ))
        }),
        "list_or_calendar" to (r.listOrCalendar?.let {
            JsonObject(mapOf("id" to s(it.id), "name" to JsonPrimitive(it.name), "create" to JsonPrimitive(it.create)))
        } ?: JsonNull),
        "tags" to JsonArray(r.tags.map { JsonPrimitive(it) }),
        "priority" to JsonPrimitive(r.priority),
        "reminders" to JsonArray(r.reminders.map {
            JsonObject(mapOf("minutes" to (it.minutes?.let { m -> JsonPrimitive(m) } ?: JsonNull), "at" to s(it.at?.toString())))
        }),
        "video" to JsonPrimitive(r.video),
    )

    @Test fun everyVectorPasses() {
        val cases = file["cases"]!!.jsonArray.map { it.jsonObject }
        assertTrue("expected at least 120 cases, found ${cases.size}", cases.size >= 120)
        val failures = mutableListOf<String>()
        for (c in cases) {
            val id = str(c["id"])
            val input = str(c["in"])!!
            val want = c["expect"]!!.jsonObject
            val r = try {
                QuickAddGrammar.parse(input, options(c))
            } catch (e: Exception) {
                failures += "$id: threw $e"
                continue
            }
            for (t in r.tokens) if (input.substring(t.start, t.end) != t.text) failures += "$id: span ${t.start}-${t.end} is not '${t.text}'"
            if (r.title != str(want["title"])) failures += "$id: title '${r.title}' != '${str(want["title"])}'"
            val tokens = r.tokens.map { "${it.kind.name.lowercase()}:${it.text}" }
            val wantTokens = want["tokens"]?.jsonArray?.map { it.jsonPrimitive.content }.orEmpty()
            if (tokens != wantTokens) failures += "$id: tokens $tokens != $wantTokens"
            val got = fields(r)
            for ((k, v) in want) {
                if (k == "title" || k == "tokens") continue
                val g = got[k]
                if (g == null) failures += "$id: unknown field $k"
                else if (!same(g, v)) failures += "$id: $k = $g, expected $v"
            }
        }
        assertEquals(failures.joinToString("\n"), 0, failures.size)
    }

    /** JSON equality where 60 and 60.0 or true and "true" are not confused. */
    private fun same(a: JsonElement, b: JsonElement): Boolean = when {
        a is JsonNull || b is JsonNull -> a is JsonNull && b is JsonNull
        a is JsonPrimitive && b is JsonPrimitive -> a.isString == b.isString && (a.content == b.content ||
            (a.booleanOrNull == null && a.content.toDoubleOrNull() != null && a.content.toDoubleOrNull() == b.content.toDoubleOrNull()))
        a is JsonArray && b is JsonArray -> a.size == b.size && a.indices.all { same(a[it], b[it]) }
        a is JsonObject && b is JsonObject -> a.keys == b.keys && a.keys.all { same(a[it]!!, b[it]!!) }
        else -> false
    }

    /** Every prefix of the shared typing lines, a code point at a time, in both modes (quick add crashed while typing, 3 Oct 2026). */
    @Test fun everyPrefixOfTheTypingLinesParses() {
        val lines = file["typing"]!!.jsonArray.map { it.jsonPrimitive.content }
        assertTrue(lines.size > 10)
        for (mode in listOf("task", "event")) for (line in lines) {
            var end = 0
            while (end < line.length) {
                end += Character.charCount(line.codePointAt(end))
                val input = line.substring(0, end)
                val r = QuickAddGrammar.parse(input, options(JsonObject(mapOf("mode" to JsonPrimitive(mode)))))
                r.tokens.forEach { t -> assertEquals("$mode \"$input\"", t.text, input.substring(t.start, t.end)) }
            }
        }
    }

    @Test fun legacyQuickAddKeepsOtherWordsInTheTitle() {
        val now = LocalDateTime.of(2026, 9, 29, 12, 0)
        QuickAdd.parse("Buy milk #groceries @store tomorrow", now).let {
            assertEquals("Buy milk #groceries @store", it.title)
            assertEquals(now.toLocalDate().plusDays(1), it.date)
        }
        QuickAdd.parse("Journal daily !", now).let {
            assertEquals("Journal daily", it.title)
            assertEquals(null, it.date)
            assertEquals(9, it.priority)
        }
    }
}
