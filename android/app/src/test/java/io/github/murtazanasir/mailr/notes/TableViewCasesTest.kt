package io.github.murtazanasir.mailr.notes

import io.github.murtazanasir.mailr.notes.data.Column
import io.github.murtazanasir.mailr.notes.data.NotesJson
import io.github.murtazanasir.mailr.notes.data.Row
import io.github.murtazanasir.mailr.notes.data.ViewEval
import io.github.murtazanasir.mailr.ui.calendar.FlowYaml
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate
import java.time.ZoneId

/**
 * A table view on the phone ([ViewEval]) against spec/cases/table-view-cases.yaml, the vectors the
 * server's tables/views.py passes too (backend/tests/test_table_view_cases.py, which also checks this
 * copy in the test resources is the same file). Offline, the table screen orders and filters its rows
 * with it.
 */
class TableViewCasesTest {
    private val file = FlowYaml.read("interaction/table-view-cases.yaml")
    private val columns = NotesJson.decodeFromJsonElement(ListSerializer(Column.serializer()), file["columns"]!!)
    private val rows = NotesJson.decodeFromJsonElement(ListSerializer(Row.serializer()), file["rows"]!!)
    private val titles = file["titles"]!!.jsonObject.mapValues { it.value.jsonPrimitive.content }
    private val ctx = ViewEval.Ctx(LocalDate.parse(file["today"]!!.jsonPrimitive.content), ZoneId.of(file["tz"]!!.jsonPrimitive.content))

    @Test fun hasTheVectors() {
        val cases = file["cases"]!!.jsonArray
        assertTrue(cases.size >= 60)
        assertEquals(cases.size, cases.map { it.jsonObject["id"]!!.jsonPrimitive.content }.toSet().size)
    }

    @Test fun everyCase() {
        val failures = mutableListOf<String>()
        for (c in file["cases"]!!.jsonArray.map { it.jsonObject }) {
            val spec = c["spec"]!!.jsonObject
            val sort = spec["sort"]?.jsonArray?.map { it.jsonObject }.orEmpty()
            val filters = spec["filters"]?.jsonArray?.map { it.jsonObject }.orEmpty()
            val got = ViewEval.rows(rows, columns, sort, filters, ctx, titles).map { it.id }
            val want = c["expect"]!!.jsonArray.map { it.jsonPrimitive.content }
            if (got != want) failures += "${c["id"]!!.jsonPrimitive.content}: $got, want $want"
        }
        assertTrue(failures.joinToString("\n"), failures.isEmpty())
    }

    @Test fun numbersAndDatesReadAsTheServerReadsThem() {
        assertEquals(0, ViewEval.parseNumber("1,000")!!.compareTo(java.math.BigDecimal(1000)))
        assertEquals(0, ViewEval.parseNumber("(12.50)")!!.compareTo(java.math.BigDecimal("-12.5")))
        assertEquals(0, ViewEval.parseNumber("82,5")!!.compareTo(java.math.BigDecimal("82.5")))
        assertEquals(LocalDate.of(2026, 10, 9), ViewEval.parseDate("fri", ctx))
        assertEquals(LocalDate.of(2026, 10, 12), ViewEval.parseDate("mon", ctx))
        assertEquals(LocalDate.of(2026, 10, 12), ViewEval.parseDate("Oct 12, 2026", ctx))
        assertEquals(LocalDate.of(2026, 10, 12), ViewEval.parseDate("12/10/2026", ctx.copy(dateOrder = "dmy")))
        assertEquals(null, ViewEval.parseDate("someday", ctx))
    }
}
