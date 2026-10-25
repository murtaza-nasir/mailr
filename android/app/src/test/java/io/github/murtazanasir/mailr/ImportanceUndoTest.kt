package io.github.murtazanasir.mailr

import android.app.Application
import io.github.murtazanasir.mailr.data.api.MailrJson
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * mail.important: Undo sends back the server's undo object unchanged, so the server can restore
 * the toggle's decision record (restore_importance with `importance`), and a select-all job's
 * undo (`job`, `ids: []`) keeps its empty ids.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class ImportanceUndoTest {
    private val f = FakeCalendarServer()

    @Before fun setUp() { f.start() }
    @After fun tearDown() { f.stop() }

    private val undo = """{"action":"restore_importance","importance":{"message_ids":[[901,false,0.42],[902,true,null]],
        "added":[77,78],"rejected":[[55,"active"]],"future_field":{"x":1}}}"""

    @Test fun toggleThenUndoSendsTheUndoUnchanged() = runBlocking {
        f.on("POST", "conversations/actions") { _, body ->
            if ("\"important\"" in body) f.json("""{"ok":true,"affected":1,"undo":$undo}""") else f.json("""{"ok":true,"affected":1}""")
        }
        val out = f.mail.act(listOf(3066L), "important")
        val req = out.undo!!
        assertTrue(f.mail.undo(req))
        val sent = f.body(f.sent("POST", "conversations/actions").last())
        val expected = MailrJson.parseToJsonElement(undo) as JsonObject
        assertEquals("restore_importance", (sent["action"] as kotlinx.serialization.json.JsonPrimitive).content)
        assertEquals(expected["importance"], sent["importance"])
        assertEquals(MailrJson.parseToJsonElement("[3066]"), sent["ids"])
        assertEquals(expected.keys + "ids", sent.keys)
    }

    @Test fun jobUndoKeepsItsEmptyIds() = runBlocking {
        val jobUndo = """{"action":"restore_importance","job":12,"ids":[]}"""
        f.on("POST", "conversations/actions") { _, body ->
            if ("\"not_important\"" in body) f.json("""{"ok":true,"affected":0,"undo":$jobUndo}""") else f.json("""{"ok":true}""")
        }
        val out = f.mail.act(listOf(1L, 2L), "not_important")
        assertEquals(12L, out.undo!!.job)
        assertTrue(f.mail.undo(out.undo!!))
        assertEquals(MailrJson.parseToJsonElement(jobUndo), f.body(f.sent("POST", "conversations/actions").last()))
    }
}
