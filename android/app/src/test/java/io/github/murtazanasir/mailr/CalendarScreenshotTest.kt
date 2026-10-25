package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.time.AppClock
import android.app.Application
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.widthIn
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.ui.Modifier
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.unit.dp
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.Attendee
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.FindTimeResult
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.api.TimeSlot
import io.github.murtazanasir.mailr.data.cal.CalConflict
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.ui.calendar.AccountErrorBanner
import io.github.murtazanasir.mailr.ui.calendar.AccountGroup
import io.github.murtazanasir.mailr.ui.calendar.AllHiddenBanner
import io.github.murtazanasir.mailr.ui.calendar.AttendeeEditor
import io.github.murtazanasir.mailr.ui.calendar.CalendarDrawer
import io.github.murtazanasir.mailr.ui.calendar.CalendarSearch
import io.github.murtazanasir.mailr.ui.calendar.CalendarViewModel
import io.github.murtazanasir.mailr.ui.calendar.ConflictSheet
import io.github.murtazanasir.mailr.ui.calendar.EventDetail
import io.github.murtazanasir.mailr.ui.calendar.EventEditor
import io.github.murtazanasir.mailr.ui.calendar.EventEditorState
import io.github.murtazanasir.mailr.ui.calendar.FindTimeSheet
import io.github.murtazanasir.mailr.ui.calendar.DeletedElsewhere
import io.github.murtazanasir.mailr.data.api.FreeBusy
import io.github.murtazanasir.mailr.data.api.BusyInterval
import io.github.murtazanasir.mailr.ui.calendar.Guest
import io.github.murtazanasir.mailr.ui.calendar.MonthPickerDialog
import io.github.murtazanasir.mailr.ui.calendar.OfflineBanner
import io.github.murtazanasir.mailr.ui.calendar.QuickCreateSheet
import io.github.murtazanasir.mailr.ui.calendar.ScopeSheet
import io.github.murtazanasir.mailr.ui.calendar.SendUpdatesSheet
import io.github.murtazanasir.mailr.ui.calendar.TaskChip
import io.github.murtazanasir.mailr.ui.calendar.TimeGrid
import io.github.murtazanasir.mailr.ui.calendar.TravelBanner
import io.github.murtazanasir.mailr.ui.calendar.dayEvents
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.ExperimentalRoborazziApi
import com.github.takahirom.roborazzi.captureRoboImage
import com.github.takahirom.roborazzi.captureScreenRoboImage
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId

