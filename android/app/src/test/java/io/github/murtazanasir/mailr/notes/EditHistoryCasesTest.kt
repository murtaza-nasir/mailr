// ACT-SURFACES: android.notes android.notesapp
// note.undo_edit, note.redo_edit: the editor history against the shared vectors
// (spec/cases/edit-history-cases.yaml, the copy in test resources); the web twin runs the same file.
package io.github.murtazanasir.mailr.notes

import io.github.murtazanasir.mailr.notes.edit.EditHistory
import io.github.murtazanasir.mailr.ui.calendar.FlowYaml
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class EditHistoryCasesTest {
    private val file = FlowYaml.read("interaction/edit-history-cases.yaml")
    private val cases = file["cases"]!!.jsonArray.map { it.jsonObject }

    private fun str(e: JsonElement) = e.jsonPrimitive.content
    private fun show(s: EditHistory.Step<String>): List<String> = when (s) {
        is EditHistory.TextStep -> listOf(s.field, s.before, s.after)
        is EditHistory.OpStep -> listOf("op", s.label)
    }
    private fun expected(a: JsonElement?): List<List<String>> = a?.jsonArray?.map { x -> x.jsonArray.map { str(it) } } ?: emptyList()

    @Test fun vectorsAreThere() {
        assertTrue(cases.size >= 50)
        assertEquals(cases.size, cases.map { str(it["id"]!!) }.toSet().size)
        assertEquals(EditHistory.IDLE_MS, file["idle"]!!.jsonPrimitive.long)
        assertEquals(EditHistory.CAP, file["cap"]!!.jsonPrimitive.intOrNull)
    }

    @Test fun everyCase() {
        val failures = mutableListOf<String>()
        for (c in cases) {
            val id = str(c["id"]!!)
            val want = c["expect"]
            try {
                when {
                    c["do"] != null -> {
                        val h = EditHistory<String>(cap = c["cap"]?.jsonPrimitive?.intOrNull ?: EditHistory.CAP)
                        val big = mutableListOf<Int>()
                        c["do"]!!.jsonArray.forEachIndexed { i, ev ->
                            val e = ev.jsonArray
                            when (str(e[0])) {
                                "t" -> {
                                    val r = h.recordText(str(e[1]), str(e[2]), str(e[3]), e[4].jsonPrimitive.long,
                                        e.getOrNull(5)?.jsonPrimitive?.contentOrNull)
                                    if (r?.big == true) big += i
                                }
                                "op" -> h.recordOp(str(e[1]), str(e[1]))
                                "undo" -> h.undo()
                                "redo" -> h.redo()
                                "seal" -> h.seal()
                                else -> error("unknown event ${e[0]}")
                            }
                        }
                        val w = want!!.jsonObject
                        assertEquals("$id undo", expected(w["undo"]), h.steps().map { show(it) })
                        val redone = generateSequence { h.redo() }.map { show(it) }.toList()
                        assertEquals("$id redo", expected(w["redo"]).reversed(), redone)
                        w["big"]?.let { b -> assertEquals("$id big", b.jsonArray.map { it.jsonPrimitive.intOrNull }, big) }
                    }
                    c["classify"] != null -> {
                        val a = c["classify"]!!.jsonArray
                        val t = (a[2] as? JsonPrimitive)?.takeIf { it !is JsonNull }?.contentOrNull
                        assertEquals(id, str(want!!).uppercase(), EditHistory.classify(EditHistory.diff(str(a[0]), str(a[1])), t).name)
                    }
                    c["big"] != null -> {
                        val a = c["big"]!!.jsonArray
                        assertEquals(id, want!!.jsonPrimitive.booleanOrNull, EditHistory.isBigRemoval(str(a[0]), str(a[1])))
                    }
                    c["revert"] != null -> {
                        val a = c["revert"]!!.jsonArray
                        assertEquals(id, (want as? JsonPrimitive)?.contentOrNull, EditHistory.revertText(str(a[0]), str(a[1]), str(a[2])))
                    }
                    c["reapply"] != null -> {
                        val a = c["reapply"]!!.jsonArray
                        assertEquals(id, (want as? JsonPrimitive)?.contentOrNull, EditHistory.reapplyText(str(a[0]), str(a[1]), str(a[2])))
                    }
                    else -> error("case $id has no kind")
                }
            } catch (e: AssertionError) {
                failures += "${e.message}"
            }
        }
        assertEquals(emptyList<String>(), failures)
    }

    @Test fun capDropsTheOldest() {
        val h = EditHistory<Int>()
        repeat(250) { h.recordOp("$it", it) }
        assertEquals(200, h.undoSize)
        assertEquals(50, (h.steps().first() as EditHistory.OpStep).op)
    }

    @Test fun savedStateKeepsTextStepsOnly() {
        val h = EditHistory<String>()
        h.recordText("title", "", "Groceries", 0, "insertFromPaste")
        h.recordOp("Checked milk", "x")
        h.recordText("body", "a", "ab", 5000)
        h.undo()
        val json = Json.encodeToString(EditHistory.Saved.serializer(), h.save())
        val back = EditHistory<String>().apply { restore(Json.decodeFromString(EditHistory.Saved.serializer(), json)) }
        assertEquals(listOf(listOf("title", "", "Groceries")), back.steps().map { show(it) })
        val r = back.peekRedo() as EditHistory.TextStep
        assertEquals("ab", r.after)
        assertFalse(r.open)
    }

    @Test fun dropRemovesAStepThatCouldNotApply() {
        val h = EditHistory<String>()
        val s = h.recordOp("Deleted milk", "x")
        h.undo()
        h.drop(s)
        assertFalse(h.canRedo)
        assertEquals(5, EditHistory.caretAfter("hello world", "hello"))
    }
}
