// ACT-SURFACES: android.notes android.notesapp
// note.quick: ItemGrammar.quick runs the quick vectors.
package io.github.murtazanasir.mailr.notes

import io.github.murtazanasir.mailr.notes.grammar.ItemGrammar
import io.github.murtazanasir.mailr.ui.calendar.FlowYaml
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The checklist item grammar: the Kotlin item grammar ([ItemGrammar], in the shared Notes module) against
 * spec/cases/notes-quickadd-cases.yaml, the vectors vitest and pytest run too (the copy in test
 * resources; frontend/src/lib/itemadd.test.ts checks it matches). note.add_items and checkitem.add on the
 * phone parse with it, so an offline add shows the same items the server makes.
 */
class ItemGrammarTest {
    private val file = FlowYaml.read("interaction/notes-quickadd-cases.yaml")
    private val lists = file["lists"]!!.jsonArray.map { l ->
        val o = l.jsonObject
        ItemGrammar.ListInfo(o["id"]!!.jsonPrimitive.content, o["name"]!!.jsonPrimitive.content,
            o["sections"]!!.jsonArray.map { it.jsonPrimitive.content })
    }
    private fun byId(e: JsonElement?) = (e as? JsonPrimitive)?.content?.let { id -> lists.firstOrNull { it.id == id } }
    private val defaultList = byId(file["defaultList"])

    private fun mode(s: String) = when (s) {
        "capture" -> ItemGrammar.Mode.CAPTURE
        "edit" -> ItemGrammar.Mode.EDIT
        else -> ItemGrammar.Mode.LIST
    }

    private fun run(input: String, mode: String, list: JsonElement?, keep: Set<String> = emptySet(), prefixCreates: Boolean = false) =
        ItemGrammar.parse(input, mode(mode), list = byId(list), lists = lists, defaultList = defaultList, keep = keep, prefixCreates = prefixCreates)

    @Test fun hasTheVectors() {
        val cases = file["cases"]!!.jsonArray
        assertTrue(cases.size >= 100)
        assertEquals(cases.size, cases.map { it.jsonObject["id"]!!.jsonPrimitive.content }.toSet().size)
    }

    @Test fun everyPrefixOfTheTypingLinesParses() {
        val lines = file["typing"]!!.jsonArray.map { it.jsonPrimitive.content }
        for (mode in listOf("list", "capture")) for (line in lines) {
            val points = line.codePoints().toArray()
            for (n in 1..points.size) {
                val input = String(points, 0, n)
                val r = run(input, mode, JsonPrimitive("n-shop"))
                for (t in r.tokens) assertEquals("$mode $input", t.text, input.substring(t.start, t.end))
            }
        }
    }

    @Test fun everyCase() {
        val failures = mutableListOf<String>()
        for (c in file["cases"]!!.jsonArray.map { it.jsonObject }) {
            val id = c["id"]!!.jsonPrimitive.content
            val input = c["in"]!!.jsonPrimitive.content
            val want = c["expect"]!!.jsonObject
            val modeName = c["mode"]!!.jsonPrimitive.content
            if (modeName == "key") {
                val got = ItemGrammar.itemKey(input)
                if (got != want["key"]!!.jsonPrimitive.content) failures += "$id: key $got"
                continue
            }
            if (modeName == "quick") {
                failures += quickFailures(id, input, want)
                continue
            }
            val keep = c["opts"]?.jsonObject?.get("keep")?.jsonArray?.map { it.jsonPrimitive.content }?.toSet().orEmpty()
            val prefixCreates = c["opts"]?.jsonObject?.get("prefixCreates")?.jsonPrimitive?.content == "true"
            val r = run(input, modeName, c["list"], keep, prefixCreates)
            for (t in r.tokens) if (input.substring(t.start, t.end) != t.text) failures += "$id: token span ${t.text}"
            val tokens = r.tokens.map { "${it.kind}:${it.text}" }
            val wantTokens = want["tokens"]?.jsonArray?.map { it.jsonPrimitive.content }.orEmpty()
            if (tokens != wantTokens) failures += "$id: tokens $tokens, want $wantTokens"
            val wantNew = want["new_sections"]?.jsonArray?.map { it.jsonPrimitive.content }.orEmpty()
            if (r.newSections != wantNew) failures += "$id: new_sections ${r.newSections}"
            if (modeName == "capture") {
                val wl = want["list"]
                val got = r.list?.let { JsonObject(mapOf("id" to (it.id?.let(::JsonPrimitive) ?: JsonNull),
                    "name" to JsonPrimitive(it.name), "create" to JsonPrimitive(it.create))) } ?: JsonNull
                if (got != (wl ?: JsonNull)) failures += "$id: list $got"
            }
            val items = r.items.map { listOf(it.text, it.qty?.let { q -> q.amount to q.unit }, it.section, it.checked) }
            val wantItems = wantItems(want)
            if (items != wantItems) failures += "$id: items $items, want $wantItems"
        }
        assertTrue(failures.joinToString("\n"), failures.isEmpty())
    }

