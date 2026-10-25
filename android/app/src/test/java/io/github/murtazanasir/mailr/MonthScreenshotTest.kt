// ACT-SURFACES: android.calendar
// The phone month: event titles in the days, the picked day's dense agenda below.
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.ui.calendar.DayEvent
import io.github.murtazanasir.mailr.ui.calendar.DayList
import io.github.murtazanasir.mailr.ui.calendar.MonthChips
import io.github.murtazanasir.mailr.ui.calendar.MonthView
import io.github.murtazanasir.mailr.ui.calendar.dayEvents
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.time.LocalDate
import java.time.ZoneId

/** A busy month around the pinned clock (Fri 2 Oct 2026): demo names only. */
object BusyMonth {
    private val zone: ZoneId = ZoneId.systemDefault()
    val today: LocalDate get() = AppClock.today()
    /** The picked day: the Monday after today. */
    val picked: LocalDate get() = today.plusDays(3)

    private fun t(day: LocalDate, h: Int, m: Int = 0) = day.atTime(h, m).atZone(zone).toOffsetDateTime().toString()
    private fun ev(uid: String, title: String, day: LocalDate, h: Int, m: Int, mins: Long, colour: String, location: String? = null) =
        CalendarEvent(uid, title, t(day, h, m), day.atTime(h, m).plusMinutes(mins).atZone(zone).toOffsetDateTime().toString(),
            color = colour, location = location)

    val events: List<CalendarEvent> get() {
        val first = today.withDayOfMonth(1)
        val blue = "#1a73e8"; val green = "#0b8043"; val purple = "#8e24aa"; val orange = "#e37400"; val teal = "#00838f"
        val out = mutableListOf<CalendarEvent>()
        // Teaching on Mondays and Wednesdays, a team stand-up on Tuesdays and Thursdays.
        var d = first.minusDays(6)
        var n = 0
        while (d.isBefore(first.plusDays(40))) {
            when (d.dayOfWeek.value) {
                1 -> { out += ev("m${n++}", "Statistics lecture", d, 9, 0, 75, teal, "Hollis Hall 208"); out += ev("m${n++}", "Office hours", d, 14, 0, 60, purple) }
                3 -> out += ev("m${n++}", "Statistics lecture", d, 9, 0, 75, teal, "Hollis Hall 208")
                2, 4 -> out += ev("m${n++}", "Team stand-up", d, 8, 30, 15, blue, "Video call")
            }
            d = d.plusDays(1)
        }
        out += ev("x1", "Proposal discussion", picked, 15, 30, 60, blue, "Video call")
        out += ev("x2", "Design review with Dana", picked, 11, 0, 45, blue, "Room 4")
        out += ev("x3", "Dinner with Jordan", picked, 19, 0, 90, green, "Lumen Bistro")
        out += ev("x4", "Budget review", today, 13, 0, 60, blue)
        out += ev("x5", "Piano lesson", today.plusDays(1), 10, 0, 45, green)
        out += ev("x6", "Quarterly planning", today.plusDays(13), 9, 30, 120, blue)
        out += ev("x7", "Farmers market", today.plusDays(8), 9, 0, 120, green)
        out += CalendarEvent("x8", "Data Week conference", today.plusDays(19).toString(), today.plusDays(22).toString(), allDay = true, color = orange)
        out += CalendarEvent("x9", "Studio offsite", today.plusDays(6).toString(), today.plusDays(7).toString(), allDay = true, color = orange)
        out += ev("x10", "Dentist", today.plusDays(10), 16, 0, 30, purple)
        return out
    }

    val dayEvents: List<DayEvent> get() = dayEvents(events, emptyList())

    /** The phone's Month view as the Calendar screen lays it out. */
    @Composable
    fun PhoneMonth() {
        val evs = dayEvents
        Column(Modifier.fillMaxSize()) {
            MonthView(picked, evs, false, {}, {}, Modifier.weight(0.64f))
            HorizontalDivider()
            DayList(picked, evs, null, {}, {}, Modifier.weight(0.36f).fillMaxWidth(), compact = true)
        }
    }
}

/** The phone month, light and dark (RtlScreenshotTest has it right to left). */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class MonthScreenshotTest(private val dark: Boolean) {
    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun params(): List<Array<Any>> = listOf(arrayOf<Any>(false), arrayOf<Any>(true))
    }

    @get:Rule val compose = createComposeRule()
    private val mode get() = if (dark) "dark" else "light"

    /** The titles show in the days, the picked day's agenda below. */
    @Test fun busyMonth() {
        RuntimeEnvironment.setQualifiers("w411dp-h891dp-port-${if (dark) "night" else "notnight"}-xxhdpi")
        compose.setContent {
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.surface) { BusyMonth.PhoneMonth() }
            }
        }
        compose.onRoot().captureRoboImage("screenshots/month-$mode-busy.png")
        compose.onNodeWithText("Dinner with Jordan").assertExists()
    }

    @Test fun chipsFitTheCell() {
        assertEquals(0, MonthChips.slots(20.dp))
        assertEquals(3, MonthChips.slots(70.dp))
        assertEquals(2, MonthChips.shown(2, 3))
        assertEquals(3, MonthChips.shown(3, 3))
        assertEquals(2, MonthChips.shown(5, 3))
        assertEquals(0, MonthChips.shown(4, 1))
    }
}
