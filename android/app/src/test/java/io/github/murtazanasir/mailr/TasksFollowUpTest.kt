// ACT-SURFACES: android.editor android.tasks
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.test.hasScrollAction
import androidx.compose.ui.test.hasText
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performScrollToNode
import androidx.compose.ui.test.performTextInput
import androidx.lifecycle.viewModelScope
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import io.github.murtazanasir.mailr.data.api.ConversationDetail
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.api.toDraft
import io.github.murtazanasir.mailr.data.db.CalCollectionEntity
import io.github.murtazanasir.mailr.data.db.CalTaskEntity
import io.github.murtazanasir.mailr.data.settings.Prefs
import io.github.murtazanasir.mailr.ui.calendar.TaskEditor
import io.github.murtazanasir.mailr.ui.calendar.TaskEditorState
import io.github.murtazanasir.mailr.ui.calendar.TasksViewModel
import io.github.murtazanasir.mailr.ui.mail.UiMessage
import io.github.murtazanasir.mailr.ui.settings.SettingsCallbacks
import io.github.murtazanasir.mailr.ui.settings.SettingsScreen
import io.github.murtazanasir.mailr.ui.tasks.QuickPrefill
import io.github.murtazanasir.mailr.ui.tasks.Smart
import io.github.murtazanasir.mailr.ui.tasks.SwipeAction
import io.github.murtazanasir.mailr.ui.tasks.TaskQuickAddBar
import io.github.murtazanasir.mailr.ui.tasks.TaskUiPrefs
import io.github.murtazanasir.mailr.ui.tasks.TaskView
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId

/**
 * The tasks follow-ups on Android: the series question when the due of a repeating task changes, subtask
 * mode in quick add (the web's Tab), the default list, tag colours, "At a time…" reminders as
 * `reminders_at`, a list from an app link, the reply composer and the swipe actions in Settings.
 *
 * Covers: task.reschedule, task.edit, task.subtask_add, task.create, list.set_default, tag.color, task.remind_at,
 * task.reply_email, mail.follow_up, mail.to_task.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class TasksFollowUpTest {
    @get:Rule val compose = createComposeRule()
    private val h = CalendarHarness()
    private val answers = mutableMapOf<String, String>()
    private val messages = mutableListOf<UiMessage>()
    private val today: LocalDate get() = h.now.toLocalDate()

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

    private fun seed(vararg ts: TaskItem) = runBlocking {
        h.g.db.calendar().putTasks(ts.map { t ->
            CalTaskEntity(t.uid, t.list, t.parent, t.completed, null, MailrJson.encodeToString(TaskItem.serializer(), t), sortOrder = t.sortOrder)
        })
    }

    private fun vm(): TasksViewModel = h.vm { TasksViewModel(h.g) }.also { v -> v.viewModelScope.launch { v.messages.collect { messages += it } } }

    private fun lastMessage(): UiMessage {
        h.until("a message") { messages.isNotEmpty() }
        return messages.last()
    }

    /** mail.follow_up and mail.to_task in the Follow-ups section: move, cancel, and turn one into a task. */
    @Test fun followUpRowsMoveCancelAndBecomeATask() {
        fun at(days: Long) = today.plusDays(days).atTime(9, 0).atZone(ZoneId.systemDefault()).toOffsetDateTime().toString()
        val c = Fixtures.detail.conversation.copy(followUpAt = at(2))
        val v = vm()
        v.changeFollowUp(c, at(3))
        val moved = h.sent("POST", "/api/v1/conversations/actions")
        assertEquals("follow_up", moved["action"]!!.jsonPrimitive.content)
        assertEquals(at(3), moved["until"]!!.jsonPrimitive.content)
        assertEquals(listOf(c.id), moved["ids"]!!.jsonArray.map { it.jsonPrimitive.long })
        v.changeFollowUp(c, null)
        h.until("the cancel") { h.requests.count { it.first.path == "/api/v1/conversations/actions" } == 2 }
        assertEquals("cancel_follow_up", h.sent("POST", "/api/v1/conversations/actions")["action"]!!.jsonPrimitive.content)

        answers["GET /api/v1/conversations/${c.id}"] = MailrJson.encodeToString(ConversationDetail.serializer(), Fixtures.detail)
        answers["POST /api/v1/calendar/from-message/confirm"] = """{"uid":"fu1","summary":"Follow up","conversation_id":${c.id}}"""
        v.followUpToTask(c)
        val made = h.sent("POST", "/api/v1/calendar/from-message/confirm")
        assertEquals("task", made["kind"]!!.jsonPrimitive.content)
        assertEquals(502L, made["message_id"]!!.jsonPrimitive.long)
        assertTrue(made["title"]!!.jsonPrimitive.content.startsWith("Follow up: "))
        h.until("Task added") { messages.any { it.text == "Task added" } }
    }

    private val weekly get() = TaskItem("r1", "Water plants", today.plusDays(1).toString(), recurring = true, rrule = "FREQ=WEEKLY")

    /** task.reschedule on a repeating task: the series question first; "This occurrence" pins the rule to the old weekday. */
    @Test fun repeatingRescheduleAsksTheScope() {
        seed(weekly)
        val v = vm()
        val moved = today.plusDays(3).toString()
        v.requestReschedule(weekly, moved)
        assertEquals(moved, v.duePrompt.value?.due)
        assertTrue(h.sentNothing())
        v.answerDueScope("this")
        val body = h.sent("PATCH", "/api/v1/tasks/r1")
        assertEquals(moved, body["due"]!!.jsonPrimitive.content)
        val day = today.plusDays(1).dayOfWeek.name.take(2)
        assertEquals("FREQ=WEEKLY;BYDAY=$day", body["recurrence"]!!.jsonPrimitive.content)
        assertNull(v.duePrompt.value)
    }

    /** task.edit: the editor's new due on a repeating task waits for the scope; "All" moves the series as it is. */
    @Test fun editorDueChangeOnARepeatingTaskAsksTheScope() {
        seed(weekly)
        val v = vm()
        var done: Boolean? = null
        val moved = today.plusDays(3).toString()
        v.save(weekly, weekly.toDraft().copy(due = moved)) { done = it }
        assertTrue(v.duePrompt.value?.draft != null)
        v.answerDueScope("all")
        h.until("saved") { done == true }
        val body = h.sent("PATCH", "/api/v1/tasks/r1")
        assertEquals(moved, body["due"]!!.jsonPrimitive.content)
        assertNull(body["recurrence"])
        // Cancelling the scope leaves the task as it was.
        v.save(weekly, weekly.toDraft().copy(due = today.plusDays(5).toString())) { done = it }
        v.answerDueScope(null)
        assertEquals(false, done)
    }

    /** task.subtask_add in quick add: the toggle chip makes the next lines subtasks of the last task added. */
    @Test fun subtaskModeInQuickAdd() {
        val v = vm()
        v.quickAddLine("Plan the trip")
        val parent = h.sent("POST", "/api/v1/tasks")["uid"]!!.jsonPrimitive.content
        h.until("last added") { v.lastAdded.value?.first == parent }
        var on = false
        compose.setContent {
            MaterialTheme {
                val focus = androidx.compose.runtime.remember { androidx.compose.ui.focus.FocusRequester() }
                TaskQuickAddBar(emptyList(), QuickPrefill(), { h.now }, focus, onAdd = { _, _ -> }, onDetails = { _, _ -> }, onClearPrefill = {},
                    subtaskOf = v.lastAdded.value?.second, subtask = on, onSubtask = { on = it; v.setSubtaskMode(it) })
            }
        }
        compose.onNodeWithText("Subtask").performClick()
        assertTrue(v.subtaskMode.value)
        h.requests.clear()
        v.quickAddLine("Book flights")
        val child = h.sent("POST", "/api/v1/tasks")
        assertEquals("Book flights", child["summary"]!!.jsonPrimitive.content)
        assertEquals(parent, child["parent"]!!.jsonPrimitive.content)
        assertEquals(parent, v.lastAdded.value?.first)
    }

    /** list.set_default and tag.color: online writes, each with an undo. */
    @Test fun defaultListAndTagColour() {
        runBlocking {
            val c = CalendarCollection("/l/home/", "Home", tasks = true, calendarId = 4)
            h.g.db.calendar().putCollections(listOf(CalCollectionEntity(c.url, 4, c.name, null, false, true, false, 0, null,
                MailrJson.encodeToString(CalendarCollection.serializer(), c))))
            h.g.calendar.loadCached()
        }
        answers["PUT /api/v1/calendar/defaults"] = """{"calendar":null,"task_list":"/l/home/"}"""
        val v = vm()
        h.until("lists") { v.collections.value.isNotEmpty() }
        v.setDefaultList(v.collections.value.single())
        assertEquals("/l/home/", h.sent("PUT", "/api/v1/calendar/defaults")["task_list"]!!.jsonPrimitive.content)
        h.until("default list") { v.defaults.value.taskList == "/l/home/" }
        v.setTagColor("home", "#039be5")
        assertEquals("#039be5", h.sent("PATCH", "/api/v1/tasks/tags/home")["color"]!!.jsonPrimitive.content)
        h.until("colour message") { messages.any { it.text.startsWith("Colour") && it.actionLabel == "Undo" } }
    }

    /** task.remind_at "At a time…": an absolute reminder goes out as reminders_at, in the sheet and in the editor. */
    @Test fun atATimeIsAnAbsoluteReminder() {
        val v = vm()
        val at = h.now.plusHours(1).toInstant().toString()
        v.remindAtTime(h.task, at)
        assertEquals(listOf(at), h.sent("PATCH", "/api/v1/tasks/t1")["reminders_at"]!!.jsonArray.map { it.jsonPrimitive.content })

        val s = TaskEditorState(h.task, null, null, h.task.uid)
        val when_ = LocalDateTime.of(today, java.time.LocalTime.of(8, 0))
        s.addReminderAt(when_)
        val d = s.draft()
        assertEquals(listOf(when_.atZone(java.time.ZoneId.systemDefault()).toInstant().toString()), d.remindersAt)
        // The relative reminders stay as they were.
        assertEquals(h.task.reminders, d.reminders)
    }

    /** task.reply_email: the editor's "Reply to the email" opens the reply composer for the task's email. */
    @Test fun replyFromTheEditor() {
        var replied: Long? = null
        compose.setContent {
            MaterialTheme {
                TaskEditor(TaskEditorState(h.task, null, null, h.task.uid), emptyList(), emptyList(), 9, saving = false, onClose = {}, onSave = {},
                    onDelete = null, onAddSubtask = null, onToggleSubtask = {}, onOpenSubtask = {},
                    onReplyEmail = { replied = h.task.conversationId })
            }
        }
        compose.onNodeWithText("Reply to the email").performScrollTo().performClick()
        assertEquals(3066L, replied)
    }

    /** A list from an app link (`tasks/l/<id>`, `tasks/today`) opens that view. */
    @Test fun appLinkOpensAList() {
        runBlocking {
            val c = CalendarCollection("/l/work/", "Work", tasks = true, calendarId = 12)
            h.g.db.calendar().putCollections(listOf(CalCollectionEntity(c.url, 12, c.name, null, false, true, false, 0, null,
                MailrJson.encodeToString(CalendarCollection.serializer(), c))))
            h.g.calendar.loadCached()
        }
        val v = vm()
        h.until("lists") { v.collections.value.isNotEmpty() }
        v.openRoute("l/12")
        assertEquals(TaskView.ListView("/l/work/"), v.view.value)
        v.openRoute("upcoming")
        assertEquals(TaskView.SmartView(Smart.UPCOMING), v.view.value)
    }

    /** The swipe actions in Settings: the entry opens the dialog, and the Tasks screen follows the saved choice. */
    @Test fun swipeActionsInSettings() {
        var opened = false
        compose.setContent {
            MaterialTheme {
                SettingsScreen(Prefs(), "Me", "me@example.com", "https://mail.example.com", emptyList(),
                    SettingsCallbacks(back = {}, update = {}, revoke = {}, signOut = {}, openWeb = {},
                        taskSwipes = { opened = true }, taskSwipeSummary = "Right: Complete · Left: Reschedule"))
            }
        }
        compose.onNode(hasScrollAction()).performScrollToNode(hasText("Task swipe actions"))
        compose.onNodeWithText("Task swipe actions").performClick()
        compose.onNodeWithText("Right: Complete · Left: Reschedule").assertExists()
        assertTrue(opened)
        val v = vm()
        TaskUiPrefs(h.app).swipeLeft = SwipeAction.DELETE
        h.until("swipe follows") { v.swipe.value.second == SwipeAction.DELETE }
        assertEquals(SwipeAction.DELETE, runBlocking { TaskUiPrefs(h.app).swipes().first().second })
    }
}