    /** note.quick: "New note…" text that starts with `#List` adds items; anything else is a text note. */
    private fun quickFailures(id: String, input: String, want: JsonObject): List<String> {
        val out = mutableListOf<String>()
        when (val q = ItemGrammar.quick(input, lists)) {
            is ItemGrammar.Quick.Note -> {
                if (want["kind"]!!.jsonPrimitive.content != "note") return listOf("$id: kind note")
                val n = want["note"]!!.jsonObject
                if (q.title != n["title"]!!.jsonPrimitive.content) out += "$id: title ${q.title}"
                if (q.body != n["body"]!!.jsonPrimitive.content) out += "$id: body ${q.body}"
            }
            is ItemGrammar.Quick.Items -> {
                if (want["kind"]!!.jsonPrimitive.content != "items") return listOf("$id: kind items")
                val r = q.result
                val got = r.list?.let { JsonObject(mapOf("id" to (it.id?.let(::JsonPrimitive) ?: JsonNull),
                    "name" to JsonPrimitive(it.name), "create" to JsonPrimitive(it.create))) } ?: JsonNull
                if (got != (want["list"] ?: JsonNull)) out += "$id: list $got"
                val tokens = r.tokens.map { "${it.kind}:${it.text}" }
                val wantTokens = want["tokens"]?.jsonArray?.map { it.jsonPrimitive.content }.orEmpty()
                if (tokens != wantTokens) out += "$id: tokens $tokens, want $wantTokens"
                val items = r.items.map { listOf(it.text, it.qty?.let { x -> x.amount to x.unit }, it.section, it.checked) }
                if (items != wantItems(want)) out += "$id: items $items, want ${wantItems(want)}"
            }
        }
        return out
    }

    private fun wantItems(want: JsonObject) = want["items"]!!.jsonArray.map { e ->
        val o = e.jsonObject
        val q = o["qty"]?.takeIf { it !is JsonNull }?.jsonObject
        listOf(o["text"]!!.jsonPrimitive.content,
            q?.let { it["amount"]!!.jsonPrimitive.content to (it["unit"] as? JsonPrimitive)?.takeIf { p -> p.isString }?.content },
            (o["section"] as? JsonPrimitive)?.takeIf { it.isString }?.content,
            o["checked"]?.jsonPrimitive?.booleanOrNull ?: false)
    }

    @Test fun quickNeverUsesADefaultList() {
        assertTrue(ItemGrammar.quick("milk, eggs", lists) is ItemGrammar.Quick.Note)
    }

    /** checkitem.add and note.add_items keep the user's spelling; the key is what matches duplicates. */
    @Test fun keysMatchDuplicates() {
        assertEquals(ItemGrammar.itemKey("Eggs"), ItemGrammar.itemKey("egg"))
        assertEquals("tomato", ItemGrammar.itemKey("Tomatoes"))
        assertEquals(ItemGrammar.itemKey("glass"), ItemGrammar.itemKey("glasses"))
    }
}
