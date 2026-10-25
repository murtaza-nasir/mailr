// ACT-SURFACES: android.widget
// Shots of the Agenda widget: calendar.today_view, task.complete and
// agent.dictate on the home screen, in its four styles at 4×3, 4×2 and 3×2, light and dark; a few
// items at 4×3 and 4×2 (the top block the same at both) and many at 4×2 (the lists cut at the edge).
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.runtime.Composable
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.ui.agenda.AgendaModel
import io.github.murtazanasir.mailr.ui.agenda.AgendaStyle
import io.github.murtazanasir.mailr.ui.agenda.AgendaWidgetContent
import io.github.murtazanasir.mailr.ui.agenda.WeatherIcon
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.time.ZoneId
import java.time.ZonedDateTime
import java.util.Locale

/**
 * The Agenda widget's four styles (glass timeline, paper & rail, week strip, numeral duotone) at
 * the mockups' sizes, 4×3 (373×315 dp), 4×2 (373×210) and 3×2 (280×210), light and dark, over a
 * made-up wallpaper so the translucency shows; an empty evening, a signed-out widget, Undo with
 * "Set weather", and the settings with the style picker. Demo data only (the mockups' sample day).
 *
 * The shots are split into several classes (Agenda*ShotsTest), so the test JVMs share them out;
 * this class holds the demo data and the helpers.
 */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
abstract class AgendaShots(protected val dark: Boolean) {
    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun params(): List<Array<Any>> = listOf(arrayOf<Any>(false), arrayOf<Any>(true))

        val zone: ZoneId = ZoneId.of("America/Chicago")
        /** Wednesday 7 October 2026, 14:05, as in the mockups. */
        val now: ZonedDateTime = ZonedDateTime.of(2026, 10, 7, 14, 5, 0, 0, zone)

        internal fun at(d: Long, h: Int, m: Int = 0) = now.plusDays(d).withHour(h).withMinute(m).toOffsetDateTime().toString()
        internal fun date(d: Long) = now.toLocalDate().plusDays(d).toString()
        internal const val BLUE = "#1a73e8"
        internal const val PEACOCK = "#039be5"
        internal const val TANGERINE = "#f4511e"
        internal const val GRAPE = "#8e24aa"
        internal const val SAGE = "#33b679"
        internal const val BANANA = "#f6bf26"
        internal const val LAVENDER = "#7986cb"

        internal fun ev(uid: String, title: String, start: String, end: String, colour: String, place: String? = null, allDay: Boolean = false) =
            CalendarEvent(uid, title, start, end, allDay = allDay, location = place, calendar = colour, calendarColor = colour)

        val events = listOf(
            ev("m1", "Budget sync", at(-2, 9), at(-2, 10), BLUE),
            ev("m2", "Seminar prep", at(-2, 13), at(-2, 14), PEACOCK),
            ev("t1", "Lab hours", at(-1, 10), at(-1, 11), SAGE),
            ev("a1", "Design review", at(0, 8, 30), at(0, 9, 15), BLUE, "Studio B"),
            ev("a2", "Statistics lecture", at(0, 11), at(0, 12, 15), PEACOCK, "Room 319"),
            ev("a3", "Call with Priya Shah", at(0, 14, 30), at(0, 15), TANGERINE, "Video call"),
            ev("a4", "Evening seminar", at(0, 17, 35), at(0, 18, 50), GRAPE, "Hall 2"),
            ev("b1", "Office hours", at(1, 10), at(1, 11), SAGE),
            ev("b2", "Team meeting", at(1, 13), at(1, 14), BLUE),
            ev("c1", "Conference travel", date(2), date(3), BANANA, allDay = true),
            ev("c2", "Flight to Denver", at(2, 9, 10), at(2, 11), LAVENDER),
            ev("s1", "Choir rehearsal", at(4, 16), at(4, 17), GRAPE),
        )
        val tasks = listOf(
            TaskItem("k1", "Send grades to registrar", date(-2), dueAllDay = true),
            TaskItem("k2", "Change the date of the quiz", date(1), dueAllDay = true),
            TaskItem("k3", "Book a dentist appointment", date(2), dueAllDay = true),
            TaskItem("k4", "Record the class", date(3), dueAllDay = true),
            TaskItem("k5", "Review Alex's draft", start = date(0), startAllDay = true),
        )
        internal fun hour(h: Long) = now.withMinute(0).plusHours(h).toInstant().toEpochMilli()
        val weather = AgendaModel.Weather(WeatherIcon.PARTLY_DAY, 72, 78, 54, null, listOf(
            AgendaModel.Hour(hour(1), WeatherIcon.PARTLY_DAY, 75),
            AgendaModel.Hour(hour(4), WeatherIcon.CLEAR_DAY, 71),
            AgendaModel.Hour(hour(7), WeatherIcon.CLEAR_NIGHT, 63),
            AgendaModel.Hour(hour(10), WeatherIcon.CLEAR_NIGHT, 58),
        ))

