package io.github.murtazanasir.mailr.ui.present

import io.github.murtazanasir.mailr.ui.calendar.FlowYaml
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * spec/cases/citation-cases.yaml (the copy in test resources), shared with the backend and the
 * web. A case seen in use: a raw [[event:… "Chinatown walk (until ~11 AM)"]] in an answer.
 */
class CiteGrammarTest {
    @Test fun citationCases() {
        val cases = FlowYaml.read("interaction/citation-cases.yaml")["cases"]!!.jsonArray.map { it.jsonObject }
        check(cases.size >= 25)
        cases.forEach { c ->
            val id = c["id"]!!.jsonPrimitive.content
            val input = c["in"]!!.jsonPrimitive.content
            val want = c["markers"]!!.jsonArray.map { m ->
                val o = m.jsonObject
                fun s(k: String) = o[k]?.takeIf { it !is JsonNull }?.jsonPrimitive?.contentOrNull
                CiteGrammar.Marker(s("kind")!!, s("id")!!, s("at"), s("quote"))
            }
            assertEquals(id, want, CiteGrammar.scan(input))
            assertEquals(id, c["clean"]!!.jsonPrimitive.content, CiteGrammar.clean(input))
            assertEquals(id, c["stream"]!!.jsonPrimitive.content, CiteGrammar.clean(input, streaming = true))
        }
    }
}
