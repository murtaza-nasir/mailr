// ACT-SURFACES: android.reader android.calendar android.tasks
package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.time.AppClock
import android.app.Application
import io.github.murtazanasir.mailr.data.agent.AgentClient
import io.github.murtazanasir.mailr.data.agent.AskAbout
import io.github.murtazanasir.mailr.data.api.Attachment
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.ImportResult
import io.github.murtazanasir.mailr.data.api.ImportedItem
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.cal.CalendarRepository
import io.github.murtazanasir.mailr.data.cal.JoinLink
import io.github.murtazanasir.mailr.data.cal.WriteResult
import io.github.murtazanasir.mailr.data.db.CalCollectionEntity
import io.github.murtazanasir.mailr.data.db.CalEventEntity
import io.github.murtazanasir.mailr.ui.calendar.IcsExport
import io.github.murtazanasir.mailr.ui.conversation.importSummary
import io.github.murtazanasir.mailr.ui.conversation.isIcs
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import okhttp3.mockwebserver.MockResponse
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.ZoneOffset
import java.time.ZonedDateTime

/**
 * The 0.4.0 finishing touches: "Ask assistant" about an event or a task, RSVP scopes as the
 * server applies them, tasks behind links (GET /tasks/{uid}), event.export_ics before a delete,
 * event.import_ics from the reader, and the conference link (event.join).
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class FinishingTouchesTest {
    private val f = FakeCalendarServer()
    private val start: ZonedDateTime = AppClock.now(ZoneOffset.UTC).plusDays(1).withHour(9).withMinute(0).withSecond(0).withNano(0)
    private val cal = CalendarCollection("https://dav/work/", "Work", "#039be5", events = true, calendarId = 1)
    private fun weekly(week: Long) = CalendarEvent("s1", "Standup", start.plusWeeks(week).toOffsetDateTime().toString(),
        start.plusWeeks(week).plusHours(1).toOffsetDateTime().toString(), calendar = cal.url, etag = "\"v1\"", recurring = true,
        rrule = "FREQ=WEEKLY", organizer = "alice@example.com", myStatus = "NEEDS-ACTION",
        occurrenceId = io.github.murtazanasir.mailr.data.api.Occurrence.utc(start.plusWeeks(week).toInstant()))
    private val w0 = weekly(0)
    private val w1 = weekly(1)
    private val w2 = weekly(2)

    @Before fun setUp() {
        f.start()
        runBlocking {
            f.db.calendar().putCollections(listOf(CalCollectionEntity(cal.url, cal.calendarId, cal.name, cal.color, true, false, false, 0, null,
                MailrJson.encodeToString(CalendarCollection.serializer(), cal))))
            // Week 2 is an override moved to two hours before week 1 starts.
            val moved = w2.copy(start = start.plusWeeks(1).minusHours(2).toOffsetDateTime().toString(),
                end = start.plusWeeks(1).minusHours(1).toOffsetDateTime().toString(), isOverride = true)
            f.db.calendar().putEvents(listOf(w0, w1, moved).map { e ->
                CalEventEntity(e.key, e.uid, OffsetDateTime.parse(e.start).toInstant().toEpochMilli(), OffsetDateTime.parse(e.end!!).toInstant().toEpochMilli(),
                    false, e.calendar, MailrJson.encodeToString(CalendarEvent.serializer(), e), occurrence = e.occurrence)
            })
            f.repo.loadCached()
        }
    }

    @After fun tearDown() = f.stop()

    // ---------------- Ask assistant (§11) ----------------

    /** An event asked about goes at the top of `context` as focused_event {uid, occurrence}, with no conversation. */
    @Test fun askAboutAnEventSendsFocusedEvent() {
        val b = AgentClient.chatBody("Can I move this to Friday?", null, null, null, AskAbout.Event("abc@x", w1.occurrence, "Standup"))
        val ctx = b["context"]!!.jsonObject
        assertNull(ctx["conversation_id"])
        val fe = ctx["focused_event"]!!.jsonObject
        assertEquals("abc@x", fe["uid"]!!.jsonPrimitive.content)
        assertEquals(w1.occurrence, fe["occurrence"]!!.jsonPrimitive.content)
        // The series (or no occurrence): null, as the contract allows.
        val series = AgentClient.chatBody("x", "s9", null, null, AskAbout.Event("abc@x", null, ""))
        assertEquals(JsonNull, series["context"]!!.jsonObject["focused_event"]!!.jsonObject["occurrence"])
        assertEquals("s9", series["session_id"]!!.jsonPrimitive.content)
    }

    /** A task asked about is focused_task (its uid); a later resume keeps it on the turn. */
    @Test fun askAboutATaskSendsFocusedTask() {
        val b = AgentClient.chatBody("Split this into steps", null, null, null, AskAbout.Task("task-uid", "Call Dana"))
        assertEquals("task-uid", b["context"]!!.jsonObject["focused_task"]!!.jsonPrimitive.content)
        val resume = AgentClient.chatBody("", "s1", null, 7, AskAbout.Task("task-uid", "Call Dana"))
        assertNull(resume["message"])
        assertEquals(7L, resume["resume_approval_id"]!!.jsonPrimitive.long)
        assertEquals("task-uid", resume["context"]!!.jsonObject["focused_task"]!!.jsonPrimitive.content)
        // Without an item, the conversation alone, as before.
        val mail = AgentClient.chatBody("Summarize", null, 42, null, null)
        assertEquals(42L, mail["context"]!!.jsonObject["conversation_id"]!!.jsonPrimitive.long)
        assertNull(mail["context"]!!.jsonObject["focused_task"])
        assertNull(AgentClient.chatBody("Hi", null, null, null, null)["context"])
    }

    /** The chat turn itself (POST /agent/chat) carries the item. */
    @Test fun chatTurnPostsTheContext() = runBlocking {
        f.on("POST", "agent/chat") { _, _ ->
            MockResponse().setHeader("Content-Type", "text/event-stream").setBody("event: done\ndata: {\"type\":\"done\"}\n\n")
        }
        withTimeout(10_000) { AgentClient(f.repo.api.client).chat("Prepare an agenda", null, null, about = AskAbout.Event("s1", w1.occurrence, "Standup")).toList() }
        val body = f.body(f.sent("POST", "agent/chat").single())
        assertEquals("s1", body["context"]!!.jsonObject["focused_event"]!!.jsonObject["uid"]!!.jsonPrimitive.content)
    }

    // ---------------- RSVP scope (§10) ----------------

    /** event.rsvp "following": this occurrence and the later ones by RECURRENCE-ID, so a moved override counts where it was. */
    @Test fun rsvpFollowingMatchesTheServer() = runBlocking {
        assertEquals(WriteResult.SENT, f.repo.rsvp(w1, "accepted", scope = "following"))
        val body = f.body(f.sent("POST", "calendar/events/s1/rsvp").single())
        assertEquals("following", body["scope"]!!.jsonPrimitive.content)
        assertEquals(w1.occurrence, body["occurrence"]!!.jsonPrimitive.content)
        assertEquals("NEEDS-ACTION", f.repo.eventInstance(w0.key)!!.myStatus)
        assertEquals("ACCEPTED", f.repo.eventInstance(w1.key)!!.myStatus)
        // Week 2 now starts before week 1 does, but its RECURRENCE-ID is later: the server answers it too.
        assertEquals("ACCEPTED", f.repo.eventInstance(w2.key)!!.myStatus)
    }

    /** this/following always carry the occurrence (the server answers 400 without it); a single event sends no scope. */
    @Test fun rsvpScopeAlwaysCarriesTheOccurrence() = runBlocking {
        f.repo.rsvp(w0, "declined", scope = "this")
        val body = f.body(f.sent("POST", "calendar/events/s1/rsvp").single())
        assertEquals("this", body["scope"]!!.jsonPrimitive.content)
        assertEquals(w0.occurrence, body["occurrence"]!!.jsonPrimitive.content)
        assertEquals("DECLINED", f.repo.eventInstance(w0.key)!!.myStatus)
        assertEquals("NEEDS-ACTION", f.repo.eventInstance(w1.key)!!.myStatus)
        val single = w0.copy(uid = "one", recurring = false, rrule = null)
        f.db.calendar().putEvents(listOf(CalEventEntity(single.key, "one", 0, 1, false, cal.url, MailrJson.encodeToString(CalendarEvent.serializer(), single))))
        f.repo.rsvp(single, "accepted", scope = "this")
        val b2 = f.body(f.sent("POST", "calendar/events/one/rsvp").single())
        assertNull(b2["scope"])
        assertNull(b2["occurrence"])
    }

    @Test fun rsvpCoversByOriginalStart() {
        val z = ZoneId.of("America/Chicago")
        val a = "2026-10-05T14:00:00+00:00"
        val b = "2026-10-12T14:00:00+00:00"
        assertTrue(CalendarRepository.rsvpCovers("following", true, b, a, z))
        assertFalse(CalendarRepository.rsvpCovers("following", true, a, b, z))
        assertTrue(CalendarRepository.rsvpCovers("following", true, b, b, z))
        assertFalse(CalendarRepository.rsvpCovers("this", true, b, a, z))
        assertTrue(CalendarRepository.rsvpCovers("all", true, a, b, z))
        assertTrue(CalendarRepository.rsvpCovers("this", false, a, b, z))
        // All-day series: dates compare as days.
        assertTrue(CalendarRepository.rsvpCovers("following", true, "2026-10-12", "2026-10-05", z))
        assertFalse(CalendarRepository.rsvpCovers("following", true, "2026-10-01", "2026-10-05", z))
    }

    // ---------------- GET /tasks/{uid} ----------------

    /** A link to a task this phone does not hold: GET /tasks/{uid}, kept in Room; 404 is null. */
    @Test fun linkedTaskIsFetchedOnce() = runBlocking {
        val t = TaskItem("t9", "Renew passport", "2026-11-01", dueAllDay = true, etag = "\"e1\"", list = "https://dav/list/")
        f.on("GET", "tasks/t9") { _, _ -> f.json(MailrJson.encodeToString(TaskItem.serializer(), t)) }
        assertEquals("Renew passport", f.repo.fetchTask("t9")!!.summary)
        assertEquals("Renew passport", f.repo.task("t9")!!.summary)
        f.repo.fetchTask("t9")
        assertEquals(1, f.sent("GET", "tasks/t9").size)
        assertNull(f.repo.fetchTask("gone"))
    }

    // ---------------- event.export_ics ----------------

    /** event.export_ics: the collection's export as bytes, for the file the user picked. */
    @Test fun exportDownloadsTheCollection() = runBlocking {
        val ics = "BEGIN:VCALENDAR\r\nX-WR-CALNAME:Work\r\nEND:VCALENDAR\r\n"
        f.on("GET", "calendar/collections/1/export") { _, _ ->
            MockResponse().setHeader("Content-Type", "text/calendar; charset=utf-8").setBody(ics)
        }
        assertArrayEquals(ics.toByteArray(), f.repo.exportIcs(cal))
        assertEquals("Work.ics", IcsExport.fileName("Work"))
        assertEquals("A_B.ics", IcsExport.fileName("A/B"))
        assertEquals("calendar.ics", IcsExport.fileName("  "))
    }

    // ---------------- event.import_ics ----------------

    /** event.import_ics: the message and the attachment's part go to POST /calendar/import. */
    @Test fun importSendsTheMessageAndPart() = runBlocking {
        f.on("POST", "calendar/import") { _, _ ->
            f.json("""{"calendar":"https://dav/work/","calendar_id":1,"imported":[{"uid":"x","summary":"Offsite"}],"skipped":[],"failed":[]}""")
        }
        val r = f.repo.importFromMessage(501, 3)
        val body = f.body(f.sent("POST", "calendar/import").single())
        assertEquals(501L, body["message_id"]!!.jsonPrimitive.long)
        assertEquals(3L, body["part"]!!.jsonPrimitive.long)
        assertEquals("Added “Offsite” to calendar", importSummary(r))
        assertEquals("Already in your calendar", importSummary(ImportResult(skipped = listOf(ImportedItem("x", "Offsite", "exists")))))
        assertEquals("Added 2 events to calendar · 1 already there",
            importSummary(ImportResult(imported = listOf(ImportedItem("a"), ImportedItem("b")), skipped = listOf(ImportedItem("c")))))
        assertTrue(Attachment(3, "invite.ics").isIcs())
        assertTrue(Attachment(4, "meeting", "text/calendar; method=REQUEST").isIcs())
        assertFalse(Attachment(5, "notes.pdf", "application/pdf").isIcs())
    }

    // ---------------- event.join ----------------

    /** event.join: the link the server and the web pick (URL property when a meeting link, else location, then description). */
    @Test fun joinLinkMatchesTheServer() {
        assertEquals("https://meet.google.com/abc-defg-hij", JoinLink.find("https://meet.google.com/abc-defg-hij", "Room 4", null))
        assertEquals("https://zoom.us/j/123", JoinLink.find("https://example.com/agenda", "Zoom: https://zoom.us/j/123", "https://meet.jit.si/x"))
        assertEquals("https://teams.microsoft.com/l/meetup-join/1", JoinLink.find(null, "Room 4", "Join: https://teams.microsoft.com/l/meetup-join/1 now"))
        assertNull(JoinLink.find("https://example.com", "Room 4", "No link here"))
    }
}
