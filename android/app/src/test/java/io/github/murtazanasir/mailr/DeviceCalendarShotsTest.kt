package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.ui.Modifier
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/** Calendar and Tasks: agenda, week, month, event detail and editor, the task list and editor. On every device of [DeviceShots], light and dark. */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class DeviceCalendarShotsTest(device: String, qualifiers: String, dark: Boolean) : DeviceShots(device, qualifiers, dark) {
    @Test fun calendarAgenda() = shot("calendar-agenda") {
        io.github.murtazanasir.mailr.ui.calendar.AgendaView(calEvents, today, noon, null, {}, {})
    }

    @Test fun calendarWeek() = shot("calendar-week") {
        val start = io.github.murtazanasir.mailr.ui.calendar.weekStart(today)
        io.github.murtazanasir.mailr.ui.calendar.TimeGrid((0L until 7L).map { start.plusDays(it) }, calEvents, noon, null, {}, {}, {})
    }

    @Test fun calendarMonth() = shot("calendar-month") {
        io.github.murtazanasir.mailr.ui.calendar.MonthView(today, calEvents, device != "phone", {}, {}, Modifier.fillMaxSize())
    }

    @Test fun eventDetail() = shot("event-detail") {
        io.github.murtazanasir.mailr.ui.calendar.EventDetail(Fixtures.events[1], null, emptyList(), 9, {}, {}, {}, {})
    }

    @Test fun eventEditor() = shot("event-editor") {
        val s = androidx.compose.runtime.remember {
            io.github.murtazanasir.mailr.ui.calendar.EventEditorState(null, null, today.plusDays(1).atTime(10, 0), null, 10, "uid-1")
                .apply { title = "Coffee with Sam"; location = "Blue Door Café" }
        }
        io.github.murtazanasir.mailr.ui.calendar.EventEditor(s, emptyList(), 9, false, {}, {}, null)
    }

    @Test fun tasks() = shot("tasks") {
        val groups = io.github.murtazanasir.mailr.ui.tasks.TaskGroups.build(io.github.murtazanasir.mailr.ui.tasks.TaskView.SmartView(io.github.murtazanasir.mailr.ui.tasks.Smart.ALL),
            Fixtures.tasks, noon, java.time.ZoneId.systemDefault())
        io.github.murtazanasir.mailr.ui.tasks.TaskGroupList(groups, noon, emptyList(), emptySet(),
            io.github.murtazanasir.mailr.ui.tasks.SwipeAction.COMPLETE to io.github.murtazanasir.mailr.ui.tasks.SwipeAction.RESCHEDULE,
            io.github.murtazanasir.mailr.ui.tasks.RowStyle(expandSubtasks = true),
            io.github.murtazanasir.mailr.ui.tasks.TaskListActions({}, {}, {}, { _, _ -> }, {}, { _, _, _ -> }, { _, _ -> }, {}, {}, {}))
    }

    @Test fun taskEditor() = shot("task-editor") {
        val s = androidx.compose.runtime.remember {
            io.github.murtazanasir.mailr.ui.calendar.TaskEditorState(Fixtures.tasks[3], null, null, "t5")
        }
        io.github.murtazanasir.mailr.ui.calendar.TaskEditor(s, emptyList(), listOf(Fixtures.tasks[2]), 9, false, {}, {}, {}, {}, {}, {})
    }
}