/** The Calendar app's new screens (android.calendar), light and dark, on a phone. */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
@OptIn(ExperimentalRoborazziApi::class)
class CalendarScreenshotTest(private val dark: Boolean) {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun params(): List<Array<Any>> = listOf(arrayOf<Any>(false), arrayOf<Any>(true))
    }

    @get:Rule val compose = createComposeRule()

    private val zone = ZoneId.systemDefault()
    private val day: LocalDate = AppClock.today().with(java.time.DayOfWeek.MONDAY).plusWeeks(1)
    private fun at(d: Int, h: Int, m: Int = 0) = day.plusDays(d.toLong()).atTime(h, m).atZone(zone).toOffsetDateTime().toString()
    private val noon = day.atTime(12, 20).atZone(zone).toInstant().toEpochMilli()

    private val events = listOf(
        CalendarEvent("a", "CHEM 101 lecture", at(0, 10), at(0, 11, 15), location = "Hollis Hall 208", color = "#1a73e8", recurring = true),
        CalendarEvent("b", "Review with Alice", at(0, 10, 30), at(0, 11, 30), color = "#0b8043", organizer = "alice@example.com",
            myStatus = "NEEDS-ACTION", attendeeList = listOf(Attendee("alice@example.com", "Alice Chen", "ACCEPTED"))),
        CalendarEvent("c", "Office hours", at(0, 14), at(0, 16), color = "#8e24aa"),
        CalendarEvent("d", "Maybe lunch", at(1, 12), at(1, 13), color = "#f4511e", myStatus = "TENTATIVE", organizer = "dana@example.com"),
        CalendarEvent("e", "Night flight", at(1, 21), at(2, 5), color = "#3f51b5"),
        CalendarEvent("f", "Standup", at(2, 9, 15), at(2, 9, 30), color = "#039be5"),
        CalendarEvent("g", "Design sprint", at(2, 13), at(2, 15), color = "#e67c73"),
        CalendarEvent("h", "1:1 Dana", at(2, 13, 30), at(2, 14), color = "#33b679"),
        CalendarEvent("i", "Vendor call", at(2, 13, 30), at(2, 14, 30), color = "#795548"),
        CalendarEvent("j", "PADS nominations due", day.toString(), day.plusDays(2).toString(), allDay = true, color = "#e37400"),
        CalendarEvent("k", "Budget sync (offline edit)", at(1, 16), at(1, 17), color = "#616161"),
    )
    private val task = TaskChip(TaskItem("t1", "Send Q3 numbers", at(0, 16, 30)), day.atTime(16, 30), false)

    private fun theme(content: @Composable () -> Unit) {
        RuntimeEnvironment.setQualifiers("w411dp-h891dp-port-${if (dark) "night" else "notnight"}-xxhdpi")
        compose.setContent {
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.surface) { content() }
            }
        }
        compose.mainClock.advanceTimeBy(1000)
        compose.waitForIdle()
    }

    private fun name(n: String) = "screenshots/calendar-${if (dark) "dark" else "light"}-$n.png"
    private fun shot(n: String, content: @Composable () -> Unit) { theme(content); compose.onRoot().captureRoboImage(name(n)) }
    private fun screen(n: String, content: @Composable () -> Unit) { theme(content); captureScreenRoboImage(name(n)) }

    private val vm by lazy { graphs.vm { CalendarViewModel(graphs.graph()) } }

    @Test fun threeDay() = shot("3day") {
        TimeGrid((0L until 3L).map { day.plusDays(it) }, dayEvents(events, emptyList()), noon, "c", {}, {}, {},
            tasks = listOf(task), pending = setOf(events.last().key))
    }

    @Test fun workWeek() = shot("workweek") {
        TimeGrid((0L until 5L).map { day.plusDays(it) }, dayEvents(events, emptyList()), noon, null, {}, {}, {}, hourHeight = 44.dp)
    }

    @Test fun skeleton() = shot("skeleton") {
        TimeGrid((0L until 3L).map { day.plusDays(it) }, emptyList(), noon, null, {}, {}, null, skeleton = true)
    }

    @Test fun drawer() = shot("drawer") {
        val cols = listOf(CalendarCollection("/w/", "Work", "#039be5", events = true, calendarId = 1, connectorId = 1),
            CalendarCollection("/p/", "Personal", "#0b8043", events = true, calendarId = 2, connectorId = 1, deviceHidden = true),
            CalendarCollection("/h/", "US holidays", "#d50000", events = true, calendarId = 3, connectorId = 2, readOnly = true))
        ModalDrawerSheet(Modifier.widthIn(max = 340.dp)) {
            CalendarDrawer(vm, day, day to day.plusDays(3), setOf(day, day.plusDays(1), day.plusDays(4)),
                listOf(AccountGroup(1, "Fastmail", null, cols.take(2)), AccountGroup(2, "Holidays", "Sync failed: 401 Unauthorized", cols.drop(2))),
                "/w/", onGo = {})
        }
    }

    @Test fun banners() = shot("banners") {
        Column {
            TravelBanner(ZoneId.of("Europe/Lisbon"), ZoneId.of("America/New_York")) {}
            OfflineBanner(3)
            AccountErrorBanner("Fastmail", "Sign-in failed. Check the password.", authFailed = true) {}
            AllHiddenBanner {}
        }
    }

    @Test fun quickCreate() = screen("quick-create") {
        QuickCreateSheet("Lunch with Dana tomorrow 12:30 at Café Luna", null, false,
            listOf(CalendarCollection("/w/", "Work", "#039be5", events = true)), "/w/", 10, 9, listOf("Café Luna"), zone,
            conflict = { _, _ -> "Overlaps Team sync 12:00–1:00 PM" }, people = { emptyList() }, newUid = { "x" }, onText = {},
            onDismiss = {}, onSave = {}, onMore = {})
    }

    @Test fun detail() = shot("detail") {
        val e = CalendarEvent("r", "Quarterly planning", at(0, 15), at(0, 16), location = "Room 204 · https://meet.google.com/abc-defg-hij",
            organizer = "alice@example.com", organizerName = "Alice Chen", myStatus = "TENTATIVE", recurring = true, rrule = "FREQ=WEEKLY;BYDAY=TU",
            reminders = listOf(10, 60), description = "Agenda: Q4 targets, hiring, the offsite.", conversationId = 12,
            attendeeList = listOf(Attendee("alice@example.com", "Alice Chen", "ACCEPTED"), Attendee("dana@example.com", "Dana", "TENTATIVE"),
                Attendee("lee@example.com", "Lee", "NEEDS-ACTION"), Attendee("me@example.com", "Me", "TENTATIVE")))
        val cols = listOf(CalendarCollection("/w/", "Work", "#039be5", events = true), CalendarCollection("/h/", "Home", "#0b8043", events = true))
        EventDetail(e.copy(calendar = "/w/"), null, cols, 9, {}, {}, {}, {}, onOpenEmail = {}, onRsvpNote = { _, _ -> }, onReminders = {},
            onDuplicate = {}, onColor = {}, onCopyLink = {}, onRsvpScoped = { _, _, _ -> }, onMove = {}, onEmailGuests = {}, onAskAssistant = {})
    }

    /** An open detail whose event was deleted on another device. */
    @Test fun deletedElsewhere() = shot("deleted-elsewhere") {
        DeletedElsewhere(CalendarEvent("r", "Quarterly planning", at(0, 15), at(0, 16)), onRestore = {}, onClose = {})
    }

    @Test fun editor() = shot("editor") {
        val s = EventEditorState(null, null, day.atTime(10, 0), null, 10, "uid-1").apply {
            title = "Design review"
            guests += Guest("dana@example.com", "Dana Kim", "ACCEPTED")
            guests += Guest("lee@example.com", "", "NEEDS-ACTION", optional = true)
            color = "#33b679"
            video = "https://meet.google.com/abc-defg-hij"
        }
        EventEditor(s, emptyList(), 9, false, {}, {}, null, people = { emptyList() }, findTime = { FindTimeResult() })
    }

    /** A new event shows the account's default reminder (/reminders/settings, here 1 day before) as a chip. */
    @Test fun editorDefaultChips() = shot("editor-default-chips") {
        val s = EventEditorState(null, null, day.atTime(15, 0), null, 10, "uid-2",
            template = CalendarEvent("uid-2", "Proposal discussion", at(0, 15), at(0, 16)),
            userDefaults = { allDay -> if (allDay) listOf(900) else listOf(1440) })
        EventEditor(s, emptyList(), 9, false, {}, {}, null, people = { emptyList() }, findTime = { FindTimeResult() })
    }

    /** An all-day event starts with the all-day default ("1 day before at 9:00"). */
    @Test fun editorDefaultChipsAllDay() = shot("editor-default-chips-allday") {
        val s = EventEditorState(null, null, day.atTime(9, 0), null, 10, "uid-3",
            template = CalendarEvent("uid-3", "Offsite", day.toString(), day.plusDays(1).toString(), allDay = true),
            userDefaults = { allDay -> if (allDay) listOf(900) else listOf(1440) })
        EventEditor(s, emptyList(), 9, false, {}, {}, null, people = { emptyList() }, findTime = { FindTimeResult() })
    }

    @Test fun guests() = screen("guests") {
        val list = mutableStateListOf(Guest("dana@example.com", "Dana Kim", "ACCEPTED"), Guest("lee@example.com", "", "DECLINED"),
            Guest("sam@example.com", "Sam Ortiz", "NEEDS-ACTION", optional = true))
        AttendeeEditor(list, { emptyList() }) {}
    }

    @Test fun findTime() = screen("find-time") {
        val a = day.atTime(10, 0)
        fun iso(t: LocalDateTime) = t.atZone(zone).toOffsetDateTime().toString()
        FindTimeSheet(30, zone, { FindTimeResult(listOf(TimeSlot(iso(a), iso(a.plusMinutes(30))), TimeSlot(iso(a.plusHours(4)), iso(a.plusHours(4).plusMinutes(30))),
            TimeSlot(iso(a.plusDays(1).plusHours(1)), iso(a.plusDays(1).plusHours(1).plusMinutes(30))))) }, 2, {},
            freebusy = { _, _ -> FreeBusy(busy = listOf(BusyInterval(iso(a.minusHours(1)), iso(a)), BusyInterval(iso(a.plusHours(1)), iso(a.plusHours(3))),
                BusyInterval(iso(a.plusDays(1).minusHours(2)), iso(a.plusDays(1))))) }) { _, _ -> }
    }

    @Test fun search() = screen("search") {
        vm.prefs.addRecent("with:dana")
        vm.prefs.addRecent("offsite")
        CalendarSearch(vm, emptyList(), zone, {}, {})
    }

    @Test fun conflict() = screen("conflict") {
        val server = CalendarEvent("g1", "Design review", at(0, 10), at(0, 11))
        val mine = kotlinx.serialization.json.buildJsonObject {
            put("summary", kotlinx.serialization.json.JsonPrimitive("Design review (moved)"))
            put("start", kotlinx.serialization.json.JsonPrimitive(at(0, 14)))
        }
        ConflictSheet(CalConflict("event|g1", "event", "g1", "Changed elsewhere", "PATCH", mine, server, null, 0), {}, {}, {}, {})
    }

    @Test fun scope() = screen("scope") { ScopeSheet("Change recurring event", {}) {} }

    @Test fun sendUpdates() = screen("send-updates") { SendUpdatesSheet("update", 3, {}) {} }

    @Test fun monthPicker() = screen("month-picker") { MonthPickerDialog(day, {}, {}) {} }

}
