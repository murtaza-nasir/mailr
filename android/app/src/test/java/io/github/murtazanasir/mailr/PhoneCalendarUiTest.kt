package io.github.murtazanasir.mailr

import android.app.Application
import android.content.Intent
import android.provider.CalendarContract
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.test.assertTextContains
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.unit.dp
import com.github.takahirom.roborazzi.captureRoboImage
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.phonecal.PhoneCalMode
import io.github.murtazanasir.mailr.data.phonecal.PhoneCalendar
import io.github.murtazanasir.mailr.data.phonecal.PhoneEvents
import io.github.murtazanasir.mailr.data.phonecal.PhoneInstance
import io.github.murtazanasir.mailr.data.phonecal.ProviderValues
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.ui.calendar.AgendaView
import io.github.murtazanasir.mailr.ui.calendar.CalendarViewModel
import io.github.murtazanasir.mailr.ui.calendar.GridActions
import io.github.murtazanasir.mailr.ui.calendar.PhoneEventDetail
import io.github.murtazanasir.mailr.ui.calendar.TimeGrid
import io.github.murtazanasir.mailr.ui.calendar.dayEvents
import io.github.murtazanasir.mailr.ui.settings.PhoneCalendarsCallbacks
import io.github.murtazanasir.mailr.ui.settings.PhoneCalendarsScreen
import io.github.murtazanasir.mailr.ui.settings.PhoneCalendarsUi
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import kotlinx.coroutines.launch
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.time.ZoneId

/**
 * Phone calendars on screen: the settings page (denied, on with calendars, a choice per calendar),
 * the calendar's events beside mailr's and read-only (a drag sends nothing), and the read-only detail
 * with "Open in Calendar", and a meeting also in Mailr's calendar shown once and not sent. Three
 * screenshots, light, phone.
 */
@OptIn(androidx.compose.ui.test.ExperimentalTestApi::class)
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp-port-notnight-xxhdpi")
class PhoneCalendarUiTest {
    @get:Rule val compose = createComposeRule()
    private val h = CalendarHarness()
    private val zone: ZoneId get() = ZoneId.systemDefault()
    private val now = AppClock.millis()
    private val hour = 3_600_000L
    private val tomorrow = AppClock.today().plusDays(1)
    private fun ms(h: Int, m: Int = 0) = tomorrow.atTime(h, m).atZone(zone).toInstant().toEpochMilli()

    private val work = PhoneCalendar(7, "Calendar", "alex@work.example", "com.google.android.gm.exchange", 0xFF0078D4.toInt())
    private val family = PhoneCalendar(9, "Family", "me@gmail.com", "com.google", 0xFF33B679.toInt())

    @Before fun setUp() {
        h.start()
        FakeCalendarProvider.install(h.app)
        FakeCalendarProvider.calendars += listOf(
            FakeCalendarProvider.Cal(work.id, work.name, work.accountName, work.accountType, work.color),
            FakeCalendarProvider.Cal(family.id, family.name, family.accountName, family.accountType, family.color))
        FakeCalendarProvider.instances += FakeCalendarProvider.Inst(4242, work.id, ms(13), ms(14, 30), title = "Faculty senate",
            location = "Hall B 210")
    }

    @After fun tearDown() {
        h.g.phoneCalendars.prefs.setEnabled(false)
        h.stop()
    }

