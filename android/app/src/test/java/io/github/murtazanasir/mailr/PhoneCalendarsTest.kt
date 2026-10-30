package io.github.murtazanasir.mailr

import android.app.Application
import android.content.Intent
import android.provider.CalendarContract
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.ApiClient
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.phonecal.PhoneBusy
import io.github.murtazanasir.mailr.data.phonecal.PhoneCalMode
import io.github.murtazanasir.mailr.data.phonecal.PhoneCalendar
import io.github.murtazanasir.mailr.data.phonecal.PhoneCalendarPrefs
import io.github.murtazanasir.mailr.data.phonecal.PhoneCalendars
import io.github.murtazanasir.mailr.data.phonecal.PhoneEvents
import io.github.murtazanasir.mailr.data.phonecal.MailrEvents
import io.github.murtazanasir.mailr.data.phonecal.PhoneInstance
import io.github.murtazanasir.mailr.data.phonecal.PhoneMatch
import io.github.murtazanasir.mailr.data.phonecal.ProviderCalendarSource
import io.github.murtazanasir.mailr.data.phonecal.ProviderValues
import io.github.murtazanasir.mailr.time.AppClock
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.time.ZoneId
import java.util.concurrent.CopyOnWriteArrayList

/**
 * Phone calendars (docs/design/device-calendars.md) against a fake calendar provider and a fake
 * server: which occurrences count as busy, stable keys and ids, a request with the contract's fields
 * only, the unchanged-snapshot skip, the permission denied path, each calendar's setting, and
 * DELETE when a calendar stops sending.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class PhoneCalendarsTest {
    private val app: Application = ApplicationProvider.getApplicationContext()
    private val server = MockWebServer()
    private val requests = CopyOnWriteArrayList<Pair<RecordedRequest, String>>()
    private val soon = CopyOnWriteArrayList<Long>()
    private val periodic = CopyOnWriteArrayList<Boolean>()
    private val now = AppClock.millis()
    private val hour = 3_600_000L
    private val day = 24 * hour
    private lateinit var pc: PhoneCalendars

    private val work = FakeCalendarProvider.Cal(7, "Calendar", "alex@work.example", "com.google.android.gm.exchange", 0xFF0078D4.toInt())
    private val home = FakeCalendarProvider.Cal(9, "Family", "me@gmail.com", "com.google", 0xFF33B679.toInt())

    private fun start(granted: Boolean = true, answer: (RecordedRequest) -> MockResponse = { r ->
        if (r.method == "PUT") MockResponse().setBody("""{"stored":1,"synced_at":"2026-10-02T15:40:00Z"}""")
        else MockResponse().setResponseCode(204)
    }) {
        FakeCalendarProvider.install(app, granted)
        FakeCalendarProvider.calendars += listOf(work, home)
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                requests += request to request.body.readUtf8()
                return answer(request)
            }
        }
        server.start()
        val sessions = SessionStore(app).apply { server = this@PhoneCalendarsTest.server.url("/").toString(); save("tok", "csrf", "me@example.com", "Me") }
        pc = phoneCalendars(ApiClient(sessions, clientId = "android-0123456789abcdef"))
    }

    /** Mailr's own events, as Room would hold them; empty: the calendar has not synced yet. */
    private val mailrEvents = MutableStateFlow<List<CalendarEvent>>(emptyList())
    private val mailr = object : MailrEvents {
        override fun events(fromMs: Long, toMs: Long): Flow<List<CalendarEvent>> = mailrEvents
        override suspend fun eventsNow(fromMs: Long, toMs: Long): List<CalendarEvent> = mailrEvents.value
    }

    private fun phoneCalendars(client: ApiClient?, installId: String = "android-0123456789abcdef") =
        PhoneCalendars(app, ProviderCalendarSource(app), PhoneCalendarPrefs(app), client, installId, device = "Pixel 9 Pro Fold",
            mailr = mailr).also {
            it.scheduleSoon = { d -> soon += d }
            it.schedulePeriodic = { on -> periodic += on }
        }

    @Before fun setUp() { requests.clear(); mailrEvents.value = emptyList() }

    @After fun tearDown() {
        if (::pc.isInitialized) {
            pc.prefs.setEnabled(false)
            pc.follow() // the provider watch off
            server.shutdown()
        }
    }

    private fun puts() = requests.filter { it.first.method == "PUT" }
    private fun deletes() = requests.filter { it.first.method == "DELETE" }
    private fun key(c: FakeCalendarProvider.Cal) = PhoneBusy.calendarKey("android-0123456789abcdef", c.type, c.account, c.id)

    private fun inst(id: Long, cal: Long = 7, startsIn: Long = day, availability: Int = ProviderValues.AVAILABILITY_BUSY,
                     status: Int? = 1, self: Int? = null, allDay: Boolean = false, title: String = "Event $id", location: String? = null) =
        FakeCalendarProvider.Inst(id, cal, now + startsIn, now + startsIn + hour, allDay, title, location, availability, status, self)

    private fun sendWork() {
        pc.permissionAnswered(granted = true, canAskAgain = true)
        pc.setMode(work.id, PhoneCalMode.SEND)
    }

    // ---------------- which occurrences count ----------------

    @Test fun busyAndTentativeCountFreeDeclinedAndCancelledDoNot() {
        fun i(availability: Int, status: Int? = 1, self: Int? = null) =
            PhoneInstance(1, 7, 0, hour, false, "t", null, availability, status, self)
        assertTrue(PhoneBusy.counts(i(ProviderValues.AVAILABILITY_BUSY)))
        assertTrue(PhoneBusy.counts(i(ProviderValues.AVAILABILITY_TENTATIVE)))
        assertFalse(PhoneBusy.counts(i(ProviderValues.AVAILABILITY_FREE)))
        assertFalse(PhoneBusy.counts(i(ProviderValues.AVAILABILITY_BUSY, self = ProviderValues.ATTENDEE_STATUS_DECLINED)))
        assertFalse(PhoneBusy.counts(i(ProviderValues.AVAILABILITY_BUSY, status = ProviderValues.STATUS_CANCELED)))
        // Accepted (1) and no answer yet (3, invited) still count.
        assertTrue(PhoneBusy.counts(i(ProviderValues.AVAILABILITY_BUSY, self = 1)))
        assertTrue(PhoneBusy.counts(i(ProviderValues.AVAILABILITY_TENTATIVE, self = 3)))
    }

    @Test fun theProviderReadMapsEachKindAndAllDayIsSentAsAllDay() {
        start()
        val allDayBegin = java.time.LocalDate.of(2026, 10, 5).atStartOfDay(java.time.ZoneOffset.UTC).toInstant().toEpochMilli()
        FakeCalendarProvider.instances += listOf(
            inst(1), inst(2, availability = ProviderValues.AVAILABILITY_TENTATIVE, startsIn = 2 * day),
            inst(3, availability = ProviderValues.AVAILABILITY_FREE), inst(4, self = ProviderValues.ATTENDEE_STATUS_DECLINED),
            inst(5, status = ProviderValues.STATUS_CANCELED),
            FakeCalendarProvider.Inst(6, 7, allDayBegin, allDayBegin + day, allDay = true),
            inst(8, cal = home.id), // another calendar, not sent
        )
        sendWork()
        runBlocking { assertEquals(PhoneCalendars.SyncResult.DONE, pc.sync(now)) }
        val body = MailrJson.parseToJsonElement(puts().single().second).jsonObject
        val busy = body["busy"]!!.jsonArray.map { it.jsonObject }
        val k = key(work)
        // Busy (1), tentative (2) and all-day (6), by start; free, declined, cancelled and the other calendar's are not there.
        assertEquals(listOf(PhoneBusy.busyId(k, 1, now + day), PhoneBusy.busyId(k, 2, now + 2 * day), PhoneBusy.busyId(k, 6, allDayBegin)),
            busy.map { it["id"]!!.jsonPrimitive.content })
        val allDay = busy.last()
        assertEquals("2026-10-05T00:00:00Z", allDay["start"]!!.jsonPrimitive.content)
        assertEquals("2026-10-06T00:00:00Z", allDay["end"]!!.jsonPrimitive.content)
        assertEquals(true, allDay["all_day"]!!.jsonPrimitive.content.toBoolean())
        assertEquals(false, busy[0]["all_day"]!!.jsonPrimitive.content.toBoolean())
        assertEquals(PhoneBusy.iso(now + day), busy[0]["start"]!!.jsonPrimitive.content)
        assertEquals(PhoneBusy.iso(now + day + hour), busy[0]["end"]!!.jsonPrimitive.content)
        assertEquals("/api/v1/calendar/phone-calendars/$k", puts().single().first.path)
    }

    // ---------------- keys and ids ----------------

    @Test fun keysAndIdsAreStableAndDistinct() {
        val k = PhoneBusy.calendarKey("android-0123456789abcdef", "com.google", "me@gmail.com", 9)
        assertEquals(k, PhoneBusy.calendarKey("android-0123456789abcdef", "com.google", "me@gmail.com", 9))
        assertTrue(k.matches(Regex("[0-9a-f]{32}")))
        assertNotEquals(k, PhoneBusy.calendarKey("android-0123456789abcdef", "com.google", "me@gmail.com", 10))
        assertNotEquals(k, PhoneBusy.calendarKey("android-fedcba9876543210", "com.google", "me@gmail.com", 9))
        assertNotEquals(k, PhoneBusy.calendarKey("android-0123456789abcdef", "com.google", "other@gmail.com", 9))
        // A fixed value, so a change of the formula shows here (the server keys its blocks by it).
        assertEquals(PhoneBusy.sha256Hex("android-0123456789abcdef\ncom.google\nme@gmail.com\n9").take(32), k)
        val id = PhoneBusy.busyId(k, 4242, 1_790_000_000_000)
        assertTrue(id.matches(Regex("[0-9a-f]{24}")))
        assertEquals(id, PhoneBusy.busyId(k, 4242, 1_790_000_000_000))
        assertNotEquals(id, PhoneBusy.busyId(k, 4242, 1_790_000_000_001))
        assertNotEquals(id, PhoneBusy.busyId(k, 4243, 1_790_000_000_000))
    }

    @Test fun theSameInstallSendsUnderTheSameKey() {
        start()
        FakeCalendarProvider.instances += inst(1)
        sendWork()
        runBlocking { pc.sync(now) }
        // Another graph on the same install (a new process) computes the same key.
        val again = phoneCalendars(null)
        val cal = ProviderCalendarSource(app).calendars().first { it.id == work.id }
        assertEquals(puts().single().first.path, "/api/v1/calendar/phone-calendars/${again.key(cal)}")
    }

    // ---------------- the request body ----------------

    @Test fun theRequestCarriesTheContractsFieldsAndNothingElse() {
        start()
        FakeCalendarProvider.instances += listOf(
            inst(4242, title = "Budget meeting with the dean", location = "Hall B 210"),
            inst(4243, availability = ProviderValues.AVAILABILITY_TENTATIVE, title = "Lunch with Dana", location = "Café"))
        sendWork()
        runBlocking { pc.sync(now) }
        val raw = puts().single().second
        val body = MailrJson.parseToJsonElement(raw).jsonObject
        assertEquals(setOf("name", "color", "device", "window_start", "window_end", "details", "busy"), body.keys)
        assertEquals("false", body["details"]!!.jsonPrimitive.content)
        body["busy"]!!.jsonArray.forEach { assertEquals(setOf("id", "start", "end", "all_day"), it.jsonObject.keys) }
        listOf("Budget", "dean", "Hall B", "Lunch", "Dana", "Café", "4242", "4243", "title", "location", "attendee", "description")
            .forEach { assertFalse("the request holds \"$it\": $raw", raw.contains(it, ignoreCase = true)) }
        assertEquals("Calendar (alex@work.example)", body["name"]!!.jsonPrimitive.content)
        assertEquals("#0078d4", body["color"]!!.jsonPrimitive.content)
        assertEquals("Pixel 9 Pro Fold", body["device"]!!.jsonPrimitive.content)
        assertEquals(PhoneBusy.iso(now - 30 * day), body["window_start"]!!.jsonPrimitive.content)
        assertEquals(PhoneBusy.iso(now + 180 * day), body["window_end"]!!.jsonPrimitive.content)
        assertEquals("application/json; charset=utf-8", puts().single().first.getHeader("Content-Type"))
    }

    // ---------------- the snapshot hash ----------------

    @Test fun anUnchangedSnapshotSendsNothingAndAChangeSendsAgain() {
        start()
        FakeCalendarProvider.instances += inst(1)
        sendWork()
        runBlocking {
            pc.sync(now)
            pc.sync(now + 30 * 60_000)
            assertEquals(1, puts().size)
            FakeCalendarProvider.instances += inst(2, startsIn = 3 * day)
            pc.sync(now + 60 * 60_000)
            assertEquals(2, puts().size)
            // A title change alone changes nothing that is sent.
            FakeCalendarProvider.instances.replaceAll { if (it.eventId == 2L) it.copy(title = "Renamed") else it }
            pc.sync(now + 90 * 60_000)
            assertEquals(2, puts().size)
            // After 12 hours the same snapshot goes again, so the server's "Updated … ago" stays current.
            pc.sync(now + 60 * 60_000 + PhoneCalendars.REFRESH_MS)
            assertEquals(3, puts().size)
        }
    }

    @Test fun offlineSendsNothingAndTheNextRunSendsTheCurrentSnapshot() {
        start(answer = { MockResponse().setResponseCode(503) })
        FakeCalendarProvider.instances += inst(1)
        sendWork()
        runBlocking { assertEquals(PhoneCalendars.SyncResult.RETRY, pc.sync(now)) }
        assertTrue(pc.prefs.sent().isEmpty())
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                requests += request to request.body.readUtf8()
                return MockResponse().setBody("""{"stored":2}""")
            }
        }
        FakeCalendarProvider.instances += inst(2, startsIn = 2 * day)
        runBlocking { assertEquals(PhoneCalendars.SyncResult.DONE, pc.sync(now + 60_000)) }
        assertEquals(2, MailrJson.parseToJsonElement(puts().last().second).jsonObject["busy"]!!.jsonArray.size)
        assertEquals(key(work), pc.prefs.sent().keys.single())
    }

    // ---------------- permission ----------------

    @Test fun deniedPermissionKeepsItOffReadsNothingAndSendsNothing() {
        start(granted = false)
        FakeCalendarProvider.instances += inst(1)
        pc.setMode(work.id, PhoneCalMode.SEND)
        pc.setEnabled(true) // the screen asks first; without access the switch stays off
        assertFalse(pc.prefs.enabled.value)
        pc.permissionAnswered(granted = false, canAskAgain = true)
        assertFalse(pc.prefs.enabled.value)
        assertFalse(pc.prefs.deniedForever.value)
        pc.permissionAnswered(granted = false, canAskAgain = false) // "Don't ask again"
        assertTrue(pc.prefs.deniedForever.value)
        assertTrue(ProviderCalendarSource(app).calendars().isEmpty())
        assertTrue(runBlocking { pc.events(now, now + 7 * day).first() }.isEmpty())
        runBlocking { assertEquals(PhoneCalendars.SyncResult.DONE, pc.sync(now)) }
        assertTrue(requests.isEmpty())
        assertTrue("nothing was read: ${FakeCalendarProvider.queried}", FakeCalendarProvider.queried.isEmpty())
        assertFalse(pc.watching)
        assertTrue(periodic.none { it })
    }

    @Test fun accessTakenBackRemovesWhatTheServerHolds() {
        start()
        FakeCalendarProvider.instances += inst(1)
        sendWork()
        runBlocking { pc.sync(now) }
        org.robolectric.Shadows.shadowOf(app).denyPermissions(android.Manifest.permission.READ_CALENDAR)
        pc.recheck()
        assertFalse(pc.prefs.enabled.value)
        runBlocking { pc.sync(now + 60_000) }
        assertEquals("/api/v1/calendar/phone-calendars/${key(work)}", deletes().single().first.path)
        assertTrue(pc.prefs.sent().isEmpty())
    }

    // ---------------- settings per calendar ----------------

    @Test fun eachCalendarKeepsItsOwnChoiceDefaultOff() {
        start()
        pc.permissionAnswered(granted = true, canAskAgain = true)
        assertEquals(PhoneCalMode.OFF, pc.prefs.mode(work.id))
        pc.setMode(work.id, PhoneCalMode.SEND)
        pc.setMode(home.id, PhoneCalMode.SHOW)
        val again = PhoneCalendarPrefs(app)
        assertTrue(again.enabled.value)
        assertEquals(mapOf(work.id to PhoneCalMode.SEND, home.id to PhoneCalMode.SHOW), again.modes.value)
        pc.setMode(home.id, PhoneCalMode.DETAILS)
        assertEquals(mapOf(work.id to PhoneCalMode.SEND, home.id to PhoneCalMode.DETAILS), PhoneCalendarPrefs(app).modes.value)
        pc.setMode(home.id, PhoneCalMode.SHOW)
        pc.setMode(home.id, PhoneCalMode.OFF)
        assertEquals(mapOf(work.id to PhoneCalMode.SEND), PhoneCalendarPrefs(app).modes.value)
        assertEquals(PhoneCalMode.OFF, PhoneCalendarPrefs(app).mode(42))
    }

    @Test fun shownCalendarsAppearReadOnlyInTheirColourAndOffOnesDoNot() {
        start()
        FakeCalendarProvider.instances += listOf(inst(1, title = "Office hours", location = "Hall B 301"), inst(2, cal = home.id))
        pc.permissionAnswered(granted = true, canAskAgain = true)
        assertTrue(runBlocking { pc.events(now, now + 7 * day).first() }.isEmpty())
        pc.setMode(work.id, PhoneCalMode.SHOW)
        val shown = runBlocking { pc.events(now, now + 7 * day) { ZoneId.of("America/Chicago") }.first { it.isNotEmpty() } }
        val e = shown.single()
        assertEquals("Office hours", e.summary)
        assertEquals("Hall B 301", e.location)
        assertEquals("#0078d4", e.color)
        assertTrue(PhoneEvents.isPhone(e))
        assertTrue(e.noReminders)
        assertTrue(e.reminders.isEmpty())
        assertEquals("phone:1|${now + day}", e.key)
        // Showing sends nothing.
        runBlocking { pc.sync(now) }
        assertTrue(requests.isEmpty())
    }

    // ---------------- DELETE ----------------

    @Test fun switchingToShowOrOffSendsDelete() {
        start()
        FakeCalendarProvider.instances += listOf(inst(1), inst(2, cal = home.id))
        sendWork()
        pc.setMode(home.id, PhoneCalMode.SEND)
        runBlocking { pc.sync(now) }
        assertEquals(setOf(key(work), key(home)), puts().map { it.first.path!!.substringAfterLast('/') }.toSet())
        soon.clear()
        pc.setMode(work.id, PhoneCalMode.SHOW)
        assertEquals(listOf(0L), soon) // a run at once
        runBlocking { pc.sync(now + 60_000) }
        assertEquals(listOf("/api/v1/calendar/phone-calendars/${key(work)}"), deletes().map { it.first.path })
        pc.setMode(home.id, PhoneCalMode.OFF)
        runBlocking { pc.sync(now + 120_000) }
        assertEquals("/api/v1/calendar/phone-calendars/${key(home)}", deletes().last().first.path)
        assertEquals(2, puts().size) // nothing sent again
        assertTrue(pc.prefs.sent().isEmpty())
        // The feature off with a calendar still sending: DELETE too.
        pc.setMode(work.id, PhoneCalMode.SEND)
        runBlocking { pc.sync(now + 180_000) }
        pc.setEnabled(false)
        runBlocking { pc.sync(now + 240_000) }
        assertEquals("/api/v1/calendar/phone-calendars/${key(work)}", deletes().last().first.path)
        assertEquals(3, deletes().size)
    }

    @Test fun aDeleteThatFailsStaysForTheNextRun() {
        var down = true
        start(answer = { r -> if (r.method == "DELETE" && down) MockResponse().setResponseCode(502) else MockResponse().setBody("{}") })
        FakeCalendarProvider.instances += inst(1)
        sendWork()
        runBlocking { pc.sync(now) }
        pc.setMode(work.id, PhoneCalMode.OFF)
        runBlocking { assertEquals(PhoneCalendars.SyncResult.RETRY, pc.sync(now + 60_000)) }
        assertEquals(key(work), pc.prefs.sent().keys.single())
        down = false
        runBlocking { assertEquals(PhoneCalendars.SyncResult.DONE, pc.sync(now + 120_000)) }
        assertTrue(pc.prefs.sent().isEmpty())
        assertEquals(2, deletes().size)
    }

    // ---------------- the provider watch and the schedule ----------------

    @Test fun providerChangesSendAMinuteLaterOnlyWhileACalendarSends() {
        start()
        pc.permissionAnswered(granted = true, canAskAgain = true)
        pc.setMode(work.id, PhoneCalMode.SHOW)
        assertFalse(pc.watching)
        assertEquals(false, periodic.last())
        pc.setMode(work.id, PhoneCalMode.SEND)
        assertTrue(pc.watching)
        assertEquals(true, periodic.last())
        soon.clear()
        app.contentResolver.notifyChange(CalendarContract.Events.CONTENT_URI, null)
        org.robolectric.Shadows.shadowOf(android.os.Looper.getMainLooper()).idle()
        assertEquals(listOf(PhoneCalendars.DEBOUNCE_MS), soon)
        pc.setMode(work.id, PhoneCalMode.OFF)
        assertFalse(pc.watching)
        assertEquals(false, periodic.last())
    }

    // ---------------- "Show and send details" ----------------

    private fun sendWorkDetails() {
        pc.permissionAnswered(granted = true, canAskAgain = true)
        pc.setMode(work.id, PhoneCalMode.DETAILS)
    }

    private fun lastBody() = MailrJson.parseToJsonElement(puts().last().second).jsonObject

    @Test fun theDetailsRequestAddsTitleAndPlaceAndNothingElse() {
        start()
        FakeCalendarProvider.instances += listOf(
            inst(4242, title = "  Planning review  ", location = "Meeting room 2"),
            inst(4243, startsIn = 2 * day, availability = ProviderValues.AVAILABILITY_TENTATIVE, title = "Garden club", location = "  "),
            inst(4244, startsIn = 3 * day, availability = ProviderValues.AVAILABILITY_FREE, title = "Free time", location = "Park"))
        sendWorkDetails()
        runBlocking { pc.sync(now) }
        val raw = puts().single().second
        val body = MailrJson.parseToJsonElement(raw).jsonObject
        assertEquals(setOf("name", "color", "device", "window_start", "window_end", "details", "busy"), body.keys)
        assertEquals("true", body["details"]!!.jsonPrimitive.content)
        val busy = body["busy"]!!.jsonArray.map { it.jsonObject }
        assertEquals(setOf("id", "start", "end", "all_day", "title", "location"), busy[0].keys)
        assertEquals("Planning review", busy[0]["title"]!!.jsonPrimitive.content)
        assertEquals("Meeting room 2", busy[0]["location"]!!.jsonPrimitive.content)
        // An empty place is left out; the free event is not sent at all.
        assertEquals(setOf("id", "start", "end", "all_day", "title"), busy[1].keys)
        assertEquals("Garden club", busy[1]["title"]!!.jsonPrimitive.content)
        assertEquals(2, busy.size)
        listOf("Free time", "Park", "4242", "4243", "attendee", "guest", "description", "organizer", "notes", "event_id")
            .forEach { assertFalse("the request holds \"$it\": $raw", raw.contains(it, ignoreCase = true)) }
        assertEquals(PhoneBusy.busyId(key(work), 4242, now + day), busy[0]["id"]!!.jsonPrimitive.content)
    }

    @Test fun titlesAndPlacesAreCutTo300Characters() {
        assertEquals(300, PhoneBusy.MAX_TEXT)
        assertEquals("x".repeat(300), PhoneBusy.text("  " + "x".repeat(400) + "  "))
        assertEquals("y".repeat(300), PhoneBusy.text("y".repeat(300)))
        // Characters, not UTF-16 units: a cut never splits an emoji.
        val emoji = "\uD83D\uDCC5"
        val cut = PhoneBusy.text("a".repeat(299) + emoji + "bbb")!!
        assertEquals("a".repeat(299) + emoji, cut)
        assertEquals(300, cut.codePointCount(0, cut.length))
        assertNull(PhoneBusy.text("   "))
        assertNull(PhoneBusy.text(null))
        start()
        FakeCalendarProvider.instances += inst(1, title = "t".repeat(500), location = "p".repeat(301))
        sendWorkDetails()
        runBlocking { pc.sync(now) }
        val e = lastBody()["busy"]!!.jsonArray.single().jsonObject
        assertEquals("t".repeat(300), e["title"]!!.jsonPrimitive.content)
        assertEquals("p".repeat(300), e["location"]!!.jsonPrimitive.content)
    }

    @Test fun aBusyOnlyBodyCannotHoldATitleOrAPlace() {
        val cal = PhoneCalendar(7, "Calendar", "alex@work.example", "x", 0xFF0078D4.toInt())
        val i = PhoneInstance(1, 7, now, now + hour, false, "Planning review", "Meeting room 2", ProviderValues.AVAILABILITY_BUSY, 1, null)
        val busyOnly = PhoneBusy.body(cal, "Phone", "k", listOf(i), PhoneBusy.window(now))
        assertFalse(busyOnly.details)
        assertTrue(busyOnly.busy.all { it.title == null && it.location == null })
        val withDetails = PhoneBusy.body(cal, "Phone", "k", listOf(i), PhoneBusy.window(now), details = true)
        assertEquals("Planning review", withDetails.busy.single().title)
        org.junit.Assert.assertThrows(IllegalArgumentException::class.java) { busyOnly.copy(busy = withDetails.busy) }
        org.junit.Assert.assertThrows(IllegalArgumentException::class.java) {
            busyOnly.copy(busy = listOf(io.github.murtazanasir.mailr.data.phonecal.BusyEntry("i", "s", "e", false, location = "Meeting room 2")))
        }
    }

    @Test fun aBusyOnlySnapshotHashesAsBeforeAndDetailsChangeTheHash() {
        val cal = PhoneCalendar(7, "Calendar", "alex@work.example", "x", 0xFF0078D4.toInt())
        val i = PhoneInstance(1, 7, now, now + hour, false, "Planning review", "Meeting room 2", ProviderValues.AVAILABILITY_BUSY, 1, null)
        val w = PhoneBusy.window(now)
        val busyOnly = PhoneBusy.body(cal, "Phone", "k", listOf(i), w)
        val e = busyOnly.busy.single()
        // The formula before details: an app update sends no busy-only calendar again.
        assertEquals(PhoneBusy.sha256Hex("${busyOnly.name}\n${busyOnly.color}\nPhone\n${e.id} ${e.start} ${e.end} false\n"), PhoneBusy.hash(busyOnly))
        val details = PhoneBusy.body(cal, "Phone", "k", listOf(i), w, details = true)
        assertNotEquals(PhoneBusy.hash(busyOnly), PhoneBusy.hash(details))
        // With no events at all the two choices still differ.
        assertNotEquals(PhoneBusy.hash(PhoneBusy.body(cal, "Phone", "k", emptyList(), w)),
            PhoneBusy.hash(PhoneBusy.body(cal, "Phone", "k", emptyList(), w, details = true)))
        assertNotEquals(PhoneBusy.hash(details),
            PhoneBusy.hash(PhoneBusy.body(cal, "Phone", "k", listOf(i.copy(title = "Budget review")), w, details = true)))
        assertNotEquals(PhoneBusy.hash(details),
            PhoneBusy.hash(PhoneBusy.body(cal, "Phone", "k", listOf(i.copy(location = null)), w, details = true)))
    }

    @Test fun withDetailsATitleOrPlaceChangeSendsAgain() {
        start()
        FakeCalendarProvider.instances += inst(1, title = "Planning review", location = "Meeting room 2")
        sendWorkDetails()
        runBlocking {
            pc.sync(now)
            pc.sync(now + 30 * 60_000)
            assertEquals(1, puts().size)
            FakeCalendarProvider.instances.replaceAll { it.copy(title = "Budget review") }
            pc.sync(now + 60 * 60_000)
            assertEquals(2, puts().size)
            assertEquals("Budget review", lastBody()["busy"]!!.jsonArray.single().jsonObject["title"]!!.jsonPrimitive.content)
            FakeCalendarProvider.instances.replaceAll { it.copy(location = "Meeting room 3") }
            pc.sync(now + 90 * 60_000)
            assertEquals(3, puts().size)
            assertEquals("Meeting room 3", lastBody()["busy"]!!.jsonArray.single().jsonObject["location"]!!.jsonPrimitive.content)
        }
    }

    @Test fun fromDetailsToBusyTimesPutsAtOnceWithoutTitles() {
        start()
        FakeCalendarProvider.instances += inst(1, title = "Planning review", location = "Meeting room 2")
        sendWorkDetails()
        runBlocking { pc.sync(now) }
        assertEquals("true", lastBody()["details"]!!.jsonPrimitive.content)
        soon.clear()
        pc.setMode(work.id, PhoneCalMode.SEND)
        assertEquals(listOf(0L), soon) // a run at once, not the 30-minute one
        runBlocking { pc.sync(now + 60_000) }
        assertEquals(2, puts().size)
        assertTrue(deletes().isEmpty())
        val raw = puts().last().second
        val body = MailrJson.parseToJsonElement(raw).jsonObject
        assertEquals("/api/v1/calendar/phone-calendars/${key(work)}", puts().last().first.path)
        assertEquals("false", body["details"]!!.jsonPrimitive.content)
        assertEquals(setOf("id", "start", "end", "all_day"), body["busy"]!!.jsonArray.single().jsonObject.keys)
        listOf("Planning", "Meeting room", "title", "location").forEach { assertFalse("the request holds \"$it\": $raw", raw.contains(it)) }
        // And back to details: titles go again; to Show on this phone: DELETE as before.
        pc.setMode(work.id, PhoneCalMode.DETAILS)
        runBlocking { pc.sync(now + 120_000) }
        assertEquals("true", lastBody()["details"]!!.jsonPrimitive.content)
        pc.setMode(work.id, PhoneCalMode.SHOW)
        runBlocking { pc.sync(now + 180_000) }
        assertEquals(listOf("/api/v1/calendar/phone-calendars/${key(work)}"), deletes().map { it.first.path })
    }

    @Test fun detailsFollowTheSameMatchingRule() {
        start()
        val b = now + day
        FakeCalendarProvider.instances += listOf(inst(1, title = "Planning review"), inst(2, startsIn = 2 * day, title = "Canceled: Garden club"),
            inst(3, startsIn = 3 * day, title = "Evening walk"))
        mailrEvents.value = listOf(mailrEvent("m1", "Planning review", b, b + hour, myStatus = "DECLINED"))
        sendWorkDetails()
        runBlocking { pc.sync(now) }
        assertEquals(listOf("Evening walk"), lastBody()["busy"]!!.jsonArray.map { it.jsonObject["title"]!!.jsonPrimitive.content })
    }

    @Test fun aDetailsCalendarWatchesTheProviderLikeABusyTimesOne() {
        start()
        sendWorkDetails()
        assertTrue(pc.watching)
        assertEquals(true, periodic.last())
        assertTrue(runBlocking { pc.events(now, now + 7 * day).first() }.isEmpty()) // shown (nothing in it yet)
    }

    // ---------------- events also in Mailr's calendar ----------------

    private val zone = ZoneId.systemDefault()
    private fun at(ms: Long) = java.time.Instant.ofEpochMilli(ms).atZone(zone).toOffsetDateTime().toString()

    /** Mailr's copy of a meeting, as the calendar sync stores it in Room. */
    private fun mailrEvent(uid: String, title: String, begin: Long, end: Long, myStatus: String? = null, status: String? = null) =
        CalendarEvent(uid, title, at(begin), at(end), status = status, myStatus = myStatus, calendar = "/dav/cal/work/", organizer = "host@example.org")

    private fun sentIds(): List<String> =
        MailrJson.parseToJsonElement(puts().last().second).jsonObject["busy"]!!.jsonArray.map { it.jsonObject["id"]!!.jsonPrimitive.content }

    private fun shownIds(): List<Long> = runBlocking { pc.events(now - day, now + 7 * day).first() }.mapNotNull { PhoneEvents.eventId(it) }

    @Test fun titlesMatchAfterPrefixesCaseSpacesAndTruncation() {
        assertEquals("planning review", PhoneMatch.normTitle("  Declined:  Planning   Review "))
        assertEquals("planning review", PhoneMatch.normTitle("FW: Re: Updated: planning review."))
        assertEquals("planning review", PhoneMatch.normTitle("Canceled: Cancelled: Accepted: Tentative: Fwd: Planning review"))
        assertTrue(PhoneMatch.titlesMatch("Halloween Potluck ", "Halloween potluck"))
        assertTrue(PhoneMatch.titlesMatch("Declined: Planning review", "planning review"))
        assertTrue(PhoneMatch.titlesMatch("Canceled: Planning review", "PLANNING REVIEW"))
        // Exchange shortens long titles: one starting with the other still matches.
        assertTrue(PhoneMatch.titlesMatch("Quarterly planning review for the garden club", "Quarterly planning review for the"))
        assertFalse(PhoneMatch.titlesMatch("Planning review", "Budget review"))
        assertFalse(PhoneMatch.titlesMatch("", "Planning review"))
        assertFalse(PhoneMatch.titlesMatch("Declined:", "Planning review"))
        // A prefix that is not at the start stays part of the title.
        assertEquals("notes re: planning", PhoneMatch.normTitle("Notes RE: planning"))
        assertTrue(PhoneMatch.cancelledTitle("Canceled: Planning review"))
        assertTrue(PhoneMatch.cancelledTitle(" cancelled: Planning review"))
        assertFalse(PhoneMatch.cancelledTitle("Planning review canceled"))
    }

    @Test fun aMeetingDeclinedInMailrIsNeitherShownNorSent() {
        start()
        val b = now + day
        FakeCalendarProvider.instances += listOf(
            inst(1, availability = ProviderValues.AVAILABILITY_TENTATIVE, title = "Planning review", status = null, self = 0),
            inst(2, startsIn = 2 * day))
        mailrEvents.value = listOf(mailrEvent("m1", "Planning review", b, b + hour, myStatus = "DECLINED"))
        sendWork()
        assertEquals(listOf(2L), shownIds())
        runBlocking { pc.sync(now) }
        assertEquals(listOf(PhoneBusy.busyId(key(work), 2, now + 2 * day)), sentIds())
    }

    @Test fun aMeetingCancelledInMailrIsNeitherShownNorSent() {
        start()
        val b = now + day
        FakeCalendarProvider.instances += listOf(inst(1, title = "Planning review"), inst(2, startsIn = 2 * day))
        mailrEvents.value = listOf(mailrEvent("m1", "Canceled: Planning review", b, b + hour, status = "CANCELLED"))
        sendWork()
        assertEquals(listOf(2L), shownIds())
        runBlocking { pc.sync(now) }
        assertEquals(listOf(PhoneBusy.busyId(key(work), 2, now + 2 * day)), sentIds())
    }

    @Test fun anAcceptedMeetingInBothCalendarsIsLeftToMailr() {
        start()
        val b = now + day
        FakeCalendarProvider.instances += inst(1, title = "Halloween Potluck ")
        mailrEvents.value = listOf(mailrEvent("m1", "Halloween potluck", b, b + hour, myStatus = "ACCEPTED"))
        sendWork()
        assertTrue(shownIds().isEmpty())
        runBlocking { pc.sync(now) }
        assertTrue(sentIds().isEmpty())
    }

    @Test fun theSameTimeWithAnotherTitleOrTheSameTitleAtAnotherTimeIsKept() {
        start()
        val b = now + day
        FakeCalendarProvider.instances += listOf(
            inst(1, title = "Planning review"),                // same time as "Budget review", another title
            inst(2, startsIn = 2 * day, title = "Garden club")) // same title as Mailr's, an hour later there
        mailrEvents.value = listOf(
            mailrEvent("m1", "Budget review", b, b + hour, myStatus = "DECLINED"),
            mailrEvent("m2", "Garden club", now + 2 * day + hour, now + 2 * day + 2 * hour, myStatus = "DECLINED"),
            mailrEvent("m3", "Garden club", now + 2 * day, now + 2 * day + 2 * hour, myStatus = "DECLINED")) // same start, other end
        sendWork()
        assertEquals(listOf(1L, 2L), shownIds().sorted())
        runBlocking { pc.sync(now) }
        assertEquals(2, sentIds().size)
    }

    @Test fun allDayEventsMatchByTheirDates() {
        start()
        val d = java.time.LocalDate.of(2026, 10, 5)
        val begin = d.atStartOfDay(java.time.ZoneOffset.UTC).toInstant().toEpochMilli()
        FakeCalendarProvider.instances += listOf(
            FakeCalendarProvider.Inst(1, work.id, begin, begin + day, allDay = true, title = "Field day"),
            FakeCalendarProvider.Inst(2, work.id, begin + day, begin + 2 * day, allDay = true, title = "Field day"))
        mailrEvents.value = listOf(CalendarEvent("m1", "Field day", d.toString(), d.plusDays(1).toString(), allDay = true, myStatus = "DECLINED"))
        sendWork()
        assertEquals(listOf(2L), shownIds())
        runBlocking { pc.sync(now) }
        assertEquals(listOf(PhoneBusy.busyId(key(work), 2, begin + day)), sentIds())
    }

    @Test fun aPhoneEventTitledCanceledIsNeitherShownNorSent() {
        start()
        FakeCalendarProvider.instances += listOf(inst(1, title = "Canceled: Planning review"), inst(2, startsIn = 2 * day))
        sendWork()
        assertEquals(listOf(2L), shownIds())
        runBlocking { pc.sync(now) }
        assertEquals(listOf(PhoneBusy.busyId(key(work), 2, now + 2 * day)), sentIds())
    }

    @Test fun withNothingInMailrsCalendarYetEveryPhoneEventCounts() {
        start()
        FakeCalendarProvider.instances += listOf(inst(1, title = "Planning review"), inst(2, startsIn = 2 * day))
        sendWork()
        assertEquals(listOf(1L, 2L), shownIds().sorted())
        runBlocking { pc.sync(now) }
        assertEquals(2, sentIds().size)
    }

    @Test fun theServersPhoneBusyTimesAreNeverMatched() {
        start()
        val b = now + day
        FakeCalendarProvider.instances += inst(1, title = "Busy")
        // The block this phone sent comes back from the server as "Busy" in a "phone:<key>" calendar.
        mailrEvents.value = listOf(CalendarEvent("p1", "Busy", at(b), at(b + hour), calendar = "phone:${key(work)}"))
        sendWork()
        assertEquals(listOf(1L), shownIds())
        runBlocking { pc.sync(now) }
        assertEquals(1, sentIds().size)
    }

    @Test fun aMeetingDeclinedLaterChangesTheSnapshotAndTheNextRunSendsIt() {
        start()
        val b = now + day
        FakeCalendarProvider.instances += listOf(inst(1, title = "Planning review"), inst(2, startsIn = 2 * day))
        sendWork()
        runBlocking {
            pc.sync(now)
            assertEquals(2, sentIds().size)
            val before = pc.prefs.sent().getValue(key(work)).hash
            // Declined in Mailr; the calendar sync puts it in Room. The next run, 30 minutes later, sends again.
            mailrEvents.value = listOf(mailrEvent("m1", "Planning review", b, b + hour, myStatus = "DECLINED"))
            pc.sync(now + 30 * 60_000)
            assertEquals(2, puts().size)
            assertEquals(listOf(PhoneBusy.busyId(key(work), 2, now + 2 * day)), sentIds())
            assertNotEquals(before, pc.prefs.sent().getValue(key(work)).hash)
            pc.sync(now + 60 * 60_000)
            assertEquals(2, puts().size)
        }
    }

    // ---------------- Open in Calendar ----------------

    @Test fun openInCalendarViewsTheEventAtItsOccurrence() {
        val cal = PhoneCalendar(7, "Calendar", "a@b.c", "x", 0xFF0078D4.toInt())
        val i = PhoneInstance(4242, 7, now + day, now + day + hour, false, "Meeting", null, 0, 1, null)
        val e = PhoneEvents.toEvent(i, cal, ZoneId.of("America/Chicago"))
        val intent = PhoneEvents.openIntent(e)!!
        assertEquals(Intent.ACTION_VIEW, intent.action)
        assertEquals("content://com.android.calendar/events/4242", intent.data.toString())
        assertEquals(now + day, intent.getLongExtra(CalendarContract.EXTRA_EVENT_BEGIN_TIME, 0))
        assertEquals(now + day + hour, intent.getLongExtra(CalendarContract.EXTRA_EVENT_END_TIME, 0))
        assertNull(PhoneEvents.openIntent(e.copy(uid = "e1")))
    }

    @Test fun mailrsOwnAccountsAreLeftOut() {
        start()
        FakeCalendarProvider.calendars += FakeCalendarProvider.Cal(11, "Mailr", "me@example.com", "io.github.murtazanasir.mailr", 0)
        assertEquals(listOf(7L, 9L), ProviderCalendarSource(app).calendars().map { it.id })
    }
}
