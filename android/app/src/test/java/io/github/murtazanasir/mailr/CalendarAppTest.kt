// ACT-SURFACES: android.editor android.calendar
package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.time.AppClock
import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.collectAsState
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.hasText
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.click
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performCustomAccessibilityActionWithLabel
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.Attendee
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.EventDraft
import io.github.murtazanasir.mailr.data.api.FindTimeResult
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.TimeSlot
import io.github.murtazanasir.mailr.data.db.CalCollectionEntity
import io.github.murtazanasir.mailr.data.db.CalConflictEntity
import io.github.murtazanasir.mailr.data.db.CalEventEntity
import io.github.murtazanasir.mailr.ui.calendar.CalPane
import io.github.murtazanasir.mailr.ui.calendar.CalendarDrawer
import io.github.murtazanasir.mailr.ui.calendar.CalendarSearch
import io.github.murtazanasir.mailr.ui.calendar.CalendarViewModel
import io.github.murtazanasir.mailr.ui.calendar.ChangePrompt
import io.github.murtazanasir.mailr.ui.calendar.ConflictPrompt
import io.github.murtazanasir.mailr.ui.calendar.EditorFor
import io.github.murtazanasir.mailr.ui.calendar.FindTimeSheet
import io.github.murtazanasir.mailr.ui.calendar.GridActions
import io.github.murtazanasir.mailr.ui.calendar.QuickCreateSheet
import io.github.murtazanasir.mailr.ui.calendar.TimeGrid
import io.github.murtazanasir.mailr.ui.calendar.TravelBanner
import io.github.murtazanasir.mailr.ui.calendar.dayEvents
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.ZonedDateTime
import java.util.TimeZone

/**
 * The Android Calendar app (surface android.calendar) against a fake server: views on
 * the overlap layout, dragging (long-press drag and the TalkBack actions), the series question and
 * the question whether to send updates after a drop, the drawer's visibility switches, quick create on the shared
 * grammar, the editor's guests, find a time, the conflict sheet and the travel banner.
 *
 * Covers: event.reschedule, event.resize, event.delete, event.create, event.invite, event.find_time,
 * event.search, event.duplicate, calendar.toggle_visibility, calendar.show_only.
 */
