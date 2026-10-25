// ACT-SURFACES: android.editor android.calendar
package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.time.AppClock
import android.app.Application
import android.content.Intent
import android.net.Uri
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.collectAsState
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.hasText
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.unit.dp
import io.github.murtazanasir.mailr.data.api.Attendee
import io.github.murtazanasir.mailr.data.api.BusyInterval
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.FindTimeResult
import io.github.murtazanasir.mailr.data.api.FreeBusy
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.api.TimeSlot
import io.github.murtazanasir.mailr.data.db.CalCollectionEntity
import io.github.murtazanasir.mailr.data.db.CalEventEntity
import io.github.murtazanasir.mailr.data.db.CalTaskEntity
import io.github.murtazanasir.mailr.ui.Destination
import io.github.murtazanasir.mailr.ui.appLink
import io.github.murtazanasir.mailr.ui.calendar.CalPane
import io.github.murtazanasir.mailr.ui.calendar.CalView
import io.github.murtazanasir.mailr.ui.calendar.CalendarDrawer
import io.github.murtazanasir.mailr.ui.calendar.CalendarViewModel
import io.github.murtazanasir.mailr.ui.calendar.DeletedElsewhere
import io.github.murtazanasir.mailr.ui.calendar.EditorFor
import io.github.murtazanasir.mailr.ui.calendar.EventDetail
import io.github.murtazanasir.mailr.ui.calendar.EventEditorState
import io.github.murtazanasir.mailr.ui.calendar.FindTimeSheet
import io.github.murtazanasir.mailr.ui.calendar.GridActions
import io.github.murtazanasir.mailr.ui.calendar.MonthView
import io.github.murtazanasir.mailr.ui.calendar.TaskChip
import io.github.murtazanasir.mailr.ui.calendar.TimeGrid
import io.github.murtazanasir.mailr.ui.calendar.calViewOf
import io.github.murtazanasir.mailr.ui.calendar.dayEvents
import io.github.murtazanasir.mailr.ui.parseDeepLink
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.time.DayOfWeek
import java.time.Duration
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

/**
 * The calendar follow-ups on Android: drags between the grid and the all-day strip, Month view
 * drags that keep the time, task chips dragged to a new due, the detail's Move to calendar,
 * Email guests, Ask assistant and RSVP scope, "Restore as new", per-calendar default
 * reminders, the default calendar, colour clear, find a time with my busy time, the editor in
 * the display zone, and app links.
 *
 * Covers: event.edit, event.reschedule, task.reschedule, event.move_calendar, event.email_guests, event.rsvp,
 * event.create, calendar.default_reminders, calendar.set_default, calendar.color, event.set_color, event.find_time,
 * calendar.create, calendar.rename, calendar.delete, event.uninvite, event.add_video, event.join, event.open_email,
 * task.complete.
 */
