// ACT-SURFACES: android.widget
package io.github.murtazanasir.mailr

import android.app.Application
import android.graphics.Paint
import android.util.TypedValue
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.ui.agenda.AgendaColumns
import io.github.murtazanasir.mailr.ui.agenda.AgendaModel
import io.github.murtazanasir.mailr.ui.agenda.AgendaSettingsCallbacks
import io.github.murtazanasir.mailr.ui.agenda.AgendaSettingsScreen
import io.github.murtazanasir.mailr.ui.agenda.AgendaSettingsUi
import io.github.murtazanasir.mailr.ui.agenda.AgendaStyles
import io.github.murtazanasir.mailr.ui.agenda.NumeralList
import io.github.murtazanasir.mailr.ui.today.TodayModel
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.time.ZoneOffset
import java.util.Locale

/**
 * "Line up days and times" on the Agenda widget (calendar.today_view
 * @android.widget): the day and time columns are as wide as the widest day and time on the phone's
 * clock, today's rows keep the day column empty, a narrow widget drops the day column for day labels,
 * and the setting is kept per widget, on by default.
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34], qualifiers = "en-rGB-w411dp-h891dp-xxhdpi")
class AgendaColumnsTest {
    @get:Rule val compose = createComposeRule()
    private val app: Application = ApplicationProvider.getApplicationContext()
    private var locale: Locale = Locale.getDefault()

    @Before fun uk() { locale = Locale.getDefault(); Locale.setDefault(Locale.UK) }
    @After fun restore() {
        Locale.setDefault(locale)
        app.getSharedPreferences("mailr_agenda_widget", 0).edit().clear().commit()
    }

    private fun measure(text: String, sp: Float): Float {
        val dm = app.resources.displayMetrics
        val p = Paint().apply { textSize = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP, sp, dm) }
        return p.measureText(text) / dm.density
    }

    @Test fun theColumnsAreAsWideAsTheWidestDayAndTime() {
        val day = AgendaColumns.dayWidth(app, 10.5f)
        listOf("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun").forEach { assertTrue(it, measure(it, 10.5f) <= day) }
        val h24 = AgendaColumns.timeWidth(app, 10.5f, h24 = true)
        val h12 = AgendaColumns.timeWidth(app, 10.5f, h24 = false)
        // Every time of the day fits its column, so titles never move.
        for (m in 0 until 24 * 60 step 7) {
            assertTrue(measure(TodayModel.time(m * 60_000L, ZoneOffset.UTC, true), 10.5f) <= h24)
            assertTrue(measure(TodayModel.time(m * 60_000L, ZoneOffset.UTC, false), 10.5f) <= h12)
        }
        assertTrue("12 h is wider ($h12 vs $h24)", h12 > h24)
        // Not much wider than needed: a dp of slack at most, rounded up.
        assertTrue(day <= measure("Wed", 10.5f) + 3f && day >= measure("Wed", 10.5f))
        // Words that share the time column ("all day") widen it when they are wider.
        assertTrue(AgendaColumns.timeWidth(app, 10.5f, h24 = true, extra = listOf("all day")) >= measure("all day", 10.5f))
    }

    private fun up(day: String?, title: String) =
        AgendaModel.Upcoming(AgendaModel.Event(title, "", title, "9:00", null, null, false, false), day)

    /** Numeral duotone: a day's name heads its first event (no day column); rows on the card's own day get none. */
    @Test fun theNumeralListHeadsEachDay() {
        val list = listOf(up(null, "Seminar"), up("Tomorrow", "Office hours"), up("Tomorrow", "Team meeting"), up("Fri", "Flight"))
        val labelled = NumeralList.rows(list, after = null)
        assertEquals(listOf("ev", "Tomorrow", "ev", "ev", "Fri", "ev"), labelled.map { (it as? NumeralList.Row.Day)?.day ?: "ev" })
        // The card is tomorrow's first event: tomorrow's other rows follow it without a heading.
        val fromTomorrow = NumeralList.rows(list.drop(1), after = "Tomorrow")
        assertEquals(listOf("ev", "ev", "Fri", "ev"), fromTomorrow.map { (it as? NumeralList.Row.Day)?.day ?: "ev" })
    }

    @Test fun theHourlyStripShowsAsManyHoursAsFit() {
        val slots = listOf("3 pm", "6 pm", "9 pm", "12 am").map { AgendaModel.HourSlot(it, io.github.murtazanasir.mailr.ui.agenda.WeatherIcon.CLEAR_DAY, 70) }
        val measure = { t: List<String> -> AgendaColumns.widest(app, t, 9.5f) }
        // The narrow widget's end half (about 87 dp): three; the wide one (about 135 dp): all four.
        assertEquals(3, io.github.murtazanasir.mailr.ui.agenda.hourlyFit(slots, 87f, measure).size)
        assertEquals(4, io.github.murtazanasir.mailr.ui.agenda.hourlyFit(slots, 135f, measure).size)
        val h24 = listOf("15", "18", "21", "00").map { AgendaModel.HourSlot(it, io.github.murtazanasir.mailr.ui.agenda.WeatherIcon.CLEAR_DAY, 70) }
        assertEquals(4, io.github.murtazanasir.mailr.ui.agenda.hourlyFit(h24, 87f, measure).size)
    }

    @Test fun theSettingIsKeptPerWidgetAndOnByDefault() {
        val st = AgendaStyles(app)
        assertTrue(st.columns(21))
        st.setColumns(21, false)
        assertFalse(AgendaStyles(app).columns(21))
        assertTrue(AgendaStyles(app).columns(22))
        st.remove(21)
        assertTrue(st.columns(21))
    }

    @Test fun theSettingsSwitchTurnsItOff() {
        val changed = mutableListOf<Boolean>()
        compose.setContent {
            Surface(Modifier.fillMaxSize()) {
                AgendaSettingsScreen(AgendaSettingsUi(weather = false),
                    AgendaSettingsCallbacks({}, {}, {}, {}, {}, {}, { emptyList() }, {}, onColumns = { changed += it }))
            }
        }
        compose.onNodeWithText("Line up days and times").performClick()
        assertEquals(listOf(false), changed)
    }
}
