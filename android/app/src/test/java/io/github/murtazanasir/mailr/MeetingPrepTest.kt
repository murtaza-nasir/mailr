// ACT-SURFACES: android.calendar android.notification android.sheet
package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.time.AppClock
import android.Manifest
import android.app.Application
import android.app.NotificationManager
import android.content.Intent
import android.net.Uri
import android.os.Looper
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import io.github.murtazanasir.mailr.data.api.Attendee
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.PrepPack
import io.github.murtazanasir.mailr.data.api.PrepSummary
import io.github.murtazanasir.mailr.data.reminders.AlertStyle
import io.github.murtazanasir.mailr.data.reminders.ReminderChannels
import io.github.murtazanasir.mailr.data.reminders.ReminderNotifier
import io.github.murtazanasir.mailr.ui.Destination
import io.github.murtazanasir.mailr.ui.calendar.EventDetail
import io.github.murtazanasir.mailr.ui.calendar.MeetingPrepRules
import io.github.murtazanasir.mailr.ui.calendar.PrepHooks
import io.github.murtazanasir.mailr.ui.calendar.QuickActionCard
import io.github.murtazanasir.mailr.ui.calendar.QuickActions
import io.github.murtazanasir.mailr.ui.calendar.SheetTarget
import io.github.murtazanasir.mailr.ui.parseDeepLink
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.time.ZonedDateTime

/**
 * Meeting prep (event.prep) on Android: the pack as the server sends it and its collapsed line; the
 * card in the event detail (android.calendar), opened, with Summarise and a thread link; the
 * reminder notification's Prep in Dismiss's place (android.notification); the reminder sheet's Prep
 * (android.sheet); and the mailr://event/…?prep=1 link that opens the card.
 */
private const val PACK = """{"event":{"uid":"ev-1","title":"Lumen review","guests":[{"email":"dana@lumen.example","name":"Dana Whitfield"}]},
 "upcoming":true,"has_guests":true,"untrusted":true,"hidden":1,"local_only":false,
 "recording":{"connector_id":3,"recording_id":12,"title":"Lumen review","meeting_date":"2026-09-28T15:00:00+00:00",
  "summary_line":"Dana wants the pricing page first","why":"series","action_items":{"open":[{"uid":"t1","summary":"Draft the pricing page","due":null}],"done":2}},
 "threads":[{"conversation_id":4503599627370497,"message_id":1,"subject":"Pricing page","snippet":"Can you share the draft?","last_date":"2026-10-01T10:00:00+00:00","messages":3,"people":[],"signals":[],"from_me":true}],
 "tasks":[{"uid":"t2","summary":"Send Dana the logo files","due":null,"why":"mentions"}],
 "notes":[{"entity":{"id":1,"name":"Dana Whitfield","kind":"person","summary":"Lumen's CMO","emails":[]},"notes":[{"id":1,"text":"Prefers short decks","kind":"preference","from_mail":false}]}],
 "attachments":[{"message_id":1,"conversation_id":4503599627370497,"filename":"pricing-v2.pdf","type":"application/pdf","size":1,"date":"2026-10-01T10:00:00+00:00","from":"dana@lumen.example","direction":"received"}],
 "open_items":{"questions":[{"conversation_id":4503599627370497,"message_id":2,"from":"dana@lumen.example","date":null,"question":"Are we still aiming for the 20th?"}],
  "waiting_on":[],"promised":[{"conversation_id":4503599627370497,"message_id":3,"date":null,"to":[],"text":"I'll send the deck on Monday."}]},
 "new_field":"ignored"}"""

