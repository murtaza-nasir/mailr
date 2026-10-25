// ACT-SURFACES: android.widget
package io.github.murtazanasir.mailr

import android.app.Application
import android.content.Intent
import androidx.compose.ui.unit.DpSize
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.db.CalEventEntity
import io.github.murtazanasir.mailr.data.db.CalTaskEntity
import io.github.murtazanasir.mailr.data.weather.OpenMeteo
import io.github.murtazanasir.mailr.data.weather.WeatherPlace
import io.github.murtazanasir.mailr.data.weather.WeatherReading
import io.github.murtazanasir.mailr.data.weather.WeatherSource
import io.github.murtazanasir.mailr.data.weather.WeatherStore
import io.github.murtazanasir.mailr.data.weather.WeatherUnits
import io.github.murtazanasir.mailr.data.weather.WeatherWork
import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.ui.agenda.AgendaModel
import io.github.murtazanasir.mailr.ui.agenda.AgendaSettingsActivity
import io.github.murtazanasir.mailr.ui.agenda.AgendaSize
import io.github.murtazanasir.mailr.ui.agenda.AgendaStyle
import io.github.murtazanasir.mailr.ui.agenda.AgendaStyles
import io.github.murtazanasir.mailr.ui.agenda.AgendaTicks
import io.github.murtazanasir.mailr.ui.agenda.AgendaTimeline
import io.github.murtazanasir.mailr.ui.agenda.GlassDims
import io.github.murtazanasir.mailr.ui.agenda.PaperRail
import io.github.murtazanasir.mailr.ui.agenda.WeekList
import io.github.murtazanasir.mailr.ui.agenda.WeatherPermissionFlow
import io.github.murtazanasir.mailr.ui.agenda.duration
import io.github.murtazanasir.mailr.ui.agenda.NumeralList
import io.github.murtazanasir.mailr.ui.agenda.nextLabel
import io.github.murtazanasir.mailr.ui.agenda.AgendaWidgets
import io.github.murtazanasir.mailr.ui.agenda.WeatherApps
import io.github.murtazanasir.mailr.ui.agenda.WeatherIcon
import io.github.murtazanasir.mailr.ui.ask.AskActivity
import io.github.murtazanasir.mailr.ui.ask.AskIntents
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.time.ZoneId
import java.time.ZonedDateTime
import java.util.Locale

