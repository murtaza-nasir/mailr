package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.time.AppClock
import android.app.Application
import android.content.Intent
import android.os.Looper
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.cal.DelayedOp
import io.github.murtazanasir.mailr.data.db.CalEventEntity
import io.github.murtazanasir.mailr.data.db.CalTaskEntity
import io.github.murtazanasir.mailr.data.db.PendingOpEntity
import io.github.murtazanasir.mailr.data.db.ReminderEntity
import io.github.murtazanasir.mailr.data.reminders.Kind
import io.github.murtazanasir.mailr.data.reminders.RState
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.Assert.fail
import org.robolectric.Shadows.shadowOf
import java.time.ZonedDateTime
import java.util.concurrent.CopyOnWriteArrayList

/**
 * A fake mailr server and an in-memory app graph with one task, one invitation and their
 * reminders, for the tests of the reminder, task and event actions on Android. Writes answer 200, reads
 * 404 (so a post-write resync never replaces the seeded rows).
 */
class CalendarHarness {
    val app: Application = ApplicationProvider.getApplicationContext()
    val server = MockWebServer()
    val requests = CopyOnWriteArrayList<Pair<RecordedRequest, String>>()
    lateinit var g: AppGraph
    /** Owns the graph and the tests' ViewModels; [stop] tears them down in order. */
    val graphs = TestGraph()
    /** Deletions waiting out their undo window (CalendarRepository.scheduleDelayed). */
    val delayed = CopyOnWriteArrayList<DelayedOp>()

    val now: ZonedDateTime = AppClock.now().withSecond(0).withNano(0)
    val task = TaskItem("t1", "Send the Q3 numbers to Dana", now.plusHours(2).toOffsetDateTime().toString(), conversationId = 3066)
    val start: ZonedDateTime = now.plusMinutes(40)
    val event = CalendarEvent("e1", "Budget review", start.toOffsetDateTime().toString(), start.plusHours(1).toOffsetDateTime().toString(),
        location = "Room 204", organizer = "alice@example.com", myStatus = "NEEDS-ACTION", conversationId = 3075)
    val taskReminder = ReminderEntity("t|t1|${task.dueKey}|0", Kind.TASK, "t1", null, task.summary, "Due in 2 h",
        now.toInstant().toEpochMilli(), now.toInstant().toEpochMilli(), null, null, RState.SHOWN, updatedAt = 0)
    val eventReminder = ReminderEntity("e|e1|${event.occurrence}|60", Kind.EVENT, "e1", event.occurrence, event.summary, "Room 204",
        now.minusMinutes(20).toInstant().toEpochMilli(), now.minusMinutes(20).toInstant().toEpochMilli(),
        start.toInstant().toEpochMilli(), start.plusHours(1).toInstant().toEpochMilli(), RState.SHOWN, updatedAt = 0)

    fun start(): CalendarHarness {
        TestGraph.assertNoLeakedFailures("A test that ran before this CalendarHarness test")
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                requests += request to request.body.readUtf8()
                return if (request.method == "GET") MockResponse().setResponseCode(404).setBody("""{"detail":"not here"}""")
                else MockResponse().setBody("""{"ok":true}""")
            }
        }
        server.start()
        SessionStore(app).apply { server = this@CalendarHarness.server.url("/").toString(); save("tok", "csrf", "me@example.com", "Me") }
        g = graphs.graph(app)
        g.calendar.scheduleDelayed = { op, _ -> delayed += op }
        g.calendar.cancelDelayed = { name -> delayed.removeAll { it.name == name } }
        runBlocking {
            g.db.calendar().putTasks(listOf(CalTaskEntity(task.uid, null, null, false, now.plusHours(2).toInstant().toEpochMilli(),
                MailrJson.encodeToString(TaskItem.serializer(), task))))
            g.db.calendar().putEvents(listOf(CalEventEntity(event.key, event.uid, start.toInstant().toEpochMilli(),
                start.plusHours(1).toInstant().toEpochMilli(), false, null, MailrJson.encodeToString(CalendarEvent.serializer(), event))))
            g.db.reminders().put(listOf(taskReminder, eventReminder))
        }
        return this
    }

    /** ViewModels cleared and their coroutines finished, the database closed, the server shut; then a
     *  coroutine of this test that failed unseen fails this test (see [TestGraph]). */
    fun stop() {
        try {
            graphs.close()
        } finally {
            server.shutdown()
        }
        TestGraph.assertNoLeakedFailures("CalendarHarness test")
    }

    /** A ViewModel cleared before the database closes. */
    fun <T : androidx.lifecycle.ViewModel> vm(make: () -> T): T = graphs.vm(make)

    fun idle() = shadowOf(Looper.getMainLooper()).idle()

    /** Waits for [cond] with the main looper idled between looks (the shared wait, io.github.murtazanasir.mailr.testing.eventually). */
    fun until(what: String, cond: () -> Boolean) = eventually(what, idle = ::idle, check = cond)

    /** The body of the last request [method] [path] that reached the server, waiting for it. */
    fun sent(method: String, path: String): JsonObject {
        until("$method $path") { requests.any { it.first.method == method && it.first.path == path } }
        return MailrJson.parseToJsonElement(requests.last { it.first.method == method && it.first.path == path }.second).jsonObject
    }

    fun sentNothing(): Boolean = requests.none { it.first.method != "GET" }

    /** Operations in the offline queue (notification and reminder actions always go there). */
    fun queued(): List<PendingOpEntity> = runBlocking { g.db.pending().next(50) }

    fun queuedBody(method: String, path: String): JsonObject {
        until("queued $method $path") { queued().any { it.method == method && it.path == path } }
        return MailrJson.parseToJsonElement(queued().last { it.method == method && it.path == path }.body).jsonObject
    }

    fun reminder(key: String): ReminderEntity? = runBlocking { g.reminders.reminder(key) }

    fun storedTask(uid: String): TaskItem? = runBlocking { g.calendar.task(uid) }

    /** The screen the app was asked to open (mailr:// deep links from the sheet). */
    fun nextOpened(): Intent? = shadowOf(app).nextStartedActivity
}
