// ACT-SURFACES: android.editor android.tasks android.share
package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.time.AppClock
import android.app.Application
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.hasText
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.longClick
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onFirst
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.test.performTextReplacement
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.swipeLeft
import androidx.compose.ui.test.swipeRight
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.cal.CalConflict
import io.github.murtazanasir.mailr.data.cal.ListCount
import io.github.murtazanasir.mailr.data.db.CalCollectionEntity
import io.github.murtazanasir.mailr.data.db.CalConflictEntity
import io.github.murtazanasir.mailr.data.db.CalTaskEntity
import io.github.murtazanasir.mailr.ui.calendar.TasksViewModel
import io.github.murtazanasir.mailr.ui.mail.UiMessage
import io.github.murtazanasir.mailr.ui.tasks.ConflictSheet
import io.github.murtazanasir.mailr.ui.tasks.ListManager
import io.github.murtazanasir.mailr.ui.tasks.ListManagerActions
import io.github.murtazanasir.mailr.ui.tasks.QuickPrefill
import io.github.murtazanasir.mailr.ui.tasks.RescheduleSheet
import io.github.murtazanasir.mailr.ui.tasks.RowStyle
import io.github.murtazanasir.mailr.ui.tasks.SelectionBar
import io.github.murtazanasir.mailr.ui.tasks.ShareTask
import io.github.murtazanasir.mailr.ui.tasks.ShareTaskScreen
import io.github.murtazanasir.mailr.ui.tasks.Smart
import io.github.murtazanasir.mailr.ui.tasks.SwipeAction
import io.github.murtazanasir.mailr.ui.tasks.TaskGroup
import io.github.murtazanasir.mailr.ui.tasks.TaskGroupList
import io.github.murtazanasir.mailr.ui.tasks.TaskListActions
import io.github.murtazanasir.mailr.ui.tasks.TaskQuickAddBar
import io.github.murtazanasir.mailr.ui.tasks.TaskView
import io.github.murtazanasir.mailr.data.share.SharedContent
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.time.LocalDate
import java.time.ZonedDateTime

