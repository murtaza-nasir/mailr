// ACT-SURFACES: android.notification android.sheet
package io.github.murtazanasir.mailr

import android.Manifest
import android.app.Application
import android.app.NotificationManager
import android.content.Intent
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.ApiClient
import io.github.murtazanasir.mailr.data.api.CalendarApi
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.MailrApi
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.ReminderSettings
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.cal.CalendarRepository
import io.github.murtazanasir.mailr.data.db.CalEventEntity
import io.github.murtazanasir.mailr.data.db.MailrDb
import io.github.murtazanasir.mailr.data.db.ReminderEntity
import io.github.murtazanasir.mailr.data.reminders.Kind
import io.github.murtazanasir.mailr.data.reminders.RState
import io.github.murtazanasir.mailr.data.reminders.ReminderChannels
import io.github.murtazanasir.mailr.data.reminders.ReminderConfig
import io.github.murtazanasir.mailr.data.reminders.ReminderEngine
import io.github.murtazanasir.mailr.data.reminders.ReminderNotifier
import io.github.murtazanasir.mailr.data.reminders.ReminderSink
import io.github.murtazanasir.mailr.data.reminders.SummarySpec
import io.github.murtazanasir.mailr.data.repo.MailRepository
import kotlinx.coroutines.test.runTest
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.time.ZoneId
import java.time.ZonedDateTime

/**
 * /reminders/settings `missed` on the phone (a late reminder posts nothing when it is off), and
 * the Join action (event.join on android.notification) for events with a conference link.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class ReminderSettingsJoinTest {
    private val zone = ZoneId.of("America/Chicago")
    private val t0 = ZonedDateTime.of(2026, 9, 29, 9, 0, 0, 0, zone)
    private var now = t0.toInstant().toEpochMilli()
    private var config = ReminderConfig(defaultTimedEvent = 10)
    private lateinit var db: MailrDb
    private lateinit var engine: ReminderEngine

    private class Shade : ReminderSink {
        val active = linkedMapOf<String, ReminderEntity>()
        val joins = mutableMapOf<String, String?>()
        override fun post(r: ReminderEntity, alert: Boolean) { active[r.key] = r }
        override fun post(r: ReminderEntity, alert: Boolean, join: String?) { post(r, alert); joins[r.key] = join }
        override fun cancel(r: ReminderEntity) { active.remove(r.key) }
        override fun postSummary(spec: SummarySpec) {}
        override fun cancelSummary(kind: String) {}
        override fun activeKeys(): Set<String> = active.keys.toSet()
    }

    private val shade = Shade()

    @Before fun setUp() {
        val ctx = ApplicationProvider.getApplicationContext<Application>()
        val sessions = SessionStore(ctx).apply { server = "http://127.0.0.1:9/"; save("t", "c", "me@example.com", "Me") }
        db = MailrDb.create(ctx, inMemory = true)
        val client = ApiClient(sessions)
        val cal = CalendarRepository(CalendarApi(client), db, MailRepository(MailrApi(client), db), now = { now }, zone = { zone })
        engine = ReminderEngine(db.reminders(), cal, { config }, shade, {}, now = { now }, zone = { zone })
    }

    @After fun tearDown() = db.close()

    private suspend fun event(uid: String, start: ZonedDateTime, location: String? = null, description: String? = null, url: String? = null) {
        val e = CalendarEvent(uid, "Event $uid", start.toOffsetDateTime().toString(), start.plusHours(1).toOffsetDateTime().toString(),
            location = location, description = description, url = url)
        db.calendar().putEvents(listOf(CalEventEntity(e.key, uid, start.toInstant().toEpochMilli(),
            start.plusHours(1).toInstant().toEpochMilli(), false, null, MailrJson.encodeToString(CalendarEvent.serializer(), e), occurrence = e.occurrence)))
    }

    /** Planned while the phone was awake; then it slept past the first reminder (the alarm came late). */
    private suspend fun sleptThrough() {
        event("late", t0.plusMinutes(5))          // its 10-minute reminder is due at t0 - 5 min
        event("ontime", t0.plusMinutes(10))       // due at t0
        now = t0.minusMinutes(20).toInstant().toEpochMilli()
        engine.resync()
        assertTrue(shade.active.isEmpty())
        now = t0.toInstant().toEpochMilli()
        engine.fire()
    }

    /** `missed: false`: a reminder that fires late is marked fired and posts nothing. */
    @Test fun missedOffPostsNothingLate() = runTest {
        config = config.copy(showMissed = false)
        sleptThrough()
        assertEquals(listOf("ontime"), shade.active.values.map { it.itemUid })
        val late = db.reminders().all().single { it.itemUid == "late" }
        assertEquals(RState.DISMISSED, late.state)
    }

    /** `missed: true` (the default): the late reminder shows, marked missed. */
    @Test fun missedOnPostsItAsMissed() = runTest {
        sleptThrough()
        assertTrue(shade.active.values.single { it.itemUid == "late" }.missed)
        assertFalse(shade.active.values.single { it.itemUid == "ontime" }.missed)
    }

    /** The settings' `missed` reaches the planner's config; `sound` is read and left to the channels. */
    @Test fun settingsCarryMissed() {
        val s = MailrJson.decodeFromString(ReminderSettings.serializer(), """{"events":true,"missed":false,"sound":true}""")
        assertFalse(s.missed)
        assertTrue(s.sound)
        assertTrue(MailrJson.decodeFromString(ReminderSettings.serializer(), "{}").missed)
    }

    /** event.join: the engine hands the event's conference link to the notification; tasks and plain events get none. */
    @Test fun eventReminderCarriesTheJoinLink() = runTest {
        event("call", t0.plusMinutes(10), location = "Zoom https://zoom.us/j/555")
        event("room", t0.plusMinutes(10), location = "Room 4")
        engine.resync()
        assertEquals("https://zoom.us/j/555", shade.joins.entries.single { it.key.contains("|call|") }.value)
        assertNull(shade.joins.entries.single { it.key.contains("|room|") }.value)
    }
}

