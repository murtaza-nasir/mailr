package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.time.AppClock
import android.app.Application
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.unit.dp
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.db.ReminderEntity
import io.github.murtazanasir.mailr.data.reminders.Kind
import io.github.murtazanasir.mailr.data.reminders.RState
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.ui.calendar.QuickActionCard
import io.github.murtazanasir.mailr.ui.calendar.QuickActions
import io.github.murtazanasir.mailr.ui.calendar.SheetTarget
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.time.ZonedDateTime

/**
 * The quick-action sheet and the when sheet, light and dark, on a phone (411 dp) and an unfolded fold (673 dp).
 * Covers task.snooze, task.reschedule, event.snooze, event.remind_before, event.reschedule.
 */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class SheetScreenshotTest(private val dark: Boolean, private val width: Int) {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0},w={1}")
        fun params(): List<Array<Any>> = listOf(false, true).flatMap { d -> listOf(411, 673).map { w -> arrayOf<Any>(d, w) } }
    }

    @get:Rule val compose = createComposeRule()

    private val now = AppClock.now()
    // The user's report: "Record the class", due today at a whole hour, four hours ago.
    private val task = TaskItem("t1", "Record the class", now.minusHours(4).truncatedTo(java.time.temporal.ChronoUnit.HOURS).toOffsetDateTime().toString(),
        conversationId = 4_503_599_627_370_600L)
    private val taskReminder = ReminderEntity("t|t1|x|0", Kind.TASK, "t1", null, task.summary, "Due today", 0, 0, null, null, RState.SHOWN, updatedAt = 0)
    private val start = now.plusMinutes(40).withSecond(0).withNano(0)
    private val event = CalendarEvent("e1", "Budget review", start.toOffsetDateTime().toString(), start.plusHours(1).toOffsetDateTime().toString(),
        location = "Room 204")
    private val eventReminder = ReminderEntity("e|e1|${event.occurrence}|10", Kind.EVENT, "e1", event.occurrence, event.summary, "",
        0, 0, start.toInstant().toEpochMilli(), start.plusHours(1).toInstant().toEpochMilli(), RState.SHOWN, updatedAt = 0)

    private fun shot(name: String, content: @Composable () -> Unit) {
        RuntimeEnvironment.setQualifiers("w${width}dp-h891dp-port-${if (dark) "night" else "notnight"}-xxhdpi")
        compose.setContent {
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.scrim.copy(alpha = 0.3f)) {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.BottomCenter) {
                        Surface(shape = MaterialTheme.shapes.extraLarge, color = MaterialTheme.colorScheme.surfaceContainerHigh,
                            modifier = Modifier.fillMaxWidth().padding(12.dp)) { content() }
                    }
                }
            }
        }
        compose.onRoot().captureRoboImage("screenshots/sheet-${if (dark) "dark" else "light"}-${if (width > 600) "fold" else "phone"}-$name.png")
    }

    private val actions by lazy {
        QuickActions(graphs.graph(), ApplicationProvider.getApplicationContext())
    }

    @Test fun taskActions() = shot("task") { QuickActionCard(SheetTarget(taskReminder, task = task), actions, 9, null, run = {}) }
    @Test fun taskSnooze() = shot("task-snooze") { QuickActionCard(SheetTarget(taskReminder, task = task), actions, 9, "snooze", run = {}) }
    @Test fun taskReschedule() = shot("task-reschedule") { QuickActionCard(SheetTarget(taskReminder, task = task), actions, 9, "reschedule", run = {}) }
    @Test fun eventActions() = shot("event") { QuickActionCard(SheetTarget(eventReminder, event = event), actions, 9, null, run = {}) }
    @Test fun eventRemindBefore() = shot("event-offset") { QuickActionCard(SheetTarget(eventReminder, event = event), actions, 9, "offset", run = {}) }
    @Test fun eventSnooze() = shot("event-snooze") { QuickActionCard(SheetTarget(eventReminder, event = event), actions, 9, "snooze", run = {}) }
    @Test fun eventReschedule() = shot("event-reschedule") { QuickActionCard(SheetTarget(eventReminder, event = event), actions, 9, "reschedule", run = {}) }
}