        fun model(at: ZonedDateTime = now, w: AgendaModel.Weather? = weather) = AgendaModel.build(events, tasks, at, 5, h24 = true, weather = w)

        /** The mockups' sizes in dp. */
        val SIZES = listOf("4x3" to (373 to 315), "4x2" to (373 to 210), "3x2" to (280 to 210))

        /** A usual day: one event left today and three tasks. */
        fun fewModel(): AgendaModel = AgendaModel.build(
            listOf(ev("f1", "Team meeting", at(0, 16), at(0, 17), PEACOCK, "Conference room")),
            listOf(TaskItem("q1", "Change the date of the quiz", at(0, 20)), TaskItem("q2", "Record the class", date(3), dueAllDay = true),
                TaskItem("q3", "Pause five seconds in the talk", date(4), dueAllDay = true)),
            now, 5, h24 = true, weather = weather)

        /** A full week: more events and tasks than any size shows, so every list scrolls. */
        fun manyModel(): AgendaModel = AgendaModel.build(events + listOf(
            ev("x1", "Coffee with Jordan", at(0, 15, 30), at(0, 16), SAGE, "Corner café"),
            ev("x2", "Grant meeting", at(0, 16, 15), at(0, 17), BLUE, "Room 210"),
            ev("x3", "Advising", at(1, 15), at(1, 16), PEACOCK),
            ev("x4", "Faculty senate", at(3, 14), at(3, 16), LAVENDER),
        ), tasks + listOf(
            TaskItem("k6", "Order lab supplies", date(1), dueAllDay = true),
            TaskItem("k7", "Reply to the dean's office", date(0), dueAllDay = true),
            TaskItem("k8", "Update the syllabus", date(4), dueAllDay = true),
        ), now, 5, h24 = true, weather = weather)
    }

    @get:Rule val compose = createComposeRule()
    protected val mode get() = if (dark) "dark" else "light"
    private var locale: Locale = Locale.getDefault()

    // The mockups' week starts on Monday: the shots use British English (the widget follows the phone).
    @Before fun ukWeek() { locale = Locale.getDefault(); Locale.setDefault(Locale.UK) }
    @After fun restore() { Locale.setDefault(locale) }

    /** The Glance content on a made-up wallpaper, as the launcher would draw it. */
    protected fun widget(name: String, wDp: Int, hDp: Int, wallpaper: Boolean = true, lang: String = "en-rGB",
                       content: @Composable () -> Unit) {
        RuntimeEnvironment.setQualifiers("$lang-w411dp-h891dp-port-${if (dark) "night" else "notnight"}-xxhdpi")
        val ctx = ApplicationProvider.getApplicationContext<Application>()
        freshDrawables(ctx)
        homeScreen(ctx, dark, wDp + 32, hDp + 32, listOf(PlacedWidget(16, 16, wDp, hDp, content)), wallpaper)
            .captureRoboImage("screenshots/agenda-$mode-$name.png")
    }

    protected fun style(st: AgendaStyle) {
        val m = model()
        SIZES.forEach { (size, wh) -> widget("${st.key}-$size", wh.first, wh.second) { AgendaWidgetContent(m, st) } }
    }

}
