// ACT-SURFACES: android.editor
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.onAllNodesWithContentDescription
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import io.github.murtazanasir.mailr.data.api.CalendarApi
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.ui.calendar.TaskEditor
import io.github.murtazanasir.mailr.ui.calendar.TaskEditorState
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.time.ZoneId

/**
 * task.remind_at in the editor: an absolute reminder set days after the due (the assistant's edit of
 * 10 Oct 2026) is shown as its own chip with its local time, and its close button removes it; the
 * PATCH then sends `reminders_at` [] so the server drops it, and the relative reminders stay.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class TaskAbsoluteReminderTest {
    @get:Rule val compose = createComposeRule()

    private val zone = ZoneId.systemDefault()
    private val today = AppClock.today()
    private val due = today.minusDays(2).atTime(8, 0).atZone(zone).toOffsetDateTime().toString()
    private val at = today.plusDays(3).atTime(6, 0).atZone(zone).toInstant().toString()
    private val task = TaskItem("t1", "Finish the midterm", due, list = "L1", reminders = listOf(30), remindersAt = listOf(at))

    @Test fun anAbsoluteReminderDaysAfterTheDueIsShownAndRemoved() {
        val s = TaskEditorState(task, null, null, task.uid)
        compose.setContent {
            MaterialTheme {
                TaskEditor(s, emptyList(), emptyList(), 9, saving = false, onClose = {}, onSave = {}, onDelete = null,
                    onAddSubtask = null, onToggleSubtask = {}, onOpenSubtask = {})
            }
        }
        compose.waitForIdle()
        // Two chips: the relative one ("… before") and the absolute one, labelled with its day and time.
        fun labels() = compose.onAllNodesWithContentDescription("Remove reminder", substring = true).fetchSemanticsNodes()
            .map { it.config[SemanticsProperties.ContentDescription].joinToString() }
        val absolute = labels().single { "before" !in it }
        assertEquals(2, labels().size)
        compose.onNodeWithContentDescription(absolute).performScrollTo().performClick()
        compose.waitForIdle()
        assertEquals(1, labels().size)

        val d = s.draft()
        assertEquals(emptyList<String>(), d.remindersAt)
        assertEquals(listOf(30), d.reminders)
        val body = MailrJson.parseToJsonElement(CalendarApi.taskBody(d, create = false)) as JsonObject
        assertEquals(emptyList<String>(), body["reminders_at"]!!.jsonArray.map { it.jsonPrimitive.content })
        assertEquals(listOf("30"), body["reminders"]!!.jsonArray.map { it.jsonPrimitive.content })
    }
}
