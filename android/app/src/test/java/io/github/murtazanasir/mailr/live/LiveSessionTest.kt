// ACT-SURFACES: android.editor
// Live editing in the open editors: draft.live_update, draft.resolve_conflict, event.live_update,
// event.resolve_conflict, task.live_update, task.resolve_conflict.
package io.github.murtazanasir.mailr.live

import android.app.Application
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import io.github.murtazanasir.mailr.data.live.LiveApi
import io.github.murtazanasir.mailr.data.live.LiveItemState
import io.github.murtazanasir.mailr.data.live.LivePort
import io.github.murtazanasir.mailr.data.live.LiveSession
import io.github.murtazanasir.mailr.data.live.LiveWords
import io.github.murtazanasir.mailr.data.sync.ServerEvent
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The open editor's live-editing state machine (data/live/LiveSession): a clean editor takes the
 * other copy (with Undo), a dirty one merges, an overlap shows the conflict bar, the editor's own
 * writes and versions it has are ignored, a merged save rebases, a 409 opens the bar.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class LiveSessionTest {
    /** An editor whose content is a JSON object; "body" merges by lines. */
    private class FakeEditor(var content: JsonObject) : LivePort {
        var dirty = false
        var saves = 0
        val applied = mutableListOf<Pair<JsonObject, Boolean>>()
        override val textFields = mapOf("body" to "lines")
        override fun mine() = content
        override fun dirty() = dirty
        override fun decode(server: JsonObject) = server
        override fun apply(content: JsonObject, clean: Boolean) {
            this.content = content
            dirty = !clean
            applied += content to clean
        }
        override fun saveNow() { saves++ }
        fun type(k: String, v: String) {
            content = JsonObject(content + (k to JsonPrimitive(v)))
            dirty = true
        }
    }

    private class FakeApi : LiveApi {
        var item: LiveItemState? = null
        var reads = 0
        val typing = mutableListOf<String>()
        override suspend fun item(kind: String, id: String): LiveItemState? { reads++; return item }
        override suspend fun typing(kind: String, id: String) { typing += "$kind:$id" }
    }

    private fun doc(subject: String, body: String) = buildJsonObject { put("subject", subject); put("body", body) }

    private fun item(version: Int?, by: String = "assistant", origin: String? = "other", deleted: Boolean = false) =
        ServerEvent("item", Json.parseToJsonElement(buildJsonObject {
            put("type", "item"); put("kind", "draft"); put("id", "42"); put("by", by)
            if (version != null) put("version", version)
            if (origin != null) put("origin", origin)
            if (deleted) put("deleted", true)
        }.toString()).jsonObject)

    private fun editing(state: String, by: String = "assistant") = ServerEvent("editing", buildJsonObject {
        put("type", "editing"); put("kind", "draft"); put("id", "42"); put("by", by); put("state", state)
    })

    private var clock = 0L

    private fun TestScope.session(editor: FakeEditor, api: FakeApi, version: Int = 1): LiveSession =
        LiveSession("draft", "42", "me-1", editor, this, api, now = { clock }).also { it.start(version, editor.content) }

    @Test fun cleanEditorTakesTheirCopyWithUndo() = runTest {
        val ed = FakeEditor(doc("Plan", "a\nb\n"))
        val api = FakeApi().apply { item = LiveItemState(2, doc("Plan", "a\nb\nc\n"), "assistant") }
        val s = session(ed, api)
        s.onEvent(item(2))
        assertEquals(doc("Plan", "a\nb\nc\n"), ed.content)
        assertFalse(ed.dirty)
        assertEquals(2, s.baseVersion)
        val n = s.ui.value.notice
        assertNotNull(n)
        assertEquals("Updated by Ambrose", LiveWords.updated(n!!.by))
        // Undo: the copy from before comes back and is saved as a new version (from the new base).
        s.undo()
        assertEquals(doc("Plan", "a\nb\n"), ed.content)
        assertTrue(ed.dirty)
        assertEquals(1, ed.saves)
        assertEquals(2, s.baseVersion)
        assertNull(s.ui.value.notice)
    }

    @Test fun ownWritesAndOldVersionsAreIgnored() = runTest {
        val ed = FakeEditor(doc("Plan", "a\n"))
        val api = FakeApi().apply { item = LiveItemState(5, doc("Other", "x\n")) }
        val s = session(ed, api, version = 3)
        s.onEvent(item(4, by = "device", origin = "me-1"))  // this install wrote it
        s.onEvent(item(3))                                     // the version the editor has
        s.onEvent(item(2))                                     // an older one
        assertEquals(0, api.reads)
        // The event says nothing newer, but the read finds the editor's own version: nothing changes.
        api.item = LiveItemState(3, doc("Other", "x\n"))
        s.onEvent(item(null))
        assertEquals(1, api.reads)
        assertEquals(doc("Plan", "a\n"), ed.content)
        assertTrue(ed.applied.isEmpty())
        // Another item, another kind: not this editor's.
        s.onEvent(ServerEvent("item", buildJsonObject { put("kind", "task"); put("id", "42"); put("version", 9) }))
        assertEquals(1, api.reads)
    }

    @Test fun dirtyEditorMergesAndStaysUnsaved() = runTest {
        val ed = FakeEditor(doc("Plan", "a\nb\n"))
        val api = FakeApi()
        val s = session(ed, api)
        ed.type("body", "A\nb\n")
        api.item = LiveItemState(2, doc("Plan v2", "a\nB\n"), "agent:Inbox Bot")
        s.onEvent(item(2, by = "agent:Inbox Bot"))
        assertEquals(doc("Plan v2", "A\nB\n"), ed.content)
        assertTrue("the merge still needs saving", ed.dirty)
        assertEquals(2, s.baseVersion)
        assertNull(s.ui.value.conflict)
        assertNull("nothing to undo after a merge", s.ui.value.notice?.previous)
    }

    @Test fun overlapShowsTheBarAndUseMineOrTheirs() = runTest {
        val ed = FakeEditor(doc("Plan", "a\nb\n"))
        val api = FakeApi().apply { item = LiveItemState(2, doc("Plan", "a\ntheirs\n"), "assistant") }
        val s = session(ed, api)
        ed.type("body", "a\nmine\n")
        s.onEvent(item(2))
        val c = s.ui.value.conflict
        assertNotNull(c)
        assertEquals(listOf("body"), c!!.fields)
        assertEquals("mine\n", c.rows.single().mine)
        assertEquals("theirs\n", c.rows.single().theirs)
        assertEquals("Ambrose changed this draft", LiveWords.changed(c.by, "draft"))
        assertEquals(doc("Plan", "a\nmine\n"), ed.content)  // nothing changed under the user
        assertEquals(1, s.baseVersion)

        // "Use mine": saved over theirs, from their version.
        s.useMine()
        assertNull(s.ui.value.conflict)
        assertEquals(2, s.baseVersion)
        assertEquals(1, ed.saves)

        // Again, and "Use the assistant's" this time.
        api.item = LiveItemState(3, doc("Plan", "a\nmore theirs\n"), "assistant")
        s.onEvent(item(3))
        assertNotNull(s.ui.value.conflict)
        s.useTheirs()
        assertEquals(doc("Plan", "a\nmore theirs\n"), ed.content)
        assertFalse(ed.dirty)
        assertEquals(3, s.baseVersion)
        assertNull(s.ui.value.conflict)
    }

    @Test fun mergedSaveRebasesTheEditor() = runTest {
        val ed = FakeEditor(doc("Plan", "a\nb\nc\n"))
        val s = session(ed, FakeApi())
        // Sent with base 1; the server merged in a change saved meanwhile (version 3).
        ed.type("body", "A\nb\nc\n")
        val sent = ed.content
        ed.dirty = false
        s.saved(sent, 3, merged = true, stored = doc("Plan", "A\nb\nC\n"))
        assertEquals(doc("Plan", "A\nb\nC\n"), ed.content)
        assertFalse(ed.dirty)
        assertEquals(3, s.baseVersion)

        // Typed on while the next save was out: the typing goes on top of what the server stored.
        ed.type("body", "A\nb\nC\nd\n")
        val sent2 = ed.content
        ed.type("body", "A2\nb\nC\nd\n")
        s.saved(sent2, 5, merged = true, stored = doc("Plan!", "A\nb\nC\nd\n"))
        assertEquals(doc("Plan!", "A2\nb\nC\nd\n"), ed.content)
        assertTrue(ed.dirty)
        assertEquals(5, s.baseVersion)

        // A plain save: the base is what was sent.
        s.saved(ed.content, 6)
        assertEquals(6, s.baseVersion)
    }

    @Test fun refusedSaveOpensTheBar() = runTest {
        val ed = FakeEditor(doc("Plan", "a\nb\n"))
        val s = session(ed, FakeApi())
        ed.type("subject", "Plan A")
        val body = Json.parseToJsonElement("""{"conflict":true,"kind":"draft","id":"42","fields":["subject"],
            "base":{"version":1,"content":{"subject":"Plan","body":"a\nb\n"}},
            "theirs":{"version":4,"content":{"subject":"Plan B","body":"a\nb\n"},"by":"assistant"},
            "mine":{"content":{"subject":"Plan A","body":"a\nb\n"}}}""").jsonObject
        s.refused(body)
        val c = s.ui.value.conflict!!
        assertEquals(4, c.version)
        assertEquals(listOf("subject"), c.fields)
        assertEquals(CompareRowOf("subject", "Plan A", "Plan B"), c.rows.single().let { CompareRowOf(it.field, it.mine, it.theirs) })
        s.useMine()
        assertEquals(4, s.baseVersion)
        assertEquals(1, ed.saves)
    }

    private data class CompareRowOf(val f: String, val m: String, val t: String)

    @Test fun editingBannerAndItsTimeout() = runTest {
        val ed = FakeEditor(doc("Plan", "a\n"))
        val s = session(ed, FakeApi())
        s.onEvent(editing("start", by = "agent:Inbox Bot"))
        assertEquals("agent:Inbox Bot", s.ui.value.editing)
        assertEquals("Inbox Bot is editing this draft…", LiveWords.editing(s.ui.value.editing, "draft"))
        s.onEvent(editing("end"))
        assertNull(s.ui.value.editing)
        s.onEvent(editing("start"))
        assertEquals("Ambrose is editing this draft…", LiveWords.editing(s.ui.value.editing, "draft"))
        advanceTimeBy(LiveSession.EDITING_TIMEOUT_MS - 1)
        runCurrent()
        assertNotNull(s.ui.value.editing)
        advanceTimeBy(2)
        runCurrent()
        assertNull("no end came: the banner goes", s.ui.value.editing)
    }

    @Test fun typingIsReportedAtMostOnceASecond() = runTest {
        val ed = FakeEditor(doc("Plan", "a\n"))
        val api = FakeApi()
        val s = session(ed, api)
        clock = 10_000
        s.typed(); s.typed()
        clock = 10_500
        s.typed()
        clock = 11_000
        s.typed()
        runCurrent()
        assertEquals(listOf("draft:42", "draft:42"), api.typing)
    }

    @Test fun deletedElsewhere() = runTest {
        val ed = FakeEditor(doc("Plan", "a\n"))
        val s = session(ed, FakeApi())
        s.onEvent(item(null, deleted = true))
        assertTrue(s.ui.value.gone)
    }

    @Test fun openTakesANewerCopyQuietly() = runTest {
        val ed = FakeEditor(doc("Plan", "a\n"))
        val api = FakeApi().apply { item = LiveItemState(7, doc("Plan", "a\nb\n")) }
        val s = LiveSession("draft", "42", "me-1", ed, this, api)
        s.open()
        assertEquals(7, s.baseVersion)
        assertEquals(doc("Plan", "a\nb\n"), ed.content)
        assertNull(s.ui.value.notice)
    }

    @Test fun words() {
        assertEquals("Updated on another device", LiveWords.updated("device"))
        assertEquals("Updated on another device", LiveWords.updated("user"))
        assertEquals("Updated by Inbox Bot", LiveWords.updated("agent:Inbox Bot"))
        assertEquals("Use Ambrose's", LiveWords.useTheirs("assistant"))
        assertEquals("Task changed on another device", LiveWords.changed("device", "task"))
    }
}
