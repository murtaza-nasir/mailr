package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.onRoot
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.data.weather.WeatherPlace
import io.github.murtazanasir.mailr.data.weather.WeatherSource
import io.github.murtazanasir.mailr.data.weather.WeatherUnits
import io.github.murtazanasir.mailr.ui.agenda.AgendaModel
import io.github.murtazanasir.mailr.ui.agenda.AgendaSettingsCallbacks
import io.github.murtazanasir.mailr.ui.agenda.AgendaSettingsScreen
import io.github.murtazanasir.mailr.ui.agenda.AgendaSettingsUi
import io.github.murtazanasir.mailr.ui.agenda.AgendaStyle
import io.github.murtazanasir.mailr.ui.agenda.AgendaWidgetContent
import io.github.murtazanasir.mailr.ui.agenda.WeatherApps
import io.github.murtazanasir.mailr.ui.agenda.WeatherIcon
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import io.github.murtazanasir.mailr.ui.today.TodayModel
import com.github.takahirom.roborazzi.captureRoboImage
import java.time.ZonedDateTime
import java.util.Locale
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/** Agenda widget shots ([AgendaShots]): Two weeks listed (also right to left), an empty evening, signed out with Undo, and the settings. Light and dark. */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class AgendaDaysShotsTest(dark: Boolean) : AgendaShots(dark) {
    /**
     * A Saturday evening with two weeks listed: the next event is on
     * Monday with a long title (two lines on the card), and the same Monday events come again a week
     * later, which carry their date ("Mon 19 Oct"). Also right to left (ar-XB).
     */
    @Test fun numeralTwoWeeks() {
        val sat = ZonedDateTime.of(2026, 10, 10, 20, 30, 0, 0, zone)
        fun on(d: Long, h: Int, m: Int = 0) = sat.plusDays(d).withHour(h).withMinute(m).toOffsetDateTime().toString()
        val evs = listOf(
            ev("w1", "Introduction to Statistics", on(2, 9), on(2, 10), BLUE, "Online (Asynchronous)"),
            ev("w2", "CHEM 101 \u2014 Exam (lab)", on(2, 17, 35), on(2, 19), GRAPE, "Room 210"),
            ev("w3", "Linear Algebra", on(4, 19, 5), on(4, 20), PEACOCK),
            ev("w4", "Office hours", on(5, 10), on(5, 11), SAGE),
            ev("w5", "Introduction to Statistics", on(9, 9), on(9, 10), BLUE, "Online (Asynchronous)"),
            ev("w6", "CHEM 101 \u2014 Class", on(9, 17, 35), on(9, 19), GRAPE, "Room 210"),
        )
        val hourly = weather.hours.mapIndexed { i, h -> h.copy(at = sat.withMinute(0).plusHours(1L + 3 * i).toInstant().toEpochMilli()) }
        val m = AgendaModel.build(evs, tasks.take(3), sat, 14, h24 = true, weather = weather.copy(hours = hourly))
        widget("numeral-2weeks-4x3", 373, 315) { AgendaWidgetContent(m, AgendaStyle.NUMERAL) }
        widget("numeral-2weeks-3x2", 280, 210) { AgendaWidgetContent(m, AgendaStyle.NUMERAL) }
        // Glance lays the widget out by the default locale's direction (layoutDirection "locale"), as
        // the phone's language sets it; the model is built again in that language.
        Locale.setDefault(Locale.forLanguageTag("ar-XB"))
        val rtl = AgendaModel.build(evs, tasks.take(3), sat, 14, h24 = true, weather = weather.copy(hours = hourly))
        widget("numeral-2weeks-rtl-4x3", 373, 315, lang = "ar-rXB-ldrtl") { AgendaWidgetContent(rtl, AgendaStyle.NUMERAL) }
    }

    /** An evening with nothing left and no tasks: each style stays calm. */
    @Test fun empty() {
        val m = AgendaModel.build(emptyList(), emptyList(), now.withHour(20), 5, h24 = true, weather = weather.copy(icon = WeatherIcon.CLEAR_NIGHT, temp = 58))
        AgendaStyle.entries.forEach { st -> widget("empty-${st.key}", 373, 210, wallpaper = false) { AgendaWidgetContent(m, st) } }
    }

    /** Signed out, and a task just completed while weather has no place yet ("Set weather"). */
    @Test fun signedOutAndUndo() {
        widget("signed-out", 373, 210, wallpaper = false) { AgendaWidgetContent(AgendaModel.signedOut(now, weather), AgendaStyle.WEEK) }
        val m = AgendaModel.build(events, tasks.drop(1), now, 5, h24 = true, undo = TodayModel.Undo("k1", "Send grades to registrar"), weatherHint = true)
        widget("undo-hint", 373, 315) { AgendaWidgetContent(m, AgendaStyle.GLASS) }
    }

    @Test fun settings() {
        RuntimeEnvironment.setQualifiers("w411dp-h891dp-port-${if (dark) "night" else "notnight"}-xxhdpi")
        compose.setContent {
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.surface) {
                    AgendaSettingsScreen(AgendaSettingsUi(5, true, WeatherSource.CITY, false, WeatherPlace(37.7, -97.3, "Springfield"),
                        WeatherUnits.AUTO, "Weather", listOf(WeatherApps.App("com.example.weather/.Main", "Weather")), emptyList(),
                        style = AgendaStyle.WEEK),
                        AgendaSettingsCallbacks({}, {}, {}, {}, {}, {}, { emptyList() }, {}))
                }
            }
        }
        compose.onRoot().captureRoboImage("screenshots/agenda-$mode-settings.png")
    }
}