@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class MeetingPrepTest {
    @get:Rule val compose = createComposeRule()
    private val h = CalendarHarness()
    private val start = AppClock.now().plusDays(1).withHour(15).withMinute(0).withSecond(0).withNano(0)
    private val event = CalendarEvent("ev-1", "Lumen review", start.toOffsetDateTime().toString(), start.plusHours(1).toOffsetDateTime().toString(),
        attendeeList = listOf(Attendee("dana@lumen.example", "Dana Whitfield", "ACCEPTED")))

    @Before fun setUp() {
        h.start()
        shadowOf(h.app).grantPermissions(Manifest.permission.POST_NOTIFICATIONS)
        ReminderChannels.create(h.app)
    }

    @After fun tearDown() = h.stop()

    private fun settle(what: String, cond: () -> Boolean) = eventually(what, idle = {
        compose.mainClock.advanceTimeBy(100)
        compose.waitForIdle()
        shadowOf(Looper.getMainLooper()).idle()
    }, check = cond)

    private fun shown(text: String) = compose.onAllNodesWithText(text, substring = true).fetchSemanticsNodes().isNotEmpty()

    @Test fun packDecodesAndCounts() {
        val p = MailrJson.decodeFromString(PrepPack.serializer(), PACK)
        assertFalse(p.empty)
        assertEquals("Last recording · 1 thread · 1 open question · 1 task · 1 note · 1 file", p.counts())
        assertEquals("Dana Whitfield", p.guestName("dana@lumen.example"))
        assertTrue(MailrJson.decodeFromString(PrepPack.serializer(), "{}").empty)
        // Offered while the event has guests and has not ended half an hour ago.
        val now = start.toInstant().toEpochMilli()
        assertTrue(MeetingPrepRules.offers(event, now))
        assertFalse(MeetingPrepRules.offers(event.copy(attendeeList = emptyList()), now))
        assertFalse(MeetingPrepRules.offers(event, start.plusHours(2).toInstant().toEpochMilli()))
    }

    /** ACT event.prep on android.calendar: the card opens, loads the pack, summarises, and its links work. */
    @Test fun eventDetailCard() {
        val pack = MailrJson.decodeFromString(PrepPack.serializer(), PACK)
        val loads = mutableListOf<String?>()
        val conversations = mutableListOf<Long>()
        val tasks = mutableListOf<String>()
        val hooks = PrepHooks(load = { _, occ -> loads += occ; pack },
            summarise = { _, _ -> PrepSummary("ok", "Dana will ask about the pricing page; bring the deck.") },
            openConversation = { conversations += it }, openTask = { tasks += it })
        compose.setContent { MaterialTheme { EventDetail(event, null, emptyList(), 9, null, {}, {}, {}, now = start.minusHours(3).toInstant().toEpochMilli(), prep = hooks) } }
        compose.onNodeWithText("Meeting prep").performScrollTo().performClick()
        settle("the pack shows \"Pricing page\"") { shown("Pricing page") }
        assertEquals(1, loads.size)
        assertTrue(shown("Dana wants the pricing page first"))
        assertTrue(shown("1 open action item, 2 done"))
        assertTrue(shown("Are we still aiming for the 20th?"))
        assertTrue(shown("Prefers short decks"))
        assertTrue(shown("Some local-only items are hidden."))
        compose.onNodeWithText("Summarise").performScrollTo().performClick()
        settle("the pack shows \"bring the deck\"") { shown("bring the deck") }
        compose.onNodeWithText("Pricing page", substring = true).performScrollTo().performClick()
        assertEquals(listOf(4503599627370497L), conversations)
        compose.onNodeWithText("Send Dana the logo files").performScrollTo().performClick()
        assertEquals(listOf("t2"), tasks)
    }

    @Test fun noGuestsNoCard() {
        val hooks = PrepHooks(load = { _, _ -> error("not asked") }, summarise = { _, _ -> error("not asked") })
        compose.setContent { MaterialTheme { EventDetail(event.copy(attendeeList = emptyList()), null, emptyList(), 9, null, {}, {}, {}, prep = hooks) } }
        compose.waitForIdle()
        assertFalse(shown("Meeting prep"))
    }

    /** ACT event.prep on android.notification: Prep takes Dismiss's place for an event with guests; Join still wins. */
    @Test fun notificationPrep() {
        val nm = h.app.getSystemService(NotificationManager::class.java)
        ReminderNotifier(h.app).post(h.eventReminder, alert = false, join = null, style = AlertStyle(prep = true))
        val n = shadowOf(nm).getNotification(h.eventReminder.key, ReminderNotifier.EVENT_ID)!!
        val titles = n.actions.map { it.title.toString() }
        assertTrue("Prep" in titles)
        assertFalse("Dismiss" in titles)
        val open = shadowOf(n.actions.first { it.title.toString() == "Prep" }.actionIntent).savedIntent
        assertEquals(Intent.ACTION_VIEW, open.action)
        assertEquals("mailr", open.data!!.scheme)
        assertEquals("1", open.data!!.getQueryParameter("prep"))
        val key2 = h.eventReminder.key + "j"
        ReminderNotifier(h.app).post(h.eventReminder.copy(key = key2), alert = false, join = "https://meet.google.com/abc-defg-hij",
            style = AlertStyle(prep = true))
        val joined = shadowOf(nm).getNotification(key2, ReminderNotifier.EVENT_ID)!!
        assertEquals("Join", joined.actions.last().title.toString())
    }

    /** ACT event.prep on android.sheet: the reminder sheet's Prep opens the event with its prep. */
    @Test fun sheetPrep() {
        val e = h.event.copy(attendeeList = listOf(Attendee("dana@lumen.example", "Dana")))
        val actions = QuickActions(h.g, h.app)
        compose.setContent { MaterialTheme { QuickActionCard(SheetTarget(h.eventReminder, event = e), actions, 9, null, run = {}) } }
        compose.onNodeWithText("Prep").performClick()
        val open = h.nextOpened()!!
        assertEquals("event", open.data!!.host)
        assertEquals("1", open.data!!.getQueryParameter("prep"))
        // The link opens the event with its prep.
        assertEquals(Destination.Event(h.event.uid, h.eventReminder.occurrence, prep = true),
            parseDeepLink(Intent(Intent.ACTION_VIEW, open.data), internal = true))
        assertEquals(Destination.Event("u1", "2026-10-02", prep = true),
            parseDeepLink(Intent(Intent.ACTION_VIEW, Uri.parse("https://mail.example.com/calendar/event/u1/2026-10-02?prep=1")), "mail.example.com"))
    }

    @Test fun sheetWithoutGuestsHasNoPrep() {
        val actions = QuickActions(h.g, h.app)
        compose.setContent { MaterialTheme { QuickActionCard(SheetTarget(h.eventReminder, event = h.event), actions, 9, null, run = {}) } }
        compose.waitForIdle()
        assertFalse(shown("Prep"))
    }
}