@OptIn(androidx.compose.ui.test.ExperimentalTestApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class CalendarFollowUpTest {
    @get:Rule val compose = createComposeRule()
    private val h = CalendarHarness()
    private val zone: ZoneId get() = ZoneId.systemDefault()
    private val tomorrow: LocalDate = AppClock.today().plusDays(1)

    @Before fun setUp() { h.start() }
    @After fun tearDown() = h.stop()

    private fun iso(t: LocalDateTime) = t.atZone(zone).toOffsetDateTime().toString()
    private fun instant(s: String) = OffsetDateTime.parse(s).toInstant()

    private fun put(vararg es: CalendarEvent) = runBlocking {
        h.g.db.calendar().putEvents(es.map { e ->
            val (s, en) = if (e.allDay) LocalDate.parse(e.start).atStartOfDay(zone).toInstant().toEpochMilli() to
                LocalDate.parse(e.end!!).atStartOfDay(zone).toInstant().toEpochMilli()
            else instant(e.start).toEpochMilli() to instant(e.end!!).toEpochMilli()
            CalEventEntity(e.key, e.uid, s, en, e.allDay, e.calendar, MailrJson.encodeToString(CalendarEvent.serializer(), e))
        })
    }

    private fun collections() = runBlocking {
        val cols = listOf(CalendarCollection("/cal/work/", "Work", "#039be5", events = true, calendarId = 7),
            CalendarCollection("/cal/home/", "Home", "#0b8043", events = true, calendarId = 8))
        h.g.db.calendar().putCollections(cols.mapIndexed { i, c ->
            CalCollectionEntity(c.url, c.calendarId, c.name, c.color, true, false, false, i, null, MailrJson.encodeToString(CalendarCollection.serializer(), c))
        })
        h.g.calendar.loadCached()
    }

    private val design get() = CalendarEvent("g1", "Design review", iso(tomorrow.atTime(10, 0)), iso(tomorrow.atTime(11, 0)), calendar = "/cal/work/")

    private fun actions(vm: CalendarViewModel) = GridActions(
        createRange = { _, _ -> }, move = vm::requestTimes, duplicate = vm::duplicate, delete = vm::requestDelete, reschedule = {},
        readOnly = vm::readOnly, notify = {}, completeTask = vm::completeTask,
        toAllDay = vm::requestAllDay, toTimed = vm::requestTimed,
        rescheduleTask = { t, at, d -> vm.requestTaskDue(t, vm.dueAt(at, d)) }, moveDays = vm::requestMoveDays)

    private fun grid(vm: CalendarViewModel, events: List<CalendarEvent>, tasks: List<TaskChip> = emptyList()) {
        compose.setContent {
            MaterialTheme {
                TimeGrid(listOf(tomorrow), dayEvents(events, emptyList()), AppClock.millis(), null, {}, {}, null,
                    tasks = tasks, actions = actions(vm))
            }
        }
        compose.mainClock.advanceTimeBy(500)
        compose.waitForIdle()
    }

    // ---------------- dragging: timed ↔ all-day, task chips, Month view ----------------

    /** event.edit: a timed event dragged up into the all-day strip becomes all-day on that day. */
    @Test fun dragIntoTheStripMakesItAllDay() {
        put(design)
        val vm = h.vm { CalendarViewModel(h.g) }
        grid(vm, listOf(design))
        val hour = with(compose.density) { 56.dp.toPx() }
        compose.onNode(hasContentDescription("Design review", substring = true)).performTouchInput {
            down(center)
            advanceEventTime(800)
            repeat(20) { moveBy(Offset(0f, -hour * 4 / 20)); advanceEventTime(16) }
            up()
        }
        val body = h.sent("PATCH", "/api/v1/calendar/events/g1")
        assertTrue(body["all_day"]!!.jsonPrimitive.boolean)
        assertEquals(tomorrow.toString(), body["start"]!!.jsonPrimitive.content)
        assertEquals(tomorrow.plusDays(1).toString(), body["end"]!!.jsonPrimitive.content)
    }

    /** event.edit: an all-day event dragged from the strip into the grid becomes 1 hour at the drop time. */
    @Test fun dragFromTheStripMakesItTimed() {
        val offsite = CalendarEvent("a1", "Offsite", tomorrow.toString(), tomorrow.plusDays(1).toString(), allDay = true)
        put(offsite)
        val vm = h.vm { CalendarViewModel(h.g) }
        grid(vm, listOf(offsite))
        val hour = with(compose.density) { 56.dp.toPx() }
        compose.onNodeWithText("Offsite").performTouchInput {
            down(center)
            advanceEventTime(800)
            repeat(20) { moveBy(Offset(0f, hour * 2 / 20)); advanceEventTime(16) }
            up()
        }
        val body = h.sent("PATCH", "/api/v1/calendar/events/a1")
        assertFalse(body["all_day"]!!.jsonPrimitive.boolean)
        assertEquals(Duration.ofHours(1), Duration.between(instant(body["start"]!!.jsonPrimitive.content), instant(body["end"]!!.jsonPrimitive.content)))
    }

    /** task.reschedule: a task chip dragged one hour down gets that due. */
    @Test fun taskChipDragReschedules() {
        val due = tomorrow.atTime(10, 0)
        val t = TaskItem("t2", "Call the bank", iso(due))
        runBlocking {
            h.g.db.calendar().putTasks(listOf(CalTaskEntity(t.uid, null, null, false, due.atZone(zone).toInstant().toEpochMilli(),
                MailrJson.encodeToString(TaskItem.serializer(), t))))
        }
        val vm = h.vm { CalendarViewModel(h.g) }
        grid(vm, emptyList(), listOf(TaskChip(t, due, false)))
        val hour = with(compose.density) { 56.dp.toPx() }
        compose.onNode(hasContentDescription("Task Call the bank", substring = true)).performTouchInput {
            down(center)
            advanceEventTime(800)
            repeat(10) { moveBy(Offset(0f, hour / 10)); advanceEventTime(16) }
            up()
        }
        val body = h.sent("PATCH", "/api/v1/tasks/t2")
        assertEquals(due.plusHours(1).atZone(zone).toInstant(), instant(body["due"]!!.jsonPrimitive.content))
    }

    /** task.complete: a tap on a task chip completes the task. */
    @Test fun taskChipTapCompletes() {
        val due = tomorrow.atTime(10, 0)
        val t = TaskItem("t3", "Pay the invoice", iso(due))
        runBlocking {
            h.g.db.calendar().putTasks(listOf(CalTaskEntity(t.uid, null, null, false, due.atZone(zone).toInstant().toEpochMilli(),
                MailrJson.encodeToString(TaskItem.serializer(), t))))
        }
        val vm = h.vm { CalendarViewModel(h.g) }
        grid(vm, emptyList(), listOf(TaskChip(t, due, false)))
        compose.onNode(hasContentDescription("Task Pay the invoice", substring = true)).performClick()
        assertTrue(h.sent("PATCH", "/api/v1/tasks/t3")["completed"]!!.jsonPrimitive.boolean)
    }

    /** event.reschedule in Month view: a drop on the next day keeps the time of day. */
    @Test fun monthDragKeepsTheTime() {
        put(design)
        val vm = h.vm { CalendarViewModel(h.g) }
        compose.setContent {
            MaterialTheme { MonthView(tomorrow, dayEvents(listOf(design), emptyList()), false, {}, {}, actions = actions(vm)) }
        }
        val label = tomorrow.format(DateTimeFormatter.ofPattern("EEEE, MMMM d", Locale.getDefault())) + ", 1 event"
        compose.onNode(hasContentDescription(label)).performTouchInput {
            down(center)
            advanceEventTime(800)
            repeat(10) { moveBy(Offset(width / 10f, 0f)); advanceEventTime(16) }
            up()
        }
        val body = h.sent("PATCH", "/api/v1/calendar/events/g1")
        assertEquals(tomorrow.plusDays(1).atTime(10, 0).atZone(zone).toInstant(), instant(body["start"]!!.jsonPrimitive.content))
        assertEquals(tomorrow.plusDays(1).atTime(11, 0).atZone(zone).toInstant(), instant(body["end"]!!.jsonPrimitive.content))
    }

    // ---------------- the event detail ----------------

    /** event.move_calendar, event.email_guests and Ask assistant from the detail's actions. */
    @Test fun detailMovesEmailsAndAsks() {
        collections()
        val e = design.copy(attendeeList = listOf(Attendee("dana@example.com", "Dana", "ACCEPTED"), Attendee("lee@example.com", "Lee"),
            Attendee("me@example.com", "Me", "ACCEPTED")), organizer = "me@example.com", isOrganizer = true)
        put(e)
        val vm = h.vm { CalendarViewModel(h.g) }
        h.until("collections") { vm.collections.value.size == 2 }
        var which: String? = null
        var asked = false
        compose.setContent {
            MaterialTheme {
                EventDetail(e, null, vm.collections.collectAsState().value, 9, null, {}, {}, {},
                    onMove = { vm.moveToCalendar(e, it) }, onEmailGuests = { which = it }, onAskAssistant = { asked = true })
            }
        }
        compose.onNodeWithText("Move to calendar").performScrollTo().performClick()
        compose.onNode(hasText("Home")).performClick()
        assertEquals("/cal/home/", h.sent("POST", "/api/v1/calendar/events/g1/move")["calendar"]!!.jsonPrimitive.content)
        h.until("moved here") { runBlocking { h.g.calendar.eventInstance(e.key)?.calendar } == "/cal/home/" }

        compose.onNodeWithText("Email guests").performScrollTo().performClick()
        compose.onNodeWithText("Guests who haven't answered").performClick()
        assertEquals("awaiting", which)
        assertEquals(listOf("lee@example.com"), vm.emailGuests(e, "awaiting").to)
        assertEquals(listOf("dana@example.com", "lee@example.com"), vm.emailGuests(e, "all").to)
        assertEquals("Design review", vm.emailGuests(e, "all").subject)

        compose.onNodeWithText("Ask Ambrose").performScrollTo().performClick()
        assertTrue(asked)
    }

    /** event.rsvp on a series: the series question first; the answer shows at once and goes out with the scope and occurrence. */
    @Test fun rsvpOnASeriesAsksTheScope() {
        val inv = CalendarEvent("r1", "Weekly 1:1", iso(tomorrow.atTime(9, 0)), iso(tomorrow.atTime(9, 30)), recurring = true,
            rrule = "FREQ=WEEKLY", organizer = "alice@example.com", myStatus = "NEEDS-ACTION")
        put(inv)
        val vm = h.vm { CalendarViewModel(h.g) }
        compose.setContent {
            MaterialTheme {
                EventDetail(inv, null, emptyList(), 9, null, {}, {}, { vm.rsvp(inv, it) },
                    onRsvpScoped = { r, note, sc -> vm.rsvp(inv, r, note, sc) })
            }
        }
        compose.onNodeWithText("Yes").performClick()
        compose.onNodeWithText("Answer for").assertExists()
        compose.onNodeWithText("OK").performClick()
        val body = h.sent("POST", "/api/v1/calendar/events/r1/rsvp")
        assertEquals("accepted", body["response"]!!.jsonPrimitive.content)
        assertEquals("this", body["scope"]!!.jsonPrimitive.content)
        assertEquals(inv.occurrence, body["occurrence"]!!.jsonPrimitive.content)
        assertEquals("ACCEPTED", runBlocking { h.g.calendar.eventInstance(inv.key)?.myStatus })
    }

    /** event.create: "Restore as new" brings an event deleted elsewhere back with a new UID. */
    @Test fun deletedElsewhereRestoresAsNew() {
        val vm = h.vm { CalendarViewModel(h.g) }
        compose.setContent { MaterialTheme { DeletedElsewhere(design, onRestore = vm::restoreAsNew, onClose = null) } }
        compose.onNodeWithText("Event deleted elsewhere").assertExists()
        compose.onNodeWithText("Restore as new").performClick()
        val body = h.sent("POST", "/api/v1/calendar/events")
        assertEquals("Design review", body["summary"]!!.jsonPrimitive.content)
        assertNotEquals("g1", body["uid"]!!.jsonPrimitive.content)
    }

    // ---------------- the drawer ----------------

    /** calendar.default_reminders, calendar.set_default and calendar.color (back to the server's colour). */
    @Test fun drawerDefaultsRemindersAndColour() {
        collections()
        val vm = h.vm { CalendarViewModel(h.g) }
        h.until("accounts") { vm.accounts.value.flatMap { it.collections }.size == 2 }
        compose.setContent {
            MaterialTheme {
                CalendarDrawer(vm, AppClock.today(), AppClock.today() to AppClock.today().plusDays(1), emptySet(), vm.accounts.collectAsState().value,
                    "/cal/work/", onGo = {})
            }
        }
        compose.onNodeWithContentDescription("More for Home").performClick()
        compose.onNodeWithText("Set as default").performClick()
        assertEquals("/cal/home/", h.sent("PUT", "/api/v1/calendar/defaults")["calendar"]!!.jsonPrimitive.content)

        compose.onNodeWithContentDescription("More for Work").performClick()
        compose.onNodeWithText("Default reminders").performClick()
        compose.onNodeWithText("Default reminders · Work").assertExists()
        compose.onNodeWithText("Cancel").performClick()
        val work = vm.collections.value.first { it.url == "/cal/work/" }
        vm.setDefaultReminders(work, listOf(10, 60), listOf(-540))
        h.until("default reminders sent") {
            h.requests.any { it.first.method == "PATCH" && it.first.path == "/api/v1/calendar/collections/7" && "default_reminders" in it.second &&
                "timed" in it.second }
        }
        val body = MailrJson.parseToJsonElement(h.requests.last { it.first.path == "/api/v1/calendar/collections/7" && "timed" in it.second }.second).jsonObject
        assertEquals(listOf(10, 60), body["default_reminders"]!!.jsonObject["timed"]!!.jsonArray.map { it.jsonPrimitive.content.toInt() })
        h.until("kept here") { vm.defaultRemindersOf("/cal/work/", true) == listOf(-540) }

        vm.colorCalendar(work, "")
        h.until("colour cleared") {
            h.requests.any { it.first.method == "PATCH" && it.first.path == "/api/v1/calendar/collections/7" && "\"color\":\"\"" in it.second }
        }
    }

    /** event.join and event.open_email on the event detail: Join opens the meeting link, "Open the email" the source. */
    @Test fun detailJoinsAndOpensTheEmail() {
        val e = design.copy(description = "Join: https://meet.google.com/abc-defg-hij", conversationId = 3066)
        var opened = false
        compose.setContent { MaterialTheme { EventDetail(e, null, emptyList(), 9, null, {}, {}, {}, onOpenEmail = { opened = true }) } }
        compose.onNodeWithText("Join").performScrollTo().performClick()
        val open = h.nextOpened()!!
        assertEquals(Intent.ACTION_VIEW, open.action)
        assertEquals("https://meet.google.com/abc-defg-hij", open.dataString)
        compose.onNodeWithText("Open the email").performScrollTo().performClick()
        assertTrue(opened)
    }

    /** event.set_color: "Calendar colour" sends an empty COLOR and the event shows its calendar's colour again. */
    @Test fun eventColourClears() {
        val coloured = design.copy(color = "#d50000")
        put(coloured)
        val vm = h.vm { CalendarViewModel(h.g) }
        vm.setEventColor(coloured, null)
        assertEquals("", h.sent("PATCH", "/api/v1/calendar/events/g1")["color"]!!.jsonPrimitive.content)
        h.until("no own colour") { runBlocking { h.g.calendar.eventInstance(coloured.key) }?.color == null }
    }

    /** calendar.create, calendar.rename, calendar.delete from the drawer's view model (online writes). */
    @Test fun calendarsCreateRenameDelete() {
        collections()
        val vm = h.vm { CalendarViewModel(h.g) }
        h.until("collections") { vm.collections.value.size == 2 }
        vm.createCalendar("Gym", "#d50000", null)
        val created = h.sent("POST", "/api/v1/calendar/collections")
        assertEquals("Gym", created["name"]!!.jsonPrimitive.content)
        assertEquals("events", created["kind"]!!.jsonPrimitive.content)
        val work = vm.collections.value.first { it.url == "/cal/work/" }
        vm.renameCalendar(work, "Office")
        assertEquals("Office", h.sent("PATCH", "/api/v1/calendar/collections/7")["name"]!!.jsonPrimitive.content)
        vm.deleteCalendar(vm.collections.value.first { it.url == "/cal/home/" }, "Home")
        h.until("deleted") { h.requests.any { it.first.method == "DELETE" && it.first.path == "/api/v1/calendar/collections/8?confirm=Home" } }
    }

    // ---------------- the editor ----------------

    /** event.uninvite and event.add_video: a removed guest and a pasted meeting link go out with the change. */
    @Test fun editorRemovesAGuestAndAddsAVideoLink() {
        val e = design.copy(organizer = "me@example.com", isOrganizer = true,
            attendeeList = listOf(Attendee("dana@example.com", "Dana", "ACCEPTED"), Attendee("lee@example.com", "Lee")))
        put(e)
        val vm = h.vm { CalendarViewModel(h.g) }
        val s = EventEditorState(e, null, tomorrow.atTime(10, 0), "/cal/work/", null, e.uid)
        s.guests.removeAll { it.email == "lee@example.com" }
        s.video = "https://meet.example.com/abc"
        s.sendUpdates = false
        vm.save(e, s.draft(), "all", false)
        val body = h.sent("PATCH", "/api/v1/calendar/events/g1")
        assertEquals(listOf("dana@example.com"), body["attendees"]!!.jsonArray.map { it.jsonObject["email"]!!.jsonPrimitive.content })
        assertEquals("https://meet.example.com/abc", body["url"]!!.jsonPrimitive.content)
    }

    /** event.find_time: my busy time from GET /calendar/freebusy is drawn on each day of the range. */
    @Test fun findTimeDrawsMyBusyTime() {
        var day = AppClock.today().plusDays(1)
        while (day.dayOfWeek == DayOfWeek.SATURDAY || day.dayOfWeek == DayOfWeek.SUNDAY) day = day.plusDays(1)
        val a = day.atTime(14, 0)
        compose.setContent {
            MaterialTheme {
                FindTimeSheet(30, zone, { FindTimeResult(listOf(TimeSlot(iso(a), iso(a.plusMinutes(30))))) }, guests = 0, onDismiss = {},
                    freebusy = { _, _ -> FreeBusy(busy = listOf(BusyInterval(iso(day.atTime(10, 0)), iso(day.atTime(11, 0))))) }) { _, _ -> }
            }
        }
        compose.onNode(hasContentDescription("1 busy, 1 free slot", substring = true)).assertExists()
        compose.onNodeWithText("Free slot · 8:00–18:00", substring = true).assertExists()
    }

    /** event.move_calendar from the editor: only the calendar changed, so only the move is sent. */
    @Test fun editorMovesTheSeriesToAnotherCalendar() {
        collections()
        put(design)
        val vm = h.vm { CalendarViewModel(h.g) }
        h.until("collections") { vm.collections.value.size == 2 }
        compose.setContent {
            MaterialTheme {
                EditorFor(vm, CalPane.Edit(design, tomorrow.atTime(10, 0)), vm.collections.collectAsState().value, "/cal/work/", null, 9,
                    saving = false, onClose = {}, onSave = { o, d, sc, rr, lv -> vm.save(o, d, sc, rr, live = lv) }, onDelete = { _, _ -> })
            }
        }
        compose.onNodeWithText("Work").performScrollTo().performClick()
        compose.onNode(hasText("Home")).performClick()
        compose.onNodeWithText("Save").performClick()
        assertEquals("/cal/home/", h.sent("POST", "/api/v1/calendar/events/g1/move")["calendar"]!!.jsonPrimitive.content)
        assertTrue(h.requests.none { it.first.method == "PATCH" })
    }

    /** The editor shows the times in the display zone and keeps the event's own zone unless another is picked. */
    @Test fun editorEditsInTheDisplayZone() {
        val london = ZoneId.of("Europe/London")
        val chicago = ZoneId.of("America/Chicago")
        val start = tomorrow.atTime(15, 0).atZone(london)
        val e = CalendarEvent("z1", "Call", start.toOffsetDateTime().toString(), start.plusHours(1).toOffsetDateTime().toString(), tz = "Europe/London")
        val s = EventEditorState(e, null, AppClock.localNow(), null, null, e.uid, displayZone = chicago)
        assertEquals(start.withZoneSameInstant(chicago).toLocalTime(), s.startTime)
        assertEquals("Europe/London", s.draft().tz)
        assertEquals(start.toInstant(), instant(s.draft().start))
        s.zone = ZoneId.of("Asia/Tokyo")
        assertEquals("Asia/Tokyo", s.draft().tz)
    }

    /** calendar.default_reminders: a new event takes its calendar's defaults until the reminders are changed by hand. */
    @Test fun newEventsTakeTheCalendarsReminders() {
        val defaults = { url: String?, allDay: Boolean -> if (url == "/cal/home/") (if (allDay) listOf(-540) else listOf(30)) else null }
        val s = EventEditorState(null, null, tomorrow.atTime(10, 0), "/cal/work/", 10, "n1", defaultsFor = defaults)
        assertEquals(listOf(10), s.reminders)
        s.pickCalendar("/cal/home/")
        assertEquals(listOf(30), s.reminders)
        s.toggleAllDay(true)
        assertEquals(listOf(-540), s.reminders)
        s.reminders = listOf(5); s.remindersTouched = true
        s.pickCalendar("/cal/work/")
        assertEquals(listOf(5), s.reminders)
    }

    // ---------------- app links ----------------

    /** https://<server>/calendar/… and /tasks/… (and mailr://) open the right place; another host does not. */
    @Test fun appLinksRoute() {
        assertEquals(Destination.Event("u1", "2026-10-02T15:00:00+00:00"),
            appLink(listOf("calendar", "event", "u1", "2026-10-02T15:00:00+00:00")))
        assertEquals(Destination.Event(null, null, "week", "2026-10-01"), appLink(listOf("calendar", "week", "2026-10-01")))
        assertEquals(Destination.Task("t9", "l/12"), appLink(listOf("tasks", "l", "12", "task", "t9")))
        assertEquals(Destination.Task(null, "today"), appLink(listOf("tasks", "today")))
        assertEquals(Destination.Task("t9", null), appLink(listOf("tasks", "task", "t9")))
        assertEquals(CalView.THREE_DAY, calViewOf("ndays"))
        assertEquals(CalView.AGENDA, calViewOf("schedule"))
        val link = Intent(Intent.ACTION_VIEW, Uri.parse("https://mail.example.com/calendar/event/u1/2026-10-02"))
        assertEquals(Destination.Event("u1", "2026-10-02"), parseDeepLink(link, "mail.example.com"))
        assertNull(parseDeepLink(link, "other.example.com"))
        assertEquals(Destination.Task(null, "t/home"), parseDeepLink(Intent(Intent.ACTION_VIEW, Uri.parse("mailr://tasks/t/home"))))
        assertEquals(Destination.Event(null, null, "month", null), parseDeepLink(Intent(Intent.ACTION_VIEW, Uri.parse("mailr://calendar/month"))))
    }
}