@OptIn(androidx.compose.ui.test.ExperimentalTestApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class CalendarAppTest {
    @get:Rule val compose = createComposeRule()
    private val h = CalendarHarness()
    private val zone: ZoneId get() = ZoneId.systemDefault()
    private val tomorrow: LocalDate = AppClock.today().plusDays(1)
    private lateinit var savedZone: TimeZone

    @Before fun setUp() { savedZone = TimeZone.getDefault(); h.start() }
    @After fun tearDown() { h.stop(); TimeZone.setDefault(savedZone) }

    /** Waits for [cond], advancing the composition clock (debounces) and the main looper (view model work). */
    private fun settle(what: String, cond: () -> Boolean) = eventually(what, idle = {
        compose.mainClock.advanceTimeBy(100)
        compose.waitForIdle()
        h.idle()
    }, check = cond)

    private fun iso(t: LocalDateTime) = t.atZone(zone).toOffsetDateTime().toString()

    private fun put(vararg es: CalendarEvent) = runBlocking {
        h.g.db.calendar().putEvents(es.map { e ->
            val s = OffsetDateTime.parse(e.start).toInstant().toEpochMilli()
            val en = OffsetDateTime.parse(e.end!!).toInstant().toEpochMilli()
            CalEventEntity(e.key, e.uid, s, en, false, e.calendar, MailrJson.encodeToString(CalendarEvent.serializer(), e))
        })
    }

    /** A weekly event I organize, with two guests. */
    private val series get() = CalendarEvent("s1", "Team sync", iso(tomorrow.atTime(15, 0)), iso(tomorrow.atTime(16, 0)),
        recurring = true, rrule = "FREQ=WEEKLY", organizer = "me@example.com", isOrganizer = true,
        attendeeList = listOf(Attendee("dana@example.com", "Dana", "ACCEPTED"), Attendee("lee@example.com", "Lee")))

    private val design get() = CalendarEvent("g1", "Design review", iso(tomorrow.atTime(10, 0)), iso(tomorrow.atTime(11, 0)))

    private fun grid(vm: CalendarViewModel, days: List<LocalDate>, events: List<CalendarEvent>, created: MutableList<Pair<LocalDateTime, LocalDateTime>>? = null) {
        compose.setContent {
            MaterialTheme {
                TimeGrid(days, dayEvents(events, emptyList()), AppClock.millis(), null, {}, {}, null,
                    actions = GridActions(
                        createRange = { s, e -> created?.add(s to e) },
                        move = vm::requestTimes, duplicate = vm::duplicate, delete = vm::requestDelete, reschedule = {},
                        readOnly = vm::readOnly, notify = {}))
            }
        }
        // Let the grid scroll to the morning before any touch is placed.
        compose.mainClock.advanceTimeBy(500)
        compose.waitForIdle()
    }

    // ---------------- views (the overlap layout) ----------------

    /** Five events at once: three columns and a "+2" chip; a night shift continues with "↳"; tentative is hatched. */
    @Test fun gridUsesLayoutDay() {
        val vm = h.vm { CalendarViewModel(h.g) }
        val at = tomorrow.atTime(13, 0)
        val five = (1..5).map { i -> CalendarEvent("o$i", "Overlap $i", iso(at), iso(at.plusHours(1))) }
        val night = CalendarEvent("n1", "Night shift", iso(tomorrow.atTime(22, 0)), iso(tomorrow.plusDays(1).atTime(6, 0)))
        val maybe = CalendarEvent("t1", "Maybe lunch", iso(tomorrow.atTime(12, 0)), iso(tomorrow.atTime(12, 45)),
            myStatus = "TENTATIVE", organizer = "dana@example.com")
        grid(vm, listOf(tomorrow, tomorrow.plusDays(1), tomorrow.plusDays(2)), five + night + maybe)
        compose.onNodeWithContentDescription("2 more events").assertExists()
        assertEquals(3, compose.onAllNodes(hasContentDescription("Overlap", substring = true)).fetchSemanticsNodes().size)
        compose.onNodeWithText("↳ Night shift").assertExists()
        compose.onNode(hasContentDescription("Maybe lunch", substring = true) and hasContentDescription("tentative", substring = true)).assertExists()
    }

    // ---------------- dragging ----------------

    /** event.reschedule by long-press, drag one hour down, drop: a single event commits at once. */
    @Test fun longPressDragMovesTheEvent() {
        put(design)
        val vm = h.vm { CalendarViewModel(h.g) }
        grid(vm, listOf(tomorrow), listOf(design))
        val hour = with(compose.density) { 56.dp.toPx() }
        compose.onNode(hasContentDescription("Design review", substring = true)).performTouchInput {
            down(center)
            advanceEventTime(800)
            repeat(10) { moveBy(Offset(0f, hour / 10)); advanceEventTime(16) }
            up()
        }
        val body = h.sent("PATCH", "/api/v1/calendar/events/g1")
        assertEquals(tomorrow.atTime(11, 0).atZone(zone).toInstant(), OffsetDateTime.parse(body["start"]!!.jsonPrimitive.content).toInstant())
        assertEquals(tomorrow.atTime(12, 0).atZone(zone).toInstant(), OffsetDateTime.parse(body["end"]!!.jsonPrimitive.content).toInstant())
    }

    /** event.reschedule through TalkBack: "Move later 15 min" moves start and end. */
    @Test fun talkBackMovesLater() {
        put(design)
        val vm = h.vm { CalendarViewModel(h.g) }
        grid(vm, listOf(tomorrow), listOf(design))
        compose.onNode(hasContentDescription("Design review", substring = true)).performCustomAccessibilityActionWithLabel("Move later 15 min")
        val body = h.sent("PATCH", "/api/v1/calendar/events/g1")
        assertEquals(tomorrow.atTime(10, 15).atZone(zone).toInstant(), OffsetDateTime.parse(body["start"]!!.jsonPrimitive.content).toInstant())
    }

    /** event.create: a tap shows the ghost; a second tap on it opens quick create with that slot. */
    @Test fun tapTwiceCreates() {
        val vm = h.vm { CalendarViewModel(h.g) }
        val created = mutableListOf<Pair<LocalDateTime, LocalDateTime>>()
        grid(vm, listOf(tomorrow), emptyList(), created)
        val hour = with(compose.density) { 56.dp.toPx() }
        // Touches are placed in the node's visible part, which starts at 8:00 (the grid opens there on a day other than today): 9:10.
        compose.onNodeWithTag("calendar-grid").performTouchInput { click(Offset(centerX, hour * 1 + hour / 6)) }
        compose.waitForIdle()
        assertTrue(created.isEmpty())
        compose.onNode(hasContentDescription("tap again to create", substring = true)).performClick()
        assertEquals(listOf(tomorrow.atTime(9, 0) to tomorrow.atTime(10, 0)), created)
    }

    // ---------------- the series question, send updates ----------------

    /** event.reschedule on a series with guests: the scope sheet, then the send-updates sheet, then the write. */
    @Test fun dropOnSeriesAsksScopeThenSendUpdates() {
        put(series)
        val vm = h.vm { CalendarViewModel(h.g) }
        vm.requestTimes(series, tomorrow.atTime(16, 0), tomorrow.atTime(17, 0))
        compose.setContent { MaterialTheme { ChangePrompt(vm.prompt.collectAsStateValue(), vm) } }
        compose.onNodeWithText("Change recurring event").assertExists()
        compose.onNodeWithText("OK").performClick()
        compose.onNodeWithText("Send update to 2 guests?").assertExists()
        compose.onNodeWithText("Don't send").performClick()
        val body = h.sent("PATCH", "/api/v1/calendar/events/s1")
        assertEquals("this", body["scope"]!!.jsonPrimitive.content)
        assertFalse(body["send_updates"]!!.jsonPrimitive.boolean)
        assertNull(vm.prompt.value)
    }

    /** event.resize keeps the start; Back on the send-updates sheet changes nothing. */
    @Test fun resizeBackSendsNothing() {
        val single = series.copy(uid = "s2", recurring = false, rrule = null)
        put(single)
        val vm = h.vm { CalendarViewModel(h.g) }
        vm.requestTimes(single, tomorrow.atTime(15, 0), tomorrow.atTime(16, 30))
        assertEquals("send", vm.prompt.value?.step)
        compose.setContent { MaterialTheme { ChangePrompt(vm.prompt.collectAsStateValue(), vm) } }
        compose.onNodeWithText("Back").performClick()
        compose.waitForIdle()
        assertNull(vm.prompt.value)
        assertTrue(h.requests.none { it.first.method == "PATCH" })
    }

    /** event.delete of an event I organize with guests: "Send cancellation", then the delayed delete. */
    @Test fun deleteAsksToSendCancellation() {
        val single = series.copy(uid = "s3", recurring = false, rrule = null)
        put(single)
        val vm = h.vm { CalendarViewModel(h.g) }
        vm.requestDelete(single)
        compose.setContent { MaterialTheme { ChangePrompt(vm.prompt.collectAsStateValue(), vm) } }
        compose.onNodeWithText("Send cancellation to 2 guests?").assertExists()
        compose.onNodeWithText("Send").performClick()
        h.until("delete scheduled") { h.delayed.any { it.method == "DELETE" && it.path.startsWith("calendar/events/s3") && "send_updates=true" in it.path } }
    }

    // ---------------- drawer ----------------

    private fun collections() = runBlocking {
        val cols = listOf(CalendarCollection("/cal/work/", "Work", "#039be5", events = true, calendarId = 7),
            CalendarCollection("/cal/home/", "Home", "#0b8043", events = true, calendarId = 8))
        h.g.db.calendar().putCollections(cols.mapIndexed { i, c ->
            CalCollectionEntity(c.url, c.calendarId, c.name, c.color, true, false, false, i, null, MailrJson.encodeToString(CalendarCollection.serializer(), c))
        })
        h.g.calendar.loadCached()
    }

    /** calendar.toggle_visibility: the switch hides a calendar on the account; with "Only on this phone", only here. */
    @Test fun drawerVisibilitySwitches() {
        collections()
        val vm = h.vm { CalendarViewModel(h.g) }
        h.until("accounts") { vm.accounts.value.flatMap { it.collections }.size == 2 }
        compose.setContent {
            MaterialTheme {
                CalendarDrawer(vm, AppClock.today(), AppClock.today() to AppClock.today().plusDays(1), emptySet(), vm.accounts.collectAsStateValue(),
                    "/cal/work/", onGo = {})
            }
        }
        compose.onNodeWithContentDescription("Show Work").performClick()
        assertTrue(h.sent("PATCH", "/api/v1/calendar/collections/7")["hidden"]!!.jsonPrimitive.boolean)
        compose.onNodeWithText("Only on this phone").performClick()
        compose.onNodeWithContentDescription("Show Home").performScrollTo().performClick()
        h.until("hidden on this phone") { vm.collections.value.first { it.url == "/cal/home/" }.deviceHidden == true }
        assertTrue(h.requests.none { it.first.path == "/api/v1/calendar/collections/8" })
    }

    /** calendar.show_only hides the others; Show all brings them back. */
    @Test fun showOnlyThis() {
        collections()
        val vm = h.vm { CalendarViewModel(h.g) }
        h.until("accounts") { vm.collections.value.size == 2 }
        vm.prefs.setDeviceOnly(true)
        vm.showOnly(vm.collections.value.first { it.url == "/cal/work/" })
        h.until("home hidden") { vm.collections.value.first { it.url == "/cal/home/" }.visible.not() }
        vm.showAll()
        h.until("home shown") { vm.collections.value.first { it.url == "/cal/home/" }.visible }
    }

    // ---------------- quick create ----------------

    /** event.create through the quick-add line: tokens become chips; the draft carries time and place; a guest waits for a tap. */
    @Test fun quickCreateParsesTheLine() {
        var saved: EventDraft? = null
        val now = AppClock.now(zone).withHour(9).withMinute(0)
        compose.setContent {
            MaterialTheme {
                QuickCreateSheet("", null, false, emptyList(), null, 10, 9, listOf("Café Luna"), zone, conflict = { _, _ -> null },
                    people = { emptyList() }, newUid = { "q1" }, onText = {}, onDismiss = {}, onSave = { saved = it }, onMore = {}, now = now)
            }
        }
        compose.onNode(hasSetTextAction()).performTextInput("Lunch with Dana tomorrow 12:30 at Café Luna")
        compose.onNodeWithText("Café Luna").assertExists()
        compose.onNodeWithContentDescription("Suggested guest Dana, tap to add").assertExists()
        compose.onNodeWithText("Save").performClick()
        val d = saved!!
        assertEquals(now.toLocalDate().plusDays(1).atTime(12, 30).atZone(zone).toInstant(), OffsetDateTime.parse(d.start).toInstant())
        assertEquals("Café Luna", d.location)
        assertNull(d.attendees)
        assertTrue(d.summary.startsWith("Lunch"))
    }

    /** × on a chip turns the token back into title text. */
    @Test fun quickCreateKeepAsText() {
        var saved: EventDraft? = null
        compose.setContent {
            MaterialTheme {
                QuickCreateSheet("", null, false, emptyList(), null, 10, 9, emptyList(), zone, conflict = { _, _ -> "Overlaps Team sync" },
                    people = { emptyList() }, newUid = { "q2" }, onText = {}, onDismiss = {}, onSave = { saved = it }, onMore = {})
            }
        }
        compose.onNode(hasSetTextAction()).performTextInput("Dinner at Home Depot tomorrow 7pm")
        compose.onNodeWithText("Overlaps Team sync").assertExists()
        compose.onNode(hasContentDescription("Keep “Home Depot” as text", substring = true)).performClick()
        compose.onNodeWithText("Save").performClick()
        assertEquals("", saved!!.location)
        assertTrue(saved!!.summary.contains("Home Depot"))
    }

    // ---------------- editor ----------------

    /** event.invite: guests from the picker; Save asks whether to send updates and sends the invitation. */
    @Test fun editorAddsGuestsAndInvites() {
        val vm = h.vm { CalendarViewModel(h.g) }
        compose.setContent {
            MaterialTheme {
                EditorFor(vm, CalPane.Edit(null, tomorrow.atTime(10, 0)), emptyList(), null, null, 9, saving = false,
                    onClose = {}, onSave = { o, d, sc, rr, lv -> vm.save(o, d, sc, rr, live = lv) }, onDelete = { _, _ -> })
            }
        }
        compose.onAllNodes(hasSetTextAction())[0].performTextInput("Planning")
        compose.onNodeWithText("Add guests").performScrollTo().performClick()
        compose.onNodeWithTag("guest-search").performTextInput("dana@example.com")
        compose.onNodeWithText("Done").performClick()
        compose.onNodeWithText("1 guest · 1 awaiting").assertExists()
        compose.onNodeWithText("Save").performClick()
        compose.onNodeWithText("Send invitation to 1 new guest?").assertExists()
        compose.onNodeWithText("Send").performClick()
        val body = h.sent("POST", "/api/v1/calendar/events")
        assertEquals("dana@example.com", body["attendees"]!!.jsonArray[0].jsonObject["email"]!!.jsonPrimitive.content)
        assertTrue(body["send_updates"]!!.jsonPrimitive.boolean)
    }

    /** event.find_time: the slots come from find-a-time; "Use" sets the time. */
    @Test fun findTimeSheetPicks() {
        var picked: Pair<LocalDateTime, LocalDateTime>? = null
        val a = tomorrow.atTime(10, 0)
        compose.setContent {
            MaterialTheme {
                FindTimeSheet(30, zone, { FindTimeResult(listOf(TimeSlot(iso(a), iso(a.plusMinutes(30))), TimeSlot(iso(a.plusHours(3)), iso(a.plusHours(3).plusMinutes(30))))) },
                    guests = 2, onDismiss = {}) { s, e -> picked = s to e }
            }
        }
        compose.onNodeWithText("Guests: availability not shared").assertExists()
        compose.onAllNodesWithText("Use")[0].performClick()
        assertEquals(a to a.plusMinutes(30), picked)
    }

    // ---------------- changed elsewhere ----------------

    /** Keep both: their version stays and mine becomes a new event. */
    @Test fun conflictKeepBoth() {
        put(design)
        val mine = """{"summary":"Design review (moved)","start":"${iso(tomorrow.atTime(14, 0))}","end":"${iso(tomorrow.atTime(15, 0))}"}"""
        runBlocking {
            h.g.db.calendar().putConflict(CalConflictEntity("event|g1", "event", "g1", "PATCH", "calendar/events/g1", mine,
                MailrJson.encodeToString(CalendarEvent.serializer(), design), "Changed elsewhere", System.currentTimeMillis()))
        }
        val vm = h.vm { CalendarViewModel(h.g) }
        h.until("conflict") { vm.conflicts.value.isNotEmpty() }
        compose.setContent { MaterialTheme { ConflictPrompt(vm.conflicts.value.first(), vm) {} } }
        compose.onNodeWithText("Changed elsewhere").assertExists()
        compose.onNodeWithText("Design review (moved)").assertExists()
        compose.onNodeWithText("Keep both").performClick()
        assertEquals("Design review (moved)", h.sent("POST", "/api/v1/calendar/events")["summary"]!!.jsonPrimitive.content)
        h.until("conflict closed") { vm.conflicts.value.isEmpty() }
    }

    // ---------------- travel ----------------

    /** The travel banner: "Home" draws the grid in the home zone, and the choice is remembered for this phone zone. */
    @Test fun travelBannerSwitchesZone() {
        TimeZone.setDefault(TimeZone.getTimeZone("Europe/Lisbon"))
        val vm = h.vm { CalendarViewModel(h.g) }
        vm.prefs.setHomeZone("America/New_York")
        compose.setContent {
            MaterialTheme { TravelBanner(ZoneId.of("Europe/Lisbon"), ZoneId.of("America/New_York")) { vm.prefs.setTravel("Europe/Lisbon", it) } }
        }
        compose.onNodeWithText("Your phone is in Lisbon", substring = true).assertExists()
        compose.onNodeWithText("Home (New York)").performClick()
        h.until("home zone shown") { vm.displayZone.value == ZoneId.of("America/New_York") }
        assertEquals("home", vm.prefs.travel.value["Europe/Lisbon"])
        // 15:00 in Lisbon is 10:00 in New York (both on summer or both on winter time most of the year).
        val e = CalendarEvent("z1", "Call", ZonedDateTime.of(tomorrow.atTime(15, 0), ZoneId.of("Europe/Lisbon")).toOffsetDateTime().toString(),
            ZonedDateTime.of(tomorrow.atTime(16, 0), ZoneId.of("Europe/Lisbon")).toOffsetDateTime().toString())
        val ny = dayEvents(listOf(e), emptyList(), vm.displayZone.value).single()
        assertEquals(ZonedDateTime.of(tomorrow.atTime(15, 0), ZoneId.of("Europe/Lisbon")).withZoneSameInstant(ZoneId.of("America/New_York")).toLocalDateTime(), ny.start)
    }

    // ---------------- search ----------------

    /** event.search: results as Upcoming (offline: the phone's copy); a result opens and the query is remembered. */
    @Test fun searchFindsAndRemembers() {
        put(design)
        val vm = h.vm { CalendarViewModel(h.g) }
        var opened: CalendarEvent? = null
        compose.setContent { MaterialTheme { CalendarSearch(vm, emptyList(), zone, onOpen = { opened = it }, onClose = {}) } }
        compose.onNode(hasSetTextAction()).performTextInput("design")
        settle("results") { compose.onAllNodesWithText("Upcoming").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("Design review").performClick()
        assertNotNull(opened)
        assertEquals(listOf("design"), vm.prefs.recent.value)
    }

    /** event.duplicate from the floating toolbar: a new event with a new UID at the lifted time. */
    @Test fun duplicateCreatesACopy() {
        put(design)
        val vm = h.vm { CalendarViewModel(h.g) }
        vm.duplicate(design, tomorrow.atTime(13, 0), tomorrow.atTime(14, 0))
        val body = h.sent("POST", "/api/v1/calendar/events")
        assertEquals("Design review", body["summary"]!!.jsonPrimitive.content)
        assertTrue(body["uid"]!!.jsonPrimitive.content != "g1")
        assertEquals(tomorrow.atTime(13, 0).atZone(zone).toInstant(), OffsetDateTime.parse(body["start"]!!.jsonPrimitive.content).toInstant())
    }
}

/** The current value of a StateFlow inside a composition. */
@androidx.compose.runtime.Composable
private fun <T> kotlinx.coroutines.flow.StateFlow<T>.collectAsStateValue(): T = this.collectAsState().value
