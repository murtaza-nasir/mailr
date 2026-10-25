package io.github.murtazanasir.mailr.ui.present

import io.github.murtazanasir.mailr.data.api.Citation
import io.github.murtazanasir.mailr.data.api.sourceRows
import io.github.murtazanasir.mailr.ui.calendar.FlowYaml
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.int
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * spec/cases/source-cases.yaml (the copy in test resources), shared with the web's sourceRows:
 * an answer that cites one email thread several times lists it once under Sources.
 */
class SourceRowsTest {
    private val json = Json { ignoreUnknownKeys = true }

    @Test fun sourceCases() {
        val cases = FlowYaml.read("interaction/source-cases.yaml")["cases"]!!.jsonArray.map { it.jsonObject }
        check(cases.size >= 5)
        cases.forEach { c ->
            val id = c["id"]!!.jsonPrimitive.content
            val cites = c["cites"]!!.jsonArray.map { json.decodeFromJsonElement(Citation.serializer(), it) }
            val want = c["rows"]!!.jsonArray.map { r -> r.jsonArray.map { it.jsonPrimitive.int } }
            assertEquals(id, want, sourceRows(cites).map { r -> r.all.map { it.n } })
        }
    }
}
