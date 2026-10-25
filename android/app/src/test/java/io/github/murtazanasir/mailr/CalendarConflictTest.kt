package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.time.AppClock
import android.app.Application
import io.github.murtazanasir.mailr.data.api.ApiException
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.EventDraft
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.api.toDraft
import io.github.murtazanasir.mailr.data.cal.BulkOp
import io.github.murtazanasir.mailr.data.cal.WriteResult
import io.github.murtazanasir.mailr.data.db.CalEventEntity
import io.github.murtazanasir.mailr.data.db.CalMasterEntity
import io.github.murtazanasir.mailr.data.db.CalTaskEntity
import io.github.murtazanasir.mailr.data.db.PendingOpEntity
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.jsonPrimitive
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.time.ZoneOffset
import java.time.ZonedDateTime

/**
 * Writes carry the version the phone last saw: If-Match plus `etag`
 * in the body (kept by queued writes), a 409 keeps the server copy and opens a conflict, a 404
 * removes the item, and "Keep mine" sends the change again against the server's version.
 * Also task.reorder / task.reparent / task.move_list (one move route), task.bulk_complete,
 * task.skip, task.delete with cascade and event.search / task.search offline.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class CalendarConflictTest {
    private val f = FakeCalendarServer()
    private val due = AppClock.now(ZoneOffset.UTC).plusDays(1).withHour(9).withMinute(0).withSecond(0).withNano(0)
    private val task = TaskItem("t1", "Pay rent", due.toOffsetDateTime().toString(), list = "https://dav/list/", etag = "\"v1\"", id = 21)

    private fun put(t: TaskItem) = runBlocking {
        f.db.calendar().putTasks(listOf(CalTaskEntity(t.uid, t.list, t.parent, t.completed, due.toInstant().toEpochMilli(),
            MailrJson.encodeToString(TaskItem.serializer(), t), etag = t.etag, sortOrder = t.sortOrder)))
    }

    private fun conflictBody(server: TaskItem) =
        """{"error":"conflict","detail":"The task changed on another device.","kind":"task",
           "server":${MailrJson.encodeToString(TaskItem.serializer(), server)}}"""

    @Before fun setUp() { f.start(); put(task) }
    @After fun tearDown() = f.stop()

    @Test fun aChangeCarriesTheVersionItSaw() = runBlocking {
        f.on("PATCH", "tasks/t1") { _, _ -> f.json(MailrJson.encodeToString(TaskItem.serializer(), task.copy(summary = "Pay the rent", etag = "\"v2\""))) }
        assertEquals(WriteResult.SENT, f.repo.updateTask(task, task.toDraft().copy(summary = "Pay the rent")))
        val r = f.sent("PATCH", "tasks/t1").single()
        assertEquals("\"v1\"", r.first.getHeader("If-Match"))
        assertEquals("\"v1\"", f.body(r)["etag"]!!.jsonPrimitive.content)
        // The answer's version is stored for the next write, and the row is confirmed.
        val row = f.db.calendar().task("t1")!!
        assertEquals("\"v2\"", row.etag)
        assertFalse(row.dirty)
    }

    @Test fun conflictKeepsTheServerCopyAndKeepMineSendsAgain() = runBlocking {
        val theirs = task.copy(summary = "Pay rent (landlord's new account)", etag = "\"v2\"")
        f.on("PATCH", "tasks/t1") { _, _ -> f.json(conflictBody(theirs), 409) }
        try {
            f.repo.updateTask(task, task.toDraft().copy(summary = "Pay rent today"))
            fail("409 expected")
        } catch (e: ApiException) {
            assertEquals(409, e.code)
        }
        assertEquals(theirs.summary, f.repo.task("t1")!!.summary)
        assertEquals("\"v2\"", f.db.calendar().task("t1")!!.etag)
        val c = f.repo.conflicts.first().single()
        assertEquals("task|t1", c.key)
        assertEquals(theirs.summary, c.serverTask!!.summary)
        assertEquals("Pay rent today", c.mine!!["summary"]!!.jsonPrimitive.content)

        // Keep mine: the same change against the server's version.
        f.on("PATCH", "tasks/t1") { _, _ -> f.json(MailrJson.encodeToString(TaskItem.serializer(), task.copy(summary = "Pay rent today", etag = "\"v3\""))) }
        assertEquals(WriteResult.SENT, f.repo.keepMine(c.key))
        val again = f.sent("PATCH", "tasks/t1").last()
        assertEquals("\"v2\"", again.first.getHeader("If-Match"))
        assertEquals("\"v2\"", f.body(again)["etag"]!!.jsonPrimitive.content)
        assertEquals("Pay rent today", f.repo.task("t1")!!.summary)
        assertTrue(f.repo.conflicts.first().isEmpty())
    }

    @Test fun useTheirsClosesTheConflict() = runBlocking {
        f.on("PATCH", "tasks/t1") { _, _ -> f.json(conflictBody(task.copy(etag = "\"v2\"")), 409) }
        runCatching { f.repo.setTaskDone("t1", true) }
        assertFalse(f.repo.task("t1")!!.completed)
        f.repo.keepTheirs("task|t1")
        assertTrue(f.repo.conflicts.first().isEmpty())
    }

    @Test fun goneOnTheServerMeansGoneHere() = runBlocking {
        f.on("PATCH", "tasks/t1") { _, _ -> f.json("""{"detail":"Task not found"}""", 404) }
        runCatching { f.repo.updateTask(task, task.toDraft().copy(summary = "x")) }
        assertNull(f.db.calendar().task("t1"))
    }

    @Test fun queuedWritesKeepTheirVersionAndMeetConflictsLater() = runBlocking {
        // Offline: two changes queue up. Only the first carries the version; the second follows it.
        f.server.shutdown()
        assertEquals(WriteResult.QUEUED, f.repo.updateTask(task, task.toDraft().copy(summary = "One")))
        assertEquals(WriteResult.QUEUED, f.repo.updateTask(task.copy(summary = "One"), task.toDraft().copy(summary = "Two")))
        val ops = f.db.pending().next(10)
        assertEquals(2, ops.size)
        assertEquals("\"v1\"", MailrJson.parseToJsonElement(ops[0].body).let { (it as kotlinx.serialization.json.JsonObject)["etag"]!!.jsonPrimitive.content })
        assertNull((MailrJson.parseToJsonElement(ops[1].body) as kotlinx.serialization.json.JsonObject)["etag"])
    }

    @Test fun aQueuedWriteRefusedWith409OpensAConflict() = runBlocking {
        val theirs = task.copy(summary = "Changed on the laptop", etag = "\"v9\"")
        f.on("PATCH", "tasks/t1") { _, _ -> f.json(conflictBody(theirs), 409) }
        f.db.pending().insert(PendingOpEntity(idempotencyKey = "k", method = "PATCH", path = "tasks/t1",
            body = """{"summary":"Mine","etag":"\"v1\""}""", description = "Change task", createdAt = 0))
        assertTrue(f.mail.drainPending())
        assertEquals(theirs.summary, f.repo.task("t1")!!.summary)
        assertEquals("Mine", f.repo.conflicts.first().single().mine!!["summary"]!!.jsonPrimitive.content)
        assertTrue(f.db.pending().next(10).isEmpty())
    }

    @Test fun aQueuedDeleteOfSomethingGoneDropsIt() = runBlocking {
        f.on("PATCH", "tasks/t1") { _, _ -> f.json("""{"detail":"Task not found"}""", 404) }
        f.db.pending().insert(PendingOpEntity(idempotencyKey = "k", method = "PATCH", path = "tasks/t1", body = "{}",
            description = "Complete", createdAt = 0))
        f.mail.drainPending()
        assertNull(f.db.calendar().task("t1"))
    }

    @Test fun anEventWriteStoresTheNewVersionOnEveryOccurrence() = runBlocking {
        val start = due.withHour(14)
        val occ = { d: Long -> start.plusDays(d).toInstant().let { io.github.murtazanasir.mailr.data.api.Occurrence.utc(it) } }
        val master = CalendarEvent("r1", "Standup", occ(0), recurring = true, rrule = "FREQ=DAILY", etag = "\"e1\"")
        f.db.calendar().putMasters(listOf(CalMasterEntity("r1", 12, null, "\"e1\"", MailrJson.encodeToString(CalendarEvent.serializer(), master))))
        val rows = (0L..1L).map { d ->
            val e = master.copy(start = occ(d), end = start.plusDays(d).plusMinutes(15).toInstant().let { io.github.murtazanasir.mailr.data.api.Occurrence.utc(it) },
                occurrenceId = occ(d), recurrenceId = occ(d))
            CalEventEntity(e.key, "r1", start.plusDays(d).toInstant().toEpochMilli(), start.plusDays(d).plusMinutes(15).toInstant().toEpochMilli(),
                false, null, MailrJson.encodeToString(CalendarEvent.serializer(), e))
        }
        f.db.calendar().putEvents(rows)
        f.on("PATCH", "calendar/events/r1") { _, _ -> f.json(MailrJson.encodeToString(CalendarEvent.serializer(), master.copy(etag = "\"e2\""))) }
        val first = f.repo.eventInstance(rows[0].key)!!
        f.repo.updateEvent(first, EventDraft("r1", "Standup (new room)", first.start, first.end, false), "this", rruleChanged = false)
        val sent = f.sent("PATCH", "calendar/events/r1").single()
        assertEquals("\"e1\"", sent.first.getHeader("If-Match"))
        assertEquals(occ(0), f.body(sent)["occurrence"]!!.jsonPrimitive.content)
        assertEquals("\"e2\"", f.db.calendar().master("r1")!!.etag)
        assertTrue(f.repo.eventsOf("r1").all { it.etag == "\"e2\"" })
    }

    @Test fun moveTakesTheMidpointAndSendsTheNeighbours() = runBlocking {
        put(TaskItem("a", "A", null, list = task.list, sortOrder = 1000))
        put(TaskItem("b", "B", null, list = task.list, sortOrder = 2000))
        assertEquals(WriteResult.SENT, f.repo.moveTask("t1", after = "a", before = "b", parent = ""))
        assertEquals(1500L, f.repo.task("t1")!!.sortOrder)
        val body = f.body(f.sent("POST", "tasks/t1/move").single())
        assertEquals("a", body["after"]!!.jsonPrimitive.content)
        assertEquals("b", body["before"]!!.jsonPrimitive.content)
        assertEquals("", body["parent"]!!.jsonPrimitive.content)
        assertEquals("\"v1\"", body["etag"]!!.jsonPrimitive.content)
    }

    @Test fun bulkPutsBackWhatTheServerRefused() = runBlocking {
        put(TaskItem("t2", "Call bank", null, list = task.list))
        f.on("POST", "tasks/bulk") { _, _ -> f.json("""{"ok":["t1"],"failed":[{"uid":"t2","detail":"read-only list"}]}""") }
        val r = f.repo.bulk(listOf("t1", "t2"), BulkOp.COMPLETE)!!
        assertEquals(listOf("t1"), r.ok)
        assertTrue(f.repo.task("t1")!!.completed)
        assertFalse(f.repo.task("t2")!!.completed)
        val body = f.body(f.sent("POST", "tasks/bulk").single())
        assertEquals("complete", body["op"]!!.jsonPrimitive.content)
    }

    @Test fun deleteCarriesTheVersionAndCascade() = runBlocking {
        val delayed = mutableListOf<io.github.murtazanasir.mailr.data.cal.DelayedOp>()
        f.repo.scheduleDelayed = { op, _ -> delayed += op }
        put(TaskItem("child", "Sub", null, parent = "t1", list = task.list))
        f.repo.deleteTask(task, cascade = true)
        assertEquals("tasks/t1?cascade=true&etag=%22v1%22", delayed.single().path)
        assertTrue(f.repo.tasks().first().none { it.uid == "t1" || it.uid == "child" })
    }

    @Test fun skipAppliesTheAdvancedTask() = runBlocking {
        val next = task.copy(due = due.plusWeeks(1).toOffsetDateTime().toString(), etag = "\"v2\"", recurring = true, rrule = "FREQ=WEEKLY")
        f.on("POST", "tasks/t1/skip") { _, _ -> f.json("""{"task":${MailrJson.encodeToString(TaskItem.serializer(), next)},"next":"${next.due}"}""") }
        f.repo.skipTask("t1")
        assertEquals(next.due, f.repo.task("t1")!!.due)
        assertEquals("\"v1\"", f.body(f.sent("POST", "tasks/t1/skip").single())["etag"]!!.jsonPrimitive.content)
    }

    @Test fun searchFallsBackToThePhoneOffline() = runBlocking {
        f.server.shutdown()
        val page = f.repo.search("rent")
        assertEquals(listOf("t1"), page.items.mapNotNull { it.task?.uid })
    }
}
