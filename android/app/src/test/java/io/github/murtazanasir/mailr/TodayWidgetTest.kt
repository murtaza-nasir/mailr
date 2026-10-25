// ACT-SURFACES: android.widget android.launcher
package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.time.AppClock
import android.app.Application
import android.appwidget.AppWidgetManager
import android.content.Intent
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.db.CalEventEntity
import io.github.murtazanasir.mailr.data.db.CalTaskEntity
import io.github.murtazanasir.mailr.ui.Destination
import io.github.murtazanasir.mailr.ui.ask.AskActivity
import io.github.murtazanasir.mailr.ui.ask.AskIntents
import io.github.murtazanasir.mailr.ui.parseDeepLink
import io.github.murtazanasir.mailr.ui.today.DayPart
import io.github.murtazanasir.mailr.ui.today.TodayModel
import io.github.murtazanasir.mailr.ui.today.TodayUndo
import io.github.murtazanasir.mailr.ui.today.TodayWidgets
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.jsonPrimitive
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

/**
 * The Today widget, the "Talk to Ambrose" widget and the tiles (android.widget,
 * android.launcher): what Today shows from the phone's copy, the tick (task.complete, through the
 * replay queue, with Undo), and the one-tap way into Ambrose listening (agent.dictate). The "New
 * task" tile opens the task editor (task.create).
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class TodayWidgetTest {
    private val h = CalendarHarness()
    private val zone: ZoneId = ZoneId.of("America/Chicago")
    /** A fixed noon, so nothing depends on the time the tests run. */
    private val noon: ZonedDateTime = ZonedDateTime.of(2026, 10, 6, 12, 0, 0, 0, zone)

    @Before fun setUp() { h.start(); TodayUndo.clear() }
    @After fun tearDown() { TodayUndo.clear(); h.stop() }

    private fun at(h: Int, m: Int = 0, days: Long = 0) = noon.plusDays(days).withHour(h).withMinute(m).toOffsetDateTime().toString()
    private fun ev(uid: String, title: String, start: String, end: String?, allDay: Boolean = false, status: String? = null, my: String? = null) =
        CalendarEvent(uid, title, start, end, allDay = allDay, status = status, myStatus = my)
    private fun task(uid: String, title: String, due: String?, status: String = "needs-action", start: String? = null, parent: String? = null) =
        TaskItem(uid, title, due, dueAllDay = due?.length == 10, status = status, start = start, parent = parent)

    @Test fun theGreetingFollowsThePartOfTheDay() {
        assertEquals(DayPart.NIGHT, DayPart.of(4))
        assertEquals(DayPart.MORNING, DayPart.of(5))
        assertEquals(DayPart.MORNING, DayPart.of(11))
        assertEquals(DayPart.AFTERNOON, DayPart.of(12))
        assertEquals(DayPart.EVENING, DayPart.of(17))
        assertEquals(DayPart.NIGHT, DayPart.of(22))
        assertEquals("Good evening", DayPart.NIGHT.greeting)
    }

    /** Today's events that are still ahead (all-day first), and the open tasks due today or overdue, overdue first. */
    /** calendar.today_view on the home screen: the events still ahead and the tasks due. */
    @Test fun todayShowsWhatIsLeftOfTheDay() {
        val events = listOf(
            ev("e1", "Budget review", at(15), at(16)),
            ev("e2", "Stand-up", at(9), at(9, 15)),                              // over
            ev("e3", "Offsite", "2026-10-06", "2026-10-07", allDay = true),
            ev("e4", "Lunch with Dana", at(11, 30), at(12, 30)),                 // now
            ev("e5", "Cancelled sync", at(14), at(15), status = "CANCELLED"),
            ev("e6", "Declined call", at(16), at(17), my = "DECLINED"),
            ev("e7", "Tomorrow's thing", at(10, days = 1), at(11, days = 1)),
        )
        val tasks = listOf(
            task("t1", "Send the Q3 numbers", at(17)),
            task("t2", "Renew the studio insurance", "2026-10-02"),
            task("t3", "Done already", at(10), status = "completed"),
            task("t4", "Next week", at(10, days = 7)),
            task("t5", "Starts later", at(18), start = at(18)),
            task("t6", "A subtask", at(17), parent = "t1"),
            task("t7", "Water the plants", "2026-10-06"),
        )
        val m = TodayModel.build(events, tasks, noon)
        assertEquals(DayPart.AFTERNOON, m.part)
        assertEquals(listOf("Offsite", "Lunch with Dana", "Budget review"), m.events.map { it.title })
        assertEquals("All day", m.events[0].time)
        assertTrue(m.events[1].now)
        assertEquals("3 PM", m.events[2].time)
        assertEquals(listOf("Renew the studio insurance", "Send the Q3 numbers", "Water the plants"), m.tasks.map { it.title })
        assertTrue(m.tasks[0].overdue)
        assertEquals("Overdue · Fri 2 Oct", m.tasks[0].note)
        assertEquals("Due 5 PM", m.tasks[1].note)
        assertNull(m.tasks[2].note)
        assertEquals("3 events · 3 tasks", m.summary)
        assertEquals("15:00", TodayModel.build(events, tasks, noon, h24 = true).events[2].time)
    }

    @Test fun anEmptyDayIsCalm() {
        val m = TodayModel.build(emptyList(), emptyList(), noon.withHour(23))
        assertTrue(m.empty)
        assertEquals(DayPart.NIGHT, m.part)
        assertEquals("Nothing due", m.summary)
        assertEquals("Nothing due today.", TodayModel.EMPTY)
        assertFalse(TodayModel.signedOut(noon).ready)
    }

    /** The widget reads the phone's copy: rows in Room show without the network. */
    @Test fun theWidgetReadsThePhonesCopy() = runBlocking {
        val now = AppClock.now(zone).withHour(12).withMinute(0)
        val e = ev("w1", "Design review", now.withHour(15).toOffsetDateTime().toString(), now.withHour(16).toOffsetDateTime().toString())
        val t = task("w2", "Call the plumber", now.withHour(17).toOffsetDateTime().toString())
        h.g.db.calendar().putEvents(listOf(CalEventEntity(e.key, e.uid, now.withHour(15).toInstant().toEpochMilli(),
            now.withHour(16).toInstant().toEpochMilli(), false, null, MailrJson.encodeToString(CalendarEvent.serializer(), e))))
        h.g.db.calendar().putTasks(listOf(CalTaskEntity(t.uid, null, null, false, now.withHour(17).toInstant().toEpochMilli(),
            MailrJson.encodeToString(TaskItem.serializer(), t))))
        val m = TodayWidgets.model(h.g, now, h24 = false)
        assertTrue(m.ready)
        assertTrue(m.events.any { it.title == "Design review" })
        assertTrue(m.tasks.any { it.title == "Call the plumber" })
        assertFalse(TodayWidgets.model(null, now, false).ready)
    }

    /** task.complete @android.widget: the tick goes through the replay queue; the header's Undo reopens it. */
    @Test fun theTickCompletesTheTaskWithUndo() {
        runBlocking { TodayWidgets.complete(h.g, "t1") }
        assertEquals(true, h.queuedBody("PATCH", "tasks/t1")["completed"]!!.jsonPrimitive.boolean)
        assertEquals("Send the Q3 numbers to Dana", TodayUndo.get()?.title)
        assertNull(TodayUndo.get(AppClock.millis() + TodayUndo.UNDO_MS + 1))
        runBlocking { TodayWidgets.undo(h.g) }
        assertEquals(false, h.queuedBody("PATCH", "tasks/t1")["completed"]!!.jsonPrimitive.boolean)
        assertNull(TodayUndo.get())
    }

    /** The widget's links open the event, the task and the calendar in the app. */
    @Test fun theLinksOpenTheApp() {
        val e = TodayModel.Event("uid 1@x", "2026-10-06T15:00:00Z", "Budget review", "3 PM", null, null, false)
        val dest = parseDeepLink(TodayWidgets.open(h.app, TodayWidgets.eventUri(e)))
        assertEquals(Destination.Event("uid 1@x", "2026-10-06T15:00:00Z"), dest)
        assertEquals(Destination.Task("t1"), parseDeepLink(TodayWidgets.open(h.app, TodayWidgets.taskUri("t1"))))
        assertEquals(Destination.Event(null, null, "day", null), parseDeepLink(TodayWidgets.open(h.app, TodayWidgets.CALENDAR_URI)))
    }

    /** agent.dictate @android.widget: "Talk to Ambrose" opens the capture listening at once. */
    @Test fun talkToAmbroseOpensListening() {
        val i = AskIntents.capture(h.app, null, listen = true, from = "today_widget")
        assertEquals(AskActivity::class.java.name, i.component?.className)
        assertEquals(AskIntents.ACTION_DICTATE, i.action)
        assertEquals(true, AskIntents.listen(i))
    }

    /** Both widgets and both tiles are registered (the old Ask widget's receiver now draws Talk to Ambrose). */
    @Test fun theWidgetsAndTilesAreRegistered() {
        val pm = h.app.packageManager
        val widgets = pm.queryBroadcastReceivers(Intent(AppWidgetManager.ACTION_APPWIDGET_UPDATE).setPackage(h.app.packageName), 0)
            .map { it.activityInfo.name.substringAfterLast('.') }.sorted()
        assertEquals(listOf("AgendaWidgetReceiver", "AskWidgetReceiver", "TodayWidgetReceiver"), widgets)
        val tiles = pm.queryIntentServices(Intent("android.service.quicksettings.action.QS_TILE").setPackage(h.app.packageName), 0)
            .map { it.serviceInfo.name.substringAfterLast('.') }.sorted()
        // task.create @android.launcher: the New task tile.
        assertEquals(listOf("AskTileService", "NewTaskTileService"), tiles)
    }
}