/** event.join on android.notification: Join takes Dismiss's place and opens the meeting link. */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class ReminderJoinNotificationTest {
    private val h = CalendarHarness()

    @Before fun setUp() {
        h.start()
        shadowOf(h.app).grantPermissions(Manifest.permission.POST_NOTIFICATIONS)
        ReminderChannels.create(h.app)
    }

    @After fun tearDown() = h.stop()

    @Test fun joinReplacesDismiss() {
        val nm = h.app.getSystemService(NotificationManager::class.java)
        ReminderNotifier(h.app).post(h.eventReminder, alert = false, join = "https://meet.google.com/abc-defg-hij")
        val n = shadowOf(nm).getNotification(h.eventReminder.key, ReminderNotifier.EVENT_ID)!!
        val titles = n.actions.map { it.title.toString() }
        assertEquals("Join", titles.last())
        assertFalse("Dismiss" in titles)
        val open = shadowOf(n.actions.last().actionIntent).savedIntent
        assertEquals(Intent.ACTION_VIEW, open.action)
        assertEquals("https://meet.google.com/abc-defg-hij", open.dataString)

        ReminderNotifier(h.app).post(h.eventReminder.copy(key = h.eventReminder.key + "x"), alert = false)
        val plain = shadowOf(nm).getNotification(h.eventReminder.key + "x", ReminderNotifier.EVENT_ID)!!
        assertEquals("Dismiss", plain.actions.last().title.toString())
    }
}

/** event.join on android.sheet: Join beside Edit opens the meeting link; an event without one shows no Join. */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class JoinSheetTest {
    @get:org.junit.Rule val compose = io.github.murtazanasir.mailr.testing.createComposeRule()
    private val h = CalendarHarness()

    @Before fun setUp() { h.start() }
    @After fun tearDown() = h.stop()

    @Test fun joinOpensTheLink() {
        val e = h.event.copy(description = "Join: https://meet.google.com/abc-defg-hij")
        val actions = io.github.murtazanasir.mailr.ui.calendar.QuickActions(h.g, h.app)
        compose.setContent {
            androidx.compose.material3.MaterialTheme {
                io.github.murtazanasir.mailr.ui.calendar.QuickActionCard(io.github.murtazanasir.mailr.ui.calendar.SheetTarget(h.eventReminder, event = e), actions, 9, null, run = {})
            }
        }
        compose.onNodeWithText("Join").performClick()
        val open = h.nextOpened()!!
        assertEquals(Intent.ACTION_VIEW, open.action)
        assertEquals("https://meet.google.com/abc-defg-hij", open.dataString)
    }

    @Test fun noLinkNoJoin() {
        val actions = io.github.murtazanasir.mailr.ui.calendar.QuickActions(h.g, h.app)
        compose.setContent {
            androidx.compose.material3.MaterialTheme {
                io.github.murtazanasir.mailr.ui.calendar.QuickActionCard(io.github.murtazanasir.mailr.ui.calendar.SheetTarget(h.eventReminder, event = h.event), actions, 9, null, run = {})
            }
        }
        compose.onAllNodesWithText("Join").assertCountEquals(0)
    }
}