/**
 * The Tasks screen on Android (surface android.tasks), against a fake server: smart lists from
 * Room (task.search too), quick add (task.create), swipes (task.complete, task.reschedule),
 * selection and bulk with one undo (task.bulk_complete, task.bulk_reschedule, task.bulk_move,
 * task.bulk_tag, task.bulk_priority, task.bulk_delete), reorder and indent (task.reorder,
 * task.reparent), the list manager (list.create, list.rename, list.color, list.reorder,
 * list.delete, tag.rename, tag.delete), repeating tasks (task.complete → "Next", task.skip),
 * the other verbs (task.set_start, task.remind_at, task.move_list, task.set_priority,
 * task.duplicate, task.subtask_add, task.clear_completed, task.open_email), the share target (task.create on android.share) and
 * the conflict sheet.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class TasksScreenTest {
    @get:Rule val compose = createComposeRule()
    private val h = CalendarHarness()
    /** Responses this test gives instead of the harness's {"ok":true}: method + path → body. */
    private val answers = mutableMapOf<String, String>()
    private val messages = mutableListOf<UiMessage>()

    @Before fun setUp() {
        h.start()
        h.server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val body = request.body.readUtf8()
                h.requests += request to body
                answers["${request.method} ${request.path?.substringBefore('?')}"]?.let { return MockResponse().setBody(it) }
                return if (request.method == "GET") MockResponse().setResponseCode(404).setBody("""{"detail":"not here"}""")
                else MockResponse().setBody("""{"ok":true}""")
            }
        }
    }

    @After fun tearDown() = h.stop()

    private val now: ZonedDateTime = h.now
    private val today: LocalDate = now.toLocalDate()

    private fun seed(vararg ts: TaskItem) = runBlocking {
        h.g.db.calendar().putTasks(ts.map { t ->
            CalTaskEntity(t.uid, t.list, t.parent, t.completed, null, MailrJson.encodeToString(TaskItem.serializer(), t), sortOrder = t.sortOrder)
        })
    }

    private fun lists(vararg cs: CalendarCollection) = runBlocking {
        h.g.db.calendar().putCollections(cs.map { c ->
            CalCollectionEntity(c.url, c.calendarId, c.name, c.color, c.events, c.tasks, c.hidden, c.sortOrder, null,
                MailrJson.encodeToString(CalendarCollection.serializer(), c))
        })
        h.g.calendar.loadCached()
    }

    private fun vm(): TasksViewModel = h.vm { TasksViewModel(h.g) }.also { v -> v.viewModelScope.launch { v.messages.collect { messages += it } } }

    private fun lastMessage(): UiMessage {
        h.until("a message") { messages.isNotEmpty() }
        return messages.last()
    }

    private fun groupsOf(v: TasksViewModel, cond: (List<TaskGroup>) -> Boolean): List<TaskGroup> {
        var latest: List<TaskGroup>? = null
        val job = v.viewModelScope.launch { v.groups.collect { latest = it } }
        h.until("groups") { latest?.let(cond) == true }
        job.cancel()
        return latest!!
    }

    private fun uids(gs: List<TaskGroup>) = gs.associate { g -> g.id to g.entries.map { it.task.uid } }

    private fun body(method: String, path: String): JsonObject = h.sent(method, "/api/v1/$path")

    private fun actions(v: TasksViewModel, emails: MutableList<Long> = mutableListOf(), onSwipe: (TaskItem, SwipeAction) -> Unit = { _, _ -> }) =
        TaskListActions(
            open = {}, toggle = { if (it.completed) v.reopen(it) else v.complete(it) }, select = { v.toggleSelect(it.uid) },
            swipe = onSwipe, reschedule = {}, reorder = { t, a, b -> v.reorder(t, a, b) }, reparent = { t, p -> v.reparent(t, p) },
            openEmail = { emails += it }, openTag = {}, addToDay = {})

    // ---------------- smart lists ----------------

    /** Today, High priority and a list from Room; task.search with an operator. */
    @Test fun smartListsFromRoom() {
        seed(
            TaskItem("od", "Overdue thing", today.minusDays(2).toString(), list = "L1"),
            TaskItem("hi", "Urgent", today.plusDays(4).toString(), list = "L1", priority = 1),
            TaskItem("nd", "Someday", null, list = "L2", categories = listOf("home")),
        )
        val v = vm()
        v.open(TaskView.TODAY)
        assertEquals(listOf("od"), uids(groupsOf(v) { gs -> gs.any { it.id == "overdue" } })["overdue"])
        v.open(TaskView.SmartView(Smart.HIGH))
        assertEquals(listOf("hi"), groupsOf(v) { gs -> gs.any { g -> g.entries.any { it.task.uid == "hi" } } }.single().entries.map { it.task.uid })
        v.open(TaskView.ListView("L2"))
        assertEquals(listOf("nd"), groupsOf(v) { gs -> gs.isNotEmpty() && gs[0].entries.all { it.task.list == "L2" } }.single().entries.map { it.task.uid })
        // ACT task.search
        v.search("@home")
        assertEquals(listOf("nd"), groupsOf(v) { gs -> gs.singleOrNull()?.id == "results" }.single().entries.map { it.task.uid })
    }

    // ---------------- quick add ----------------

    /** task.create: the bar parses the line; Enter adds and clears it (the field keeps focus); the view's prefill fills the rest. */
    @Test fun quickAddCreatesFromTheGrammar() {
        val v = vm()
        compose.setContent {
            MaterialTheme {
                val focus = androidx.compose.runtime.remember { androidx.compose.ui.focus.FocusRequester() }
                TaskQuickAddBar(emptyList(), QuickPrefill(day = today, tag = "errand"), { AppClock.now() }, focus,
                    onAdd = { text, keep -> v.quickAdd(text, keep = keep, prefill = QuickPrefill(day = today, tag = "errand")) },
                    onDetails = { _, _ -> }, onClearPrefill = {})
            }
        }
        compose.onNodeWithContentDescription("Quick add").performTextInput("Buy stamps !!! @post")
        compose.onNodeWithText("High priority").assertExists()
        compose.onNodeWithContentDescription("Add task").performClick()
        val sent = body("POST", "tasks")
        assertEquals("Buy stamps", sent["summary"]!!.jsonPrimitive.content)
        assertEquals(today.toString(), sent["due"]!!.jsonPrimitive.content)
        assertEquals(listOf("post", "errand"), sent["categories"]!!.jsonArray.map { it.jsonPrimitive.content })
        assertEquals(1, sent["priority"]!!.jsonPrimitive.content.toInt())
        compose.onNodeWithText("Buy stamps !!! @post").assertDoesNotExist()
    }

    // ---------------- swipes ----------------

    /** A swipe right completes (task.complete); a swipe left opens the reschedule sheet, whose verbs are kept apart (task.reschedule). */
    @Test fun swipes() {
        val v = vm()
        val swiped = mutableListOf<Pair<String, SwipeAction>>()
        val emails = mutableListOf<Long>()
        val task = h.task
        compose.setContent {
            MaterialTheme {
                TaskGroupList(listOf(TaskGroup("g", "", listOf(io.github.murtazanasir.mailr.ui.tasks.TaskEntry(task)))), AppClock.millis(),
                    emptyList(), emptySet(), SwipeAction.COMPLETE to SwipeAction.RESCHEDULE, RowStyle(), actions(v, emails) { t, a -> swiped += t.uid to a })
            }
        }
        compose.onNodeWithText(task.summary).performTouchInput { swipeLeft(startX = width * 0.95f, endX = width * 0.1f, durationMillis = 400) }
        compose.waitForIdle()
        assertEquals(listOf("t1" to SwipeAction.RESCHEDULE), swiped)
        // ACT task.open_email: the row's email mark, as an action of the row.
        val row = compose.onNode(hasText(task.summary) and SemanticsMatcher.keyIsDefined(SemanticsActions.CustomActions)).fetchSemanticsNode()
        compose.runOnIdle { row.config[SemanticsActions.CustomActions].first { it.label == "Open email" }.action() }
        assertEquals(listOf(3066L), emails)
        compose.onNodeWithText(task.summary).performTouchInput { swipeRight(startX = width * 0.05f, endX = width * 0.9f, durationMillis = 400) }
        compose.mainClock.advanceTimeBy(1_000)
        assertEquals(true, body("PATCH", "tasks/t1")["completed"]!!.jsonPrimitive.boolean)
    }

    /** The reschedule sheet: Reschedule / Defer / Remind me. No date clears the due (task.reschedule); Defer sets a start (task.set_start). */
    @Test fun rescheduleSheetVerbs() {
        val v = vm()
        var verb by mutableStateOf("")
        compose.setContent {
            MaterialTheme {
                RescheduleSheet(h.task, 9, onReschedule = { d -> verb = "due"; v.reschedule(h.task, d) },
                    onDefer = { s -> verb = "start"; v.setStart(h.task, s) }, onRemind = { m -> v.remindAt(h.task, m) }, onSkip = null)
            }
        }
        compose.onNodeWithText("Moves the due date.").assertExists()
        compose.onNodeWithText("No date").performScrollTo().performClick()
        assertEquals(true, body("PATCH", "tasks/t1")["clear_due"]!!.jsonPrimitive.boolean)
        compose.onNodeWithText("Defer").performScrollTo().performClick()
        compose.onNodeWithText("Hides the task until it starts.").assertExists()
        compose.onAllNodesWithText("Next week").onFirst().performClick()
        h.until("start sent") { h.requests.count { it.first.method == "PATCH" } >= 2 }
        val start = MailrJson.parseToJsonElement(h.requests.last { it.first.method == "PATCH" }.second) as JsonObject
        assertTrue(start["start"]!!.jsonPrimitive.content.isNotBlank())
        compose.onNodeWithText("Remind me").performClick()
        compose.onNodeWithText("Adds a reminder. The due date stays.").assertExists()
    }

    // ---------------- selection and bulk ----------------

    /** Multi-select: a long-press selects; the bar's Complete sends one bulk call (task.bulk_complete); one Undo reopens all. */
    @Test fun multiSelectBulkCompleteAndUndo() {
        seed(TaskItem("t2", "Second task", today.toString()))
        val v = vm()
        val keep = v.viewModelScope.launch { v.tasks.collect {} }
        h.until("tasks") { v.tasks.value?.size == 2 }
        compose.setContent {
            MaterialTheme {
                val sel by v.selection.collectAsStateWithLifecycle()
                Column(Modifier.fillMaxSize()) {
                    TaskGroupList(listOf(TaskGroup("g", "", listOf(io.github.murtazanasir.mailr.ui.tasks.TaskEntry(h.task),
                        io.github.murtazanasir.mailr.ui.tasks.TaskEntry(TaskItem("t2", "Second task", today.toString()))))),
                        AppClock.millis(), emptyList(), sel, SwipeAction.COMPLETE to SwipeAction.RESCHEDULE, RowStyle(), actions(v),
                        modifier = Modifier.weight(1f))
                    if (sel.isNotEmpty()) SelectionBar(sel.size, v::bulkComplete, {}, {}, {}, {}, {})
                }
            }
        }
        compose.onNodeWithText(h.task.summary).performTouchInput { longClick() }
        compose.onNodeWithText("Second task").performClick()
        compose.onNodeWithText("Complete").performClick()
        val b = body("POST", "tasks/bulk")
        assertEquals("complete", b["op"]!!.jsonPrimitive.content)
        assertEquals(setOf("t1", "t2"), b["uids"]!!.jsonArray.map { it.jsonPrimitive.content }.toSet())
        val m = lastMessage()
        assertEquals("Completed 2 tasks", m.text)
        runBlocking { m.action!!.invoke() }
        h.until("reopen") { h.requests.any { it.first.path == "/api/v1/tasks/bulk" && it.second.contains("\"reopen\"") } }
        keep.cancel()
    }

    /** task.bulk_reschedule (+1 day keeps the time, one undo restores each due), task.bulk_move, task.bulk_tag, task.bulk_priority, task.bulk_delete. */
    @Test fun bulkVerbs() {
        seed(TaskItem("t2", "Second", today.toString(), list = "L1", categories = listOf("old")))
        val v = vm()
        fun pick() { v.clearSelection(); v.toggleSelect("t1"); v.toggleSelect("t2"); h.until("tasks") { v.tasks.value?.size == 2 } }
        val keep = v.viewModelScope.launch { v.tasks.collect {} }
        pick()
        v.bulkShift(1)
        h.until("shift") { h.requests.count { it.first.path == "/api/v1/tasks/bulk" } >= 2 }
        val dues = h.requests.filter { it.first.path == "/api/v1/tasks/bulk" }.map { MailrJson.parseToJsonElement(it.second) as JsonObject }
            .map { (it["args"] as JsonObject)["due"]!!.jsonPrimitive.content }.toSet()
        assertTrue(today.plusDays(1).toString() in dues)
        val undo = lastMessage()
        runBlocking { undo.action!!.invoke() }
        h.until("undo restores both dues") { h.requests.count { it.first.path == "/api/v1/tasks/bulk" } >= 4 }

        pick(); v.bulkMove("L2")
        h.until("move") { h.requests.any { it.second.contains("\"move\"") } }
        pick(); v.bulkTag(setOf("new"), setOf("old"))
        h.until("tag") { h.requests.any { it.second.contains("\"tag\"") } && h.requests.any { it.second.contains("\"untag\"") } }
        pick(); v.bulkPriority(3)
        h.until("priority") { h.requests.any { it.second.contains("\"priority\"") && it.second.contains("\"uids\"") } }
        pick(); v.bulkDelete()
        h.until("delete scheduled") { h.delayed.count { it.method == "DELETE" } == 2 && messages.last().text.startsWith("Deleted") }
        runBlocking { lastMessage().action!!.invoke() }
        h.until("undo") { h.delayed.isEmpty() }
        keep.cancel()
    }

    // ---------------- reorder and indent ----------------

    /** In manual order: "Move down" places a task after its neighbour (task.reorder); "Make subtask" puts it under the row above (task.reparent). */
    @Test fun reorderAndIndent() {
        val a = TaskItem("a", "Alpha", null, list = "L1", sortOrder = 1024)
        val b = TaskItem("b", "Beta", null, list = "L1", sortOrder = 2048)
        val c = TaskItem("c", "Gamma", null, list = "L1", sortOrder = 3072)
        seed(a, b, c)
        val v = vm()
        compose.setContent {
            MaterialTheme {
                TaskGroupList(listOf(TaskGroup("list", "", listOf(a, b, c).map { io.github.murtazanasir.mailr.ui.tasks.TaskEntry(it) })),
                    AppClock.millis(), emptyList(), emptySet(), SwipeAction.COMPLETE to SwipeAction.RESCHEDULE,
                    RowStyle(showList = false, manual = true, expandSubtasks = true), actions(v))
            }
        }
        fun act(row: String, label: String) {
            val node = compose.onNode(hasText(row) and SemanticsMatcher.keyIsDefined(SemanticsActions.CustomActions)).fetchSemanticsNode()
            compose.runOnIdle { node.config[SemanticsActions.CustomActions].first { it.label == label }.action() }
        }
        act("Alpha", "Move down")
        val move = body("POST", "tasks/a/move")
        assertEquals("b", move["after"]!!.jsonPrimitive.content)
        assertEquals("c", move["before"]!!.jsonPrimitive.content)
        act("Gamma", "Make subtask")
        h.until("reparent") { h.requests.any { it.first.path == "/api/v1/tasks/c/move" } }
        val re = MailrJson.parseToJsonElement(h.requests.last { it.first.path == "/api/v1/tasks/c/move" }.second) as JsonObject
        assertEquals("b", re["parent"]!!.jsonPrimitive.content)
    }

    /**
     * One Undo for each move (task.reorder, task.reparent): it goes back through the same POST /move,
     * after its old neighbour above, or before the one below when it was first, and under its old
     * parent. Reopening a done subtask has an Undo too (task.reopen).
     */
    @Test fun undoPutsMovesBack() {
        val a = TaskItem("a", "Alpha", null, list = "L1", sortOrder = 1024)
        val b = TaskItem("b", "Beta", null, list = "L1", sortOrder = 2048)
        val c = TaskItem("c", "Gamma", null, list = "L1", sortOrder = 3072)
        val p = TaskItem("p", "Packing", null, list = "L1", sortOrder = 4096)
        val s = TaskItem("s", "Passport", null, status = "completed", list = "L1", parent = "p", sortOrder = 1024)
        seed(a, b, c, p, s)
        val v = vm()
        fun moves(uid: String) = h.requests.filter { it.first.path == "/api/v1/tasks/$uid/move" }
            .map { MailrJson.parseToJsonElement(it.second) as JsonObject }

        // Alpha (first) moved after Beta; Undo puts it before Beta again.
        v.reorder(a, "b", "c")
        h.until("the move") { moves("a").size == 1 }
        val moved = lastMessage()
        assertEquals("Task moved", moved.text)
        runBlocking { moved.action!!() }
        h.until("the undo") { moves("a").size == 2 }
        assertEquals("b", moves("a")[1]["before"]!!.jsonPrimitive.content)
        assertEquals(null, moves("a")[1]["after"])

        // Gamma under Beta; Undo: the top level again, after Beta.
        messages.clear()
        v.reparent(c, "b")
        h.until("the reparent") { moves("c").size == 1 }
        val sub = lastMessage()
        runBlocking { sub.action!!() }
        h.until("the undo") { moves("c").size == 2 }
        assertEquals("", moves("c")[1]["parent"]!!.jsonPrimitive.content)
        assertEquals("b", moves("c")[1]["after"]!!.jsonPrimitive.content)

        // A done subtask reopened (its check in the list or the editor); Undo completes it again.
        messages.clear()
        v.setDone(s, false)
        h.until("the reopen") { h.requests.any { it.first.path == "/api/v1/tasks/s" && it.second.contains("\"completed\":false") } }
        val reopened = lastMessage()
        assertEquals("Reopened “Passport”", reopened.text)
        runBlocking { reopened.action!!() }
        h.until("the undo") { h.requests.any { it.first.path == "/api/v1/tasks/s" && it.second.contains("\"completed\":true") } }
    }

    // ---------------- list manager ----------------

    /** list.create, list.rename, list.color, list.delete (move the tasks first), tag.rename, tag.delete. */
    @Test fun listManager() {
        val work = CalendarCollection("L1", "Work", tasks = true, calendarId = 11)
        val home = CalendarCollection("L2", "Home", tasks = true, calendarId = 12)
        lists(work, home)
        seed(TaskItem("w1", "Work task", null, list = "L1", categories = listOf("call")))
        val v = vm()
        h.until("tasks") { runBlocking { h.g.calendar.tasksNow().size } == 2 }
        val keep = v.viewModelScope.launch { v.tasks.collect {} }
        h.until("tasks in vm") { v.tasks.value?.size == 2 }
        compose.setContent {
            MaterialTheme {
                ListManager(listOf(ListCount(work, 1), ListCount(home, 0)), listOf(io.github.murtazanasir.mailr.data.api.TagInfo("call", 1)), ListManagerActions(
                    create = { n, c -> v.createList(n, c) }, update = { c, n, col -> v.updateList(c, n, col) }, reorder = { v.reorderLists(it) },
                    delete = { c, to -> v.deleteList(c, to, c.name) }, renameTag = { o, n -> v.renameTag(o, n) }, deleteTag = { v.deleteTag(it) },
                    close = {}))
            }
        }
        // list.create, list.rename, list.color and tag.rename go through the same calls as the manager's dialogs.
        v.createList("Errands", "#0b8043")
        val created = body("POST", "calendar/collections")
        assertEquals("Errands", created["name"]!!.jsonPrimitive.content)
        assertEquals("#0b8043", created["color"]!!.jsonPrimitive.content)
        assertEquals("tasks", created["kind"]!!.jsonPrimitive.content)
        v.updateList(work, "Office", "#d50000")
        val patched = body("PATCH", "calendar/collections/11")
        assertEquals("Office", patched["name"]!!.jsonPrimitive.content)
        assertEquals("#d50000", patched["color"]!!.jsonPrimitive.content)
        v.reorderLists(listOf("L2", "L1"))
        h.until("reorder") { h.requests.any { it.first.path == "/api/v1/calendar/collections/11" && it.second.contains("sort_order") } }
        v.renameTag("call", "phone")
        h.until("tag renamed") { h.requests.any { it.second.contains("\"untag\"") && it.second.contains("\"call\"") } }
        // tag.delete from the manager: confirmed, then removed from every task.
        compose.onNodeWithContentDescription("Delete @call").performClick()
        compose.onNodeWithText("Delete").performClick()
        h.until("tag deleted") { messages.any { it.text.startsWith("Tag @call removed") } }
        // Delete: its task moves to Home first, then the list goes (confirmed twice).
        compose.onNodeWithContentDescription("More for Work").performClick()
        compose.onNodeWithText("Delete").performClick()
        compose.onNodeWithText("Delete list").performClick()
        compose.onNodeWithText("Delete").performClick()
        h.until("delete list") { h.requests.any { it.first.method == "DELETE" && it.first.path!!.startsWith("/api/v1/calendar/collections/11") } }
        val move = body("POST", "tasks/bulk")
        assertEquals("move", move["op"]!!.jsonPrimitive.content)
        assertEquals("L2", (move["args"] as JsonObject)["list"]!!.jsonPrimitive.content)
        assertTrue(h.requests.last { it.first.method == "DELETE" }.first.path!!.contains("confirm=Work"))
        keep.cancel()
    }

    // ---------------- repeating tasks ----------------

    /**
     * task.complete on a repeating task: the server advances it; the snackbar says "Next: …" and
     * Undo is PATCH completed false, which the server answers by restoring the due and deleting
     * the history copy. task.skip likewise.
     */
    @Test fun repeatingCompleteAndSkip() {
        val due = today.plusDays(1).toString()
        val next = today.plusDays(8).toString()
        val weekly = TaskItem("r1", "Water plants", due, recurring = true, rrule = "FREQ=WEEKLY")
        seed(weekly)
        answers["PATCH /api/v1/tasks/r1"] = """{"task":{"uid":"r1","summary":"Water plants","due":"$next","recurring":true,"rrule":"FREQ=WEEKLY","etag":"\"e2\""},"next":"$next"}"""
        val v = vm()
        v.complete(weekly)
        val m = lastMessage()
        assertTrue(m.text, m.text.startsWith("Next: "))
        assertEquals(true, body("PATCH", "tasks/r1")["completed"]!!.jsonPrimitive.boolean)
        // The completion names the due it is for (one advance per base due).
        assertEquals(due, body("PATCH", "tasks/r1")["occurrence"]!!.jsonPrimitive.content)
        answers["PATCH /api/v1/tasks/r1"] = """{"uid":"r1","summary":"Water plants","due":"$due","recurring":true,"rrule":"FREQ=WEEKLY","etag":"\"e4\"","restored":"$due"}"""
        runBlocking { m.action!!.invoke() }
        val undo = body("PATCH", "tasks/r1")
        assertEquals(false, undo["completed"]!!.jsonPrimitive.boolean)
        assertEquals(null, undo["due"])
        h.until("due restored") { h.storedTask("r1")?.due == due }

        messages.clear()
        answers["POST /api/v1/tasks/r1/skip"] = """{"task":{"uid":"r1","summary":"Water plants","due":"$next","recurring":true,"etag":"\"e3\""},"next":"$next"}"""
        v.skip(weekly)
        h.until("skip") { h.requests.any { it.first.path == "/api/v1/tasks/r1/skip" } }
        assertTrue(lastMessage().text.startsWith("Skipped · Next: "))
    }

    /** task.move_list, task.set_priority, task.remind_at, task.duplicate, task.clear_completed. */
    @Test fun singleTaskVerbs() {
        seed(TaskItem("done1", "Old done", null, list = "L1", status = "completed"))
        val v = vm()
        val keep = v.viewModelScope.launch { v.tasks.collect {} }
        h.until("tasks") { v.tasks.value?.size == 2 }
        v.moveToList(h.task, "L9")
        assertEquals("L9", body("POST", "tasks/t1/move")["list"]!!.jsonPrimitive.content)
        v.setPriority(h.task, 3)
        h.until("priority") { h.requests.any { it.first.method == "PATCH" && it.second.contains("\"priority\":1") } }
        v.remindAt(h.task, 30)
        h.until("reminder") { h.requests.any { it.first.method == "PATCH" && it.second.contains("30") && it.second.contains("reminders") } }
        v.duplicate(h.task)
        h.until("duplicate") { h.requests.any { it.first.method == "POST" && it.first.path == "/api/v1/tasks" } }
        // ACT task.subtask_add: a line under a task (the grammar applies).
        v.quickAdd("Call the venue tomorrow", parent = "t1")
        h.until("subtask") { h.requests.any { it.first.path == "/api/v1/tasks" && it.second.contains("\"parent\":\"t1\"") } }
        v.clearCompleted("L1")
        h.until("clear completed") { h.requests.any { it.first.path == "/api/v1/tasks/bulk" && it.second.contains("\"delete\"") && it.second.contains("done1") } }
        keep.cancel()
    }

    // ---------------- share target ----------------

    /** android.share → task.create: the editor opens with the shared title and the link in the notes; Save adds the task. */
    @Test fun shareIntentOpensTheEditor() {
        val draft = ShareTask.draft(SharedContent(subject = "Read the release notes", text = "https://kotlinlang.org/docs/whatsnew21.html"), "share-1")
        var finished: String? = null
        compose.setContent { MaterialTheme { ShareTaskScreen(h.g, draft, 9) { finished = it ?: "closed" } } }
        compose.onNodeWithText("Read the release notes").assertExists()
        compose.onNodeWithText("https://kotlinlang.org/docs/whatsnew21.html").assertExists()
        compose.onNodeWithText("Save").performClick()
        val sent = body("POST", "tasks")
        assertEquals("Read the release notes", sent["summary"]!!.jsonPrimitive.content)
        assertEquals("https://kotlinlang.org/docs/whatsnew21.html", sent["notes"]!!.jsonPrimitive.content)
        h.until("finished") { finished != null }
    }

    // ---------------- conflicts ----------------

    /** Changed elsewhere: the sheet shows their version and mine; Use mine sends my change again; Keep both adds a copy. */
    @Test fun conflictSheet() {
        val server = h.task.copy(summary = "Send the Q4 numbers", etag = "\"srv\"")
        runBlocking {
            h.g.db.calendar().putConflict(CalConflictEntity("task|t1", "task", "t1", "PATCH", "tasks/t1",
                """{"summary":"Send the Q3 numbers to Dana and Lee"}""", MailrJson.encodeToString(TaskItem.serializer(), server), "changed", 0))
        }
        val v = vm()
        var conflicts: List<CalConflict> = emptyList()
        val job = v.viewModelScope.launch { v.conflicts.collect { conflicts = it } }
        h.until("conflict") { conflicts.isNotEmpty() }
        val c = conflicts.first()
        var chosen = ""
        compose.setContent { MaterialTheme { ConflictSheet(c, onTheirs = { chosen = "theirs" }, onMine = { chosen = "mine"; v.keepMine(c) }, onBoth = { chosen = "both" }) } }
        compose.onNodeWithText("Changed elsewhere").assertExists()
        compose.onNodeWithText("Send the Q4 numbers").assertExists()
        compose.onNodeWithText("Send the Q3 numbers to Dana and Lee").assertExists()
        compose.onNodeWithText("Use mine").performClick()
        assertEquals("mine", chosen)
        assertEquals("Send the Q3 numbers to Dana and Lee", body("PATCH", "tasks/t1")["summary"]!!.jsonPrimitive.content)
        h.until("resolved") { conflicts.isEmpty() }
        job.cancel()
    }
}
