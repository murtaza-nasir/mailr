// ACT-SURFACES: android.widget android.settings
// Shots of the Today widget and of Settings → Activity (activity.list, read-only on the phone).
package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.time.AppClock
import android.app.Application
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.ActivityItem
import io.github.murtazanasir.mailr.data.api.ActivityPage
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.ui.ask.AskWidgetContent
import io.github.murtazanasir.mailr.ui.settings.ActivityScreen
import io.github.murtazanasir.mailr.ui.settings.ActivityUi
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import io.github.murtazanasir.mailr.ui.today.TodayModel
import io.github.murtazanasir.mailr.ui.today.TodayWidgetContent
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.time.ZoneId
import java.time.ZonedDateTime

/**
 * The home-screen widgets, light and dark: the Today widget at three sizes (task.complete, agent.dictate
 * on android.widget), an empty day at night, the "Talk to Ambrose" widget at 1x1 and wide, and
 * Settings → Activity (read-only). Demo data only.
 */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class TodayScreenshotTest(private val dark: Boolean) {
    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun params(): List<Array<Any>> = listOf(arrayOf<Any>(false), arrayOf<Any>(true))
    }

    @get:Rule val compose = createComposeRule()
    private val mode get() = if (dark) "dark" else "light"
    private val zone = ZoneId.of("America/Chicago")
    private val morning = ZonedDateTime.of(2026, 10, 6, 9, 10, 0, 0, zone)

    private fun at(h: Int, m: Int = 0) = morning.withHour(h).withMinute(m).toOffsetDateTime().toString()

    private val events = listOf(
        CalendarEvent("e1", "Design review with Dana", at(9), at(10), calendar = "work", calendarColor = "#1a73e8"),
        CalendarEvent("e2", "Lunch with Jordan", at(12, 30), at(13, 30), calendar = "home", calendarColor = "#0b8043"),
        CalendarEvent("e3", "Piano lesson", at(17), at(18), calendar = "family", calendarColor = "#8e24aa"),
    )
    private val tasks = listOf(
        TaskItem("t1", "Renew the studio insurance", "2026-10-02", dueAllDay = true),
        TaskItem("t2", "Send the Q3 numbers to Dana", at(15)),
        TaskItem("t3", "Buy oat milk", "2026-10-06", dueAllDay = true),
        TaskItem("t4", "Book the dentist", "2026-10-06", dueAllDay = true),
    )
    private val full get() = TodayModel.build(events, tasks, morning)

    /** The Glance content on a home-screen colour, as the launcher would draw it. */
    private fun widget(name: String, wDp: Int, hDp: Int, content: @Composable () -> Unit) {
        RuntimeEnvironment.setQualifiers("w411dp-h891dp-port-${if (dark) "night" else "notnight"}-xxhdpi")
        val ctx = ApplicationProvider.getApplicationContext<Application>()
        homeScreen(ctx, dark, wDp + 32, hDp + 32, listOf(PlacedWidget(16, 16, wDp, hDp, content)))
            .captureRoboImage("screenshots/today-$mode-$name.png")
    }

    @Test fun todayLarge() = widget("large", 360, 380) { TodayWidgetContent(full) }

    @Test fun todayMedium() = widget("medium", 360, 220) {
        TodayWidgetContent(TodayModel.build(events, tasks, morning.withHour(14)).copy(undo = TodayModel.Undo("t9", "Call the plumber")))
    }

    @Test fun todayCompact() = widget("compact", 300, 110) { TodayWidgetContent(TodayModel.build(events, tasks, morning.withHour(19))) }

    @Test fun todayEmptyNight() = widget("empty", 360, 220) { TodayWidgetContent(TodayModel.build(emptyList(), emptyList(), morning.withHour(23))) }

    @Test fun talkSquare() = widget("talk-1x1", 72, 88) {
        AskWidgetContent(ApplicationProvider.getApplicationContext<Application>())
    }

    @Test fun talkWide() = widget("talk-wide", 300, 64) {
        AskWidgetContent(ApplicationProvider.getApplicationContext<Application>())
    }

    private val now = AppClock.now().withHour(16).withMinute(0)
    private fun ago(minutes: Long) = now.minusMinutes(minutes).toOffsetDateTime().toString()
    private val activity = listOf(
        ActivityItem("1", ago(5), "you", null, "tasks", "Completed “Buy oat milk”"),
        ActivityItem("2", ago(40), "ambrose", null, "calendar", "Moved “Piano lesson” to 5 PM"),
        ActivityItem("3", ago(95), "agent", "Research agent", "mail", "Labelled 3 conversations Research"),
        ActivityItem("4", ago(60 * 26), "admin", "Sam Lee", "account", "Your administrator added you to the group Studio."),
        ActivityItem("5", ago(60 * 27), "you", null, "mail", "Archived 12 conversations", undone = true),
    )

    @Test fun activityScreen() {
        RuntimeEnvironment.setQualifiers("w411dp-h891dp-port-${if (dark) "night" else "notnight"}-xxhdpi")
        var more = 0
        compose.setContent {
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.surface) {
                    ActivityScreen(ActivityUi(activity, loading = false, more = true), {}, { more++ }, {}, now)
                }
            }
        }
        compose.onRoot().captureRoboImage("screenshots/activity-$mode.png")
        compose.onNodeWithText("Ambrose · ", substring = true).assertExists()
        compose.onNodeWithText("Research agent", substring = true).assertExists()
        compose.onNodeWithText("Your administrator (Sam Lee)", substring = true).assertExists()
        compose.onNodeWithText("Undone", substring = true).assertExists()
        compose.onNodeWithText("Yesterday").assertExists()
        compose.onNodeWithText("Show earlier").performClick()
        assertEquals(1, more)
    }

    /** GET /activity: ids may come as numbers or strings. */
    @Test fun activityParses() {
        val page = MailrJson.decodeFromString(ActivityPage.serializer(), """{"items":[{"id":12,"at":"2026-10-06T15:00:00+00:00","who":"agent",
            "by":"Research agent","app":"mail","text":"Archived 3 conversations","target":null,"undo":{"method":"POST","path":"/x","body":{}},
            "undone":false,"ok":true}],"next_cursor":"abc"}""")
        assertEquals("12", page.items.single().id)
        assertEquals("Research agent", page.items.single().actor)
        assertEquals("abc", page.nextCursor)
    }
}