    private fun theme(content: @Composable () -> Unit) {
        compose.setContent {
            MailrTheme(ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.surface) { content() }
            }
        }
        compose.mainClock.advanceTimeBy(500)
        compose.waitForIdle()
    }

    private val senate get() = PhoneEvents.toEvent(
        PhoneInstance(4242, work.id, ms(13), ms(14, 30), false, "Faculty senate", "Hall B 210", ProviderValues.AVAILABILITY_BUSY, 1, null),
        work, zone)

    // ---------------- settings ----------------

    @Test fun settingsListsTheCalendarsAndSetsEachOne() {
        val picked = mutableListOf<Pair<Long, PhoneCalMode>>()
        theme {
            PhoneCalendarsScreen(PhoneCalendarsUi(enabled = true, granted = true, deniedForever = false, calendars = listOf(work, family),
                modes = mapOf(work.id to PhoneCalMode.SEND, family.id to PhoneCalMode.DETAILS)),
                PhoneCalendarsCallbacks({}, {}, { id, m -> picked += id to m }, {}))
        }
        compose.onNodeWithTag("phonecal-7").assertTextContains("Show and send busy times", substring = true)
        compose.onNodeWithTag("phonecal-9").assertTextContains("Show and send details", substring = true)
        compose.onNodeWithText("“Show and send details”, each event's title and place are sent as well", substring = true).assertExists()
        compose.onRoot().captureRoboImage("screenshots/phonecal-light-settings.png")
        // Four choices, in order; the details one says what it sends.
        compose.onNodeWithTag("phonecal-7").performClick()
        listOf("off", "show", "send", "details").forEach { compose.onNodeWithTag("phonecal-7-$it").assertExists() }
        assertEquals(listOf(PhoneCalMode.OFF, PhoneCalMode.SHOW, PhoneCalMode.SEND, PhoneCalMode.DETAILS), PhoneCalMode.entries)
        compose.onNodeWithTag("phonecal-7-details")
            .assertTextContains("Titles and places are sent to your Mailr server; guests and descriptions are not.", substring = true)
        compose.onNodeWithTag("phonecal-7-details").performClick()
        compose.onNodeWithTag("phonecal-9").performClick()
        compose.onNodeWithTag("phonecal-9-send").performClick()
        compose.onNodeWithTag("phonecal-7").performClick()
        compose.onNodeWithTag("phonecal-7-off").performClick()
        assertEquals(listOf(work.id to PhoneCalMode.DETAILS, family.id to PhoneCalMode.SEND, work.id to PhoneCalMode.OFF), picked)
    }

    @Test fun settingsAfterDontAskAgainOffersTheSystemSettings() {
        val calls = mutableListOf<String>()
        theme {
            PhoneCalendarsScreen(PhoneCalendarsUi(enabled = false, granted = false, deniedForever = true, calendars = null, modes = emptyMap()),
                PhoneCalendarsCallbacks({}, { calls += "switch:$it" }, { _, _ -> }, { calls += "settings" }))
        }
        compose.onNodeWithText("Calendar access is not allowed", substring = true).assertExists()
        compose.onNodeWithText("Calendars on this phone").assertDoesNotExist()
        compose.onNodeWithTag("phonecal-open-settings").performClick()
        compose.onNodeWithText("Show phone calendars").performClick()
        assertEquals(listOf("settings", "switch:true"), calls)
    }

    // ---------------- the calendar ----------------

    @Test fun shownCalendarsJoinTheCalendarAndCannotBeDragged() {
        val pc = h.g.phoneCalendars
        pc.permissionAnswered(granted = true, canAskAgain = true)
        pc.setMode(work.id, PhoneCalMode.SHOW)
        val vm = h.vm { CalendarViewModel(h.g) }
        vm.setAnchor(tomorrow)
        val events = mutableListOf<CalendarEvent>()
        val scope = h.graphs.scope()
        scope.launch { vm.events.collect { events.clear(); events += it.orEmpty() } }
        eventually("the phone event in the calendar's events", idle = h::idle) { events.any { PhoneEvents.isPhone(it) } }
        val e = events.first { PhoneEvents.isPhone(it) }
        assertEquals("Faculty senate", e.summary)
        assertTrue(vm.readOnly(e))
        scope.coroutineContext[kotlinx.coroutines.Job]?.cancel()

        // A long-press drag on it moves nothing and sends nothing; "Read-only calendar" is said instead.
        val said = mutableListOf<String>()
        compose.setContent {
            MaterialTheme {
                TimeGrid(listOf(tomorrow), dayEvents(listOf(e), emptyList()), AppClock.millis(), null, {}, {}, null,
                    actions = GridActions(createRange = { _, _ -> }, move = vm::requestTimes, duplicate = vm::duplicate,
                        delete = vm::requestDelete, reschedule = {}, readOnly = vm::readOnly, notify = { said += it }))
            }
        }
        compose.mainClock.advanceTimeBy(500)
        compose.waitForIdle()
        val step = with(compose.density) { 56.dp.toPx() }
        compose.onNode(hasContentDescription("Faculty senate", substring = true)).performTouchInput {
            down(center); advanceEventTime(800)
            repeat(10) { moveBy(Offset(0f, step / 10)); advanceEventTime(16) }
            up()
        }
        compose.waitForIdle(); h.idle()
        assertEquals(listOf("Read-only calendar"), said)
        assertTrue(h.sentNothing())
        // TalkBack has no move actions on it either; it says "On this phone".
        compose.onNode(hasContentDescription("On this phone", substring = true)).assertExists()
    }

    @Test fun busyTimesThisPhoneSentAreNotDrawnTwice() {
        // The server sends back the busy times of each phone calendar as a calendar of its own. The ones
        // this phone sent are hidden here (the phone's own events are already shown); another phone's stay.
        val own = "a".repeat(32)
        val other = "b".repeat(32)
        h.g.phoneCalendars.prefs.putSent(io.github.murtazanasir.mailr.data.phonecal.PhoneCalendarPrefs.Sent(own, work.id, "h", now))
        fun col(key: String, device: String) = io.github.murtazanasir.mailr.data.api.CalendarCollection(url = "phone:$key",
            name = "Calendar", events = true, readOnly = true, phone = io.github.murtazanasir.mailr.data.api.PhoneInfo(key, device))
        fun busy(key: String, at: Int): io.github.murtazanasir.mailr.data.db.CalEventEntity {
            val s = tomorrow.atTime(at, 0).atZone(zone)
            val e = CalendarEvent("phone-$key-$at@mailr", "Busy", s.toOffsetDateTime().toString(), s.plusHours(1).toOffsetDateTime().toString(),
                calendar = "phone:$key")
            return io.github.murtazanasir.mailr.data.db.CalEventEntity(e.key, e.uid, s.toInstant().toEpochMilli(), s.plusHours(1).toInstant().toEpochMilli(),
                false, null, io.github.murtazanasir.mailr.data.api.MailrJson.encodeToString(CalendarEvent.serializer(), e))
        }
        kotlinx.coroutines.runBlocking {
            val cols = listOf(col(own, "This phone"), col(other, "Tablet"))
            h.g.db.calendar().putCollections(cols.map {
                io.github.murtazanasir.mailr.data.db.CalCollectionEntity(it.url, null, it.name, null, true, false, false, 0, null,
                    io.github.murtazanasir.mailr.data.api.MailrJson.encodeToString(io.github.murtazanasir.mailr.data.api.CalendarCollection.serializer(), it))
            })
            h.g.db.calendar().putEvents(listOf(busy(own, 9), busy(other, 11)))
            h.g.calendar.loadCached()
        }
        val vm = h.vm { CalendarViewModel(h.g) }
        vm.setAnchor(tomorrow)
        val events = mutableListOf<CalendarEvent>()
        val scope = h.graphs.scope()
        scope.launch { vm.events.collect { events.clear(); events += it.orEmpty() } }
        eventually("the other phone's busy time in the calendar's events", idle = h::idle) { events.any { it.calendar == "phone:$other" } }
        assertTrue(events.none { it.calendar == "phone:$own" })
        scope.coroutineContext[kotlinx.coroutines.Job]?.cancel()
    }

    @Test fun aMeetingAlsoInMailrsCalendarIsShownOnceAndNotSent() {
        // The phone holds "Faculty senate" (13:00–14:30, from setUp), "Garden club" (no answer recorded, tentative)
        // and "Evening walk". Mailr's calendar holds the first, accepted, and the second, declined, in a
        // calendar hidden on this phone.
        FakeCalendarProvider.instances += listOf(
            FakeCalendarProvider.Inst(4243, work.id, ms(16), ms(17), title = "Garden club", availability = ProviderValues.AVAILABILITY_TENTATIVE,
                status = null, self = 0),
            FakeCalendarProvider.Inst(4244, work.id, ms(18), ms(19), title = "Evening walk"))
        fun col(url: String, hidden: Boolean) = io.github.murtazanasir.mailr.data.api.CalendarCollection(url = url, name = url, events = true,
            hidden = hidden)
        fun row(e: CalendarEvent, from: Long, to: Long) = io.github.murtazanasir.mailr.data.db.CalEventEntity(e.key, e.uid, from, to, false,
            e.calendar, io.github.murtazanasir.mailr.data.api.MailrJson.encodeToString(CalendarEvent.serializer(), e))
        fun at(ms: Long) = java.time.Instant.ofEpochMilli(ms).atZone(zone).toOffsetDateTime().toString()
        val senateMailr = CalendarEvent("m-senate", "Faculty senate", at(ms(13)), at(ms(14, 30)), calendar = "/dav/work/", myStatus = "ACCEPTED")
        val clubMailr = CalendarEvent("m-club", "Declined: Garden club", at(ms(16)), at(ms(17)), calendar = "/dav/club/", myStatus = "DECLINED")
        kotlinx.coroutines.runBlocking {
            h.g.db.calendar().putCollections(listOf(col("/dav/work/", false), col("/dav/club/", true)).map {
                io.github.murtazanasir.mailr.data.db.CalCollectionEntity(it.url, null, it.name, null, true, false, it.hidden, 0, null,
                    io.github.murtazanasir.mailr.data.api.MailrJson.encodeToString(io.github.murtazanasir.mailr.data.api.CalendarCollection.serializer(), it))
            })
            h.g.db.calendar().putEvents(listOf(row(senateMailr, ms(13), ms(14, 30)), row(clubMailr, ms(16), ms(17))))
            h.g.calendar.loadCached()
        }
        val pc = h.g.phoneCalendars
        pc.permissionAnswered(granted = true, canAskAgain = true)
        pc.setMode(work.id, PhoneCalMode.SEND)
        val vm = h.vm { CalendarViewModel(h.g) }
        vm.setAnchor(tomorrow)
        val events = mutableListOf<CalendarEvent>()
        val scope = h.graphs.scope()
        scope.launch { vm.events.collect { events.clear(); events += it.orEmpty() } }
        eventually("the unmatched phone event in the calendar's events", idle = h::idle) { events.any { it.summary == "Evening walk" } }
        // Shown once, from Mailr's calendar; the declined meeting is in a hidden calendar, so it is not shown at all.
        assertEquals(listOf("m-senate"), events.filter { it.summary == "Faculty senate" }.map { it.uid })
        assertTrue(events.none { it.summary.contains("Garden club") })
        scope.coroutineContext[kotlinx.coroutines.Job]?.cancel()

        kotlinx.coroutines.runBlocking { pc.sync() }
        val put = h.requests.single { it.first.method == "PUT" }
        val busy = io.github.murtazanasir.mailr.data.api.MailrJson.parseToJsonElement(put.second).jsonObject["busy"]!!.jsonArray
            .map { it.jsonObject["id"]!!.jsonPrimitive.content }
        val key = pc.key(pc.source.calendars().first { it.id == work.id })
        assertEquals(listOf(io.github.murtazanasir.mailr.data.phonecal.PhoneBusy.busyId(key, 4244, ms(18))), busy)
    }

    @Test fun theScheduleMarksPhoneEvents() {
        val mine = CalendarEvent("c", "Office hours", tomorrow.atTime(10, 0).atZone(zone).toOffsetDateTime().toString(),
            tomorrow.atTime(11, 0).atZone(zone).toOffsetDateTime().toString(), color = "#8e24aa", location = "Hall B 301")
        theme { AgendaView(dayEvents(listOf(mine, senate), emptyList()), tomorrow, now, null, {}, {}) }
        compose.onNodeWithText("On this phone", substring = true).assertExists()
        compose.onRoot().captureRoboImage("screenshots/phonecal-light-schedule.png")
    }

    // ---------------- the detail ----------------

    @Test fun theDetailIsReadOnlyAndOpensTheCalendarApp() {
        val opened = mutableListOf<Intent>()
        theme { PhoneEventDetail(senate, "Calendar", zone, onClose = {}, onOpenInCalendar = { opened += it }) }
        compose.onRoot().captureRoboImage("screenshots/phonecal-light-detail.png")
        listOf("Edit event", "Delete event", "Remove from my calendar").forEach {
            compose.onNode(hasContentDescription(it)).assertDoesNotExist()
        }
        listOf("Going?", "Yes", "No", "Maybe", "Duplicate", "Colour", "Move to calendar", "Copy link", "No reminders").forEach {
            compose.onNodeWithText(it).assertDoesNotExist()
        }
        compose.onNodeWithText("Hall B 210").assertExists()
        compose.onNodeWithText("On this phone").assertExists()
        compose.onNodeWithTag("open-in-calendar").performClick()
        val i = opened.single()
        assertEquals(Intent.ACTION_VIEW, i.action)
        assertEquals("content://com.android.calendar/events/4242", i.data.toString())
        assertEquals(ms(13), i.getLongExtra(CalendarContract.EXTRA_EVENT_BEGIN_TIME, 0))
        assertEquals(ms(14, 30), i.getLongExtra(CalendarContract.EXTRA_EVENT_END_TIME, 0))
        assertTrue(i.flags and Intent.FLAG_ACTIVITY_NEW_TASK != 0)
    }
}
