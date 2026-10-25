// ACT-SURFACES: android.editor
// Live editing in the open editors: draft.live_update, draft.resolve_conflict, event.live_update,
// event.resolve_conflict, task.live_update, task.resolve_conflict.
package io.github.murtazanasir.mailr.live

import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.cal.LiveSave
import io.github.murtazanasir.mailr.data.live.LiveApi
import io.github.murtazanasir.mailr.data.live.LiveItemState
import io.github.murtazanasir.mailr.data.live.LiveSession
import io.github.murtazanasir.mailr.ui.calendar.TaskEditorState
import io.github.murtazanasir.mailr.ui.calendar.TaskLivePort
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate

/** The task editor under live editing (ui/calendar/LiveEditors.kt): fields in, merges, the PATCH body. */
class TaskLivePortTest {
    private val task = TaskItem(uid = "t-1", summary = "Send the report", notes = "Draft\nCharts\n", due = "2026-10-09", priority = 0,
        list = "/tasks/work/")

    /** The server's live content for a task (calendar/live_items.py TASK_KEYS). */
    private fun content(summary: String = task.summary, notes: String = task.notes!!, due: String? = task.due) = buildJsonObject {
        put("summary", summary); put("notes", notes); put("due", due); put("due_all_day", true)
        put("start", null as String?); put("status", "needs-action"); put("priority", 0)
        putJsonArray("categories") {}; put("rrule", null as String?); put("repeat_from", "due"); put("parent", null as String?)
        putJsonArray("reminders") {}; put("list", "/tasks/work/"); put("sort_order", null as Int?); put("tz", null as String?)
    }

    private class Api(var item: LiveItemState?) : LiveApi {
        override suspend fun item(kind: String, id: String) = item
        override suspend fun typing(kind: String, id: String) = Unit
    }

    @Test fun cleanEditorTakesTheServerCopy() = runTest {
        val s = TaskEditorState(task, null, null, task.uid)
        val port = TaskLivePort(s) {}
        val api = Api(LiveItemState(4, content(summary = "Send the Q3 report", due = "2026-10-10"), "assistant"))
        val live = LiveSession("task", task.uid, "me", port, this, api, onBase = port::rebased)
        live.open()
        assertEquals(4, live.baseVersion)
        assertEquals("Send the Q3 report", s.title)
        assertEquals(LocalDate.parse("2026-10-10"), s.dueDate)
        assertFalse("what the server has is not unsaved", s.dirty)
        assertEquals("Send the Q3 report", s.original?.summary)
        // The PATCH sends only what differs from the server's copy, with the version.
        s.notes = "Draft\nCharts\nAppendix\n"
        val body = LiveSave(live.baseVersion, port.baseline).body(TaskLivePort.body(s))!!
        assertEquals(setOf("notes", "base_version"), body.keys)
        assertEquals(JsonPrimitive(4), body["base_version"])
        assertNull("nothing changed: no request", LiveSave(4, TaskLivePort.body(s)).body(TaskLivePort.body(s)))
    }

    @Test fun dirtyNotesMergeByLinesAndConflictsShowTheBar() = runTest {
        val s = TaskEditorState(task, null, null, task.uid)
        val port = TaskLivePort(s) {}
        val api = Api(LiveItemState(1, content()))
        val live = LiveSession("task", task.uid, "me", port, this, api, onBase = port::rebased)
        live.open()
        s.notes = "Draft v2\nCharts\n"
        api.item = LiveItemState(2, content(notes = "Draft\nCharts\nTables\n"), "assistant")
        live.onItem(io.github.murtazanasir.mailr.data.live.LiveSignal.Item("task", task.uid, 2, "assistant", "web-1", false))
        assertEquals("Draft v2\nCharts\nTables\n", s.notes)
        assertTrue("the merge still saves", s.dirty)
        assertEquals(2, live.baseVersion)
        // The baseline is now the server's copy: the save sends the merged notes only.
        val body = LiveSave(live.baseVersion, port.baseline).body(TaskLivePort.body(s))!!
        assertEquals(setOf("notes", "base_version"), body.keys)

        // Both change the title: the bar, and the editor keeps what the user typed.
        s.title = "Send the report today"
        api.item = LiveItemState(3, content(summary = "Send the report Monday", notes = "Draft\nCharts\nTables\n"), "assistant")
        live.onItem(io.github.murtazanasir.mailr.data.live.LiveSignal.Item("task", task.uid, 3, "assistant", "web-1", false))
        val c = live.ui.value.conflict
        assertNotNull(c)
        assertEquals(listOf("summary"), c!!.fields)
        assertEquals("Send the report today", s.title)
        live.useTheirs()
        assertEquals("Send the report Monday", s.title)
        assertEquals("Draft\nCharts\nTables\n", s.notes)
        assertFalse(s.dirty)
    }

    @Test fun aLiveConflictBodyIsRecognised() {
        val x = io.github.murtazanasir.mailr.data.api.ApiException(409, "changed", """{"conflict":true,"fields":["summary"]}""")
        assertNotNull(LiveSave.conflictOf(x))
        assertNull(LiveSave.conflictOf(io.github.murtazanasir.mailr.data.api.ApiException(409, "etag", """{"error":"conflict","server":{}}""")))
        assertNull(LiveSave.conflictOf(io.github.murtazanasir.mailr.data.api.ApiException(500, "x", """{"conflict":true}""")))
    }

    @Test fun withoutAVersionTheWholeBodyGoes() {
        val full = JsonObject(mapOf("summary" to JsonPrimitive("a"), "notes" to JsonPrimitive("b")))
        assertEquals(full, LiveSave(null, full).body(full))
    }
}
