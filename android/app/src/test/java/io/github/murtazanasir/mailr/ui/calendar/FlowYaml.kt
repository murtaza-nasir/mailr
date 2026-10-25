package io.github.murtazanasir.mailr.ui.calendar

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject

/**
 * Reads the shared vector files (quickadd-cases.yaml and layout-cases.yaml, copied into test resources). They are
 * YAML in one restricted shape, so no YAML library is needed: `key: <json>` lines at the top level and
 * `  - <json>` items under `cases:`; lines starting with `#` are comments. The web twin is
 * frontend/src/lib/flowYaml.ts.
 */
object FlowYaml {
    fun read(resource: String): JsonObject {
        val text = FlowYaml::class.java.classLoader!!.getResource(resource)!!.readText()
        val out = linkedMapOf<String, JsonElement>()
        val cases = mutableListOf<JsonElement>()
        var inCases = false
        text.lines().forEachIndexed { i, line ->
            val t = line.trim()
            if (t.isEmpty() || t.startsWith("#")) return@forEachIndexed
            if (t == "cases:") {
                inCases = true
                return@forEachIndexed
            }
            if (inCases && t.startsWith("- ")) {
                cases += Json.parseToJsonElement(t.substring(2))
                return@forEachIndexed
            }
            val m = Regex("^([A-Za-z_][\\w-]*):\\s+(.+)$").find(line)
            require(m != null && !line.startsWith(" ")) { "line ${i + 1}: not a \"key: <json>\" or \"- <json>\" line" }
            out[m.groupValues[1]] = Json.parseToJsonElement(m.groupValues[2])
            inCases = false
        }
        out["cases"] = JsonArray(cases)
        return JsonObject(out)
    }
}