/**
 * The Agenda widget (android.widget): what it lists from the phone's copy
 * (calendar.today_view), what fits at each size (the top block one size, the lists scrolling), the tick (task.complete, shared with Today), the
 * large zone into Ambrose listening (agent.dictate), and the weather: Open-Meteo straight from the
 * phone, at most hourly, never through the mailr server.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class AgendaWidgetTest {
    private val h = CalendarHarness()
    private val zone: ZoneId = ZoneId.of("America/Chicago")
    /** Wednesday 7 October 2026, noon. */
    private val noon: ZonedDateTime = ZonedDateTime.of(2026, 10, 7, 12, 0, 0, 0, zone)

    @Before fun setUp() { h.start() }
    @After fun tearDown() {
        h.app.getSharedPreferences("mailr_agenda_widget", 0).edit().clear().commit()
        h.stop()
    }

    private fun at(hr: Int, m: Int = 0, days: Long = 0) = noon.plusDays(days).withHour(hr).withMinute(m).toOffsetDateTime().toString()
    private fun date(days: Long) = noon.toLocalDate().plusDays(days).toString()
    private fun ev(uid: String, title: String, start: String, end: String?, allDay: Boolean = false, status: String? = null) =
        CalendarEvent(uid, title, start, end, allDay = allDay, status = status)
    private fun task(uid: String, title: String, due: String?, status: String = "needs-action", parent: String? = null) =
        TaskItem(uid, title, due, dueAllDay = due?.length == 10, status = status, parent = parent)

    private val events = listOf(
        ev("e1", "Stand-up", at(9), at(9, 15)),                                  // over
        ev("e2", "Lunch with Dana", at(11, 30), at(12, 30)),                     // now
        ev("e3", "Budget review", at(15), at(16)),
        ev("e4", "Data Week", date(1), date(3), allDay = true),                  // Thu and Fri
        ev("e5", "1:1 with Sam", at(10, days = 1), at(10, 30, days = 1)),
        ev("e6", "Cancelled sync", at(14, days = 1), at(15, days = 1), status = "CANCELLED"),
        ev("e7", "Next week", at(10, days = 8), at(11, days = 8)),
    )
    private val tasks = listOf(
        task("t1", "Renew the studio insurance", "2026-10-02"),
        task("t2", "Send the Q3 numbers", at(17)),
        task("t3", "Draft the board update", date(1)),
        task("t4", "Water the plants", date(3)),
        task("t5", "Far away", date(20)),
        task("t6", "Done already", date(0), status = "completed"),
        task("t7", "A subtask", date(0), parent = "t2"),
    )

    /** calendar.today_view on the home screen: the next days grouped, ended events left out, tasks by day. */
    @Test fun theAgendaGroupsTheNextDays() {
        val m = AgendaModel.build(events, tasks, noon, days = 5)
        assertEquals(listOf("Today", "Tomorrow", "Fri 9 Oct", "Sat 10 Oct", "Sun 11 Oct"), m.days.map { it.label })
        assertEquals(listOf("Lunch with Dana", "Budget review"), m.days[0].events.map { it.title })
        assertTrue(m.days[0].events[0].now)
        assertEquals("3 PM", m.days[0].events[1].time)
        assertEquals(listOf("Data Week", "1:1 with Sam"), m.days[1].events.map { it.title })
        assertEquals("All day", m.days[1].events[0].time)
        assertTrue(m.days[1].events[0].allDay)
        assertEquals(listOf("Data Week"), m.days[2].events.map { it.title })
        assertTrue(m.days[4].events.isEmpty())
        assertEquals("7", m.dayNumber)
        assertEquals("Wednesday", m.weekday)
        assertEquals("October", m.month)

        assertEquals(listOf("Renew the studio insurance", "Send the Q3 numbers", "Draft the board update", "Water the plants"), m.tasks.map { it.title })
        assertTrue(m.tasks[0].overdue)
        assertEquals("Fri 2 Oct", m.tasks[0].note)
        assertNull(m.tasks[0].day)
        assertEquals("5 PM", m.tasks[1].note)
        assertEquals("Tomorrow", m.tasks[2].note)
        assertEquals("Sat", m.tasks[3].note)
        // Single column: overdue tasks sit under Today, the others under their day.
        assertEquals(listOf("Renew the studio insurance", "Send the Q3 numbers"), m.tasksOn(m.days[0]).map { it.title })
        assertEquals(listOf("Water the plants"), m.tasksOn(m.days[3]).map { it.title })
        assertEquals("17:00", AgendaModel.build(events, tasks, noon, h24 = true).tasks[1].note)
    }

    @Test fun anEmptyWeekAndSignedOutAreCalm() {
        val m = AgendaModel.build(emptyList(), emptyList(), noon)
        assertTrue(m.empty)
        assertFalse(AgendaModel.signedOut(noon).ready)
    }

    /** The widget reads the phone's copy: rows in Room show without the network. */
    @Test fun theWidgetReadsThePhonesCopy() = runBlocking {
        val now = AppClock.now(zone).withHour(12).withMinute(0)
        val e = ev("w1", "Design review", now.plusDays(1).withHour(15).toOffsetDateTime().toString(), now.plusDays(1).withHour(16).toOffsetDateTime().toString())
        h.g.db.calendar().putEvents(listOf(CalEventEntity(e.key, e.uid, now.plusDays(1).withHour(15).toInstant().toEpochMilli(),
            now.plusDays(1).withHour(16).toInstant().toEpochMilli(), false, null, MailrJson.encodeToString(CalendarEvent.serializer(), e))))
        val t = task("w2", "Call the plumber", now.plusDays(2).toLocalDate().toString())
        h.g.db.calendar().putTasks(listOf(CalTaskEntity(t.uid, null, null, false, now.plusDays(2).toInstant().toEpochMilli(),
            MailrJson.encodeToString(TaskItem.serializer(), t))))
        val m = AgendaWidgets.model(h.g, now, h24 = false, days = 5)
        assertTrue(m.ready)
        assertTrue(m.days[1].events.any { it.title == "Design review" })
        assertTrue(m.tasks.any { it.title == "Call the plumber" })
        assertFalse(AgendaWidgets.model(null, now, false).ready)
    }

    /** agent.dictate @android.widget: the date header (a hidden target, no microphone) opens the capture listening at once. */
    @Test fun theTalkZoneOpensAmbroseListening() {
        val i = AgendaWidgets.listen(h.app)
        assertEquals(AskActivity::class.java.name, i.component?.className)
        assertEquals(AskIntents.ACTION_DICTATE, i.action)
        assertEquals(true, AskIntents.listen(i))
        assertEquals("agenda_widget", i.getStringExtra(AskIntents.EXTRA_FROM))
    }

    /**
     * task.complete @android.widget: the Agenda's circle is the Today widget's tick, through the replay
     * queue; task.reopen @android.widget: the Undo chip in the header opens the task again.
     */
    @Test fun theCircleCompletesTheTaskAndUndoReopensIt() {
        runBlocking { io.github.murtazanasir.mailr.ui.today.TodayWidgets.complete(h.g, "t1") }
        assertEquals("true", h.queuedBody("PATCH", "tasks/t1")["completed"].toString())
        assertEquals("t1", io.github.murtazanasir.mailr.ui.today.TodayUndo.get()?.uid)
        runBlocking { io.github.murtazanasir.mailr.ui.today.TodayWidgets.undo(h.g) }
        h.until("reopened") { h.queued().lastOrNull { it.method == "PATCH" && it.path == "tasks/t1" }?.body?.contains("\"completed\":false") == true }
        assertEquals("false", h.queuedBody("PATCH", "tasks/t1")["completed"].toString())
        assertNull(io.github.murtazanasir.mailr.ui.today.TodayUndo.get())
    }

    @Test fun weatherCodesBecomePictures() {
        assertEquals(WeatherIcon.CLEAR_DAY, WeatherIcon.of(0, true))
        assertEquals(WeatherIcon.CLEAR_NIGHT, WeatherIcon.of(1, false))
        assertEquals(WeatherIcon.PARTLY_DAY, WeatherIcon.of(2, true))
        assertEquals(WeatherIcon.CLOUDY, WeatherIcon.of(3, false))
        assertEquals(WeatherIcon.FOG, WeatherIcon.of(45, true))
        assertEquals(WeatherIcon.RAIN, WeatherIcon.of(61, true))
        assertEquals(WeatherIcon.RAIN, WeatherIcon.of(81, true))
        assertEquals(WeatherIcon.SNOW, WeatherIcon.of(73, true))
        assertEquals(WeatherIcon.STORM, WeatherIcon.of(95, true))
    }

    /** Units follow the region unless chosen; the place is rounded to about 10 km before it is kept or sent. */
    @Test fun unitsAndRounding() {
        assertTrue(WeatherUnits.AUTO.fahrenheit(Locale.US))
        assertFalse(WeatherUnits.AUTO.fahrenheit(Locale.UK))
        assertFalse(WeatherUnits.CELSIUS.fahrenheit(Locale.US))
        assertEquals(WeatherPlace(37.7, -97.3, null), WeatherPlace.rounded(37.6872, -97.3301, null))
        val r = WeatherReading(18.3, 30.6, 16.7, 2, true, 0, WeatherPlace(37.7, -97.3, "Springfield"))
        val f = WeatherStore.show(r, fahrenheit = true, place = "Springfield")
        assertEquals(65, f.temp)
        assertEquals(87, f.high)
        assertEquals(62, f.low)
        assertEquals("65°", f.tempLine)
        assertEquals("87° / 62°", f.rangeLine)
        assertEquals(18, WeatherStore.show(r, fahrenheit = false, place = null).temp)
    }

    /** The widget shows only a reading for the place it is set to, and none once it is six hours old or weather is off. */
    @Test fun theStoreShowsOnlyACurrentReading() {
        val s = WeatherStore(h.app)
        val now = AppClock.millis()
        assertNull(s.shown(now))
        s.source = WeatherSource.CITY
        s.city = WeatherPlace(37.7, -97.3, "Springfield")
        s.units = WeatherUnits.CELSIUS
        s.reading = WeatherReading(18.0, 25.0, 12.0, 0, true, now - 60_000, WeatherPlace(37.7, -97.3, "Springfield"))
        assertEquals(18, s.shown(now)?.temp)
        assertEquals("Springfield", s.shown(now)?.place)
        assertNull(s.shown(now + WeatherStore.STALE_MS))
        s.city = WeatherPlace(51.5, -0.1, "London")
        assertNull(s.shown(now))
        s.city = WeatherPlace(37.7, -97.3, "Springfield")
        s.enabled = false
        assertNull(s.shown(now))
    }

    /** Open-Meteo is asked straight from the phone: rounded coordinates, no mailr token or cookie, and at most hourly. */
    @Test fun theForecastComesFromOpenMeteoAtMostHourly() = runBlocking {
        val server = MockWebServer()
        server.start()
        try {
            repeat(2) {
                server.enqueue(MockResponse().setBody("""{"current":{"temperature_2m":18.3,"weather_code":61,"is_day":0},
                    "daily":{"temperature_2m_max":[30.6],"temperature_2m_min":[16.7]}}"""))
            }
            val api = OpenMeteo(forecastBase = server.url("/v1/forecast"), searchBase = server.url("/v1/search"))
            val s = WeatherStore(h.app)
            s.source = WeatherSource.CITY
            s.city = WeatherPlace(37.7, -97.3, "Springfield")
            WeatherWork.run(h.app, api)
            val req = server.takeRequest()
            assertEquals("37.7", req.requestUrl?.queryParameter("latitude"))
            assertEquals("-97.3", req.requestUrl?.queryParameter("longitude"))
            assertNull(req.getHeader("Authorization"))
            assertNull(req.getHeader("Cookie"))
            assertEquals(WeatherIcon.RAIN, WeatherIcon.of(s.reading!!.code, s.reading!!.day))
            assertFalse(s.reading!!.day)
            // A second run within the hour keeps the reading and asks nothing.
            WeatherWork.run(h.app, api)
            assertEquals(1, server.requestCount)
            // A new city is fetched at once.
            s.city = WeatherPlace(51.5, -0.1, "London")
            WeatherWork.run(h.app, api)
            assertEquals(2, server.requestCount)
            // Nothing went to the mailr server.
            assertTrue(h.requests.none { it.first.path.orEmpty().contains("weather") || it.first.path.orEmpty().contains("forecast") })
        } finally {
            server.shutdown()
        }
    }

    @Test fun citySearchResultsAreParsed() {
        val o = Json.parseToJsonElement("""{"results":[{"name":"Springfield","latitude":37.69224,"longitude":-97.33754,
            "admin1":"Illinois","country":"United States"},{"name":"Springfield Falls","latitude":33.91,"longitude":-98.49,"country":"United States"}]}""").jsonObject
        val r = OpenMeteo.parseSearch(o)
        assertEquals(listOf("Springfield", "Springfield Falls"), r.map { it.name })
        assertEquals("Illinois, United States", r[0].region)
        assertTrue(OpenMeteo.parseSearch(Json.parseToJsonElement("{}").jsonObject).isEmpty())
    }

    /** With no app chosen (or the chosen one gone), Android chooses among weather apps, else a weather search. */
    @Test fun theWeatherTapOpensTheChosenAppOrAndroidsChoice() {
        val none = WeatherApps.intent(h.app, null)
        assertTrue(none.action == Intent.ACTION_CHOOSER || none.selector?.hasCategory(WeatherApps.CATEGORY_APP_WEATHER) == true)
        val gone = WeatherApps.intent(h.app, "com.example.gone/.Main")
        assertEquals(none.action, gone.action)
        assertTrue(WeatherApps.looksLikeWeather("Weather", "com.example.app", "weather"))
        assertTrue(WeatherApps.looksLikeWeather("Forecast", "com.example.weatherpro", "weather"))
        assertTrue(WeatherApps.looksLikeWeather("Météo", "fr.example", "météo"))
        assertFalse(WeatherApps.looksLikeWeather("Maps", "com.example.maps", "weather"))
    }

    // ============================ the four styles ============================

    private val two = ZonedDateTime.of(2026, 10, 7, 14, 5, 0, 0, zone)
    private fun at2(d: Long, hr: Int, m: Int = 0) = two.plusDays(d).withHour(hr).withMinute(m).toOffsetDateTime().toString()
    private fun ev2(uid: String, s: String, e: String, colour: String = "#1a73e8") = CalendarEvent(uid, uid, s, e, calendar = colour, calendarColor = colour)

    /** The week strip: the phone's first day of the week, today marked, up to three colours per day, past days too. */
    @Test fun theWeekModelCoversTheWholeWeek() {
        val old = Locale.getDefault()
        try {
            Locale.setDefault(Locale.UK)
            val evs = listOf(ev2("mon1", at2(-2, 9), at2(-2, 10)), ev2("mon2", at2(-2, 11), at2(-2, 12), "#039be5"),
                ev2("sun", at2(4, 9), at2(4, 10), "#8e24aa"),
                ev2("w1", at2(0, 9), at2(0, 10)), ev2("w2", at2(0, 11), at2(0, 12), "#039be5"), ev2("w3", at2(0, 15), at2(0, 16), "#f4511e"),
                ev2("w4", at2(0, 17), at2(0, 18), "#33b679"))
            val m = AgendaModel.build(evs, emptyList(), two, h24 = true)
            assertEquals(listOf("5", "6", "7", "8", "9", "10", "11"), m.week.map { it.number })
            assertEquals(listOf("M", "T", "W", "T", "F", "S", "S"), m.week.map { it.letter })
            assertTrue(m.week[2].today)
            assertTrue(m.week[0].past && m.week[1].past && !m.week[3].past)
            assertEquals(2, m.week[0].dots.size)
            assertEquals(3, m.week[2].dots.size)
            assertEquals("#8e24aa", m.week[6].dots.single().second)
            assertEquals(41, m.weekNumber)
            // The fetch reaches back to the start of the week and on to its end.
            assertEquals(java.time.LocalDate.of(2026, 10, 5), AgendaModel.from(two.toLocalDate()))
            assertEquals(java.time.LocalDate.of(2026, 10, 12), AgendaModel.until(two.toLocalDate(), 3))
            assertEquals("mailr://calendar/day/2026-10-05", AgendaWidgets.dayUri(m.week[0].date))
            // Today's ended events are kept apart (faded), and the day list keeps only what is ahead.
            assertEquals(listOf("w1", "w2"), m.earlier.map { it.title })
            assertEquals(listOf("w3", "w4"), m.days[0].events.map { it.title })
            assertEquals(4, m.eventsToday)
            Locale.setDefault(Locale.US)
            assertEquals("S", AgendaModel.build(evs, emptyList(), two).week.first().letter)
        } finally {
            Locale.setDefault(old)
        }
    }

    /** The glass timeline: the hours shown, blocks where the events are, overlaps side by side, never cut off at the bottom. */
    @Test fun theTimelineLaysOutOverlapsInColumns() {
        val evs = listOf(ev2("a", at2(0, 9), at2(0, 10)), ev2("b", at2(0, 9, 30), at2(0, 11)), ev2("c", at2(0, 10, 30), at2(0, 11, 30)),
            ev2("d", at2(0, 13), at2(0, 14)), ev2("late", at2(0, 20, 50), at2(0, 21)))
        val m = AgendaModel.build(evs, emptyList(), two)
        assertEquals(listOf("a", "b", "c", "d", "late"), m.timeline.map { it.event.title })
        assertEquals(9 * 60, m.timeline[0].startMin)
        assertEquals(14 * 60 + 5, m.nowMinute)
        val lay = AgendaTimeline.layout(m.timeline, m.nowMinute, 260f, 13, 2, 24f)
        assertEquals(9 to 22, lay.startHour to lay.endHour)
        val byTitle = lay.blocks.associateBy { it.timed.event.title }
        // a and b overlap, c starts after a ends: two columns, c takes a's.
        assertEquals(2, byTitle.getValue("a").cols)
        assertEquals(0, byTitle.getValue("a").col)
        assertEquals(1, byTitle.getValue("b").col)
        assertEquals(0, byTitle.getValue("c").col)
        assertEquals(1, byTitle.getValue("d").cols)
        val perHour = 260f / 13
        assertEquals(0f, byTitle.getValue("a").top, 0.01f)
        assertEquals(24f, byTitle.getValue("a").height, 0.01f)
        assertEquals(1.5f * perHour, byTitle.getValue("b").height, 0.01f)
        // A ten-minute event at the end is stretched to the minimum and moved up to stay whole.
        val late = byTitle.getValue("late")
        assertEquals(24f, late.height, 0.01f)
        assertEquals(260f, late.top + late.height, 0.01f)
        // The now-line, and no hour label within 40 minutes of now.
        assertEquals((14 * 60 + 5 - 9 * 60) * perHour / 60, lay.nowY!!, 0.01f)
        assertFalse(lay.hours.first { it.hour == 14 }.label)
        assertTrue(lay.hours.first { it.hour == 12 }.label)
        // 4×2: the hours that fit at full size (six), around now.
        val span = GlassDims.hours(GlassDims.timelineHeight(210f, narrow = false))
        assertEquals(6, span)
        val (s, e) = AgendaTimeline.window(m.timeline, m.nowMinute, span)
        assertEquals(6, e - s)
        assertTrue(m.nowMinute / 60 in s + 1 until e)
        // A short block moved up from the bottom edge sits beside the block it now covers.
        val edge = AgendaModel.build(listOf(ev2("x", at2(0, 15, 30), at2(0, 16)), ev2("y", at2(0, 17, 35), at2(0, 17, 50))), emptyList(), two)
        val tight = AgendaTimeline.layout(edge.timeline, edge.nowMinute, 75f, 5, 2, 24f).blocks.associateBy { it.timed.event.title }
        assertEquals(2, tight.getValue("x").cols)
        assertEquals(1, tight.getValue("y").col)
        // An empty day centres on now.
        assertEquals(13 to 22, AgendaTimeline.window(emptyList(), m.nowMinute, 9))
    }

    /** Numeral duotone: the next event with a countdown within the hour, the time alone further out, "Now" under way. */
    @Test fun theNextEventCountsDown() {
        val evs = listOf(ev2("Call", at2(0, 14, 30), at2(0, 15)), ev2("Seminar", at2(0, 17, 35), at2(0, 18, 50)),
            ev2("Office hours", at2(1, 10), at2(1, 11)))
        val m = AgendaModel.build(evs, emptyList(), two, h24 = true)
        assertEquals("Call", m.next?.event?.title)
        assertEquals(25, m.next?.minutes)
        assertEquals("14:30 · in 25 min", nextLabel(m.next!!))
        assertEquals("30 min", duration(m.next!!.durationMin))
        assertEquals("1 h 15 min", duration(75))
        assertEquals(listOf(null, "Tomorrow"), m.upcoming.map { it.day })
        val early = AgendaModel.build(evs, emptyList(), two.withHour(12), h24 = true)
        assertNull(early.next?.minutes)
        assertEquals("14:30", nextLabel(early.next!!))
        val during = AgendaModel.build(evs, emptyList(), two.withHour(14).withMinute(40), h24 = true)
        assertTrue(during.next!!.underWay)
        assertEquals("Now · until 15:00", nextLabel(during.next!!))
        val evening = AgendaModel.build(evs, emptyList(), two.withHour(20), h24 = true)
        assertEquals("Tomorrow 10:00", nextLabel(evening.next!!))
        // The redraws that keep it true: each minute with a countdown, at the start of the hour before, at the end when under way.
        val t = two.toInstant().toEpochMilli()
        assertEquals(t - t % 60_000 + 60_000, AgendaTicks.nextChange(AgendaStyle.NUMERAL, m, t))
        val e0 = early.next!!.event
        assertEquals(e0.start - 60 * 60_000L, AgendaTicks.nextChange(AgendaStyle.NUMERAL, early, two.withHour(12).toInstant().toEpochMilli()))
        assertEquals(during.next!!.event.end, AgendaTicks.nextChange(AgendaStyle.NUMERAL, during, two.withHour(14).withMinute(40).toInstant().toEpochMilli()))
        // The glass timeline's now-line moves every quarter hour; the other styles need no ticks.
        assertEquals(t - t % AgendaTicks.QUARTER + AgendaTicks.QUARTER, AgendaTicks.nextChange(AgendaStyle.GLASS, m, t))
        assertNull(AgendaTicks.nextChange(AgendaStyle.PAPER, m, t))
        assertNull(AgendaTicks.nextChange(AgendaStyle.WEEK, m, t))
        assertNull(AgendaTicks.nextChange(AgendaStyle.GLASS, AgendaModel.build(emptyList(), emptyList(), two), t))
    }

    /**
     * With events up to two weeks ahead, "Mon 17:35" could be this
     * Monday or the next. "Today" and "Tomorrow", the weekday two to six days ahead, then the weekday
     * with the date; the card, the rows and the tasks' notes follow it.
     */
    @Test fun laterDaysCarryTheirDate() {
        val old = Locale.getDefault()
        try {
            Locale.setDefault(Locale.UK)
            val today = two.toLocalDate() // Wednesday 7 October 2026
            assertEquals(listOf("Today", "Tomorrow", "Fri", "Sat", "Sun", "Mon", "Tue", "Wed 14 Oct", "Mon 19 Oct"),
                listOf(0L, 1, 2, 3, 4, 5, 6, 7, 12).map { AgendaModel.relativeDay(today.plusDays(it), today) })
            val evs = listOf(ev2("Mon", at2(5, 17, 35), at2(5, 19)), ev2("Mon next", at2(12, 17, 35), at2(12, 19)),
                ev2("Tue next", at2(13, 9), at2(13, 10)))
            val m = AgendaModel.build(evs, listOf(TaskItem("k", "Hand in grades", at2(12, 0).take(10), dueAllDay = true)), two, days = 14, h24 = true)
            assertEquals("Mon 17:35", nextLabel(m.next!!))
            assertEquals(listOf("Mon 19 Oct", "Tue 20 Oct"), m.upcoming.map { it.day })
            assertEquals("Mon 19 Oct", m.tasks.single().note)
            // Rows that go on with the card's day get no heading; each later day one over its first event.
            val rows = NumeralList.rows(m.upcoming, m.next!!.day)
            assertEquals(listOf("Mon 19 Oct", "ev", "Tue 20 Oct", "ev"), rows.map { (it as? NumeralList.Row.Day)?.day ?: "ev" })
            Locale.setDefault(Locale.GERMANY)
            assertEquals("Mo., 19. Okt.", AgendaModel.relativeDay(today.plusDays(12), today))
        } finally {
            Locale.setDefault(old)
        }
    }

    /** Only the width changes the layout: under 300 dp wide is the 3×2 one; the height never is a breakpoint. */
    @Test fun onlyTheWidthChangesTheLayout() {
        assertFalse(AgendaSize.of(DpSize(373.dp, 315.dp)).narrow)
        assertFalse(AgendaSize.of(DpSize(373.dp, 160.dp)).narrow)
        assertTrue(AgendaSize.of(DpSize(280.dp, 210.dp)).narrow)
        assertFalse(AgendaSize.of(DpSize(300.dp, 250.dp)).narrow)
    }

    /** Every style's top block with one row of each list fits two rows on a Pixel (about 205 dp), wide and narrow. */
    @Test fun everyStyleFitsTwoRows() {
        AgendaStyle.entries.forEach { st ->
            listOf(false, true).forEach { narrow ->
                val need = AgendaSize.need(st, narrow)
                assertTrue("$st narrow=$narrow needs $need dp", need <= AgendaSize.MIN_HEIGHT)
            }
        }
        // The numeral duotone: its numeral, the next-event card with a title of two lines and one event
        // after it (195 dp before the compact layout of 12 Oct 2026).
        assertEquals(169f, AgendaSize.need(AgendaStyle.NUMERAL), 0.01f)
        // The timeline keeps 15 dp an hour or more: 13 hours at 4×3, 6 at 4×2, never under 3.
        assertEquals(13, GlassDims.hours(GlassDims.timelineHeight(315f, narrow = false)))
        assertEquals(6, GlassDims.hours(GlassDims.timelineHeight(210f, narrow = false)))
        assertEquals(3, GlassDims.hours(GlassDims.timelineHeight(150f, narrow = true)))
        assertTrue(GlassDims.timelineHeight(210f, narrow = false) / 6 >= GlassDims.HOUR)
    }

    /** The resize limits: three columns and two rows on a phone; the default size is at least as wide as the limit. */
    @Test fun theResizeLimitsAllowThreeByTwo() {
        val ctx = androidx.test.core.app.ApplicationProvider.getApplicationContext<Application>()
        val p = ctx.resources.getXml(io.github.murtazanasir.mailr.R.xml.agenda_widget)
        val ns = "http://schemas.android.com/apk/res/android"
        var attrs: Map<String, String> = emptyMap()
        while (p.next() != org.xmlpull.v1.XmlPullParser.END_DOCUMENT) {
            if (p.eventType == org.xmlpull.v1.XmlPullParser.START_TAG && p.name == "appwidget-provider") {
                attrs = listOf("minWidth", "minResizeWidth", "minResizeHeight", "targetCellWidth", "targetCellHeight")
                    .associateWith { p.getAttributeValue(ns, it) }
            }
        }
        fun dp(k: String) = attrs.getValue(k).removeSuffix("dip").removeSuffix("dp").toFloat()
        // Android's own two-row value: a launcher counts rows by its smallest cell, so more would forbid 4×2.
        assertEquals(110f, dp("minResizeHeight"), 0.01f)
        // Three columns on a Pixel (about 93 dp a column), not two.
        assertTrue(dp("minResizeWidth") in 190f..270f)
        assertTrue(dp("minWidth") >= dp("minResizeWidth"))
        assertEquals("4", attrs["targetCellWidth"])
        assertEquals("3", attrs["targetCellHeight"])
    }

    /** Paper & rail: every day is listed (the list scrolls); today's ended events stay faded while today fits the visible height. */
    @Test fun theRailKeepsTodayWhole() {
        val evs = listOf(ev2("a", at2(0, 8, 30), at2(0, 9)), ev2("b", at2(0, 11), at2(0, 12)), ev2("c", at2(0, 14, 30), at2(0, 15)),
            ev2("d", at2(0, 17, 35), at2(0, 18)), ev2("e", at2(1, 10), at2(1, 11)), ev2("f", at2(1, 13), at2(1, 14)))
        val m = AgendaModel.build(evs, emptyList(), two)
        val rows = PaperRail.rows(m, 130f)
        assertEquals(listOf("a", "b", "c", "d", "e", "f"), rows.filterIsInstance<PaperRail.Row.Ev>().map { it.e.title })
        assertTrue(rows.filterIsInstance<PaperRail.Row.Ev>().take(2).all { it.e.past })
        assertEquals(2, rows.count { it is PaperRail.Row.Head })
        assertTrue((rows.first() as PaperRail.Row.Head).first)
        // Little room: the ended events give way, the later days stay (they scroll).
        val tight = PaperRail.rows(m, 80f)
        assertEquals(listOf("c", "d", "e", "f"), tight.filterIsInstance<PaperRail.Row.Ev>().map { it.e.title })
        assertTrue(PaperRail.rows(AgendaModel.signedOut(two), 400f).isEmpty())
    }

    /** Week strip: the last ended event while today fits; narrow, the tasks follow the days in the same list. */
    @Test fun theWeekListScrollsWithTheTasksWhenNarrow() {
        val evs = listOf(ev2("a", at2(0, 8, 30), at2(0, 9)), ev2("c", at2(0, 14, 30), at2(0, 15)), ev2("e", at2(1, 10), at2(1, 11)))
        val tasks = listOf(task("t1", "Record the class", date(1)), task("t2", "Send grades", date(-1)))
        val m = AgendaModel.build(evs, tasks, two)
        val wide = WeekList.rows(m, 100f)
        assertEquals(listOf("a", "c", "e"), wide.filterIsInstance<WeekList.Row.Ev>().map { it.e.title })
        assertTrue(wide.none { it is WeekList.Row.Task || it is WeekList.Row.TasksHead })
        assertEquals(listOf("c", "e"), WeekList.rows(m, 40f).filterIsInstance<WeekList.Row.Ev>().map { it.e.title })
        val narrow = WeekList.rows(m, 100f, withTasks = true)
        val head = narrow.filterIsInstance<WeekList.Row.TasksHead>().single()
        assertEquals(2, head.count)
        assertFalse(head.first)
        assertEquals(2, narrow.count { it is WeekList.Row.Task })
        assertTrue(narrow.last() is WeekList.Row.Task)
    }

    /** The hourly strip: the next full hour, then every three hours, from the stored forecast. */
    @Test fun theHourlyStripAndItsStorage() {
        val hours = (0L..12L).map { AgendaModel.Hour(two.withMinute(0).plusHours(it).toInstant().toEpochMilli(), WeatherIcon.CLEAR_DAY, 70 - it.toInt()) }
        val slots = AgendaModel.hourSlots(hours, two, h24 = true)
        assertEquals(listOf("15", "18", "21", "00"), slots.map { it.label })
        assertEquals(listOf(69, 66, 63, 60), slots.map { it.temp })
        assertEquals("3 PM", AgendaModel.hourSlots(hours, two, h24 = false).first().label)
        val o = Json.parseToJsonElement("""{"current":{"temperature_2m":18.3,"weather_code":2,"is_day":1},
            "daily":{"temperature_2m_max":[25.0],"temperature_2m_min":[12.0]},
            "hourly":{"time":[1791392400,1791396000,1791399600],"temperature_2m":[17.5,18.0,16.0],"weather_code":[2,0,61],"is_day":[1,1,0]}}""").jsonObject
        val r = OpenMeteo.parseForecast(o, WeatherPlace(37.7, -97.3, null), 1791392400_000L)
        assertEquals(3, r.hours.size)
        assertEquals(WeatherIcon.RAIN, WeatherIcon.of(r.hours[2].code, r.hours[2].day))
        assertEquals(r.hours, WeatherStore.decodeHours(WeatherStore.encodeHours(r.hours)))
        val s = WeatherStore(h.app)
        s.source = WeatherSource.CITY
        s.city = WeatherPlace(37.7, -97.3, "Springfield")
        s.units = WeatherUnits.CELSIUS
        s.reading = r.copy(fetchedAt = AppClock.millis())
        assertEquals(listOf(18, 18, 16), s.shown(AppClock.millis())!!.hours.map { it.temp })
    }

    /** Each widget keeps its own style; widgets placed before styles get the default written down; a removed widget's goes. */
    @Test fun theStyleIsKeptPerWidget() {
        val st = AgendaStyles(h.app)
        assertEquals(AgendaStyle.WEEK, st[11])
        st[11] = AgendaStyle.GLASS
        st[12] = AgendaStyle.NUMERAL
        assertEquals(AgendaStyle.GLASS, AgendaStyles(h.app)[11])
        assertEquals(AgendaStyle.NUMERAL, AgendaStyles(h.app)[12])
        assertEquals(1, st.migrate(intArrayOf(11, 12, 13)))
        assertTrue(st.has(13))
        assertEquals(AgendaStyle.DEFAULT, st[13])
        assertEquals(AgendaStyle.GLASS, st[11])
        assertEquals(0, st.migrate(intArrayOf(11, 12, 13)))
        st.remove(12)
        assertFalse(st.has(12))
        assertEquals(AgendaStyle.PAPER, AgendaStyle.of("paper"))
        assertNull(AgendaStyle.of("bogus"))
    }

    /** The weather's place: asked for when the settings open with "this phone's location"; a no offers a city and is respected. */
    @Test fun theLocationIsAskedWhenTheSettingsOpen() {
        val fresh = WeatherPermissionFlow.State(weatherOn = true, source = WeatherSource.LOCATION, granted = false, placeKnown = false, denied = false)
        assertEquals(WeatherPermissionFlow.Step.ASK, WeatherPermissionFlow.onOpen(fresh))
        assertEquals(WeatherPermissionFlow.Step.NONE, WeatherPermissionFlow.onOpen(fresh.copy(askedNow = true)))
        assertEquals(WeatherPermissionFlow.Step.NONE, WeatherPermissionFlow.onOpen(fresh.copy(granted = true)))
        assertEquals(WeatherPermissionFlow.Step.NONE, WeatherPermissionFlow.onOpen(fresh.copy(source = WeatherSource.CITY)))
        assertEquals(WeatherPermissionFlow.Step.NONE, WeatherPermissionFlow.onOpen(fresh.copy(weatherOn = false)))
        // A no: the city search opens, and the settings never ask again on their own.
        assertEquals(WeatherPermissionFlow.Step.OFFER_CITY, WeatherPermissionFlow.onResult(false))
        assertEquals(WeatherPermissionFlow.Step.NONE, WeatherPermissionFlow.onResult(true))
        assertEquals(WeatherPermissionFlow.Step.OFFER_CITY, WeatherPermissionFlow.onOpen(fresh.copy(denied = true)))
        assertEquals(WeatherPermissionFlow.Step.NONE, WeatherPermissionFlow.onDone(fresh.copy(denied = true)))
        // Done with no place: asked once if not yet asked in this visit.
        assertEquals(WeatherPermissionFlow.Step.ASK, WeatherPermissionFlow.onDone(fresh))
        assertEquals(WeatherPermissionFlow.Step.NONE, WeatherPermissionFlow.onDone(fresh.copy(askedNow = true)))
        assertEquals(WeatherPermissionFlow.Step.NONE, WeatherPermissionFlow.onDone(fresh.copy(placeKnown = true)))
        // The store keeps the no.
        val s = WeatherStore(h.app)
        assertFalse(s.locationDenied)
        s.locationDenied = true
        assertTrue(WeatherStore(h.app).locationDenied)
    }

    /** No place yet with weather on: the widget says "Set weather", and its tap opens this widget's settings. */
    @Test fun noPlaceShowsSetWeather() = runBlocking {
        val s = WeatherStore(h.app)
        assertTrue(s.enabled && s.place == null)
        assertTrue(AgendaWidgets.model(h.app).weatherHint)
        s.source = WeatherSource.CITY
        s.city = WeatherPlace(37.7, -97.3, "Springfield")
        assertFalse(AgendaWidgets.model(h.app).weatherHint)
        val i = AgendaWidgets.settings(h.app, 42)
        assertEquals(AgendaSettingsActivity::class.java.name, i.component?.className)
        assertEquals(42, i.getIntExtra(android.appwidget.AppWidgetManager.EXTRA_APPWIDGET_ID, 0))
        assertTrue(i.data.toString() != AgendaWidgets.settings(h.app, 43).data.toString())
    }
}
